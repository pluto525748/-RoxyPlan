"""Bounded real-service runner for direct and gated natural-chat corpus cases.

prepare/validate are stdlib-only. run needs explicit online opt-in, reviewed
manifest SHA256 and an environment credential. Preludes use the real service;
only observed gates permit dependent original turns. Event/legacy adapters
remain unimplemented and visible, never silently become passes.
"""
from __future__ import annotations

import argparse
import copy
import json
import os
import sys
import time
from collections import Counter
from contextlib import redirect_stderr, redirect_stdout
from datetime import datetime, timedelta
from pathlib import Path
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[1]
sys.dont_write_bytecode = True
sys.path.insert(0, str(ROOT)) if str(ROOT) not in sys.path else None
from scripts import run_learning_week_acceptance as week

LIMITS = {"http": 1000, "reported_tokens_stop": 2000000, "output_tokens_per_request": 1500,
          "group_seconds": 90, "turn_seconds": 80, "parent_seconds": 7200}
CORPUS = ROOT / "tests/fixtures/beta_interaction_regressions.jsonl"
DEFERRED = {"bt_action_07", "bt_action_09", "bt_cd_11", "bt_d30_06"}
NATURAL_MEMORY_READS = {"bt_mem_06", "bt_mem_11", "bt_mem_12"}
CONDITIONAL = {"conditional_natural_prelude", "conditional_in_sequence"}
GATE_KEYS = {
    "suggestion_count", "suggestion_count_min", "suggestion_source", "suggestion_purpose",
    "valid_today_session_snapshot", "valid_today_suggestion_snapshot", "business_writes",
    "required_titles_in_order", "pending_kind_any", "pending_tool", "known_topic_nonempty",
    "no_today_suggestion_snapshot", "displayed_list_purpose_any", "displayed_option_count_min",
    "successful_tool", "successful_tool_count", "confirmation_pending", "read_snapshot_source",
    "snapshot_includes_all_seed_today_tasks", "valid_plan_read_snapshot", "display_order",
    "previous_successful_tool", "previous_business_writes", "pending_task_count",
    "consumed_display_order", "target_date_offset", "target_seed_title", "time_slot",
    "previous_read_date_offset", "previous_user_topic", "original_snapshot_still_valid",
    "original_memory_text_unmodified", "first_title", "first_option_consumed", "refinement_used",
}


def sources():
    return {**week.source_snapshot(), Path(__file__).relative_to(ROOT).as_posix(): week.digest(Path(__file__))}


def load_design(path):
    design = json.loads(Path(path).read_text(encoding="utf-8-sig"))
    originals = {c["case_id"]: c for c in map(json.loads, CORPUS.read_text(encoding="utf-8-sig").splitlines())}
    cases = design.get("cases", [])
    if len(cases) != 129 or {c["case_id"] for c in cases} != set(originals):
        raise ValueError("design must account for all 129 original records exactly once")
    if len({c["case_id"] for c in cases}) != len(cases) or design["source"]["sha256"] != week.digest(CORPUS):
        raise ValueError("corpus identity mismatch")
    for case in cases:
        original = originals[case["case_id"]]
        if case.get("frozen_original") != original:
            raise ValueError("frozen_original was changed: " + case["case_id"])
        wanted = [{"role": "user", "text": original["text"]}] if original["kind"] == "single_turn" else original["turns"]
        actual = case.get("turns", [])
        if len(wanted) != len(actual) or any(a.get("role") != b.get("role") or a.get("text") != b.get("text") or a.get("event") != b.get("event") for a, b in zip(wanted, actual)):
            raise ValueError("original text/role/order was rewritten: " + case["case_id"])
        expected = [original["expected"]] if original["kind"] == "single_turn" else [t.get("expect", {}) for t in wanted]
        if [t.get("expect", {}) for t in actual] != expected:
            raise ValueError("frozen turn expectations changed: " + case["case_id"])
        seed = case.get("seed", {})
        if set(seed) - {"business_date", "plans", "memories", "profile", "actions", "normalizations", "reviews"}:
            raise ValueError("unsupported seed fields; pending/snapshots/history cannot be seeded")
    return design


def ready_reason(case):
    if case["execution"]["design_status"] != "ready_direct":
        return case["execution"]["design_status"]
    if case.get("prelude") or case.get("require") or case.get("require_before"):
        return "pending_prelude_or_gate_adapter"
    for turn in case["turns"]:
        if turn.get("role") != "user" or turn.get("require") or turn.get("require_before"):
            return "pending_event_or_gate_adapter"
    return None


def execution_reason(case):
    """Closed support list, separate from the unchanged direct eligibility."""
    status = case["execution"]["design_status"]
    if status == "ready_direct":
        return ready_reason(case)
    if status not in CONDITIONAL:
        return status
    if case.get("require") or case.get("require_before"):
        return "pending_case_level_gate_adapter"
    for turn in [*case.get("prelude", []), *case["turns"]]:
        if turn.get("role") != "user":
            return "pending_event_adapter"
        unknown = (set(turn.get("require") or {}) | set(turn.get("require_before") or {})) - GATE_KEYS
        if unknown:
            return "pending_gate_adapter:" + ",".join(sorted(unknown))
    if any(p.get("allowed_prelude_writes") not in {"none", "one_today_plan", "three_today_plans"} for p in case.get("prelude", [])):
        return "pending_prelude_write_policy"
    return None


def business_changes(before, after):
    changed = []
    for name in week.BUSINESS:
        left, right = before[name]["data"], after[name]["data"]
        if name == week.BUSINESS[0]:
            left, right = week.semantic_memory(left), week.semantic_memory(right)
        if left != right:
            changed.append(name)
    if before.get("auxiliary_business_files") != after.get("auxiliary_business_files"):
        changed.append("auxiliary_business_files")
    return changed


