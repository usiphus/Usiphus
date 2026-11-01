
#!/usr/bin/env python3
"""Usiphus V16: one governed release using the compatible V14 command."""
from __future__ import annotations
import argparse, contextlib, ctypes, hashlib, importlib.util, json, logging, os, shutil, sys, tempfile, uuid
from datetime import date, datetime, timezone
from pathlib import Path
from types import SimpleNamespace

VERSION = "16.0.0"
ROOT = Path(__file__).resolve().parent
LOG = logging.getLogger("usiphus.v14")
FILES = {"cards":"cards.json","curriculum":"curriculum.json","mapping":"mapping.json",
         "evidence":"evidence.json","learner_progress":"learner_progress.json",
         "cohort_analytics":"cohort_analytics.json","review_schedule":"review_schedule.csv"}
sys.path.insert(0,os.path.dirname(os.path.abspath(__file__)))
from usiphus_common import atomic_write_text  # noqa: E402

MARKER = ".usiphus_v14_bundle"
POINTER_MARKER_TEXT = "usiphus-v14-pointer-root\nThis directory holds immutable releases and a current.json pointer managed by Usiphus V14.\n"
PUBLISH_MODES = {"auto", "pointer"}
V15_KEYS = {"v15_sources","v15_claims","v15_evidence_mode","v15_block_on_failure","v15_minimum_reviewers","v15_require_registered_sources","v15_require_signed_audit","v15_require_audit_anchor","v15_audit_trusted_keys","v15_anchors"}
ALLOWED = {"schema_version","inputs","output_dir","as_of","max_evidence_age_days","run_regression_tests","publish_mode","keep_releases","require_release_ledger"} | V15_KEYS

class ReleaseError(ValueError):
    pass

def digest(path):
    h=hashlib.sha256()
    with Path(path).open("rb") as f:
        for chunk in iter(lambda:f.read(1024*1024),b""): h.update(chunk)
    return h.hexdigest()

def engine(version):
    name=f"usiphus_v{version}"
    spec=importlib.util.spec_from_file_location(name,ROOT/f"{name}.py")
    if spec is None or spec.loader is None: raise ReleaseError(f"Missing engine {name}.py")
    module=importlib.util.module_from_spec(spec)
    sys.modules[name]=module
    spec.loader.exec_module(module)
    return module

