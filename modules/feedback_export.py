"""轻量内测反馈导出：先出清单、明确同意后写一个 ZIP，没有后台上传。

设计边界：

* 调用方通过 ``data_root`` 显式给出实例根，模块不推断、不回退项目目录；实例根下
  沿用既有布局 ``data/private/``、``logs/``、``memory.json``，与
  ``MemoryService.from_data_root`` 的目录语义一致。
* 只导出白名单内的数据文件。``llm_secrets.json``、``.env``、私人配置与凭据备份
  不在白名单里，因此不存在“顺手打包进去”的路径。
* 结构化内容先按键名移除凭据字段，字符串值再按模式遮蔽可识别凭据；不承诺万能脱敏。
* 复用既有日志与轮转结果，不新增采集总线、定时器、后台同步或网络调用。
* 目录逃逸、符号链接与 junction 一律在收集阶段拒绝。
* ``plan()`` 只读；``export()`` 只写调用方给出的目标 ZIP，从不写实例根。

已有开发日志是摘要与指纹（``modules/development_log.py`` 的字段白名单），
聊天、成长与记忆才是正文，所以导出说明必须写明：日志无法还原完整模型输入输出
与工具原文，本次导出也不修改任何既有数据。
"""
from __future__ import annotations

import json
import platform
import re
import sys
import zipfile
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple


SCHEMA_VERSION = "1.0"

SOURCE_REAL = "real_user"
SOURCE_ENGINEERING = "engineering"
SOURCE_STUDY = "user_study"
SOURCE_LABELS = {
    SOURCE_REAL: "真实使用",
    SOURCE_ENGINEERING: "工程或模拟",
    SOURCE_STUDY: "真实用户体验（日期手动推进）",
}

CATEGORY_FEEDBACK = "feedback"
CATEGORY_CHAT = "chat"
CATEGORY_GROWTH = "growth"
CATEGORY_DIAGNOSTICS = "diagnostics"
CATEGORY_MEMORY = "memory"
CATEGORY_META = "meta"

CATEGORY_LABELS = {
    CATEGORY_FEEDBACK: "反馈文字",
    CATEGORY_CHAT: "已保存的聊天",
    CATEGORY_GROWTH: "学习记录",
    CATEGORY_DIAGNOSTICS: "诊断、用量与运行日志",
    CATEGORY_MEMORY: "长期记忆",
    CATEGORY_META: "版本、环境与清单",
}

# 导出选项在界面上的说明；正文类选项必须写出“内容会交给开发者”。
CATEGORY_HINTS = {
    CATEGORY_CHAT: "对话正文会交给开发者，默认不选",
    CATEGORY_GROWTH: "计划、行动、时长与已保存复盘正文会交给开发者",
    CATEGORY_DIAGNOSTICS: "摘要、计数、指纹与轮转日志，不含对话正文",
    CATEGORY_MEMORY: "记忆正文会交给开发者，默认不选",
}

STATUS_INCLUDED = "included"
STATUS_MISSING = "missing"
STATUS_UNREADABLE = "unreadable"
STATUS_SKIPPED = "skipped"
STATUS_EMPTY = "empty"

STATUS_LABELS = {
    STATUS_INCLUDED: "将包含",
    STATUS_MISSING: "缺失",
    STATUS_UNREADABLE: "损坏或无法读取",
    STATUS_SKIPPED: "已跳过",
    STATUS_EMPTY: "范围内为空",
}

DEFAULT_RANGE_DAYS = 7

RUN_META_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{3,63}\Z")
RUN_DIR = re.compile(r"run-(\d{8})T\d{6}-[0-9a-f]{32}\Z")

# 只有明确表示凭据的键名才移除。像 prompt_tokens / completion_tokens 这种计数
# 字段必须原样保留，否则导出包会把真正有用的用量数据删掉。
_CREDENTIAL_KEY = re.compile(
    r"(?:api[_-]?key|apikey|secret|password|passwd|credential|authorization"
    r"|auth[_-]?header|bearer|private[_-]?key|access[_-]?key"
    r"|access[_-]?token|refresh[_-]?token|auth[_-]?token|id[_-]?token|session[_-]?token"
    r"|^tokens?$)",
    re.IGNORECASE,
)
_CREDENTIAL_TEXT = (
    # 前缀要有词边界，否则 risk-management 这类普通词会被当成 sk- 密钥遮蔽。
    re.compile(r"(?<![A-Za-z0-9])sk-[A-Za-z0-9_\-]{12,}"),
    re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._\-]{12,}"),
    re.compile(
        r"(?i)\b(?:api[_-]?key|apikey|access[_-]?token|secret|password)\b"
        r"\s*[:=]\s*[\"']?[^\s\"',;]{6,}"
    ),
    re.compile(r"(?i)\bauthorization\b\s*[:=]\s*[^\s,;]{6,}"),
)
_MASK = "[已遮蔽]"
_REDACTED_KEY_VALUE = "[已移除的凭据字段]"


class FeedbackExportError(Exception):
    """Raised when an export cannot be planned or written at all."""


@dataclass(frozen=True)
class RunMeta:
    """运行元信息；由调用方显式提供，模块不生成、不猜测。"""

    version: str
    instance_id: str
    source: str = SOURCE_ENGINEERING

    def validation_error(self) -> Optional[str]:
        if not str(self.version or "").strip():
            return "缺少应用版本"
        if not RUN_META_ID.match(str(self.instance_id or "")):
            return "实例匿名 ID 缺失或格式不正确"
        if self.source not in SOURCE_LABELS:
            return "来源必须标记为真实使用或工程模拟"
        return None