def check_gates(required, observation):
    """Evaluate explicit design keys against a read-only real observation."""
    checks = []
    for key, wanted in (required or {}).items():
        actual = observation.get(key)
        if key not in GATE_KEYS:
            passed = False
        elif key == "pending_kind_any":
            actual = observation.get("pending_kind")
            passed = actual in wanted
        elif key == "displayed_list_purpose_any":
            actual = observation.get("displayed_list_purpose")
            aliases = {"directions": {"clarification_options", "discussion"}, "long_term_plan": {"long_term_outline"}}
            passed = any(actual in aliases.get(item, {item}) for item in wanted)
        elif key in {"successful_tool", "previous_successful_tool"}:
            actual = observation.get("successful_tools", [])
            passed = wanted in actual
        elif key == "required_titles_in_order":
            actual = observation.get("suggestion_titles")
            passed = actual == wanted
        elif key == "first_title":
            actual = (observation.get("suggestion_titles") or [None])[0]
            passed = actual == wanted
        elif key == "consumed_display_order":
            actual = observation.get("consumed_display_orders", [])
            passed = wanted in actual
        elif key in {"suggestion_count_min", "displayed_option_count_min"}:
            actual = observation.get("suggestion_count" if key == "suggestion_count_min" else "displayed_option_count")
            passed = isinstance(actual, int) and actual >= wanted
        elif key == "display_order":
            actual = observation.get("read_display_orders", [])
            passed = wanted in actual
        elif key == "previous_user_topic":
            actual = observation.get("actual_user", "")
            passed = wanted in actual
        elif key == "target_seed_title":
            actual = observation.get("pending_target_titles", [])
            passed = wanted in actual
        elif key == "previous_business_writes":
            actual = observation.get("business_writes")
            passed = actual == wanted
        elif key == "previous_read_date_offset":
            actual = observation.get("target_date_offset")
            passed = actual == wanted
        else:
            passed = actual == wanted
        checks.append({"gate": key, "expected": wanted, "observed": actual, "passed": bool(passed)})
    return {"passed": all(c["passed"] for c in checks), "checks": checks}


def observe_runtime(runtime, growth, history, session, day, previous, provenance, seed_snapshot,
                    *, response=None, user_text="", before=None, after=None):
    """Read original producers; never create pending, snapshots, or messages."""
    from modules.suggestion_snapshot import SuggestionSnapshot, SuggestionSnapshotObject
    state_object = runtime.interaction_coordinator.current(session)
    state = state_object.to_dict()
    suggestion = state.get("last_suggestion_snapshot") or {}
    read = state.get("last_read_snapshot") or {}
    objects = suggestion.get("objects", [])
    now = runtime.interaction_coordinator.now_provider()
    results = (response or {}).get("tool_results", [])
    if response and suggestion.get("snapshot_id") and suggestion.get("source_turn_id") == response.get("request_id"):
        provenance["suggestions"].setdefault(suggestion["snapshot_id"], response["request_id"])
        provenance.setdefault("first_suggestion_id", suggestion["snapshot_id"])
    for result in results:
        if result["success"] and result["tool"] == "show_plan" and read.get("snapshot_id"):
            ids = [str(t.get("uid")) for t in result.get("data", {}).get("tasks", [])]
            if [o.get("stable_id") for o in read.get("objects", [])] == ids:
                provenance["reads"][read["snapshot_id"]] = ids
    try:
        captured = datetime.fromisoformat(suggestion["captured_at"])
        valid = (suggestion.get("conversation_id") == session and suggestion.get("target_date") == day
                 and suggestion.get("list_purpose") == "today_tasks" and bool(objects)
                 and any(not o.get("consumed") for o in objects)
                 and captured <= now <= captured+timedelta(seconds=runtime.interaction_coordinator.continuation_ttl_seconds)
                 and provenance["suggestions"].get(suggestion.get("snapshot_id")) == suggestion.get("source_turn_id"))
    except (ValueError, TypeError, KeyError):
        valid = False
    tasks = growth.tasks(day, strict_read=True)
    live = {str(t["uid"]): t for t in tasks}
    read_objects = read.get("objects", [])
    valid_read = (read.get("conversation_id") == session and read.get("tool_name") == "show_plan"
                  and read.get("snapshot_id") in provenance["reads"] and bool(read_objects)
                  and all(o.get("date") == day and o.get("stable_id") in live
                          and o.get("title") == live[o["stable_id"]]["title"]
                          and bool(o.get("done")) == bool(live[o["stable_id"]].get("done")) for o in read_objects))
    if valid_read:
        try:
            captured = datetime.fromisoformat(read["captured_at"])
            valid_read = captured <= now <= captured+timedelta(seconds=runtime.interaction_coordinator.continuation_ttl_seconds)
        except (KeyError, ValueError, TypeError):
            valid_read = False
    pending = runtime.confirmation_manager.pending(scope=session) or {}
    immutable = state.get("immutable_arguments") or {}
    pending_tool = str(pending.get("tool") or immutable.get("tool_name") or "") if state_object.pending or pending else ""
    confirmation = bool(state_object.pending and state.get("state") == "awaiting_confirmation"
                        and (pending or state.get("interaction_kind") in {"plan_batch_addition", "plan_batch_completion"}))
    current_response = response if response is not None else (previous or {}).get("response", {})
    actual_user = user_text if response is not None else (previous or {}).get("actual_user", "")
    known = state.get("known_fields") or {}
    successful = [r for r in current_response.get("tool_results", []) if r["success"]]
    read_date = state.get("last_read_arguments", {}).get("date") or read.get("arguments", {}).get("date")
    if not read_date and read_objects:
        read_date = read_objects[0].get("date")
    try:
        offset = (datetime.fromisoformat(read_date).date()-datetime.fromisoformat(day).date()).days
    except (ValueError, TypeError):
        offset = None
    titles = [SuggestionSnapshotObject.from_dict(o).plan_arguments()["title"] for o in objects]
    seed_ids = {t["uid"] for t in (seed_snapshot[week.BUSINESS[1]]["data"] or {}).get("days", {}).get(day, {}).get("tasks", [])}
    data = after if after is not None else week.snapshot(runtime.project_root)
    changed = business_changes(before, data) if before is not None else (previous or {}).get("changed_business_files", [])
    return {"state": state, "response": current_response, "actual_user": actual_user,
            "valid_today_session_snapshot": bool(valid), "valid_today_suggestion_snapshot": bool(valid),
            "suggestion_count": len(objects), "suggestion_titles": titles,
            "suggestion_source": "real_assistant_reply" if suggestion.get("snapshot_id") in provenance["suggestions"] else None,
            "suggestion_purpose": suggestion.get("list_purpose"),
            "no_today_suggestion_snapshot": not (suggestion and suggestion.get("target_date") == day and suggestion.get("list_purpose") == "today_tasks"),
            "displayed_list_purpose": SuggestionSnapshot.displayed_list_purpose(current_response.get("message", ""), actual_user, state.get("request_mode", "")),
            "displayed_option_count": len(SuggestionSnapshot.numbered_options(current_response.get("message", ""))),
            "pending_kind": state.get("interaction_kind") if state_object.pending else None,
            "pending_tool": pending_tool, "confirmation_pending": confirmation,
            "pending_task_count": len(state.get("action_candidates", [])) if state_object.pending else 0,
            "known_topic_nonempty": bool(known.get("learning_topic") or known.get("title") or known.get("topic")),
            "successful_tools": [r["tool"] for r in successful], "successful_tool_count": len(successful),
            "business_writes": len(changed), "changed_business_files": changed,
            "read_snapshot_source": "real_tool_result" if read.get("snapshot_id") in provenance["reads"] else None,
            "valid_plan_read_snapshot": bool(valid_read), "read_display_orders": [o.get("display_order") for o in read_objects],
            "snapshot_includes_all_seed_today_tasks": bool(valid_read and {o["stable_id"] for o in read_objects} == seed_ids),
            "target_date_offset": offset, "pending_target_titles": [live[uid]["title"] for uid in state.get("selected_object_ids", []) if uid in live],
            "consumed_display_orders": [o.get("display_order") for o in objects if o.get("consumed")],
            "first_option_consumed": bool(objects and objects[0].get("consumed")),
            "original_snapshot_still_valid": bool(valid and suggestion.get("snapshot_id") == provenance.get("first_suggestion_id")),
            "original_memory_text_unmodified": week.semantic_memory(data[week.BUSINESS[0]]["data"]) == week.semantic_memory(seed_snapshot[week.BUSINESS[0]]["data"]),
            "refinement_used": known.get("suggestion_refinement_used"), "time_slot": suggestion.get("common_time_slot")}


