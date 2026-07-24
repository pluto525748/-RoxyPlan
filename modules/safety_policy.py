from __future__ import annotations

from dataclasses import dataclass


VALID_RISK_LEVELS = {"low", "medium", "high"}


@dataclass(frozen=True)
class SafetyDecision:
    allowed: bool
    status: str
    reason: str


class SafetyPolicy:
    """Deterministic policy gate; model output cannot bypass this decision."""

    def __init__(
        self,
        *,
        medium_confidence_threshold: float = 0.82,
        always_confirm_high_risk: bool = True,
    ) -> None:
        self.medium_confidence_threshold = max(
            0.0, min(float(medium_confidence_threshold), 1.0)
        )
        self.always_confirm_high_risk = bool(always_confirm_high_risk)

    def evaluate(
        self,
        *,
        risk_level: str,
        confidence: float,
        requires_confirmation: bool = False,
        confirmed: bool = False,
        negated: bool = False,
        informational: bool = False,
    ) -> SafetyDecision:
        risk = str(risk_level)
        if risk not in VALID_RISK_LEVELS:
            return SafetyDecision(False, "denied", "unknown_risk")
        if negated:
            return SafetyDecision(False, "denied", "negated_request")
        if informational:
            return SafetyDecision(False, "denied", "informational_request")

        confirmation_needed = requires_confirmation or (
            risk == "high" and self.always_confirm_high_risk
        )
        if confirmation_needed and not confirmed:
            print(f"[Safety] risk={risk} confirmation required", flush=True)
            return SafetyDecision(False, "confirmation_required", "confirmation_required")
        if risk == "medium" and float(confidence) < self.medium_confidence_threshold:
            return SafetyDecision(False, "clarification", "confidence_too_low")
        return SafetyDecision(True, "allowed", "allowed")
