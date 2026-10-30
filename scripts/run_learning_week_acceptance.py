"""Prepare, then explicitly run a bounded Qt-free real DeepSeek learning week.

No online work happens at import, prepare, validate or --help. Execution requires
--execute-online AND the SHA256 of a previously reviewed plan. No product files,
formal settings, credentials, or historical corpus are edited. See generated
README.md for evidence limits and the independent-corpus replay backlog.
"""
from __future__ import annotations

import argparse
import copy
import hashlib
import json
import multiprocessing as mp
import os
import re
import subprocess
import sys
import time
from collections import Counter
from contextlib import redirect_stderr, redirect_stdout
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[1]
sys.dont_write_bytecode = True
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from scripts.learning_week_plan import CORPUS, INDEX, audit_corpus, build_plan, digest, validate_plan
from scripts import online_acceptance_budget as shared_budget
from scripts.online_transport_error import safe_transport_error_details


def write_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("w", encoding="utf-8", newline="\n") as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2)
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, path)


def append_event(path, value):
    with Path(path).open("a", encoding="utf-8", newline="\n") as stream:
        stream.write(json.dumps(value, ensure_ascii=False) + "\n")
        stream.flush()
        os.fsync(stream.fileno())


def git(*args):
    return subprocess.check_output(["git", "-C", str(ROOT), *args], text=True, encoding="utf-8").strip()


def source_snapshot():
    paths = [*ROOT.glob("modules/**/*.py"), ROOT / "server/agent_service.py",
             *ROOT.glob("data/personas/roxy/**/*"), Path(__file__), ROOT / "scripts/learning_week_plan.py",
             ROOT / "scripts/online_acceptance_budget.py", ROOT / "scripts/run_resumed_online_acceptance.py"]
    paths.append(ROOT / "scripts/online_transport_error.py")
    return {p.relative_to(ROOT).as_posix(): digest(p) for p in sorted(set(paths)) if p.is_file()}


def fresh_output(path):
    target = Path(path).resolve()
    runs = (ROOT / "test_workspace/runs").resolve()
    if not target.is_relative_to(runs) or target == runs:
        raise ValueError("output must be a new child under this tree's test_workspace/runs")
    target.mkdir(parents=True, exist_ok=False)
    return target


def prepare(output, start):
    target = fresh_output(output)
    plan = build_plan(start)
    assert validate_plan(plan) == 630
    plan["source"].update({"head": git("rev-parse", "HEAD"), "dirty": bool(git("status", "--porcelain")),
                           "files": source_snapshot()})
    write_json(target / "plan.json", plan)
    audit = audit_corpus(plan)
    write_json(target / "corpus-audit.json", audit)
    for group in plan["groups"]:
        lines = [f"# {group['id']} {group['goal']}", "", group["preference"], "",
                 "每行是一条用户输入及对应助手最终回复，共7天×15轮；不计控制事件为用户轮。", ""]
        for day in group["days"]:
            lines += [f"## 第{day['day']}天 {day['date']} · {day['topic']}", ""]
            lines += [f"计划轨迹（不是执行结果）：{day['trajectory']}", ""]
            for turn in day["turns"]:
                lines += [f"{turn['id']}: {turn['user']}",
                          f"  - 工具：{turn['expected']['tools']}；数据：{turn['expected']['change']}；{turn['expected']['reply_contract']}"]
                if turn.get("before"):
                    lines += [f"  - 非用户控制事件：{turn['before']}"]
            lines.append("")
        (target / f"{group['id']}-plan.md").write_text("\n".join(lines), encoding="utf-8")
    counts = Counter(r["disposition"] for r in audit["records"])
    audit_lines = ["# 129条语料逐条分流（均未执行）", "", str(dict(counts)), "",
                   "嵌入仅为合同的语义迁移，不冒充原句+原前置完整覆盖。独立前置回放尚需单独setup与排期。", "",
                   "| ID | 分流 | 差异 | 七日位置 |", "|---|---|---|---|"]
    for row in audit["records"]:
        audit_lines.append(f"| {row['case_id']} | {row['disposition']} | {row['contract_difference']}: {row['reason']} | {', '.join(row['planned_turns'][:3])} |")
    (target / "corpus-audit.md").write_text("\n".join(audit_lines), encoding="utf-8")
    plan_hash = digest(target / "plan.json")
    notes = f"""# 七日真实共享服务验收准备

状态：prepared_not_executed。630轮=6组×7日×15用户输入及15最终助手回复，1260条消息。
计划SHA256：`{plan_hash}`。原始129条/索引未更改，差异及逐条分流见corpus-audit.json。

接口（在源码树运行，使用已安装依赖的明确Python路径）：

```
python scripts/run_learning_week_acceptance.py validate --plan <本目录>/plan.json
python scripts/run_learning_week_acceptance.py run --plan <本目录>/plan.json --reviewed-plan-sha256 {plan_hash} --groups g1 --output test_workspace/runs/<新在线run> --execute-online --budget-ledger test_workspace/runs/<共同账本>.json --used-http <累计HTTP> --used-reported-tokens <累计报告token> --remaining-http <剩余HTTP> --remaining-reported-tokens <剩余报告token> --parent-seconds 600
```

第二条仅供总控另行排期授权后执行。本批不在线运行。密钥只由DEEPSEEK_API_KEY环境变量提供，缺失立即失败，不读取正式配置或私有凭据，不打印密钥。默认使用仓库当前ModelSettings默认DeepSeek模型；可用--model显式选择，记录请求与服务回显模型，禁本地降级/自动升级/隐式重试。不能因响应包含deepseek字符串就判真：端点严格为api.deepseek.com，记录真实HTTP及provider路径。

每组仅一个长驻worker，持续七个模拟日；第4日首轮声明次日重建、第5日第9轮声明同日重建，其余轮次保留同一共享服务。新日用ensure_daily_learning_session，不手工种pending/建议快照。每组全新app根，logger先于服务/模型构造。业务文件全量前后快照、ToolResult、UID/日期/标题、交互状态、诊断、HTTP可见原文与最终回复独立落盘。handler调用前/返回后即时append，原始结果及当时业务快照不依赖handle最终返回；父进程停止worker后再留末态快照。记忆读取会更新use_count/last_used/last_used_at：字节差异保留，只对这三项明确分开核对；不把使用元数据变化冒充正文变更。

六组以不同目标、主题及计划时长运行，含全部完成、仅第一项完成、零完成、零计划外行动、5/8/15/20/35/50分钟和未计时轨迹；次日建议必须面对真实存储，计划时长不得冒充实际时长。第2日通过真实用户输入保存A“我更适合早上学习”，第3日明确记住B“我现在更适合晚上学习”，按当前显式保存合同由真实save_formal_memory operation=updated核对同ID替换；不强加不存在的二次确认。下一轮中性质疑、第4日重建后质疑保留人工真假审核。第7日精确确认删除另一条偏好，之后展示不能复活旧事实。明确“展示长期记忆”对照全部有效ID集合，自然问答允许相关子集。

HTTP请求先经父进程和同一共享ledger落盘、预留额度，再发放网络许可；只保存消息可见内容、工具调用、模型及允许的用量字段，永不保存headers、密钥、reasoning。真实HTTP响应可见内容在provider解析/产品守卫之前保存。run必须显式传入累计消耗与剩余额度并与共同账本一致，禁止各阶段重置；--max-http等参数只能收紧计划上限。每请求最多1500输出token，每轮80秒，单次执行父进程硬截止不超过600秒。未知usage、HTTP错误、失联或超时锁止新HTTP及后续组；保留已预留但可能已发送请求及未知计费，不自动退款或估算usage。请求预留是保守准入余量，报告token上限是停止阈值，不能保证最后一个真实响应不越过阈值。父进程只终止自己的worker，保留现场。中断的组不自动恢复或冒充连续完整通过；重新开始须另行审查，不能覆盖首轮。

业务失败保留原状态继续安全后续，缺失依赖标blocked且不发用户消息/伪造助手回复；错误写入或基础设施异常停止本组。失败不重问、不改原句、不用事后回复否定ToolResult成功。若另行批准重试，使用全新run并标明首轮目录，不能覆盖首答。合成show_plan故障与过期控制事件明示标注，均不是provider故障。业务事实自动核对；回复自然性、记忆质疑真假、知识正确性、次日建议是否合理由人工逐轮审核，自动通过只叫machine_contract_pass_pending_review，不生成总体成功率。630轮是工程模拟，不是真实七天用户数据或630名用户。

限制：独立提醒事件、同文多记忆/旧编号等原始前置不混进连续七日；独立回放尚未执行。模拟日期不改系统时间，不证明真实七日使用、Windows桌面、无Python电脑或EXE可分发。不引入未来自动保存/迁移/调度，不用模型生成预期或自评。
"""
    (target / "README.md").write_text(notes, encoding="utf-8")
    return {"directory": str(target), "plan_sha256": plan_hash, "rounds": 630,
            "messages": 1260, "corpus_dispositions": dict(counts)}