def prepare(args):
    design = load_design(args.design)
    root = week.fresh_output(args.output)
    week.write_json(root / "frozen-design.json", design)
    ready = [c["case_id"] for c in design["cases"] if ready_reason(c) is None]
    conditional = [c["case_id"] for c in design["cases"] if c["execution"]["design_status"] in CONDITIONAL and execution_reason(c) is None]
    value = {"stage": "prepared_not_executed", "head": week.git("rev-parse", "HEAD"),
             "dirty": bool(week.git("status", "--porcelain")), "source": sources(),
             "design_sha256": week.digest(root / "frozen-design.json"), "external_input_sha256": week.digest(Path(args.design)),
             "corpus_sha256": week.digest(CORPUS), "limits": LIMITS, "ready_direct_ids": ready,
             "conditional_ids": conditional, "runnable_ids": ready+conditional,
             "verification_layer": "Qt-free shared service; no pet/notification adapter or complete-App acceptance",
             "pending_or_excluded": {c["case_id"]: execution_reason(c) for c in design["cases"] if execution_reason(c)},
             "overlay": {"natural_memory_reads": sorted(NATURAL_MEMORY_READS), "deferred_policy": sorted(DEFERRED),
                         "note": "Frozen expectations stay intact; current overlays and machine checks reported independently. Natural reply/content assertions remain human review."}}
    week.write_json(root / "prepared.json", value)
    return {"directory": str(root), "manifest_sha256": week.digest(root / "prepared.json"),
            "ready_direct": len(ready), "conditional_attemptable": len(conditional),
            "pending_or_excluded": 129-len(ready)-len(conditional), "execution": "not_run"}


def validate(path):
    path = Path(path)
    prepared = json.loads(path.read_text(encoding="utf-8"))
    design_path = path.parent / "frozen-design.json"
    if week.digest(design_path) != prepared["design_sha256"] or prepared["source"] != sources():
        raise ValueError("reviewed source/design drift; prepare a new review artifact")
    if prepared["limits"] != LIMITS:
        raise ValueError("budget drift")
    design = load_design(design_path)
    if prepared["ready_direct_ids"] != [c["case_id"] for c in design["cases"] if ready_reason(c) is None]:
        raise ValueError("ready selection changed")
    conditional = [c["case_id"] for c in design["cases"] if c["execution"]["design_status"] in CONDITIONAL and execution_reason(c) is None]
    if prepared["conditional_ids"] != conditional or prepared["runnable_ids"] != prepared["ready_direct_ids"]+conditional:
        raise ValueError("conditional selection changed")
    return prepared, design


def resolve_seed(seed, business_date):
    def resolve(value):
        if isinstance(value, dict):
            return {k: resolve(v) for k, v in value.items()}
        if isinstance(value, list):
            return [resolve(v) for v in value]
        if isinstance(value, str):
            return value.replace("$run_business_date", business_date).replace("$business_day_T09:00:00", business_date + "T09:00:00")
        return value
    return resolve(copy.deepcopy(seed))


def seed_runtime(runtime, growth, app, seed, day):
    """Only persisted synthetic data; no UI/history/interaction state mutation."""
    resolved = resolve_seed(seed, day)
    for plan in resolved.get("plans", []):
        target = (datetime.fromisoformat(day).date()+timedelta(days=int(plan.get("date_offset", 0)))).isoformat()
        task = growth.add_task(plan["title"], target, duration_minutes=plan.get("duration_minutes"))
        if plan.get("done"):
            done, changed = growth.complete_by_id(task["id"], target)
            if not done or not done.get("done") or not changed:
                raise week.Halt("persisted_seed_completion_failed")
    for action in resolved.get("actions", []):
        target = (datetime.fromisoformat(day).date()+timedelta(days=int(action.get("date_offset", 0)))).isoformat()
        growth.add_record(action["content"], date=target, duration_minutes=action.get("duration_minutes"))
    memory = {"version": 2, "profile": resolved.get("profile", {}), "memories": resolved.get("memories", [])}
    if not runtime.memory_manager.repository.save_memories(memory):
        raise week.Halt("persisted_memory_seed_failed")
    for review in resolved.get("reviews", []):
        target = (datetime.fromisoformat(day).date()+timedelta(days=int(review.get("date_offset", 0)))).isoformat()
        growth.save_today_review(target)
    return resolved


