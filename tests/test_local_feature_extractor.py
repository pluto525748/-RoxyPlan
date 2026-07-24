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


if __name__ == "__main__":
    test_time_duration_reference_and_operation_features()
    test_natural_dates_and_action_splitting()
    test_negation_and_questions_are_not_blind_actions()
    print("local feature extractor tests passed")
