
#!/usr/bin/env python3
"""Usiphus V11 — privacy-aware adaptive learning and assessment analytics.

Consumes a V10 specialty curriculum, V10 verified mapping report, and consented
pseudonymous assessment events. Uses an explainable Beta estimate and configurable
spaced repetition. Never edits source cards, curriculum, or evidence.
Educational estimates are not clinical competence determinations.
"""
from __future__ import annotations
import argparse, bisect, csv, json, logging, math, os, sys, tempfile, uuid
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Mapping, Sequence

VERSION = "11.0.0"
SCHEMA_VERSION = 1
MIN_COHORT_FLOOR = 5  # k-anonymity style floor; smaller cohorts are never reported
SETTINGS_KEYS = {"prior_alpha", "prior_beta", "mastery_thresholds", "interval_days", "minimum_attempts_for_target", "cohort_resolution"}
LOG = logging.getLogger("usiphus.v11")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import usiphus_ledger as ledger_mod  # noqa: E402
LEARNER_KEYS = {"learner_id", "consent_granted", "consented_at", "consent_revoked_at"}
ATTEMPT_KEYS = {"attempt_id", "learner_id", "card_id", "answered_at", "score"}
FORBIDDEN_KEYS = {"name", "full_name", "email", "phone", "address", "date_of_birth", "dob", "password", "free_text", "response_text"}

def clean(value: Any) -> str:
    return str(value or "").strip()

def parse_time(value: Any, label: str) -> datetime:
    raw = clean(value)
    if not raw: raise ValueError(f"{label} is required")
    try: parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError: raise ValueError(f"{label} must be an ISO-8601 timestamp") from None
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError(f"{label} must include a timezone")
    return parsed.astimezone(timezone.utc)

def learner_uuid(value: Any) -> str:
    raw = clean(value)
    try: parsed = uuid.UUID(raw)
    except (ValueError, AttributeError): raise ValueError("learner_id must be an opaque UUIDv4 pseudonym") from None
    if parsed.version != 4 or str(parsed) != raw.lower():
        raise ValueError("learner_id must be a canonical UUIDv4 pseudonym")
    return str(parsed)

def read_json(path: Path) -> Any:
    if not path.is_file(): raise ValueError(f"Input file does not exist: {path}")
    if path.stat().st_size > 50 * 1024 * 1024: raise ValueError(f"Input exceeds 50 MiB: {path}")
    with path.open("r", encoding="utf-8-sig") as handle: return json.load(handle)

def reject_extra_keys(record: dict[str, Any], allowed: set[str], label: str) -> None:
    keys = set(record)
    if keys & FORBIDDEN_KEYS: raise ValueError(f"{label} contains prohibited direct-identifier or free-text fields")
    extra = keys - allowed
    if extra: raise ValueError(f"{label} contains unsupported fields: {', '.join(sorted(extra))}")

def load_curriculum(path: Path) -> tuple[str, list[dict[str, Any]]]:
    payload = read_json(path)
    if not isinstance(payload, dict) or not isinstance(payload.get("competencies"), list):
        raise ValueError("V10 curriculum must contain a competencies list")
    specialty = clean(payload.get("specialty")).casefold()
    if not specialty: raise ValueError("Curriculum specialty is required")
    competencies, seen = [], set()
    for index, item in enumerate(payload["competencies"]):
        if not isinstance(item, dict): raise ValueError(f"Competency {index} must be an object")
        cid, title = clean(item.get("competency_id") or item.get("id")), clean(item.get("title") or item.get("name"))
        if not cid or not title or cid.casefold() in seen: raise ValueError(f"Competency {index} requires a unique ID and title")
        if cid[:1] in ("=", "+", "-", "@"): raise ValueError(f"Competency ID {cid!r} must not start with a spreadsheet formula character")
        seen.add(cid.casefold())
        weight, required = item.get("weight", 1.0), item.get("required", True)
        if isinstance(weight, bool) or not isinstance(weight, (int,float)) or not math.isfinite(weight) or weight <= 0:
            raise ValueError(f"Competency {cid} must have a finite positive weight")
        if not isinstance(required, bool): raise ValueError(f"Competency {cid} required must be boolean")
        competencies.append({"competency_id":cid,"title":title,"domain":clean(item.get("domain") or "Uncategorized"),"weight":float(weight),"required":required})
    if not competencies: raise ValueError("Curriculum must contain at least one competency")
    return specialty, competencies