def persist_canonical_seed(memory_manager, resolved):
    """Only after native seed reload, before observers or ANY chat request."""
    if not memory_manager.save():
        raise week.Halt("canonical_seed_save_failed")
    actual = memory_manager.data
    by_id = {str(m["id"]): m for m in actual.get("memories", [])}
    requested = resolved.get("memories", [])
    if set(by_id) != {str(m["id"]) for m in requested} or actual.get("profile") != resolved.get("profile", {}):
        raise week.Halt("canonical_seed_changed_fixture_identity_or_profile")
    for record in requested:
        if any(by_id[str(record["id"])].get(key) != value for key, value in record.items()):
            raise week.Halt("canonical_seed_changed_explicit_fixture_field")


is_confirmation_preview = week.is_confirmation_preview
execution_evidence = week.execution_evidence


def evaluate(case, turn, response, before, after, aliases, business_date, *, handler_results=None):
    """Machine tool/data facts only; never silently reinterpret source prose."""
    frozen = copy.deepcopy(turn.get("expect", {}))
    canonical = lambda seq: [aliases.get(name, name) for name in seq]
    results, evidence = execution_evidence(response, handler_results)
    actual = [r["tool"] for r in results]
    original_tools = canonical(frozen.get("tools", []))
    frozen_failures = [] if actual == original_tools else ["frozen_tool_sequence_mismatch"]
    if frozen.get("success") is not None and any(r["success"] != frozen["success"] for r in results):
        frozen_failures.append("frozen_tool_success_mismatch")
    if any(r["tool"] in canonical(frozen.get("forbidden_tools", [])) for r in results):
        frozen_failures.append("frozen_forbidden_tool_observed")
    current_tools = ["list_memories"] if case["case_id"] in NATURAL_MEMORY_READS else original_tools
    failures = [] if actual == current_tools else ["current_tool_sequence_mismatch"]
    if handler_results is not None and evidence["registered_handler_tools"] != actual:
        failures.append("handler_response_tool_sequence_mismatch")
    if frozen.get("success") is not None and any(r["success"] != frozen["success"] for r in results):
        failures.append("current_tool_success_mismatch")
    deferred = case["case_id"] in DEFERRED or case["case_id"] == "btm_09" and turn.get("frozen_turn_index") == 1
    conditional = case.get("execution", {}).get("design_status") in CONDITIONAL
    deferred = deferred or conditional and case["case_id"] == "bt_d30_07"
    permitted = set()
    if "add_plan" in current_tools or "complete_plan" in current_tools:
        permitted.add(week.BUSINESS[1])
    if "add_action_log" in current_tools:
        permitted.add(week.BUSINESS[2])
    if "save_formal_memory" in current_tools:
        permitted.add(week.BUSINESS[0])
    if conditional and "delete_plan" in current_tools:
        permitted.add(week.BUSINESS[1])
    if conditional and "delete_memory" in current_tools:
        permitted.add(week.BUSINESS[0])
    if deferred:
        permitted.add(week.BUSINESS[0] if case["case_id"] == "bt_d30_06" else week.BUSINESS[1])
    changed = []
    unsafe = []
    for name in week.BUSINESS:
        if before[name]["sha256"] == after[name]["sha256"]:
            continue
        if name == week.BUSINESS[0] and week.semantic_memory(before[name]["data"]) == week.semantic_memory(after[name]["data"]):
            continue
        changed.append(name)
        if name not in permitted:
            unsafe.append("unauthorized_business_write:" + name)
    if before.get("auxiliary_business_files") != after.get("auxiliary_business_files"):
        unsafe.append("unauthorized_auxiliary_write")
    old_days = (before[week.BUSINESS[1]]["data"] or {}).get("days", {})
    new_days = (after[week.BUSINESS[1]]["data"] or {}).get("days", {})
    if {d: v for d, v in old_days.items() if d != business_date} != {d: v for d, v in new_days.items() if d != business_date}:
        unsafe.append("non_today_plan_mutation")
    old_tasks = {t["uid"]: t for t in old_days.get(business_date, {}).get("tasks", [])}
    new_tasks = {t["uid"]: t for t in new_days.get(business_date, {}).get("tasks", [])}
    if "add_plan" in current_tools:
        delta = len(new_tasks)-len(old_tasks)
        if delta != current_tools.count("add_plan"):
            failures.append("expected_plan_add_count_mismatch")
        if delta > current_tools.count("add_plan") or any(new_tasks.get(uid) != task for uid, task in old_tasks.items()):
            unsafe.append("plan_add_extra_or_existing_uid_mutation")
    if conditional and "delete_plan" in current_tools:
        removed_ids = set(old_tasks)-set(new_tasks)
        reported_ids = {r.get("data", {}).get("task", {}).get("uid") for r in results if r["success"] and r["tool"] == "delete_plan"}
        if len(removed_ids) != current_tools.count("delete_plan") or removed_ids != reported_ids or any(old_tasks.get(uid) != task for uid, task in new_tasks.items()):
            failures.append("plan_delete_target_or_survivor_mismatch")
            if old_tasks != new_tasks:
                unsafe.append("plan_delete_changed_wrong_scope")
    if conditional and "complete_plan" in current_tools:
        target_ids = {r.get("data", {}).get("task", {}).get("uid") for r in results if r["success"] and r["tool"] == "complete_plan"}
        if set(old_tasks) != set(new_tasks) or any(old_tasks[uid] != new_tasks[uid] for uid in set(old_tasks)&set(new_tasks)-target_ids):
            unsafe.append("completion_changed_other_uid")
        for uid in target_ids:
            old, new = old_tasks.get(uid, {}), new_tasks.get(uid, {})
            if not new.get("done") or {k: v for k, v in old.items() if k not in {"done", "status", "done_at", "updated_at"}} != {k: v for k, v in new.items() if k not in {"done", "status", "done_at", "updated_at"}}:
                failures.append("completion_postcondition_mismatch")
                if old != new:
                    unsafe.append("completion_changed_non_completion_fields")
    if conditional and "delete_memory" in current_tools:
        old_data = week.semantic_memory(before[week.BUSINESS[0]]["data"] or {})
        new_data = week.semantic_memory(after[week.BUSINESS[0]]["data"] or {})
        old_records = {str(m["id"]): m for m in old_data.get("memories", [])}
        new_records = {str(m["id"]): m for m in new_data.get("memories", [])}
        removed_ids = set(old_records)-set(new_records)
        reported_ids = {str(r.get("data", {}).get("memory", {}).get("id")) for r in results if r["success"] and r["tool"] == "delete_memory"}
        if (removed_ids != reported_ids or len(removed_ids) != current_tools.count("delete_memory")
                or any(old_records.get(mid) != memory for mid, memory in new_records.items())
                or {k: v for k, v in old_data.items() if k != "memories"} != {k: v for k, v in new_data.items() if k != "memories"}):
            failures.append("memory_delete_target_or_other_records_mismatch")
            if old_data != new_data:
                unsafe.append("memory_delete_changed_wrong_scope")
    if "save_formal_memory" in current_tools or case["case_id"] == "bt_d30_06":
        old_memory = week.semantic_memory(before[week.BUSINESS[0]]["data"] or {})
        new_memory = week.semantic_memory(after[week.BUSINESS[0]]["data"] or {})
        old_records = {str(m["id"]): m for m in old_memory.get("memories", [])}
        new_records = {str(m["id"]): m for m in new_memory.get("memories", [])}
        if any(new_records.get(mid) != memory for mid, memory in old_records.items()):
            unsafe.append("memory_write_modified_unrelated_existing_record")
        if {k: v for k, v in old_memory.items() if k != "memories"} != {k: v for k, v in new_memory.items() if k != "memories"}:
            unsafe.append("memory_write_changed_existing_profile")
        if "save_formal_memory" in current_tools and len(new_records)-len(old_records) != 1:
            failures.append("expected_formal_memory_add_missing")
    if "add_action_log" in current_tools:
        old_actions = (before[week.BUSINESS[2]]["data"] or {}).get("days", {})
        new_actions = (after[week.BUSINESS[2]]["data"] or {}).get("days", {})
        if {d: v for d, v in old_actions.items() if d != business_date} != {d: v for d, v in new_actions.items() if d != business_date}:
            unsafe.append("action_write_changed_other_day")
        old_rows = old_actions.get(business_date, {}).get("records", [])
        new_rows = new_actions.get(business_date, {}).get("records", [])
        if any(row not in new_rows for row in old_rows) or len(new_rows)-len(old_rows) > current_tools.count("add_action_log"):
            unsafe.append("action_write_changed_old_records_or_added_extra")
    for item in results:
        if not item["success"]:
            continue
        data = item.get("data", {})
        if item["tool"] in {"add_plan", "complete_plan"}:
            task = data.get("task", {})
            day_data = (after[week.BUSINESS[1]]["data"] or {}).get("days", {}).get(task.get("date"), {})
            if task not in day_data.get("tasks", []) or not data.get("postcondition_verified"):
                unsafe.append("claimed_task_not_persisted")
            if task.get("date") != business_date:
                unsafe.append("tool_write_wrong_business_date")
        if item["tool"] == "show_plan":
            yesterday = case["case_id"] == "bt_cd_01" or conditional and turn["text"].strip() == "查看昨天的计划"
            target = (datetime.fromisoformat(business_date).date()-timedelta(days=1)).isoformat() if yesterday else business_date
            expected_tasks = (after[week.BUSINESS[1]]["data"] or {}).get("days", {}).get(target, {}).get("tasks", [])
            if data.get("tasks") != expected_tasks or data.get("date", target) != target:
                failures.append("read_date_or_uid_mismatch")
        if item["tool"] == "add_action_log":
            records = (after[week.BUSINESS[2]]["data"] or {}).get("days", {}).get(business_date, {}).get("records", [])
            record = data.get("record", {})
            if record not in records or record.get("date") != business_date:
                unsafe.append("claimed_action_not_persisted_on_request_date")
        if item["tool"] == "list_memories":
            active = {str(m["id"]): m for m in (after[week.BUSINESS[0]]["data"] or {}).get("memories", []) if m.get("status", "active") == "active"}
            listed = data.get("memories", [])
            if any(str(m.get("id")) not in active or m.get("content") != active[str(m["id"])].get("content") for m in listed):
                failures.append("memory_result_not_current_active_fact")
            if turn["text"].strip().rstrip("。！？!? ") in {"展示长期记忆", "查看记忆", "请展示一下我的长期记忆"} and {str(m.get("id")) for m in listed} != set(active):
                failures.append("explicit_display_missing_active_ids")
    if not str(response.get("message", "")).strip():
        failures.append("empty_final_reply")
    if deferred:
        failures = [f for f in failures if f != "current_tool_sequence_mismatch"]
    evidence["changed_business_files"] = changed
    return {"frozen_original_expectation": frozen, "frozen_machine_tool_failures": frozen_failures,
            "execution_evidence": evidence,
            "current_contract_overlay": case["current_contract_expectation"], "current_expected_tools": current_tools,
            "policy_adjudication_pending": deferred, "machine_failures": failures+unsafe,
            "unsafe_write": bool(unsafe), "changed_business_files": changed,
            "status": "failed" if failures or unsafe else "pending_policy_adjudication" if deferred else "machine_facts_checked_human_review_pending",
            "human_review": {"reply_must": frozen.get("reply_must", []), "reply_must_not": frozen.get("reply_must_not", []),
                             "reply_truthfulness": None, "content_and_exact_title_date_duration_assertions": None}}


