import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from modules.contracts import AgentResponse, ToolResult
from modules.response_composer import ResponseComposer


def test_empty_and_none_like_messages_have_public_fallback():
    response = ResponseComposer().compose(AgentResponse("completed", ""))
    assert response.message
    assert response.message not in {"None", "null"}


def test_verified_candidate_id_and_partial_results_are_reported():
    accepted = ToolResult(
        True,
        "accept_memory_candidate",
        "completed",
        {"memory_operation": {"candidate_id": 7}},
        operation_kind="write",
    )
    response = ResponseComposer().compose(
        AgentResponse("completed", "", tool_results=[accepted])
    )
    assert "7" in response.message

    failed = ToolResult(False, "add_action_log", "failed", {}, "write_failed")
    partial = ResponseComposer().compose(
        AgentResponse(
            "partial_success",
            "",
            tool_results=[accepted, failed],
        )
    )
    assert "7" in partial.message
    assert "没有完成" in partial.message


def test_batch_plan_titles_do_not_duplicate_sentence_punctuation():
    results = [
        ToolResult(
            True,
            "add_plan",
            "completed",
            {"task": {"title": title}},
            operation_kind="write",
        )
        for title in ("复习一个概念。", "跑一个最小示例。")
    ]

    response = ResponseComposer().compose(
        AgentResponse("completed", "", tool_results=results)
    )

    assert response.message == "已加入 2 项今日计划：复习一个概念、跑一个最小示例。"


def test_unverified_success_claim_is_blocked():
    # LLM chat path always uses status="chat"; "completed" without
    # ToolResults is a system-only status in production.
    response = ResponseComposer().compose(
        AgentResponse("chat", "已经帮你保存好了。"),
        user_text="帮我保存这个",
    )
    assert "保存好了" not in response.message
    assert "目前还没有执行" not in response.message


def test_natural_unverified_plan_add_claim_is_blocked():
    response = ResponseComposer().compose(
        AgentResponse("chat", "好，我帮你把“简历包装”加入今天计划了。"),
        user_text="加进今日计划",
        verified_turn_context={"execution": {"performed": False}},
    )

    assert "加入今天计划了" not in response.message
    assert "表达不够准确" in response.message


def test_chat_without_tool_result_blocks_common_write_completion_claims():
    composer = ResponseComposer()

    for message in (
        "我记下了。",
        "已经帮你加入了。",
        "好，这项算完成了。",
        "好，我会把这个回答保存到长期记忆里。",
    ):
        response = composer.compose(AgentResponse("chat", message))

        assert response.message != message
        assert not any(
            claim in response.message
            for claim in ("我记下了", "帮你加入了", "这项算完成了")
        )


def test_real_world_completion_acknowledgement_is_not_treated_as_app_success():
    composer = ResponseComposer()
    cases = (
        ("我今天完成了毕业论文答辩", "这件事已经完成了，辛苦了。"),
        ("我终于完成搬家了", "这项大事已经完成了，可以松口气了。"),
        ("我删除那个群聊了", "这件事已经完成了。"),
        ("我完成了公司交给我的任务", "这项任务已经完成了，辛苦了。"),
        ("我把工作任务做完了", "这项工作任务已经完成了，辛苦了。"),
        ("我刚办完入职手续了", "这件事已经完成了，辛苦了。"),
        ("我刚把入职手续办妥了", "这件事已经完成了，辛苦了。"),
        ("我终于把搬家搞定了", "这件事已经完成了，可以松口气了。"),
        ("我们顺利结束了毕业答辩", "这件事已经完成了，辛苦了。"),
    )

    for user_text, message in cases:
        response = composer.compose(
            AgentResponse("chat", message),
            user_text=user_text,
            verified_turn_context={"execution": {"performed": False}},
        )

        assert response.message == message