def load_verified_card_map(path: Path, competency_ids: set[str], specialty: str) -> dict[str, set[str]]:
    payload = read_json(path)
    if not isinstance(payload, dict) or not isinstance(payload.get("candidates"), list):
        raise ValueError("Mapping report must be a V10 curriculum_coverage.json with candidates")
    if clean(payload.get("specialty")).casefold() != specialty.casefold():
        raise ValueError("V10 mapping report specialty does not match the curriculum")
    card_map: dict[str,set[str]] = defaultdict(set)
    for row in payload["candidates"]:
        if not isinstance(row, dict): raise ValueError("Mapping candidate rows must be objects")
        if row.get("status") != "verified": continue
        if clean(row.get("method")) != "faculty-approved" or not clean(row.get("reviewer")):
            raise ValueError("Verified mapping lacks faculty-approved provenance (method, reviewer, reviewed_at)")
        parse_time(row.get("reviewed_at"), "Verified mapping reviewed_at")
        card_id, cid = clean(row.get("card_id")), clean(row.get("competency_id"))
        if not card_id or cid.casefold() not in competency_ids: raise ValueError("Verified mapping contains an unknown card or competency ID")
        card_map[card_id].add(cid)
    if not card_map: raise ValueError("V10 mapping report contains no verified card-to-competency mappings")
    return dict(card_map)

def load_settings(path: Path | None) -> dict[str,Any]:
    cfg = {"prior_alpha":1.0,"prior_beta":1.0,"mastery_thresholds":[0.50,0.65,0.80,0.90],
           "interval_days":[1,2,4,7,14],"minimum_attempts_for_target":3,"cohort_resolution":0.01}
    if path is not None:
        payload=read_json(path)
        if not isinstance(payload,dict): raise ValueError("Settings must be a JSON object")
        unknown = set(payload) - SETTINGS_KEYS
        if unknown: raise ValueError("Unknown settings keys: " + ", ".join(sorted(unknown)))
        cfg.update(payload)
    res=cfg["cohort_resolution"]
    if isinstance(res,bool) or not isinstance(res,(int,float)) or not math.isfinite(res) or not 0<res<=1:
        raise ValueError("cohort_resolution must be a number greater than 0 and at most 1 (e.g. 0.01, 0.05, 0.1)")
    for key in ("prior_alpha","prior_beta"):
        x=cfg[key]
        if isinstance(x,bool) or not isinstance(x,(int,float)) or not math.isfinite(x) or x<=0:
            raise ValueError(f"{key} must be finite and positive")
    thresholds, intervals = cfg["mastery_thresholds"], cfg["interval_days"]
    if not isinstance(thresholds,list) or not thresholds or any(isinstance(x,bool) or not isinstance(x,(int,float)) or not 0<x<1 for x in thresholds):
        raise ValueError("mastery_thresholds must be a non-empty list in (0,1)")
    if thresholds != sorted(set(thresholds)): raise ValueError("mastery_thresholds must be strictly increasing")
    if not isinstance(intervals,list) or len(intervals)!=len(thresholds)+1 or any(isinstance(x,bool) or not isinstance(x,int) or x<1 for x in intervals):
        raise ValueError("interval_days must have one positive integer per mastery band")
    if any(a>b for a,b in zip(intervals,intervals[1:])): raise ValueError("interval_days must be non-decreasing")
    n=cfg["minimum_attempts_for_target"]
    if isinstance(n,bool) or not isinstance(n,int) or n<1: raise ValueError("minimum_attempts_for_target must be positive")
    return cfg

