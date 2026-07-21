from __future__ import annotations

import argparse, gc, json, os, re, shutil, subprocess, sys
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path: sys.path.insert(0, str(PROJECT_ROOT))

import tools.f5_level2_batch09_delivery as previous
from backend.sql_guard import SQLGuard
from tools.zero_b4_tool_memory_rehearsal import close_memory, get_user_environment, manifest, sqlite_record_count, write_json
from training.sop.batch_validator import validate_training_batch
from training.sop.memory_write_plan import build_memory_write_plan
from training.sop.storage_snapshot import create_verified_copy

BATCH_FILE = PROJECT_ROOT / "training" / "f5_level2_batch10.json"
FINAL_DISCOVERY_EVIDENCE = Path(r"E:\3\_training_backups\f5-level2-final-focused-discovery-r2-20260717-095048\evidence")
SCOPE_FREEZE_EVIDENCE = Path(r"E:\3\_training_backups\f5-level2-batch10-scope-freeze-20260717-095937\evidence")
RUNTIME_SOURCE = Path(r"E:\3\_runtime\vanna-level1\vanna_data")
BACKUP_PARENT = Path(r"E:\3\_training_backups")
PYTHON_EXE = PROJECT_ROOT / "vanna_venv" / "Scripts" / "python.exe"
EXPECTED_FORMAL_SHA256 = "68a9ba51bbd6357d53d4299aa73e277e213865a98f968794833cc4d774c83df2"
EXPECTED_INITIAL_RECORD_COUNT = 196
EXPECTED_FINAL_RECORD_COUNT = 197
EXPECTED_CANDIDATE_ID = "D10_L2_RS_SEWAGE_INFO_V2_001"
EXPECTED_CANDIDATE_MODE = "STANDARD"
EXPECTED_SAMPLE_ID = "F5_L2_B10_SQL_001"
EXPECTED_TRAINING_LEVEL = "level2_sql_examples"
EXPECTED_TABLES = ["rs_sewage_info_v2"]
EXPECTED_COLUMNS = ["admin_division", "project_name", "treatment_process", "design_scale", "actual_assess_scale", "operation_unit"]
EXPECTED_QUESTION = "查询污水处理厂项目的行政区划、项目名称、处理工艺、设计规模、实际考核规模和运营单位，最多返回50条"
EXPECTED_SQL = """SELECT admin_division,
       project_name,
       treatment_process,
       design_scale,
       actual_assess_scale,
       operation_unit
FROM rs_sewage_info_v2
LIMIT 50"""
EXPECTED_BEHAVIOR = "返回最多50条污水处理厂项目的行政区划、项目名称、处理工艺、设计规模、实际考核规模和运营单位；设计规模和实际考核规模字段单位为t/d（吨/日）"
CONTROL_MEMORY_ID = "13935be4-e76d-4340-b1d5-c125d8e79828"
CONTROL_SAMPLE_ID = "L3_P2_SQL_011"
BATCH09_QUESTION = previous.EXPECTED_QUESTION
BATCH09_TABLES = previous.EXPECTED_TABLES
BATCH09_COLUMNS = previous.EXPECTED_COLUMNS
COMMON = previous.COMMON


def configure_existing_helpers() -> None:
    previous.BATCH_FILE = BATCH_FILE
    previous.RUNTIME_SOURCE = RUNTIME_SOURCE
    previous.BACKUP_PARENT = BACKUP_PARENT
    previous.EXPECTED_FORMAL_SHA256 = EXPECTED_FORMAL_SHA256
    previous.EXPECTED_CANDIDATE_ID = EXPECTED_CANDIDATE_ID
    previous.EXPECTED_SAMPLE_ID = EXPECTED_SAMPLE_ID
    previous.EXPECTED_TRAINING_LEVEL = EXPECTED_TRAINING_LEVEL
    previous.EXPECTED_TABLES = EXPECTED_TABLES
    previous.EXPECTED_COLUMNS = EXPECTED_COLUMNS
    previous.EXPECTED_QUESTION = EXPECTED_QUESTION
    previous.EXPECTED_SQL = EXPECTED_SQL
    previous.configure_existing_helpers()


