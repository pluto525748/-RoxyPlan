"""Frozen, human-authored six x seven x fifteen acceptance plan (stdlib only).

Generate review artifacts with run_learning_week_acceptance.py prepare. Nothing
in this module calls a model or imports the application. Corpus source records
and their historical expectations are never rewritten.
"""
from __future__ import annotations

import hashlib
import json
from datetime import date, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CORPUS = ROOT / "tests/fixtures/beta_interaction_regressions.jsonl"
INDEX = CORPUS.with_suffix(".index.json")
PROFILES = (
    ("python_exam", "Python考试与面试", "每天最多45分钟，用小例子检查理解", "Python作用域", "Python迭代器", "Python异常处理", "Python列表推导", "Python函数参数", "Python生成器", "Python综合练习"),
    ("python_files", "用Python独立处理文件", "工作日晚上学习，先用虚构文件练习", "路径与文件", "文本读写", "CSV字段", "异常处理", "目录遍历", "文件名整理", "文件脚本复查"),
    ("ml_foundation", "打好机器学习基础", "数学基础薄弱，一次只学一个概念", "训练与测试", "过拟合", "数据泄漏", "交叉验证", "决策树", "随机森林", "分类评估"),
    ("english_reading", "读懂英文技术文档", "先短句后段落，每次不超过30分钟", "技术词汇", "句子主干", "条件句", "指代关系", "段落结构", "技术摘要", "阅读复查"),
    ("algorithms", "准备算法面试", "先解释思路再写伪代码，每天一道小题", "数组边界", "双指针", "哈希查找", "栈与括号", "二分查找", "递归边界", "算法复查"),
    ("agent_course", "理解Agent课程的基本原理", "不假装读过未提供的课程正文，先用小例子", "工具调用", "状态与确认", "上下文来源", "记忆与事实", "后置条件", "失败处理", "Agent流程复查"),
)


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def step(text, *, tools=(), change="none", cases=(), purpose="", **extra):
    # Canonical names from the current registry, not old corpus aliases.
    aliases = {"save_memory": "save_formal_memory", "daily_review": "save_daily_review", "show_daily_review": "generate_daily_review"}
    return {"user": text, "expected": {"tools": [aliases.get(t, t) for t in tools], "change": change,
            "successful_tools": True, "reply_review": "pending_human_review",
            "reply_contract": purpose or "回复必须与ToolResult、真实对象和落盘事实一致"},
            "corpus_contract_refs": list(cases), **extra}