def load_events(path: Path, as_of: datetime):
    payload=read_json(path)
    if not isinstance(payload,dict) or not isinstance(payload.get("learners"),list) or not isinstance(payload.get("attempts"),list):
        raise ValueError("Attempt file requires learners and attempts lists")
    privacy=payload.get("privacy")
    if not isinstance(privacy,dict): raise ValueError("Attempt file requires privacy.retention_days")
    reject_extra_keys(privacy,{"retention_days","minimum_cohort_size"},"privacy settings")
    retention, cohort_min=privacy.get("retention_days"),privacy.get("minimum_cohort_size",5)
    if isinstance(retention,bool) or not isinstance(retention,int) or retention<1: raise ValueError("retention_days must be positive")
    if isinstance(cohort_min,bool) or not isinstance(cohort_min,int) or cohort_min<MIN_COHORT_FLOOR: raise ValueError(f"minimum_cohort_size must be at least {MIN_COHORT_FLOOR}")
    learners={}
    for i,item in enumerate(payload["learners"]):
        if not isinstance(item,dict): raise ValueError(f"Learner consent record {i} must be an object")
        reject_extra_keys(item,LEARNER_KEYS,f"Learner consent record {i}")
        lid=learner_uuid(item.get("learner_id"))
        if lid in learners: raise ValueError(f"Duplicate learner consent record: {lid}")
        granted=item.get("consent_granted")
        if not isinstance(granted,bool): raise ValueError(f"consent_granted must be boolean for {lid}")
        consented=parse_time(item.get("consented_at"),f"consented_at for {lid}") if granted else None
        revoked=parse_time(item["consent_revoked_at"],f"consent_revoked_at for {lid}") if item.get("consent_revoked_at") else None
        if consented is not None and revoked is not None and revoked < consented:
            raise ValueError(f"consent_revoked_at precedes consented_at for {lid}")
        learners[lid]={"granted":granted,"consented":consented,"revoked":revoked}
    cutoff=as_of-timedelta(days=retention)
    accepted,seen=[],set()
    counts={"excluded_no_consent":0,"excluded_retention":0,"excluded_revoked":0}
    for i,item in enumerate(payload["attempts"]):
        if not isinstance(item,dict): raise ValueError(f"Attempt {i} must be an object")
        reject_extra_keys(item,ATTEMPT_KEYS,f"Attempt {i}")
        aid=clean(item.get("attempt_id"))
        if not aid or aid in seen: raise ValueError(f"Attempt {i} requires a unique attempt_id")
        seen.add(aid)
        lid=learner_uuid(item.get("learner_id"))
        if lid not in learners: raise ValueError(f"Attempt {aid} has no corresponding consent record")
        when=parse_time(item.get("answered_at"),f"answered_at for {aid}")
        if when>as_of: raise ValueError(f"Attempt {aid} is dated in the future")
        consent=learners[lid]
        if not consent["granted"] or consent["consented"] is None or when<consent["consented"]:
            counts["excluded_no_consent"]+=1; continue
        if consent["revoked"] is not None and when>=consent["revoked"]:
            counts["excluded_revoked"]+=1; continue
        if when<cutoff: counts["excluded_retention"]+=1; continue
        score=item.get("score")
        if isinstance(score,bool) or not isinstance(score,(int,float)) or not math.isfinite(score) or not 0<=score<=1:
            raise ValueError(f"Attempt {aid} score must be finite and between 0 and 1")
        card=clean(item.get("card_id"))
        if not card: raise ValueError(f"Attempt {aid} requires card_id")
        accepted.append({"attempt_id":aid,"learner_id":lid,"card_id":card,"answered_at":when,"score":float(score)})
    return accepted,counts,cohort_min,retention

def estimate_mastery(scores: Sequence[float], alpha0: float, beta0: float):
    alpha,beta=alpha0+sum(scores),beta0+sum(1.0-s for s in scores)
    return alpha/(alpha+beta),alpha,beta

def interval_for(mastery: float, settings: dict[str,Any]) -> int:
    return settings["interval_days"][bisect.bisect_right(settings["mastery_thresholds"],mastery)]

