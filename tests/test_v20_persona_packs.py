import shutil
from pathlib import Path

from modules.context_builder import ContextBuilder
from modules.persona_registry import PersonaRegistry
from server.agent_service import AgentService
from tests.v18_test_support import RecordingLLM, build_service


ROOT = Path(__file__).resolve().parents[1]
PERSONAS_ROOT = ROOT / "data" / "personas"


def test_roxy_pack_scans_and_has_a_bounded_fixed_core():
    registry = PersonaRegistry(PERSONAS_ROOT)

    assert registry.available_personas()[0]["persona_id"] == "roxy"
    pack = registry.current_pack()
    assert "[Identity]" in pack.fixed_core()
    assert "成长观" not in pack.fixed_core()
    assert pack.manifest["schema_version"] == "1.0"
    assert pack.manifest["provenance"]["contains_original_text"] is False


def test_missing_pack_file_is_safely_reported(tmp_path):
    broken_root = tmp_path / "personas"
    shutil.copytree(PERSONAS_ROOT / "roxy", broken_root / "broken")
    (broken_root / "broken" / "identity.md").unlink()

    registry = PersonaRegistry(broken_root)

    assert registry.available_personas() == []
    assert "broken" in registry.errors()
    result = registry.select("not-installed")
    assert result.success is False
    assert result.error_code == "fallback_persona_unavailable"


def test_invalid_persona_falls_back_to_roxy_with_an_explicit_result():
    registry = PersonaRegistry(PERSONAS_ROOT)

    result = registry.select("unknown-persona")

    assert result.success is False
    assert result.fallback_used is True
    assert result.active_persona_id == "roxy"
    assert result.error_code == "persona_not_found"


def test_lore_and_examples_are_retrieved_only_when_relevant():
    pack = PersonaRegistry(PERSONAS_ROOT).current_pack()

    neutral = pack.retrieve_context("嗯")
    tired = pack.retrieve_context("我今天很累，不想学了")

    assert neutral.lore == ""
    assert neutral.dialogue_examples == ""
    assert tired.dialogue_example_items == 1
    assert "user_tired" in tired.dialogue_examples
    assert "成长观" not in pack.fixed_core()


def test_context_builder_orders_sections_and_trims_low_priority_material():
    builder = ContextBuilder(character_budget=1200)
    result = builder.build(
        personality_context="",
        persona_context="persona core",
        memory_context="memory" * 120,
        session_summary="summary",
        recent_messages=[{"role": "assistant", "content": "recent"}],
        knowledge_context="knowledge" * 400,
        current_user_input="current message",
        instruction="safety",
        user_goals_context="goal",
        today_pending_context="pending",
        today_completed_context="completed" * 80,
        persona_lore_context="lore" * 120,
        persona_examples_context="examples" * 100,
    )
    system = [item["content"] for item in result if item["role"] == "system"]
    diagnostics = {item.name: item for item in builder.last_diagnostics}

    assert system[:4] == ["safety", "persona core", "goal", "pending"]
    assert diagnostics["relevant_knowledge"].trimmed is True
    assert diagnostics["current_user_message"].trimmed is False
    assert result[-1] == {"role": "user", "content": "current message"}


def test_web_agent_context_uses_the_same_roxy_pack_without_copying_user_data(tmp_path):
    service, growth, memory, _history = build_service(tmp_path, RecordingLLM())
    memory.add_memory("我的长期目标是成为 AI 工程师", category="goal")
    growth.add_task("复习随机森林")

    before_memories = [item["id"] for item in memory.memories()]
    before_plans = [item["uid"] for item in growth.tasks()]
    messages = service._build_chat_messages("我今天很累", "persona-web")
    switch = service.persona_registry.select("roxy")

    combined = "\n".join(item["content"] for item in messages)
    assert switch.success is True
    assert service.persona_registry.current_pack().persona_id == "roxy"
    assert "[Persona: 洛琪希 (roxy)]" in combined
    assert "重要长期目标" in combined
    assert "今日未完成计划" in combined
    assert [item["id"] for item in memory.memories()] == before_memories
    assert [item["uid"] for item in growth.tasks()] == before_plans


def test_web_service_exposes_registry_switching_contract(tmp_path):
    service, _growth, _memory, _history = build_service(tmp_path, RecordingLLM())

    assert service.available_personas()[0]["persona_id"] == "roxy"
    result = service.persona_registry.select("invalid")
    assert result.success is False
    assert result.active_persona_id == "roxy"
    assert result.error_code == "persona_not_found"
