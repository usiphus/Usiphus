#!/usr/bin/env python3
"""Usiphus V14: configuration-driven unified release orchestration."""
from __future__ import annotations
import argparse, hashlib, importlib.util, json, logging, shutil, sys, tempfile, uuid
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

VERSION = "14.0.0"
ROOT = Path(__file__).resolve().parent
LOG = logging.getLogger("usiphus.v14")
FILES = {"cards":"cards.json","curriculum":"curriculum.json","mapping":"mapping.json",
         "evidence":"evidence.json","learner_progress":"learner_progress.json",
         "cohort_analytics":"cohort_analytics.json","review_schedule":"review_schedule.csv"}
ALLOWED = {"schema_version","inputs","output_dir","as_of","max_evidence_age_days","run_regression_tests"}

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
    if not isinstance(cfg["output_dir"],str) or not cfg["output_dir"].strip(): raise ReleaseError("output_dir is required")
    output=(path.parent/cfg["output_dir"]).expanduser().resolve()
    if output in {path.parent,ROOT}: raise ReleaseError("output_dir cannot be the project or config directory")
    age=cfg.get("max_evidence_age_days",730)
    if type(age) is not int or age<1: raise ReleaseError("max_evidence_age_days must be positive")
    if cfg.get("as_of") is not None:
        try: datetime.strptime(cfg["as_of"],"%Y-%m-%d")
        except (TypeError,ValueError) as exc: raise ReleaseError("as_of must be YYYY-MM-DD") from exc
    tests=cfg.get("run_regression_tests",True)
    if not isinstance(tests,bool): raise ReleaseError("run_regression_tests must be boolean")
    cfg.update(inputs=inputs,output_dir=str(output),max_evidence_age_days=age,run_regression_tests=tests)
    return cfg,path

def write_json(path,value):
    temp=Path(str(path)+".tmp-"+uuid.uuid4().hex)
    try:
        temp.write_text(json.dumps(value,indent=2,sort_keys=True,ensure_ascii=False)+"\n",encoding="utf-8")
        temp.replace(path)
    finally:
        if temp.exists(): temp.unlink()

def publish(stage,target):
    backup=Path(str(target)+".backup-"+uuid.uuid4().hex)
    had_old=target.exists()
    try:
        if had_old: target.replace(backup)
        stage.replace(target)
    except Exception:
        if had_old and backup.exists() and not target.exists(): backup.replace(target)
        raise
    if backup.exists():
        if backup.is_dir(): shutil.rmtree(backup)
        else: backup.unlink()

def run_release(cfg,config_path):
    target=Path(cfg["output_dir"])
    target.parent.mkdir(parents=True,exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=f".{target.name}.v14-",dir=target.parent) as tmp:
        stage=Path(tmp)/"bundle"
        stage.mkdir()
        engines={v:engine(v) for v in range(8,14)}
        stages=[]
        if cfg["run_regression_tests"]:
            for v,module in engines.items():
                if module.run_self_test()!=0: raise ReleaseError(f"V{v} self-test failed")
                stages.append({"stage":f"V{v} self-test","status":"passed"})
        else: stages.append({"stage":"V8-V13 regression tests","status":"skipped"})
        source=stage/"source-artifacts"
        source.mkdir()
        staged={}
        for key,name in FILES.items():
            staged[key]=source/name
            shutil.copyfile(cfg["inputs"][key],staged[key])
        out=stage/"v12"
        args=SimpleNamespace(cards=str(staged["cards"]),curriculum=str(staged["curriculum"]),
            mapping=str(staged["mapping"]),evidence=str(staged["evidence"]),
            learner_progress=str(staged["learner_progress"]),cohort_analytics=str(staged["cohort_analytics"]),
            review_schedule=str(staged["review_schedule"]),output=str(out),as_of=cfg.get("as_of"),
            max_evidence_age_days=cfg["max_evidence_age_days"])
        if engines[12].run(args)!=0: raise ReleaseError("V12 integration failed")
        stages.append({"stage":"V12 integration","status":"passed"})
        validation=engines[13].validate_bundle(source,out,stage/"v13_validation_report.json")
        if validation.get("validation_status")!="passed": raise ReleaseError("V13 validation failed")
        write_json(stage/"v13_validation_report.json",validation)
        stages.append({"stage":"V13 validation","status":"passed"})
        checklist=json.loads((out/"release_checklist.json").read_text(encoding="utf-8"))
        ready=checklist.get("release_ready") is True
        status="ready" if ready else "blocked"
        now=datetime.now(timezone.utc).isoformat().replace("+00:00","Z")
        summary={"schema_version":1,"usiphus_version":VERSION,"specialty":validation.get("specialty"),
                 "release_status":status,"v12_release_ready":ready,"v13_validation_status":"passed",
                 "generated_at":now,"stages":stages,
                 "boundary":"Software status only; not clinical approval, educational validity, competence, anonymization, or legal compliance."}
        write_json(stage/"release_summary.json",summary)
        report=["# Usiphus V14 release report","",f"- Specialty: {summary['specialty']}",
                f"- Release status: **{status.upper()}**","","| Stage | Status |","|---|---|"]
        report += [f"| {x['stage']} | {x['status']} |" for x in stages]
        report += ["","Automated checks do not establish clinical truth, educational effectiveness,",
                   "learner competence, anonymization, or legal/institutional compliance.",
                   "Qualified faculty review remains mandatory.",""]
        (stage/"release_report.md").write_text("\n".join(report),encoding="utf-8")
        source_hashes={k:{"path":p,"sha256":digest(p),"bytes":Path(p).stat().st_size} for k,p in cfg["inputs"].items()}
        shutil.rmtree(source)
        output_hashes={str(p.relative_to(stage)):{"sha256":digest(p),"bytes":p.stat().st_size}
                       for p in sorted(stage.rglob("*")) if p.is_file()}
        manifest={"schema_version":1,"usiphus_version":VERSION,
            "engine_versions":{f"v{v}":engines[v].VERSION for v in engines},
            "config_sha256":digest(config_path),"inputs":source_hashes,"outputs":output_hashes,
            "release_status":status,"generated_at":now,
            "note":"Hashes support integrity/change detection; timestamps vary and byte-identical bundles are not promised."}
        write_json(stage/"v14_manifest.json",manifest)
        publish(stage,target)
        summary["output_dir"]=str(target)
        summary["manifest"]=str(target/"v14_manifest.json")
        return summary