def public_text(value):
    """Remove hidden-thought blocks; do not persist the hidden channel."""
    text = str(value or "")
    text = re.sub(r"<think(?:ing)?\b[^>]*>.*?(?:</think(?:ing)?>|\Z)", "", text, flags=re.S | re.I)
    return text.strip()


def safe_tool_calls(value):
    result = []
    for item in value if isinstance(value, list) else []:
        if not isinstance(item, dict):
            continue
        function = item.get("function")
        function = function if isinstance(function, dict) else {}
        result.append({"id": item.get("id"), "type": item.get("type"),
                       "function": {"name": function.get("name"), "arguments": function.get("arguments")}})
    return result


def public_payload(payload):
    result = {k: payload[k] for k in ("model", "max_tokens", "stream", "temperature", "tools", "tool_choice") if k in payload}
    result["messages"] = [{k: public_text(m[k]) if k == "content" else safe_tool_calls(m[k]) if k == "tool_calls" else m[k]
                           for k in ("role", "content", "tool_calls", "tool_call_id", "name") if k in m}
                          for m in payload.get("messages", [])]
    return result


def public_response(raw):
    if not isinstance(raw, dict):
        return {"invalid_response_type": type(raw).__name__, "usage": {}}
    usage = raw.get("usage")
    usage = usage if isinstance(usage, dict) else {}
    safe_usage = {k: v for k, v in usage.items() if k in {
        "prompt_tokens", "completion_tokens", "total_tokens", "prompt_cache_hit_tokens", "prompt_cache_miss_tokens"}
        and isinstance(v, int) and not isinstance(v, bool) and v >= 0}
    choices = []
    for item in raw.get("choices", []) if isinstance(raw.get("choices"), list) else []:
        if not isinstance(item, dict):
            continue
        message = item.get("message") or {}
        message = message if isinstance(message, dict) else {}
        choices.append({"finish_reason": item.get("finish_reason"),
                        "message": {"content": public_text(message.get("content")),
                                    "tool_calls": safe_tool_calls(message.get("tool_calls", []))}})
    return {"model": raw.get("model"), "choices": choices, "usage": safe_usage}


class Budget:
    """In-memory protocol fixture; real CLI runs require SharedBudget."""
    def __init__(self, limits):
        self.limits = limits
        self.http = 0
        self.tokens = 0
        self.unknown_usage = 0
        self.completed_responses = 0
        self.stop_reason = None

    def can_start_turn(self):
        return not self.stop_reason and self.http < self.limits["http"] and self.tokens < self.limits["reported_tokens_stop"]

    def reserve(self, request=None):
        if not self.can_start_turn():
            return False
        self.http += 1
        return True

    def observe(self, response, *, event="http_response", request_id=None):
        if event == "http_response":
            self.completed_responses += 1
        usage = response.get("usage", {})
        total = usage.get("total_tokens")
        if isinstance(total, int) and not isinstance(total, bool) and total >= 0:
            self.tokens += total
        else:
            self.unknown_usage += 1
            self.halt("unknown_usage_or_http_error")
        if event != "http_response":
            self.halt("http_error")
        return not self.stop_reason

    def dispatched(self, request_id):
        return True

    def halt(self, reason):
        self.stop_reason = self.stop_reason or reason

    def report(self):
        return {"http_reserved": self.http, "reported_total_tokens": self.tokens,
                "responses_with_unknown_usage": self.unknown_usage,
                "requests_without_completed_response": self.http-self.completed_responses,
                "token_values_are_api_reported_not_estimates": True}


class Halt(BaseException):
    """Must escape provider's network/invalid-response fallback handlers."""


def isolated_audit_hook(root):
    root = Path(root).resolve()
    formal_roots = [ROOT, ROOT.parents[1] / "Roxyplan"]
    forbidden = [r / name for r in formal_roots for name in
                 ("memory.json", "data/private", "data/pet_config.json", "config.json", "data/today_plan.json", "data/action_log.json", "data/growth_log.json")]

    def hook(event, args):
        if event == "open" and isinstance(args[0], (str, bytes, os.PathLike)):
            path = Path(os.fsdecode(args[0])).resolve()
            if any(path == p.resolve() or path.is_relative_to(p.resolve()) for p in forbidden):
                raise Halt("formal_data_access_blocked")
            mode = args[1] or ""
            flags = args[2] or 0
            writing = any(c in str(mode) for c in "wax+") or bool(flags & (os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_TRUNC))
            if writing and not path.is_relative_to(root):
                raise Halt("write_outside_run_blocked")
        if event in {"os.mkdir", "os.remove", "os.rmdir", "os.rename"}:
            for value in args[:2] if event == "os.rename" else args[:1]:
                if isinstance(value, (str, bytes, os.PathLike)) and not Path(os.fsdecode(value)).resolve().is_relative_to(root):
                    raise Halt("mutation_outside_run_blocked")
    sys.addaudithook(hook)


BUSINESS = ("memory.json", "data/private/today_plan.json", "data/private/action_log.json", "data/private/growth_log.json")
USAGE_FIELDS = {"last_used", "last_used_at", "use_count"}