def test_ambiguous_app_completion_claim_stays_guarded_without_tool_result():
    response = ResponseComposer().compose(
        AgentResponse("chat", "好，这项计划已经完成了。"),
        user_text="帮我把这个计划标记完成",
        verified_turn_context={
            "semantic_intent": "complete_plan",
            "execution": {"performed": False},
            "user_reported_plan_progress": True,
        },
    )

    assert "这项计划已经完成了" not in response.message
    assert "表达不够准确" in response.message

    memory_response = ResponseComposer().compose(
        AgentResponse("chat", "好，这件事已经保存好了。"),
        user_text="记住我喜欢吃西瓜",
        verified_turn_context={"execution": {"performed": False}},
    )

    assert "已经保存好了" not in memory_response.message


def test_chat_without_tool_result_blocks_semantic_plan_change_claim():
    response = ResponseComposer().compose(
        AgentResponse(
            "chat",
            "好的，这轮验收记录按时间顺序整理已经改到晚上，时长保持三十分钟不变。",
        ),
        verified_turn_context={"execution": {"performed": False}},
    )

    assert "已经改到晚上" not in response.message
    assert "表达不够准确" in response.message


def test_chat_without_tool_result_keeps_plan_change_question_and_suggestion():
    composer = ResponseComposer()

    question = composer.compose(
        AgentResponse("chat", "要不要把这项改到晚上？"),
        verified_turn_context={"execution": {"performed": False}},
    )
    suggestion = composer.compose(
        AgentResponse("chat", "如果你愿意，可以把这项改到晚上。"),
        verified_turn_context={"execution": {"performed": False}},
    )

    assert question.message == "要不要把这项改到晚上？"
    assert suggestion.message == "如果你愿意，可以把这项改到晚上。"


def test_chat_goal_language_is_not_rewritten_as_tool_failure():
    response = ResponseComposer().compose(
        AgentResponse(
            "chat",
            "我可以陪你把这个愿望展开，再一起想想怎样逐步完成目标。",
        ),
        user_text="我要成为百万富翁",
    )

    assert "陪你把这个愿望展开" in response.message
    assert "目前还没有执行" not in response.message


def test_unverified_indirect_plan_completion_claim_uses_recorded_state():
    context = {
        "execution": {"performed": False},
        "user_reported_plan_progress": True,
        "recorded_plan_state": {
            "pending_titles": ["机器学习", "睡前半小时放下手机"],
            "completed_titles": ["写小说"],
        },
    }

    response = ResponseComposer().compose(
        AgentResponse("chat", "今天很充实，把计划一项项做完了，不容易。"),
        user_text="这些今日计划看起来都完成了吧",
        verified_turn_context=context,
    )

    assert "把计划一项项做完了" not in response.message
    assert "还没有更新计划记录" in response.message
    assert "机器学习" in response.message
    assert "睡前半小时放下手机" in response.message


def test_truthful_plan_progress_chat_is_not_rewritten():
    message = "听起来你现实中已经做完了不少，不过本地记录还没有同步。要把机器学习标记完成吗？"
    context = {
        "execution": {"performed": False},
        "user_reported_plan_progress": True,
        "recorded_plan_state": {
            "pending_titles": ["机器学习"],
            "completed_titles": [],
        },
    }

    response = ResponseComposer().compose(
        AgentResponse("chat", message),
        verified_turn_context=context,
    )

    assert response.message == message


def test_prior_verified_add_is_not_retracted_by_later_chat_prose():
    context = {
        "execution": {"performed": False},
        "prior_verified_operation": {
            "kind": "plan_added",
            "title": "简历包装",
            "status": "success",
        },
    }
    message = (
        "西瓜确实很适合夏天。\n\n"
        "不过刚才那条我得更正一下：我其实没有真的执行添加操作，"
        "简历包装目前还没有被记录进今日计划。"
    )

    response = ResponseComposer().compose(
        AgentResponse("chat", message),
        user_text="我喜欢吃西瓜",
        verified_turn_context=context,
    )

    assert response.message == "西瓜确实很适合夏天。"


def test_explicit_prior_success_dispute_gets_verified_acknowledgement():
    context = {
        "execution": {"performed": False},
        "prior_verified_operation": {
            "kind": "plan_added",
            "title": "简历包装",
            "status": "success",
        },
    }

    response = ResponseComposer().compose(
        AgentResponse("chat", "对，刚才已经把“简历包装”加入今天计划了。"),
        user_text="我看到已经加入了啊",
        verified_turn_context=context,
    )

    assert response.message == (
        "对，你看到的是对的：刚才已经成功把“简历包装”加入今日计划了。"
        "这轮聊天没有改变它。"
    )