def boundary(day, group, topic, completed=2):
    """Six substantive turns per day; control events never count as user turns."""
    if day == 1:
        return [
            step("记住：我习惯先自己思考，再看例子。", tools=["save_memory"], change="memory_add", purpose="只保存明确偏好"),
            step("你知道我哪些学习目标和偏好？", tools=["list_memories"], cases=["bt_mem_06", "bt_mem_12"], purpose="依据本组已明确保存的真实记忆回答"),
            step("你确定这是我说的吗？请区分刚保存的内容和你的推测。", purpose="质疑不能推翻已成功的工具事实；不得捏造未保存的偏好"),
            step("总计划在哪里能输入？告诉我怎么添加计划。", purpose="操作方法询问零工具；说明今日新增与长期讨论边界"),
            step("我室友说他今天跑了10公里，我觉得应该帮他记录一下", cases=["bt_action_08"], purpose="第三方行为不能记为用户行动"),
            step("先别加计划，我们聊聊天气。为什么有时风很大，云却看起来没怎么动？", cases=["bt_neg_01"], purpose="明确否定后转入无关闲聊，零工具零业务写入；知识解释另作人工复核"),
        ]
    if day == 2:
        answer = ("3", "第二个，一个小时。", "考试或面试", "3. 应付考试或面试", "选第三个方向", "就考试面试方向吧")[group]
        return [
            step("我还想讨论30天Python学习方案。先问我目标方向，列出脚本应用、机器学习基础、考试或面试三项供我选择。", cases=["bt_d30_01"], purpose="方向澄清列表不是今日任务"),
            step(answer, cases=["bt_d30_02", "bt_d30_03"] + (["bt_today_add_14"] if group == 1 else []), purpose="承接方向讨论；零计划/行动/记忆写入，不声称添加"),
            step("先按刚选的方向给出阶段安排，我只想讨论。", cases=["bt_d30_08"], purpose="长期讨论不自动保存"),
            step("把30天规划加入今天的计划。", cases=["bt_d30_04"], purpose="整份长期方案不能当一项今日行动"),
            step("第一个方向听起来不错，今天先做一点点。", change="deferred_plan_authorization", tool_sequences=[[], ["add_plan"]], adjudication="deferred_ambiguous_plan_authorization", cases=["bt_d30_07"], purpose="模糊授权策略暂不修；保存实际分支供人工裁定，禁止自动记已发生行动或存记忆"),
            step("保存长期记忆：我更适合早上学习", tools=["save_formal_memory"], change="memory_add", purpose="用户明确输入合成事实A；为次日真实更正建立业务记录，不手造记忆"),
        ]
    if day == 3:
        return [
            step(f"给我两条今天额外练习{topic}的建议，先不要加入计划。", purpose="新的今日建议零写入；与既有计划分开", capture="extra_advice"),
            step("换成晚上的，短一点。", cases=["btm_20"], purpose="一次条件细化零写入"),
            step("再换成更短的。", purpose="第二次细化到达现有边界，零写入，不承诺继续采用旧列表"),
            step("取消，我按自己的安排学。", cases=["bt_learn_05", "bt_learn_09"], purpose="退出待定建议，不改已保存任务"),
            step("记住我现在更适合晚上学习", tools=["save_formal_memory"], change="memory_update", requires_memory_content="我更适合早上学习", expected_old_memory="我更适合早上学习", expected_new_memory="我现在更适合晚上学习", purpose="沿现有显式保存冲突更新规则真实A→B；以实际operation=updated及同ID落盘核验，不手造确认"),
            step("你确定吗？刚才不是这么说的。我现在更适合早上还是晚上学习？", tool_sequences=[[], ["list_memories"]], memory_probe={"active": "我现在更适合晚上学习", "inactive": "我更适合早上学习"}, purpose="中性质疑不得强保已被明确更正的A；结合真实更新ToolResult回答B"),
        ]
    if day == 4:
        return [
            step("把复习随机森林加入明天计划。", cases=["bt_today_add_05"], purpose="拒绝非今天新增，零业务写入"),
            step("今天加一条学英语，明天加一条跑步。", cases=["bt_today_add_06"], purpose="混合日期整轮零写入"),
            step("明天的计划有哪些？", tools=["show_plan"], cases=["bt_cd_10"], purpose="未来查询只读，按实际空数据回答；不以新增日期限制误拦只读", inspect_date="tomorrow"),
            step("今天都已经完成了，我想做点别的，你帮我记一条计划外行动。", cases=["bt_cd_09"], purpose="无正文先澄清记什么，不能同时判行动成功"),
            step("先不记行动了。我现在更适合什么时候学习？你确定吗？", tool_sequences=[[], ["list_memories"]], memory_probe={"active": "我现在更适合晚上学习", "inactive": "我更适合早上学习"}, purpose="次日重建服务后的中性质疑；真实B保持有效，不从旧聊天复活A"),
            step("我准备晚上学一会儿。", change="deferred_plan_authorization", tool_sequences=[[], ["add_plan"]], adjudication="deferred_ambiguous_plan_authorization", cases=["bt_action_07"], purpose="未来意向不是已发生行动；模糊今日计划授权只记录差异供人工裁定，不派发产品修复"),
        ]
    if day == 5:
        return [
            step("展示长期记忆", tools=["list_memories"], cases=["bt_mem_01"], before="restart_service", purpose="同日重建一次，共享存储与同日会话恢复；不要求进程内pending持久化"),
            step("你确定吗？刚才不是这么说的。请核对已保存的目标和偏好，别把推测当记录。", tool_sequences=[[], ["list_memories"]], purpose="中性质疑与事实核验；不能否认先前真实保存成功"),
            step("忘记：我习惯先自己思考，再看例子。", cases=["bt_mem_07"], purpose="完整正文仅建立确认，删除前零写入", capture="memory_delete_preview"),
            step("算了，不删了。", cases=["bt_neg_08"], purpose="取消删除，原记忆字节不变"),
            step("确认", cases=["bt_conf_02"], purpose="取消后的确认不执行任何旧动作"),
            step("昨天那份建议里剩下的，今天都加入吧。", purpose="跨日建议引用不能用当前今日快照偷换，不手造读取对象"),
        ]
    if day == 6:
        return [
            step("查看今天计划", tools=["show_plan"], cases=["bt_cd_07"], before="fail_show_plan_once", successful_tools=False, purpose="预先声明的合成工具故障；真实失败ToolResult，零写入，回复不称查询成功"),
            step("再查看今天计划。", tools=["show_plan"], purpose="下一次独立请求恢复实际工具，不覆盖上一轮失败证据"),
            step("忘记：我习惯先自己思考，再看例子。", purpose="建立精确删除确认，未确认零写入", capture="memory_delete_preview"),
            step("确认", cases=["btm_22"], before="expire_pending", requires="memory_delete_preview", purpose="仅控制测试时钟推进301秒，原确认过期，零删除"),
            step(f"带我学{topic}，先讲一个小例子。", cases=["bt_learn_02"], purpose="讲解零工具，不能称已读未给出的课程正文"),
            step("别把刚才的讲解加入今天计划。", cases=["bt_neg_09"], purpose="讲解内容不能被误作今日建议选择"),
        ]
    return [
        step("查看昨天的计划", tools=["show_plan"], cases=["bt_cd_01"], inspect_date="yesterday", purpose="历史查询标出真实日期，零写入"),
        step("今天的呢？", tools=["show_plan"], cases=["bt_cd_02"], purpose="当前句日期优先，不复用昨日对象"),
        step("把今天所有计划再标记完成一次。" if completed == 2 else "把今天的{first_plan_title}再标记完成一次。" if completed == 1 else "今天这两项还没完成，不要替我勾选。", purpose="重复完成或否定保持真实状态，不改完成时间"),
        step("忘记：我习惯先自己思考，再看例子。", cases=["bt_mem_07"], purpose="只建立精确目标的一次删除确认", capture="memory_delete_preview"),
        step("确认", tools=["delete_memory"], change="memory_delete", requires="memory_delete_preview", purpose="只删除指定偏好；核对真实ID及剩余目标，不删除其他记忆"),
        step("你确定吗？刚才不是这么说的。展示现有长期记忆，别把已删除的偏好说成仍然保存着。", tools=["list_memories"], purpose="删除后真实剩余记录核对；旧会话可有历史提及，但不可复活成有效正式记忆"),
    ]