def semantic_memory(value):
    if isinstance(value, dict):
        return {k: semantic_memory(v) for k, v in value.items() if k not in USAGE_FIELDS}
    if isinstance(value, list):
        return [semantic_memory(v) for v in value]
    return value


def snapshot(app):
    result = {}
    for name in BUSINESS:
        path = app / name
        result[name] = {"sha256": digest(path) if path.exists() else None,
                        "data": json.loads(path.read_text(encoding="utf-8-sig")) if path.exists() else None}
    auxiliary_paths = [app / "data/private/memory_conflicts.json", app / "data/private/memory_candidates.json",
                       *app.glob("data/knowledge/**/*")]
    result["auxiliary_business_files"] = {p.relative_to(app).as_posix(): digest(p) for p in auxiliary_paths if p.is_file()}
    return result


def is_confirmation_preview(result, response):
    """Executor preview, backed by the same typed pending object, not a call."""
    pending = response.get("pending_confirmation") or {}
    confirmation = result.get("data", {}).get("confirmation") or {}
    error = result.get("error") or {}
    return bool(
        isinstance(pending, dict) and isinstance(confirmation, dict) and isinstance(error, dict)
        and response.get("status") == "confirmation_required"
        and result.get("success") is False
        and result.get("status") == "confirmation_required"
        and error.get("code") == "confirmation_required"
        and confirmation.get("confirmation_id")
        and confirmation.get("confirmation_id") == pending.get("confirmation_id")
        and confirmation.get("tool") == pending.get("tool") == result.get("tool")
        and isinstance(confirmation.get("arguments"), dict)
        and confirmation.get("arguments") == pending.get("arguments")
    )


def execution_evidence(response, handler_results=None):
    returned = response.get("tool_results", [])
    previews = [i for i, result in enumerate(returned) if is_confirmation_preview(result, response)]
    negated = [i for i, result in enumerate(returned)
               if handler_results == [] and result.get("success") is False
               and isinstance(result.get("error"), dict)
               and result["error"].get("code") == "negated_request"]
    execution_results = [result for i, result in enumerate(returned) if i not in previews + negated]
    return execution_results, {
        "returned_tool_results": [{"tool": r["tool"], "success": r["success"], "status": r.get("status"),
                                   "error_code": (r.get("error") or {}).get("code") if isinstance(r.get("error"), dict) else r.get("error")}
                                  for r in returned],
        "confirmation_preview_result_indexes": previews,
        "negated_preflight_result_indexes": negated,
        "non_preview_result_tools": [r["tool"] for r in execution_results],
        "registered_handler_tools": [r["tool"] for r in handler_results] if handler_results is not None else None,
        "handler_observation_available": handler_results is not None,
    }