def apply_complementary_suppression(cohort: list[dict[str,Any]], cohort_values: Mapping[str,list[dict[str,Any]]], k: int) -> int:
    """Suppress one cohort from every overlapping pair with a small symmetric difference.

    Published counts and means can be differenced after multiplying mean by count.
    This checks overlapping, non-nested cohorts as well as strict subsets. One member
    of each unsafe pair is suppressed; this is not differential privacy.
    """
    index={row["competency_id"]:i for i,row in enumerate(cohort)}
    members={cid:frozenset(v["lid"] for v in cohort_values.get(cid,[])) for cid in index}
    suppressed=0
    while True:
        live=sorted((cid for cid in index if not cohort[index[cid]].get("suppressed")),
                    key=lambda c:(len(members[c]),c))
        victim=None
        for i,left in enumerate(live):
            for right in live[i+1:]:
                a,b=members[left],members[right]
                if not (a & b) or a == b:
                    continue
                if (0 < len(a-b) < k) or (0 < len(b-a) < k):
                    victim=left
                    break
            if victim:
                break
        if victim is None:
            return suppressed
        row=cohort[index[victim]]
        cohort[index[victim]]={"competency_id":victim,"title":row["title"],"domain":row["domain"],
            "suppressed":True,
            "suppression_reason":f"Complementary suppression: an overlapping cohort pair differs by fewer than {k} learners"}
        suppressed+=1

def coarsen(value: float, resolution: float) -> float:
    """Round to a multiple of `resolution` (coarser published means leak less about individuals)."""
    return round(round(value/resolution)*resolution,6)

def apply_ledger(cohort: list[dict[str,Any]], cohort_values: Mapping[str,list[dict[str,Any]]], k: int,
                 ledger: dict[str,Any], mode: str) -> tuple[int,dict[str,frozenset[str]]]:
    """Suppress cohorts that would allow differencing against EARLIER releases. Returns (n suppressed, published member hashes)."""
    index={row["competency_id"]:i for i,row in enumerate(cohort)}
    current={cid:ledger_mod.hashed_members(ledger,[v["lid"] for v in cohort_values.get(cid,[])])
             for cid in index if not cohort[index[cid]].get("suppressed")}
    flagged=ledger_mod.conflicts(current,ledger,k,mode)
    for cid,reason in flagged.items():
        row=cohort[index[cid]]
        cohort[index[cid]]={"competency_id":cid,"title":row["title"],"domain":row["domain"],"suppressed":True,"suppression_reason":reason}
    return len(flagged),{cid:m for cid,m in current.items() if cid not in flagged}

