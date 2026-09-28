import tempfile
from pathlib import Path

from modules.growth_manager import GrowthManager
from modules.memory_manager import MemoryManager
from modules.tool_registry import create_roxy_tool_registry
from tests.isolation_support import build_isolated_memory_manager


class RecordingPet:
    def __init__(self):
        self.calls = 0

    def start_dance(self):
        self.calls += 1
        return True


def test_tool_registry_declares_dance_without_calling_qt_pet():
    with tempfile.TemporaryDirectory() as temp:
        root = Path(temp)
        pet = RecordingPet()
        registry = create_roxy_tool_registry(
            GrowthManager(root / "private"),
            build_isolated_memory_manager(root),
            pet,
        )
        result = registry.get("play_dance").handler()
        assert result.success
        assert result.data["client_action"]["name"] == "play_dance"
        assert pet.calls == 0


def test_desktop_ui_dispatches_before_rendering_message():
    source = Path("frontend/pet_app.py").read_text(encoding="utf-8")
    method = source[source.index("    def _present_agent_response"):source.index("    def ask_ai")]
    assert method.index("dispatch_all") < method.index("self.add_message")
