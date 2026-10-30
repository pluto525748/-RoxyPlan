"""Prepare/review/run a new natural-dialogue gate on the existing week worker.

No HTTP at import/prepare/validate. This is an explicitly labelled new
engineering comparison, not a rewritten first attempt of the historical P0.
"""
from __future__ import annotations

import argparse
import copy
import json
import os
import re
import sys
import time
from datetime import date, datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from scripts import run_learning_week_acceptance as week


LIMITS = {"http": 60, "reported_tokens_stop": 200000,
          "output_tokens_per_request": 1500, "turn_seconds": 80,
          "group_seconds": 600, "parent_seconds": 600}
MEMORY = "我习惯在周日晚上整理这一周的学习笔记"
GOAL = "我的长期目标是提高Python基础"
PREFERENCE = "我偏好安静的学习环境"
ORIGINAL_CHALLENGE = "你确定吗？刚才不是这么说的。请核对已保存的目标和偏好，别把推测当记录。"
CAPTURED_FALSE_DENIAL = "当时并没有真正写入长期记忆"


def turn(id, user, tools=(), change="none", **extra):
    return {"id": id, "user": user,
            "expected": {"tools": list(tools), "change": change, "successful_tools": True}, **extra}


def build_gate(real_date):
    today = date.fromisoformat(real_date)
    yesterday = (today - timedelta(days=1)).isoformat()
    behind = (today - timedelta(days=7)).isoformat()
    ahead = (today + timedelta(days=10)).isoformat()
    def day(value, turns, ordinal=1):
        return {"date": value, "day": ordinal, "turns": turns}
    memory = [
        turn("gate-memory-t01", "我想让你记住" + MEMORY, ["save_formal_memory"], "memory_add"),
        turn("gate-memory-t02", "展示一下我的长期记忆。", ["list_memories"], memory_content=MEMORY),
        turn("gate-memory-t03", ORIGINAL_CHALLENGE, ["list_memories"],
             memory_attributes=["goal", "preference"], memory_expected_contents=[],
             historical_save_content=MEMORY,
             tool_sequences=[["list_memories"], ["list_memories", "list_memories"]]),
        turn("gate-memory-t04", "请查看我的记忆审计记录。", hidden_audit=True),
        turn("gate-memory-t05", "我不想让你记住我习惯每天早上跑步。"),
        turn("gate-memory-t06", "我希望以后能养成每天早上跑步的习惯。"),
        turn("gate-memory-t07", "怎么才能让你记住我每天要喝足够的水？"),
    ]
    historical = [
        turn("gate-source-t01", "我想让你记住" + MEMORY, ["save_formal_memory"], "memory_add"),
        turn("gate-source-t02", "刚才那条已经保存了吗？", historical_save_content=MEMORY,
             tool_sequences=[[], ["list_memories"]]),
    ]
    categories = [
        turn("gate-categories-t01", "我想让你记住" + GOAL, ["save_formal_memory"], "memory_add"),
        turn("gate-categories-t02", "我想让你记住" + PREFERENCE, ["save_formal_memory"], "memory_add"),
        turn("gate-categories-t03", "我想让你记住" + MEMORY, ["save_formal_memory"], "memory_add"),
        turn("gate-categories-t04", "请核对已保存的目标和偏好。", ["list_memories"],
             memory_attributes=["goal", "preference"], memory_expected_contents=[GOAL, PREFERENCE],
             tool_sequences=[["list_memories"], ["list_memories", "list_memories"]]),
    ]
    review_seed = [
        turn("gate-review-t01", "记录今天的计划外行动：整理Python错误示例，实际用了5分钟。",
             ["add_action_log"], "action_add"),
        turn("gate-review-t02", "今日复盘。", ["save_daily_review"], "review_save"),
        turn("gate-review-t03", "重新生成并保存今天的复盘。", ["save_daily_review"], "review_save",
             review_target_date=yesterday, regenerated=True),
    ]
    review_seed[0]["expected"]["duration_minutes"] = 5
    review_read = [
        turn("gate-review-t04", "请查看昨天的复盘，核对实际记录的行动和时长。",
             ["generate_daily_review"], review_target_date=yesterday),
    ]
    behind_turns = [
        turn("gate-behind-t01", "今天想练习公开表达。请给两条今天可做的具体建议，第一条总共15分钟、第二条总共10分钟，"
             "可以使用两分钟录音或视频作为素材。用1、2编号，每条最后注明总时长，先别加入计划。",
             capture="advice", expected_totals=[15, 10]),
        turn("gate-behind-t02", "第二个加入今天计划", ["add_plan"], "plan_add", requires="advice", selection=2),
        turn("gate-behind-t03", "第一个也加入今天计划", ["add_plan"], "plan_add", requires="advice", selection=1),
    ]
    ahead_turns = [
        turn("gate-ahead-t01", "今天想练习英语听力。请给两条今天可做的具体建议，用1、2编号，"
             "每条最后注明总时长，只展示，先别加入计划。", capture="advice"),
        turn("gate-ahead-t02", "全部加入今天计划", ["add_plan", "add_plan"],
             "deferred_plan_authorization", requires="advice", select_all=True),
    ]
    return {"schema_version": 2, "kind": "revised_memory_scope_gate_new_version_retest",
            "real_date": real_date, "limits": dict(LIMITS),
            "evaluator_contract_revision": {
                "previous_tested_head": "8923189f4165d8917d52c96d410651c32e5e88a3",
                "previous_plan_sha256": "af5ca7e1c00cf0aa003bdf629c2b145fe28bfd6f3d70909c99315e8ab9dcf849",
                "original_three_inputs_preserved": True,
                "historical_receipt": "live revalidated prior operation/facts or matching typed read; no mandatory new handler",
                "explicit_categories": "typed goal+preference scope only; habit never proves a match or whole-store absence",
                "old_false_denial": "remains a real failure, independent of obsolete mandatory-handler metric"},
            "groups": [
                {"id": "gate-memory", "days": [day(real_date, memory)]},
                {"id": "gate-source", "days": [day(real_date, historical)]},
                {"id": "gate-categories", "days": [day(real_date, categories)]},
                {"id": "gate-review", "days": [day(yesterday, review_seed), day(real_date, review_read, 2)]},
                {"id": "gate-behind", "days": [day(behind, behind_turns)]},
                {"id": "gate-ahead", "days": [day(ahead, ahead_turns)]}],
            "limits_of_evidence": ["synthetic dates, not real multi-day user benefit",
                "no seeded pending/suggestions, no model fallback or retries",
                "machine facts plus human reply review; no automatic semantic pass",
                "explicit 15/10 comparison is new wording; ordinary advice follows actual displayed totals"]}


