import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from modules.safety_policy import SafetyPolicy


def test_risk_decisions():
    policy = SafetyPolicy(medium_confidence_threshold=0.82)
    assert policy.evaluate(risk_level="low", confidence=0.1).allowed
    assert policy.evaluate(risk_level="medium", confidence=0.7).status == "clarification"
    assert policy.evaluate(risk_level="medium", confidence=0.9).allowed
    assert policy.evaluate(risk_level="high", confidence=1.0).status == "confirmation_required"
    assert policy.evaluate(risk_level="high", confidence=1.0, confirmed=True).allowed


def test_negated_and_informational_requests_are_denied():
    policy = SafetyPolicy()
    assert policy.evaluate(risk_level="high", confidence=1.0, negated=True).reason == "negated_request"
    assert policy.evaluate(risk_level="high", confidence=1.0, informational=True).reason == "informational_request"


def test_explicit_local_authorization_bypasses_only_medium_confidence_gate():
    policy = SafetyPolicy(medium_confidence_threshold=0.82)

    allowed = policy.evaluate(
        risk_level="medium",
        confidence=0.8,
        authorization_granted=True,
    )
    high_risk = policy.evaluate(
        risk_level="high",
        confidence=1.0,
        authorization_granted=True,
    )
    negated = policy.evaluate(
        risk_level="medium",
        confidence=1.0,
        authorization_granted=True,
        negated=True,
    )

    assert allowed.allowed
    assert high_risk.status == "confirmation_required"
    assert negated.reason == "negated_request"


if __name__ == "__main__":
    test_risk_decisions()
    test_negated_and_informational_requests_are_denied()
    print("safety policy tests passed")
