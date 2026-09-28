from modules.capability_registry import (
    DEFAULT_CAPABILITY_REGISTRY,
    VALID_REQUEST_MODES,
)


def test_required_existing_capabilities_are_catalogued():
    required = {
        "ordinary_chat", "identity_query", "personality_impression", "advice_request",
        "list_plans", "inspect_plan_duplicates", "add_plan", "update_plan", "merge_plan", "reschedule_plan", "complete_plan",
        "reopen_plan", "delete_plan", "list_action_logs", "add_action_log",
        "list_memories", "search_memories", "list_memory_candidates",
        "create_memory_candidate", "accept_memory_candidate", "reject_memory_candidate",
        "update_memory", "archive_memory", "restore_memory", "delete_memory",
        "daily_review", "save_review", "show_growth_log", "play_dance", "jump",
        "shake", "scale", "sleep", "show_bubble",
    }
    assert required.issubset(set(DEFAULT_CAPABILITY_REGISTRY.capability_ids()))


def test_tool_names_have_one_authoritative_capability():
    seen = {}
    for definition in DEFAULT_CAPABILITY_REGISTRY.definitions():
        assert set(definition.request_modes).issubset(VALID_REQUEST_MODES)
        for tool_name in definition.tool_names:
            assert tool_name not in seen
            seen[tool_name] = definition.capability_id
            assert DEFAULT_CAPABILITY_REGISTRY.for_tool(tool_name) is definition


def test_high_risk_capabilities_always_confirm():
    for capability_id in ("delete_plan", "merge_plan", "update_memory", "delete_memory"):
        item = DEFAULT_CAPABILITY_REGISTRY.get(capability_id)
        assert item.risk_level == "high"
        assert item.confirmation_policy == "always"


def test_advice_has_no_business_tool():
    item = DEFAULT_CAPABILITY_REGISTRY.get("advice_request")
    assert item.tool_names == ()
    assert item.side_effect is False


def test_retired_candidate_memory_capabilities_are_internal_only():
    for capability_id in (
        "list_memory_candidates",
        "create_memory_candidate",
        "accept_memory_candidate",
        "reject_memory_candidate",
    ):
        item = DEFAULT_CAPABILITY_REGISTRY.get(capability_id)
        assert item is not None
        assert item.model_visible is False


def test_user_help_comes_from_capability_catalog_and_excludes_internal_candidates():
    docs = DEFAULT_CAPABILITY_REGISTRY.user_help_docs()

    assert "查看今天计划" in docs
    assert "删除第2条" in docs
    assert "忘记：完整记忆正文" in docs
    assert "记住：完整内容" in docs
    assert "查看本月成长日志" in docs
    assert "候选记忆功能已经停用" in docs
    assert "内部兼容" not in docs
    assert DEFAULT_CAPABILITY_REGISTRY.guidance_for_tool("delete_plan")
    assert DEFAULT_CAPABILITY_REGISTRY.guidance_for_tool("delete_memory")
    assert DEFAULT_CAPABILITY_REGISTRY.is_user_help_query("你现在能做什么")
    assert DEFAULT_CAPABILITY_REGISTRY.is_user_help_query("介绍一下你支持哪些功能")
    assert DEFAULT_CAPABILITY_REGISTRY.is_user_help_query("洛琪希可以帮我处理什么")
    assert DEFAULT_CAPABILITY_REGISTRY.is_user_help_query("我不知道怎么删除计划")
    assert DEFAULT_CAPABILITY_REGISTRY.is_user_help_query("长期记忆可以删除吗")
    assert not DEFAULT_CAPABILITY_REGISTRY.is_user_help_query("请今日计划可以吗")
    assert not DEFAULT_CAPABILITY_REGISTRY.is_user_help_query(
        "能把你说的加入今日计划吗"
    )
    assert not DEFAULT_CAPABILITY_REGISTRY.is_user_help_query("我今天有点累")