def build_analytics(specialty,competencies,card_map,attempts,excluded,cohort_min,retention,settings,as_of,
                    ledger=None,ledger_mode="subset",published_members_out=None):
    by_pair: dict[tuple[str,str],list[dict[str,Any]]]=defaultdict(list)
    learners=set(); unmapped=0
    for event in attempts:
        mapped=card_map.get(event["card_id"])
        if not mapped: unmapped+=1; continue
        learners.add(event["learner_id"])
        for cid in mapped: by_pair[(event["learner_id"],cid)].append(event)
    excluded=dict(excluded); excluded["excluded_unmapped"]=unmapped
    individual=[]; cohort_values=defaultdict(list); schedule=[]
    for lid in sorted(learners):
        rows=[]
        for comp in competencies:
            cid=comp["competency_id"]
            events=sorted(by_pair.get((lid,cid),[]),key=lambda e:(e["answered_at"],e["attempt_id"]))
            if not events and not any(cid in values for values in card_map.values()): continue
            mastery,alpha,beta=estimate_mastery([e["score"] for e in events],settings["prior_alpha"],settings["prior_beta"])
            last=events[-1]["answered_at"] if events else None
            interval=interval_for(mastery,settings)
            due=last+timedelta(days=interval) if last else as_of
            target=len(events)>=settings["minimum_attempts_for_target"] and mastery>=settings["mastery_thresholds"][-1]
            status="not_started" if not events else ("study_target_met" if target else ("review_due" if due<=as_of else "learning"))
            row={"competency_id":cid,"title":comp["title"],"domain":comp["domain"],"attempt_count":len(events),
                 "mastery_estimate":round(mastery,4),"posterior_alpha":round(alpha,4),"posterior_beta":round(beta,4),
                 "last_attempt_at":last.isoformat().replace("+00:00","Z") if last else None,
                 "recommended_interval_days":interval,"next_due_at":due.isoformat().replace("+00:00","Z"),
                 "status":status,"interpretation":"Educational study estimate only; not a clinical competence determination."}
            rows.append(row)
            if events: cohort_values[cid].append({"lid":lid,"mastery":mastery,"attempts":len(events)})
            schedule.append({"learner_id":lid,"competency_id":cid,"next_due_at":row["next_due_at"],
                "recommended_interval_days":interval,"status":status,"mastery_estimate":row["mastery_estimate"]})
        individual.append({"learner_id":lid,"competencies":rows})
    cohort=[]
    for comp in competencies:
        values=cohort_values.get(comp["competency_id"],[])
        if len(values)<cohort_min:
            cohort.append({"competency_id":comp["competency_id"],"title":comp["title"],"domain":comp["domain"],
                "suppressed":True,"suppression_reason":f"Fewer than minimum cohort size ({cohort_min})"})
        else:
            cohort.append({"competency_id":comp["competency_id"],"title":comp["title"],"domain":comp["domain"],
                "suppressed":False,"learner_count":len(values),
                "mean_mastery_estimate":coarsen(sum(x["mastery"] for x in values)/len(values),settings.get("cohort_resolution",0.01)),
                "mean_attempts_per_learner":round(sum(x["attempts"] for x in values)/len(values),2)})
    apply_complementary_suppression(cohort,cohort_values,cohort_min)
    ledger_summary={"used":ledger is not None,"mode":ledger_mode if ledger is not None else None,
                    "prior_releases":len(ledger["releases"]) if ledger is not None else 0,"suppressed_by_ledger":0}
    if ledger is not None:
        n_flagged,published=apply_ledger(cohort,cohort_values,cohort_min,ledger,ledger_mode)
        ledger_summary["suppressed_by_ledger"]=n_flagged
        if published_members_out is not None: published_members_out.update(published)
    ledger_note=("Cross-release differencing is checked against a release ledger ("+str(ledger_mode)+" mode); outside knowledge and "
                 "re-identification from small numbers are NOT addressed, and this is not differential privacy." if ledger is not None else
                 "Differencing across separate releases (or against outside knowledge) is NOT prevented because no --release-ledger was used. Treat outputs as sensitive.")
    learner_report={"schema_version":SCHEMA_VERSION,"usiphus_version":VERSION,"specialty":specialty,
        "generated_at":as_of.isoformat().replace("+00:00","Z"),
        "privacy":{"consent_required":True,"retention_days":retention,
            "retention_cutoff":(as_of-timedelta(days=retention)).isoformat().replace("+00:00","Z"),
            "learner_ids_are_pseudonymous":True,"raw_answers_stored":False},
        "active_learners":len(learners),"accepted_attempts":sum(1 for event in attempts if card_map.get(event["card_id"])),
        "excluded_events":excluded,"learners":individual,
        "limitations":["Mastery is a descriptive educational estimate, not proof of knowledge or clinical competence.",
            "Only attempts on cards with V10 faculty-verified competency mappings contribute.",
            "UUID format and consent flags cannot independently verify identity, consent authenticity, or legal compliance."]}
    cohort_report={"schema_version":SCHEMA_VERSION,"usiphus_version":VERSION,"specialty":specialty,
        "generated_at":as_of.isoformat().replace("+00:00","Z"),"minimum_cohort_size":cohort_min,
        "competencies":cohort,"suppressed_competencies":sum(bool(x.get("suppressed")) for x in cohort),
        "limitations":["Small cohorts are suppressed; do not use aggregate estimates for high-stakes decisions.",
            "Overlapping cohorts within one release are complementary-suppressed.",ledger_note],
        "release_ledger":ledger_summary}
    return learner_report,cohort_report,sorted(schedule,key=lambda x:(x["next_due_at"],x["learner_id"],x["competency_id"]))

def atomic_write(path: Path,content: str):
    path.parent.mkdir(parents=True,exist_ok=True); temp=None
    try:
        with tempfile.NamedTemporaryFile("w",encoding="utf-8",dir=path.parent,delete=False) as handle:
            temp=handle.name; handle.write(content); handle.flush()
        Path(temp).replace(path)
    finally:
        if temp and Path(temp).exists(): Path(temp).unlink()