def build_remaining_gate(real_date):
    """Independent untouched groups; never replay the interrupted audit turn."""
    plan = copy.deepcopy(build_gate(real_date))
    original_groups = {g["id"]: g for g in plan["groups"]}
    original_turns = {t["id"]: (g["id"], t) for g in plan["groups"]
                      for d in g["days"] for t in d["turns"]}
    controls = []
    for index, original in enumerate(original_groups["gate-memory"]["days"][0]["turns"][4:], 1):
        new = copy.deepcopy(original)
        new["id"] = f"gate-categories-control-t{index:02}"
        new["origin_turn_id"] = original["id"]
        controls.append(new)
    plan["groups"] = [g for g in plan["groups"] if g["id"] != "gate-memory"]
    categories = next(g for g in plan["groups"] if g["id"] == "gate-categories")
    categories["days"][0]["turns"].extend(controls)
    mapping = []
    setup_ids = {"gate-source-t01", "gate-categories-t01", "gate-categories-t02", "gate-categories-t03"}
    for group in plan["groups"]:
        for day in group["days"]:
            for item in day["turns"]:
                origin = item.get("origin_turn_id", item["id"])
                original_group, original = original_turns[origin]
                mapping.append({"new_turn_id": item["id"], "origin_turn_id": origin,
                    "origin_group": original_group, "new_group": group["id"],
                    "user_text_unchanged": item["user"] == original["user"],
                    "expected_unchanged": item["expected"] == original["expected"],
                    "counted_user_round": True,
                    "natural_setup": origin in setup_ids})
    plan.update(schema_version=3, kind="remaining_independent_gate_after_http23",
        remaining_provenance={
            "prior_head": "3b3bd23c3d94f9205deb8dbbd404104fce9d2cbb",
            "prior_plan_sha256": "0886a46ccda1ff789ba422c89eea9a9f02583f57893eabeb2a0af5c6da36e9b5",
            "previous_run": "test_workspace/runs/online-revised-memory-20261026/gate-run-3b3bd23-first",
            "completed_not_replayed": ["gate-memory-t01", "gate-memory-t02", "gate-memory-t03"],
            "interrupted_not_retried": {"turn": "gate-memory-t04", "request": "http-00023"},
            "execution_context": "new independent app roots, not transparent continuation of the old session",
            "moved_controls": "original last three memory inputs now follow natural goal/preference/habit saves and scoped read",
            "coverage_limit": "18 counted user rounds, including all natural setup; never backfill old 22 as fully passed",
            "turn_mapping": mapping})
    return plan