def load_config(filename):
    path=Path(filename).expanduser().resolve()
    if not path.is_file(): raise ReleaseError(f"Config not found: {path}")
    try: cfg=json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError,UnicodeDecodeError,json.JSONDecodeError) as exc: raise ReleaseError(f"Invalid config: {exc}") from exc
    if not isinstance(cfg,dict): raise ReleaseError("Config root must be an object")
    if set(cfg)-ALLOWED: raise ReleaseError("Unknown config keys: "+", ".join(sorted(set(cfg)-ALLOWED)))
    if not {"schema_version","inputs","output_dir"} <= set(cfg): raise ReleaseError("schema_version, inputs and output_dir are required")
    if type(cfg["schema_version"]) is not int or cfg["schema_version"]!=1: raise ReleaseError("schema_version must be integer 1")
    if not isinstance(cfg["inputs"],dict) or set(cfg["inputs"])!=set(FILES): raise ReleaseError("inputs must contain exactly the seven documented artifacts")
    inputs={}
    for key,value in cfg["inputs"].items():
        if not isinstance(value,str) or not value.strip(): raise ReleaseError(f"Invalid path for {key}")
        p=(path.parent/value).expanduser().resolve()
        if not p.is_file(): raise ReleaseError(f"Input {key} not found: {p}")
        if p.stat().st_size>50*1024*1024: raise ReleaseError(f"Input {key} exceeds 50 MiB")
        inputs[key]=str(p)
    v15_inputs={}
    if bool(cfg.get("v15_sources")) != bool(cfg.get("v15_claims")):
        raise ReleaseError("v15_sources and v15_claims must be configured together")
    for key in ("v15_sources","v15_claims"):
        if cfg.get(key) is not None:
            if not isinstance(cfg[key],str) or not cfg[key].strip(): raise ReleaseError(f"Invalid path for {key}")
            p=(path.parent/cfg[key]).expanduser().resolve()
            if not p.is_file(): raise ReleaseError(f"{key} not found: {p}")
            if p.stat().st_size>50*1024*1024: raise ReleaseError(f"{key} exceeds 50 MiB")
            v15_inputs[key]=str(p)
    for key in ("v15_audit_trusted_keys","v15_anchors"):
        if cfg.get(key) is not None:
            if not v15_inputs: raise ReleaseError(f"{key} requires the V15 stage (v15_sources and v15_claims)")
            if not isinstance(cfg[key],str) or not cfg[key].strip(): raise ReleaseError(f"Invalid path for {key}")
            p=(path.parent/cfg[key]).expanduser().resolve()
            if not p.is_file(): raise ReleaseError(f"{key} not found: {p}")
            if p.stat().st_size>50*1024*1024: raise ReleaseError(f"{key} exceeds 50 MiB")
            v15_inputs[key]=str(p)
    if "v15_anchors" in v15_inputs and "v15_audit_trusted_keys" not in v15_inputs:
        raise ReleaseError("v15_anchors requires v15_audit_trusted_keys so the signed anchor file can be verified")
    publish_mode=cfg.get("publish_mode","auto"); keep=cfg.get("keep_releases",5)
    if publish_mode not in PUBLISH_MODES: raise ReleaseError("publish_mode must be 'auto' or 'pointer'")
    if type(keep) is not int or keep<1: raise ReleaseError("keep_releases must be a positive integer")
    need_ledger=cfg.get("require_release_ledger",False)
    if not isinstance(need_ledger,bool): raise ReleaseError("require_release_ledger must be boolean")
    reviewers=cfg.get("v15_minimum_reviewers",1)
    if type(reviewers) is not int or reviewers<1: raise ReleaseError("v15_minimum_reviewers must be a positive integer")
    registered=cfg.get("v15_require_registered_sources",True)
    if not isinstance(registered,bool): raise ReleaseError("v15_require_registered_sources must be boolean")
    evidence_mode=cfg.get("v15_evidence_mode")
    if evidence_mode is None: evidence_mode="strict" if registered else "legacy"
    if evidence_mode not in {"strict","legacy"}: raise ReleaseError("v15_evidence_mode must be strict or legacy")
    registered = evidence_mode == "strict"
    block_v15=cfg.get("v15_block_on_failure",True)
    if not isinstance(block_v15,bool): raise ReleaseError("v15_block_on_failure must be boolean")
    signed=cfg.get("v15_require_signed_audit",True); anchored=cfg.get("v15_require_audit_anchor",True)
    if not isinstance(signed,bool) or not isinstance(anchored,bool): raise ReleaseError("v15_require_signed_audit and v15_require_audit_anchor must be boolean")
    if not isinstance(cfg["output_dir"],str) or not cfg["output_dir"].strip(): raise ReleaseError("output_dir is required")
    output=(path.parent/cfg["output_dir"]).expanduser().resolve()
    # Never publish over the repository/config directory, an input artifact,
    # or any ancestor that contains one of those protected paths.
    protected_files = [path, *(Path(value).resolve() for value in [*inputs.values(), *v15_inputs.values()])]
    protected_dirs = [ROOT, path.parent]
    for protected in protected_dirs:
        if output == protected or output in protected.parents:
            raise ReleaseError(f"output_dir cannot replace protected directory: {protected}")
    for protected in protected_files:
        if output == protected or output in protected.parents:
            raise ReleaseError(f"output_dir cannot replace or contain protected file: {protected}")
    age=cfg.get("max_evidence_age_days",730)
    if type(age) is not int or age<1: raise ReleaseError("max_evidence_age_days must be positive")
    if cfg.get("as_of") is not None:
        try: datetime.strptime(cfg["as_of"],"%Y-%m-%d")
        except (TypeError,ValueError) as exc: raise ReleaseError("as_of must be YYYY-MM-DD") from exc
    tests=cfg.get("run_regression_tests",True)
    if not isinstance(tests,bool): raise ReleaseError("run_regression_tests must be boolean")
    cfg.update(inputs=inputs,output_dir=str(output),max_evidence_age_days=age,run_regression_tests=tests,
               v15=v15_inputs,v15_minimum_reviewers=reviewers,v15_require_registered_sources=registered,
               v15_evidence_mode=evidence_mode,v15_block_on_failure=block_v15,
               v15_require_signed_audit=signed,v15_require_audit_anchor=anchored,publish_mode=publish_mode,keep_releases=keep,require_release_ledger=need_ledger)
    return cfg,path

