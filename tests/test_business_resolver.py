import sys
import tempfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
TESTS = Path(__file__).resolve().parent
for path in (ROOT, TESTS):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

from modules.local_feature_extractor import LocalFeatureExtractor
from modules.semantic_action_parser import ActionCandidate
from v18_test_support import NoCallLLM, build_service


def candidate(domain, tool, arguments=None, **kwargs):
    return ActionCandidate(
        domain=domain,
        tool_name=tool,
        arguments=arguments or {},
        confidence=kwargs.pop("confidence", 0.95),
        explicit_command=kwargs.pop("explicit_command", True),
        **kwargs,
    )


def test_plan_resolution_duplicate_and_real_target_lookup():
    with tempfile.TemporaryDirectory() as temp:
        service, growth, _memory, _history = build_service(Path(temp), NoCallLLM())
        resolver = service.business_resolver
        growth.add_task("学习机器学习基础")

        duplicate = resolver.resolve(
            candidate("plan", "add_plan", {"title": "学习机器学习基础"}),
            conversation_id="plan",
        )
        assert duplicate.status == "ambiguous"
        assert duplicate.reason_code == "similar_plan_exists"

        completed = resolver.resolve(
            candidate("plan", "complete_plan", {"match_text": "机器学习基础"}),
            conversation_id="plan",
        )
        assert completed.status == "resolved"
        assert completed.resolved_action.arguments["match_text"] == "学习机器学习基础"


def test_candidate_ids_are_locally_validated_and_action_logs_are_deduplicated():
    with tempfile.TemporaryDirectory() as temp:
        service, growth, _memory, _history = build_service(Path(temp), NoCallLLM())
        resolver = service.business_resolver
        created = service.memory_service.create_candidate("我喜欢早上安静学习")
        candidate_id = int(created.data["candidate"]["id"])
        features = LocalFeatureExtractor().extract(f"确认记忆{candidate_id}")

        accepted = resolver.resolve(
            candidate(
                "memory",
                "accept_memory_candidate",
                {"candidate_id": candidate_id},
            ),
            conversation_id="memory",
            features=features,
        )
        missing = resolver.resolve(
            candidate("memory", "accept_memory_candidate", {"candidate_id": 999}),
            conversation_id="memory",
            features=LocalFeatureExtractor().extract("确认记忆999"),
        )
        assert accepted.status == "resolved"
        assert accepted.resolved_action.arguments == {"candidate_id": candidate_id}
        assert missing.status == "not_found"

        growth.add_record("完成了交互状态测试")
        duplicate = resolver.resolve(
            candidate(
                "action_log",
                "add_action_log",
                {"content": "完成了交互状态测试"},
            ),
            conversation_id="action",
        )
        assert duplicate.status == "forbidden"
        assert duplicate.reason_code == "duplicate_action_log"


def test_progress_statement_requires_confirmation_but_explicit_record_does_not():
    with tempfile.TemporaryDirectory() as temp:
        service, _growth, _memory, _history = build_service(Path(temp), NoCallLLM())
        resolver = service.business_resolver
        progress = resolver.resolve(
            candidate(
                "action_log",
                "add_action_log",
                {"content": "我已经理解随机森林的两个随机性来源"},
                explicit_command=False,
                requires_confirmation_hint=True,
            ),
            conversation_id="action",
        )
        explicit = resolver.resolve(
            candidate(
                "action_log",
                "add_action_log",
                {"content": "今天完成了V1.8测试"},
            ),
            conversation_id="action",
        )
        assert progress.status == "confirmation_required"
        assert explicit.status == "resolved"


if __name__ == "__main__":
    test_plan_resolution_duplicate_and_real_target_lookup()
    test_candidate_ids_are_locally_validated_and_action_logs_are_deduplicated()
    test_progress_statement_requires_confirmation_but_explicit_record_does_not()
    print("business resolver tests passed")