def write_outputs(output,learner,cohort,schedule):
    atomic_write(output/"learner_progress.json",json.dumps(learner,indent=2,ensure_ascii=False)+"\n")
    atomic_write(output/"cohort_analytics.json",json.dumps(cohort,indent=2,ensure_ascii=False)+"\n")
    import io
    buf=io.StringIO(); fields=["learner_id","competency_id","next_due_at","recommended_interval_days","status","mastery_estimate"]
    writer=csv.DictWriter(buf,fieldnames=fields); writer.writeheader(); writer.writerows(schedule)
    atomic_write(output/"review_schedule.csv",buf.getvalue())
    manifest={"usiphus_version":VERSION,"schema_version":SCHEMA_VERSION,
        "outputs":["learner_progress.json","cohort_analytics.json","review_schedule.csv"],
        "source_files_modified":False,"privacy_note":"Outputs contain pseudonymous learner identifiers; protect as sensitive educational records."}
    atomic_write(output/"v11_manifest.json",json.dumps(manifest,indent=2)+"\n")

def run(curriculum_path,mapping_path,attempts_path,output,settings_path,as_of_raw,
        ledger_path=None,ledger_mode="subset",update_ledger=True):
    as_of=parse_time(as_of_raw,"--as-of") if as_of_raw else datetime.now(timezone.utc)
    specialty,competencies=load_curriculum(curriculum_path)
    ids={c["competency_id"].casefold() for c in competencies}
    card_map=load_verified_card_map(mapping_path,ids,specialty); settings=load_settings(settings_path)
    attempts,excluded,cohort_min,retention=load_events(attempts_path,as_of)
    ledger=ledger_mod.load_ledger(ledger_path) if ledger_path else None
    published={}
    learner,cohort,schedule=build_analytics(specialty,competencies,card_map,attempts,excluded,cohort_min,retention,settings,as_of,
        ledger=ledger,ledger_mode=ledger_mode,published_members_out=published)
    write_outputs(output,learner,cohort,schedule)
    if ledger is not None and update_ledger:   # recorded only after outputs were written successfully
        ledger_mod.record_release(ledger,uuid.uuid4().hex,as_of.isoformat().replace("+00:00","Z"),published)
        ledger_mod.save_ledger(Path(ledger_path),ledger)
        LOG.info("V11: release ledger updated (%d releases recorded)",len(ledger["releases"]))
    LOG.info("V11: %d active learners; %d accepted attempts; %d cohort metrics suppressed",
        learner["active_learners"],learner["accepted_attempts"],cohort["suppressed_competencies"])
    return 0