def self_test():
    import unittest
    class Tests(unittest.TestCase):
        def test_hash_changes(self):
            with tempfile.TemporaryDirectory() as d:
                p=Path(d)/"x"; p.write_text("a"); a=digest(p); p.write_text("b"); self.assertNotEqual(a,digest(p))
        def test_config_resolves_paths_and_defaults(self):
            with tempfile.TemporaryDirectory() as d:
                root=Path(d)
                for name in FILES.values(): (root/name).write_text("{}")
                path=root/"cfg.json"; path.write_text(json.dumps({"schema_version":1,"inputs":FILES,"output_dir":"build"}))
                cfg,_=load_config(path)
                self.assertEqual(Path(cfg["inputs"]["cards"]),root/"cards.json")
                self.assertTrue(cfg["run_regression_tests"])
                self.assertEqual(cfg["max_evidence_age_days"],730)
        def test_config_rejects_extra_key(self):
            with tempfile.TemporaryDirectory() as d:
                root=Path(d)
                for name in FILES.values(): (root/name).write_text("{}")
                cfg={"schema_version":1,"inputs":FILES,"output_dir":"build","unexpected":True}
                path=root/"cfg.json"; path.write_text(json.dumps(cfg))
                with self.assertRaises(ReleaseError): load_config(path)
        def test_publish_replaces_previous_bundle(self):
            with tempfile.TemporaryDirectory() as d:
                root=Path(d); old=root/"old"; new=root/"new"; old.mkdir(); new.mkdir()
                (old/"old.txt").write_text("old"); (new/"new.txt").write_text("new")
                publish(new,old)
                self.assertFalse((old/"old.txt").exists())
                self.assertEqual((old/"new.txt").read_text(),"new")
    result=unittest.TextTestRunner(verbosity=2).run(unittest.defaultTestLoader.loadTestsFromTestCase(Tests))
    return 0 if result.wasSuccessful() else 2

def main(argv=None):
    parser=argparse.ArgumentParser(description="Usiphus V14 unified release platform")
    parser.add_argument("--config"); parser.add_argument("--self-test",action="store_true")
    args=parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO,format="%(levelname)s | %(message)s")
    if args.self_test: return self_test()
    if not args.config: parser.error("--config is required unless --self-test is used")
    try:
        cfg,path=load_config(args.config); result=run_release(cfg,path)
        print(json.dumps(result,indent=2,ensure_ascii=False))
        return 0 if result["release_status"]=="ready" else 1
    except (ReleaseError,OSError,ValueError,TypeError) as exc:
        LOG.error("%s",exc); return 2

if __name__=="__main__":
    raise SystemExit(main())