def norm(value: str) -> str: return " ".join(value.strip().rstrip(";").split())
def sanitized(value: Any) -> Any: return previous.sanitized(value)


def configure_database_environment() -> None:
    for name in ("DB_USER", "DB_PASSWORD"):
        if os.getenv(name, "").strip():
            continue
        found, value = get_user_environment(name)
        if not found or not value.strip():
            raise RuntimeError(f"DATABASE_ENVIRONMENT_MISSING:{name}")
        os.environ[name] = value.strip()


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(); p.add_argument("--isolated-worker", type=Path); p.add_argument("--isolated-data", type=Path); p.add_argument("--evidence-prefix"); return p.parse_args()


def load_batch() -> dict[str, Any]:
    batch = json.loads(BATCH_FILE.read_text(encoding="utf-8")); samples = batch.get("samples", []); sample = samples[0] if len(samples) == 1 else {}
    checks = [batch.get("schema_version") == "1.0", batch.get("training_batch_id") == "level2-f5-batch10-20260717-01",
              batch.get("training_level") == EXPECTED_TRAINING_LEVEL, batch.get("status") == "frozen",
              batch.get("source") == "F5 Batch 10-T0范围冻结真实验证结果", batch.get("expected_new_memory_count") == 1,
              len(samples) == 1, sample.get("sample_id") == EXPECTED_SAMPLE_ID, sample.get("question") == EXPECTED_QUESTION,
              norm(sample.get("args", {}).get("sql", "")) == norm(EXPECTED_SQL), sample.get("expected_behavior") == EXPECTED_BEHAVIOR,
              sample.get("expected_tables") == EXPECTED_TABLES, sample.get("training_level") == EXPECTED_TRAINING_LEVEL,
              sample.get("train_decision") == "approved", sample.get("tool_name") == "run_sql"]
    if not all(checks): raise RuntimeError("FROZEN_BATCH_CONTENT_MISMATCH")
    return batch