def write_json(path,value):
    temp=Path(str(path)+".tmp-"+uuid.uuid4().hex)
    try:
        temp.write_text(json.dumps(value,indent=2,sort_keys=True,ensure_ascii=False)+"\n",encoding="utf-8")
        temp.replace(path)
    finally:
        if temp.exists(): temp.unlink()

def _marker_kind(target):
    marker=target/MARKER
    if not marker.is_file(): return None
    try: text=marker.read_text(encoding="utf-8")
    except (OSError,UnicodeDecodeError): return "bundle"
    return "pointer" if text.startswith("usiphus-v14-pointer-root") else "bundle"

def ensure_replaceable(target,mode="auto"):
    """Only touch a directory this tool created (marker) or an empty/missing one, and never switch publish modes in place."""
    if not target.exists() and not target.is_symlink(): return
    if target.is_symlink() or not target.is_dir():
        raise ReleaseError(f"output_dir exists and is not a plain directory: {target}")
    if not any(target.iterdir()): return
    kind=_marker_kind(target)
    if kind is None:
        raise ReleaseError(f"Refusing to replace {target}: it is not empty and was not created by Usiphus V14 (missing {MARKER}).")
    want="pointer" if mode=="pointer" else "bundle"
    if kind!=want:
        raise ReleaseError(f"{target} was created in '{kind}' publish mode; refusing to switch to '{want}'. Use a new output_dir.")

@contextlib.contextmanager
def release_lock(target):
    lock=target.parent/f".{target.name}.v14.lock"
    try: fd=os.open(str(lock),os.O_CREAT|os.O_EXCL|os.O_WRONLY)
    except FileExistsError: raise ReleaseError(f"Another V14 release appears to be running (lock file {lock}); delete it if it is stale.") from None
    try:
        os.write(fd,str(os.getpid()).encode()); os.close(fd)
        yield
    finally:
        with contextlib.suppress(FileNotFoundError): lock.unlink()

def _exchange(a,b):
    """Atomically swap two directories. Linux: renameat2(RENAME_EXCHANGE). macOS: renamex_np(RENAME_SWAP).
    Returns False when unsupported (other OS, old kernel, or a filesystem without support)."""
    try:
        if sys.platform.startswith("linux"):
            fn=ctypes.CDLL(None,use_errno=True).renameat2
            fn.argtypes=[ctypes.c_int,ctypes.c_char_p,ctypes.c_int,ctypes.c_char_p,ctypes.c_uint]
            return fn(-100,os.fsencode(str(a)),-100,os.fsencode(str(b)),2)==0
        if sys.platform=="darwin":
            fn=ctypes.CDLL(None,use_errno=True).renamex_np
            fn.argtypes=[ctypes.c_char_p,ctypes.c_char_p,ctypes.c_uint]
            return fn(os.fsencode(str(a)),os.fsencode(str(b)),2)==0
    except (OSError,AttributeError,TypeError): pass
    return False

def read_current(target):
    """Resolve the live release of a pointer-mode output directory (consumers should do this, then read from the result)."""
    pointer=Path(target)/"current.json"
    try: doc=json.loads(pointer.read_text(encoding="utf-8"))
    except (OSError,UnicodeDecodeError,json.JSONDecodeError) as exc: raise ReleaseError(f"Cannot read {pointer}: {exc}") from exc
    rid=doc.get("release_id") if isinstance(doc,dict) else None
    if not isinstance(rid,str) or not rid or "/" in rid or "\\" in rid or rid.startswith("."):
        raise ReleaseError("current.json has an invalid release_id")
    release=Path(target)/"releases"/rid
    if not release.is_dir(): raise ReleaseError(f"Release directory missing: {release}")
    return release

def published_manifest_path(target, mode):
    live = read_current(target) if mode == "pointer" else Path(target)
    manifest = live / "v14_manifest.json"
    if not manifest.is_file():
        raise ReleaseError(f"Published manifest not found: {manifest}")
    return manifest


