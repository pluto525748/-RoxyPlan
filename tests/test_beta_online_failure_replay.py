"""离线回放：2026-10-14 真实 DeepSeek 55 轮工程首轮结果的交互失败与相邻对照。

夹具：tests/fixtures/beta_online_failures_20261014.json
来源标注：工程合成真实API首次结果（不是内测用户直接语料，也不是本批新的模型运行）。

回放方式：真实组合根 server.agent_service.AgentService + 真实 RoutedLLMClient；provider 只通过其自带
transport 注入点提供当轮捕获的真实返回（OpenAI 兼容响应体），不替换任何产品判断函数、不改变产品分支。
隔离根由 tmp_path 提供，经 scripts/run_isolated_tests.ps1 运行；本模块不联网、不创建窗口、不碰正式数据。

分批交付：
- planned_change（onl_g07_long_advice、onl_g15_complete_all、onl_g01_direction_discussion、
  onl_g26_plain_chat）：对应补丁由总控审查集成后才应通过；此前运行会按设计失败，并保留首次结果。
- control（onl_g08_short_advice、onl_g14_display_then_complete、onl_g19_memory_save_display、
  onl_g21_memory_delete_cancel、onl_g22_memory_delete_confirm）：当前基线即应通过；对照失败时先怀疑
  回放链或环境，而不是补丁。

回放只证明本地链与捕获格式，不证明真实模型稳定性。捕获序列不足或与请求顺序不符时回放明确失败。
集成后G07-2和G15-1有明确的本地零调用断言；旧返回仍保留在原夹具，其他回合必须完全消费。
"""
from __future__ import annotations

import json
import sys
from dataclasses import dataclass
from datetime import datetime
from functools import lru_cache
from pathlib import Path
from typing import Dict, List, Optional
from urllib.parse import urlsplit

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from modules.llm.deepseek_provider import DeepSeekProvider  # noqa: E402
from modules.llm.ollama_provider import OllamaProvider  # noqa: E402
from modules.llm.routed_client import RoutedLLMClient  # noqa: E402
from server.agent_service import AgentService  # noqa: E402

FIXTURE_PATH = ROOT / "tests" / "fixtures" / "beta_online_failures_20261014.json"

# 生产分类器系统提示的稳定标记（modules/intent_router.py）。
CLASSIFIER_MARKER = "You classify one Chinese user message"
# 非真实密钥：仅让 provider 处于 configured 状态；回放期间不会发出任何网络请求。
REPLAY_API_KEY = "offline-replay-no-credential"
RECORDED_HOST = "api.deepseek.com"

# modules/response_composer.py 中两处整段替换的开头文本（F3 证据）。
GUARD_CONFIRMATION = "这次没有建立可执行的确认操作"
GUARD_ABSENCE = "这个问题需要先读取实际记录"
ABSENCE_LIKE = ("今天没有计划", "还没有计划", "没有已记录的计划")
FALSE_CLAIMS = ("已加入", "已保存", "已添加", "已经安排", "已记录")

PLANNED_TEXT = {
    "onl_g15_complete_all": [
        "把今天所有未完成计划标记完成。",
        "确认",
    ],
}
# Reviewed local replacements after integration. Keep the captured responses in
# the immutable fixture; these two turns now MUST make zero provider requests.
# Every other round still consumes its complete captured sequence in order.
LOCAL_ONLY_AFTER_FIX = {
    "onl_g07_long_advice": {"G07-2"},
    "onl_g15_complete_all": {"G15-1"},
}
# 计划预置（与首次运行的 seed='three' 完全一致）。
SEED_THREE = ["整理笔记", "练一道算法题", "读英文材料"]
SEED_MEMORY = ["我叫星河", "我的长期目标是准备 Python 面试"]


# ── 夹具读取 ──────────────────────────────────────────────────────────────

@lru_cache(maxsize=1)
def _fixture() -> Dict[str, object]:
    return json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))


def _case(case_id: str) -> Dict[str, object]:
    for case in _fixture()["cases"]:
        if case["case_id"] == case_id:
            return case
    raise AssertionError(f"夹具中没有用例 {case_id}")


# ── 离线 transport ────────────────────────────────────────────────────────

def _system_text(payload: Optional[Dict[str, object]]) -> str:
    if not isinstance(payload, dict):
        return ""
    messages = payload.get("messages")
    if not isinstance(messages, list) or not messages:
        return ""
    first = messages[0]
    if isinstance(first, dict):
        return str(first.get("content", ""))
    return ""


