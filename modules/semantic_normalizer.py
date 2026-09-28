"""Deterministic normalizer for model-produced SemanticDecisions.

Runs *before* strict schema validation.  Only per-tool whitelist mappings
are applied.  Global fuzzy matching, semantic guessing, and intent changes
are explicitly forbidden.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

from modules.semantic_action_parser import SemanticDecision, SemanticToolCall
from modules.tool_registry import ToolRegistry


@dataclass
class NormalizeDiagnostic:
    """One atomic normalization record — fully deterministic and auditable."""

    tool_index: int
    original_tool_name: str
    resolved_tool_name: str
    field_mappings: List[Tuple[str, str, str]] = field(default_factory=list)
    # Each tuple: (original_field, canonical_field, rule_source)
    # rule_source is one of: "tool_alias", "field_alias:<tool_name>"
    tool_name_changed: bool = False


@dataclass
class NormalizeResult:
    decision: SemanticDecision
    diagnostics: List[NormalizeDiagnostic] = field(default_factory=list)
    # Fields that were dropped because they didn't map to any known field
    # for the target tool.  These are preserved for the validator to reject
    # if the field is genuinely unknown (not an alias miss).
    unresolved_fields: Dict[int, List[str]] = field(default_factory=dict)


class DeterministicNormalizer:
    """Applies per-tool whitelist mappings from ToolRegistry.

    Invariants (enforced, not just documented):
    - Tool count is preserved.
    - Intent is never changed.
    - Authorization scope is never widened.
    - Only exact alias matches are resolved; fuzzy matching is forbidden.
    - Every normalization is recorded in diagnostics.
    """

    def __init__(self, registry: ToolRegistry) -> None:
        self._registry = registry
        # Cache alias maps so they're computed once per registry lifetime.
        self._tool_aliases: Dict[str, str] = registry.tool_name_aliases()
        self._field_aliases: Dict[str, Dict[str, str]] = registry.all_field_aliases()

    def normalize(self, decision: SemanticDecision) -> NormalizeResult:
        """Return a normalized decision with full diagnostic trace."""
        diagnostics: List[NormalizeDiagnostic] = []
        unresolved: Dict[int, List[str]] = {}
        normalized_calls: List[SemanticToolCall] = []

        for index, call in enumerate(decision.tool_calls):
            diag = NormalizeDiagnostic(
                tool_index=index,
                original_tool_name=call.name,
                resolved_tool_name=call.name,
            )

            # -- 1. Resolve tool name alias ---------------------------------
            canonical_tool = self._resolve_tool_name(call.name)
            if canonical_tool != call.name:
                diag.tool_name_changed = True
                diag.resolved_tool_name = canonical_tool
                diag.field_mappings.append(
                    (call.name, canonical_tool, "tool_alias")
                )

            # -- 2. Resolve field name aliases ------------------------------
            tool_field_aliases = self._field_aliases.get(canonical_tool, {})
            normalized_args: Dict[str, object] = {}
            tool_unresolved: List[str] = []

            for field_name, value in call.arguments.items():
                canonical_field = tool_field_aliases.get(field_name)
                if canonical_field is not None:
                    normalized_args[canonical_field] = value
                    diag.field_mappings.append(
                        (
                            field_name,
                            canonical_field,
                            f"field_alias:{canonical_tool}",
                        )
                    )
                elif field_name in self._known_fields(canonical_tool):
                    # Already a canonical field name — pass through.
                    normalized_args[field_name] = value
                else:
                    # Unknown field.  Keep it so the validator can produce
                    # a precise error.  The normalizer must not silently drop
                    # data that might belong to a different tool.
                    normalized_args[field_name] = value
                    tool_unresolved.append(field_name)

            if tool_unresolved:
                unresolved[index] = tool_unresolved

            normalized_calls.append(
                SemanticToolCall(name=canonical_tool, arguments=normalized_args)
            )
            diagnostics.append(diag)

        # Build the normalized decision.  Intent, mode, confidence, and tool
        # count are preserved exactly — the normalizer only touches tool names
        # and field names within each tool call.
        normalized_decision = SemanticDecision(
            mode=decision.mode,
            intent=decision.intent,
            tool_calls=normalized_calls,
            confidence=decision.confidence,
            follow_up_target=decision.follow_up_target,
            reply_hint=decision.reply_hint,
            subject=decision.subject,
            polarity=decision.polarity,
            modality=decision.modality,
        )

        return NormalizeResult(
            decision=normalized_decision,
            diagnostics=diagnostics,
            unresolved_fields=unresolved,
        )

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _resolve_tool_name(self, name: str) -> str:
        """Map an alias to its canonical tool name, if registered."""
        clean = str(name).strip()
        if not clean:
            return clean
        # Direct hit — already canonical.
        if self._registry.get(clean) is not None:
            return clean
        # Alias lookup.
        return self._tool_aliases.get(clean, clean)

    def _known_fields(self, tool_name: str) -> set:
        """Return the set of canonical field names for *tool_name*."""
        tool = self._registry.get(tool_name)
        if tool is None:
            return set()
        return set(tool.parameters_schema.keys())