def publish_pointer(stage,target,keep):
    """Immutable releases + atomically replaced current.json pointer. Works on every OS because the only
    cross-reader step is os.replace on one small file."""
    ensure_replaceable(target,"pointer")
    target.mkdir(parents=True,exist_ok=True)
    (target/MARKER).write_text(POINTER_MARKER_TEXT,encoding="utf-8")
    releases=target/"releases"; releases.mkdir(exist_ok=True)
    now=datetime.now(timezone.utc)
    rid=now.strftime("%Y%m%dT%H%M%S%fZ")+"-"+uuid.uuid4().hex[:8]
    dest=releases/rid
    try: stage.replace(dest)
    except OSError: shutil.move(str(stage),str(dest))   # different filesystem: copy, then the pointer swap below is still atomic
    manifest=dest/"v14_manifest.json"
    pointer={"schema_version":1,"release_id":rid,"path":f"releases/{rid}","published_at":now.isoformat().replace("+00:00","Z"),
             "manifest_sha256":digest(manifest) if manifest.is_file() else None}
    atomic_write_text(json.dumps(pointer,indent=2,sort_keys=True)+"\n",target/"current.json")
    for old in sorted((p for p in releases.iterdir() if p.is_dir() and p.name!=rid),key=lambda p:p.name,reverse=True)[max(keep-1,0):]:
        shutil.rmtree(old,ignore_errors=True)
    return "pointer"

def publish(stage,target,mode="auto",keep=5):
    """Publish the new bundle. Returns 'created', 'atomic-exchange', 'rename-swap' or 'pointer'.

    atomic-exchange (Linux/macOS): readers always see either the complete old or complete new bundle.
    rename-swap (other platforms/filesystems): two renames with automatic restore on failure;
    there is a very short window in which output_dir does not exist (a warning recommends pointer mode).
    pointer (publish_mode: "pointer"): immutable releases/<id>/ plus an atomically replaced current.json."""
    if mode=="pointer": return publish_pointer(stage,target,keep)
    ensure_replaceable(target,mode)
    if not target.exists():
        stage.replace(target); return "created"
    if _exchange(stage,target):
        shutil.rmtree(stage,ignore_errors=True)   # stage now holds the previous bundle
        return "atomic-exchange"
    backup=Path(str(target)+".backup-"+uuid.uuid4().hex)
    had_old=target.exists()
    try:
        if had_old: target.replace(backup)
        stage.replace(target)
    except Exception:
        if had_old and backup.exists() and not target.exists(): backup.replace(target)
        raise
    if backup.exists():
        if backup.is_dir(): shutil.rmtree(backup,ignore_errors=True)
        else: backup.unlink(missing_ok=True)
        if backup.exists(): LOG.warning("New bundle published, but the old backup could not be removed: %s",backup)
    LOG.warning("Atomic directory exchange is unavailable here; used a two-rename swap. Set publish_mode to 'pointer' for gap-free publishing.")
    return "rename-swap"

def run_release(cfg,config_path):
    target=Path(cfg["output_dir"])
    target.parent.mkdir(parents=True,exist_ok=True)
    with release_lock(target):
        ensure_replaceable(target,cfg.get("publish_mode","auto"))
        return _run_release_locked(cfg,config_path,target)

