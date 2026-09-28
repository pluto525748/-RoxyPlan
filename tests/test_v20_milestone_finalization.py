from pathlib import Path

from modules.persona_registry import PersonaRegistry
from server.main import create_app
from tests.v18_test_support import RecordingLLM, build_service


ROOT = Path(__file__).resolve().parents[1]


def test_roxy_pack_has_nonempty_runtime_material_in_every_required_module():
    pack = PersonaRegistry(ROOT / "data" / "personas").current_pack()

    assert pack.identity and pack.behavior and pack.speaking_style
    assert pack.truth_and_boundaries and pack.relationships and pack.worldview
    assert pack.dialogue_examples and pack.evaluation_cases and pack.assets
    assert "AI 角色" in pack.truth_and_boundaries
    assert any(item.get("scene") == "user_tired" for item in pack.dialogue_examples)


def test_chat_cannot_switch_persona_and_web_exposes_no_mutating_persona_route(tmp_path):
    service, _growth, _memory, _history = build_service(tmp_path, RecordingLLM())
    before = service.persona_registry.active_persona_id

    response = service.handle("换成鸣人", "persona-chat")
    routes = {route.path for route in create_app(agent_service=service).routes}

    assert response.status == "chat"
    assert service.persona_registry.active_persona_id == before == "roxy"
    assert "/v1/personas/{persona_id}/select" not in routes


def test_persona_and_context_diagnostics_are_opt_in_and_sanitized(tmp_path):
    service, _growth, _memory, _history = build_service(tmp_path, RecordingLLM())
    service.conversation_service.interaction_diagnostics.enabled = True

    response = service.handle("我今天很累", "persona-diagnostics")
    record = service.diagnostic_snapshot()[-1]

    assert response.status == "chat"
    assert record["persona_context"]["active_persona_id"] == "roxy"
    assert record["persona_context"]["persona_version"] == "1.0.0"
    assert record["persona_context"]["fixed_core_characters"] > 0
    assert "lore_items" in record["persona_context"]
    assert "relevant_summary_items" in record["persona_context"]
    assert any(item["name"] == "persona_fixed_core" for item in record["context_sections"])
    assert "我今天很累" not in str(record)
