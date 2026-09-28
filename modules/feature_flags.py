from __future__ import annotations

from typing import Dict, List, Mapping


SEMANTIC_FLAG_DEFAULTS: Dict[str, bool] = {
    "unified_semantic_parser_enabled": True,
    "interaction_coordinator_enabled": True,
    "business_resolver_enabled": True,
    "action_preview_enabled": True,
    "action_batch_enabled": True,
    "deterministic_response_enabled": True,
    "unified_client_action_dispatcher_enabled": True,
    "legacy_intent_path_enabled": False,
    "legacy_direct_pet_action_enabled": False,
}


def validate_feature_flags(settings: Mapping[str, object]) -> List[str]:
    """Return startup warnings for unsafe or non-functional combinations."""
    enabled = {
        name: bool(settings.get(name, default))
        for name, default in SEMANTIC_FLAG_DEFAULTS.items()
    }
    warnings: List[str] = []
    if enabled["unified_semantic_parser_enabled"] and enabled["legacy_intent_path_enabled"]:
        warnings.append("unified_and_legacy_intent_enabled")
    if not enabled["unified_semantic_parser_enabled"] and not enabled["legacy_intent_path_enabled"]:
        warnings.append("all_intent_paths_disabled")
    if enabled["action_preview_enabled"] and not enabled["interaction_coordinator_enabled"]:
        warnings.append("action_preview_without_interaction_state")
    if enabled["action_batch_enabled"] and not enabled["unified_semantic_parser_enabled"]:
        warnings.append("action_batch_without_unified_parser")
    if enabled["business_resolver_enabled"] and not enabled["unified_semantic_parser_enabled"]:
        warnings.append("business_resolver_without_unified_parser")
    if (
        enabled["unified_client_action_dispatcher_enabled"]
        and enabled["legacy_direct_pet_action_enabled"]
    ):
        warnings.append("unified_and_legacy_pet_action_enabled")
    if not enabled["unified_client_action_dispatcher_enabled"] and not enabled[
        "legacy_direct_pet_action_enabled"
    ]:
        warnings.append("all_pet_action_paths_disabled")
    return warnings


def compatibility_rollback_profile() -> Dict[str, bool]:
    """Explicit compatibility profile; it never enables two write paths."""
    return {
        **SEMANTIC_FLAG_DEFAULTS,
        "unified_semantic_parser_enabled": False,
        "interaction_coordinator_enabled": False,
        "business_resolver_enabled": False,
        "action_preview_enabled": False,
        "action_batch_enabled": False,
        "deterministic_response_enabled": False,
        "legacy_intent_path_enabled": True,
        "unified_client_action_dispatcher_enabled": True,
        "legacy_direct_pet_action_enabled": False,
    }
