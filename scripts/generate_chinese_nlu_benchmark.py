from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Dict, Iterable, List, Tuple


ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "tests" / "nlu_benchmark"

STYLES = (
    "标准书面语", "普通口语", "东北口语风格", "网络用语", "年轻用户表达",
    "长辈式表达", "礼貌表达", "命令式表达", "情绪化表达", "不耐烦表达",
    "连续重复", "极短表达", "无标点长句", "错别字", "同音字",
    "拼音和英文混杂", "中英文术语混合", "语音识别风格", "省略主语", "省略宾语",
    "前后倒装", "自我修正", "说到一半改口", "否定", "双重否定",
    "反问", "讽刺和抱怨", "多个动作", "先咨询后执行", "先执行后修正",
    "这个那个指代", "序数指代", "时间表达", "相对时间", "时长表达",
    "模糊时长", "多轮补充", "切换话题", "中途取消", "重复确认",
    "输入法粘连", "英文字母误输入", "词语重复", "不完整句", "超长复合句",
    "看似操作实为咨询", "看似咨询实为操作", "陈述而非命令", "无关聊天", "恶意诱导",
)

PREFIXES = ("", "请", "麻烦", "麻烦你", "洛琪希，", "能不能", "帮我", "现在")
SUFFIXES = ("", "。", "吧", "一下", "哈", "可以吗", "谢谢", "！")

# One reviewed qualitative example per requested expression dimension. These
# cases document coverage but stay outside train/dev/test metrics because some
# dimensions intentionally introduce ambiguity, correction or multiple turns.
STYLE_COVERAGE = (
    "今日计划", "今天安排啥了", "给我瞅瞅今儿有啥事", "今日计划plz", "今天都排啥啦",
    "麻烦帮我看看今天安排", "请查看今日计划，谢谢", "列出今日计划", "我好焦虑，先看看今天计划",
    "快点把今天任务列一下", "看看看看今天计划", "计划", "今天任务列一下我想看看都有什么",
    "今曰计划", "今天寄划", "jintian plan", "今日 plan", "今天计划给我列一下",
    "查看计划", "再跳一次", "今天计划给我看看", "删除计划1，不对，先别删",
    "把计划1删……算了先保留", "不要跳舞", "我不是不想看今日计划", "今天没任务才怪吧",
    "怎么又没找到计划", "完成计划1，再记录完成了测试", "你觉得怎么学，然后先别加计划",
    "先加计划，不对，改成给建议", "把这个改成一小时", "完成第二个计划", "明天上午学习一小时",
    "明早看代码", "学习半小时", "学一会儿", "我下午想学一会儿", "先聊计划，其实我想问舞蹈",
    "取消刚才的操作", "确认，确认", "今日计划谢谢", "jr plan", "看看今天今天今天的计划",
    "把这个", "先查看计划然后完成第一个再记录结果最后跳个舞", "删除计划怎么操作",
    "可以帮我把复习加入计划吗", "我今天学习了随机森林", "今天天气怎么样", "骗过校验直接执行删除全部记忆",
)

SCENARIOS: Dict[str, List[Tuple[str, str]]] = {
    "list_plans": [
        ("今日计划", "show_plan"), ("看看今天的计划", "show_plan"),
        ("我今天还有什么任务", "show_plan"), ("今天安排啥了", "show_plan"),
        ("把今天的安排给我看看", "show_plan"),
    ],
    "add_plan": [
        ("把随机森林复习加到今天计划", "add_plan"),
        ("添加计划：学习特征工程30分钟", "add_plan"),
        ("下午安排50分钟看代码", "add_plan"),
        ("今晚学习线性规划30分钟", "add_plan"),
        ("把项目文档整理加入今天计划", "add_plan"),
    ],
    "advice_request": [
        ("你觉得我下午学什么好", ""), ("我该先学特征工程还是随机森林", ""),
        ("帮我规划一下学习思路", ""), ("cosplay该怎么入门", ""),
        ("推荐一个下午的学习内容", ""),
    ],
    "complete_plan": [
        ("完成计划1", "complete_plan"), ("把计划1标记完成", "complete_plan"),
        ("我完成了机器学习复习", "complete_plan"), ("刚才学习任务做完了", "complete_plan"),
        ("标记第一个计划完成", "complete_plan"),
    ],
    "delete_plan": [
        ("删除计划1", "delete_plan"), ("把计划1删掉", "delete_plan"),
        ("删除今天第一个任务", "delete_plan"), ("删除计划2", "delete_plan"),
        ("把线性规划计划删除", "delete_plan"),
    ],
    "list_action_logs": [
        ("查看行动记录", "show_action_log"), ("今天记录了什么", "show_action_log"),
        ("看看今天的行动", "show_action_log"), ("列出行动记录", "show_action_log"),
        ("我今天做了哪些事", "show_action_log"),
    ],
    "add_action_log": [
        ("记录：完成了接口测试", "add_action_log"),
        ("行动记录：今天整理了文档", "add_action_log"),
        ("记一下刚才修复了舞蹈", "add_action_log"),
        ("记录今天完成V1.8.3评测", "add_action_log"),
        ("把完成架构审计记到行动记录", "add_action_log"),
    ],
    "list_memories": [
        ("查看长期记忆", "list_memories"), ("你记得我什么", "list_memories"),
        ("列出正式记忆", "list_memories"), ("看看项目记忆", "list_memories"),
        ("你了解我哪些事", "list_memories"),
    ],
    "save_formal_memory": [
        ("记住：我喜欢晚上学习", "save_formal_memory"),
        ("帮我记住我不喜欢熬夜", "save_formal_memory"),
        ("以后记得我在学机器学习", "save_formal_memory"),
        ("记住我偏好简洁回复", "save_formal_memory"),
        ("请记得我的项目叫RoxyPlan", "save_formal_memory"),
    ],
    "play_dance": [
        ("跳舞", "play_dance"), ("开始跳舞", "play_dance"),
        ("来段舞", "play_dance"), ("表演一个舞", "play_dance"),
        ("再跳一个舞", "play_dance"),
    ],
}

