from __future__ import annotations

import re
from difflib import SequenceMatcher
from typing import Dict, List, Optional, Tuple

from modules.memory_candidate_manager import MemoryCandidateManager
from modules.memory_manager import MemoryManager


CANDIDATE_CATEGORIES = {
    "preference",
    "goal",
    "project",
    "routine",
    "relationship",
    "constraint",
    "current_state",
    "learning",
    "health",
    "other",
}

SENSITIVE_PATTERNS = (
    r"肠胃|胃病|过敏|失眠|睡不着|身体|疾病|吃药|疼痛|心理|抑郁|焦虑症",
    r"父母|家人|伴侣|妻子|丈夫|孩子|家庭关系",
    r"身份证|住址|手机号|真实姓名|身份信息",
)


class MemoryGovernanceService:
    """Shared candidate review and conflict workflow for desktop and Web."""

    def __init__(
        self,
        memory_manager: MemoryManager,
        candidate_manager: MemoryCandidateManager,
        *,
        enabled: bool = True,
    ) -> None:
        self.memory_manager = memory_manager
        self.candidate_manager = candidate_manager
        self.enabled = bool(enabled)

    def set_enabled(self, enabled: bool) -> None:
        self.enabled = bool(enabled)

    def propose_state_update(
        self,
        source_text: str,
        *,
        location: str,
        source: str = "conversation_state",
    ) -> Dict[str, object]:
        if not self.enabled:
            return {"status": "disabled", "candidate": None}
        clean_location = str(location).strip()
        content = f"我现在在{clean_location}"
        existing = next(
            (
                item
                for item in self.memory_manager.memories("active")
                if str(item.get("scope", "")) == "current_state"
                and str(item.get("location", "")).strip() == clean_location
            ),
            None,
        )
        if existing is not None:
            return {"status": "already_formal", "candidate": None}
        candidate, added = self.candidate_manager.add_candidate(
            content,
            "current_state",
            source_text,
            source=source,
            confidence=0.96,
            sensitivity="sensitive",
            reason="用户明确更正了当前所在位置，等待确认是否更新长期状态",
            memory_fields={
                "scope": "current_state",
                "location": clean_location,
            },
        )
        return {
            "status": "added" if added else "duplicate_candidate",
            "candidate": candidate,
        }

    def propose_from_text(
        self,
        source_text: str,
        *,
        explicit: bool = False,
        source: str = "conversation",
        source_role: str = "user",
    ) -> Dict[str, object]:
        if not self.enabled:
            return {"status": "disabled", "candidate": None}

        original = str(source_text).strip()
        if self._is_non_user_source(source_role, source) or self._looks_like_assistant_or_tool_text(original):
            return {
                "status": "skipped",
                "candidate": None,
                "reason": "non_user_or_execution_text",
            }
        content = self._strip_memory_request(original) if explicit else original
        if not content:
            return {"status": "skipped", "candidate": None, "reason": "empty"}

        sensitive = self._is_sensitive(content)
        if sensitive and not explicit:
            return {
                "status": "skipped_sensitive",
                "candidate": None,
                "reason": "sensitive_requires_explicit_request",
            }

        classification = self._classify_candidate(content, explicit=explicit)
        if classification is None:
            return {
                "status": "skipped",
                "candidate": None,
                "reason": "not_stable_long_term_information",
            }
        category, confidence, reason = classification

        memory_category = self.to_memory_category(category)
        duplicate, similarity = self.memory_manager.find_duplicate(
            content,
            category=memory_category,
        )
        if duplicate is not None:
            print(
                f"[MemoryGovernance] duplicate long-term id={duplicate.get('id')}",
                flush=True,
            )
            return {
                "status": "duplicate",
                "candidate": None,
                "duplicate_of": duplicate.get("id"),
                "similarity": similarity,
            }

        candidate, added = self.candidate_manager.add_candidate(
            content,
            category,
            original,
            source=source,
            confidence=1.0 if explicit else confidence,
            sensitivity="high" if sensitive else "normal",
            reason=(
                f"{reason}，仍需人工确认"
                if explicit
                else reason
            ),
        )
        return {
            "status": "added" if added else "duplicate_candidate",
            "candidate": candidate,
        }

    def accept_candidate(
        self,
        candidate_id: int,
        *,
        edited_content: Optional[str] = None,
        source: str = "user_confirmation",
    ) -> Dict[str, object]:
        candidate = self.candidate_manager.get(candidate_id)
        if candidate is None or candidate.get("status") != "pending":
            return {"status": "missing", "candidate": candidate}

        content = str(edited_content or candidate.get("content", "")).strip()
        if not content:
            return {"status": "invalid", "error": "empty_content"}
        category = self.to_memory_category(str(candidate.get("category", "other")))
        memory_fields = candidate.get("memory_fields", {})
        memory_fields = memory_fields if isinstance(memory_fields, dict) else {}
        if str(memory_fields.get("scope", "")) == "current_state":
            new_location = str(memory_fields.get("location", "")).strip()
            previous = next(
                (
                    item
                    for item in self.memory_manager.memories("active")
                    if str(item.get("scope", "")) == "current_state"
                    and str(item.get("location", "")).strip()
                    and str(item.get("location", "")).strip() != new_location
                ),
                None,
            )
            if previous is not None:
                conflict = self.memory_manager.create_relation_conflict(
                    previous,
                    content,
                    category,
                    source="memory_candidate",
                    relation="conflict",
                )
                accepted, changed = self.candidate_manager.accept(
                    candidate_id,
                    content=content,
                    conflict_with=int(conflict.get("id", 0)) or None,
                )
                if changed:
                    self.memory_manager.record_audit(
                        "accept_candidate_state_conflict",
                        f"candidate:{candidate_id}",
                        candidate,
                        accepted,
                        source,
                    )
                return {
                    "status": "conflict",
                    "candidate": accepted,
                    "conflict": conflict,
                    "relation": "current_state_conflict",
                }
        relation = self.assess_relation(content, category)

        if relation["relation"] in {"exact_duplicate", "near_duplicate"}:
            duplicate = relation.get("memory") or {}
            accepted, changed = self.candidate_manager.accept(
                candidate_id,
                content=content,
                duplicate_of=int(duplicate.get("id", 0)) or None,
            )
            if changed:
                self.memory_manager.record_audit(
                    "accept_candidate_duplicate",
                    f"candidate:{candidate_id}",
                    candidate,
                    accepted,
                    source,
                )
            return {
                "status": "duplicate",
                "candidate": accepted,
                "memory": duplicate,
                "relation": relation["relation"],
            }

        if relation["relation"] == "mergeable":
            old_memory = relation.get("memory") or {}
            conflict = self.memory_manager.create_relation_conflict(
                old_memory,
                content,
                category,
                source="memory_candidate",
                relation="mergeable",
            )
            accepted, changed = self.candidate_manager.accept(
                candidate_id,
                content=content,
                conflict_with=int(conflict.get("id", 0)) or None,
            )
            if changed:
                self.memory_manager.record_audit(
                    "accept_candidate_mergeable",
                    f"candidate:{candidate_id}",
                    candidate,
                    accepted,
                    source,
                )
            return {
                "status": "conflict",
                "candidate": accepted,
                "conflict": conflict,
                "relation": "mergeable",
            }

        result = self.memory_manager.add_memory(
            content,
            category=category,
            confidence=float(candidate.get("confidence", 0.85)),
            source="memory_candidate",
            scope=str(memory_fields.get("scope", "")) or None,
            location=str(memory_fields.get("location", "")) or None,
            valid_from=str(memory_fields.get("valid_from", "")) or None,
            valid_until=str(memory_fields.get("valid_until", "")) or None,
        )
        if result.get("status") == "error":
            return result
        conflict = result.get("conflict") if isinstance(result.get("conflict"), dict) else {}
        duplicate = result.get("memory") if result.get("status") == "duplicate" else {}
        accepted, changed = self.candidate_manager.accept(
            candidate_id,
            content=content,
            duplicate_of=(int(duplicate.get("id", 0)) or None) if duplicate else None,
            conflict_with=(int(conflict.get("id", 0)) or None) if conflict else None,
        )
        if changed:
            self.memory_manager.record_audit(
                "accept_candidate",
                f"candidate:{candidate_id}",
                candidate,
                accepted,
                source,
            )
        result["candidate"] = accepted
        return result

    def reject_candidate(
        self,
        candidate_id: int,
        *,
        source: str = "user_confirmation",
    ) -> Dict[str, object]:
        before = self.candidate_manager.get(candidate_id)
        candidate, changed = self.candidate_manager.reject(candidate_id)
        if candidate is None:
            return {"status": "missing"}
        if changed:
            self.memory_manager.record_audit(
                "reject_candidate",
                f"candidate:{candidate_id}",
                before,
                candidate,
                source,
            )
        return {"status": "rejected" if changed else "unchanged", "candidate": candidate}

    def reject_low_value(
        self,
        *,
        confidence_threshold: float = 0.65,
        source: str = "user_confirmation",
    ) -> Dict[str, object]:
        rejected: List[int] = []
        for candidate in self.candidate_manager.pending():
            confidence = float(candidate.get("confidence", 0.0))
            reason = str(candidate.get("reason", ""))
            if confidence >= confidence_threshold and reason != "旧版候选记忆":
                continue
            result = self.reject_candidate(int(candidate.get("id", 0)), source=source)
            if result.get("status") == "rejected":
                rejected.append(int(candidate.get("id", 0)))
        return {"status": "rejected", "candidate_ids": rejected, "count": len(rejected)}

    def assess_relation(self, content: str, category: str) -> Dict[str, object]:
        conflict = self.memory_manager.detect_conflict(content, category)
        if conflict is not None:
            return {"relation": "conflict", "memory": conflict, "score": 1.0}

        duplicate, score = self.memory_manager.find_duplicate(content, category=category)
        if duplicate is not None:
            return {
                "relation": "exact_duplicate" if score >= 0.96 else "near_duplicate",
                "memory": duplicate,
                "score": score,
            }

        best: Optional[Dict[str, object]] = None
        best_score = 0.0
        semantic = self.memory_manager.semantic_core(content)
        for item in self.memory_manager.memories("active", category=category):
            candidate_score = SequenceMatcher(
                None,
                semantic,
                self.memory_manager.semantic_core(str(item.get("content", ""))),
            ).ratio()
            if candidate_score > best_score:
                best = item
                best_score = candidate_score
        if best is not None and best_score >= 0.55:
            return {"relation": "mergeable", "memory": best, "score": best_score}
        return {"relation": "new", "memory": None, "score": best_score}

    def conflicts(self, status: str = "pending") -> List[Dict[str, object]]:
        result = []
        for item in self.memory_manager.conflicts(status):
            view = dict(item)
            old_memory = self.memory_manager.get(int(item.get("old_memory_id", 0)))
            view["old_memory"] = old_memory
            result.append(view)
        return result

    def resolve_conflict(
        self,
        conflict_id: int,
        resolution: str,
        *,
        merged_content: Optional[str] = None,
        source: str = "user_confirmation",
    ) -> Dict[str, object]:
        return self.memory_manager.resolve_conflict(
            conflict_id,
            resolution,
            merged_content=merged_content,
            source=source,
        )

    @staticmethod
    def to_memory_category(category: str) -> str:
        mapping = {"routine": "habit", "constraint": "rule", "current_state": "other"}
        return MemoryManager.normalize_category(mapping.get(category, category))

    @classmethod
    def _classify_candidate(
        cls,
        content: str,
        *,
        explicit: bool,
    ) -> Optional[Tuple[str, float, str]]:
        text = str(content).strip().strip("。.!！?？")
        lowered = text.lower()
        transient = cls._is_transient_or_question(text)
        if not explicit and transient:
            return None

        if explicit and transient:
            return (
                "other",
                0.72,
                "用户明确要求保存，但内容更像临时安排或当前问题",
            )

        rules = (
            ("goal", 0.92, "用户表达了长期目标", r"^我(?:以后|长期|最终)想.+|^我的长期目标是.+|^我想把.+(?:长期|一直).+(?:做下去|坚持下去)$"),
            ("routine", 0.88, "用户表达了稳定习惯", r"^我(?:一般|通常|习惯|每天|每周|周末).+"),
            ("preference", 0.88, "用户表达了稳定偏好", r"^我(?:一直|通常|一般)?(?:更)?(?:喜欢|不喜欢|偏好|更适合).+"),
            ("project", 0.86, "用户表达了长期项目状态", r"^我(?:正在|一直在|长期在).*(?:项目|roxyplan).+|^我的.+项目(?:长期|主要|目前).+|^roxyplan.+"),
            ("constraint", 0.86, "用户表达了持续约束", r"^(?:以后|今后).*(?:不要|必须|需要).+|^我(?:一直)?(?:不能|必须).+|^(?:codex|roxyplan).*(?:不要|必须).+|^不要自动生图$"),
        )
        for category, confidence, reason, pattern in rules:
            if re.fullmatch(pattern, lowered, flags=re.IGNORECASE):
                return category, confidence, reason

        if explicit:
            classified = MemoryManager.classify(text)
            category_map = {"habit": "routine", "rule": "constraint"}
            category = category_map.get(classified, classified)
            if category not in CANDIDATE_CATEGORIES:
                category = "other"
            return category, 1.0, "用户明确要求保存"
        return None

    @classmethod
    def _is_sensitive(cls, content: str) -> bool:
        return any(re.search(pattern, content, flags=re.IGNORECASE) for pattern in SENSITIVE_PATTERNS)

    @staticmethod
    def _is_non_user_source(source_role: str, source: str) -> bool:
        role = str(source_role or "user").strip().lower()
        origin = str(source or "").strip().lower()
        return role in {"assistant", "system", "tool", "agent", "execution"} or origin in {
            "assistant",
            "system",
            "tool",
            "agent",
            "execution",
            "tool_result",
        }

    @staticmethod
    def _looks_like_assistant_or_tool_text(content: str) -> bool:
        text = re.sub(r"\s+", "", str(content or "").strip())
        if not text:
            return False
        patterns = (
            r"^(?:好[，,:：]?我(?:已经|已)?记住了)[：,:：]",
            r"^(?:我(?:已经|已)?记住了)[：,:：]",
            r"^(?:目前|现在|本轮|这次).{0,30}(?:还没有|未|没有)(?:执行|完成|成功)",
            r"^(?:工具|操作|请求).{0,20}(?:不可用|失败|未执行|没有执行)",
            r"^(?:已|已经)(?:添加|加入|保存|完成|记录|更新|删除|归档|恢复).{0,24}(?:计划|行动|记忆|操作|任务)?",
        )
        return any(re.search(pattern, text, flags=re.IGNORECASE) for pattern in patterns)

    @staticmethod
    def _is_transient_or_question(content: str) -> bool:
        if re.search(r"(?:怎么|如何|为什么|什么|多少|能不能|可以吗|吗$|呢$|[?？])", content):
            return True
        if re.match(
            r"^(?:今天|今晚|明天|上午|下午|晚上|这次|刚才|刚刚|一会儿|待会儿|这周|本周|下周)",
            content,
        ):
            return True
        if re.search(r"我(?:现在|今天)(?:很|有点|不太)?(?:累|难过|焦虑|开心|烦|困)", content):
            return True
        if re.search(r"(?:完成了|做完了|测试了|学习了|记录一下|行动记录)", content):
            return True
        return False

    @staticmethod
    def _strip_memory_request(text: str) -> str:
        value = str(text).strip()
        patterns = (
            r"^(?:请|帮我)?记住[：:，,\s]*(.+)$",
            r"^以后你要记得[：:，,\s]*(.+)$",
            r"^把[：:\s]*(.+?)\s*记住$",
        )
        for pattern in patterns:
            match = re.fullmatch(pattern, value, flags=re.IGNORECASE)
            if match:
                return match.group(1).strip()
        return value