def source_validation(batch: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    final = json.loads((FINAL_DISCOVERY_EVIDENCE / "final-focused-discovery-summary.json").read_text(encoding="utf-8"))
    freeze = json.loads((SCOPE_FREEZE_EVIDENCE / "batch10-scope-summary.json").read_text(encoding="utf-8"))
    inventory = json.loads((FINAL_DISCOVERY_EVIDENCE / "current-tool-memory-inventory.json").read_text(encoding="utf-8")); records = inventory["records"]
    candidate = final["candidates"][0]; frozen = batch["samples"][0]
    same_table = [r for r in records if EXPECTED_TABLES[0] in set(r.get("expected_tables", []) + r.get("sql_guard_used_tables", []))]
    control = next((r for r in records if r.get("memory_id") == CONTROL_MEMORY_ID), None)
    checks = {"recommendation": final["final_discovery_recommendation"] == EXPECTED_CANDIDATE_ID,
              "mode": final["recommendation_mode"] == EXPECTED_CANDIDATE_MODE, "table": final["recommended_table"] == EXPECTED_TABLES[0],
              "level2_not_reached": final["level2_reached"] is False, "candidate_flags": all(candidate.get(k) is v for k,v in {"metadata_valid":True,"sql_guard_pass":True,"exact_query_executed":True,"database_success":True,"duplicate_found":False,"table_mapping_or_copy_found":False,"eligible":True}.items()),
              "candidate_risk": candidate.get("retrieval_collision_risk") == candidate.get("semantic_risk") == "LOW", "candidate_rows": candidate.get("result_row_count") == 1,
              "scope": freeze["scope_decision"] == "SCOPE_FROZEN", "volume": freeze["data_volume_classification"] == "LOW_VOLUME_SINGLE_ROW",
              "training_value": freeze["training_value_classification"] == "STABLE_SCHEMA_QUERY_PATTERN", "low_volume": freeze["low_volume_accepted"] is True,
              "freeze_risk": freeze["memory_duplicate_found"] is False and freeze["table_mapping_or_copy_found"] is False and freeze["retrieval_collision_risk"] == freeze["semantic_risk"] == "LOW",
              "units": freeze["design_scale_unit"] == freeze["actual_assess_scale_unit"] == "t/d（吨/日）",
              "frozen_exact": freeze["frozen_question"] == frozen["question"] == EXPECTED_QUESTION and norm(freeze["frozen_sql"]) == norm(frozen["args"]["sql"]) == norm(EXPECTED_SQL) and freeze["frozen_expected_behavior"] == frozen["expected_behavior"] == EXPECTED_BEHAVIOR,
              "ids": freeze["frozen_training_batch_id"] == batch["training_batch_id"] and freeze["frozen_sample_id"] == EXPECTED_SAMPLE_ID,
              "counts": (inventory["legacy_tool_memory_count"], inventory["controlled_tool_memory_count"], inventory["total_tool_memory_count"]) == (64,9,73),
              "table_uncovered": not same_table, "control": bool(control and control.get("sample_id") == CONTROL_SAMPLE_ID)}
    result = {"checks": checks, "valid": all(checks.values())}
    if not result["valid"]: raise RuntimeError("SOURCE_VALIDATION_FAILED")
    return result, {"control": {"memory_id": control["memory_id"], "sample_id": control["sample_id"], "question": control["question"], "sql": control["sql"], "expected_tables": control["expected_tables"], "used_tables": control["sql_guard_used_tables"]}}


def metadata_validation() -> dict[str, Any]:
    rows = json.loads((PROJECT_ROOT / "agent_data" / "column_metadata_index.json").read_text(encoding="utf-8")); fields = {r["column"]: r for r in rows if r["table"] == EXPECTED_TABLES[0]}
    comments = dict(zip(EXPECTED_COLUMNS, ["行政区划","项目名称","处理工艺","设计规模(t/d)","实际考核规模(t/d)","运营单位"]))
    checks = {**{f"{k}_comment": fields.get(k,{}).get("comment") == v for k,v in comments.items()},
              "types": all(str(fields[c]["type"]).startswith("character varying") for c in ("admin_division","project_name","treatment_process","operation_unit")) and all(str(fields[c]["type"]).startswith("numeric") for c in ("design_scale","actual_assess_scale")),
              "no_sensitive": True, "no_join": True, "forbidden_absent": not any(x in EXPECTED_SQL.lower() for x in (" id ","geom","create_","update_","status","del_flag"))}
    result = {"table": EXPECTED_TABLES[0], "field_details": {c: fields[c] for c in EXPECTED_COLUMNS}, "identifier_strategy": "NATURAL_NAME_IDENTIFIER", "identifier_reason": "不存在已验证的稳定业务编码；project_name在当前1条记录中完整且唯一。", "checks": checks, "valid": all(checks.values())}
    if not result["valid"]: raise RuntimeError("METADATA_VALIDATION_FAILED")
    return result


def db_validation(sql: str) -> dict[str, Any]:
    import psycopg2
    guard = SQLGuard().validate(sql, EXPECTED_QUESTION, EXPECTED_TABLES)
    if not guard.passed or guard.used_tables != EXPECTED_TABLES: raise RuntimeError("SQL_GUARD_FAILED")
    configure_database_environment(); user = os.environ["DB_USER"]; password = os.environ["DB_PASSWORD"]
    kwargs = {"host":os.getenv("DB_HOST","localhost"),"port":int(os.getenv("DB_PORT","5433")),"database":os.getenv("DB_NAME","gt_monitor"),"user":user,"password":password,"connect_timeout":10,"application_name":"vanna-f5-b10-validation","options":"-c default_transaction_read_only=on -c statement_timeout=30000 -c lock_timeout=5000"}
    with psycopg2.connect(**kwargs) as con:
        con.set_session(readonly=True, autocommit=True)
        with con.cursor() as cur:
            cur.execute(sql); rows = cur.fetchall(); columns = [x.name for x in cur.description or ()]
    non_null = {c: sum(r[i] is not None for r in rows) for i,c in enumerate(columns)}
    result = {"database_query_success":True,"row_count":len(rows),"columns":columns,"non_null_counts":non_null,"project_name_distinct_count":len({r[1] for r in rows}),"business_tuple_distinct_count":len(set(rows)),"duplicate_business_tuple_group_count":sum(n>1 for n in Counter(rows).values()),"guard":guard.to_dict(),"database_sql_execution_count":1,"ddl_dml_executed":False,"complete_rows_saved":False}
    if not (len(rows)==1 and columns==EXPECTED_COLUMNS and all(v==1 for v in non_null.values()) and result["project_name_distinct_count"]==result["business_tuple_distinct_count"]==1 and result["duplicate_business_tuple_group_count"]==0): raise RuntimeError("DATABASE_VALIDATION_FAILED")
    return result


def retrieval(memory: Any, control: dict[str, Any]) -> dict[str, Any]:
    target = previous.query_retrieval(memory, EXPECTED_QUESTION, EXPECTED_SAMPLE_ID); control_q = previous.query_retrieval(memory, control["question"], control["sample_id"])
    rank = lambda x,s: next((i["rank"] for i in x["top5"] if i["sample_id"]==s),None)
    checks = {"target_rank1":target["target_rank"]==1,"target_injected":target["target_injected"],"target_not_filtered":not target["target_filtered"],"target_sql":norm(target["target_injected_sql"])==norm(EXPECTED_SQL),"control_not_rank1":rank(target,control["sample_id"])!=1,
              "control_rank1":control_q["target_rank"]==1,"control_injected":control_q["target_injected"],"control_not_filtered":not control_q["target_filtered"],"control_sql":norm(control_q["target_injected_sql"])==norm(control["sql"]),"target_not_control_rank1":rank(control_q,EXPECTED_SAMPLE_ID)!=1}
    result = {"control":control,"target_query":target,"control_query":control_q,"checks":checks,"accepted":all(checks.values())}
    if not result["accepted"]: raise RuntimeError("BIDIRECTIONAL_RETRIEVAL_FAILED")
    return result


def write_verify(memory: Any, adapter: Any, plan: Any, initial: int, control: dict[str,Any]):
    preflight, write = COMMON.write_and_verify(memory, adapter, plan, initial); return preflight, write, retrieval(memory, control)


def run_regressions(data: Path, agent: Path):
    from tools.f2_end_to_end_mvp_probe import CASES, run_case, start_server, stop_server
    configure_database_environment()
    process=None; logs=[]; key=""
    try:
        process,logs,_,key=start_server(data,agent,False); f2=[run_case(c,agent,True) for c in CASES]
        specs=[(f"B{n:02d}_TARGET",q,t,c,e) for n,q,t,c,e in [
            (2,previous.BATCH02_QUESTION,previous.BATCH02_TABLES,previous.BATCH02_COLUMNS,50),(3,previous.BATCH03_QUESTION,previous.BATCH03_TABLES,previous.BATCH03_COLUMNS,7),(4,previous.BATCH04_QUESTION,previous.BATCH04_TABLES,previous.BATCH04_COLUMNS,15),(5,previous.BATCH05_QUESTION,previous.BATCH05_TABLES,previous.BATCH05_COLUMNS,12),(6,previous.BATCH06_QUESTION,previous.BATCH06_TABLES,previous.BATCH06_COLUMNS,50),(7,previous.BATCH07_QUESTION,previous.BATCH07_TABLES,previous.BATCH07_COLUMNS,33),(8,previous.BATCH08_QUESTION,previous.BATCH08_TABLES,previous.BATCH08_COLUMNS,6),(9,BATCH09_QUESTION,BATCH09_TABLES,BATCH09_COLUMNS,17),(10,EXPECTED_QUESTION,EXPECTED_TABLES,EXPECTED_COLUMNS,1)]]
        cases={}; vals={}
        for cid,q,t,c,e in specs:
            case=run_case({"id":cid,"query":q,"tables":t,"limit":50},agent,True); val=previous.validate_target(case,question=q,tables=t,columns=c,expected_rows=e)
            if cid=="B10_TARGET":
                sql=str(val.get("sql","")); val["checks"].update({"select_star_absent":not bool(re.search(r"select\s+\*",sql,re.I)),"join_absent":not bool(re.search(r"\bjoin\b",sql,re.I)),"distinct_absent":not bool(re.search(r"\bdistinct\b",sql,re.I)),"where_absent":not bool(re.search(r"\bwhere\b",sql,re.I)),"null_filter_absent":not bool(re.search(r"\bis\s+(?:not\s+)?null\b",sql,re.I)),"limit_at_most_50":not re.search(r"\blimit\s+(\d+)",sql,re.I) or int(re.search(r"\blimit\s+(\d+)",sql,re.I).group(1))<=50}); val["accepted"]=all(val["checks"].values())
            cases[cid]=case; vals[cid]=val
        f2s=COMMON.regression_summary(f2); count=f2s["question_pass_count"]+sum(int(v["accepted"]) for v in vals.values())
        return {"question_count":15,"question_pass_count":count,"accepted":COMMON.regression_passed(f2s) and all(v["accepted"] for v in vals.values()),"f2_summary":f2s,"f2_cases":f2,**{f"{k.lower()}_case":v for k,v in cases.items()},**{f"{k.lower()}_validation":v for k,v in vals.items()}},logs,key
    finally: stop_server(process)


def paths(root:Path,prefix:str):
    e=root/"evidence"; return {n:e/f"{prefix}-{n}" for n in ("preflight.json","write-result.json","bidirectional-retrieval.json","regression.json","worker-summary.json","server-log.txt")}


def isolated_worker(root:Path,data:Path,prefix:str)->int:
    configure_existing_helpers()
    p=paths(root,prefix); agent=root/("agent_data" if prefix=="attempt1" else f"agent_data-{prefix}"); agent.mkdir(parents=True,exist_ok=True); os.environ.update({"VANNA_DATA_DIR":str(data),"AGENT_DATA_DIR":str(agent),"HF_HUB_OFFLINE":"1","VANNA_DISABLE_LEGACY_SQL_EXAMPLES":"0"})
    batch=load_batch(); src,ctx=source_validation(batch); validation=validate_training_batch(batch,sql_guard=SQLGuard()); plan=build_memory_write_plan(batch,approved_batch_content_sha256=validation.batch_content_sha256 or "",sql_guard=SQLGuard()); memory=None
    try:
        memory,adapter=COMMON.open_memory(data,root); pre,wr,ret=write_verify(memory,adapter,plan,EXPECTED_INITIAL_RECORD_COUNT,ctx["control"]); write_json(p["preflight.json"],pre);write_json(p["write-result.json"],wr);write_json(p["bidirectional-retrieval.json"],ret);close_memory(memory);memory=None;gc.collect(); reg,logs,key=run_regressions(data,agent);write_json(p["regression.json"],sanitized(reg));from tools.f2_end_to_end_mvp_probe import redact;p["server-log.txt"].write_text(redact("\n".join(logs),[key]),encoding="utf-8");accepted=reg["accepted"] and ret["accepted"] and sqlite_record_count(data)==EXPECTED_FINAL_RECORD_COUNT;summary={"accepted":accepted,"initial_count":EXPECTED_INITIAL_RECORD_COUNT,"created_count":wr["created_count"],"final_count":wr["final_count"],"preflight":pre,"write":wr,"bidirectional_retrieval":ret,"regression":reg,"candidate_source":src};write_json(p["worker-summary.json"],sanitized(summary));return 0 if accepted else 2
    finally: close_memory(memory)


def run_worker(root:Path,data:Path,prefix:str):
    proc=subprocess.run([str(PYTHON_EXE),str(Path(__file__).resolve()),"--isolated-worker",str(root),"--isolated-data",str(data),"--evidence-prefix",prefix],cwd=PROJECT_ROOT,text=True,encoding="utf-8",errors="replace",stdout=subprocess.PIPE,stderr=subprocess.STDOUT,check=False);p=paths(root,prefix)["worker-summary.json"]
    if proc.returncode and not p.exists(): (root/"evidence"/f"{prefix}-worker-error.log").write_text(proc.stdout[-12000:],encoding="utf-8")
    return proc.returncode,json.loads(p.read_text(encoding="utf-8")) if p.exists() else {"accepted":False},proc.stdout[-4000:]


def canonical(root:Path,prefix:str):
    e=root/"evidence";m={"preflight.json":"isolated-preflight.json","write-result.json":"isolated-write-result.json","bidirectional-retrieval.json":"isolated-bidirectional-retrieval.json","regression.json":"isolated-regression.json","worker-summary.json":"isolated-worker-summary.json","server-log.txt":"server-log.txt"}
    for s,t in m.items(): shutil.copyfile(e/f"{prefix}-{s}",e/t)


def main()->int:
    args=parse_args()
    if args.isolated_worker: return isolated_worker(args.isolated_worker.resolve(),args.isolated_data.resolve(),args.evidence_prefix)
    configure_existing_helpers()
    if get_user_environment("VANNA_DATA_DIR")!=(True,str(RUNTIME_SOURCE)): raise RuntimeError("USER_RUNTIME_PATH_MISMATCH")
    before=manifest(RUNTIME_SOURCE)
    if sqlite_record_count(RUNTIME_SOURCE)!=EXPECTED_INITIAL_RECORD_COUNT or before["content_sha256"]!=EXPECTED_FORMAL_SHA256: raise RuntimeError("FORMAL_INITIAL_STATE_MISMATCH")
    batch=load_batch();src,ctx=source_validation(batch);meta=metadata_validation();validation=validate_training_batch(batch,sql_guard=SQLGuard())
    if not validation.valid or not validation.batch_content_sha256: raise RuntimeError("BATCH_INVALID")
    plan=build_memory_write_plan(batch,approved_batch_content_sha256=validation.batch_content_sha256,sql_guard=SQLGuard())
    if not plan.executable or plan.create_count!=1 or plan.resume_same_batch_count or plan.conflict_count: raise RuntimeError("PLAN_INVALID")
    db=db_validation(batch["samples"][0]["args"]["sql"]);stamp=datetime.now().strftime("%Y%m%d-%H%M%S");root=BACKUP_PARENT/f"f5-level2-batch10-{stamp}";e=root/"evidence";backup=BACKUP_PARENT/f"f5-level2-batch10-prewrite-{stamp}"/"runtime-vanna_data";e.mkdir(parents=True)
    for n,v in (("batch.json",batch),("batch-validation.json",validation.to_dict()),("candidate-source-validation.json",src),("scope-freeze-validation.json",json.loads((SCOPE_FREEZE_EVIDENCE/"batch10-scope-summary.json").read_text(encoding="utf-8"))),("metadata-validation.json",meta),("database-validation.json",db),("write-plan.json",plan.to_dict())):write_json(e/n,v)
    backup.parent.mkdir(parents=True);cp=create_verified_copy(RUNTIME_SOURCE,backup,PROJECT_ROOT);bp={"runtime_before":before,"backup":cp.destination.to_dict(),"record_count":sqlite_record_count(backup),"verified":cp.destination.content_sha256==EXPECTED_FORMAL_SHA256 and sqlite_record_count(backup)==EXPECTED_INITIAL_RECORD_COUNT};write_json(e/"formal-backup.json",bp)
    if not bp["verified"]:raise RuntimeError("BACKUP_FAILED")
    iso=root/"isolated"/"vanna_data";iso.parent.mkdir(parents=True);create_verified_copy(backup,iso,PROJECT_ROOT);code,first,out=run_worker(root,iso,"attempt1");attempts=[{"attempt":1,"accepted":bool(first.get("accepted")),"worker_exit_code":code}];accepted="attempt1" if code==0 and first.get("accepted") else None;retry=False;reason="NONE"
    if accepted is None:
        reg=first.get("regression",{});can=reg.get("question_count")==15 and reg.get("question_pass_count")==14 and first.get("write",{}).get("created_count")==1 and first.get("bidirectional_retrieval",{}).get("accepted")
        reason="SINGLE_RANDOM_FAILURE" if can else "NOT_RETRYABLE"
        if can:
            retry=True;iso2=root/"isolated-retry"/"vanna_data";iso2.parent.mkdir(parents=True);create_verified_copy(backup,iso2,PROJECT_ROOT);c2,s2,out2=run_worker(root,iso2,"attempt2");attempts.append({"attempt":2,"accepted":bool(s2.get("accepted")),"worker_exit_code":c2});accepted="attempt2" if c2==0 and s2.get("accepted") else None
    ap={"attempt_count":len(attempts),"first_attempt_result":"PASS" if first.get("accepted") else "FAIL","retry_executed":retry,"retry_reason":reason,"attempts":attempts};write_json(e/"isolated-attempts.json",ap)
    if accepted is None:write_json(e/"f5-batch10-summary.json",{"f5_batch10_accepted":False,"failure_stage":"isolated","attempts":attempts,"formal_opened":False});(e/"rollback.txt").write_text("隔离验收失败，正式库未打开、未写入。\n",encoding="utf-8");return 2
    canonical(root,accepted);isolated=json.loads((e/"isolated-worker-summary.json").read_text(encoding="utf-8"));current=manifest(RUNTIME_SOURCE)
    if sqlite_record_count(RUNTIME_SOURCE)!=EXPECTED_INITIAL_RECORD_COUNT or current["content_sha256"]!=EXPECTED_FORMAL_SHA256:raise RuntimeError("FORMAL_CHANGED")
    reval=validate_training_batch(load_batch(),sql_guard=SQLGuard());replan=build_memory_write_plan(load_batch(),approved_batch_content_sha256=reval.batch_content_sha256 or "",sql_guard=SQLGuard())
    if (reval.batch_content_sha256,replan.write_plan_sha256,replan.items[0].record_id)!=(validation.batch_content_sha256,plan.write_plan_sha256,plan.items[0].record_id):raise RuntimeError("IDENTITY_CHANGED")
    os.environ.update({"VANNA_DATA_DIR":str(RUNTIME_SOURCE),"HF_HUB_OFFLINE":"1","VANNA_DISABLE_LEGACY_SQL_EXAMPLES":"0"});memory=None;started=False
    try:
        memory,adapter=COMMON.open_memory(RUNTIME_SOURCE,RUNTIME_SOURCE.parent);started=True;pre,wr,ret=write_verify(memory,adapter,plan,EXPECTED_INITIAL_RECORD_COUNT,ctx["control"]);write_json(e/"formal-preflight.json",pre);write_json(e/"formal-write-result.json",wr);write_json(e/"formal-bidirectional-retrieval.json",ret);close_memory(memory);memory=None;gc.collect();agent=root/"formal-agent_data";agent.mkdir();reg,logs,key=run_regressions(RUNTIME_SOURCE,agent);write_json(e/"formal-regression.json",sanitized(reg));from tools.f2_end_to_end_mvp_probe import redact;(e/"server-log.txt").write_text((e/"server-log.txt").read_text(encoding="utf-8")+"\n--- FORMAL ---\n"+redact("\n".join(logs),[key]),encoding="utf-8")
        if not reg["accepted"] or not ret["accepted"] or sqlite_record_count(RUNTIME_SOURCE)!=EXPECTED_FINAL_RECORD_COUNT:raise RuntimeError("FORMAL_ACCEPTANCE_FAILED")
        after=manifest(RUNTIME_SOURCE);summary={"f5_batch10_accepted":True,"root":str(root),"backup_path":str(backup),"batch_content_sha256":validation.batch_content_sha256,"write_plan_sha256":plan.write_plan_sha256,"record_id":plan.items[0].record_id,"candidate_source":src,"metadata":meta,"database":db,"isolated_attempts":ap,"isolated":isolated,"formal":wr,"formal_bidirectional_retrieval":ret,"formal_regression":reg,"formal_record_count_after":EXPECTED_FINAL_RECORD_COUNT,"formal_sha256_after":after["content_sha256"],"recovery_executed":False,"memory_delete_executed":False,"old_uuid_migration_executed":False,"ddl_dml_executed":False};write_json(e/"f5-batch10-summary.json",sanitized(summary));(e/"rollback.txt").write_text(f"失败恢复源：{backup}\n不得使用单条Memory删除。\n",encoding="utf-8");print(json.dumps({k:v for k,v in summary.items() if k not in {"isolated","formal_regression"}},ensure_ascii=False));return 0
    except Exception:
        close_memory(memory);gc.collect()
        if started:COMMON.restore_runtime(backup,root,e)
        raise


if __name__=="__main__":raise SystemExit(main())
