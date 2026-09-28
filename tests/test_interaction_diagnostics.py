import json
import sys
import tempfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
TESTS = Path(__file__).resolve().parent
for path in (ROOT, TESTS):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

from v18_test_support import NoCallLLM, build_service
from modules.interaction_diagnostics import InteractionDiagnostics


def test_disabled_diagnostics_ignore_persist_path_and_do_not_write(tmp_path):
    persistent = tmp_path / "logs" / "interaction_diagnostics.jsonl"
    diagnostics = InteractionDiagnostics(
        enabled=False,
        persist_path=persistent,
    )

    diagnostics.begin("disabled", "不应记录的消息")
    diagnostics.update("disabled", route_source="fixed_command")
    finalized = diagnostics.finalize(
        "disabled",
        request_id="request-disabled",
        status="completed",
    )

    assert diagnostics.enabled is False
    assert finalized is None
    assert diagnostics.snapshot() == []
    assert persistent.exists() is False
    assert diagnostics.export(tmp_path / "disabled-export.json") is False


def test_pipeline_fields_are_kept_in_initial_and_persistent_record(tmp_path):
    persistent = tmp_path / "logs" / "interaction_diagnostics.jsonl"
    diagnostics = InteractionDiagnostics(
        enabled=True,
        persist_path=persistent,
    )
    expected_pipeline = {
        "validation_status": "valid",
        "repair_attempted": False,
    }

    diagnostics.begin("pipeline", "只保存脱敏流水线事实")
    diagnostics.update(
        "pipeline",
        pipeline_diagnostics=expected_pipeline,
        pipeline_outcome="accepted",
    )
    finalized = diagnostics.finalize(
        "pipeline",
        request_id="request-pipeline",
        status="completed",
    )

    assert finalized is not None
    assert finalized["pipeline_diagnostics"] == expected_pipeline
    assert finalized["pipeline_outcome"] == "accepted"
    persisted = json.loads(persistent.read_text(encoding="utf-8"))
    assert persisted["pipeline_diagnostics"] == expected_pipeline
    assert persisted["pipeline_outcome"] == "accepted"


def test_persistent_diagnostics_are_sanitized_and_exportable():
    with tempfile.TemporaryDirectory() as temp:
        service, _growth, _memory, _history = build_service(Path(temp), NoCallLLM())
        service.handle("今日计划", "diag-off")
        records = service.diagnostic_snapshot()
        assert len(records) == 1
        assert records[0]["route_source"] == "fixed_command"
        assert records[0]["tool_results"][0]["tool"] == "show_plan"
        serialized = json.dumps(records, ensure_ascii=False)
        assert "今日计划" not in serialized
        assert "normalized_text" in records[0]
        assert set(records[0]["normalized_text"]) == {"length", "sha256"}
        persistent = Path(temp) / "logs" / "interaction_diagnostics.jsonl"
        assert persistent.exists()
        assert "今日计划" not in persistent.read_text(encoding="utf-8")

        destination = Path(temp) / "diagnostics.json"
        assert service.export_diagnostics(destination) is True
        assert destination.exists()


def test_diagnostics_identify_interaction_control_instead_of_fresh_turn(tmp_path):
    service, _growth, _memory, _history = build_service(tmp_path, NoCallLLM())
    conversation_id = "diag-control"
    service.interaction_coordinator.awaiting_clarification(
        conversation_id,
        "missing_slots",
        missing_fields=["duration_minutes"],
    )

    response = service.handle("取消", conversation_id)
    record = service.diagnostic_snapshot()[-1]

    assert response.status == "completed"
    assert record["interaction_state_before"] == "awaiting_clarification"
    assert record["coordinator_decision"] == "control:cancel"
    assert record["interaction_state_after"] == "cancelled"


if __name__ == "__main__":
    test_persistent_diagnostics_are_sanitized_and_exportable()
    print("interaction diagnostics tests passed")