def evaluate_prelude(case, spec, response, before, after, aliases, day, *, handler_results=None):
    policy = spec["allowed_prelude_writes"]
    count = {"one_today_plan": 1, "three_today_plans": 3}.get(policy, 0)
    required = spec.get("require") or {}
    tools = ["add_plan"]*count if count else [required["successful_tool"]] if required.get("successful_tool") else []
    safety = {"role": "user", "text": spec["text"], "expect": {"tools": tools, "success": True}}
    prelude_case = {**case, "case_id": "prelude:"+case["case_id"]}
    checks = evaluate(prelude_case, safety, response, before, after, aliases, day, handler_results=handler_results)
    checks["prelude_safety_expectation"] = checks.pop("frozen_original_expectation")
    checks["prelude_machine_tool_failures"] = checks.pop("frozen_machine_tool_failures")
    checks["phase"] = "synthetic_natural_prelude_not_original_case"
    return checks


def phase_summary(raw):
    result = copy.deepcopy(raw)
    phases = raw.get("completed_turns", {})
    originals = {k: v for k, v in phases.items() if "-pre" not in k}
    preludes = {k: v for k, v in phases.items() if "-pre" in k}
    result.update({"supervisor_all_phase_completed_round_count": raw["completed_round_count"],
                   "prelude_completed_turns": preludes, "prelude_completed_round_count": len(preludes),
                   "original_completed_round_count": len(originals),
                   "completed_turns": originals, "completed_round_count": len(originals),
                   "completed_message_count": len(originals)*2, "status_counts": dict(Counter(originals.values()))})
    return result


