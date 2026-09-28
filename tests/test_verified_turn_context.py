from modules.verified_turn_context import VerifiedTurnContext


def test_plan_progress_context_separates_user_report_from_recorded_state():
    context = VerifiedTurnContext.for_chat_reply(
        {
            "intent": "chat",
            "semantic_decision": {"mode": "chat", "intent": "chat"},
        },
        operation_cues=["完成"],
        domain_cues=["plan"],
        plans=[
            {"id": 1, "uid": "private-1", "title": "写小说", "done": True},
            {"id": 2, "uid": "private-2", "title": "机器学习", "done": False},
        ],
    )

    facts = context.to_dict()
    model_text = context.to_model_text()
    diagnostic = context.diagnostic_summary()

    assert facts["execution"]["performed"] is False
    assert facts["user_reported_plan_progress"] is True
    assert facts["recorded_plan_state"] == {
        "pending_titles": ["机器学习"],
        "completed_titles": ["写小说"],
    }
    assert "private-1" not in model_text
    assert "private-2" not in model_text
    assert "机器学习" in model_text
    assert diagnostic["pending_plan_count"] == 1
    assert "pending_titles" not in diagnostic


def test_ordinary_chat_does_not_expose_unrelated_plan_titles():
    context = VerifiedTurnContext.for_chat_reply(
        {"intent": "chat"},
        operation_cues=[],
        domain_cues=[],
        plans=[{"id": 1, "title": "私人计划", "done": False}],
    )

    assert context.user_reported_plan_progress is False
    assert context.pending_plan_titles == []
    assert "私人计划" not in context.to_model_text()


def test_prior_verified_operation_is_redacted_and_not_invalidated_by_current_chat():
    context = VerifiedTurnContext.for_chat_reply(
        {"intent": "chat"},
        prior_tool_result={"tool": "add_plan", "status": "success", "tool_call_id": "secret-call"},
        prior_task={
            "id": 7,
            "uid": "private-stable-id",
            "title": "简历包装",
            "done": False,
            "status": "pending",
        },
    )

    facts = context.to_dict()
    model_text = context.to_model_text()

    assert facts["execution"]["performed"] is False
    assert facts["prior_verified_operation"] == {
        "kind": "plan_added",
        "title": "简历包装",
        "status": "success",
    }
    assert "private-stable-id" not in model_text
    assert "secret-call" not in model_text
    assert "不会推翻" in model_text
