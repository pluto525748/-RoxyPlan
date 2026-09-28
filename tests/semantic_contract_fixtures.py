"""Shared semantic-payload builders for current and historical test fixtures.

The current builder is deliberately strict: a current-contract fixture must
spell out every model-visible field.  Historical V2.1 fixtures predate the
structured semantic fields, so they must enter through the explicitly named
compatibility adapter below instead of weakening the current contract.
"""
from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any


CURRENT_SEMANTIC_FIELDS = (
    "mode",
    "intent",
    "entities",
    "proposed_tool",
    "confidence",
    "follow_up_target",
    "needs_confirmation",
    "warnings",
    "clarification_question",
    "candidate_actions",
    "subject",
    "polarity",
    "modality",
    "request_mode",
    "explicit_command",
)

_VALID_MODES = {"chat", "read", "write", "clarify"}
_VALID_SUBJECTS = {"", "self", "other"}
_VALID_POLARITIES = {"", "positive", "negative"}
_VALID_MODALITIES = {"", "commitment", "desire", "hypothetical", "question"}
_VALID_REQUEST_MODES = {
    "query",
    "execute",
    "possible_action",
    "advice",
    "discuss",
}


class SemanticFixtureContractError(ValueError):
    """A test fixture does not satisfy the semantic payload contract."""


def build_current_semantic_payload(
    source: Mapping[str, Any],
    *,
    case_id: str | None = None,
) -> dict[str, Any]:
    """Validate and copy one payload written for the current model contract.

    This function never supplies semantic defaults.  In particular,
    ``subject``, ``polarity``, ``modality``, ``request_mode`` and
    ``explicit_command`` must be authored by the current fixture itself.
    """

    label = _case_label(source, case_id)
    missing = [field for field in CURRENT_SEMANTIC_FIELDS if field not in source]
    if missing:
        _fail(label, f"missing current fields: {', '.join(missing)}")

    mode = _required_string(source["mode"], label, "mode")
    if mode not in _VALID_MODES:
        _fail(label, f"invalid mode: {mode!r}")

    intent = _required_string(source["intent"], label, "intent")
    entities = source["entities"]
    if not isinstance(entities, Mapping):
        _fail(label, "entities must be an object")

    proposed_tool = _optional_string(source["proposed_tool"], label, "proposed_tool")
    confidence = source["confidence"]
    if isinstance(confidence, bool) or not isinstance(confidence, (int, float)):
        _fail(label, "confidence must be a number")
    if not 0.0 <= float(confidence) <= 1.0:
        _fail(label, "confidence must be between 0 and 1")

    follow_up_target = _optional_string(
        source["follow_up_target"], label, "follow_up_target"
    )
    needs_confirmation = source["needs_confirmation"]
    if not isinstance(needs_confirmation, bool):
        _fail(label, "needs_confirmation must be a boolean")

    warnings = source["warnings"]
    if not isinstance(warnings, list) or not all(
        isinstance(item, str) for item in warnings
    ):
        _fail(label, "warnings must be an array of strings")

    clarification_question = _optional_string(
        source["clarification_question"], label, "clarification_question"
    )
    candidate_actions = source["candidate_actions"]
    if not isinstance(candidate_actions, list) or not all(
        isinstance(item, Mapping) for item in candidate_actions
    ):
        _fail(label, "candidate_actions must be an array of objects")

    subject = _enum_string(source["subject"], label, "subject", _VALID_SUBJECTS)
    polarity = _enum_string(
        source["polarity"], label, "polarity", _VALID_POLARITIES
    )
    modality = _enum_string(
        source["modality"], label, "modality", _VALID_MODALITIES
    )
    request_mode = _enum_string(
        source["request_mode"], label, "request_mode", _VALID_REQUEST_MODES
    )
    explicit_command = source["explicit_command"]
    if not isinstance(explicit_command, bool):
        _fail(label, "explicit_command must be a boolean")
    if explicit_command and (mode != "write" or request_mode != "execute"):
        _fail(
            label,
            "explicit_command=true requires mode='write' and request_mode='execute'",
        )

    return {
        "mode": mode,
        "intent": intent,
        "entities": dict(entities),
        "proposed_tool": proposed_tool,
        "confidence": float(confidence),
        "follow_up_target": follow_up_target,
        "needs_confirmation": needs_confirmation,
        "warnings": list(warnings),
        "clarification_question": clarification_question,
        "candidate_actions": [dict(item) for item in candidate_actions],
        "subject": subject,
        "polarity": polarity,
        "modality": modality,
        "request_mode": request_mode,
        "explicit_command": explicit_command,
    }