def test_unrelated_real_world_join_does_not_consume_prior_plan_success():
    context = {
        "execution": {"performed": False},
        "prior_verified_operation": {
            "kind": "plan_added",
            "title": "简历包装",
            "status": "success",
        },
    }
    cases = (
        ("我已经加入新的项目群了", "听起来你已经顺利加入新的项目群了。"),
        ("这个已经加入新的项目群了", "明白，这个已经加入新的项目群了。"),
        ("它已经加入新的项目群了", "原来它已经加入新的项目群了。"),
        ("我还没有加入新的项目群", "其实你现在还没有加入新的项目群。"),
        ("我今天完成入职了", "恭喜你已经完成入职。"),
        ("我删除那个群聊了", "你已经删除那个群聊了。"),
    )

    for user_text, message in cases:
        response = ResponseComposer().compose(
            AgentResponse("chat", message),
            user_text=user_text,
            verified_turn_context=context,
        )

        assert response.message == message
        assert "简历包装" not in response.message


def test_same_title_real_world_event_does_not_consume_prior_plan_success():
    cases = (
        (
            {"kind": "plan_added", "title": "项目群", "status": "success"},
            "我今天正式加入项目群了",
            "听起来你今天正式加入项目群了。",
        ),
        (
            {
                "kind": "plan_completed",
                "title": "毕业论文答辩",
                "status": "success",
            },
            "我今天完成了毕业论文答辩",
            "这件事已经完成了，辛苦了。",
        ),
        (
            {"kind": "plan_deleted", "title": "群聊", "status": "success"},
            "我删除那个群聊了",
            "明白，你已经删除那个群聊了。",
        ),
    )

    for prior, user_text, message in cases:
        response = ResponseComposer().compose(
            AgentResponse("chat", message),
            user_text=user_text,
            verified_turn_context={
                "execution": {"performed": False},
                "prior_verified_operation": prior,
            },
        )

        assert response.message == message


def test_same_title_new_plan_command_is_not_rewritten_as_prior_success():
    context = {
        "execution": {"performed": False},
        "prior_verified_operation": {
            "kind": "plan_added",
            "title": "项目群",
            "status": "success",
        },
    }

    for user_text in ("把项目群加入今天计划", "项目群加入今天计划"):
        message = "你是想把项目群加入今天计划吗？"
        response = ResponseComposer().compose(
            AgentResponse("chat", message),
            user_text=user_text,
            verified_turn_context=context,
        )

        assert response.message == message


def test_verified_plan_completion_context_guards_all_deictic_false_success():
    composer = ResponseComposer()

    for message in (
        "这件事已经完成了。",
        "那件事已经完成了。",
        "这个已经完成了。",
        "那个已经完成了。",
        "它已经完成了。",
    ):
        response = composer.compose(
            AgentResponse("chat", message),
            user_text="帮我把这个计划标记完成",
            verified_turn_context={
                "semantic_intent": "complete_plan",
                "execution": {"performed": False},
                "user_reported_plan_progress": True,
                "recorded_plan_state": {"pending_titles": ["项目群"]},
            },
        )

        assert message not in response.message
        assert "表达不够准确" in response.message


def test_later_false_write_is_not_hidden_by_real_world_completion_phrase():
    response = ResponseComposer().compose(
        AgentResponse("chat", "这件事已经完成了，这条也保存了。"),
        user_text="我刚办完入职手续了",
        verified_turn_context={"execution": {"performed": False}},
    )

    assert "保存了" not in response.message
    assert "表达不够准确" in response.message


def test_same_title_real_world_denial_is_not_treated_as_prior_retraction():
    message = "其实你还没有加入项目群，等准备好再说也行。"
    response = ResponseComposer().compose(
        AgentResponse("chat", message),
        user_text="我还没有加入项目群",
        verified_turn_context={
            "execution": {"performed": False},
            "prior_verified_operation": {
                "kind": "plan_added",
                "title": "项目群",
                "status": "success",
            },
        },
    )

    assert response.message == message