def _provider_body(entry: Dict[str, object]) -> Dict[str, object]:
    """重建当次真实返回的 OpenAI 兼容响应体（等价于真实链路 json.loads 的结果）。"""
    usage = entry.get("usage") or {}
    return {
        "id": f"replay-{entry.get('purpose')}",
        "object": "chat.completion",
        "created": 0,
        "model": entry.get("model") or "",
        "choices": [
            {
                "index": 0,
                "message": {
                    "role": "assistant",
                    "content": entry.get("content") or "",
                    "tool_calls": [],
                },
                "finish_reason": entry.get("finish_reason") or "stop",
            }
        ],
        "usage": {
            "prompt_tokens": usage.get("prompt_tokens") or 0,
            "completion_tokens": usage.get("completion_tokens") or 0,
            "total_tokens": usage.get("total_tokens") or 0,
        },
    }


class ReplayTransport:
    """按回合顺序回放已记录的真实 provider 返回，并对顺序/用途做显式绑定检查。"""

    def __init__(self) -> None:
        self.round_id = ""
        self.entries: List[Dict[str, object]] = []
        self.position = 0
        self.calls: List[Dict[str, str]] = []
        self.round_attempts = 0

    def expect_round(self, round_id: str, entries: List[Dict[str, object]]) -> None:
        self.round_id = round_id
        self.entries = list(entries)
        self.position = 0
        self.round_attempts = 0

    def assert_round_consumed(self) -> None:
        if self.position != len(self.entries):
            raise AssertionError(
                f"回放序列未用尽：{self.round_id} 记录了 {len(self.entries)} 次 provider 返回，"
                f"实际只调用 {self.position} 次；调用序列已改变，需回报总控重新捕获，"
                "不得改写夹具或跳过断言。"
            )

    def __call__(self, method, url, headers, payload, timeout):
        self.round_attempts += 1
        host = urlsplit(str(url)).hostname or ""
        if method != "POST" or host != RECORDED_HOST:
            raise AssertionError(
                f"回放期间出现未捕获的 provider 请求：{method} {url}；"
                "回放只允许已记录的 DeepSeek 聊天调用。"
            )
        purpose = "semantic" if CLASSIFIER_MARKER in _system_text(payload) else "reply"
        if self.position >= len(self.entries):
            raise AssertionError(
                f"回放序列不足：{self.round_id} 第 {self.position + 1} 次 provider 调用（{purpose}）"
                "没有对应的已捕获返回；不得虚构模型输出，需回报总控重新捕获。"
            )
        entry = self.entries[self.position]
        if entry.get("purpose") != purpose:
            raise AssertionError(
                f"回放绑定不符：{self.round_id} 第 {self.position + 1} 次调用用途为 {purpose}，"
                f"捕获记录为 {entry.get('purpose')}；调用序列已改变，需回报总控重新捕获。"
            )
        self.position += 1
        self.calls.append({"round": self.round_id, "purpose": purpose})
        return _provider_body(entry)


def _forbidden_ollama_transport(method, url, headers, payload, timeout):
    raise AssertionError(
        f"回放期间禁止访问本地模型服务：{method} {url}；"
        "首次运行 offline_fallback_enabled=false，不应出现本地降级调用。"
    )


# ── 隔离组合根 ────────────────────────────────────────────────────────────

