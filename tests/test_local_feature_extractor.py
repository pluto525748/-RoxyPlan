import sys
from datetime import datetime
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from modules.chinese_entity_parser import ChineseEntityParser
from modules.local_feature_extractor import LocalFeatureExtractor


def build_extractor():
    parser = ChineseEntityParser(now_provider=lambda: datetime(2026, 7, 24, 9, 0, 0))
    return LocalFeatureExtractor(parser)


def test_time_duration_reference_and_operation_features():
    features = build_extractor().extract("把第二个计划推迟两个小时，改成50分钟")
    assert "plan" in features.domain_cues
    assert "推迟" in features.operation_cues
    assert "改成" in features.operation_cues
    assert features.ordinal_expressions == ["第二个"]
    assert features.explicit_ids == {}
    assert features.parsed_time["duration_minutes"] == 50
    assert features.parsed_time["shift_minutes"] == 120


def test_natural_dates_and_action_splitting():
    extractor = build_extractor()
    features = extractor.extract("明早学半小时算法，然后记录今天完成了测试")
    assert "明早" in features.date_expressions
    assert "半小时" in features.duration_expressions
    assert "然后" in features.conjunctions
    assert len(extractor.split_actions(features.normalized_text)) == 2


def test_negation_and_questions_are_not_blind_actions():
    extractor = build_extractor()
    negative = extractor.extract("我不想跳舞")
    question = extractor.extract("跳舞有什么好处？")
    assert negative.negation_cues
    assert negative.likely_actionable is False
    assert question.is_question is True
    assert question.likely_actionable is False


def test_negation_before_long_plan_payload_is_not_actionable():
    features = build_extractor().extract("先不要把验收-否定事项C加入今天计划")
    assert features.polarity == "negated"
    assert "不要" in features.negation_cues
    assert features.likely_actionable is False


def test_plan_write_negation_after_advice_clause_is_not_a_command_envelope():
    features = build_extractor().extract(
        "请给我四项今天可以做的机器学习计划建议，先只给建议，不要加入计划"
    )
    assert features.polarity == "negated"
    assert features.payload_text == ""
    assert features.likely_actionable is False


def test_natural_multi_item_suffix_is_left_for_semantic_model():
    features = build_extractor().extract(
        "我今天要做三件事：确定路线、准备证件、查询天气，请都加进计划"
    )
    assert features.payload_text == ""
    assert features.command_text == ""
    assert features.clause_parse_status == "not_actionable"


def test_plan_command_shell_is_removed_from_title():
    parser = build_extractor().entity_parser
    assert parser.clean_plan_title("请把验收-真实桌面链路A加入今天计划") == "验收-真实桌面链路A"


if __name__ == "__main__":
    test_time_duration_reference_and_operation_features()
    test_natural_dates_and_action_splitting()
    test_negation_and_questions_are_not_blind_actions()
    print("local feature extractor tests passed")