def select_groups(plan, selection):
    """Validate explicit selection; execution always follows frozen plan order."""
    known = [g["id"] for g in plan["groups"]]
    if selection == "all":
        return known
    selected = [part.strip() for part in str(selection or "").split(",")]
    if (not all(selected) or len(selected) != len(set(selected))
            or not set(selected) <= set(known)):
        raise ValueError("explicit nonempty unique groups from the reviewed plan required")
    return [name for name in known if name in selected]


def displayed_total(title):
    """Independent observable total; ambiguous prose stays unverifiable."""
    text = str(title).strip()
    explicit = re.findall(r"(?:总共|总时长|共计)\s*[：:]?\s*(\d+)\s*分钟", text)
    if len(set(explicit)) == 1:
        return int(explicit[0])
    terminal = re.search(r"(\d+)\s*分钟\s*[。.!！]?\s*$", text)
    return int(terminal.group(1)) if terminal else None


def observe_composer_context(composer, sink):
    """Observe real composer inputs; never synthesize or change product facts."""
    original = composer.compose
    def observed(*args, **kwargs):
        sink.append(copy.deepcopy(kwargs.get("verified_turn_context") or {}))
        return original(*args, **kwargs)
    composer.compose = observed


def check_memory_contract(turn, before, after, final, results, composition_contexts):
    failures = []
    evidence = {"failures": failures}
    reads = [r.get("data", {}).get("memory_read", {}) for r in results
             if r.get("tool") == "list_memories" and r.get("success")]
    records = (after.get(week.BUSINESS[0], {}).get("data") or {}).get("memories", [])
    live = {str(r.get("id")): r for r in records if r.get("status", "active") == "active"}
    attributes = turn.get("memory_attributes")
    if attributes:
        covered = set()
        returned = []
        normalized = lambda value: re.sub(r"[\s。！？!?]", "", str(value))
        for snapshot in reads:
            request = snapshot.get("request", {})
            attribute = request.get("attribute")
            if request.get("topic"):
                failures.append("unrequested_topic_narrows_category_scope")
            if request.get("query_mode") != "attribute":
                failures.append("category_query_widened_or_wrong_mode")
            elif attribute in attributes:
                covered.add(attribute)
            elif (attribute == "fact" and set(attributes) == {"goal", "preference"}
                  and normalized(request.get("query")) == normalized(turn["user"])):
                # Existing external schema: the reviewed closed dual-category
                # query uses attribute=fact, not a new public list parameter.
                covered.update(attributes)
            else:
                failures.append("category_query_scope_not_proven")
            returned.extend(snapshot.get("facts", []))
        if covered != set(attributes):
            failures.append("typed_category_scope_coverage_missing")
        contents = [f.get("content") for f in returned]
        if set(contents) != set(turn["memory_expected_contents"]):
            failures.append("category_results_do_not_match_known_saved_scope")
        if len({str(f.get("memory_id")) for f in returned}) != len(returned):
            failures.append("duplicate_memory_read_identity")
        for fact in returned:
            record = live.get(str(fact.get("memory_id")))
            if (fact.get("authority") != "formal_memory" or record is None
                    or record.get("content") != fact.get("content")):
                failures.append("typed_category_fact_not_current_formal_record")
        evidence["category_scope"] = {"requested": attributes, "covered": sorted(covered),
                                      "returned_facts": returned, "matching_record_count": len(returned)}
    content = turn.get("historical_save_content")
    if content:
        old = (before.get(week.BUSINESS[0], {}).get("data") or {}).get("memories", [])
        prior = [r for r in old if r.get("content") == content and r.get("status", "active") == "active"]
        retained = len(prior) == 1 and bool(prior[0].get("uid")) and any(r.get("uid") == prior[0].get("uid")
            and r.get("content") == content for r in live.values())
        operations = [c.get("prior_memory_operation", {}) for c in composition_contexts]
        historical_facts = [f for c in composition_contexts for f in c.get("prior_memory_facts", [])]
        operation_valid = any(o.get("kind") == "memory_saved" and o.get("status") == "success"
            and o.get("content") == content and o.get("source") == "formal_memory_tool"
            and o.get("observed_at") for o in operations)
        fact_valid = any(f.get("content") == content and f.get("source") == "explicit_user_command"
                         and f.get("observed_at") for f in historical_facts)
        matching_read = any(f.get("content") == content and f.get("authority") == "formal_memory"
                            and live.get(str(f.get("memory_id")), {}).get("content") == content
                            for r in reads for f in r.get("facts", []))
        if not retained or not (operation_valid or fact_valid or matching_read):
            failures.append("historical_save_has_no_live_verified_evidence")
        evidence["historical_save"] = {"same_live_uid_and_content": retained,
            "observed_revalidated_operation": bool(operation_valid),
            "observed_revalidated_facts": fact_valid, "matching_typed_read": matching_read,
            "requires_new_handler": False}
        if CAPTURED_FALSE_DENIAL in str(final.get("message", "")).replace(" ", ""):
            failures.append("captured_false_denial_of_prior_save_reappeared")
    return evidence