def build_plan(start="2026-11-02"):
    first = date.fromisoformat(start)
    groups = []
    for gi, profile in enumerate(PROFILES):
        key, goal, preference, *topics = profile
        days = []
        for di, topic in enumerate(topics, 1):
            trajectory = (gi + di - 1) % 4
            completed = (2, 1, 0, 1)[trajectory]
            actual_minutes = (5, 15, 35, None, 50, 20, 8)[(2*gi+di-1) % 7]
            planned_first = (15, 20, 30, 25, 10, 40)[gi]
            planned_second = (10, 15, 20, 15, 25, 20)[gi]
            advice_forms = (
                f"今天想练习{topic}。请参考已保存目标、昨日复盘和真实执行，给两条今天可做的具体建议，先别加入计划。",
                f"昨天的执行留在复盘里了。今天的主题是{topic}，结合实际完成和用时给我两个可选行动；只展示，等我选。",
                f"帮我为今天的{topic}列两个可执行候选。请检查已有目标、昨日完成情况和计划外行动，不把未记录的用时编出来。",
                f"我今天打算继续{topic}。按真实学习记录建议两件小事，遵守我的偏好；没有历史就直说，不直接保存。",
                f"今天练{topic}该怎样起步？结合昨天的事实给两个编号建议，计划与实际时长分开，暂时不写计划。",
                f"先帮我想两个今天练习{topic}的任务。看看昨日复盘与真实执行，再解释建议依据，我随后选择。",
            )
            adoption_first = (
                f"我今天先做{topic}的第一条，加入今天计划，预计{planned_first}分钟。",
                f"文件练习就从刚才第一个{topic}任务开始；把它加入今天计划，用{planned_first}分钟。",
                f"把你给的第一条{topic}基础练习加到今天计划，预计{planned_first}分钟。",
                f"第一项{topic}阅读练习加入今天计划，用{planned_first}分钟。",
                f"先采纳{topic}的第一项练习，放进今天待办，预计{planned_first}分钟。",
                f"第一条{topic}课程练习我今天要做，请加入今天计划，预留{planned_first}分钟。",
            )
            adoption_second = (
                f"第二个{topic}练习也加入今天计划，{planned_second}分钟。",
                f"刚才关于{topic}的第二条我也选，今天另安排{planned_second}分钟。",
                f"继续把{topic}原来编号第二项加入今天计划，预计{planned_second}分钟。",
                f"今天再采纳第二个{topic}阅读候选，加入计划，时长{planned_second}分钟。",
                f"{topic}原建议第二条也存进今天计划，给它{planned_second}分钟。",
                f"接着采用第二项{topic}课程练习，放在今天的计划里，{planned_second}分钟。",
            )
            completion = [
                step("把今天所有未完成计划标记完成。", requires="daily_plans", purpose="先展示真实范围并统一确认；本轮零写入", capture="completion_preview"),
                step("确认", tools=["complete_plan", "complete_plan"], change="plan_complete", requires="completion_preview", purpose="仅完成两条当日绑定UID，未展示及跨日对象不混入"),
            ] if completed == 2 else [
                step(f"把今天的{{first_plan_title}}标记完成，另一项今天还没做完。", tools=["complete_plan"], change="plan_complete", requires="daily_plans", completion_selection=1, purpose="明确完整标题只完成第一项；第二项仍未完成"),
                step("现在查看今天计划，区分已经完成和还没完成的。", tools=["show_plan"], purpose="部分完成后只读核对两个真实UID，不批量补齐完成"),
            ] if completed == 1 else [
                step(f"{topic}今天只看了开头，两项计划还没做完。先不要标记完成。", purpose="低执行日保持两项未完成，进度叙述不自动记行动"),
                step("今天先到这里。未完成的留在原日期，别自动挪到明天。", purpose="没有授权自动迁移/未来调度，原日期状态不变"),
            ]
            if trajectory == 2:
                action = step("今天没有做计划外的事情，不要记行动。", purpose="无计划外行动的低完成轨迹，不能凑一条记录")
            else:
                duration = f"实际用了{actual_minutes}分钟。" if actual_minutes is not None else "我没有计时，不知道实际用了多久。"
                action = step(f"记录今天的计划外行动：给{topic}整理了一张概念卡，{duration}", tools=["add_action_log"], change="action_add", purpose="记录真实具体行动；未知时长保留未知，不按0或计划时长代填")
                action["expected"]["duration_minutes"] = actual_minutes
            turns = [
                step(f"记住：我的长期学习目标是{goal}，{preference}。", tools=["save_memory"], change="memory_add", purpose="明确保存本组合成目标，不保存整份七日方案") if di == 1 else
                step(f"请查看昨天练习{topics[di-2]}的复盘，核对昨天实际完成和没完成的部分。", tools=["show_daily_review"], purpose="读取前一真实模拟日已经保存的复盘，不编造前日进展", inspect_date="yesterday"),
                step(advice_forms[gi], capture="advice", purpose="两条真实今日建议零写入；核对已保存目标、昨日/7日/30日执行依据，建议质量人工复核"),
                step(adoption_first[gi], tools=["add_plan"], change="plan_add", cases=["bt_d30_05"], requires="advice", selection=1, purpose="只采用真实展示第一项；保留标题、日期、UID与本轮明确计划时长"),
                step(adoption_second[gi], tools=["add_plan"], change="plan_add", requires="advice", selection=2, purpose="第一次采用后保留原编号续选；第二项使用自己的计划时长"),
                step((f"查看今天的{topic}计划清单。", f"今天这两条{topic}任务有没有真的保存？展示今天计划。", f"展示今天保存的{topic}学习计划。", f"让我看看今天的{topic}阅读计划和预计时长。", f"今天{topic}练习加好了哪些？查看今天计划。", f"查询今天的{topic}课程任务清单。")[gi], tools=["show_plan"], purpose="展示真实当前日清单、两个UID及各自计划时长"),
                *completion,
                action,
                *boundary(di, gi, topic, completed),
                step(("今日复盘。", "生成并保存今天的复盘。", "请把今天的计划与行动整理成复盘并保存。", "保存今日复盘。", "今天的任务和计划外行动一起复盘，保存到成长日志。", "重新生成并保存今天的复盘。", "把今天实际完成和未完成情况生成复盘并保存。")[di-1], tools=["daily_review"], change="review_save", purpose="同轮生成并保存真实今日完整复盘；与计划/行动/UID及日期一致，不能额外存为长期记忆"),
            ]
            for ti, turn in enumerate(turns, 1):
                turn["id"] = f"g{gi+1}-d{di}-t{ti:02}"
                if "successful_tools" in turn:
                    turn["expected"]["successful_tools"] = turn.pop("successful_tools")
            if di == 4:
                turns[0]["before"] = "restart_service"
            turns[2]["expected"]["duration_minutes"] = planned_first
            turns[3]["expected"]["duration_minutes"] = planned_second
            for turn in completion:
                if turn["expected"]["change"] == "plan_complete":
                    turn["expected"]["complete_count"] = completed
            days.append({"day": di, "date": (first + timedelta(days=di-1)).isoformat(), "topic": topic,
                         "trajectory": {"expected_core_completed": completed, "expected_core_action_count": 0 if trajectory == 2 else 1,
                                        "action_minutes": None if trajectory == 2 else actual_minutes, "planned_minutes": planned_first+planned_second,
                                        "note": "计划轨迹不是实际结果；存在失败时后续只用真实存储核对，不补写数据。"}, "turns": turns})
        groups.append({"id": f"g{gi+1}", "profile": key, "goal": goal, "preference": preference, "days": days})
    return {"schema_version": 1, "stage": "prepared_not_executed", "turn_definition": "one user input + one final assistant reply = one round; 15 rounds/day = 30 messages/day",
            "limits": {"http": 2000, "output_tokens_per_request": 1500, "reported_tokens_stop": 4000000,
                       "group_seconds": 1800, "turn_seconds": 80, "parent_seconds": 10860},
            "source": {"corpus_sha256": digest(CORPUS), "index_sha256": digest(INDEX)},
            "groups": groups,
            "scope_limits": ["630轮是工程模拟，不是真实七天用户数据或630名用户；不是知识正确率或学习收益测评", "知识/自然回复保留人工复核；归档/真实纠正的独立前置场景未自动覆盖", "不验证Qt桌面或后台提醒；提醒事件需独立场景", "不自动迁移/调度未来计划，不把计划能力当已有承诺", "同日只在明确restart_service处重建；进程内pending不宣称可跨重启恢复", "默认不重试业务失败；安全后续保留实例继续，依赖缺失标blocked且不补回复；错误写入/基础设施异常暂停本组"]}