def _run_release_locked(cfg,config_path,target):
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True, prefix=f".{target.name}.v14-",dir=target.parent) as tmp:
        stage=Path(tmp)/"bundle"
        stage.mkdir()
        engines={v:engine(v) for v in range(8,14)}
        if cfg.get("v15"): engines[15]=engine(15)
        stages=[]
        if cfg["run_regression_tests"]:
            for v,module in engines.items():
                if module.run_self_test()!=0: raise ReleaseError(f"V{v} self-test failed")
                stages.append({"stage":f"V{v} self-test","status":"passed"})
        else: stages.append({"stage":"V8-V13 regression tests","status":"skipped"})
        source=stage/"source-artifacts"
        source.mkdir()
        v15_source=stage/"v15-source-artifacts"
        v15_source.mkdir()
        staged={}
        for key,name in FILES.items():
            staged[key]=source/name
            shutil.copyfile(cfg["inputs"][key],staged[key])
        # Analyze immutable snapshots rather than caller-owned mutable paths.
        v15_staged={}
        v15_names={"v15_sources":"v15_sources.json","v15_claims":"v15_claims.json",
                   "v15_audit_trusted_keys":"v15_audit_trusted_keys.json","v15_anchors":"v15_anchors.json"}
        for key,name in v15_names.items():
            if key in cfg["v15"]:
                snapshot=v15_source/name
                shutil.copyfile(cfg["v15"][key],snapshot)
                v15_staged[key]=snapshot
        out=stage/"v12"
        args=SimpleNamespace(cards=str(staged["cards"]),curriculum=str(staged["curriculum"]),
            mapping=str(staged["mapping"]),evidence=str(staged["evidence"]),
            learner_progress=str(staged["learner_progress"]),cohort_analytics=str(staged["cohort_analytics"]),
            review_schedule=str(staged["review_schedule"]),output=str(out),as_of=cfg.get("as_of"),
            max_evidence_age_days=cfg["max_evidence_age_days"])
        if engines[12].run(args)!=0: raise ReleaseError("V12 integration failed")
        stages.append({"stage":"V12 integration","status":"passed"})
        validation=engines[13].validate_bundle(source,out,stage/"v13_validation_report.json",require_release_ledger=cfg.get("require_release_ledger",False))
        if validation.get("validation_status")!="passed": raise ReleaseError("V13 validation failed")
        write_json(stage/"v13_validation_report.json",validation)
        stages.append({"stage":"V13 validation","status":"passed"})
        checklist=json.loads((out/"release_checklist.json").read_text(encoding="utf-8"))
        v12_ready=checklist.get("release_ready") is True
        v15_status=None
        v15_result=None
        if 15 in engines:
            v15=engines[15]
            verifier=v15.verifier_from_path(str(v15_staged["v15_audit_trusted_keys"]) if "v15_audit_trusted_keys" in v15_staged else os.environ.get(v15.TRUSTED_KEYS_ENV))
            anchors=v15.anchors_from_path(str(v15_staged["v15_anchors"]) if "v15_anchors" in v15_staged else None,verifier)
            v15_result=v15.analyze(
                v15.records(v15.read_json(v15_staged["v15_sources"]),"sources","Source registry"),
                v15.records(v15.read_json(v15_staged["v15_claims"]),"claims","Claim registry"),
                as_of=date.fromisoformat(cfg["as_of"]) if cfg.get("as_of") else date.today(),
                max_age_days=cfg["max_evidence_age_days"],minimum_reviewers=cfg["v15_minimum_reviewers"],
                require_registered_sources=cfg["v15_require_registered_sources"],audit_hmac_key=v15.audit_key_from_env(),
                require_signed_audit=cfg["v15_require_signed_audit"],require_audit_anchor=cfg["v15_require_audit_anchor"],
                audit_verifier=verifier,external_anchors=anchors)
            v15.write_outputs(v15_result,stage/"v15")
            v15_status=v15_result["governance_status"]
            stages.append({"stage":"V15 evidence governance","status":"passed" if v15_status=="checks_passed" else "blocked"})
        v15_blocked = v15_status == "blocked"
        if v15_blocked and cfg.get("v15_block_on_failure",True):
            details=[]
            for finding in v15_result["findings"]:
                details.append(f"{finding['kind']} {finding['id']}: {'; '.join(finding['issues'])}")
            detail_text=" | ".join(details[:8])
            if len(details)>8: detail_text += f" | ... {len(details)-8} additional finding(s)"
            raise ReleaseError(
                f"V15 evidence governance blocked this release ({len(details)} finding(s)): "
                f"{detail_text}. Previous published bundle was preserved."
            )
        ready=v12_ready and v15_status in (None,"checks_passed")
        status="ready" if ready else ("ready_with_warnings" if v15_blocked else "blocked")
        now=datetime.now(timezone.utc).isoformat().replace("+00:00","Z")
        summary={"schema_version":2,"usiphus_version":VERSION,"specialty":validation.get("specialty"),
                 "release_status":status,"v12_release_ready":v12_ready,"v15_governance_status":v15_status,"v13_validation_status":"passed",
                 "v15_evidence_mode":cfg["v15_evidence_mode"] if cfg.get("v15") else "not_configured",
                 "v15_block_on_failure":cfg.get("v15_block_on_failure",True),
                 "v15_governance":{"status":v15_status,
                     "source_count":v15_result["source_count"] if v15_result else None,
                     "claim_count":v15_result["claim_count"] if v15_result else None,
                     "legacy_reference_claims":v15_result["legacy_reference_claims"] if v15_result else None,
                     "finding_count":len(v15_result["findings"]) if v15_result else None,
                     "impact_source_count":len(v15_result["impact_index"]) if v15_result else None,
                     "findings":v15_result["findings"] if v15_result else []},
                 "generated_at":now,"stages":stages,
                 "boundary":"Software status only; not clinical approval, educational validity, competence, anonymization, or legal compliance."}
        write_json(stage/"release_summary.json",summary)
        report=["# Usiphus V16 governed release report","",f"- Specialty: {summary['specialty']}",
                f"- Release status: **{status.upper()}**","","| Stage | Status |","|---|---|"]
        report += [f"| {x['stage']} | {x['status']} |" for x in stages]
        if 15 in engines:
            report += ["",f"- V15 evidence mode: **{cfg['v15_evidence_mode']}**",
                       f"- V15 mandatory failure gate: **{cfg.get('v15_block_on_failure',True)}**",
                       "", "## V15 governance findings"]
            if v15_result["findings"]:
                report += [f"- **{row['kind']} {row['id']}**: {'; '.join(row['issues'])}"
                           for row in v15_result["findings"]]
            else:
                report.append("- No source or claim metadata findings.")
            report += ["", f"- Registered sources: {v15_result['source_count']}",
                       f"- Claims: {v15_result['claim_count']}",
                       f"- Legacy references: {v15_result['legacy_reference_claims']}",
                       f"- Source impact entries: {len(v15_result['impact_index'])}"]
        report += ["","Automated checks do not establish clinical truth, educational effectiveness,",
                   "learner competence, anonymization, or legal/institutional compliance.",
                   "Qualified faculty review remains mandatory.",""]
        (stage/"release_report.md").write_text("\n".join(report),encoding="utf-8")
        (stage/MARKER).write_text("Created by Usiphus V14. This directory may be replaced by later V14 releases.\n",encoding="utf-8")
        source_hashes={}
        for key,original in cfg["inputs"].items():
            snap=staged[key]
            source_hashes[key]={"path":original,"sha256":digest(snap),"bytes":snap.stat().st_size}
        for key,original in cfg["v15"].items():
            snap=v15_staged[key]
            source_hashes[key]={"path":original,"sha256":digest(snap),"bytes":snap.stat().st_size}
        report_hashes={}
        if (stage/"v15").is_dir():
            report_hashes={p.name:{"sha256":digest(p),"bytes":p.stat().st_size}
                           for p in sorted((stage/"v15").iterdir()) if p.is_file()}
        shutil.rmtree(source)
        shutil.rmtree(v15_source)
        output_hashes={str(p.relative_to(stage)):{"sha256":digest(p),"bytes":p.stat().st_size}
                       for p in sorted(stage.rglob("*")) if p.is_file()}
        manifest={"schema_version":2,"usiphus_version":VERSION,
            "engine_versions":{f"v{v}":engines[v].VERSION for v in engines},
            "config_sha256":digest(config_path),"inputs":source_hashes,"outputs":output_hashes,
            "release_policy":{"v15_enabled":15 in engines,
                "v15_evidence_mode":cfg["v15_evidence_mode"] if cfg.get("v15") else "not_configured",
                "v15_block_on_failure":cfg.get("v15_block_on_failure",True),
                "v15_minimum_reviewers":cfg["v15_minimum_reviewers"] if cfg.get("v15") else None,
                "v15_require_registered_sources":cfg["v15_require_registered_sources"] if cfg.get("v15") else None,
                "v15_require_signed_audit":cfg["v15_require_signed_audit"] if cfg.get("v15") else None,
                "v15_require_audit_anchor":cfg["v15_require_audit_anchor"] if cfg.get("v15") else None,
                "max_evidence_age_days":cfg["max_evidence_age_days"],
                "as_of":cfg.get("as_of")},
            "governance_report_hashes":report_hashes,
            "release_status":status,"generated_at":now,
            "note":"Hashes support integrity/change detection; timestamps vary and byte-identical bundles are not promised."}
        write_json(stage/"v14_manifest.json",manifest)
        mode=publish(stage,target,cfg.get("publish_mode","auto"),cfg.get("keep_releases",5)); LOG.info("Published release bundle (%s)",mode)
        summary["output_dir"]=str(target)
        summary["manifest"]=str(published_manifest_path(target,mode))
        return summary

