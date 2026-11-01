
#!/usr/bin/env python3
"""Release ledger: protects aggregate cohort statistics against differencing ACROSS releases.

Problem: publishing 'cohort A, mean 0.62' this month and 'cohort A plus one learner, mean 0.64'
next month lets a reader solve for that one learner. V11 alone is stateless, so it cannot see this.

The ledger remembers which learners were in every cohort that was actually PUBLISHED (suppressed
cohorts are never recorded because no number left the building). Members are stored as salted
SHA-256 hashes, not raw IDs. Before a new release, each cohort is compared with every earlier one:

  subset mode (default)   suppress when one published cohort is a proper subset of another and
                          fewer than k learners are in the difference (the difference of the two
                          published sums would expose those few learners).
  strict mode             additionally, for the SAME competency across releases, suppress when
                          fewer than k learners joined AND fewer than k left (sliding-window
                          attack: knowing one leaver's value reveals the joiner's).

Limits (stated in every report): the salt lives in the ledger, so the ledger is as sensitive as the
learner data it protects; deleting or editing the ledger disables the protection (V14 can require
its use); outside knowledge and non-membership leakage (e.g. a single learner re-identified from
rounding) are not addressed; this is not differential privacy.
"""
from __future__ import annotations

import hashlib
import json
import os
import secrets
import stat
import sys
from pathlib import Path
from typing import Any, Iterable, Mapping

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from usiphus_common import atomic_write_text  # noqa: E402

VERSION = "1.0.0"
LEDGER_SCHEMA = 1
MODES = ("subset", "strict")


class LedgerError(ValueError):
    pass


def new_ledger() -> dict[str, Any]:
    return {"schema_version": LEDGER_SCHEMA, "salt": secrets.token_hex(32), "releases": []}


def load_ledger(path: Path | None) -> dict[str, Any]:
    """Return the ledger at path, or a fresh in-memory ledger when the file does not exist yet."""
    if path is None or not Path(path).exists():
        return new_ledger()
    try:
        ledger = json.loads(Path(path).read_text(encoding="utf-8-sig"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise LedgerError(f"Cannot read release ledger {path}: {exc}") from exc
    if (not isinstance(ledger, dict) or ledger.get("schema_version") != LEDGER_SCHEMA
            or not isinstance(ledger.get("salt"), str) or len(ledger["salt"]) < 32
            or not isinstance(ledger.get("releases"), list)):
        raise LedgerError("Release ledger is malformed; refusing to continue (a bad ledger must not silently disable protection)")
    for release in ledger["releases"]:
        if not isinstance(release, dict) or not isinstance(release.get("cohorts"), dict) \
                or not all(isinstance(v, list) for v in release["cohorts"].values()):
            raise LedgerError("Release ledger contains a malformed release entry")
    return ledger


def hash_member(salt: str, learner_id: str) -> str:
    return hashlib.sha256(f"{salt}\x1f{learner_id}".encode("utf-8")).hexdigest()


def hashed_members(ledger: Mapping[str, Any], learner_ids: Iterable[str]) -> frozenset[str]:
    salt = ledger["salt"]
    return frozenset(hash_member(salt, lid) for lid in learner_ids)


def conflicts(current: Mapping[str, frozenset[str]], ledger: Mapping[str, Any], k: int,
              mode: str = "subset") -> dict[str, str]:
    """Map competency_id -> reason for every current cohort that must be suppressed."""
    if mode not in MODES:
        raise LedgerError(f"ledger mode must be one of {MODES}")
    flagged: dict[str, str] = {}
    for cid, members in sorted(current.items()):
        for release in ledger["releases"]:
            when = release.get("release_id", "?")
            for prior_cid, prior_list in release["cohorts"].items():
                prior = frozenset(prior_list)
                if prior == members:
                    continue
                if prior < members and 0 < len(members - prior) < k:
                    flagged[cid] = (f"Cross-release differencing protection: fewer than {k} learners differ from the cohort "
                                    f"published for '{prior_cid}' in release {when}")
                elif members < prior and 0 < len(prior - members) < k:
                    flagged[cid] = (f"Cross-release differencing protection: this cohort is within fewer than {k} learners of "
                                    f"the larger cohort published for '{prior_cid}' in release {when}")
                elif (mode == "strict" and prior_cid == cid
                        and 0 < len(members - prior) < k and 0 < len(prior - members) < k):
                    flagged[cid] = (f"Cross-release differencing protection (strict): fewer than {k} learners joined and "
                                    f"fewer than {k} left since release {when}")
                if cid in flagged:
                    break
            if cid in flagged:
                break
    return flagged


def record_release(ledger: dict[str, Any], release_id: str, generated_at: str,
                   published: Mapping[str, frozenset[str]]) -> None:
    ledger["releases"].append({"release_id": release_id, "generated_at": generated_at,
                               "cohorts": {cid: sorted(m) for cid, m in sorted(published.items())}})


def save_ledger(path: Path, ledger: Mapping[str, Any]) -> None:
    atomic_write_text(json.dumps(ledger, indent=2, sort_keys=True) + "\n", Path(path))
    if os.name == "posix":
        os.chmod(path, stat.S_IRUSR | stat.S_IWUSR)  # salted membership data: owner-only