def check_turn(turn, before, after, response, tasks_before, tasks_after, actions_before, actions_after, day, selected_title=None, *, handler_results=None):
    """Strict mechanical facts; natural-language judgments remain explicit review."""
    failures = []
    expected = turn["expected"]
    results, execution = execution_evidence(response, handler_results)
    if handler_results is not None and execution["registered_handler_tools"] != [r["tool"] for r in results]:
        failures.append("handler_vs_final_tool_sequence_mismatch")
    tools = [r["tool"] for r in results]
    allowed_sequences = turn.get("tool_sequences", [expected["tools"]])
    if tools not in allowed_sequences:
        failures.append(f"tool_sequence:{tools!r} not in {allowed_sequences!r}")
    if any(bool(r["success"]) != expected["successful_tools"] for r in results):
        failures.append("unexpected_tool_success_state")
    if not str(response.get("message", "")).strip():
        failures.append("empty_final_reply")
    change = expected["change"]
    allowed = {"plan_add": {BUSINESS[1]}, "plan_complete": {BUSINESS[1]},
               "action_add": {BUSINESS[2]}, "review_save": {BUSINESS[3]},
               "memory_add": {BUSINESS[0]}, "memory_update": {BUSINESS[0]}, "memory_delete": {BUSINESS[0]},
               "deferred_plan_authorization": {BUSINESS[1]}}.get(change, set())
    changed = []
    metadata_only = []
    if before.get("auxiliary_business_files", {}) != after.get("auxiliary_business_files", {}):
        failures.append("unauthorized_auxiliary_business_change")
    for name in BUSINESS:
        if before[name]["sha256"] == after[name]["sha256"]:
            continue
        changed.append(name)
        if name == BUSINESS[0] and semantic_memory(before[name]["data"]) == semantic_memory(after[name]["data"]):
            metadata_only.append(name)
            continue
        if name not in allowed:
            failures.append("unauthorized_business_change:" + name)
    if change not in {"none", "deferred_plan_authorization"} and not (set(changed) & allowed):
        failures.append("expected_write_missing")
    # All dates are retained: never allow a permitted today write to hide a
    # simultaneous mutation of yesterday/tomorrow records in the same file.
    for name in allowed - {BUSINESS[0]}:
        key = "entries" if name == BUSINESS[3] else "days"
        old_dates = (before[name]["data"] or {}).get(key, {})
        new_dates = (after[name]["data"] or {}).get(key, {})
        if {k: v for k, v in old_dates.items() if k != day} != {k: v for k, v in new_dates.items() if k != day}:
            failures.append("unauthorized_other_day_change:" + name)
    old = {t["uid"]: t for t in tasks_before}
    new = {t["uid"]: t for t in tasks_after}
    if len(new) != len(tasks_after) or any(not uid for uid in new):
        failures.append("invalid_or_duplicate_task_uid")
    if any(t.get("date") != day for t in tasks_after):
        failures.append("wrong_business_date")
    if change == "plan_add":
        added = [t for uid, t in new.items() if uid not in old]
        if len(added) != 1 or any(new.get(uid) != t for uid, t in old.items()):
            failures.append("plan_add_did_not_preserve_other_uid_records")
        if selected_title is None:
            failures.append("missing_actual_displayed_selection")
        elif added and (not str(added[0].get("title", "")).strip() or re.sub(r"\s+", "", str(added[0].get("title", ""))) not in re.sub(r"\s+", "", selected_title)):
            failures.append("saved_title_not_from_selected_displayed_item")
        if added and "duration_minutes" in expected and added[0].get("duration_minutes") != expected["duration_minutes"]:
            failures.append("planned_duration_not_preserved")
    if change == "plan_complete":
        count = expected.get("complete_count", 2)
        targets = ({tasks_before[turn["completion_selection"]-1]["uid"]}
                   if turn.get("completion_selection") and len(tasks_before) >= turn["completion_selection"] else
                   {uid for uid, t in old.items() if not t.get("done")})
        changed_done = {uid for uid in old.keys() & new.keys() if bool(old[uid].get("done")) != bool(new[uid].get("done"))}
        if set(old) != set(new) or changed_done != targets or len(changed_done) != count or any(not new[uid].get("done") for uid in targets if uid in new):
            failures.append("completion_uid_set_or_state_mismatch")
        if any(old[uid] != new[uid] for uid in (old.keys() & new.keys()) - targets):
            failures.append("completion_changed_unselected_uid")
        completion_fields = {"done", "status", "done_at", "updated_at"}
        if any({k: v for k, v in old[uid].items() if k not in completion_fields}
               != {k: v for k, v in new[uid].items() if k not in completion_fields} for uid in old.keys() & new.keys()):
            failures.append("completion_changed_non_completion_fields")
    if change == "action_add":
        added = [r for r in actions_after if r not in actions_before]
        if len(added) != 1 or added[0].get("duration_minutes") != expected.get("duration_minutes", 10) or added[0].get("date") != day:
            failures.append("action_content_duration_or_date_mismatch")
        elif not str(added[0].get("content", "")).strip() or str(added[0]["content"]).strip() not in turn["user"]:
            failures.append("action_content_not_authorized_by_user")
        if any(item not in actions_after for item in actions_before):
            failures.append("action_removed_existing_record")
    old_mem = (before[BUSINESS[0]]["data"] or {}).get("memories", [])
    new_mem = (after[BUSINESS[0]]["data"] or {}).get("memories", [])
    old_mem_by_id = {str(m.get("id")): semantic_memory(m) for m in old_mem}
    new_mem_by_id = {str(m.get("id")): semantic_memory(m) for m in new_mem}
    if change == "memory_add":
        added = [m for mid, m in new_mem_by_id.items() if mid not in old_mem_by_id]
        if len(added) != 1 or any(new_mem_by_id.get(mid) != m for mid, m in old_mem_by_id.items()):
            failures.append("memory_add_modified_other_records_or_count")
        elif not added[0].get("content") or added[0]["content"] not in turn["user"]:
            failures.append("memory_content_not_explicitly_authorized")
    if change == "memory_delete":
        removed = [m for mid, m in old_mem_by_id.items() if mid not in new_mem_by_id]
        if len(removed) != 1 or removed[0].get("content") != "我习惯先自己思考，再看例子。" or any(old_mem_by_id.get(mid) != m for mid, m in new_mem_by_id.items()):
            failures.append("memory_delete_target_or_remaining_records_mismatch")
    if change == "memory_update":
        affected = [mid for mid, item in old_mem_by_id.items() if item.get("content") == turn["expected_old_memory"]]
        if (len(affected) != 1 or set(old_mem_by_id) != set(new_mem_by_id)
                or new_mem_by_id.get(affected[0], {}).get("content") != turn["expected_new_memory"]
                or any(old_mem_by_id[mid] != new_mem_by_id[mid] for mid in old_mem_by_id if mid not in affected)):
            failures.append("memory_correction_not_same_id_or_changed_other_records")
        operations = [r.get("data", {}).get("memory_operation", {}).get("data", {}).get("operation") for r in results if r["tool"] == "save_formal_memory" and r["success"]]
        if operations != ["updated"]:
            failures.append("memory_correction_missing_real_update_confirmation")
    probe = turn.get("memory_probe")
    if probe:
        active_contents = {m.get("content") for m in new_mem if m.get("status", "active") == "active"}
        if probe["active"] not in active_contents or probe["inactive"] in active_contents:
            failures.append("corrected_memory_fact_not_active_or_old_fact_revived")
        reads = [r for r in results if r["tool"] == "list_memories" and r["success"]]
        if reads and not any(probe["active"] in {
                f.get("content") for f in r.get("data", {}).get("memory_read", {}).get("facts", [])
        } for r in reads):
            failures.append("corrected_memory_read_omits_active_fact")
    if change == "review_save":
        entry = ((after[BUSINESS[3]]["data"] or {}).get("entries") or {}).get(day, {})
        review = entry.get("review", {})
        plan_snapshot = review.get("plan_snapshot", [])
        if (review.get("date") != day or review.get("total") != len(tasks_after)
                or review.get("done") != sum(bool(t.get("done")) for t in tasks_after)
                or review.get("action_count") != len(actions_after)
                or {t.get("stable_id") for t in plan_snapshot} != set(new)
                or review.get("action_records") != actions_after):
            failures.append("saved_review_does_not_match_actual_business_facts")
        for row in plan_snapshot:
            task = new.get(row.get("stable_id"), {})
            if any(row.get(key) != task.get(key) for key in ("title", "done", "status", "date", "time_slot", "duration_minutes")):
                failures.append("saved_review_plan_snapshot_fields_mismatch")
    # Bind the tool's claimed changed objects to the actual persisted objects.
    for item in results:
        if not item["success"]:
            continue
        data = item.get("data", {})
        task = data.get("task")
        if item["tool"] in {"add_plan", "complete_plan"}:
            if not isinstance(task, dict) or new.get(task.get("uid")) != task or not data.get("postcondition_verified"):
                failures.append("tool_task_or_postcondition_disagrees_with_persistence")
        if item["tool"] == "show_plan":
            query_day = (datetime.fromisoformat(day).date() + timedelta(days={"yesterday": -1, "tomorrow": 1}.get(turn.get("inspect_date"), 0))).isoformat()
            stored = ((after[BUSINESS[1]]["data"] or {}).get("days") or {}).get(query_day, {}).get("tasks", [])
            if data.get("tasks") != stored or (data.get("date") and data["date"] != query_day):
                failures.append("read_tool_returned_wrong_date_or_uid_records")
        if item["tool"] == "save_daily_review":
            stored_entry = ((after[BUSINESS[3]]["data"] or {}).get("entries") or {}).get(day)
            if data.get("entry") != stored_entry or not data.get("postcondition_verified"):
                failures.append("saved_review_tool_result_not_persisted")
        if item["tool"] == "generate_daily_review":
            query_day = (datetime.fromisoformat(day).date() + timedelta(days={"yesterday": -1, "tomorrow": 1}.get(turn.get("inspect_date"), 0))).isoformat()
            review = data.get("review", {})
            stored_tasks = ((after[BUSINESS[1]]["data"] or {}).get("days") or {}).get(query_day, {}).get("tasks", [])
            stored_actions = ((after[BUSINESS[2]]["data"] or {}).get("days") or {}).get(query_day, {}).get("records", [])
            if (review.get("date") != query_day or review.get("total") != len(stored_tasks)
                    or review.get("done") != sum(bool(t.get("done")) for t in stored_tasks)
                    or review.get("action_records") != stored_actions):
                failures.append("review_query_date_or_persisted_facts_mismatch")
        if item["tool"] == "list_memories":
            for row in data.get("memories", []):
                persisted = new_mem_by_id.get(str(row.get("id")))
                if persisted is None or row.get("content") != persisted.get("content") or persisted.get("status", "active") != "active":
                    failures.append("memory_read_returned_unstored_or_deleted_fact")
            if turn["user"].strip().rstrip("。！？!? ") == "展示长期记忆":
                expected_ids = {str(m.get("id")) for m in new_mem if m.get("status", "active") == "active"}
                returned_ids = [str(m.get("id")) for m in data.get("memories", [])]
                if set(returned_ids) != expected_ids or len(returned_ids) != len(set(returned_ids)):
                    failures.append("explicit_memory_display_incomplete_id_set")
    # Do not trust intent labels or let final prose negate successful results.
    # These are diagnostic flags, NOT a semantic oracle based on phrase regexes.
    non_write_failures = {"expected_write_missing", "empty_final_reply", "unexpected_tool_success_state",
                          "missing_actual_displayed_selection", "read_tool_returned_wrong_date_or_uid_records",
                          "review_query_date_or_persisted_facts_mismatch", "memory_read_returned_unstored_or_deleted_fact",
                          "explicit_memory_display_incomplete_id_set", "corrected_memory_fact_not_active_or_old_fact_revived",
                          "corrected_memory_read_omits_active_fact"}
    wrong_write = (bool(set(changed) - set(metadata_only)) and any(f not in non_write_failures and not f.startswith("tool_sequence:") for f in failures)) or "unauthorized_auxiliary_business_change" in failures
    return {"status": "failed" if failures else "pending_adjudication" if turn.get("adjudication") else "machine_contract_pass_pending_review", "stop_group_for_wrong_write": wrong_write,
            "failures": failures, "changed_business_files": changed,
            "memory_usage_metadata_only_changes": metadata_only,
            "execution_evidence": execution,
            "successful_tool_facts": [r for r in results if r["success"]],
            "human_review": {"reply_truthfulness": None, "knowledge_correctness": None,
                             "memory_challenge_consistency": None, "next_day_advice_quality": None}}


