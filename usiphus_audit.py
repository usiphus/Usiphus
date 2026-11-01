
#!/usr/bin/env python3
"""Usiphus audit tool: create signing keys, append signed audit events, export and verify anchors.

  keygen         create an Ed25519 key pair (private key stays with the signer; give the
                 PUBLIC key to verifiers via a trusted-keys file)
  trust          add a public key for an actor to a trusted-keys JSON file
  append         append one Ed25519-signed, hash-chained event to a claim
  anchors        export the head hash of every audit chain as a SIGNED anchor file. Keep that
                 file OUTSIDE the claims file's control (signed git tag, auditor's mailbox,
                 object store with retention, or an RFC 3161 timestamp - see docs)
  verify         verify chains (and optionally the anchor file) with trusted PUBLIC keys only

Examples:
  python usiphus_audit.py keygen --private alice.key --public alice.pub
  python usiphus_audit.py trust --keys trusted_keys.json --actor alice --public alice.pub
  python usiphus_audit.py append --claims claims.json --claim-id C1 --actor alice --action approve \\
        --to approved --key alice.key --output claims.new.json
  python usiphus_audit.py anchors --claims claims.new.json --actor alice --key alice.key --output anchors.json
  python usiphus_audit.py verify --claims claims.new.json --trusted-keys trusted_keys.json --anchors anchors.json

A passphrase-protected key reads its passphrase from USIPHUS_AUDIT_KEY_PASSPHRASE (or --passphrase-env).
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import usiphus_signing as sig  # noqa: E402
import usiphus_v15 as v15  # noqa: E402
from usiphus_common import atomic_write_text  # noqa: E402

VERSION = "1.0.0"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _load_claims(path: Path) -> tuple[Any, list[dict[str, Any]]]:
    payload = v15.read_json(path)
    rows = payload.get("claims") if isinstance(payload, dict) else payload
    if not isinstance(rows, list) or not all(isinstance(r, dict) for r in rows):
        raise sig.SigningError("Claims must be a list of objects or an object containing 'claims'")
    return payload, rows


def _write(path: Path, payload: Any) -> None:
    atomic_write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", path)


def _signer(args: argparse.Namespace) -> sig.Ed25519Signer:
    return sig.Ed25519Signer(args.key, sig.passphrase_from_env(args.passphrase_env))


def cmd_keygen(args: argparse.Namespace) -> dict[str, Any]:
    kid = sig.generate_keypair(Path(args.private), Path(args.public), sig.passphrase_from_env(args.passphrase_env))
    return {"key_id": kid, "private_key": str(Path(args.private).resolve()), "public_key": str(Path(args.public).resolve()),
            "note": "Keep the private key secret (mode 600). Share only the public key."}


def cmd_trust(args: argparse.Namespace) -> dict[str, Any]:
    path = Path(args.keys)
    payload: dict[str, Any] = {"keys": []}
    if path.exists():
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
        if not isinstance(payload, dict) or not isinstance(payload.get("keys"), list):
            raise sig.SigningError("Existing trusted keys file is malformed")
    entry = sig.trusted_entry(args.actor, args.public)
    kid = sig.public_key_id(entry["public_key_pem"])
    if any(e.get("actor") == args.actor and sig.public_key_id(e["public_key_pem"]) == kid for e in payload["keys"]):
        return {"added": False, "key_id": kid, "note": "Already trusted"}
    payload["keys"].append(entry)
    _write(path, payload)
    return {"added": True, "actor": args.actor, "key_id": kid}


def cmd_append(args: argparse.Namespace) -> dict[str, Any]:
    if bool(args.output) == bool(args.in_place):
        raise sig.SigningError("Specify exactly one of --output or --in-place")
    source = Path(args.claims)
    payload, rows = _load_claims(source)
    claim = next((r for r in rows if v15.clean(r.get("claim_id")) == args.claim_id), None)
    if claim is None:
        raise sig.SigningError(f"Claim not found: {args.claim_id}")
    events = claim.get("audit_events")
    if events is None:
        events = claim["audit_events"] = []
    if not isinstance(events, list):
        raise sig.SigningError("audit_events must be a list")
    if events:
        problems = v15.verify_hash_chain(events)
        if problems:
            raise sig.SigningError("Existing audit chain is already broken: " + "; ".join(problems[:3]))
    details: dict[str, Any] = json.loads(args.details_json) if args.details_json else {}
    if not isinstance(details, dict):
        raise sig.SigningError("--details-json must be a JSON object")
    if args.to:
        details["to"] = args.to
    previous = events[-1]["event_hash"] if events else "0" * 64
    event = v15.build_event(previous, args.actor, args.action, args.timestamp or _now(), details, signer=_signer(args))
    candidate = events + [event]
    new_status = args.to or v15.clean(claim.get("status") or "draft")
    problems = v15.audit_workflow_issues(candidate, new_status.casefold())
    if problems:
        raise sig.SigningError("Refusing to append: " + "; ".join(problems))
    claim["audit_events"] = candidate
    if args.to:
        claim["status"] = args.to
    _write(source if args.in_place else Path(args.output), payload)
    return {"claim_id": args.claim_id, "event_hash": event["event_hash"], "events_in_chain": len(candidate),
            "status": claim.get("status"), "key_id": _signer(args).key_id}


def _heads(rows: list[dict[str, Any]]) -> dict[str, str]:
    heads = {}
    for r in rows:
        events = r.get("audit_events")
        if isinstance(events, list) and events and isinstance(events[-1], dict) and events[-1].get("event_hash"):
            heads[v15.clean(r.get("claim_id"))] = str(events[-1]["event_hash"])
    return heads


def cmd_anchors(args: argparse.Namespace) -> dict[str, Any]:
    _, rows = _load_claims(Path(args.claims))
    heads = _heads(rows)
    doc = sig.build_anchor_document(heads, _signer(args), args.actor, _now())
    _write(Path(args.output), doc)
    return {"anchors": len(heads), "output": str(Path(args.output).resolve()),
            "next_step": "Store this file where the claims editors cannot alter it (signed git tag, auditor email, WORM storage, RFC 3161 timestamp)."}


def cmd_verify(args: argparse.Namespace) -> tuple[dict[str, Any], int]:
    trusted = sig.TrustedKeys.load(args.trusted_keys)
    _, rows = _load_claims(Path(args.claims))
    anchors = sig.load_anchor_file(args.anchors, trusted) if args.anchors else None
    problems: list[dict[str, Any]] = []
    for r in rows:
        cid = v15.clean(r.get("claim_id"))
        events = r.get("audit_events")
        if events is None:
            continue
        head = v15.clean(anchors.get(cid)) if anchors is not None else v15.clean(r.get("audit_anchor"))
        issues = v15.verify_hash_chain(events, expected_head=head, verifier=trusted)
        if anchors is not None and events and not head:
            issues.append("claim has audit events but no entry in the signed anchor file")
        if issues:
            problems.append({"claim_id": cid, "issues": issues})
    return {"claims_checked": sum(1 for r in rows if r.get("audit_events") is not None),
            "claims_with_problems": len(problems), "problems": problems,
            "anchored": anchors is not None}, (1 if problems else 0)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)

    def with_key(p: argparse.ArgumentParser) -> None:
        p.add_argument("--key", required=True, help="Private key PEM")
        p.add_argument("--passphrase-env", default=sig.PASSPHRASE_ENV)

    p = sub.add_parser("keygen"); p.add_argument("--private", required=True); p.add_argument("--public", required=True)
    p.add_argument("--passphrase-env", default=sig.PASSPHRASE_ENV, help="Environment variable holding a passphrase to encrypt the private key (optional)")
    p = sub.add_parser("trust"); p.add_argument("--keys", required=True); p.add_argument("--actor", required=True); p.add_argument("--public", required=True)
    p = sub.add_parser("append"); p.add_argument("--claims", required=True); p.add_argument("--claim-id", required=True)
    p.add_argument("--actor", required=True); p.add_argument("--action", required=True); p.add_argument("--to", choices=sorted(v15.WORKFLOW))
    p.add_argument("--timestamp"); p.add_argument("--details-json"); p.add_argument("--output"); p.add_argument("--in-place", action="store_true"); with_key(p)
    p = sub.add_parser("anchors"); p.add_argument("--claims", required=True); p.add_argument("--actor", required=True); p.add_argument("--output", required=True); with_key(p)
    p = sub.add_parser("verify"); p.add_argument("--claims", required=True); p.add_argument("--trusted-keys", required=True); p.add_argument("--anchors")

    args = parser.parse_args(argv)
    try:
        if args.command == "verify":
            result, code = cmd_verify(args)
        else:
            result, code = {"keygen": cmd_keygen, "trust": cmd_trust, "append": cmd_append, "anchors": cmd_anchors}[args.command](args), 0
        print(json.dumps(result, indent=2))
        return code
    except (OSError, ValueError, v15.V15Error, sig.SigningError, json.JSONDecodeError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