def run_self_test():
    import unittest
    class V11Tests(unittest.TestCase):
        def setUp(self):
            self.cfg=load_settings(None); self.now=parse_time("2026-10-31T12:00:00Z","test")
            self.comps=[{"competency_id":"U1","title":"Retention","domain":"Emergency","weight":2.0,"required":True},
                        {"competency_id":"U2","title":"Renal colic","domain":"Stone","weight":1.0,"required":True}]
            self.map={"card-1":{"U1"},"card-2":{"U2"}}
            self.a="123e4567-e89b-42d3-a456-426614174000"; self.b="123e4567-e89b-42d3-a456-426614174001"
            self.events=[{"attempt_id":"a1","learner_id":self.a,"card_id":"card-1","answered_at":parse_time("2026-10-30T12:00:00Z","t"),"score":1.0},
                         {"attempt_id":"a2","learner_id":self.a,"card_id":"card-1","answered_at":parse_time("2026-10-31T10:00:00Z","t"),"score":0.8},
                         {"attempt_id":"a3","learner_id":self.b,"card_id":"card-2","answered_at":parse_time("2026-10-31T11:00:00Z","t"),"score":0.5}]
        def test_beta_prior_and_updates(self):
            self.assertEqual(estimate_mastery([1,0],1,1),(0.5,2,2))
        def test_high_scores_raise_estimate(self): self.assertGreater(estimate_mastery([1,1,1],1,1)[0],0.5)
        def test_low_scores_lower_estimate(self): self.assertLess(estimate_mastery([0,0,0],1,1)[0],0.5)
        def test_fractional_score(self): self.assertEqual(estimate_mastery([0.5],1,1)[0],0.5)
        def test_schedule_bands(self):
            self.assertEqual([interval_for(x,self.cfg) for x in (0.2,0.6,0.7,0.85,0.95)],[1,2,4,7,14])
        def test_uuidv4_only(self):
            self.assertEqual(learner_uuid(self.a),self.a)
            with self.assertRaises(ValueError): learner_uuid("not-a-uuid")
        def test_naive_time_rejected(self):
            with self.assertRaises(ValueError): parse_time("2026-10-31T12:00:00","test")
        def test_only_verified_mappings_loaded(self):
            with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as d:
                p=Path(d)/"map.json"; p.write_text(json.dumps({"specialty":"urology","candidates":[
                    {"card_id":"card-1","competency_id":"U1","status":"suggested"},
                    {"card_id":"card-2","competency_id":"U2","status":"verified","method":"faculty-approved","reviewer":"Faculty","reviewed_at":"2026-10-31T12:00:00Z"}]}))
                self.assertEqual(load_verified_card_map(p,{"u1","u2"},"urology"),{"card-2":{"U2"}})
        def test_unknown_verified_mapping_rejected(self):
            with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as d:
                p=Path(d)/"map.json"; p.write_text(json.dumps({"specialty":"urology","candidates":[{"card_id":"c","competency_id":"BAD","status":"verified"}]}))
                with self.assertRaises(ValueError): load_verified_card_map(p,{"u1"},"urology")
        def test_small_cohort_suppressed(self):
            _,cohort,_=build_analytics("urology",self.comps,self.map,self.events[:2],{},2,365,self.cfg,self.now)
            self.assertTrue(all(x["suppressed"] for x in cohort["competencies"]))
        def test_cohort_visible_at_minimum(self):
            events=self.events+[{"attempt_id":"a4","learner_id":self.b,"card_id":"card-1","answered_at":self.now,"score":1.0}]
            _,cohort,_=build_analytics("urology",self.comps,self.map,events,{},2,365,self.cfg,self.now)
            row=next(x for x in cohort["competencies"] if x["competency_id"]=="U1")
            self.assertFalse(row["suppressed"]); self.assertEqual(row["learner_count"],2)
        def test_overlapping_non_nested_cohorts_are_suppressed(self):
            rows=[{"competency_id":cid,"title":cid,"domain":"D","suppressed":False} for cid in ("A","B")]
            values={"A":[{"lid":str(i)} for i in (1,2,3,4,5,6)],
                    "B":[{"lid":str(i)} for i in (1,2,3,4,5,7)]}
            self.assertEqual(apply_complementary_suppression(rows,values,5),1)
            self.assertEqual(sum(bool(row["suppressed"]) for row in rows),1)
        def test_unmapped_attempt_excluded(self):
            learner,_,_=build_analytics("urology",self.comps,{"card-1":{"U1"}},self.events,{},2,365,self.cfg,self.now)
            self.assertEqual(learner["excluded_events"]["excluded_unmapped"],1)
        def test_retention_excludes_old_attempt(self):
            with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as d:
                p=Path(d)/"events.json"; p.write_text(json.dumps({"privacy":{"retention_days":30},
                    "learners":[{"learner_id":self.a,"consent_granted":True,"consented_at":"2026-01-01T00:00:00Z"}],
                    "attempts":[{"attempt_id":"old","learner_id":self.a,"card_id":"card-1","answered_at":"2026-01-01T00:00:00Z","score":1}]}))
                accepted,counts,_,_=load_events(p,self.now)
                self.assertEqual(accepted,[]); self.assertEqual(counts["excluded_retention"],1)
        def test_revoked_consent_excluded(self):
            with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as d:
                p=Path(d)/"events.json"; p.write_text(json.dumps({"privacy":{"retention_days":365},
                    "learners":[{"learner_id":self.a,"consent_granted":True,"consented_at":"2026-01-01T00:00:00Z","consent_revoked_at":"2026-10-30T00:00:00Z"}],
                    "attempts":[{"attempt_id":"revoked","learner_id":self.a,"card_id":"card-1","answered_at":"2026-10-30T01:00:00Z","score":1}]}))
                accepted,counts,_,_=load_events(p,self.now)
                self.assertEqual(accepted,[]); self.assertEqual(counts["excluded_revoked"],1)
        def test_direct_identifiers_rejected(self):
            with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as d:
                p=Path(d)/"events.json"; p.write_text(json.dumps({"privacy":{"retention_days":365},
                    "learners":[{"learner_id":self.a,"consent_granted":True,"consented_at":"2026-01-01T00:00:00Z","email":"x"}],"attempts":[]}))
                with self.assertRaises(ValueError): load_events(p,self.now)
        def test_invalid_score_rejected(self):
            with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as d:
                p=Path(d)/"events.json"; p.write_text(json.dumps({"privacy":{"retention_days":365},
                    "learners":[{"learner_id":self.a,"consent_granted":True,"consented_at":"2026-01-01T00:00:00Z"}],
                    "attempts":[{"attempt_id":"bad","learner_id":self.a,"card_id":"card-1","answered_at":"2026-10-30T00:00:00Z","score":1.5}]}))
                with self.assertRaises(ValueError): load_events(p,self.now)
        def test_source_mapping_not_mutated(self):
            before={k:set(v) for k,v in self.map.items()}
            build_analytics("urology",self.comps,self.map,self.events,{},2,365,self.cfg,self.now)
            self.assertEqual(self.map,before)
        def test_unseen_competency_due_now(self):
            _,_,schedule=build_analytics("urology",self.comps,self.map,[self.events[0]],{},2,365,self.cfg,self.now)
            unseen=next(row for row in schedule if row["competency_id"]=="U2")
            self.assertEqual(unseen["next_due_at"],self.now.isoformat().replace("+00:00","Z"))
            self.assertEqual(unseen["status"],"not_started")
        def test_outputs_written(self):
            learner,cohort,schedule=build_analytics("urology",self.comps,self.map,self.events,{},2,365,self.cfg,self.now)
            with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as d:
                write_outputs(Path(d),learner,cohort,schedule)
                for name in ("learner_progress.json","cohort_analytics.json","review_schedule.csv","v11_manifest.json"):
                    self.assertTrue((Path(d)/name).exists())
    result=unittest.TextTestRunner(verbosity=2).run(unittest.defaultTestLoader.loadTestsFromTestCase(V11Tests))
    return 0 if result.wasSuccessful() else 2