def make_runtime(app, clock, client, *, real_time_interactions=False):
    # Configure global logger in worker BEFORE ANY runtime/model construction.
    from modules.chat_history_manager import ChatHistoryManager
    from modules.growth_manager import GrowthManager
    from modules.memory_manager import MemoryManager
    from server.agent_service import AgentService
    private = app / "data/private"
    growth = GrowthManager(private, now_provider=clock.now)
    memory = MemoryManager(app / "memory.json", backup_dir=private / "backups",
                           conflict_file=private / "memory_conflicts.json", audit_file=private / "memory_audit.json")
    history = ChatHistoryManager(private, now_provider=clock.now)
    runtime = AgentService(project_root=app, growth_manager=growth, memory_manager=memory,
                           chat_history_manager=history, llm_client=client)
    service = runtime.conversation_service
    service.now_provider = clock.now
    service.business_now_provider = clock.now
    service.state_manager.now_provider = clock.now
    runtime.intent_router.entity_parser.now_provider = clock.now
    runtime.confirmation_manager.now_provider = clock.now
    runtime.interaction_coordinator.now_provider = clock.now
    if real_time_interactions:
        # New gate control only: business dates may differ from wall time,
        # while snapshot/confirmation TTLs retain the real configured clock.
        service.now_provider = datetime.now
        service.state_manager.now_provider = datetime.now
        runtime.confirmation_manager.now_provider = datetime.now
        runtime.interaction_coordinator.now_provider = datetime.now
    for name, module in list(sys.modules.items()):
        if name.startswith(("modules.", "server.")) and getattr(module, "__file__", None):
            if not Path(module.__file__).resolve().is_relative_to(ROOT):
                raise Halt("application_module_imported_from_another_tree")
    return runtime, growth, history


def worker(connection, config):
    root = Path(config["root"])
    sys.dont_write_bytecode = True
    isolated_audit_hook(root)
    with (root / "worker.log").open("w", encoding="utf-8") as log, redirect_stdout(log), redirect_stderr(log):
        try:
            worker_inner(connection, config)
        except BaseException as error:
            # Exception text/tracebacks can contain URLs or request objects.
            connection.send({"event": "worker_error", "error_type": type(error).__name__,
                             "error_code": str(error) if isinstance(error, Halt) else "not_serialized"})
        finally:
            connection.close()