def test_same_title_real_world_destination_is_not_prior_plan_reference():
    message = "嗯，我也看到了，它已经进公司通讯录了。"
    response = ResponseComposer().compose(
        AgentResponse("chat", message),
        user_text="我看到项目群已经加入公司通讯录了",
        verified_turn_context={
            "execution": {"performed": False},
            "prior_verified_operation": {
                "kind": "plan_added",
                "title": "项目群",
                "status": "success",
            },
        },
    )

    assert response.message == message


def test_other_plan_denial_does_not_retract_prior_success():
    message = "其实英语阅读还没有加入今日计划。"
    response = ResponseComposer().compose(
        AgentResponse("chat", message),
        user_text="英语阅读还没有加入今日计划",
        verified_turn_context={
            "execution": {"performed": False},
            "prior_verified_operation": {
                "kind": "plan_added",
                "title": "简历包装",
                "status": "success",
            },
        },
    )

    assert response.message == message

    pronoun_message = "实际上它还没加入今日计划。"
    pronoun_response = ResponseComposer().compose(
        AgentResponse("chat", pronoun_message),
        user_text="英语阅读还没有加入今日计划",
        verified_turn_context={
            "execution": {"performed": False},
            "prior_verified_operation": {
                "kind": "plan_added",
                "title": "简历包装",
                "status": "success",
            },
        },
    )
    assert pronoun_response.message == pronoun_message

    question_message = "目前这个还没有加入计划。"
    question_response = ResponseComposer().compose(
        AgentResponse("chat", question_message),
        user_text="摄影群加入了吗",
        verified_turn_context={
            "execution": {"performed": False},
            "prior_verified_operation": {
                "kind": "plan_added",
                "title": "简历包装",
                "status": "success",
            },
        },
    )
    assert question_response.message == question_message


def test_pronominal_prior_retractions_are_removed_without_title_leakage():
    context = {
        "execution": {"performed": False},
        "prior_verified_operation": {
            "kind": "plan_added",
            "title": "简历包装",
            "status": "success",
        },
    }
    messages = (
        "抱歉，我之前其实没把它加进今天计划。",
        "刚才的添加没有生效，计划里其实没有它。",
        "刚才没有加进去，计划里还是空的。",
        "实际上它还没加进今日计划。",
    )

    for message in messages:
        response = ResponseComposer().compose(
            AgentResponse("chat", message),
            user_text="我们继续聊别的",
            verified_turn_context=context,
        )

        assert "简历包装" in response.message
        assert "成功" in response.message
        assert "没有生效" not in response.message
        assert "还没加进" not in response.message


def test_user_correction_away_from_plan_scope_does_not_consume_prior_success():
    context = {
        "execution": {"performed": False},
        "prior_verified_operation": {
            "kind": "plan_added",
            "title": "项目群",
            "status": "success",
        },
    }
    cases = (
        (
            "我不是说把项目群加入今日计划，我是说我加入项目群了",
            "明白，你说的是现实中加入了项目群。",
        ),
        (
            "我明明说的是加入项目群，不是加入今日计划",
            "明白，你纠正的是现实事件，不是今日计划。",
        ),
    )

    for user_text, message in cases:
        response = ResponseComposer().compose(
            AgentResponse("chat", message),
            user_text=user_text,
            verified_turn_context=context,
        )

        assert response.message == message


def test_current_turn_failure_can_preserve_prior_success_explicitly():
    message = "实际上这次没有加入摄影群，之前那个计划还在。"
    response = ResponseComposer().compose(
        AgentResponse("chat", message),
        user_text="这次摄影群没有加入",
        verified_turn_context={
            "execution": {"performed": False},
            "prior_verified_operation": {
                "kind": "plan_added",
                "title": "简历包装",
                "status": "success",
            },
        },
    )

    assert response.message == message


