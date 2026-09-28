from pathlib import Path


def read_tree(directory):
    return "\n".join(
        path.read_text(encoding="utf-8")
        for path in Path(directory).rglob("*.py")
    )


def test_agent_and_business_modules_do_not_import_qt_or_frontend():
    agent_sources = "\n".join(
        Path(path).read_text(encoding="utf-8")
        for path in (
            "modules/agent_core.py",
            "modules/agent_planner.py",
            "modules/conversation_service.py",
            "modules/tool_registry.py",
            "modules/client_action.py",
            "modules/client_action_result.py",
        )
    )
    assert "PySide6" not in agent_sources
    assert "import frontend" not in agent_sources
    assert "from frontend" not in agent_sources


def test_agent_does_not_call_pet_action_manager():
    modules = read_tree("modules")
    assert "PetActionManager" not in modules
    assert ".start_dance()" not in Path("modules/tool_registry.py").read_text(encoding="utf-8")


def test_dispatcher_uses_shared_allowlist():
    source = Path("frontend/client_action_dispatcher.py").read_text(encoding="utf-8")
    assert "ClientActionPolicy" in source
    assert "eval(" not in source
    assert "subprocess" not in source