def worker_inner(connection, config):
    from urllib import request
    from modules.development_log import configure_development_log
    from modules.simulation_clock import SimulatedBusinessClock
    root = Path(config["root"])
    app = root / "app"
    group = config["group"]
    base_clock = SimulatedBusinessClock(app, group["days"][0]["date"])
    configure_development_log(root / "development_log")
    # Offset only the TEST clock for expiry, never mutate system time.
    class TestClock:
        offset = 0
        started = time.monotonic()
        def now(self):
            return datetime.fromisoformat(base_clock.today() + "T10:00:00") + timedelta(seconds=self.offset + time.monotonic()-self.started)
    clock = TestClock()
    (app / "data").mkdir(exist_ok=True)
    write_json(app / "data/pet_config.json", {"interaction_diagnostics_enabled": True})
    from modules.llm.routed_client import RoutedLLMClient
    from modules.llm.settings import DEFAULT_DEEPSEEK_MODEL
    model = config.get("model") or DEFAULT_DEEPSEEK_MODEL
    settings = {"online_model_enabled": True, "offline_fallback_enabled": False,
                "model_mode": "economy", "enable_auto_escalation": False,
                "enable_thinking_for_complex_tasks": False, "default_model": model,
                "max_provider_retries": 0, "max_output_tokens": config["limits"]["output_tokens_per_request"],
                "request_timeout_seconds": 60}
    client = RoutedLLMClient(app, settings=settings)
    if not client.deepseek_provider.configured:
        raise Halt("deepseek_not_configured")
    current_turn = ""
    captured_requests = []
    captured_responses = []
    captured_http_errors = []
    class NoRedirect(request.HTTPRedirectHandler):
        def redirect_request(self, *_args, **_kwargs):
            return None
    opener = request.build_opener(NoRedirect())

    def transport(method, url, headers, payload, timeout):
        parsed = urlsplit(url)
        if method != "POST" or parsed.scheme != "https" or parsed.netloc != "api.deepseek.com" or parsed.path not in {"/chat/completions", "/v1/chat/completions"}:
            raise Halt("unexpected_provider_endpoint")
        if not 1 <= int(payload.get("max_tokens", 0)) <= config["limits"]["output_tokens_per_request"]:
            raise Halt("output_budget_not_applied")
        safe_request = public_payload(payload)
        captured_requests.append(safe_request)
        connection.send({"event": "http_request", "turn": current_turn, "request": safe_request,
                         "request_ordinal_in_turn": len(captured_requests), "provider": "deepseek", "endpoint": url})
        permit = connection.recv()
        if not permit.get("permit"):
            raise Halt("budget_exhausted")
        try:
            req = request.Request(url, data=json.dumps(payload, ensure_ascii=False).encode("utf-8"), headers=headers, method=method)
            connection.send({"event": "http_dispatch", "turn": current_turn, "request_id": permit["request_id"]})
            if not connection.recv().get("permit"):
                raise Halt("dispatch_denied")
            with opener.open(req, timeout=timeout) as response:
                raw = json.loads(response.read().decode("utf-8"))
            # Parent acknowledges durable raw-visible evidence before returning
            # into production provider parsing/sanitization.
            safe_response = public_response(raw)
            captured_responses.append(safe_response)
            connection.send({"event": "http_response", "turn": current_turn, "request_id": permit["request_id"], "response": safe_response})
            connection.recv()
            return raw
        except BaseException as error:
            captured_http_errors.append(type(error).__name__)
            connection.send({"event": "http_error", "turn": current_turn, "request_id": permit["request_id"],
                             "error_type": type(error).__name__, **safe_transport_error_details(error)})
            connection.recv()
            raise

    client.deepseek_provider.transport = transport
    def deny_offline(*_args, **_kwargs):
        raise Halt("offline_fallback_attempt")
    client.ollama_provider.chat_with_tools = deny_offline
    runtime, growth, history = make_runtime(app, clock, client,
        real_time_interactions=bool(config.get("gate_contract")))
    observer_results = []
    observer_calls = []
    composition_contexts = []
    fault = {"show_plan": False}

    def observe_handlers():
        for name in runtime.tool_registry.names():
            definition = runtime.tool_registry.get(name)
            original = definition.handler
            def observed(*args, _name=name, _original=original, **kwargs):
                from modules.contracts import ToolResult
                observer_calls.append({"tool": _name, "arguments": copy.deepcopy(kwargs)})
                handler_log = root / "turns" / current_turn / "handler-events.jsonl"
                append_event(handler_log, {"stage": "handler_called", "tool": _name, "arguments": copy.deepcopy(kwargs)})
                if _name == "show_plan" and fault["show_plan"]:
                    fault["show_plan"] = False
                    result = ToolResult(success=False, tool="show_plan", message="synthetic_acceptance_failure", error="synthetic_acceptance_failure")
                else:
                    try:
                        result = _original(*args, **kwargs)
                    except BaseException as error:
                        append_event(handler_log, {"stage": "handler_raised", "tool": _name, "error_type": type(error).__name__})
                        raise
                if hasattr(result, "to_dict"):
                    observer_results.append(result.to_dict())
                    # Persist before returning into any later model polish,
                    # guard, retry, timeout or worker failure. Do not rely on
                    # a final reply to retain the fact that a handler ran.
                    append_event(handler_log, {"stage": "handler_returned", "tool_result": result.to_dict()})
                    write_json(root / "turns" / current_turn / f"handler-{len(observer_results):02}-business.json", snapshot(app))
                return result
            definition.handler = observed
        if config.get("gate_contract"):
            from scripts.run_resumed_online_acceptance import observe_composer_context
            observe_composer_context(runtime.response_composer, composition_contexts)
    observe_handlers()
    verified_writes = []
    actual_turns = 0
    attempted_turns = 0
    for day in group["days"]:
        if base_clock.today() != day["date"]:
            if base_clock.advance_one_day() != day["date"]:
                raise Halt("simulation_date_mismatch")
        clock.offset = 0
        session = history.ensure_daily_learning_session(day["date"])["session_id"]
        anchors = {}
        for planned_turn in day["turns"]:
            turn = copy.deepcopy(planned_turn)
            current_turn = turn["id"]
            required = turn.get("requires")
            live_tasks = growth.tasks(day["date"], strict_read=True)
            available = (len(live_tasks) == 2 and all(not t.get("done") for t in live_tasks)) if required == "daily_plans" else not required or required in anchors
            if turn.get("requires_memory_content"):
                matches = [m for m in runtime.memory_manager.memories() if m.get("content") == turn["requires_memory_content"]]
                if len(matches) != 1:
                    available = False
                    required = "actual_memory_A_for_explicit_correction"
            bindings = {}
            for index, label in enumerate(("first_plan_title", "second_plan_title")):
                if "{" + label + "}" in turn["user"]:
                    if len(live_tasks) <= index or not live_tasks[index].get("title"):
                        available = False
                        required = "actual_" + label
                    else:
                        bindings[label] = {"title": live_tasks[index]["title"], "uid": live_tasks[index]["uid"], "date": live_tasks[index]["date"]}
                        turn["user"] = turn["user"].replace("{" + label + "}", live_tasks[index]["title"])
            if bindings:
                turn["reviewed_user_template"] = planned_turn["user"]
                turn["runtime_title_bindings"] = bindings
            if not available:
                blocked = {"event": "turn_blocked", "turn": current_turn, "missing_dependency": required,
                           "user_not_sent": True, "assistant_reply": None, "status": "blocked_not_executed"}
                write_json(root / "blocked" / (current_turn + ".json"), blocked)
                connection.send(blocked)
                continue
            connection.send({"event": "turn_started", "turn": current_turn})
            if not connection.recv().get("permit"):
                raise Halt("new_turn_denied")
            directory = root / "turns" / current_turn
            directory.mkdir(parents=True)
            write_json(directory / "input.json", turn)
            pre_state = runtime.interaction_coordinator.current(session).to_dict()
            if turn.get("before") == "restart_service":
                if runtime.interaction_coordinator.current(session).pending:
                    raise Halt("restart_precondition_not_quiescent")
                runtime, growth, history = make_runtime(app, clock, client,
                    real_time_interactions=bool(config.get("gate_contract")))
                observe_handlers()
                if history.ensure_daily_learning_session(day["date"])["session_id"] != session:
                    raise Halt("daily_session_not_restored")
            if turn.get("before") == "expire_pending":
                clock.offset += 301
            if turn.get("before") == "fail_show_plan_once":
                fault["show_plan"] = True
            before = snapshot(app)
            before_tasks = growth.tasks(day["date"], strict_read=True)
            before_actions = growth.records_for_date(day["date"], strict_read=True)
            write_json(directory / "before.json", before)
            before_history = (history.get_session(session) or {}).get("messages", [])
            selected_title = None
            if turn.get("selection"):
                objects = anchors.get("advice", {}).get("objects", [])
                index = turn["selection"] - 1
                if len(objects) > index:
                    selected_title = objects[index].get("title")
            observer_results.clear()
            observer_calls.clear()
            composition_contexts.clear()
            captured_requests.clear()
            captured_responses.clear()
            captured_http_errors.clear()
            start = time.monotonic()
            attempted_turns += 1
            result = runtime.handle(turn["user"], session)
            final = result.to_dict()
            if config.get("gate_contract"):
                write_json(directory / "composition-contexts.json", composition_contexts)
            after = snapshot(app)
            tasks = growth.tasks(day["date"], strict_read=True)
            actions = growth.records_for_date(day["date"], strict_read=True)
            state = runtime.interaction_coordinator.current(session).to_dict()
            checked_day = turn.get("review_target_date", day["date"]) if config.get("gate_contract") else day["date"]
            checked_before_tasks, checked_tasks = before_tasks, tasks
            checked_before_actions, checked_actions = before_actions, actions
            if checked_day != day["date"]:
                checked_before_tasks = (before[BUSINESS[1]]["data"] or {}).get("days", {}).get(checked_day, {}).get("tasks", [])
                checked_tasks = (after[BUSINESS[1]]["data"] or {}).get("days", {}).get(checked_day, {}).get("tasks", [])
                checked_before_actions = (before[BUSINESS[2]]["data"] or {}).get("days", {}).get(checked_day, {}).get("records", [])
                checked_actions = (after[BUSINESS[2]]["data"] or {}).get("days", {}).get(checked_day, {}).get("records", [])
            checks = check_turn(turn, before, after, final, checked_before_tasks, checked_tasks, checked_before_actions, checked_actions, checked_day, selected_title,
                                handler_results=observer_results)
            if turn.get("capture") == "advice":
                displayed = state.get("last_suggestion_snapshot") or {}
                if (len(displayed.get("objects", [])) != 2 or displayed.get("list_purpose") != "today_tasks"
                        or displayed.get("conversation_id") != session or displayed.get("target_date") != day["date"]):
                    checks["failures"].append("actual_today_two_item_snapshot_missing")
                else:
                    anchors["advice"] = displayed
            if turn.get("capture") == "completion_preview":
                if len(before_tasks) != 2 or state.get("state") != "awaiting_confirmation":
                    checks["failures"].append("bound_completion_confirmation_missing")
                else:
                    anchors["completion_preview"] = state
            if turn.get("capture") == "memory_delete_preview":
                if state.get("state") == "awaiting_confirmation":
                    anchors["memory_delete_preview"] = state
                else:
                    checks["failures"].append("memory_delete_confirmation_missing")
            if turn.get("capture") == "advice":
                serialized_requests = json.dumps(captured_requests, ensure_ascii=False)
                if "只读事实" not in serialized_requests or "last_7_days" not in serialized_requests or "last_30_days" not in serialized_requests:
                    checks["failures"].append("real_planning_evidence_not_in_model_request")
                if day["day"] > 1:
                    previous_day = (datetime.fromisoformat(day["date"]).date()-timedelta(days=1)).isoformat()
                    prior_review = ((before[BUSINESS[3]]["data"] or {}).get("entries") or {}).get(previous_day)
                    if not prior_review or "saved_yesterday_review" not in serialized_requests:
                        checks["failures"].append("saved_yesterday_review_evidence_missing")
            # An observer captures the registered handlers; response facts may
            # be richer postcondition-checked objects but cannot hide handlers.
            if turn.get("requires") == "daily_plans" and len(before_tasks) != 2:
                checks["failures"].append("daily_two_plan_precondition_missing")
            if config.get("gate_contract"):
                from scripts.run_resumed_online_acceptance import check_gate_turn
                extra = check_gate_turn(turn, before, after, final, state, anchors,
                                        observer_calls, observer_results, day["date"],
                                        composition_contexts=composition_contexts)
                checks["gate_facts"] = extra
                checks["failures"].extend(extra["failures"])
            if checks["failures"]:
                checks["status"] = "failed"
            for item in final.get("tool_results", []):
                if item["success"] and item["tool"] in {"save_formal_memory", "delete_memory", "add_plan", "complete_plan", "add_action_log", "save_daily_review"}:
                    fact = {"turn": current_turn, "tool_result": item,
                            "fact_layer": "ToolResult reported success; persistence checked independently",
                            "after_business_hashes": {k: after[k]["sha256"] for k in BUSINESS},
                            "persistence_check_failures": list(checks["failures"])}
                    verified_writes.append(fact)
                    append_event(root / "successful-tool-facts.jsonl", fact)
            history_messages = (history.get_session(session) or {}).get("messages", [])
            if (len(history_messages) != len(before_history)+2
                    or history_messages[-2].get("role") != "user"
                    or history_messages[-1].get("role") != "assistant"
                    or history_messages[-2].get("content") != turn["user"]
                    or history_messages[-1].get("content") != final.get("message")):
                checks["failures"].append("daily_history_user_final_reply_accounting_mismatch")
                checks["status"] = "failed"
            provider_error = bool(captured_requests and (
                captured_http_errors or len(captured_responses) != len(captured_requests)
                or client.last_response is None or client.last_response.error is not None
                or client.last_response.provider != "deepseek"
                or any(r.get("usage", {}).get("completion_tokens", 0) > config["limits"]["output_tokens_per_request"] for r in captured_responses)))
            if provider_error:
                checks["infrastructure_failure"] = True
                checks["failures"].append("real_provider_failed_or_unverified_no_fallback_credit")
                checks["status"] = "failed"
            actual_turns += 1
            evidence = {"id": current_turn, "business_date": day["date"], "session_id": session,
                        "elapsed_seconds": time.monotonic()-start, "final_response": final,
                        "registered_handler_calls": observer_calls, "registered_handler_results": observer_results,
                        "interaction_state": state, "diagnostics": runtime.conversation_service.diagnostic_snapshot(),
                        "interaction_state_before_control": pre_state,
                        "tasks_before": before_tasks, "tasks_after": tasks,
                        "actions_before": before_actions, "actions_after": actions,
                        "raw_visible_responses": captured_responses, "requests_this_turn": len(captured_requests),
                        "http_error_types": captured_http_errors,
                        "provider": "deepseek" if captured_requests and not provider_error else None,
                        "requested_models": [r.get("model") for r in captured_requests],
                        "returned_models": [r.get("model") for r in captured_responses],
                        "prior_and_current_successful_write_facts": copy.deepcopy(verified_writes),
                        "history_message_count": len(history_messages),
                        "checks": checks, "execution_mode": "real_deepseek_shared_service; synthetic_data_and_clock",
                        "synthetic_control_event": turn.get("before")}
            write_json(directory / "after.json", after)
            write_json(directory / "result.json", evidence)
            connection.send({"event": "turn_completed", "turn": current_turn, "status": checks["status"]})
            if config.get("gate_contract") and checks["failures"]:
                connection.send({"event": "group_finished", "status": "gate_failed_first_evidence_retained"})
                return
            if checks["stop_group_for_wrong_write"] or provider_error:
                connection.send({"event": "group_finished", "status": "infrastructure_stopped" if provider_error else "wrong_write_stopped_first_evidence_retained"})
                return
    connection.send({"event": "group_finished", "status": "scheduled_rounds_exhausted_review_required", "actual_turns": actual_turns, "attempted_user_turns": attempted_turns})