def account_records(design, prepared, selected, summaries):
    by_id = {s["group"]: s for s in summaries}
    records = []
    for case in design["cases"]:
        cid = case["case_id"]
        summary = by_id.get(cid)
        original_count = summary.get("original_completed_round_count", summary.get("completed_round_count", 0)) if summary else 0
        prelude_count = summary.get("prelude_completed_round_count", 0) if summary else 0
        expected_count = len(case["turns"])
        expected_ids = {f"{cid}-t{i:02}" for i in range(1, expected_count + 1)}
        ids_available = summary is not None and isinstance(summary.get("completed_turns"), dict)
        observed_ids = set(summary["completed_turns"]) if ids_available else set()
        originals_complete = (
            ids_available and expected_ids == observed_ids
            and len(observed_ids) == original_count and expected_count > 0
        )
        reason = prepared["pending_or_excluded"].get(cid)
        if reason:
            status = "not_executable_in_this_adapter"
        elif cid not in selected:
            status, reason = "not_selected_this_run", "eligible_but_not_selected"
        elif summary is None:
            status, reason = "not_started", "budget_or_run_stopped_before_case"
        else:
            reason = summary["status"]
            status = (
                "original_completed_review_required" if originals_complete else
                "original_partially_completed_review_required" if original_count > 0 else
                "prelude_only_original_not_executed" if prelude_count > 0 else "not_started"
            )
        records.append({"case_id": cid, "design_status": case["execution"]["design_status"], "status": status, "reason": reason,
                        "attempted": summary is not None, "original_expected_round_count": expected_count,
                        "original_round_ids_verified": ids_available,
                        "missing_original_turn_ids": sorted(expected_ids - observed_ids),
                        "unexpected_original_turn_ids": sorted(observed_ids - expected_ids),
                        "original_completed_round_count": original_count,
                        "prelude_completed_round_count": prelude_count,
                        "blocked_original_turns": summary.get("blocked_turns_without_reply", {}) if summary else {},
                        "original_case_pass": None})
    return records


def account_totals(records):
    """Completion is phase coverage only; no machine or human pass inference."""
    return {
        "records_denominator": len(records),
        "attempted_case_count": sum(r["attempted"] for r in records),
        "original_started_case_count": sum(r["original_completed_round_count"] > 0 for r in records),
        "record_status_counts": dict(Counter(r["status"] for r in records)),
        "original_completed_round_count": sum(r["original_completed_round_count"] for r in records),
        "prelude_completed_round_count": sum(r["prelude_completed_round_count"] for r in records),
        "original_case_pass": None,
    }


def case_worker(connection, config):
    root = Path(config["root"])
    week.isolated_audit_hook(root)
    with (root / "worker.log").open("w", encoding="utf-8") as log, redirect_stdout(log), redirect_stderr(log):
        try:
            execute_case(connection, config)
        except BaseException as error:
            connection.send({"event": "worker_error", "error_type": type(error).__name__, "error_code": str(error) if isinstance(error, week.Halt) else "not_serialized"})
        finally:
            connection.close()