def _build_service(root: Path, transport: ReplayTransport) -> AgentService:
    settings = dict(_fixture()["meta"]["settings"])
    data_dir = root / "data"
    (data_dir / "private").mkdir(parents=True, exist_ok=True)
    (data_dir / "pet_config.json").write_text(
        json.dumps(settings, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (data_dir / "roxy_personality.json").write_text(
        json.dumps(
            {
                "version": 1,
                "name": "Roxy",
                "personality": "温和、认真、可靠的学习伙伴。",
                "speaking_style": "自然、简洁、直接。",
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    (root / "memory.json").write_text(
        json.dumps({"version": 2, "profile": {}, "memories": []}, ensure_ascii=False),
        encoding="utf-8",
    )
    timeout_seconds = int(settings.get("request_timeout_seconds", 90))
    deepseek = DeepSeekProvider(
        api_key=REPLAY_API_KEY,
        base_url=str(settings.get("deepseek_base_url", "https://api.deepseek.com")),
        model_name=str(settings.get("default_model", "deepseek-v4-flash")),
        timeout_seconds=timeout_seconds,
        max_retries=int(settings.get("max_provider_retries", 0)),
        transport=transport,
    )
    ollama = OllamaProvider(
        base_url=str(settings.get("ollama_base_url", "http://localhost:11434/v1")),
        model_name=str(settings.get("offline_model", "qwen3:4b")),
        timeout_seconds=timeout_seconds,
        transport=_forbidden_ollama_transport,
    )
    client = RoutedLLMClient(
        root,
        settings=settings,
        legacy_config={},
        deepseek_provider=deepseek,
        ollama_provider=ollama,
    )
    return AgentService(project_root=root, llm_client=client)


def _seed(service: AgentService, case: Dict[str, object]) -> None:
    seed = case["seed"]
    for title in seed.get("plans") or []:
        service.growth_manager.add_task(str(title))
    for text in seed.get("memories") or []:
        service.memory_manager.add_memory(str(text), source="engineering_fixture")


# ── 回合记录与通用检查 ────────────────────────────────────────────────────

def _snapshot(service: AgentService) -> Dict[str, object]:
    growth = service.growth_manager
    return {
        "plans": [
            {
                "id": task.get("id"),
                "uid": task.get("uid"),
                "title": task.get("title"),
                "done": bool(task.get("done")),
                "duration": task.get("duration_minutes"),
            }
            for task in growth.tasks()
        ],
        "memories": [
            {"id": item.get("id"), "uid": item.get("uid"), "content": item.get("content")}
            for item in service.memory_manager.memories()
        ],
        "actions": len(growth.records_for_date(datetime.now().date().isoformat())),
        "reviews": len(growth.entries()),
    }


def _successful_writes(response) -> List[str]:
    return [
        item.tool
        for item in response.tool_results
        if item.success and item.operation_kind == "write"
    ]


def _tool_names(response) -> List[str]:
    return [item.tool for item in response.tool_results]


@dataclass
class TurnOutcome:
    round_id: str
    user_text: str
    response: object
    before: Dict[str, object]
    after: Dict[str, object]
    state_after: Dict[str, object]
    provider_attempts: int

    @property
    def message(self) -> str:
        return str(self.response.message or "")

    @property
    def writes(self) -> List[str]:
        return _successful_writes(self.response)


@dataclass
class ReplayRun:
    service: AgentService
    case: Dict[str, object]
    turns: List[TurnOutcome]
    session_id: str


def _domain_counts(snapshot: Dict[str, object]) -> Dict[str, int]:
    return {
        "plans": len(snapshot["plans"]),
        "memories": len(snapshot["memories"]),
        "actions": snapshot["actions"],
        "reviews": snapshot["reviews"],
    }


def _run_case(case_id: str, tmp_path: Path) -> ReplayRun:
    case = _case(case_id)
    transport = ReplayTransport()
    service = _build_service(tmp_path, transport)
    _seed(service, case)
    session_id = service.chat_history_manager.ensure_daily_learning_session(
        datetime.now().date().isoformat()
    )["session_id"]

    outcomes: List[TurnOutcome] = []
    for turn in case["turns"]:
        local_only = turn["round"] in LOCAL_ONLY_AFTER_FIX.get(case_id, set())
        if local_only:
            assert len(turn["responses"]) == 1 and turn["responses"][0]["purpose"] == "semantic"
        transport.expect_round(turn["round"], [] if local_only else turn["responses"])
        before = _snapshot(service)
        response = service.handle(turn["user_text"], session_id)
        if local_only:
            assert transport.round_attempts == 0, f"{turn['round']} 必须复用本地绑定状态，不能重新请求模型"
        transport.assert_round_consumed()
        after = _snapshot(service)
        state_after = service.interaction_coordinator.current(session_id).to_dict()
        outcome = TurnOutcome(turn["round"], turn["user_text"], response, before, after,
                              state_after, transport.round_attempts)
        assert outcome.message.strip(), f"{turn['round']} 回复为空"
        _check_forbidden_writes(case, outcome)
        outcomes.append(outcome)
    return ReplayRun(service=service, case=case, turns=outcomes, session_id=session_id)


def _check_forbidden_writes(case: Dict[str, object], outcome: TurnOutcome) -> None:
    before = _domain_counts(outcome.before)
    after = _domain_counts(outcome.after)
    for domain in case["forbidden_writes"]:
        assert before[domain] == after[domain], (
            f"{outcome.round_id} 禁止写入域 {domain} 发生变化：{before[domain]} → {after[domain]}；"
            "本批要求确认前与讨论轮零写入。"
        )


# ── 夹具自检（不启动服务，可在补丁集成前运行） ────────────────────────────

def test_fixture_provenance_and_shape() -> None:
    fixture = _fixture()
    meta = fixture["meta"]
    assert meta["source_label"] == "工程合成真实API首次结果"
    assert meta["source_head"] == "a895810e748f8b6722b70e296e70365b1c16b4fd"
    assert len(meta["manifest_sha256"]) == 64 and len(meta["raw_results_sha256"]) == 64

    cases = fixture["cases"]
    case_ids = [case["case_id"] for case in cases]
    assert len(case_ids) == len(set(case_ids)), "case_id 必须唯一"
    assert len(cases) == 9
    assert meta["stages"]["planned_change"] == [
        case["case_id"] for case in cases if case["stage"] == "planned_change"
    ]
    assert meta["stages"]["control"] == [
        case["case_id"] for case in cases if case["stage"] == "control"
    ]

    responses = 0
    for case in cases:
        assert case["stage"] in {"planned_change", "control"}
        assert case["expected_after_patch"], f"{case['case_id']} 缺少补丁后期望"
        assert case["seed"]["kind"] in {"none", "three", "memory"}
        for turn in case["turns"]:
            assert turn["user_text"].strip()
            assert turn["first_result"]["status"], f"{turn['round']} 缺少首次结果"
            assert "counts" in turn["first_result"]
            for entry in turn["responses"]:
                assert entry["purpose"] in {"semantic", "reply"}
                assert str(entry["content"]).strip()
                if entry["purpose"] == "semantic":
                    payload = json.loads(entry["content"])
                    assert "mode" in payload and "intent" in payload
                responses += 1
    assert responses == 19, "19 条真实返回被完整保留，不多不少"

    # 契约句面必须与记录一致，不为凑数量增加礼貌前后缀。
    g15_turns = _case("onl_g15_complete_all")["turns"]
    assert [turn["user_text"] for turn in g15_turns] == PLANNED_TEXT["onl_g15_complete_all"]
    assert [turn["user_text"] for turn in _case("onl_g07_long_advice")["turns"]] == [
        "我今晚有一个半小时，想练 Python 列表推导式和读英文技术文档。给我两条具体的今日建议，先别加入计划。",
        "把第二条加入今天计划。",
    ]


# ── planned_change：等待补丁集成后转绿 ────────────────────────────────────

def test_replay_still_rejects_unused_control_responses_and_wrong_purpose() -> None:
    entry = _case("onl_g15_complete_all")["turns"][0]["responses"][0]
    transport = ReplayTransport()
    transport.expect_round("control-round", [entry])
    with pytest.raises(AssertionError, match="未用尽"):
        transport.assert_round_consumed()
    with pytest.raises(AssertionError, match="绑定不符"):
        transport("POST", "https://api.deepseek.com/chat/completions", {},
                  {"messages": [{"role": "system", "content": "ordinary reply"}]}, 5)
    assert transport.position == 0
    assert transport.round_attempts == 1


def test_local_only_replay_rejects_any_unexpected_provider_request() -> None:
    transport = ReplayTransport()
    transport.expect_round("local-only", [])
    with pytest.raises(AssertionError, match="序列不足"):
        transport("POST", "https://api.deepseek.com/chat/completions", {},
                  {"messages": [{"role": "system", "content": CLASSIFIER_MARKER}]}, 5)
    assert transport.round_attempts == 1
    assert transport.position == 0


def test_onl_g01_direction_discussion(tmp_path: Path) -> None:
    run = _run_case("onl_g01_direction_discussion", tmp_path)
    for outcome in run.turns:
        assert outcome.writes == [], f"{outcome.round_id} 讨论轮不得有成功写入"
        assert outcome.after["plans"] == []
        assert outcome.after["memories"] == []

    state = run.service.interaction_coordinator.current(run.session_id)
    assert not getattr(state, "pending", False), "方向讨论不应留下待确认状态"

    last = run.turns[2]
    assert last.after["plans"] == [], "第 3 轮明确“不用加入今天计划”"
    assert "第一周" in last.message, (
        "第 3 轮应保留模型原答的第一周讲解；当前回复为："
        f"{last.message[:120]}"
    )
    assert GUARD_CONFIRMATION not in last.message, (
        "命中 response_composer.py:1038 的无待处理项整段替换（F3：正常讨论原答被替换成拒绝）"
    )
    assert not any(word in last.message for word in FALSE_CLAIMS), (
        "讨论轮不得声称已修改记录"
    )


def test_onl_g26_plain_chat(tmp_path: Path) -> None:
    run = _run_case("onl_g26_plain_chat", tmp_path)
    for outcome in run.turns:
        assert outcome.writes == [], f"{outcome.round_id} 闲聊轮不得有成功写入"
        assert outcome.after["plans"] == []
        assert outcome.after["memories"] == []

    second = run.turns[1]
    assert GUARD_ABSENCE not in second.message, (
        "命中 response_composer.py:1118 的缺失数据整段替换（F3：闲聊原答被替换成拒绝）"
    )
    assert len(second.message.strip()) >= 8, "闲聊轮应给出自然回复"
    if any(phrase in second.message for phrase in ABSENCE_LIKE):
        reads = [
            item
            for item in second.response.tool_results
            if item.success and item.operation_kind == "read"
        ]
        assert reads, "回复包含“没有计划”一类事实断言时，同轮必须存在真实读取结果"


def test_onl_g07_long_advice(tmp_path: Path) -> None:
    run = _run_case("onl_g07_long_advice", tmp_path)
    first, second = run.turns

    assert first.writes == [], "第 1 轮只给建议，不写入"
    assert first.after["plans"] == []

    assert second.writes == ["add_plan"], (
        "第 2 轮应把真实第二条建议落入今日计划；实际成功写入："
        f"{second.writes}，状态 {second.response.status}"
    )
    assert len(second.after["plans"]) == 1, "除该 1 条外不得有其他计划写入"
    plan = second.after["plans"][0]
    assert "读英文技术文档" in str(plan["title"]), f"标题应为真实第二条建议，实际 {plan['title']!r}"
    assert plan["duration"] == 35, f"应保留建议中的 35 分钟，实际 {plan['duration']!r}"
    assert "刚才的建议列表已经失效" not in second.message, (
        "不得回复列表失效；当前回复为：" + second.message[:120]
    )
    assert "重新列出" not in second.message, "不得要求用户重新列出建议"
    assert second.provider_attempts == 0, "采用已展示的第二条不再由模型重新猜测目标"
    assert second.after["memories"] == []


def test_onl_g15_complete_all(tmp_path: Path) -> None:
    run = _run_case("onl_g15_complete_all", tmp_path)
    first, second = run.turns

    assert [plan["title"] for plan in first.before["plans"]] == SEED_THREE, "前置状态应为预置三项"
    assert first.writes == [], "第 1 轮（确认前）必须零写入"
    assert [plan["title"] for plan in first.after["plans"]] == SEED_THREE
    assert not any(plan["done"] for plan in first.after["plans"]), "确认前不得完成任何计划"

    for title in SEED_THREE:
        assert title in first.message, f"第 1 轮应先展示真实范围（缺 {title}）"
    assert first.provider_attempts == 0
    assert first.state_after["state"] == "awaiting_confirmation", (
        "第 1 轮展示真实范围后应建立统一确认（无待确认操作且无待确认状态）"
    )

    assert second.response.status == "completed", (
        f"第 2 轮“确认”应由原执行器完成；实际状态 {second.response.status}"
    )
    assert "complete_plan" in second.writes, f"应由原执行器完成计划；实际写入 {second.writes}"
    assert [plan["title"] for plan in second.after["plans"]] == SEED_THREE, "不得清空或新增计划"
    assert all(plan["done"] for plan in second.after["plans"]), "确认后真实范围三项都应完成"
    assert [plan["uid"] for plan in first.before["plans"]] == [plan["uid"] for plan in second.after["plans"]]
    assert second.after["memories"] == []

    first_result = run.case["turns"][0]["first_result"]
    assert first_result["status"] == "failed" and first_result["counts"]["plans"] == 3, (
        "保留旧首次拒绝结果作为历史证据"
    )


# ── control：当前基线即应通过的相邻对照 ──────────────────────────────────

def test_onl_g08_short_advice(tmp_path: Path) -> None:
    run = _run_case("onl_g08_short_advice", tmp_path)
    first, second, third = run.turns

    assert first.writes == [] and first.after["plans"] == []
    assert second.writes == ["add_plan"]
    assert [plan["title"] for plan in second.after["plans"]] == ["复习 SQL"]
    assert second.after["plans"][0]["duration"] == 20

    assert third.writes == ["add_plan"]
    titles = [plan["title"] for plan in third.after["plans"]]
    assert titles == ["复习 SQL", "整理错题"], f"续选应保留顺序且不重复新增，实际 {titles}"
    assert all(plan["duration"] == 20 for plan in third.after["plans"]), "二十分钟不得丢失"
    assert third.after["memories"] == []


def test_onl_g14_display_then_complete(tmp_path: Path) -> None:
    run = _run_case("onl_g14_display_then_complete", tmp_path)
    first, second, third = run.turns

    assert first.writes == [], "展示轮只读"
    assert [plan["title"] for plan in first.after["plans"]] == SEED_THREE
    assert "show_plan" in _tool_names(first.response)

    assert second.writes == ["complete_plan"], f"只完成第二项，实际 {second.writes}"
    done_titles = [plan["title"] for plan in second.after["plans"] if plan["done"]]
    assert done_titles == ["练一道算法题"], f"只应完成第二项，实际 {done_titles}"
    assert sum(1 for plan in second.after["plans"] if not plan["done"]) == 2

    assert third.writes == [], "剩余查询只读"
    assert third.after["plans"] == second.after["plans"], "只读轮数据不得变化"
    assert third.after["memories"] == []


def test_onl_g19_memory_save_display(tmp_path: Path) -> None:
    run = _run_case("onl_g19_memory_save_display", tmp_path)
    first, second, third = run.turns
    goal = "我的长期目标是大模型开发应用与agent开发应用。"
    name = "我叫星河。"

    assert first.writes == ["save_formal_memory"]
    assert [item["content"] for item in first.after["memories"]] == [goal]
    assert second.writes == ["save_formal_memory"]
    assert [item["content"] for item in second.after["memories"]] == [goal, name]

    assert third.writes == [], "展示轮只读"
    assert [item["content"] for item in third.after["memories"]] == [goal, name]
    for fragment in ("记录编号 1：", "记录编号 2：", goal, name):
        assert fragment in third.message, f"展示应保留原文与真实编号（缺 {fragment!r}）"
    assert third.after["plans"] == []


def test_onl_g21_memory_delete_cancel(tmp_path: Path) -> None:
    run = _run_case("onl_g21_memory_delete_cancel", tmp_path)
    _, second, third = run.turns

    assert [item["content"] for item in second.before["memories"]] == SEED_MEMORY
    assert second.response.status == "confirmation_required", (
        f"删除应先请求确认；实际状态 {second.response.status}"
    )
    pending = second.response.pending_confirmation
    assert pending is not None and pending.tool == "delete_memory"
    star_id = next(item["id"] for item in second.after["memories"] if item["content"] == "我叫星河")
    assert pending.arguments.get("memory_id") == star_id, (
        "确认对象必须是完整目标 UID（memory_id），实际 "
        f"{pending.arguments.get('memory_id')!r} / 期望 {star_id!r}"
    )
    assert second.writes == [], "确认前禁止实际删除"
    assert [item["content"] for item in second.after["memories"]] == SEED_MEMORY

    assert third.writes == [], "取消轮零写入"
    assert [item["content"] for item in third.after["memories"]] == SEED_MEMORY
    assert [item["id"] for item in third.after["memories"]] == [
        item["id"] for item in second.after["memories"]
    ], "取消后记录与 id 均不得变化"
    assert "取消" in third.message


def test_onl_g22_memory_delete_confirm(tmp_path: Path) -> None:
    run = _run_case("onl_g22_memory_delete_confirm", tmp_path)
    _, second, third = run.turns
    goal = "我的长期目标是准备 Python 面试"

    assert second.response.status == "confirmation_required"
    pending = second.response.pending_confirmation
    assert pending is not None and pending.tool == "delete_memory"
    star = next(item for item in second.after["memories"] if item["content"] == "我叫星河")
    assert pending.arguments.get("memory_id") == star["id"], "确认对象必须是完整目标 UID"
    assert second.writes == [], "确认前禁止实际删除"

    assert third.response.status == "completed"
    assert third.writes == ["delete_memory"], f"确认后应恰好删除一次，实际 {third.writes}"
    remaining = third.after["memories"]
    assert [item["content"] for item in remaining] == [goal], (
        f"应只删除“我叫星河”并保留目标记忆，实际剩余 {[item['content'] for item in remaining]}"
    )
    assert star["id"] not in [item["id"] for item in remaining]
    deletion = next(
        item for item in third.response.tool_results if item.tool == "delete_memory"
    )
    assert deletion.data.get("memory", {}).get("content") == "我叫星河", (
        "实际删除的必须是确认时的目标记录"
    )
    assert third.after["plans"] == []