def test_explicit_same_title_prior_state_check_uses_verified_success():
    expected = (
        "对，你看到的是对的：刚才已经成功把“项目群”加入今日计划了。"
        "这轮聊天没有改变它。"
    )
    context = {
        "execution": {"performed": False},
        "prior_verified_operation": {
            "kind": "plan_added",
            "title": "项目群",
            "status": "success",
        },
    }

    for user_text in (
        "刚才不是已经把项目群加入今日计划了吗",
        "项目群已经显示在今日计划里了吧",
        "我看到项目群已经加入了啊",
    ):
        response = ResponseComposer().compose(
            AgentResponse("chat", "让我再看看。"),
            user_text=user_text,
            verified_turn_context=context,
        )

        assert response.message == expected


def test_other_or_external_plan_state_does_not_consume_prior_success():
    cases = (
        (
            {"kind": "plan_added", "title": "简历包装", "status": "success"},
            "我看到英语学习计划已经加入了",
        ),
        (
            {"kind": "plan_added", "title": "项目群", "status": "success"},
            "我看到项目群已经加入公司的培训计划了",
        ),
    )

    for prior, user_text in cases:
        message = "我明白你说的是另一件事。"
        response = ResponseComposer().compose(
            AgentResponse("chat", message),
            user_text=user_text,
            verified_turn_context={
                "execution": {"performed": False},
                "prior_verified_operation": prior,
            },
        )

        assert response.message == message


def test_user_reported_ui_mismatch_returns_honest_verification_boundary():
    context = {
        "execution": {"performed": False},
        "prior_verified_operation": {
            "kind": "plan_added",
            "title": "项目群",
            "status": "success",
        },
    }

    for user_text in (
        "我看到项目群其实没有加入今日计划",
        "我看到这个计划未加入",
    ):
        response = ResponseComposer().compose(
            AgentResponse("chat", "你说得对，它目前没有加入。"),
            user_text=user_text,
            verified_turn_context=context,
        )

        assert "刚才系统返回“项目群”处理成功" in response.message
        assert "不能断言当前状态" in response.message
        assert "查看今日计划" in response.message

    completed = ResponseComposer().compose(
        AgentResponse("chat", "抱歉，它仍是待完成。"),
        user_text="我看到简历包装还是待完成的",
        verified_turn_context={
            "execution": {"performed": False},
            "prior_verified_operation": {
                "kind": "plan_completed",
                "title": "简历包装",
                "status": "success",
            },
        },
    )
    assert "刚才系统返回“简历包装”处理成功" in completed.message
    assert "不能断言当前状态" in completed.message


def test_direct_question_about_prior_operation_uses_verified_success():
    response = ResponseComposer().compose(
        AgentResponse("chat", "我再确认一下。"),
        user_text="你刚刚是不是把项目群加入计划了？",
        verified_turn_context={
            "execution": {"performed": False},
            "prior_verified_operation": {
                "kind": "plan_added",
                "title": "项目群",
                "status": "success",
            },
        },
    )

    assert "项目群" in response.message
    assert "成功" in response.message

    external_message = "你问的是公司的培训计划，我不能用今日计划状态代替回答。"
    external = ResponseComposer().compose(
        AgentResponse("chat", external_message),
        user_text="你刚刚是不是把项目群加入公司的培训计划了？",
        verified_turn_context={
            "execution": {"performed": False},
            "prior_verified_operation": {
                "kind": "plan_added",
                "title": "项目群",
                "status": "success",
            },
        },
    )
    assert external.message == external_message


def test_prior_denial_scope_handles_questions_double_negation_and_true_denial():
    context = {
        "execution": {"performed": False},
        "prior_verified_operation": {
            "kind": "plan_added",
            "title": "项目群",
            "status": "success",
        },
    }
    preserved = (
        "我刚才说错的是时间，不是说项目群没有加入今日计划。",
        "项目群目前还没有加入今日计划吗？我可以先帮你查看。",
        "目前没有必要重新加入今日计划，因为项目群已经在里面。",
    )

    for message in preserved:
        response = ResponseComposer().compose(
            AgentResponse("chat", message),
            user_text="继续说",
            verified_turn_context=context,
        )
        assert response.message == message

    denied = ResponseComposer().compose(
        AgentResponse("chat", "刚才项目群并未加入今日计划。"),
        user_text="继续说",
        verified_turn_context=context,
    )
    assert "成功" in denied.message
    assert "并未加入" not in denied.message