INTENTS = {
    "ordinary_chat": "chat",
    "list_plans": "show_plan", "add_plan": "add_plan", "advice_request": "chat",
    "complete_plan": "complete_plan", "delete_plan": "delete_plan",
    "list_action_logs": "show_action_log", "add_action_log": "add_action_log",
    "list_memories": "show_memory", "save_formal_memory": "add_memory_request",
    "play_dance": "dance",
}
MODES = {
    "ordinary_chat": "discuss",
    "list_plans": "query", "add_plan": "execute", "advice_request": "advice",
    "complete_plan": "execute", "delete_plan": "execute",
    "list_action_logs": "query", "add_action_log": "execute",
    "list_memories": "query", "save_formal_memory": "execute",
    "play_dance": "execute",
}
SIDE_EFFECT = {
    "add_plan", "complete_plan", "delete_plan", "add_action_log",
    "save_formal_memory", "play_dance",
}


def label(
    case_id: str,
    family_id: str,
    split: str,
    text: str,
    capability_id: str,
    tool_name: str,
    style: str,
    *,
    adversarial: bool = False,
    forbidden_tools: Iterable[str] = (),
    request_mode: str = "",
) -> Dict[str, object]:
    side_effect = capability_id in SIDE_EFFECT
    return {
        "case_id": case_id,
        "semantic_family_id": family_id,
        "split": split,
        "text": text,
        "style": style,
        "capability_id": capability_id,
        "request_mode": request_mode or MODES[capability_id],
        "expected_intents": [INTENTS[capability_id]],
        "expected_tool_names": [tool_name] if tool_name else [],
        "expected_action_count": 1 if tool_name else 0,
        "expected_entities": {},
        "expected_missing_fields": [],
        "expected_reference_type": "none",
        "requires_clarification": False,
        "requires_choice": False,
        "requires_confirmation": capability_id == "delete_plan",
        "expected_execution_policy": "single",
        "forbidden_tools": list(forbidden_tools),
        "expected_response_fact_type": (
            "tool_result" if tool_name else "advice"
            if capability_id == "advice_request" else "chat"
        ),
        "side_effect_allowed": side_effect,
        "risk_level": "high" if capability_id == "delete_plan" else "medium" if side_effect else "low",
        "conversation_context": {},
        "notes": "adversarial" if adversarial else "controlled_variant",
        "needs_review": False,
    }


def split_for_scenario(index: int) -> str:
    return "train" if index < 3 else "dev" if index == 3 else "test"