def adapt_legacy_v21_semantic_payload(
    source: Mapping[str, Any],
    *,
    case_id: str | None = None,
    expected_candidates: Sequence[str] | None = None,
) -> dict[str, Any]:
    """Project one legacy V2.1 fixture into the complete current payload.

    Compatibility inference is intentionally confined to this adapter.  V2.1
    recorded ``mode`` and, for matrix cases, ``expected_candidates`` but did
    not record the newer structured fields.  Empty structured annotations mean
    "unknown in the historical fixture"; they are not linguistic guesses.
    """

    label = _case_label(source, case_id)
    for required in ("mode", "intent"):
        if required not in source:
            _fail(label, f"legacy V2.1 fixture missing field: {required}")

    mode = str(source["mode"]).strip()
    if expected_candidates is None:
        raw_expected = source.get("expected_candidates")
        if raw_expected is None:
            # Some replay fixtures only recorded the model proposal.  In that
            # older shape, write mode itself was the execution signal.
            has_expected_action = mode == "write"
        else:
            if not isinstance(raw_expected, Sequence) or isinstance(
                raw_expected, (str, bytes)
            ):
                _fail(label, "legacy expected_candidates must be an array")
            has_expected_action = bool(raw_expected)
    else:
        if isinstance(expected_candidates, (str, bytes)):
            _fail(label, "legacy expected_candidates must be an array")
        has_expected_action = bool(expected_candidates)

    if "request_mode" in source:
        request_mode = source["request_mode"]
    elif mode == "read":
        request_mode = "query"
    elif mode == "chat":
        request_mode = "discuss"
    elif mode == "write" and has_expected_action:
        request_mode = "execute"
    else:
        request_mode = "possible_action"

    explicit_command = source.get(
        "explicit_command",
        mode == "write" and has_expected_action,
    )
    projected = {
        "mode": mode,
        "intent": source["intent"],
        "entities": dict(source.get("entities", {})),
        "proposed_tool": source.get("proposed_tool"),
        "confidence": source.get("confidence", 0.97),
        "follow_up_target": source.get("follow_up_target"),
        "needs_confirmation": source.get("needs_confirmation", False),
        "warnings": list(source.get("warnings", [])),
        "clarification_question": source.get("clarification_question"),
        "candidate_actions": list(source.get("candidate_actions", [])),
        "subject": source.get("subject", ""),
        "polarity": source.get("polarity", ""),
        "modality": source.get("modality", ""),
        "request_mode": request_mode,
        "explicit_command": explicit_command,
    }
    return build_current_semantic_payload(projected, case_id=label)


def _case_label(source: Mapping[str, Any], case_id: str | None) -> str:
    value = case_id if case_id is not None else source.get("id")
    return str(value or "<unknown-case>")


def _fail(case_id: str, message: str) -> None:
    raise SemanticFixtureContractError(
        f"semantic fixture case {case_id!r}: {message}"
    )


def _required_string(value: Any, case_id: str, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        _fail(case_id, f"{field} must be a non-empty string")
    return value.strip()


def _optional_string(value: Any, case_id: str, field: str) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        _fail(case_id, f"{field} must be a string or null")
    return value


def _enum_string(
    value: Any,
    case_id: str,
    field: str,
    allowed: set[str],
) -> str:
    if not isinstance(value, str):
        _fail(case_id, f"{field} must be a string")
    normalized = value.strip().lower()
    if normalized not in allowed:
        _fail(case_id, f"invalid {field}: {value!r}")
    return normalized