def terminate_owned(process):
    if process.is_alive():
        process.terminate()
        process.join(3)
        if process.is_alive():
            process.kill()
            process.join(3)


def supervise(group, config, budget, parent_deadline, *, _worker_target=worker):
    root = Path(config["root"])
    root.mkdir()
    parent, child = mp.get_context("spawn").Pipe()
    process = mp.get_context("spawn").Process(target=_worker_target, args=(child, config))
    process.start()
    child.close()
    start = time.monotonic()
    turn_started = None
    current = None
    statuses = {}
    blocked = {}
    status = "worker_exited_without_completion"
    evidence = root / "http-and-control.jsonl"
    try:
        while True:
            now = time.monotonic()
            if now >= parent_deadline or now-start >= config["limits"]["group_seconds"]:
                status = "parent_group_or_total_deadline"
                break
            if current is None and now-start >= config["limits"]["turn_seconds"]:
                status = "worker_startup_deadline"
                break
            if turn_started is not None and now-turn_started >= config["limits"]["turn_seconds"]:
                status = "parent_turn_hard_deadline"
                break
            if not parent.poll(0.05):
                if not process.is_alive():
                    break
                continue
            try:
                event = parent.recv()
            except EOFError:
                break
            event["observed_at_utc"] = datetime.now(timezone.utc).isoformat()
            kind = event["event"]
            if kind == "http_request":
                permit = budget.reserve(event.get("request"))
                request_id = getattr(budget, "request_id", None) or f"http-{budget.http:05}"
                event.update({"request_id": request_id, "budget_permit": permit})
                append_event(evidence, event)  # durable BEFORE permission/network
                parent.send({"permit": permit, "request_id": request_id})
                if not permit:
                    status = "budget_stop_before_http"
                    break
            elif kind == "http_dispatch":
                permit = budget.dispatched(event.get("request_id"))
                append_event(evidence, event)
                parent.send({"permit": permit})
                if not permit:
                    status = "budget_failure_closed"
                    break
            elif kind in {"http_response", "http_error"}:
                keep_running = budget.observe(event.get("response", {}), event=kind, request_id=event.get("request_id"))
                append_event(evidence, event)
                parent.send({"stored": True})
                if not keep_running:
                    status = "budget_failure_closed"
                    break
            elif kind == "turn_started":
                current = event["turn"]
                turn_started = time.monotonic()
                permit = budget.can_start_turn()
                append_event(evidence, event)
                parent.send({"permit": permit})
                if not permit:
                    status = "budget_stop_before_new_turn"
                    break
            else:
                append_event(evidence, event)
                if kind == "turn_blocked":
                    blocked[event["turn"]] = event["missing_dependency"]
                if kind == "turn_completed":
                    statuses[event["turn"]] = event["status"]
                    turn_started = None
                if kind in {"group_finished", "worker_error"}:
                    status = event.get("status", "worker_error:" + event.get("error_type", "unknown"))
                    break
    finally:
        process.join(0.3)
        terminate_owned(process)
        parent.close()
        if status in {"parent_group_or_total_deadline", "worker_startup_deadline", "parent_turn_hard_deadline",
                      "worker_exited_without_completion", "case_stopped_infrastructure_or_unsafe_write",
                      "infrastructure_stopped", "wrong_write_stopped_first_evidence_retained"} or status.startswith("worker_error"):
            budget.halt(status)
    if (root / "app").is_dir():
        try:
            write_json(root / "business-after-worker-stop.json", snapshot(root / "app"))
        except (OSError, ValueError) as error:
            write_json(root / "business-snapshot-error.json", {"error_type": type(error).__name__})
    planned = [t["id"] for d in group["days"] for t in d["turns"]]
    result = {"group": group["id"], "status": status, "elapsed_seconds": time.monotonic()-start,
              "worker_exitcode": process.exitcode, "worker_alive_after_cleanup": process.is_alive(),
              "completed_turns": statuses, "interrupted_turn": current if turn_started is not None else None,
              "blocked_turns_without_reply": blocked,
              "completed_round_count": len(statuses), "completed_message_count": len(statuses)*2,
              "status_counts": dict(Counter(statuses.values())),
              "unexecuted_turns": [tid for tid in planned if tid not in statuses], "budget_after": budget.report()}
    write_json(root / "group-summary.json", result)
    return result


