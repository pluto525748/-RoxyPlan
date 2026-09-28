from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, Mapping

from modules.capability_registry import DEFAULT_CAPABILITY_REGISTRY
from modules.contracts import ensure_json_value


@dataclass(frozen=True)
class ActionPreview:
    tool_name: str
    target_summary: str
    changes: Dict[str, object] = field(default_factory=dict)
    affects_existing_data: bool = False
    reversible: bool = False
    confirmation_prompt: str = "回复“确认”执行，回复“取消”放弃。"

    def to_dict(self) -> Dict[str, object]:
        return {
            "tool_name": self.tool_name,
            "target_summary": self.target_summary,
            "changes": ensure_json_value(self.changes, "ActionPreview.changes"),
            "affects_existing_data": self.affects_existing_data,
            "reversible": self.reversible,
            "confirmation_prompt": self.confirmation_prompt,
        }

    def message(self) -> str:
        lines = ["我理解你想做的是：", self.target_summary]
        if self.changes:
            lines.extend(f"- {key}: {value}" for key, value in self.changes.items())
        lines.append("可以撤销。" if self.reversible else "这项操作可能无法自动撤销。")
        lines.append(self.confirmation_prompt)
        return "\n".join(lines)


def build_action_preview(
    immutable_arguments: Mapping[str, object],
    *,
    safe_summary: str = "",
) -> ActionPreview:
    values = dict(immutable_arguments or {})
    tool_name = str(values.pop("tool_name", values.pop("tool", ""))).strip()
    capability = DEFAULT_CAPABILITY_REGISTRY.for_tool(tool_name)
    target = str(safe_summary or "").strip() or f"执行 {tool_name}"
    return ActionPreview(
        tool_name=tool_name,
        target_summary=target,
        changes=values,
        affects_existing_data=bool(capability and capability.side_effect),
        reversible=bool(capability and capability.reversible),
    )
