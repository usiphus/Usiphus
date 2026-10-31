#!/usr/bin/env python3
"""V5 reliability, evaluation, governance and cache services.

Clinical validation data and reviewer identities are never fabricated here:
metrics require externally adjudicated labels; signed reviewer assertions require
an institution-managed secret and remain distinct from an external IdP.
"""
from __future__ import annotations
import argparse, datetime as dt, email.utils, hashlib, hmac, json, math, os, random, sqlite3, time
from collections import defaultdict
from pathlib import Path
from typing import Any, Callable
from urllib.parse import urlparse

UTC = dt.timezone.utc
HARVEST_STATES = {"discovered", "downloaded", "parsed", "retained", "rejected", "failed"}
REVIEW_ROLES = {"clinician", "clinical_adjudicator", "editor", "admin"}


def now() -> str:
    return dt.datetime.now(UTC).replace(microsecond=0).isoformat()

def digest(value: str | bytes) -> str:
    return hashlib.sha256(value.encode() if isinstance(value, str) else value).hexdigest()

def retry_delay(retry_after: str | None, attempt: int, *, cap: float = 30.0, jitter: float = .5) -> float:
    if retry_after:
        try: return min(max(float(retry_after), 0.0), cap)
        except (TypeError, ValueError):
            try:
                stamp = email.utils.parsedate_to_datetime(retry_after)
                if stamp.tzinfo is None: stamp = stamp.replace(tzinfo=UTC)
                return min(max((stamp - dt.datetime.now(UTC)).total_seconds(), 0.0), cap)
            except (TypeError, ValueError, OverflowError): pass
    return min((2 ** min(attempt, 10)) + random.uniform(0, jitter), cap)

class HostRateLimiter:
    """Thread-safe-enough single-process per-host minimum-interval limiter."""
    def __init__(self, requests_per_second: float = 2.0):
        if requests_per_second <= 0: raise ValueError("requests_per_second must be positive")
        self.interval = 1.0 / requests_per_second
        self.next_allowed: dict[str, float] = {}
    def wait(self, url: str, sleep: Callable[[float], None] = time.sleep) -> None:
        host = (urlparse(url).hostname or "").lower()
        if not host: raise ValueError("URL must contain a host")
        current = time.monotonic(); due = self.next_allowed.get(host, current)
        delay = max(0.0, due-current)
        if delay: sleep(delay)
        self.next_allowed[host] = max(current, due) + self.interval


def init_harvest_state(path: Path) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path); conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("""CREATE TABLE IF NOT EXISTS harvest_jobs(
      candidate_id TEXT PRIMARY KEY, specialty TEXT NOT NULL, state TEXT NOT NULL,
      attempts INTEGER NOT NULL DEFAULT 0, retryable INTEGER NOT NULL DEFAULT 0,
      source_path TEXT NOT NULL DEFAULT '', source_sha256 TEXT NOT NULL DEFAULT '',
      error TEXT NOT NULL DEFAULT '', updated_at TEXT NOT NULL,
      CHECK(state IN ('discovered','downloaded','parsed','retained','rejected','failed')))""")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_harvest_jobs_state ON harvest_jobs(state,retryable)")
    conn.commit(); return conn

def checkpoint(conn: sqlite3.Connection, candidate_id: str, specialty: str, state: str,
                *, source_path: str = "", source_sha256: str = "", error: str = "", retryable: bool = False) -> None:
    if state not in HARVEST_STATES: raise ValueError(f"Invalid harvest state: {state}")
    conn.execute("""INSERT INTO harvest_jobs(candidate_id,specialty,state,attempts,retryable,source_path,source_sha256,error,updated_at)
      VALUES(?,?,?,?,?,?,?,?,?) ON CONFLICT(candidate_id) DO UPDATE SET
      specialty=excluded.specialty,state=excluded.state,attempts=harvest_jobs.attempts + CASE WHEN excluded.state='discovered' THEN 1 ELSE 0 END,
      retryable=excluded.retryable,source_path=excluded.source_path,source_sha256=excluded.source_sha256,
      error=excluded.error,updated_at=excluded.updated_at""",
      (candidate_id,specialty,state,1,int(retryable),source_path,source_sha256,error,now()))
    conn.commit()