def check_gate_turn(turn, before, after, final, state, anchors, calls, results, business_date,
                    *, composition_contexts=()):
    failures = []
    facts = {"failures": failures, "business_date": business_date}
    if any(x["tool"] == "show_memory_audit" for x in calls + results):
        failures.append("hidden_audit_handler_executed")
    memory_contract = check_memory_contract(turn, before, after, final, results, composition_contexts)
    facts["memory_contract"] = memory_contract
    failures.extend(memory_contract["failures"])
    content = turn.get("memory_content")
    if content:
        read_facts = [f for x in results if x["tool"] == "list_memories"
                      for f in x.get("data", {}).get("memory_read", {}).get("facts", [])]
        if content not in [x.get("content") for x in read_facts]:
            failures.append("saved_memory_missing_from_actual_typed_read")
    advice = anchors.get("advice", {})
    objects = advice.get("objects", [])
    if turn.get("capture") == "advice":
        totals = [displayed_total(x.get("title")) for x in objects]
        facts["displayed_totals"] = totals
        if len(totals) != 2 or any(x is None or x < 1 for x in totals):
            failures.append("displayed_total_unverifiable_not_a_duration_product_verdict")
        if "expected_totals" in turn and totals != turn["expected_totals"]:
            failures.append("suggestions_do_not_meet_explicit_user_duration_constraints")
        captured = advice.get("captured_at", "")
        if captured[:10] != datetime.now().date().isoformat() or advice.get("target_date") != business_date:
            failures.append("snapshot_wall_date_or_business_target_mismatch")
    if turn.get("selection") or turn.get("select_all"):
        selected = objects if turn.get("select_all") else [x for x in objects if x.get("display_order") == turn["selection"]]
        old = (before[week.BUSINESS[1]]["data"] or {}).get("days", {}).get(business_date, {}).get("tasks", [])
        new = (after[week.BUSINESS[1]]["data"] or {}).get("days", {}).get(business_date, {}).get("tasks", [])
        added = [x for x in new if x.get("uid") not in {y.get("uid") for y in old}]
        if len(selected) != (2 if turn.get("select_all") else 1) or len(added) != len(selected):
            failures.append("selected_object_count_does_not_match_actual_additions")
        if any(x not in new for x in old):
            failures.append("adoption_changed_existing_uid_record")
        layers = []
        def compact(value):
            return re.sub(r"\s+", "", str(value))
        for item in selected:
            expected = displayed_total(item.get("title"))
            matches = [x for x in added if compact(x.get("title")) and compact(x.get("title")) in compact(item.get("title"))]
            if len(matches) != 1 or expected is None:
                failures.append("adoption_has_no_unique_displayed_total_and_saved_target")
                continue
            saved = matches[0]
            matching_calls = [x for x in calls if x["tool"] == "add_plan" and x.get("arguments", {}).get("title") == saved["title"]]
            matching_results = [x for x in results if x["tool"] == "add_plan" and x.get("data", {}).get("task", {}).get("uid") == saved["uid"]]
            final_results = [x for x in final.get("tool_results", []) if x["tool"] == "add_plan" and x.get("data", {}).get("task", {}).get("uid") == saved["uid"]]
            row = {"display_order": item["display_order"], "displayed": expected,
                   "handler": matching_calls[0].get("arguments", {}).get("duration_minutes") if len(matching_calls) == 1 else None,
                   "handler_result": matching_results[0]["data"]["task"].get("duration_minutes") if len(matching_results) == 1 else None,
                   "tool_result": final_results[0]["data"]["task"].get("duration_minutes") if len(final_results) == 1 else None,
                   "persisted": saved.get("duration_minutes"), "uid": saved["uid"]}
            layers.append(row)
            if any(row[k] != expected for k in ("handler", "handler_result", "tool_result", "persisted")):
                failures.append("displayed_total_to_actual_write_duration_mismatch")
        facts["duration_layers"] = layers
    if turn.get("review_target_date"):
        target = turn["review_target_date"]
        for x in results:
            data = x.get("data", {})
            review = data.get("entry", {}).get("review", data.get("review", {}))
            if review.get("date") != target or review.get("action_count") != 1 or (review.get("action_records") or [{}])[0].get("duration_minutes") != 5:
                failures.append("yesterday_review_date_or_saved_action_fact_mismatch")
        if turn.get("regenerated"):
            prior = (before[week.BUSINESS[3]]["data"] or {}).get("entries", {}).get(target, {})
            saved = (after[week.BUSINESS[3]]["data"] or {}).get("entries", {}).get(target, {})
            if not prior or saved.get("uid") != prior.get("uid") or saved.get("revision") != prior.get("revision", 0) + 1:
                failures.append("regenerated_review_not_same_uid_next_revision")
    return facts


