from frontend.settings_dialog import DEFAULT_SETTINGS
from modules.feature_flags import (
    SEMANTIC_FLAG_DEFAULTS,
    compatibility_rollback_profile,
    validate_feature_flags,
)


def test_default_flags_have_one_semantic_and_one_pet_action_path():
    assert validate_feature_flags(DEFAULT_SETTINGS) == []
    assert DEFAULT_SETTINGS["unified_semantic_parser_enabled"] is True
    assert DEFAULT_SETTINGS["legacy_intent_path_enabled"] is False
    assert DEFAULT_SETTINGS["unified_client_action_dispatcher_enabled"] is True
    assert DEFAULT_SETTINGS["legacy_direct_pet_action_enabled"] is False
    assert DEFAULT_SETTINGS["interaction_continuation_ttl_seconds"] == 1200
    assert DEFAULT_SETTINGS["interaction_confirmation_ttl_seconds"] == 180


def test_conflicting_and_fully_disabled_paths_are_reported():
    conflicting = {
        **SEMANTIC_FLAG_DEFAULTS,
        "legacy_intent_path_enabled": True,
        "legacy_direct_pet_action_enabled": True,
    }
    assert "unified_and_legacy_intent_enabled" in validate_feature_flags(conflicting)
    assert "unified_and_legacy_pet_action_enabled" in validate_feature_flags(conflicting)

    disabled = {
        **SEMANTIC_FLAG_DEFAULTS,
        "unified_semantic_parser_enabled": False,
        "legacy_intent_path_enabled": False,
        "unified_client_action_dispatcher_enabled": False,
        "legacy_direct_pet_action_enabled": False,
        "action_preview_enabled": False,
        "action_batch_enabled": False,
        "business_resolver_enabled": False,
    }
    warnings = validate_feature_flags(disabled)
    assert "all_intent_paths_disabled" in warnings
    assert "all_pet_action_paths_disabled" in warnings


def test_rollback_profile_is_non_conflicting_and_keeps_pet_available():
    rollback = compatibility_rollback_profile()
    assert validate_feature_flags(rollback) == []
    assert rollback["legacy_intent_path_enabled"] is True
    assert rollback["unified_semantic_parser_enabled"] is False
    assert rollback["unified_client_action_dispatcher_enabled"] is True