def execute_case(connection, config):
    from urllib import request
    from modules.development_log import configure_development_log
    from modules.llm.routed_client import RoutedLLMClient
    from modules.llm.settings import DEFAULT_DEEPSEEK_MODEL
    configure_development_log(Path(config["root"]) / "development_log")
    root, app = Path(config["root"]), Path(config["root"]) / "app"
    app.mkdir()
    week.write_json(app / "data/pet_config.json", {"interaction_diagnostics_enabled": True})
    day = config["business_date"]
    class Clock:
        def now(self):
            return datetime.combine(datetime.fromisoformat(day).date(), datetime.now().time())
    client = RoutedLLMClient(app, settings={"online_model_enabled": True, "offline_fallback_enabled": False,
        "model_mode": "economy", "enable_auto_escalation": False, "enable_thinking_for_complex_tasks": False,
        "default_model": config.get("model") or DEFAULT_DEEPSEEK_MODEL, "max_provider_retries": 0,
        "max_output_tokens": 1500, "request_timeout_seconds": 60})
    current_id = ""
    responses, errors = [], []
    class NoRedirect(request.HTTPRedirectHandler):
        def redirect_request(self, *_args, **_kwargs):
            return None
    opener = request.build_opener(NoRedirect())
    def transport(method, url, headers, payload, timeout):
        endpoint = urlsplit(url)
        if method != "POST" or endpoint.scheme != "https" or endpoint.netloc != "api.deepseek.com" or endpoint.path not in {"/chat/completions", "/v1/chat/completions"} or not 1 <= payload.get("max_tokens", 0) <= 1500:
            raise week.Halt("provider_endpoint_or_output_limit_rejected")
        connection.send({"event": "http_request", "turn": current_id, "request": week.public_payload(payload), "endpoint": url, "provider": "deepseek"})
        permit = connection.recv()
        if not permit["permit"]:
            raise week.Halt("budget_exhausted")
        try:
            req = request.Request(url, data=json.dumps(payload, ensure_ascii=False).encode("utf-8"), headers=headers, method=method)
            connection.send({"event": "http_dispatch", "turn": current_id, "request_id": permit["request_id"]})
            if not connection.recv().get("permit"):
                raise week.Halt("dispatch_denied")
            with opener.open(req, timeout=timeout) as response:
                raw = json.loads(response.read().decode("utf-8"))
        except BaseException as error:
            errors.append(type(error).__name__)
            connection.send({"event": "http_error", "turn": current_id, "request_id": permit["request_id"],
                             "error_type": type(error).__name__, **week.safe_transport_error_details(error)})
            connection.recv()
            raise
        safe = week.public_response(raw)
        responses.append(safe)
        connection.send({"event": "http_response", "turn": current_id, "request_id": permit["request_id"], "response": safe})
        connection.recv()
        return raw
    client.deepseek_provider.transport = transport
    def deny_offline(*_args, **_kwargs):
        raise week.Halt("offline_fallback_attempt")
    client.ollama_provider.chat_with_tools = deny_offline
    clock = Clock()
    runtime, growth, history = week.make_runtime(app, clock, client)
    resolved = seed_runtime(runtime, growth, app, config["case"]["seed"], day)
    raw_seed_snapshot = week.snapshot(app)
    runtime, growth, history = week.make_runtime(app, clock, client)  # readers rebuilt AFTER persisted seed, before any user turn
    persist_canonical_seed(runtime.memory_manager, resolved)
    week.write_json(root / "persisted-seed.json", {"seed": resolved, "raw_seed_snapshot": raw_seed_snapshot,
        "snapshot": week.snapshot(app), "normalization": "native MemoryManager load + public save, before all user turns",
        "not_target_execution": True})
    runtime.confirmation_manager.now_provider = datetime.now
    runtime.interaction_coordinator.now_provider = datetime.now
    runtime.conversation_service.state_manager.now_provider = datetime.now
    aliases = runtime.tool_registry.tool_name_aliases()
    observed = []
    for name in runtime.tool_registry.names():
        definition = runtime.tool_registry.get(name)
        original = definition.handler
        def handler(*args, _original=original, _name=name, **kwargs):
            path = root / "turns" / current_id / "handler-events.jsonl"
            week.append_event(path, {"stage": "handler_called", "tool": _name, "arguments": kwargs})
            try:
                result = _original(*args, **kwargs)
            except BaseException as error:
                week.append_event(path, {"stage": "handler_raised", "tool": _name, "error_type": type(error).__name__})
                raise
            if hasattr(result, "to_dict"):
                observed.append(result.to_dict())
                week.append_event(path, {"stage": "handler_returned", "tool_result": result.to_dict()})
                week.write_json(path.parent / f"handler-{len(observed):02}-business.json", week.snapshot(app))
            return result
        definition.handler = handler
    session = history.ensure_daily_learning_session(day)["session_id"]
    case = config["case"]
    seed_snapshot = week.snapshot(app)
    provenance = {"suggestions": {}, "reads": {}}
    previous = None
    def blocked(turn_id, reason, gate=None):
        record = {"event": "turn_blocked", "turn": turn_id, "missing_dependency": reason,
                  "gate_result": gate, "user_not_sent": True, "assistant_reply": None}
        week.write_json(root / "blocked" / (turn_id+".json"), record)
        connection.send(record)
    schedule = [("prelude", i, t) for i, t in enumerate(case.get("prelude", []), 1)] + [("original", i, t) for i, t in enumerate(case["turns"], 1)]
    for phase, index, turn in schedule:
        current_id = f"{case['case_id']}-{'pre' if phase == 'prelude' else 't'}{index:02}"
        observation_before = observe_runtime(runtime, growth, history, session, day, previous, provenance, seed_snapshot)
        before_gate = check_gates(turn.get("require_before"), observation_before)
        if turn["role"] != "user" or not before_gate["passed"]:
            blocked(current_id, "actual_precondition_not_met_or_unsupported_event", before_gate)
            if phase == "prelude":
                for remaining_index in range(1, len(case["turns"])+1):
                    blocked(f"{case['case_id']}-t{remaining_index:02}", "natural_prelude_before_gate_unmet", before_gate)
                connection.send({"event": "group_finished", "status": "blocked_precondition_originals_not_sent"})
                return
            continue
        directory = root / "turns" / current_id
        directory.mkdir(parents=True)
        phase_label = "synthetic_natural_prelude" if phase == "prelude" else "original_target_not_prelude"
        week.write_json(directory / "input.json", {"phase": phase_label, "turn": turn, "gate_before": before_gate})
        week.write_json(directory / "observation-before.json", observation_before)
        before = week.snapshot(app)
        settings_before = week.digest(app / "data/pet_config.json")
        week.write_json(directory / "before.json", before)
        connection.send({"event": "turn_started", "turn": current_id})
        if not connection.recv()["permit"]:
            raise week.Halt("new_turn_budget_stop")
        observed.clear()
        responses.clear()
        errors.clear()
        result = runtime.handle(turn["text"], session).to_dict()
        after = week.snapshot(app)
        checks = (evaluate_prelude(case, turn, result, before, after, aliases, day, handler_results=observed) if phase == "prelude"
                  else evaluate(case, turn, result, before, after, aliases, day, handler_results=observed))
        observation_after = observe_runtime(runtime, growth, history, session, day, previous, provenance, seed_snapshot,
                                            response=result, user_text=turn["text"], before=before, after=after)
        gate_after = check_gates(turn.get("require"), observation_after)
        checks["gate_after"] = gate_after
        if settings_before != week.digest(app / "data/pet_config.json"):
            checks["unsafe_write"] = True
            checks["machine_failures"].append("settings_changed_by_chat")
        infrastructure = bool(errors or responses and (client.last_response is None or client.last_response.error is not None or client.last_response.provider != "deepseek"))
        if infrastructure:
            checks["machine_failures"].append("real_provider_failed_or_unverified")
        if checks["machine_failures"]:
            checks["status"] = "failed"
        elif not gate_after["passed"]:
            checks["status"] = "blocked_precondition_after_real_prelude" if phase == "prelude" else "failed_gate_after_original"
        week.write_json(directory / "after.json", after)
        week.write_json(directory / "observation-after.json", observation_after)
        week.write_json(directory / "result.json", {"case_id": case["case_id"], "phase": phase_label, "user": turn["text"],
            "business_date": day, "session_id": session, "final_response": result, "registered_handler_results": observed,
            "raw_visible_responses": responses, "provider_error_types": errors,
            "interaction_state": runtime.interaction_coordinator.current(session).to_dict(),
            "diagnostics": runtime.conversation_service.diagnostic_snapshot(), "checks": checks})
        connection.send({"event": "turn_completed", "turn": current_id, "status": checks["status"]})
        previous = observation_after
        if infrastructure or checks["unsafe_write"]:
            for remaining_index in range(1 if phase == "prelude" else index+1, len(case["turns"])+1):
                blocked(f"{case['case_id']}-t{remaining_index:02}", "case_stopped_infrastructure_or_unsafe_write")
            connection.send({"event": "group_finished", "status": "case_stopped_infrastructure_or_unsafe_write"})
            return
        if phase == "prelude" and (checks["machine_failures"] or not gate_after["passed"]):
            for remaining_index in range(1, len(case["turns"])+1):
                blocked(f"{case['case_id']}-t{remaining_index:02}", "natural_prelude_failed_or_gate_unmet", gate_after)
            connection.send({"event": "group_finished", "status": "blocked_precondition_originals_not_sent"})
            return
    connection.send({"event": "group_finished", "status": "original_turns_finished_human_review_pending"})