def resumable_candidates(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    rows = conn.execute("SELECT * FROM harvest_jobs WHERE state IN ('discovered','failed') AND (state='discovered' OR retryable=1) ORDER BY updated_at,candidate_id").fetchall()
    return [dict(r) for r in rows]


def verify_cache(root: Path) -> dict[str, Any]:
    """Verify manifest, per-article metadata, XML digest and required paths."""
    manifest = root / "manifest.jsonl"; issues: list[dict[str, str]] = []; checked = 0
    if not root.is_dir(): return {"ok": False, "checked": 0, "issues": [{"kind":"missing_root","path":str(root)}]}
    if not manifest.is_file(): return {"ok": False, "checked": 0, "issues": [{"kind":"missing_manifest","path":str(manifest)}]}
    seen: set[tuple[str,str]] = set()
    for line_no, line in enumerate(manifest.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip(): continue
        try: row = json.loads(line)
        except json.JSONDecodeError:
            issues.append({"kind":"malformed_manifest_json","path":str(manifest),"line":str(line_no)}); continue
        specialty, pmcid = str(row.get("specialty", "")), str(row.get("pmcid", row.get("id", "")))
        key=(specialty,pmcid)
        if key in seen: issues.append({"kind":"duplicate_manifest_entry","path":f"{specialty}/{pmcid}"})
        seen.add(key); folder=root/specialty/pmcid; xml=folder/"article.xml"; meta=folder/"meta.json"; checked+=1
        text_file = folder / "article.txt"
        if not xml.is_file() or not meta.is_file() or not text_file.is_file():
            issues.append({"kind":"missing_required_file","path":str(folder)}); continue
        try:
            metadata=json.loads(meta.read_text(encoding="utf-8"))
            if not isinstance(metadata,dict): raise ValueError("metadata must be an object")
            version=metadata.get("schema_version")
            if not isinstance(version,int) or version < 1 or version > 5: issues.append({"kind":"unsupported_metadata_schema","path":str(meta)})
            actual=digest(xml.read_bytes())
            expected=str(metadata.get("content_sha256") or metadata.get("source_xml_sha256") or "")
            if not expected or not hmac.compare_digest(actual,expected): issues.append({"kind":"xml_hash_mismatch","path":str(xml)})
            if str(metadata.get("pmcid") or pmcid) != pmcid: issues.append({"kind":"identity_mismatch","path":str(meta)})
            if str(metadata.get("specialty") or specialty) != specialty: issues.append({"kind":"specialty_mismatch","path":str(meta)})
            status = (metadata.get("article_status") or {}).get("status") if isinstance(metadata.get("article_status"), dict) else None
            if status and status not in {"current","corrected","retracted","expression_of_concern","unknown","conflict"}:
                issues.append({"kind":"invalid_article_status","path":str(meta)})
            if row.get("content_sha256") and row["content_sha256"] != actual: issues.append({"kind":"manifest_hash_mismatch","path":str(meta)})
        except (OSError,ValueError,json.JSONDecodeError) as exc: issues.append({"kind":"invalid_metadata","path":str(meta),"detail":str(exc)})
    for xml_path in root.glob("*/*/article.xml"):
        relative = xml_path.relative_to(root)
        key = (relative.parts[0], relative.parts[1])
        if key not in seen:
            issues.append({"kind":"unlisted_article_directory","path":str(xml_path.parent)})
    return {"ok": not issues, "checked": checked, "issues": issues, "manifest_sha256": digest(manifest.read_bytes())}


def _f1(tp: int, fp: int, fn: int) -> dict[str, float | int]:
    p=tp/(tp+fp) if tp+fp else 0.0; r=tp/(tp+fn) if tp+fn else 0.0
    return {"tp":tp,"fp":fp,"fn":fn,"precision":p,"recall":r,"f1":(2*p*r/(p+r) if p+r else 0.0)}

def evaluate_gold_set(path: Path, *, dataset_version: str, protocol: str) -> dict[str, Any]:
    """Evaluate predictions against independently adjudicated JSONL labels.

    Each row: specialty, card_type, case_id, reviewer_count>=1, adjudicated=true,
    gold_supported, predicted_supported, evidence_exact, answer_claim_count,
    supported_claim_count, optional error. Synthetic rows are rejected.
    """
    if not dataset_version.strip() or not protocol.strip(): raise ValueError("dataset_version and reviewer protocol are required")
    groups: dict[tuple[str,str],list[dict[str,Any]]]=defaultdict(list); errors=[]; total=0
    assertion_attributes = ("negation", "certainty", "temporality", "experiencer", "family_history")
    for n,line in enumerate(path.read_text(encoding="utf-8").splitlines(),1):
        if not line.strip(): continue
        row=json.loads(line); total+=1
        if not isinstance(row,dict) or row.get("synthetic") is True or row.get("adjudicated") is not True:
            raise ValueError(f"Line {n}: only real, adjudicated gold labels are accepted")
        reviewer_ids = row.get("reviewer_ids")
        if not isinstance(reviewer_ids, list) or not reviewer_ids or len(set(map(str, reviewer_ids))) != len(reviewer_ids):
            raise ValueError(f"Line {n}: distinct de-identified reviewer_ids are required")
        reviewers = int(row.get("reviewer_count",0))
        if reviewers != len(reviewer_ids): raise ValueError(f"Line {n}: reviewer_count must match reviewer_ids")
        if row.get("high_risk") is True and reviewers < 2:
            raise ValueError(f"Line {n}: high-risk records require two independent reviewers")
        labels = row.get("reviewer_labels")
        if isinstance(labels, list) and len(set(map(str, labels))) > 1:
            if not row.get("adjudicator_id") or row.get("adjudicator_id") in set(map(str, reviewer_ids)):
                raise ValueError(f"Line {n}: disagreements require an independent adjudicator")
        for field in ("specialty","predicted_specialty","card_type","case_id","gold_diagnosis","predicted_diagnosis",
                      "gold_supported","predicted_supported","evidence_exact","answer_claim_count","supported_claim_count",
                      "assertion_gold","assertion_predicted"):
            if field not in row: raise ValueError(f"Line {n}: missing {field}")
        if not isinstance(row["assertion_gold"],dict) or not isinstance(row["assertion_predicted"],dict):
            raise ValueError(f"Line {n}: assertion_gold/assertion_predicted must be objects")
        if any(key not in row["assertion_gold"] or key not in row["assertion_predicted"] for key in assertion_attributes):
            raise ValueError(f"Line {n}: assertion labels must include {', '.join(assertion_attributes)}")
        if not isinstance(row["gold_supported"],bool) or not isinstance(row["predicted_supported"],bool) or not isinstance(row["evidence_exact"],bool):
            raise ValueError(f"Line {n}: boolean labels required")
        if int(row["answer_claim_count"]) < 1 or not 0 <= int(row["supported_claim_count"]) <= int(row["answer_claim_count"]):
            raise ValueError(f"Line {n}: invalid claim counts")
        groups[(str(row["specialty"]),str(row["card_type"]))].append(row)
        if row.get("error") or row["gold_supported"] != row["predicted_supported"]:
            error_type = "false_positive" if row["predicted_supported"] else "false_negative"
            errors.append({"case_id":row["case_id"],"specialty":row["specialty"],
                           "error":row.get("error") or error_type,"error_type":error_type})
    report={"dataset_version":dataset_version,"protocol":protocol,"n":total,"groups":{},"errors":errors}
    for key,rows in sorted(groups.items()):
        tp=sum(r["gold_supported"] and r["predicted_supported"] for r in rows)
        fp=sum((not r["gold_supported"]) and r["predicted_supported"] for r in rows)
        fn=sum(r["gold_supported"] and (not r["predicted_supported"]) for r in rows)
        claims=sum(int(r["answer_claim_count"]) for r in rows); covered=sum(int(r["supported_claim_count"]) for r in rows)
        assertion_accuracy = {attribute: sum(r["assertion_gold"][attribute] == r["assertion_predicted"][attribute] for r in rows)/len(rows)
                              for attribute in assertion_attributes}
        diagnosis_exact = sum(str(r["gold_diagnosis"]).strip().casefold() == str(r["predicted_diagnosis"]).strip().casefold() for r in rows)/len(rows)
        specialty_accuracy = sum(str(r["specialty"]).strip().casefold() == str(r["predicted_specialty"]).strip().casefold() for r in rows)/len(rows)
        report["groups"]["/".join(key)]={"n":len(rows),**_f1(tp,fp,fn),
          "evidence_span_exactness":sum(r["evidence_exact"] for r in rows)/len(rows),
          "unsupported_claim_rate":(claims-covered)/claims if claims else 0.0,
          "diagnosis_exact_match":diagnosis_exact,"specialty_accuracy":specialty_accuracy,
          "assertion_attribute_accuracy":assertion_accuracy,
          "claim_denominator":claims,"supported_claims":covered}
    report["source_sha256"]=digest(path.read_bytes())
    return report


def issue_reviewer_token(*, reviewer_id: str, role: str, secret: str, expires_at: str) -> dict[str,str]:
    if not reviewer_id.strip() or role not in REVIEW_ROLES or len(secret)<32: raise ValueError("reviewer_id, allowed role and >=32-character secret required")
    expiry=dt.datetime.fromisoformat(expires_at.replace("Z","+00:00"))
    if expiry.tzinfo is None or expiry <= dt.datetime.now(UTC): raise ValueError("expires_at must be a future timezone-aware timestamp")
    payload={"reviewer_id":reviewer_id.strip(),"role":role,"expires_at":expiry.astimezone(UTC).isoformat()}
    raw=json.dumps(payload,sort_keys=True,separators=(",",":")); sig=hmac.new(secret.encode(),raw.encode(),hashlib.sha256).hexdigest()
    return {"payload":raw,"signature":sig}

def verify_reviewer_token(token: dict[str,str], *, secret: str) -> dict[str,str]:
    if len(secret)<32: raise ValueError("review secret must be at least 32 characters")
    raw=str(token.get("payload", "")); signature=str(token.get("signature", ""))
    expected=hmac.new(secret.encode(),raw.encode(),hashlib.sha256).hexdigest()
    if not hmac.compare_digest(signature,expected): raise PermissionError("invalid reviewer token signature")
    payload=json.loads(raw)
    if payload.get("role") not in REVIEW_ROLES or not payload.get("reviewer_id"): raise PermissionError("invalid reviewer claims")
    expiry=dt.datetime.fromisoformat(payload["expires_at"].replace("Z","+00:00"))
    if expiry.tzinfo is None or expiry <= dt.datetime.now(UTC): raise PermissionError("reviewer token expired")
    return payload


def validate_guideline_record(row: dict[str,Any], allowlist: set[str]) -> dict[str,Any]:
    required=("guideline_id","publisher","title","stable_url","publication_date","last_updated","recommendation_text","strength_grade","accessed_at","version")
    missing=[k for k in required if not str(row.get(k," ")).strip()]
    if missing: raise ValueError("Missing guideline fields: "+", ".join(missing))
    from urllib.parse import urlparse
    parsed=urlparse(str(row["stable_url"]))
    if parsed.scheme!="https" or not parsed.netloc: raise ValueError("Guideline stable_url must be HTTPS")
    host=parsed.hostname.lower()
    if host not in allowlist: raise PermissionError(f"Guideline host is not allow-listed: {host}")
    for field in ("publication_date","last_updated","accessed_at"):
        dt.date.fromisoformat(str(row[field])[:10])
    return {**row,"source_type":"guideline","allowlisted":True,"review_status":"needs_review"}


def main(argv: list[str] | None = None) -> int:
    p=argparse.ArgumentParser(description="V5 reliability and evaluation utilities")
    sub=p.add_subparsers(dest="command",required=True)
    c=sub.add_parser("verify-cache"); c.add_argument("root",type=Path); c.add_argument("--output",type=Path)
    e=sub.add_parser("evaluate"); e.add_argument("gold_jsonl",type=Path); e.add_argument("--dataset-version",required=True); e.add_argument("--protocol",required=True); e.add_argument("--output",type=Path,required=True)
    t=sub.add_parser("issue-token"); t.add_argument("--reviewer-id",required=True); t.add_argument("--role",choices=sorted(REVIEW_ROLES),required=True); t.add_argument("--expires-at",required=True); t.add_argument("--secret-env",default="ANKI_REVIEW_HMAC_SECRET"); t.add_argument("--output",type=Path,required=True)
    args=p.parse_args(argv)
    if args.command=="issue-token":
        secret=os.environ.get(args.secret_env, "")
        if len(secret)<32: raise SystemExit(f"{args.secret_env} must contain a >=32-character signing secret")
        token=issue_reviewer_token(reviewer_id=args.reviewer_id,role=args.role,secret=secret,expires_at=args.expires_at)
        args.output.write_text(json.dumps(token,ensure_ascii=False)+"\n",encoding="utf-8")
        print(f"Wrote signed reviewer token to {args.output}")
        return 0
    if args.command=="verify-cache": result=verify_cache(args.root); out=args.output
    else: result=evaluate_gold_set(args.gold_jsonl,dataset_version=args.dataset_version,protocol=args.protocol); out=args.output
    payload=json.dumps(result,indent=2,ensure_ascii=False)+"\n"
    if out: out.write_text(payload,encoding="utf-8")
    else: print(payload,end="")
    return 0 if result.get("ok",True) else 2
if __name__=="__main__": raise SystemExit(main())