def self_test():
    import unittest
    class Tests(unittest.TestCase):
        def test_hash_changes(self):
            with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as d:
                p=Path(d)/"x"; p.write_text("a"); a=digest(p); p.write_text("b"); self.assertNotEqual(a,digest(p))
        def test_config_resolves_paths_and_defaults(self):
            with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as d:
                root=Path(d)
                for name in FILES.values(): (root/name).write_text("{}")
                path=root/"cfg.json"; path.write_text(json.dumps({"schema_version":1,"inputs":FILES,"output_dir":"build"}))
                cfg,_=load_config(path)
                self.assertEqual(Path(cfg["inputs"]["cards"]).resolve(),(root/"cards.json").resolve())
                self.assertTrue(cfg["run_regression_tests"])
                self.assertEqual(cfg["max_evidence_age_days"],730)
        def test_config_rejects_extra_key(self):
            with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as d:
                root=Path(d)
                for name in FILES.values(): (root/name).write_text("{}")
                cfg={"schema_version":1,"inputs":FILES,"output_dir":"build","unexpected":True}
                path=root/"cfg.json"; path.write_text(json.dumps(cfg))
                with self.assertRaises(ReleaseError): load_config(path)
        def test_publish_replaces_previous_bundle(self):
            with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as d:
                root=Path(d); old=root/"old"; new=root/"new"; old.mkdir(); new.mkdir()
                (old/"old.txt").write_text("old"); (old/MARKER).write_text("x"); (new/"new.txt").write_text("new")
                publish(new,old)
                self.assertFalse((old/"old.txt").exists())
                self.assertEqual((old/"new.txt").read_text(),"new")
        def test_refuses_to_replace_unmarked_nonempty_directory(self):
            with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as d:
                root=Path(d); old=root/"precious"; new=root/"new"; old.mkdir(); new.mkdir()
                (old/"keep.txt").write_text("user data"); (new/"new.txt").write_text("new")
                with self.assertRaises(ReleaseError): publish(new,old)
                self.assertEqual((old/"keep.txt").read_text(),"user data")
        def test_lock_blocks_concurrent_release(self):
            with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as d:
                target=Path(d)/"out"
                with release_lock(target):
                    with self.assertRaises(ReleaseError):
                        with release_lock(target): pass
                with release_lock(target): pass
        def test_v15_inputs_must_be_paired(self):
            with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as d:
                root=Path(d)
                for name in FILES.values(): (root/name).write_text("{}")
                (root/"s.json").write_text("{}")
                path=root/"cfg.json"; path.write_text(json.dumps({"schema_version":1,"inputs":FILES,"output_dir":"build","v15_sources":"s.json"}))
                with self.assertRaises(ReleaseError): load_config(path)
    result=unittest.TextTestRunner(verbosity=2).run(unittest.defaultTestLoader.loadTestsFromTestCase(Tests))
    return 0 if result.wasSuccessful() else 2

def main(argv=None):
    parser=argparse.ArgumentParser(description="Usiphus V14 unified release platform")
    parser.add_argument("--config"); parser.add_argument("--self-test",action="store_true")
    parser.add_argument("--print-current",action="store_true",help="Print the live release directory of a pointer-mode output and exit")
    args=parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO,format="%(levelname)s | %(message)s")
    if args.self_test: return self_test()
    if not args.config: parser.error("--config is required unless --self-test is used")
    try:
        cfg,path=load_config(args.config)
        if args.print_current:
            if cfg.get("publish_mode")!="pointer": raise ReleaseError("--print-current only applies to publish_mode 'pointer'")
            print(read_current(Path(cfg["output_dir"]))); return 0
        result=run_release(cfg,path)
        print(json.dumps(result,indent=2,ensure_ascii=False))
        return 0 if result["release_status"] in {"ready","ready_with_warnings"} else 1
    except (ReleaseError,OSError,ValueError,TypeError) as exc:
        LOG.error("%s",exc); return 2

if __name__=="__main__":
    raise SystemExit(main())