def validate_file(path):
    plan = json.loads(Path(path).read_text(encoding="utf-8"))
    count = validate_plan(plan)
    if plan["source"]["corpus_sha256"] != digest(CORPUS) or plan["source"]["index_sha256"] != digest(INDEX):
        raise ValueError("original corpus/index drifted")
    if plan["source"].get("files") != source_snapshot():
        raise ValueError("tested source/resources changed since preparation; prepare new review plan")
    return plan, count


def run(args):
    plan, _ = validate_file(args.plan)
    if not args.execute_online or digest(Path(args.plan)) != args.reviewed_plan_sha256:
        raise ValueError("online opt-in and exact reviewed plan hash required")
    if not os.environ.get("DEEPSEEK_API_KEY", "").strip():
        raise ValueError("DEEPSEEK_API_KEY environment credential required; no private-file fallback")
    selected = args.groups.split(",") if args.groups != "all" else [g["id"] for g in plan["groups"]]
    if len(selected) != len(set(selected)) or not set(selected) <= {g["id"] for g in plan["groups"]}:
        raise ValueError("unknown or duplicate groups")
    root = fresh_output(args.output)
    write_json(root / "frozen-plan.json", plan)
    limits = dict(plan["limits"])
    for key, value in (("http", args.max_http), ("reported_tokens_stop", args.max_reported_tokens), ("group_seconds", args.group_seconds)):
        if value is not None:
            if value <= 0 or value > limits[key]:
                raise ValueError("command overrides may only tighten reviewed budgets")
            limits[key] = value
    manifest = {"head": git("rev-parse", "HEAD"), "dirty": bool(git("status", "--porcelain")),
                "reviewed_plan_sha256": args.reviewed_plan_sha256, "source": plan["source"]["files"],
                "selected_groups": selected, "limits": limits, "stage": "execution_started",
                "credential_source": "environment_only_value_never_recorded", "python": sys.version}
    write_json(root / "manifest.json", manifest)
    budget = shared_budget.open_for_stage(args, limits, root=ROOT, stage=root,
        source_files=manifest["source"], source_check=lambda: source_snapshot() == manifest["source"])
    deadline = time.monotonic() + args.parent_seconds
    summaries = []
    try:
        for group in plan["groups"]:
            if group["id"] not in selected:
                continue
            if time.monotonic() >= deadline or not budget.can_start_turn():
                break
            config = {"root": str(root / group["id"]), "group": group, "limits": limits, "model": args.model}
            summaries.append(supervise(group, config, budget, deadline))
            write_json(root / "progress.json", {"groups": summaries, "budget": budget.report()})
    finally:
        result = {"stage": "execution_stopped_human_review_required", "groups": summaries,
                  "budget": budget.report(), "overall_pass": None, "source_unchanged": source_snapshot() == manifest["source"],
                  "not_run_groups": [g for g in selected if g not in [s["group"] for s in summaries]]}
        try:
            write_json(root / "summary.json", result)
        finally:
            budget.close()
    return {"directory": str(root), "budget": budget.report(), "groups_completed_or_stopped": len(summaries)}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    p = commands.add_parser("prepare")
    p.add_argument("--output", required=True)
    p.add_argument("--start-date", default="2026-11-02")
    v = commands.add_parser("validate")
    v.add_argument("--plan", required=True)
    r = commands.add_parser("run")
    r.add_argument("--plan", required=True)
    r.add_argument("--reviewed-plan-sha256", required=True)
    r.add_argument("--execute-online", action="store_true")
    r.add_argument("--groups", default="g1")
    r.add_argument("--output", required=True)
    r.add_argument("--model")
    r.add_argument("--max-http", type=int)
    r.add_argument("--max-reported-tokens", type=int)
    r.add_argument("--group-seconds", type=int)
    shared_budget.add_arguments(r)
    args = parser.parse_args()
    if args.command == "prepare":
        result = prepare(args.output, args.start_date)
    elif args.command == "validate":
        _, count = validate_file(args.plan)
        result = {"valid": True, "planned_rounds": count, "execution": "not_run"}
    else:
        result = run(args)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