@dataclass(frozen=True)
class ExportScope:
    """一次导出的日期范围与内容类别。正文类别默认不包含。"""

    start: date
    end: date
    include_chat: bool = False
    include_growth: bool = True
    include_diagnostics: bool = True
    include_memory: bool = False

    def validate(self) -> Optional[str]:
        if self.end < self.start:
            return "结束日期早于开始日期"
        return None

    def covers(self, value: Optional[date]) -> bool:
        return value is not None and self.start <= value <= self.end

    def selected_categories(self) -> Tuple[str, ...]:
        chosen = [CATEGORY_FEEDBACK, CATEGORY_META]
        if self.include_chat:
            chosen.append(CATEGORY_CHAT)
        if self.include_growth:
            chosen.append(CATEGORY_GROWTH)
        if self.include_diagnostics:
            chosen.append(CATEGORY_DIAGNOSTICS)
        if self.include_memory:
            chosen.append(CATEGORY_MEMORY)
        return tuple(chosen)

    def as_document(self) -> Dict[str, object]:
        return {
            "start": self.start.isoformat(),
            "end": self.end.isoformat(),
            "categories": {
                CATEGORY_CHAT: self.include_chat,
                CATEGORY_GROWTH: self.include_growth,
                CATEGORY_DIAGNOSTICS: self.include_diagnostics,
                CATEGORY_MEMORY: self.include_memory,
            },
        }


@dataclass(frozen=True)
class FeedbackText:
    """用户填写的反馈正文。"""

    problem: str
    expected: str
    actual: str

    def is_empty(self) -> bool:
        return not any(str(part or "").strip() for part in (self.problem, self.expected, self.actual))

    def as_document(self) -> Dict[str, str]:
        return {
            "problem": str(self.problem or ""),
            "expected": str(self.expected or ""),
            "actual": str(self.actual or ""),
        }


@dataclass(frozen=True)
class PlannedEntry:
    """清单里的一行：某个文件会被包含、缺失、损坏还是跳过。"""

    category: str
    archive_path: str
    status: str
    detail: str = ""
    source_name: str = ""
    relpath: str = ""
    # 该候选项被允许存在的根目录；写入前要再核一次，防止计划与写入之间被换掉。
    root: Optional[Path] = None

    @property
    def category_label(self) -> str:
        return CATEGORY_LABELS.get(self.category, self.category)

    @property
    def status_label(self) -> str:
        return STATUS_LABELS.get(self.status, self.status)

    def as_document(self) -> Dict[str, str]:
        """清单展示用字段：只出现实例根内的相对位置，不含本机绝对路径。"""
        document = {
            "category": self.category,
            "archive_path": self.archive_path,
            "status": self.status,
            "detail": self.detail,
        }
        if self.relpath:
            document["source"] = self.relpath
        return document


@dataclass(frozen=True)
class ExportPlan:
    """写入之前展示给用户的清单。"""

    scope: ExportScope
    entries: Tuple[PlannedEntry, ...]
    notes: Tuple[str, ...] = ()

    @property
    def included(self) -> Tuple[PlannedEntry, ...]:
        return tuple(entry for entry in self.entries if entry.status == STATUS_INCLUDED)

    @property
    def problems(self) -> Tuple[PlannedEntry, ...]:
        return tuple(
            entry for entry in self.entries
            if entry.status in (STATUS_MISSING, STATUS_UNREADABLE)
        )

    def has_content(self) -> bool:
        return bool(self.included)


@dataclass(frozen=True)
class ExportOutcome:
    """一次导出尝试的真实结果；失败时绝不假装成功。"""

    ok: bool
    target: Optional[Path] = None
    error: str = ""
    file_count: int = 0
    byte_count: int = 0
    redacted_fields: int = 0
    entries: Tuple[PlannedEntry, ...] = ()


def default_scope(today: Optional[date] = None, *, days: int = DEFAULT_RANGE_DAYS) -> ExportScope:
    reference = today or date.today()
    return ExportScope(start=reference - timedelta(days=max(0, days - 1)), end=reference)


def collect_environment(extra: Optional[Mapping[str, object]] = None) -> Dict[str, object]:
    """Only what a maintainer needs; never user names, paths or host names."""
    info: Dict[str, object] = {
        "platform": platform.platform(),
        "python": platform.python_version(),
        "executable_bits": 64 if sys.maxsize > 2 ** 32 else 32,
    }
    if extra:
        for key, value in extra.items():
            if isinstance(key, str) and key and isinstance(value, (str, int, float, bool)):
                info[key] = value
    return info