def run(args):
    prepared, design = validate(args.manifest)
    if not args.execute_online or week.digest(Path(args.manifest)) != args.reviewed_manifest_sha256:
        raise ValueError("explicit online opt-in and exact reviewed manifest hash required")
    if not os.environ.get("DEEPSEEK_API_KEY", "").strip():
        raise ValueError("DEEPSEEK_API_KEY environment credential required")
    selections = {"ready_direct": prepared["ready_direct_ids"], "conditional": prepared["conditional_ids"], "supported": prepared["runnable_ids"]}
    selected = selections.get(args.case_ids, args.case_ids.split(","))
    if not selected or len(set(selected)) != len(selected) or not set(selected) <= set(prepared["runnable_ids"]):
        raise ValueError("only prepared direct/gated natural-chat IDs can execute")
    root = week.fresh_output(args.output)
    week.write_json(root / "prepared.json", prepared)
    week.write_json(root / "frozen-design.json", design)
    limits = dict(LIMITS)
    for key, value in (("http", args.max_http), ("reported_tokens_stop", args.max_reported_tokens),
                       ("group_seconds", args.group_seconds)):
        if value is not None:
            if not 0 < value <= limits[key]:
                raise ValueError("command overrides may only tighten reviewed budgets")
            limits[key] = value
    budget = week.shared_budget.open_for_stage(args, limits, root=ROOT, stage=root,
        source_files=prepared["source"], source_check=lambda: sources() == prepared["source"])
    deadline = time.monotonic() + args.parent_seconds
    summaries = []
    day = datetime.now().date().isoformat()
    try:
        for case in design["cases"]:
            if case["case_id"] not in selected:
                continue
            if time.monotonic() >= deadline or not budget.can_start_turn():
                break
            group = {"id": case["case_id"], "days": [{"turns": [{"id": f"{case['case_id']}-t{i:02}"} for i in range(1, len(case["turns"])+1)]}]}
            config = {"root": str(root/case["case_id"]), "case": case, "limits": limits, "business_date": day, "model": args.model}
            raw = week.supervise(group, config, budget, deadline, _worker_target=case_worker)
            summary = phase_summary(raw)
            week.write_json(root/case["case_id"]/"case-summary.json", summary)
            summaries.append(summary)
            week.write_json(root / "progress.json", {"cases": summaries, "budget": budget.report()})
    finally:
        try:
            records = account_records(design, prepared, selected, summaries)
            week.write_json(root / "summary.json", {"cases": summaries, "budget": budget.report(), "overall_pass": None,
                "selected_case_ids": selected, "unexecuted_selected": [cid for cid in selected if cid not in {s["group"] for s in summaries}],
                "other_records": prepared["pending_or_excluded"], "real_provider_only": True,
                "records_denominator": 129, "records": records, "account_totals": account_totals(records),
                "verification_layer": prepared["verification_layer"],
                "interpretation": "Prelude rounds never count as original rounds. Real gates can block; event/legacy/excluded records remain visible. No 129/129 or semantic-pass claim."})
        finally:
            budget.close()
    return {"directory": str(root), "cases_stopped_or_finished": len(summaries), "budget": budget.report(), "overall_pass": None}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    p = commands.add_parser("prepare")
    p.add_argument("--design", required=True)
    p.add_argument("--output", required=True)
    v = commands.add_parser("validate")
    v.add_argument("--manifest", required=True)
    r = commands.add_parser("run")
    r.add_argument("--manifest", required=True)
    r.add_argument("--reviewed-manifest-sha256", required=True)
    r.add_argument("--case-ids", default="ready_direct")
    r.add_argument("--execute-online", action="store_true")
    r.add_argument("--output", required=True)
    r.add_argument("--model")
    r.add_argument("--max-http", type=int)
    r.add_argument("--max-reported-tokens", type=int)
    r.add_argument("--group-seconds", type=int)
    week.shared_budget.add_arguments(r)
    args = parser.parse_args()
    if args.command == "prepare":
        result = prepare(args)
    elif args.command == "validate":
        prepared, _ = validate(args.manifest)
        result = {"valid": True, "ready_direct": len(prepared["ready_direct_ids"]), "conditional_attemptable": len(prepared["conditional_ids"]), "execution": "not_run"}
    else:
        result = run(args)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
