"""Production-chain replay acceptance tests for V2.1.

Runs incident regression and optimization set through the real
AgentService → ConversationService chain with isolated data.

Reports are separated by provenance: incident_regression vs optimization.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
TESTS = ROOT / "tests"
for p in (str(ROOT), str(TESTS)):
    if p not in sys.path:
        sys.path.insert(0, p)

from replay_acceptance_runner import ReplayRunner

FIXTURES = TESTS / "fixtures"


def _load_cases(filename: str) -> list:
    path = FIXTURES / filename
    if not path.exists():
        return []
    data = json.loads(path.read_text("utf-8"))
    return list(data.get("cases", []))


INCIDENT_CASES = _load_cases("v21_incident_regression.json")
OPTIMIZATION_V2_CASES = _load_cases("v21_optimization_v2.json")


# ── Incident Regression ──────────────────────────────────────────────────

@pytest.mark.parametrize(
    "case",
    INCIDENT_CASES,
    ids=[c["id"] for c in INCIDENT_CASES],
)
def test_incident_regression_case(case):
    """已知事故回归：证明同一个 Bug 不再复发。"""
    runner = ReplayRunner(mode="stub")
    report = runner.run_case(case)
    _assert_report(report, case["id"], "incident_regression")


# ── Optimization Set V2 ──────────────────────────────────────────────────

@pytest.mark.parametrize(
    "case",
    OPTIMIZATION_V2_CASES,
    ids=[c["id"] for c in OPTIMIZATION_V2_CASES],
)
def test_optimization_v2_case(case):
    """可见优化集：基于语言现象的全新对话结构。"""
    runner = ReplayRunner(mode="stub")
    report = runner.run_case(case)
    _assert_report(report, case["id"], "optimization")


# ── Multi-turn specific tests ────────────────────────────────────────────

def test_incident_001_schema_reject_no_business_impersonation():
    """incident_001 单独验证：schema 失败后回复不含业务询问。"""
    case = next(c for c in INCIDENT_CASES if c["id"] == "incident_001")
    runner = ReplayRunner(mode="stub")
    report = runner.run_case(case)
    assert report["passed"], report.get("turns", [{}])[0].get("failure_reasons", [])
    turn = report["turns"][0]
    reply = turn.get("final_response", "")
    assert "几点" not in reply, f"Reply asks time after schema rejection: {reply}"
    assert "多久" not in reply, f"Reply asks duration after schema rejection: {reply}"


def test_incident_003_bare_affirmative_no_raw_write():
    """incident_003 单独验证：bare affirmative 不触发写工具。"""
    case = next(c for c in INCIDENT_CASES if c["id"] == "incident_003")
    runner = ReplayRunner(mode="stub")
    report = runner.run_case(case)
    assert report["passed"], report.get("turns", [{}])[0].get("failure_reasons", [])
    turn = report["turns"][0]
    assert turn["actual_write_count"] == 0
    tools = [t["tool"] for t in turn["tool_calls"]]
    assert "save_formal_memory" not in tools


def test_incident_006_preference_save_then_panel_visible():
    """incident_006 单独验证：保存偏好后面板可见。"""
    case = next(c for c in INCIDENT_CASES if c["id"] == "incident_006")
    runner = ReplayRunner(mode="stub")
    report = runner.run_case(case)
    assert report["passed"], report.get("turns", [{}])[0].get("failure_reasons", [])
    mem_result = report.get("panel_results", {}).get("memory_content", {})
    assert mem_result.get("passed", False), f"Panel memory check failed: {mem_result}"


def test_incident_007_previous_turn_reference_chain():
    """incident_007 单独验证：第一轮愿望澄清，第二轮明确添加计划。"""
    case = next(c for c in INCIDENT_CASES if c["id"] == "incident_007")
    runner = ReplayRunner(mode="stub")
    report = runner.run_case(case)
    assert report["passed"], report.get("turns", [{}])[0].get("failure_reasons", [])
    t0 = report["turns"][0]
    assert t0["status"] == "clarification"
    t1 = report["turns"][1]
    assert t1["status"] == "completed"
    assert "add_plan" in [t["tool"] for t in t1["tool_calls"]]


def test_opt_v2_013_four_turn_panel_final_state():
    """opt_v2_013 四轮构建计划后面板验证。"""
    case = next(c for c in OPTIMIZATION_V2_CASES if c["id"] == "opt_v2_013")
    runner = ReplayRunner(mode="stub")
    report = runner.run_case(case)
    assert report["passed"], report.get("turns", [{}])[0].get("failure_reasons", [])
    panel = report.get("panel_results", {}).get("plan_panel", {})
    assert panel.get("passed"), f"Panel check failed: {panel}"


def test_opt_v2_015_dialect_read_no_write():
    """opt_v2_015 方言查询不触发写操作。"""
    case = next(c for c in OPTIMIZATION_V2_CASES if c["id"] == "opt_v2_015")
    runner = ReplayRunner(mode="stub")
    report = runner.run_case(case)
    assert report["passed"], report.get("turns", [{}])[0].get("failure_reasons", [])
    turn = report["turns"][0]
    assert turn["actual_write_count"] == 0


# ── Categories report ────────────────────────────────────────────────────

def test_incident_regression_summary():
    """报告已知事故回归分类通过率。"""
    runner = ReplayRunner(mode="stub")
    results = {}
    for case in INCIDENT_CASES:
        report = runner.run_case(case)
        cat = case.get("category", "unknown")
        if cat not in results:
            results[cat] = {"passed": 0, "failed": 0, "cases": []}
        if report["passed"]:
            results[cat]["passed"] += 1
        else:
            results[cat]["failed"] += 1
            results[cat]["cases"].append(case["id"])

    total_pass = sum(r["passed"] for r in results.values())
    total = len(INCIDENT_CASES)
    print(f"\n=== Incident Regression Summary ===")
    print(f"Overall: {total_pass}/{total} = {total_pass/total*100:.1f}%")
    for cat, r in sorted(results.items()):
        print(f"  {cat}: {r['passed']}/{r['passed']+r['failed']} passed")
    failed_cases = []
    for r in results.values():
        if r['failed'] > 0:
            failed_cases.extend(r['cases'])
    assert total_pass == total, f"Incident regression failures: {failed_cases}"


def test_optimization_v2_summary():
    """报告可见优化集分类通过率。"""
    runner = ReplayRunner(mode="stub")
    results = {}
    for case in OPTIMIZATION_V2_CASES:
        report = runner.run_case(case)
        cat = case.get("category", "unknown")
        if cat not in results:
            results[cat] = {"passed": 0, "failed": 0, "cases": []}
        if report["passed"]:
            results[cat]["passed"] += 1
        else:
            results[cat]["failed"] += 1
            results[cat]["cases"].append(case["id"])

    total_pass = sum(r["passed"] for r in results.values())
    total = len(OPTIMIZATION_V2_CASES)
    print(f"\n=== Optimization V2 Summary ===")
    print(f"Overall: {total_pass}/{total} = {total_pass/total*100:.1f}%")
    for cat, r in sorted(results.items()):
        print(f"  {cat}: {r['passed']}/{r['passed']+r['failed']} passed")
    failed_cases = []
    for r in results.values():
        if r['failed'] > 0:
            failed_cases.extend(r['cases'])
    assert total_pass == total, f"Optimization failures: {failed_cases}"


# ── Helper ───────────────────────────────────────────────────────────────

def _assert_report(report, case_id, provenance):
    """Validate a replay report and assert pass."""
    if not report["passed"]:
        failures = []
        for t in report.get("turns", []):
            failures.extend(t.get("failure_reasons", []))
        for k, v in report.get("panel_results", {}).items():
            if not v.get("passed", True):
                failures.append(f"panel_{k}:{v}")
        pytest.fail(
            f"[{provenance}] {case_id} failed: {failures}"
        )