def validate(path):
    plan = json.loads(Path(path).read_text(encoding="utf-8"))
    builder = build_remaining_gate if plan.get("kind") == "remaining_independent_gate_after_http23" else build_gate
    expected = builder(plan["real_date"])
    if {k: v for k, v in plan.items() if k != "source"} != expected:
        raise ValueError("reviewed gate content changed")
    if plan["source"]["files"] != week.source_snapshot():
        raise ValueError("gate source freeze changed")
    return plan


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    p = commands.add_parser("prepare")
    p.add_argument("--output", required=True)
    p.add_argument("--real-date", default=date.today().isoformat())
    p.add_argument("--remaining", action="store_true")
    v = commands.add_parser("validate")
    v.add_argument("--plan", required=True)
    r = commands.add_parser("run")
    r.add_argument("--plan", required=True)
    r.add_argument("--reviewed-plan-sha256", required=True)
    r.add_argument("--output", required=True)
    r.add_argument("--execute-online", action="store_true")
    r.add_argument("--model", default="deepseek-v4-flash")
    r.add_argument("--groups", required=True)
    week.shared_budget.add_arguments(r)
    args = parser.parse_args()
    if args.command == "prepare":
        root = week.fresh_output(args.output)
        plan = (build_remaining_gate if args.remaining else build_gate)(args.real_date)
        plan["source"] = {"head": week.git("rev-parse", "HEAD"),
                          "dirty": bool(week.git("status", "--porcelain")), "files": week.source_snapshot()}
        week.write_json(root / "plan.json", plan)
        result = {"plan": str(root / "plan.json"), "sha256": week.digest(root / "plan.json"), "execution": "not_run"}
    elif args.command == "validate":
        plan = validate(args.plan)
        result = {"valid": True, "user_turns": sum(len(d["turns"]) for g in plan["groups"] for d in g["days"]), "execution": "not_run"}
    else:
        plan = validate(args.plan)
        selected = select_groups(plan, args.groups)
        unselected = [g["id"] for g in plan["groups"] if g["id"] not in selected]
        if not args.execute_online or week.digest(Path(args.plan)) != args.reviewed_plan_sha256:
            raise ValueError("online opt-in and exact reviewed plan SHA required")
        if plan["real_date"] != date.today().isoformat():
            raise ValueError("real date changed; prepare/review a new gate without rewriting the old plan")
        if not os.environ.get("DEEPSEEK_API_KEY", "").strip():
            raise ValueError("environment credential required")
        root = week.fresh_output(args.output)
        week.write_json(root / "frozen-plan.json", plan)
        week.write_json(root / "manifest.json", {"head": week.git("rev-parse", "HEAD"),
            "dirty": bool(week.git("status", "--porcelain")), "source": week.source_snapshot(),
            "plan_sha256": args.reviewed_plan_sha256, "python": sys.version, "model_requested": args.model,
            "selected_groups": selected, "unselected_groups": unselected})
        budget = week.shared_budget.open_for_stage(args, plan["limits"], root=ROOT, stage=root,
            source_files=plan["source"]["files"], source_check=lambda: week.source_snapshot() == plan["source"]["files"])
        summaries = []
        deadline = time.monotonic() + args.parent_seconds
        try:
            for group in plan["groups"]:
                if group["id"] not in selected:
                    continue
                if time.monotonic() >= deadline or not budget.can_start_turn():
                    break
                config = {"root": str(root / group["id"]), "group": group, "limits": plan["limits"],
                          "model": args.model, "gate_contract": True}
                summary = week.supervise(group, config, budget, deadline)
                summaries.append(summary)
                if any(x == "failed" for x in summary["completed_turns"].values()) or summary["unexecuted_turns"]:
                    budget.halt("gate_failed_or_incomplete")
                    break
        except BaseException:
            budget.halt("gate_parent_interrupted")
            raise
        finally:
            try:
                week.write_json(root / "summary.json", {"groups": summaries, "budget": budget.report(),
                    "overall_pass": None, "new_engineering_gate": True,
                    "selected_groups": selected, "unselected_groups": unselected,
                    "selected_planned_user_rounds": sum(len(d["turns"]) for g in plan["groups"] if g["id"] in selected for d in g["days"]),
                    "not_started_groups": [name for name in selected if name not in {s["group"] for s in summaries}],
                    "human_review_required": True})
            finally:
                budget.close()
        result = {"directory": str(root), "budget": budget.report(), "overall_pass": None}
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