def parse_args(argv: Sequence[str] | None = None):
    p=argparse.ArgumentParser(description="Usiphus V11 adaptive learning and assessment analytics")
    p.add_argument("--curriculum"); p.add_argument("--mapping"); p.add_argument("--attempts")
    p.add_argument("--settings"); p.add_argument("--output",default="./build-v11")
    p.add_argument("--release-ledger",help="Path to the release ledger (created on first use). Protects against differencing across releases; keep it with the learner data.")
    p.add_argument("--ledger-mode",choices=ledger_mod.MODES,default="subset",help="subset (default) or strict (also blocks sliding-window churn on the same competency)")
    p.add_argument("--no-ledger-update",action="store_true",help="Check against the ledger but do not record this release (dry run)")
    p.add_argument("--as-of"); p.add_argument("--self-test",action="store_true"); p.add_argument("--verbose",action="store_true")
    return p.parse_args(argv)

def main(argv: Sequence[str] | None = None):
    args=parse_args(argv)
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s | %(levelname)s | %(name)s | %(message)s")
    if args.self_test: return run_self_test()
    if not args.curriculum or not args.mapping or not args.attempts:
        LOG.error("--curriculum, --mapping, and --attempts are required unless --self-test is used"); return 2
    try:
        return run(Path(args.curriculum),Path(args.mapping),Path(args.attempts),Path(args.output),
            Path(args.settings) if args.settings else None,args.as_of,
            ledger_path=Path(args.release_ledger) if args.release_ledger else None,
            ledger_mode=args.ledger_mode,update_ledger=not args.no_ledger_update)
    except (ValueError,OSError,json.JSONDecodeError) as exc:
        LOG.error("%s",exc); return 2

if __name__ == "__main__":
    raise SystemExit(main())