def _within(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
    except ValueError:
        return False
    return True


def _parse_datetime(value: object) -> Optional[datetime]:
    text = str(value or "").strip()
    if not text:
        return None
    candidate = text[:-1] + "+00:00" if text.endswith("Z") else text
    try:
        parsed = datetime.fromisoformat(candidate)
    except ValueError:
        return None
    if parsed.tzinfo is not None:
        parsed = parsed.astimezone(timezone.utc).replace(tzinfo=None)
    return parsed


def _parse_date(value: object) -> Optional[date]:
    text = str(value or "").strip()
    if len(text) < 10:
        return None
    try:
        parsed = date.fromisoformat(text[:10])
    except ValueError:
        return None
    return parsed if parsed.isoformat() == text[:10] else None


def _scrub_text(value: str) -> Tuple[str, int]:
    hits = 0
    text = value
    for pattern in _CREDENTIAL_TEXT:
        text, found = pattern.subn(_MASK, text)
        hits += found
    return text, hits


def _sanitize(value: Any, counters: Dict[str, int]) -> Any:
    """按结构移除凭据字段，再遮蔽字符串里可识别的凭据。"""
    if isinstance(value, Mapping):
        cleaned: Dict[str, Any] = {}
        for key, item in value.items():
            name = str(key)
            if _CREDENTIAL_KEY.search(name):
                cleaned[name] = _REDACTED_KEY_VALUE
                counters["fields"] += 1
                continue
            cleaned[name] = _sanitize(item, counters)
        return cleaned
    if isinstance(value, (list, tuple)):
        return [_sanitize(item, counters) for item in value]
    if isinstance(value, str):
        text, hits = _scrub_text(value)
        counters["values"] += hits
        return text
    return value


def _read_json(path: Path) -> Tuple[Optional[Any], str]:
    try:
        with path.open("r", encoding="utf-8") as stream:
            return json.load(stream), ""
    except FileNotFoundError:
        return None, "文件不存在"
    except json.JSONDecodeError as error:
        return None, f"JSON 无法解析（第 {error.lineno} 行）"
    except UnicodeDecodeError:
        return None, "文件不是 UTF-8 文本"
    except OSError as error:
        return None, f"无法读取（{type(error).__name__}）"


class FeedbackExportService:
    """从一个显式实例根收集反馈包。构造后即确定可用性，不延迟猜路径。"""

    def __init__(self, data_root: Optional[Path], run_meta: Optional[RunMeta]) -> None:
        self.data_root = Path(data_root) if data_root is not None else None
        self.run_meta = run_meta
        self._unavailable = self._check_available()

    def suggested_scope(self) -> ExportScope:
        scope = default_scope()
        if self.available and self.run_meta.source == SOURCE_STUDY:
            from modules.runtime_paths import read_json_strict
            state = read_json_strict(self.data_root / "simulation_state.json")
            business_day = date.fromisoformat(state["business_date"])
            # Business-dated growth and wall-clock chat/diagnostics must both
            # be visible in the consent list after a date advance.
            return ExportScope(min(scope.start, business_day), max(scope.end, business_day))
        return scope

    # ---------------------------------------------------------------- 可用性

    def _check_available(self) -> Optional[str]:
        if self.data_root is None:
            return "未接入数据根，导出不可用。"
        if self.run_meta is None:
            return "缺少运行元信息，导出不可用。"
        problem = self.run_meta.validation_error()
        if problem:
            return f"{problem}，导出不可用。"
        try:
            if not self.data_root.is_dir():
                return "数据根不存在或不是目录，导出不可用。"
        except OSError:
            return "数据根无法访问，导出不可用。"
        return None

    @property
    def available(self) -> bool:
        return self._unavailable is None

    @property
    def unavailable_reason(self) -> str:
        return self._unavailable or ""

    @property
    def private_dir(self) -> Path:
        return Path(self.data_root) / "data" / "private"

    @property
    def logs_dir(self) -> Path:
        return Path(self.data_root) / "logs"

    def environment(self, extra: Optional[Mapping[str, object]] = None) -> Dict[str, object]:
        return collect_environment(extra)

    # ------------------------------------------------------------------ 清单

    def _relative_name(self, resolved: Path) -> str:
        """清单里只写实例根内的相对位置，本机绝对路径不进入导出内容。"""
        try:
            return resolved.relative_to(Path(self.data_root).resolve()).as_posix()
        except (ValueError, OSError):
            return resolved.name

    def _inspect(
        self, category: str, source: Path, archive_path: str, root: Optional[Path] = None,
    ) -> PlannedEntry:
        """只读检查一个候选项；任何形式的逃逸都在这里被拒绝。

        ``root`` 是该候选项允许存在的根目录：用量文件在 ``data/private/``，运行日志在
        ``logs/``，长期记忆在实例根。按类别一刀切会把合法文件的根判错。
        """
        allowed = Path(root) if root is not None else self.private_dir
        name = str(source)
        if source.is_symlink():
            return PlannedEntry(
                category, archive_path, STATUS_SKIPPED, "符号链接，已跳过", name, "", allowed
            )
        try:
            resolved = source.resolve(strict=True)
        except FileNotFoundError:
            return PlannedEntry(
                category, archive_path, STATUS_MISSING, "文件不存在", name, "", allowed
            )
        except OSError as error:
            return PlannedEntry(
                category, archive_path, STATUS_UNREADABLE,
                f"无法解析路径（{type(error).__name__}）", name, "", allowed,
            )
        relative = self._relative_name(resolved)
        if not resolved.is_file():
            return PlannedEntry(
                category, archive_path, STATUS_SKIPPED, "不是普通文件，已跳过",
                name, relative, allowed,
            )
        if (not _within(allowed.resolve(), Path(self.data_root).resolve())
                or not _within(resolved, allowed.resolve())):
            return PlannedEntry(
                category, archive_path, STATUS_SKIPPED,
                "路径超出允许的实例根，已跳过", name, relative, allowed,
            )
        return PlannedEntry(category, archive_path, STATUS_INCLUDED, "", name, relative, allowed)

    def _inspected_json(
        self, category: str, source: Path, archive_path: str, root: Optional[Path] = None,
    ) -> Tuple[PlannedEntry, Optional[Any]]:
        entry = self._inspect(category, source, archive_path, root)
        if entry.status != STATUS_INCLUDED:
            return entry, None
        document, problem = _read_json(Path(entry.source_name))
        if problem:
            return (_restatus(entry, STATUS_UNREADABLE, problem), None)
        return entry, document

    def _development_log_entries(self, scope: ExportScope) -> List[PlannedEntry]:
        """轮转日志按 run 目录收集；目录名里的日期决定是否落在范围内。"""
        entries: List[PlannedEntry] = []
        root = self.logs_dir / "development"
        if not root.is_dir():
            return entries
        try:
            run_dirs = sorted(path for path in root.iterdir() if path.is_dir())
        except OSError:
            return entries
        outside = 0
        for run_dir in run_dirs:
            match = RUN_DIR.match(run_dir.name)
            if match is None:
                continue
            try:
                run_date = datetime.strptime(match.group(1), "%Y%m%d").date()
            except ValueError:
                continue
            if not scope.covers(run_date):
                outside += 1
                continue
            try:
                files = sorted(path for path in run_dir.iterdir() if path.is_file())
            except OSError:
                entries.append(PlannedEntry(
                    CATEGORY_DIAGNOSTICS, f"logs/development/{run_dir.name}/",
                    STATUS_UNREADABLE, "无法读取该运行目录", str(run_dir),
                    f"logs/development/{run_dir.name}/", self.logs_dir,
                ))
                continue
            for file in files:
                # 只认既有轮转命名，其他文件（含任何落进来的外部文件）不导出。
                if not re.fullmatch(r"(?:events(?:\.[0-9]{6})?\.jsonl|runtime(?:\.[0-9]{6})?\.log)", file.name):
                    continue
                archive_path = f"logs/development/{run_dir.name}/{file.name}"
                # 运行目录名里已经带日期，文件本身不再按行过滤日期。
                entries.append(self._inspect(CATEGORY_DIAGNOSTICS, file, archive_path, self.logs_dir))
        if outside:
            entries.append(PlannedEntry(
                CATEGORY_DIAGNOSTICS, "logs/development/",
                STATUS_SKIPPED, f"{outside} 个运行目录不在所选日期范围内", str(root),
                "logs/development/", self.logs_dir,
            ))
        return entries

    def plan(self, scope: ExportScope) -> ExportPlan:
        """只读地列出待导出内容；不写任何文件。"""
        if not self.available:
            return ExportPlan(scope=scope, entries=(), notes=(self.unavailable_reason,))
        problem = scope.validate()
        if problem:
            return ExportPlan(scope=scope, entries=(), notes=(f"{problem}。",))

        entries: List[PlannedEntry] = []
        notes: List[str] = []

        if scope.include_chat:
            history_entry, history_document = self._inspected_json(
                CATEGORY_CHAT, self.private_dir / "chat_history.json", "chat/chat_history.json"
            )
            history_entry, kept_ids = self._filter_chat_entry(
                history_entry, history_document, scope, notes
            )
            entries.append(history_entry)
            # 摘要按 session_id 与仍在范围内的会话关联；没有关联的摘要不导出，
            # 因为拿不到能证明它在范围内的依据。
            summaries_entry, summaries_document = self._inspected_json(
                CATEGORY_CHAT, self.private_dir / "chat_summaries.json", "chat/chat_summaries.json"
            )
            entries.append(
                self._filter_summaries_entry(summaries_entry, summaries_document, kept_ids)
            )
        if scope.include_growth:
            for name in ("today_plan.json", "action_log.json", "growth_log.json"):
                entry, document = self._inspected_json(
                    CATEGORY_GROWTH, self.private_dir / name, f"growth/{name}"
                )
                entries.append(self._filter_day_store_entry(entry, document, scope, name))
        if scope.include_diagnostics:
            entry, document = self._inspected_json(
                CATEGORY_DIAGNOSTICS, self.private_dir / "model_usage.json",
                "diagnostics/model_usage.json", self.private_dir,
            )
            entries.append(self._filter_usage_entry(entry, document, scope, notes))
            entries.append(self._inspect(
                CATEGORY_DIAGNOSTICS, self.logs_dir / "interaction_diagnostics.jsonl",
                "diagnostics/interaction_diagnostics.jsonl", self.logs_dir,
            ))
            entries.extend(self._development_log_entries(scope))
        if scope.include_memory:
            entries.append(self._inspect(
                CATEGORY_MEMORY, Path(self.data_root) / "memory.json", "memory/memory.json",
                Path(self.data_root),
            ))

        notes.append(
            "开发日志只有摘要、计数与指纹，无法还原完整模型输入输出或工具原文；"
            "聊天、成长与记忆类目才是正文。"
        )
        notes.append(
            "日志沿用既有轮转与容量限制，已轮转移除的旧文件不会出现在本次导出里。"
        )
        notes.append("开发日志按运行目录的 UTC 启动日期选择，整次运行保留；跨午夜运行可能含其他日期的事件。")
        if not scope.include_chat and not scope.include_memory:
            notes.append("本次未选择聊天与长期记忆，不含这两类正文。")
        return ExportPlan(scope=scope, entries=tuple(entries), notes=tuple(notes))

    def _filter_chat_entry(
        self, entry: PlannedEntry, document: Optional[Any], scope: ExportScope,
        notes: List[str],
    ) -> Tuple[PlannedEntry, set]:
        """返回清单行与仍在范围内的 session_id 集合，供摘要关联使用。"""
        if entry.status != STATUS_INCLUDED or not isinstance(document, Mapping):
            return entry, set()
        sessions = document.get("sessions")
        if not isinstance(sessions, list):
            return _restatus(entry, STATUS_UNREADABLE, "文件结构不是预期的会话列表"), set()
        kept_ids, undated = set(), 0
        for session in sessions:
            if not isinstance(session, Mapping):
                undated += 1
                continue
            if _chat_session_in_scope(session, scope):
                kept_ids.add(session.get("session_id"))
            elif not _chat_session_has_time(session):
                # 没有可解析时间就无从按日期关联，如实计入并说明，不悄悄丢弃。
                undated += 1
        if undated:
            notes.append(f"聊天：{undated} 条记录没有可解析的时间，无法按日期关联，未包含。")
        if not kept_ids:
            return _restatus(entry, STATUS_EMPTY, "所选日期范围内没有会话"), set()
        return (
            _restatus(entry, STATUS_INCLUDED, f"{len(kept_ids)} 个会话（整段保留，不逐条裁剪）"),
            kept_ids,
        )

    def _filter_summaries_entry(
        self, entry: PlannedEntry, document: Optional[Any], kept_ids: set,
    ) -> PlannedEntry:
        if entry.status != STATUS_INCLUDED or not isinstance(document, Mapping):
            return entry
        summaries = document.get("summaries")
        if not isinstance(summaries, Mapping):
            return _restatus(entry, STATUS_UNREADABLE, "文件结构不是预期的 summaries 映射")
        kept = sum(1 for key in summaries if key in kept_ids)
        if kept == 0:
            return _restatus(entry, STATUS_EMPTY, "没有落在所选范围内的会话摘要")
        return _restatus(entry, STATUS_INCLUDED, f"{kept} 条会话摘要")

    def _filter_day_store_entry(
        self, entry: PlannedEntry, document: Optional[Any], scope: ExportScope, name: str,
    ) -> PlannedEntry:
        if entry.status != STATUS_INCLUDED or not isinstance(document, Mapping):
            return entry
        store_key = "entries" if name == "growth_log.json" else "days"
        store = document.get(store_key)
        if not isinstance(store, Mapping):
            return _restatus(entry, STATUS_UNREADABLE, f"文件结构不是预期的 {store_key} 映射")
        kept = sum(1 for key in store if scope.covers(_parse_date(key)))
        if kept == 0:
            return _restatus(entry, STATUS_EMPTY, "所选日期范围内没有记录")
        return _restatus(entry, STATUS_INCLUDED, f"{kept} 天记录")

    def _filter_usage_entry(
        self, entry: PlannedEntry, document: Optional[Any], scope: ExportScope,
        notes: List[str],
    ) -> PlannedEntry:
        if entry.status != STATUS_INCLUDED:
            return entry
        notes.append(
            "模型用量是累计汇总文件，按整份包含；它覆盖的时间范围见文件内的 updated_at 与 recent 列表。"
        )
        return _restatus(
            entry, STATUS_INCLUDED, "整份包含（累计汇总，未按日期裁剪，范围见文件内容）"
        )

    # ------------------------------------------------------------------ 导出

    def export(
        self,
        plan: ExportPlan,
        feedback: FeedbackText,
        target: Path,
        *,
        environment: Optional[Mapping[str, object]] = None,
    ) -> ExportOutcome:
        """写出 ZIP。失败时返回 ok=False 并清理临时文件，绝不声称成功。"""
        if not self.available:
            return ExportOutcome(ok=False, error=self.unavailable_reason)
        if not plan.included:
            return ExportOutcome(ok=False, error="清单里没有可导出的内容。")

        destination = Path(target)
        if destination.suffix.lower() != ".zip":
            destination = destination.with_suffix(".zip")
        meta = self.run_meta
        assert meta is not None  # available 已保证
        counters: Dict[str, int] = {"fields": 0, "values": 0}
        written: List[PlannedEntry] = []
        total_bytes = 0
        temporary = destination.with_name(destination.name + ".part")

        try:
            destination.parent.mkdir(parents=True, exist_ok=True)
            with zipfile.ZipFile(temporary, "w", zipfile.ZIP_DEFLATED) as archive:
                payloads = self._build_payloads(plan, feedback, environment or {}, counters)
                for name, data in payloads:
                    archive.writestr(name, data)
                    total_bytes += len(data)
                for entry in plan.included:
                    data, problem = self._render_entry(entry, plan.scope, counters)
                    if problem:
                        # 计划之后文件变了：如实记录，不写坏内容。
                        written.append(_restatus(entry, STATUS_UNREADABLE, problem))
                        continue
                    if data is None:
                        written.append(_restatus(entry, STATUS_EMPTY, "导出时范围内已无内容"))
                        continue
                    archive.writestr(entry.archive_path, data)
                    total_bytes += len(data)
                    written.append(entry)
                archive.writestr(
                    "manifest.json",
                    json.dumps(
                        self._manifest_document(plan, written, total_bytes, counters),
                        ensure_ascii=False, indent=2,
                    ).encode("utf-8"),
                )
            temporary.replace(destination)
        except (OSError, zipfile.BadZipFile, ValueError) as error:
            _discard(temporary)
            return ExportOutcome(
                ok=False, target=None, error=f"写入失败（{type(error).__name__}）：{destination}",
                entries=tuple(written),
            )
        except Exception:
            _discard(temporary)
            raise

        included = tuple(entry for entry in written if entry.status == STATUS_INCLUDED)
        return ExportOutcome(
            ok=True,
            target=destination,
            file_count=len(included),
            byte_count=total_bytes,
            redacted_fields=counters["fields"] + counters["values"],
            entries=tuple(written),
        )

    def _build_payloads(
        self, plan: ExportPlan, feedback: FeedbackText, environment: Mapping[str, object],
        counters: Dict[str, int],
    ) -> List[Tuple[str, bytes]]:
        meta = self.run_meta
        assert meta is not None
        source_label = SOURCE_LABELS.get(meta.source, meta.source)
        feedback_document = {
            "schema_version": SCHEMA_VERSION,
            "source": meta.source,
            "source_label": source_label,
            "app_version": meta.version,
            "instance_id": meta.instance_id,
            "export_scope": plan.scope.as_document(),
            "feedback": feedback.as_document(),
            "note": (
                "本包由用户在 RoxyPlan 设置中主动导出；没有后台上传，"
                "日志类内容为摘要与指纹，无法还原完整模型输入输出或工具原文。"
            ),
        }
        environment_document = {
            "schema_version": SCHEMA_VERSION,
            "app_version": meta.version,
            "instance_id": meta.instance_id,
            "source": meta.source,
            "source_label": source_label,
            "captured_at": datetime.now().astimezone().isoformat(timespec="seconds"),
            "environment": collect_environment(environment),
        }
        return [
            ("feedback.json", _dump(feedback_document, counters)),
            ("environment.json", _dump(environment_document, counters)),
            ("README.txt", _readme_text(plan, feedback, meta.source).encode("utf-8")),
        ]

    def _render_entry(
        self, entry: PlannedEntry, scope: ExportScope, counters: Dict[str, int],
    ) -> Tuple[Optional[bytes], str]:
        source = Path(entry.source_name)
        if source.is_symlink():
            return None, "导出时该路径变成了符号链接，已跳过"
        allowed = Path(entry.root) if entry.root is not None else self.private_dir
        try:
            resolved = source.resolve(strict=True)
        except FileNotFoundError:
            return None, "导出时文件已不存在"
        except OSError:
            return None, "导出时无法解析该路径"
        if (not _within(allowed.resolve(), Path(self.data_root).resolve())
                or not _within(resolved, allowed.resolve()) or not resolved.is_file()):
            return None, "导出时该路径超出允许的实例根，已跳过"

        if entry.category == CATEGORY_CHAT:
            return self._render_chat(resolved, scope, counters)
        if entry.category == CATEGORY_GROWTH:
            return self._render_day_store(resolved, scope, counters)
        if entry.category == CATEGORY_DIAGNOSTICS:
            return self._render_diagnostics(resolved, scope, counters)
        if entry.category == CATEGORY_MEMORY:
            document, problem = _read_json(resolved)
            if problem:
                return None, problem
            return _dump(document, counters), ""
        return resolved.read_bytes(), ""

    def _kept_session_ids(self, scope: ExportScope) -> set:
        document, problem = _read_json(self.private_dir / "chat_history.json")
        if problem or not isinstance(document, Mapping):
            return set()
        sessions = document.get("sessions")
        if not isinstance(sessions, list):
            return set()
        return {
            session.get("session_id") for session in sessions
            if isinstance(session, Mapping)
            and session.get("session_id")
            and _chat_session_in_scope(session, scope)
        }

    def _render_chat(
        self, path: Path, scope: ExportScope, counters: Dict[str, int],
    ) -> Tuple[Optional[bytes], str]:
        document, problem = _read_json(path)
        if problem:
            return None, problem
        if not isinstance(document, Mapping):
            return None, "文件结构不是预期的对象"
        if path.name == "chat_summaries.json":
            # 摘要通过 session_id 与仍在范围内的会话关联，关联不上就不写。
            kept_ids = self._kept_session_ids(scope)
            summaries = document.get("summaries")
            if not isinstance(summaries, Mapping):
                return None, "文件结构不是预期的 summaries 映射"
            kept = {key: value for key, value in summaries.items() if key in kept_ids}
            if not kept:
                return None, ""
            payload = dict(document)
            payload["summaries"] = kept
            return _dump(payload, counters), ""
        sessions = document.get("sessions")
        if not isinstance(sessions, list):
            return None, "文件结构不是预期的会话列表"
        kept = [s for s in sessions if isinstance(s, Mapping) and _chat_session_in_scope(s, scope)]
        if not kept:
            return None, ""
        payload = {"version": document.get("version", 1), "sessions": kept}
        return _dump(payload, counters), ""

    def _render_day_store(
        self, path: Path, scope: ExportScope, counters: Dict[str, int],
    ) -> Tuple[Optional[bytes], str]:
        document, problem = _read_json(path)
        if problem:
            return None, problem
        if not isinstance(document, Mapping):
            return None, "文件结构不是预期的映射"
        store_key = "entries" if path.name == "growth_log.json" else "days"
        store = document.get(store_key)
        if not isinstance(store, Mapping):
            return None, f"文件结构不是预期的 {store_key} 映射"
        kept = {key: value for key, value in store.items() if scope.covers(_parse_date(key))}
        if not kept:
            return None, ""
        payload = dict(document)
        payload[store_key] = kept
        return _dump(payload, counters), ""

    def _render_diagnostics(
        self, path: Path, scope: ExportScope, counters: Dict[str, int],
    ) -> Tuple[Optional[bytes], str]:
        """三类诊断数据格式不同，按各自的真实结构处理，不硬套同一套规则。

        * ``logs/development/`` 下的事件日志用 ``timestamp`` 字段，运行日志是纯文本；
          它们的日期由所在运行目录名决定，不再逐行过滤。
        * ``logs/interaction_diagnostics.jsonl`` 用 ``created_at``，按所选日期逐行过滤。
        * 其余 JSON（用量）是累计汇总，整份保留。
        """
        if _within(path, self.logs_dir / "development"):
            if path.name.endswith(".log"):
                return self._render_text_log(path, counters)
            return self._render_event_log(path, counters)
        if path.suffix == ".jsonl":
            return self._render_dated_jsonl(path, scope, counters)
        document, problem = _read_json(path)
        if problem:
            return None, problem
        return _dump(document, counters), ""

    def _render_text_log(self, path: Path, counters: Dict[str, int]) -> Tuple[Optional[bytes], str]:
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            return None, "文件不是 UTF-8 文本"
        except OSError as error:
            return None, f"无法读取（{type(error).__name__}）"
        if not text.strip():
            return None, ""
        lines = []
        for line in text.splitlines():
            masked, hits = _scrub_text(line)
            counters["values"] += hits
            lines.append(masked)
        header = (
            "# 运行日志是既有开发日志的可读版本，内容为摘要与指纹，"
            "无法还原完整模型输入输出或工具原文。\n"
        )
        return (header + "\n".join(lines) + "\n").encode("utf-8"), ""

    def _render_event_log(self, path: Path, counters: Dict[str, int]) -> Tuple[Optional[bytes], str]:
        kept: List[str] = []
        unreadable = 0
        try:
            with path.open("r", encoding="utf-8") as stream:
                for line in stream:
                    stripped = line.strip()
                    if not stripped:
                        continue
                    try:
                        record = json.loads(stripped)
                    except json.JSONDecodeError:
                        unreadable += 1
                        continue
                    if not isinstance(record, Mapping):
                        unreadable += 1
                        continue
                    kept.append(json.dumps(_sanitize(record, counters), ensure_ascii=False))
        except UnicodeDecodeError:
            return None, "文件不是 UTF-8 文本"
        except OSError as error:
            return None, f"无法读取（{type(error).__name__}）"
        if not kept:
            return None, ""
        header = (
            "# 事件日志为既有开发日志的字段白名单输出，已经是摘要与指纹；"
            "日期由所在运行目录名决定。\n"
            f"# 无法解析的行 {unreadable} 行未包含。\n"
        )
        return (header + "\n".join(kept) + "\n").encode("utf-8"), ""

    def _render_dated_jsonl(
        self, path: Path, scope: ExportScope, counters: Dict[str, int],
    ) -> Tuple[Optional[bytes], str]:
        kept: List[str] = []
        unreadable = 0
        undated = 0
        try:
            with path.open("r", encoding="utf-8") as stream:
                for line in stream:
                    stripped = line.strip()
                    if not stripped:
                        continue
                    try:
                        record = json.loads(stripped)
                    except json.JSONDecodeError:
                        unreadable += 1
                        continue
                    if not isinstance(record, Mapping):
                        unreadable += 1
                        continue
                    stamped = _parse_datetime(record.get("created_at"))
                    if stamped is None:
                        undated += 1
                        continue
                    if scope.covers(stamped.date()):
                        kept.append(json.dumps(_sanitize(record, counters), ensure_ascii=False))
        except UnicodeDecodeError:
            return None, "文件不是 UTF-8 文本"
        except OSError as error:
            return None, f"无法读取（{type(error).__name__}）"
        if not kept:
            return None, ""
        header = (
            f"# 按所选日期（{scope.start.isoformat()} 至 {scope.end.isoformat()}）逐行过滤；"
            f"结构化字段已按键名移除凭据。\n"
            f"# 无法解析的行 {unreadable} 行、无法按日期关联的行 {undated} 行未包含。\n"
        )
        return (header + "\n".join(kept) + "\n").encode("utf-8"), ""

    def _manifest_document(
        self, plan: ExportPlan, written: Sequence[PlannedEntry],
        byte_count: int, counters: Dict[str, int],
    ) -> Dict[str, object]:
        meta = self.run_meta
        assert meta is not None
        return {
            "schema_version": SCHEMA_VERSION,
            "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
            "app_version": meta.version,
            "instance_id": meta.instance_id,
            "source": meta.source,
            "source_label": SOURCE_LABELS.get(meta.source, meta.source),
            "scope": plan.scope.as_document(),
            "byte_count": byte_count,
            "redacted_count": counters["fields"] + counters["values"],
            # files 只列真的写进包里的文件；没写进去的都进 not_included，不混在一起。
            "files": [entry.as_document() for entry in written if entry.status == STATUS_INCLUDED],
            "not_included": [
                entry.as_document() for entry in _not_included(plan, written)
            ],
            "notes": list(plan.notes),
            "limitations": [
                "开发日志为摘要与指纹，无法还原完整模型输入输出或工具原文。",
                "聊天按会话单位保留：会话内任一时间落在所选范围内即整段保留，不逐条裁剪。",
                "学习记录按天裁剪，仅保留所选日期范围内的天。",
                "日志沿用既有轮转与容量限制，已轮转移除的旧内容不在包内。",
                "已排除凭据文件（llm_secrets、.env、私人配置与凭据备份）；"
                "结构化字段按键名移除凭据，正文遮蔽可识别凭据，但不承诺万能脱敏。",
            ],
        }


def _chat_session_has_time(session: Mapping[str, Any]) -> bool:
    for key in ("started_at", "updated_at"):
        if _parse_datetime(session.get(key)) is not None:
            return True
    messages = session.get("messages")
    if isinstance(messages, list):
        for message in messages:
            if isinstance(message, Mapping) and _parse_datetime(message.get("created_at")) is not None:
                return True
    return False


def _chat_session_in_scope(session: Mapping[str, Any], scope: ExportScope) -> bool:
    # 每日会话的归属是业务日期；模拟日期不能由真实创建时间替代。
    if session.get("session_kind") == "daily_learning":
        business_date = _parse_date(session.get("business_date"))
        if business_date is not None:
            return scope.covers(business_date)
    for key in ("started_at", "updated_at"):
        stamp = _parse_datetime(session.get(key))
        if stamp is not None and scope.covers(stamp.date()):
            return True
    messages = session.get("messages")
    if isinstance(messages, list):
        for message in messages:
            if not isinstance(message, Mapping):
                continue
            stamp = _parse_datetime(message.get("created_at"))
            if stamp is not None and scope.covers(stamp.date()):
                return True
    # 会话没有可解析时间时，用会话 key 里的日期兜底，仍然不做逐条裁剪。
    return False


def feedback_export_for_runtime(runtime):
    """启动层已初始化的实例信息；不推测项目目录或实例用途。"""
    if runtime is None:
        return None
    from modules.runtime_paths import read_json_strict, RuntimeConfigurationError

    metadata = read_json_strict(runtime.metadata_file)
    if metadata.get("data_source") != runtime.data_source:
        raise RuntimeConfigurationError("Runtime metadata source does not match")
    source = (SOURCE_STUDY if runtime.is_study else
              SOURCE_REAL if runtime.data_source == "real" else SOURCE_ENGINEERING)
    return FeedbackExportService(
        data_root=runtime.data_root,
        run_meta=RunMeta(version=runtime.version, instance_id=metadata["instance_id"], source=source),
    )


def _not_included(plan: ExportPlan, written: Sequence[PlannedEntry]) -> List[PlannedEntry]:
    """合并“计划里就没选中”和“导出时才发现写不了”的两类未包含项。"""
    entries: List[PlannedEntry] = [
        entry for entry in plan.entries if entry.status != STATUS_INCLUDED
    ]
    seen = {entry.archive_path for entry in entries}
    for entry in written:
        if entry.status != STATUS_INCLUDED and entry.archive_path not in seen:
            entries.append(entry)
            seen.add(entry.archive_path)
    return entries


def _restatus(entry: PlannedEntry, status: str, detail: str) -> PlannedEntry:
    """只改状态与说明，保留来源字段，避免重建清单行时漏掉相对路径与允许根。"""
    return PlannedEntry(
        entry.category, entry.archive_path, status, detail,
        entry.source_name, entry.relpath, entry.root,
    )


def _dump(document: Any, counters: Dict[str, int]) -> bytes:
    return json.dumps(
        _sanitize(document, counters), ensure_ascii=False, indent=2
    ).encode("utf-8")


def _discard(path: Path) -> None:
    try:
        if path.exists():
            path.unlink()
    except OSError:
        pass


def _readme_text(plan: ExportPlan, feedback: FeedbackText, source: str = "") -> str:
    scope = plan.scope
    lines = [
        "RoxyPlan 内测反馈包",
        "====================",
        "",
        f"导出日期范围：{scope.start.isoformat()} 至 {scope.end.isoformat()}",
        f"包含类别：{'、'.join(CATEGORY_LABELS[name] for name in scope.selected_categories())}",
        f"来源标记：{SOURCE_LABELS.get(source, '未标记')}",
        "",
        "本包由用户在 RoxyPlan 设置中主动导出，没有后台上传。",
    ]
    if source == SOURCE_ENGINEERING:
        # 工程与模拟数据不能被读成真实用户的使用证据，这一条写在最显眼处。
        lines.append("本包内容来自工程或模拟运行，不能当作真实用户连续使用的证据。")
    elif source == SOURCE_STUDY:
        lines.append("本包用于用户体验收集，业务日期可手动推进；聊天和反馈由使用者产生，不据业务日期推断连续使用天数或长期学习成效。")
    lines += [
        "包内文件：",
        "  feedback.json      你填写的反馈文字与导出范围",
        "  environment.json   版本、实例匿名 ID、来源与系统环境",
        "  manifest.json      实际文件清单、缺失说明与已知局限",
        "  其余目录           按类别导出的数据文件",
        "",
        "已知局限：",
        "  * 开发日志只有摘要、计数与指纹，无法还原完整模型输入输出或工具原文。",
        "  * 聊天按会话整段保留，不逐条裁剪。",
        "  * 学习记录按天裁剪到所选日期范围。",
        "  * 日志沿用既有轮转与容量限制，已轮转移除的旧内容不在包内。",
        "  * 已排除凭据文件；结构化字段按键名移除凭据，正文遮蔽可识别凭据，",
        "    但不承诺万能脱敏。",
        "",
    ]
    if not feedback.is_empty():
        lines.extend([
            "反馈摘要（完整内容见 feedback.json）：",
            f"  问题：{_one_line(feedback.problem)}",
            f"  预期：{_one_line(feedback.expected)}",
            f"  实际：{_one_line(feedback.actual)}",
            "",
        ])
    return "\n".join(lines)


def _one_line(text: str, limit: int = 60) -> str:
    compact = " ".join(str(text or "").split())
    if not compact:
        return "（未填写）"
    return compact if len(compact) <= limit else compact[:limit].rstrip() + "…"
