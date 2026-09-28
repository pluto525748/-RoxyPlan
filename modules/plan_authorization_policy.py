"""Deterministic policy for authorizing plan-affecting tool execution.

Reads validated semantic fields plus parser-owned request metadata.
Never inspects raw text or keywords.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Optional

from modules.semantic_action_parser import SemanticDecision


class PlanAuthorization(Enum):
    """What the policy has decided about a plan-affecting SemanticDecision."""

    EXECUTE = "execute"
    PENDING = "pending"
    NO_ACTION = "no_action"


# ── Tools that create or modify plans ────────────────────────────────
_PLAN_WRITE_TOOLS = {
    "add_plan",
    "complete_plan",
    "update_plan",
    "merge_plan",
    "reschedule_plan",
    "delete_plan",
    "reopen_plan",
    "cancel_plan",
}


@dataclass(frozen=True)
class PlanAuthorizationResult:
    authorization: PlanAuthorization
    reason: str = ""


class PlanAuthorizationPolicy:
    """Decide whether a plan tool call may execute directly, must first
    create a confirmation pending, or must not execute at all.

    The policy reads structured SemanticDecision fields and the parser-owned
    request_mode / explicit_command flags. It does not inspect raw user text,
    entities, or any keyword table.
    """

    def evaluate(
        self,
        decision: SemanticDecision,
        *,
        request_mode: str = "",
        explicit_command: bool = False,
    ) -> PlanAuthorizationResult:
        # Only plan-affecting write tools go through this policy.
        tool_names = {item.name for item in decision.tool_calls}
        if not (tool_names & _PLAN_WRITE_TOOLS):
            return PlanAuthorizationResult(PlanAuthorization.EXECUTE)

        subject = decision.subject
        polarity = decision.polarity
        modality = decision.modality

        # ── Block: other / negative / hypothetical / question ──────
        if subject == "other":
            return PlanAuthorizationResult(
                PlanAuthorization.NO_ACTION,
                "subject=other — plan action blocked",
            )
        if polarity == "negative":
            return PlanAuthorizationResult(
                PlanAuthorization.NO_ACTION,
                "polarity=negative — plan action blocked",
            )
        if modality == "hypothetical":
            return PlanAuthorizationResult(
                PlanAuthorization.NO_ACTION,
                "modality=hypothetical — plan action blocked",
            )
        if modality == "question":
            return PlanAuthorizationResult(
                PlanAuthorization.NO_ACTION,
                "modality=question — plan action blocked",
            )

        # ── Allow: self + positive + commitment → direct execute ───
        if (
            subject == "self"
            and polarity == "positive"
            and modality == "commitment"
        ):
            return PlanAuthorizationResult(PlanAuthorization.EXECUTE)

        # Explicit plan commands remain executable even when the wording also
        # expresses desire, for example "我想把...加入计划".
        if (
            subject == "self"
            and polarity == "positive"
            and modality == "desire"
            and str(request_mode) == "execute"
            and explicit_command
        ):
            return PlanAuthorizationResult(
                PlanAuthorization.EXECUTE,
                "explicit execute request overrides desire pending",
            )

        # ── Pending: non-explicit desire → confirm first ───────────
        if (
            subject == "self"
            and polarity == "positive"
            and modality == "desire"
        ):
            return PlanAuthorizationResult(
                PlanAuthorization.PENDING,
                "self+positive+desire → confirm before executing",
            )

        # Default: no structured fields → let existing safety policy
        # and confirmation manager decide (preserve pre-existing
        # behaviour for tool calls that lack annotation).
        return PlanAuthorizationResult(PlanAuthorization.EXECUTE)