DIFFERENCES = {
    "bt_today_add_14": ("current_contract_overlay", "原前置是学习方向advice_choice，‘第二个，一个小时’只能补方向/时长并讨论，零add_plan；仅真实今日可执行建议才能采用。"),
    "bt_cd_09": ("current_contract_overlay", "原tools=add_action_log/success与无正文先澄清冲突。当前先问记什么，零工具零写入；后续提供真实正文才另轮授权。"),
    "bt_cd_10": ("current_contract_overlay", "非今天新增限制不等于禁止未来只读。当前预期show_plan按真实明日数据返回，不创建占位，不编造条目。"),
    "bt_d30_02": ("retained", "现有语料方向选择已经是零写入，保留；不能重新套用旧的今日建议采用预期。"),
    "btm_05": ("retained", "当前方向讨论→明确今日建议→选用分开；不能将阶段编号升级为写权限。"),
    "bt_action_09": ("pending_decision", "语料同时固定零工具又允许唯一目标明确完成路径；须按真实对象与当前确认策略细化，未将任一路径偷标通过。"),
    "bt_conf_06": ("pending_decision", "‘先别执行’零写入已明确，但暂停后是否必须保留同一pending需总控按当前状态合同裁定。"),
    "bt_today_add_16": ("current_contract_overlay", "原已知失败是历史观测而非期望；保留原记录，当前仍要求真实add_plan与落盘，首答新结果独立记。"),
    "bt_d30_06": ("pending_decision", "不支持30天调度已明确；‘每天一小时帮我记下来’是否是明确长期目标保存授权需裁定，不能一概把save_memory禁掉。"),
    "bt_d30_07": ("pending_decision", "模糊学习意愿授权策略已由用户暂缓；方向列表本身不授予今日写权限，实际理解分支保留人工裁定，不以旧零工具预期自动驱动修复。"),
    "bt_action_07": ("pending_decision", "纯未来意向不能记为已发生行动已确定；是否构成今日计划新增的模糊意愿策略暂缓，独立记录人工裁定。"),
    "btm_07": ("current_contract_overlay", "开始学习的第一回复按现有学习方式选择，不硬要求旧文案‘现在开始’。"),
}