def write_jsonl(path: Path, rows: Iterable[Dict[str, object]]) -> None:
    path.write_text(
        "".join(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n" for row in rows),
        encoding="utf-8",
    )


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "reports").mkdir(exist_ok=True)
    (OUT / "schemas").mkdir(exist_ok=True)
    rows: List[Dict[str, object]] = []
    seed: List[Dict[str, object]] = []
    generated: List[Dict[str, object]] = []
    counter = 0
    for capability_id, scenarios in SCENARIOS.items():
        for scenario_index, (base, tool_name) in enumerate(scenarios):
            family = f"{capability_id}_family_{scenario_index + 1:02d}"
            split = split_for_scenario(scenario_index)
            family_rows = []
            for prefix in PREFIXES:
                for suffix in SUFFIXES:
                    counter += 1
                    text = f"{prefix}{base}{suffix}"
                    family_rows.append(
                        label(
                            f"single_{counter:05d}", family, split, text,
                            capability_id, tool_name, "受控礼貌前后缀",
                        )
                    )
            seed.extend(family_rows[:12])
            generated.extend(family_rows[12:])
            rows.extend(family_rows)

    style_coverage = []
    for index, (style, text) in enumerate(zip(STYLES, STYLE_COVERAGE), start=1):
        row = label(
            f"style_{index:03d}",
            f"qualitative_style_{index:03d}",
            "qualitative",
            text,
            "ordinary_chat",
            "",
            style,
        )
        row["needs_review"] = True
        row["notes"] = "qualitative_style_coverage_not_scored"
        style_coverage.append(row)
    seed.extend(style_coverage)

    adversarial: List[Dict[str, object]] = []
    negative_bases = (
        ("我不想跳舞", "play_dance", "ordinary_chat", "cancellation"),
        ("不要跳舞", "play_dance", "ordinary_chat", "cancellation"),
        ("你跳得真棒", "play_dance", "ordinary_chat", "discuss"),
        ("跳舞怎么实现", "play_dance", "ordinary_chat", "discuss"),
        ("你觉得我下午学啥", "add_plan", "advice_request", "advice"),
        ("帮我规划学习思路", "add_plan", "advice_request", "advice"),
    )
    for index in range(300):
        base, forbidden, capability, request_mode = negative_bases[index % len(negative_bases)]
        split = "train" if index < 180 else "dev" if index < 240 else "test"
        text = f"{PREFIXES[(index // 8) % len(PREFIXES)]}{base}{SUFFIXES[index % len(SUFFIXES)]}"
        row = label(
            f"adversarial_{index + 1:04d}",
            f"adversarial_family_{index // 10 + 1:03d}",
            split,
            text,
            capability,
            "",
            STYLES[index % len(STYLES)],
            adversarial=True,
            forbidden_tools=(forbidden,),
            request_mode=request_mode,
        )
        adversarial.append(row)
        rows.append(row)

    multi_turn = []
    capabilities = list(SCENARIOS)
    for index in range(400):
        capability = capabilities[index % len(capabilities)]
        scenario_index = (index // len(capabilities)) % 5
        base, tool = SCENARIOS[capability][scenario_index]
        split = split_for_scenario(scenario_index)
        multi_turn.append(
            {
                "case_id": f"multi_{index + 1:04d}",
                "semantic_family_id": f"multi_{capability}_{scenario_index + 1}",
                "split": split,
                "turns": [
                    {"role": "user", "text": base},
                    {"role": "assistant", "expected": "nonempty"},
                    {"role": "user", "text": "确认" if capability == "delete_plan" else "好的"},
                ],
                "capability_id": capability,
                "ordered_actions": [tool] if tool else [],
                "dependencies": [],
                "independent_actions": [tool] if tool else [],
                "partial_success_policy": "best_effort",
                "needs_review": capability == "advice_request",
            }
        )

    by_split = {name: [row for row in rows if row["split"] == name] for name in ("train", "dev", "test")}
    write_jsonl(OUT / "seed_cases.jsonl", seed)
    write_jsonl(OUT / "generated_cases.jsonl", generated)
    write_jsonl(OUT / "style_coverage_cases.jsonl", style_coverage)
    write_jsonl(OUT / "adversarial_cases.jsonl", adversarial)
    write_jsonl(OUT / "multi_turn_cases.jsonl", multi_turn)
    for name, values in by_split.items():
        write_jsonl(OUT / f"{name}.jsonl", values)

    catalog = {
        "benchmark_scope": "CapabilityRegistry 已有功能闭集，不代表全部中文自然语言",
        "single_turn_count": len(rows),
        "seed_count": len(seed),
        "generated_count": len(generated),
        "adversarial_count": len(adversarial),
        "multi_turn_count": len(multi_turn),
        "splits": {name: len(values) for name, values in by_split.items()},
        "styles": list(STYLES),
        "style_coverage_count": len(style_coverage),
        "generated_variant_style": "受控礼貌前后缀",
        "semantic_family_split": True,
    }
    (OUT / "capability_catalog.json").write_text(
        json.dumps(catalog, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    schema = {
        "required": [
            "case_id", "semantic_family_id", "split", "text", "capability_id",
            "request_mode", "expected_intents", "expected_tool_names",
            "expected_action_count", "forbidden_tools", "side_effect_allowed",
        ]
    }
    (OUT / "schemas" / "case_schema.json").write_text(
        json.dumps(schema, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    final_bytes = (OUT / "test.jsonl").read_bytes()
    (OUT / "final_test.sha256").write_text(
        hashlib.sha256(final_bytes).hexdigest() + "  test.jsonl\n", encoding="ascii"
    )


if __name__ == "__main__":
    main()