def test_prior_status_synonym_denials_are_grounded_by_verified_success():
    cases = (
        (
            {"kind": "plan_added", "title": "项目群", "status": "success"},
            "项目群其实不在今日计划里。",
        ),
        (
            {"kind": "plan_added", "title": "项目群", "status": "success"},
            "项目群没有出现在今日计划中。",
        ),
        (
            {
                "kind": "plan_completed",
                "title": "简历包装",
                "status": "success",
            },
            "简历包装其实还是待完成的。",
        ),
        (
            {"kind": "plan_deleted", "title": "群聊计划", "status": "success"},
            "群聊计划其实还在。",
        ),
    )

    for prior, message in cases:
        response = ResponseComposer().compose(
            AgentResponse("chat", message),
            user_text="继续说",
            verified_turn_context={
                "execution": {"performed": False},
                "prior_verified_operation": prior,
            },
        )
        assert "成功" in response.message
        assert message != response.message


def test_empty_plan_claim_requires_a_real_successful_query():
    composer = ResponseComposer()
    blocked = composer.compose(
        AgentResponse("chat", "今天还没有计划。"),
        user_text="我今天有计划吗",
    )
    verified = composer.compose(
        AgentResponse(
            "completed",
            "今天还没有计划。",
            tool_results=[
                ToolResult(
                    True,
                    "show_plan",
                    "listed",
                    {"tasks": []},
                    operation_kind="read",
                )
            ],
        )
    )
    wrong_tool = composer.compose(
        AgentResponse(
            "completed",
            "计划已经添加。",
            tool_results=[
                ToolResult(
                    True,
                    "show_plan",
                    "listed",
                    {"tasks": []},
                    operation_kind="read",
                )
            ],
        )
    )
    assert "读取实际记录" in blocked.message
    assert verified.message == "今天还没有计划。"
    assert "已经添加" not in wrong_tool.message


def test_successful_show_plan_result_never_falls_back_to_unexecuted_action_text():
    response = ResponseComposer().compose(
        AgentResponse(
            "completed",
            "completed",
            tool_results=[
                ToolResult(
                    True,
                    "show_plan",
                    "listed",
                    {"tasks": [{"id": 1, "title": "复习随机森林", "done": False}]},
                    operation_kind="read",
                )
            ],
        ),
        user_text="今天还有什么计划",
    )

    assert "复习随机森林" in response.message
    assert "目前还没有执行这项操作" not in response.message


def test_daily_review_prefers_verified_natural_summary_when_available():
    response = ResponseComposer().compose(
        AgentResponse(
            "completed",
            "generated",
            tool_results=[
                ToolResult(
                    True,
                    "generate_daily_review",
                    "generated",
                    {
                        "review": {
                            "text": "数据库式回退文本",
                            "natural_summary": "（轻轻点头）今天确实推进了一些事情。",
                        }
                    },
                    operation_kind="read",
                )
            ],
        ),
        user_text="我今天都完成了什么",
    )
    assert response.message == "（轻轻点头）今天确实推进了一些事情。"


def test_conversation_reads_render_verified_tool_data():
    composer = ResponseComposer()
    recent = composer.compose(
        AgentResponse(
            "completed",
            "listed",
            tool_results=[
                ToolResult(
                    True,
                    "show_recent_conversation",
                    "listed",
                    {"messages": [{"role": "user", "content": "测试会话恢复"}]},
                    operation_kind="read",
                )
            ],
        )
    )
    history = composer.compose(
        AgentResponse(
            "completed",
            "listed",
            tool_results=[
                ToolResult(
                    True,
                    "show_conversation_history",
                    "listed",
                    {"sessions": [{"title": "本地会话", "message_count": 4}]},
                    operation_kind="read",
                )
            ],
        )
    )

    assert "测试会话恢复" in recent.message
    assert "本地会话" in history.message


if __name__ == "__main__":
    test_empty_and_none_like_messages_have_public_fallback()
    test_verified_candidate_id_and_partial_results_are_reported()
    test_unverified_success_claim_is_blocked()
    test_empty_plan_claim_requires_a_real_successful_query()
    print("response composer tests passed")