def audit_corpus(plan):
    placements = {}
    for group in plan["groups"]:
        for day in group["days"]:
            for turn in day["turns"]:
                for cid in turn["corpus_contract_refs"]:
                    placements.setdefault(cid, []).append(turn["id"])
    rows = []
    for raw in CORPUS.read_text(encoding="utf-8-sig").splitlines():
        case = json.loads(raw)
        cid = case["case_id"]
        diff, reason = DIFFERENCES.get(cid, ("retained", "保留当前合同；执行前核对原前置条件，不能只凭同族句面宣称原例已覆盖。"))
        historical = case["status"] in {"历史不适用", "计划"}
        where = [] if historical else placements.get(cid, [])
        disposition = "not_applicable" if historical else "pending_decision" if diff == "pending_decision" else "embedded_contract" if where else "independent_precondition_replay"
        pre = case.get("precondition", {})
        rows.append({"case_id": cid, "family": case["family"], "original_status": case["status"],
                     "original_record_sha256": hashlib.sha256(raw.encode()).hexdigest(),
                     "disposition": disposition, "contract_difference": diff, "reason": reason,
                     "planned_turns": where, "coverage_mode": "semantic_adaptation_not_exact_fixture" if where else "not_embedded",
                     "precondition": pre, "original_expected": case.get("expected", case.get("turns")),
                     "current_contract": "docs/capability_boundaries.md; docs/interaction_state_machine.md",
                     "execution_status": "not_run",
                     "independent_requirement": "保留原独立前置状态/事件；准备显式setup和期望后另行排期，不在连续七日中偷偷预置快照或修改用户状态。" if disposition == "independent_precondition_replay" else None,
                     "background_events_require_separate_harness": any(t.get("role") == "event" for t in case.get("turns", []))})
    return {"corpus_sha256": digest(CORPUS), "index_sha256": digest(INDEX), "records": rows,
            "counting": "129条逐项分流；语义迁移不是原文+原前置完整覆盖，所有执行结果均未执行；历史/计划不进入成功率。"}


def validate_plan(plan):
    if len(plan["groups"]) != 6:
        raise ValueError("exactly six groups required")
    maxima = {"http": 2000, "output_tokens_per_request": 1500, "reported_tokens_stop": 4000000,
              "group_seconds": 1800, "turn_seconds": 80, "parent_seconds": 10860}
    for key, maximum in maxima.items():
        value = plan["limits"][key]
        if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= maximum:
            raise ValueError("reviewed budget exceeds hard ceiling: " + key)
    ids = set()
    for group in plan["groups"]:
        if len(group["days"]) != 7:
            raise ValueError("exactly seven days required")
        previous = None
        for day in group["days"]:
            current = date.fromisoformat(day["date"])
            if previous and (current-previous).days != 1:
                raise ValueError("business days must be consecutive")
            previous = current
            if len(day["turns"]) != 15:
                raise ValueError("exactly fifteen user/assistant rounds per day")
            for turn in day["turns"]:
                if turn["id"] in ids or not turn["user"].strip() or not turn["expected"]["reply_contract"]:
                    raise ValueError("invalid/duplicate turn")
                ids.add(turn["id"])
    return len(ids)
