"""Bounded W01/W03/W04 real-provider recheck; prepare never imports the product.

Uses the existing supervised worker, transport journal and shared budget. Seed
receipts are explicitly synthetic preparation, not invented previous user turns.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.dont_write_bytecode = True
from scripts import run_learning_week_acceptance as week
from scripts import online_acceptance_budget as shared
from scripts.learning_week_plan import step

LEDGER = ROOT / "test_workspace/runs/online-resume-20261026/shared-budget.json"
START_HTTP, START_TOKENS = 294, 1238609
LIMIT_HTTP, LIMIT_TOKENS = 90, 450000
DAY, PRIOR = "2026-11-24", "2026-11-23"
LIMITS = {"http": LIMIT_HTTP, "reported_tokens_stop": LIMIT_TOKENS,
          "output_tokens_per_request": 1500, "group_seconds": 180, "turn_seconds": 80}


def source_snapshot():
    result = week.source_snapshot()
    for path in [Path(__file__), *ROOT.glob("tests/test_seven_day_priority*.py"),
                 ROOT / "tests/fixtures/seven_day_priority_20261027.json"]:
        result[path.relative_to(ROOT).as_posix()] = week.digest(path)
    return result


def turn(tid, text, **kwargs):
    return {"id": tid, **step(text, **kwargs)}


def build_plan():
    captured = json.loads((ROOT / "tests/fixtures/seven_day_priority_20261027.json").read_text(encoding="utf-8"))["cases"]
    choices = [captured["W01"]["user"], "请把编号一那项加入今天计划。", "编号1加入今天计划。"]
    queries = [captured["W03"]["user"], "请给我看一下昨日已保存的复盘，不要重新保存。",
               "看看昨天的复盘，完成任务不代表已经学会。"]
    advice = [captured["W04"]["user"],
              "今天能学20分钟过拟合，结合昨天的实际进展给两项今日候选，分别12和8分钟，先只展示。",
              "请给我两项今天学习过拟合的计划建议，第一项12分钟、第二项8分钟。只给我选，不要加入计划，也说说怎样采用。"]
    groups = []
    for family, expressions in [("W01", choices), ("W03", queries), ("W04", advice)]:
        for variant, expression in enumerate(expressions, 1):
            for repetition in range(1, 4):
                gid = f"{family}-v{variant}-r{repetition}"
                if family == "W03":
                    turns = [turn(f"{gid}-query", expression, tools=["generate_daily_review"], inspect_date="yesterday")]
                else:
                    request = ("给我两项今天的Python学习计划建议，第一项12分钟、第二项8分钟。先只展示，不要加入。"
                               if family == "W01" else expression)
                    turns = [turn(f"{gid}-advice", request, capture="advice"),
                             turn(f"{gid}-first", expression if family == "W01" else "第一项加入今天计划。",
                                  tools=["add_plan"], change="plan_add", selection=1, requires="advice"),
                             turn(f"{gid}-second", "编号二那项也加入今天计划。" if family == "W01" else "第二项也加入今天计划。",
                                  tools=["add_plan"], change="plan_add", selection=2, requires="advice")]
                    turns[1]["expected"]["duration_minutes"] = 12
                    turns[2]["expected"]["duration_minutes"] = 8
                seeded = family in {"W03", "W04"}
                days = ([{"day": 1, "date": PRIOR, "topic": "独立合成前置", "turns": []}] if seeded else [])
                days.append({"day": 2 if seeded else 1, "date": DAY, "topic": family, "turns": turns})
                groups.append({"id": gid, "family": family, "variant": variant, "repetition": repetition,
                               "seed": "yesterday_completed_pending_action_review_profile" if seeded else "empty",
                               "days": days})
    return {"schema_version": 1, "scope": "27 independent synthetic instances; each family 3 expressions x 3 preplanned repetitions",
            "limits": LIMITS, "ledger_start": {"http": START_HTTP, "tokens": START_TOKENS}, "groups": groups,
            "human_review": ["No pooled offline/online/version pass rate", "W04 conditional-offer generation coverage must be observed, not forced or retried",
                             "W02/W05/W06/W07 unchanged; original 20 failures/15 blocked rounds remain original facts"],
            "source": {"head": week.git("rev-parse", "HEAD"), "dirty": bool(week.git("status", "--porcelain")),
                       "files": source_snapshot(), "diff": week.git("diff", "--binary")}}


def prepare_runtime(runtime, app):
    receipts = []

    def call(name, **arguments):
        result = runtime.tool_registry.get(name).handler(**arguments)
        receipts.append({"tool": name, "arguments": arguments, "result": result.to_dict()})
        if not result.success:
            raise RuntimeError("synthetic_preparation_failed:" + name)
        return result

    first = call("add_plan", title="理解训练集与测试集的区别", date=PRIOR, duration_minutes=12)
    call("add_plan", title="说明训练集和测试集各用在哪一步", date=PRIOR, duration_minutes=8)
    call("complete_plan", match_text=first.data["task"]["uid"])
    call("add_action_log", content="整理训练集与测试集概念卡", date=PRIOR, duration_minutes=12)
    call("save_daily_review", date=PRIOR)
    call("save_formal_memory", content="我的长期目标是打好机器学习基础。")
    return {"scope": "Synthetic setup only; zero user/API rounds; real registered handlers and persisted facts",
            "receipts": receipts, "business": week.snapshot(app)}


def seeded_worker(connection, config):
    original = week.make_runtime

    def factory(app, clock, client, **kwargs):
        runtime, growth, history = original(app, clock, client, **kwargs)
        if config["group"]["seed"] != "empty":
            week.write_json(Path(config["root"]) / "preparation.json", prepare_runtime(runtime, app))
        return runtime, growth, history

    week.make_runtime = factory
    week.worker(connection, config)


def review_group(root, group):
    records = {p.parent.name: json.loads(p.read_text(encoding="utf-8")) for p in (root / "turns").glob("*/result.json")}
    failures, coverage = [], []
    for tid, result in records.items():
        failures.extend({"turn": tid, "failure": f} for f in result["checks"]["failures"])
        if tid.endswith("advice"):
            snapshot = result["interaction_state"].get("last_suggestion_snapshot") or {}
            if snapshot.get("source_turn_id") != result["final_response"]["request_id"]:
                failures.append({"turn": tid, "failure": "snapshot_not_from_current_reply"})
            prose = [r["choices"][0]["message"]["content"] for r in result["raw_visible_responses"]]
            coverage.append({"turn": tid, "raw_generation_saved": bool(prose),
                             "conditional_offer_present": any("选好" in s and "告诉我" in s and "再" in s for s in prose)})
        for handler in result["registered_handler_results"]:
            if handler["tool"] == "add_plan" and handler["success"]:
                task = handler["data"]["task"]
                if not any(t == task for t in result["tasks_after"]):
                    failures.append({"turn": tid, "failure": "handler_task_not_persisted"})
    return {"family": group["family"], "completed": len(records), "mechanical_failures": failures,
            "generation_coverage": coverage, "human_review_required": True}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=["prepare", "run"])
    parser.add_argument("--output", required=True)
    parser.add_argument("--plan")
    parser.add_argument("--reviewed-plan-sha256")
    parser.add_argument("--execute-online", action="store_true")
    args = parser.parse_args()
    output = week.fresh_output(args.output)
    if args.mode == "prepare":
        plan = build_plan()
        week.write_json(output / "plan.json", plan)
        print(json.dumps({"plan": str(output / "plan.json"), "sha256": week.digest(output / "plan.json"), "http": 0}))
        return
    plan_path = Path(args.plan)
    plan = json.loads(plan_path.read_text(encoding="utf-8"))
    if not args.execute_online or week.digest(plan_path) != args.reviewed_plan_sha256:
        raise ValueError("exact plan SHA and explicit online opt-in required")
    if source_snapshot() != plan["source"]["files"]:
        raise ValueError("reviewed source drift")
    if not os.environ.get("DEEPSEEK_API_KEY", "").strip():
        raise ValueError("environment credential required")
    if not LEDGER.is_file():
        raise ValueError("existing shared ledger required")
    state = json.loads(LEDGER.read_text(encoding="utf-8"))
    used_http = state["http_reserved"] - START_HTTP
    used_tokens = state["reported_tokens"] - START_TOKENS
    if used_http < 0 or used_tokens < 0:
        raise ValueError("shared budget predates authorized baseline")
    limits = {**LIMITS, "http": LIMIT_HTTP - used_http, "reported_tokens_stop": LIMIT_TOKENS - used_tokens}
    if limits["http"] <= 0 or limits["reported_tokens_stop"] < 30000:
        raise ValueError("batch budget exhausted")
    week.write_json(output / "frozen-plan.json", plan)
    week.write_json(output / "manifest.json", {"source": plan["source"], "plan_sha256": week.digest(plan_path),
                                             "python": sys.version, "limits_remaining_this_batch": limits})
    budget = shared.SharedBudget(LEDGER, limits, used_http=state["http_reserved"], used_tokens=state["reported_tokens"],
        remaining_http=1500-state["http_reserved"], remaining_tokens=3000000-state["reported_tokens"],
        stage=output, source_files=plan["source"]["files"], source_check=lambda: source_snapshot() == plan["source"]["files"], token_reserve=30000)
    outcomes, reviews = [], []
    deadline = time.monotonic() + 600
    try:
        for group in plan["groups"]:
            if time.monotonic() >= deadline or not budget.can_start_turn():
                break
            config = {"root": str(output / group["id"]), "group": group, "limits": limits, "model": "deepseek-v4-flash"}
            outcome = week.supervise(group, config, budget, deadline, _worker_target=seeded_worker)
            outcomes.append(outcome)
            review = review_group(output / group["id"], group)
            reviews.append(review)
            week.write_json(output / "progress.json", {"outcomes": outcomes, "reviews": reviews, "budget": budget.report()})
            if outcome.get("infrastructure_failure") or outcome.get("interrupted_turn") or review["mechanical_failures"] or outcome["unexecuted_turns"]:
                budget.halt("priority_recheck_failed_or_incomplete")
                break
    except BaseException:
        budget.halt("priority_recheck_parent_interrupted")
        raise
    finally:
        week.write_json(output / "summary.json", {"outcomes": outcomes, "reviews": reviews, "budget": budget.report(),
            "source_unchanged": source_snapshot() == plan["source"]["files"], "overall_pass": None,
            "not_started_groups": [g["id"] for g in plan["groups"] if g["id"] not in {r["group"] for r in outcomes}]})
        budget.close()
    print(json.dumps({"output": str(output), "groups_finished": len(outcomes), "budget": budget.report()}, ensure_ascii=False))


if __name__ == "__main__":
    main()
