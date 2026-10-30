"""Read-only checker and browser for the Beta interaction regression corpus.

Validates tests/fixtures/beta_interaction_regressions.jsonl and its source
index (tests/fixtures/beta_interaction_regressions.index.json):

- JSONL parsing, per-kind required fields, status vocabulary
- case_id uniqueness and precondition-aware dedupe
- every source reference: fixture case lookup, golden index lookup,
  test-name existence, doc quote presence
- source index inventory: sha256 and record counts against the real files

It never imports application modules, never calls a model, and never writes
to the repository. Usage:

    python -B scripts/check_beta_regression_corpus.py            # run checks
    python -B scripts/check_beta_regression_corpus.py --summary  # counts only
    python -B scripts/check_beta_regression_corpus.py --table    # readable table
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CORPUS = ROOT / "tests" / "fixtures" / "beta_interaction_regressions.jsonl"
INDEX = ROOT / "tests" / "fixtures" / "beta_interaction_regressions.index.json"

STATUS_VOCAB = {"未执行", "待验证", "已知失败", "计划", "历史不适用"}
SOURCE_TYPES = {"真实反馈", "既有测试", "公开表达", "人工变体"}
REF_KINDS = {"fixture_case", "golden_index", "test_name", "doc"}
SINGLE_REQUIRED = {
    "case_id", "kind", "family", "family_label", "text", "precondition",
    "expected", "capability_basis", "status", "needs_review", "source",
}
MULTI_REQUIRED = {
    "case_id", "kind", "family", "family_label", "precondition", "turns",
    "dependencies", "forbidden_writes", "capability_basis", "status",
    "needs_review", "source",
}
EXPECT_REQUIRED = {"data_changes"}
RESULT_COLUMNS_EMPTY = "未执行"


def normalize(text: str) -> str:
    return "".join(text.split())


def sha256_of(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def crlf_text_sha256_of(path: Path) -> str | None:
    """Compare known UTF-8 text using only CRLF -> LF, without rewriting it.

    Keep BOM, spaces, lone CR and every other byte significant. The expected
    hash remains the recorded snapshot (or its explicit integration override).
    Unknown/binary sources retain raw-byte-only comparison.
    """
    if path.suffix.lower() not in {".json", ".jsonl", ".md", ".py", ".txt", ".sha256"}:
        return None
    data = path.read_bytes()
    if b"\x00" in data:
        return None
    try:
        data.decode("utf-8")
    except UnicodeDecodeError:
        return None
    return hashlib.sha256(data.replace(b"\r\n", b"\n")).hexdigest()


def count_records(path: Path):
    """Same counting rules the index uses: jsonl lines, json cases lists."""
    if path.suffix == ".jsonl":
        lines = [line for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
        return len(lines)
    if path.suffix == ".json":
        obj = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(obj, list):
            return len(obj)
        if isinstance(obj, dict):
            for key in ("cases", "case_ids"):
                if isinstance(obj.get(key), list):
                    return len(obj[key])
        return None
    return None


class Report:
    def __init__(self):
        self.errors: list[str] = []
        self.warnings: list[str] = []

    def error(self, message: str) -> None:
        self.errors.append(message)

    def warn(self, message: str) -> None:
        self.warnings.append(message)

    def ok(self) -> bool:
        return not self.errors


def load_jsonl(path: Path, report: Report):
    records = []
    for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        try:
            records.append(json.loads(line))
        except json.JSONDecodeError as exc:
            report.error(f"第 {number} 行 JSON 解析失败: {exc}")
    return records


def collect_texts(container) -> list[str]:
    """All candidate user-facing texts of one fixture case."""
    texts = []
    if not isinstance(container, dict):
        return texts
    for key in ("text", "seed"):
        value = container.get(key)
        if isinstance(value, str):
            texts.append(value)
    turns = container.get("turns")
    if isinstance(turns, list):
        for turn in turns:
            if isinstance(turn, dict) and isinstance(turn.get("text"), str):
                texts.append(turn["text"])
    return texts


def load_fixture_index(path: Path, report: Report):
    """Map fixture case ids to their texts; None on load failure."""
    if not path.exists():
        report.error(f"引用文件不存在: {path}")
        return None
    try:
        if path.suffix == ".jsonl":
            obj = load_jsonl(path, report)
            table = {}
            for item in obj:
                if isinstance(item, dict) and isinstance(item.get("case_id"), str):
                    table[item["case_id"]] = collect_texts(item)
            return table
        data = json.loads(path.read_text(encoding="utf-8"))
        items = data.get("cases") if isinstance(data, dict) else data
        if not isinstance(items, list):
            report.error(f"引用文件结构无法识别: {path}")
            return None
        table = {}
        for item in items:
            if isinstance(item, dict) and isinstance(item.get("id"), str):
                table[item["id"]] = collect_texts(item)
        return table
    except (OSError, json.JSONDecodeError) as exc:
        report.error(f"引用文件读取失败 {path}: {exc}")
        return None


def check_ref(ref: dict, report: Report, context: str, cache: dict) -> None:
    if not isinstance(ref, dict):
        report.error(f"{context}: 引用不是对象: {ref!r}")
        return
    kind = ref.get("kind")
    path_value = ref.get("path")
    locator = ref.get("locator")
    quote = ref.get("quote")
    if kind not in REF_KINDS:
        report.error(f"{context}: 未知引用类型 {kind!r}")
        return
    if not isinstance(path_value, str):
        report.error(f"{context}: 引用缺少 path")
        return
    file = ROOT / path_value
    if not file.exists():
        report.error(f"{context}: 引用文件不存在 {path_value}")
        return

    if kind in ("fixture_case", "golden_index"):
        if not isinstance(locator, str) or not isinstance(quote, str):
            report.error(f"{context}: {path_value} 的 {kind} 引用需要 locator 与 quote")
            return
        key = ("fixture", path_value)
        if key not in cache:
            cache[key] = load_fixture_index(file, report)
        table = cache[key]
        if table is None:
            return
        if kind == "fixture_case":
            if locator not in table:
                report.error(f"{context}: 用例 {locator} 不存在于 {path_value}")
                return
            texts = table[locator]
            if not texts:
                report.warn(f"{context}: 用例 {locator} 无可比对文本")
                return
            if normalize(quote) not in {normalize(text) for text in texts}:
                report.error(
                    f"{context}: 引用文本与 {path_value}#{locator} 不一致: "
                    f"quote={quote!r}"
                )
            return
        # golden_index: locator = case[N], 1-based
        stripped = locator.strip()
        if not (stripped.startswith("case[") and stripped.endswith("]")):
            report.error(f"{context}: golden_index locator 需为 case[N]，当前 {locator!r}")
            return
        try:
            position = int(stripped[5:-1])
        except ValueError:
            report.error(f"{context}: golden_index 下标无效 {locator!r}")
            return
        key2 = ("golden_order", path_value)
        if key2 not in cache:
            try:
                data = json.loads(file.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError) as exc:
                report.error(f"{context}: 读取失败 {path_value}: {exc}")
                return
            items = data.get("cases") if isinstance(data, dict) else data
            cache[key2] = items if isinstance(items, list) else []
        items = cache[key2]
        if position < 1 or position > len(items):
            report.error(f"{context}: golden 下标越界 {locator}（共 {len(items)} 条）")
            return
        item = items[position - 1]
        if not isinstance(item, dict) or not isinstance(item.get("text"), str):
            report.error(f"{context}: golden 第 {position} 条缺少 text")
            return
        if normalize(quote) not in normalize(item["text"]):
            report.error(
                f"{context}: golden case[{position}] 文本不匹配: quote={quote!r} "
                f"actual={item['text']!r}"
            )
        return

    if kind == "test_name":
        if not isinstance(locator, str):
            report.error(f"{context}: test_name 引用缺少 locator")
            return
        source = file.read_text(encoding="utf-8", errors="replace")
        if f"def {locator}(" not in source:
            report.error(f"{context}: 测试不存在 {path_value}::{locator}")
        return

    # doc
    if quote is not None:
        if not isinstance(quote, str):
            report.error(f"{context}: doc 引用 quote 需为字符串")
            return
        text = file.read_text(encoding="utf-8", errors="replace")
        if normalize(quote) not in normalize(text):
            report.error(f"{context}: 文档引文未找到 {path_value}: quote={quote!r}")


def check_records(records: list[dict], report: Report) -> None:
    seen_ids: set[str] = set()
    seen_texts: dict[tuple[str, str, str], str] = {}
    cache: dict = {}
    for record in records:
        case_id = record.get("case_id")
        context = f"[{case_id or '无 case_id'}]"
        if not isinstance(case_id, str) or not case_id:
            report.error("存在缺少 case_id 的记录")
            continue
        if case_id in seen_ids:
            report.error(f"{context}: case_id 重复")
        seen_ids.add(case_id)

        kind = record.get("kind")
        if kind == "single_turn":
            missing = SINGLE_REQUIRED - set(record)
            if missing:
                report.error(f"{context}: 缺少字段 {sorted(missing)}")
                continue
            text = record["text"]
            if not isinstance(text, str) or not text.strip():
                report.error(f"{context}: text 为空")
                continue
            expectation = record["expected"]
            if not isinstance(expectation, dict) or not EXPECT_REQUIRED <= set(expectation):
                report.error(f"{context}: expected 缺少 {sorted(EXPECT_REQUIRED - set(expectation or {}))}")
            dedupe_key = (
                normalize(text),
                json.dumps(record.get("precondition"), ensure_ascii=False, sort_keys=True),
                record.get("family"),
            )
            if dedupe_key in seen_texts:
                report.warn(f"{context}: 与 {seen_texts[dedupe_key]} 句面与前置状态重复")
            else:
                seen_texts[dedupe_key] = case_id
        elif kind == "multi_turn":
            missing = MULTI_REQUIRED - set(record)
            if missing:
                report.error(f"{context}: 缺少字段 {sorted(missing)}")
                continue
            turns = record["turns"]
            if not isinstance(turns, list) or not turns:
                report.error(f"{context}: turns 为空")
                continue
            user_texts = []
            for position, turn in enumerate(turns, start=1):
                if not isinstance(turn, dict):
                    report.error(f"{context}: 第 {position} 轮不是对象")
                    continue
                role = turn.get("role")
                if role not in ("user", "event"):
                    report.error(f"{context}: 第 {position} 轮 role 无效 {role!r}")
                if role == "user":
                    text = turn.get("text")
                    if not isinstance(text, str) or not text.strip():
                        report.error(f"{context}: 第 {position} 轮缺少 text")
                    else:
                        user_texts.append(text)
                expectation = turn.get("expect")
                if not isinstance(expectation, dict) or not EXPECT_REQUIRED <= set(expectation):
                    report.error(f"{context}: 第 {position} 轮 expect 缺少字段")
                if role == "user" and isinstance(expectation, dict) and "tools" not in expectation:
                    report.error(f"{context}: 第 {position} 轮用户期望缺少 tools")
            if user_texts:
                dedupe_key = (
                    normalize(user_texts[0]),
                    json.dumps(record.get("precondition"), ensure_ascii=False, sort_keys=True),
                    record.get("family"),
                )
                if dedupe_key in seen_texts:
                    if record.get("reuse_of") != seen_texts[dedupe_key]:
                        report.warn(f"{context}: 首轮句面与 {seen_texts[dedupe_key]} 重复且未声明 reuse_of")
                else:
                    seen_texts[dedupe_key] = case_id
        else:
            report.error(f"{context}: 未知 kind {kind!r}")
            continue

        status = record.get("status")
        if status not in STATUS_VOCAB:
            report.error(f"{context}: status 无效 {status!r}")
        if status == "计划" and "planned_change" not in record:
            report.error(f"{context}: status=计划 需提供 planned_change")
        if status == "历史不适用" and "current_mapping" not in record:
            report.error(f"{context}: status=历史不适用 需提供 current_mapping")

        if not isinstance(record.get("capability_basis"), str):
            report.error(f"{context}: capability_basis 缺失")

        source = record.get("source")
        if not isinstance(source, dict):
            report.error(f"{context}: source 缺失")
            continue
        if source.get("type") not in SOURCE_TYPES:
            report.error(f"{context}: source.type 无效 {source.get('type')!r}")
        refs = source.get("refs")
        if not isinstance(refs, list) or not refs:
            report.error(f"{context}: source.refs 为空")
            continue
        for ref in refs:
            check_ref(ref, report, context, cache)


def check_index(report: Report, corpus_records: list[dict]) -> None:
    if not INDEX.exists():
        report.error(f"索引文件不存在: {INDEX.relative_to(ROOT)}")
        return
    try:
        index = json.loads(INDEX.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        report.error(f"索引 JSON 解析失败: {exc}")
        return

    inventory = index.get("source_inventory")
    if not isinstance(inventory, list) or not inventory:
        report.error("索引缺少 source_inventory")
        return
    for entry in inventory:
        path_value = entry.get("path")
        file = ROOT / str(path_value)
        if not file.exists():
            report.error(f"索引条目文件不存在: {path_value}")
            continue
        # Preserve the contributor snapshot; an explicit coordinator review
        # can bind the same references to another existing source version.
        recorded_sha = entry.get("integration_sha256", entry.get("sha256"))
        if recorded_sha and recorded_sha != "PENDING":
            actual = sha256_of(file)
            if actual != recorded_sha and crlf_text_sha256_of(file) != recorded_sha:
                message = (
                    f"来源快照已变化 {path_value}: "
                    f"记录 {recorded_sha[:12]}… 实际 {actual[:12]}…"
                )
                if entry.get("frozen", False):
                    report.error(message)
                else:
                    report.warn(message + "；保留原哈希，逐项核对当前引用与预期")
        recorded_count = entry.get("records")
        if isinstance(recorded_count, int):
            actual_count = count_records(file)
            if actual_count is not None and actual_count != recorded_count:
                report.error(f"索引计数不一致 {path_value}: 记录 {recorded_count} 实际 {actual_count}")

    corpus_meta = index.get("corpus", {})
    if corpus_meta.get("path") == str(CORPUS.relative_to(ROOT)).replace("\\", "/"):
        recorded_sha = corpus_meta.get("sha256")
        if recorded_sha and recorded_sha != "PENDING":
            actual = sha256_of(CORPUS)
            if actual != recorded_sha and crlf_text_sha256_of(CORPUS) != recorded_sha:
                report.error(f"语料哈希与索引不一致: 记录 {recorded_sha[:12]}… 实际 {actual[:12]}…")
    kinds = [record.get("kind") for record in corpus_records]
    singles = sum(1 for kind in kinds if kind == "single_turn")
    multis = sum(1 for kind in kinds if kind == "multi_turn")
    declared = corpus_meta.get("counts", {})
    if declared.get("single_turn") != singles:
        report.error(f"语料计数不一致: 记录单轮 {declared.get('single_turn')} 实际 {singles}")
    if declared.get("multi_turn") != multis:
        report.error(f"语料计数不一致: 记录多轮 {declared.get('multi_turn')} 实际 {multis}")


def summary(records: list[dict]) -> None:
    families: dict[str, int] = {}
    statuses: dict[str, int] = {}
    sources: dict[str, int] = {}
    for record in records:
        families[record.get("family", "?")] = families.get(record.get("family", "?"), 0) + 1
        statuses[record.get("status", "?")] = statuses.get(record.get("status", "?"), 0) + 1
        source = record.get("source") or {}
        sources[source.get("type", "?")] = sources.get(source.get("type", "?"), 0) + 1

    singles = sum(1 for r in records if r.get("kind") == "single_turn")
    multis = sum(1 for r in records if r.get("kind") == "multi_turn")
    out = [f"总记录：{len(records)}（单轮 {singles}，多轮 {multis}）", "", "按家族："]
    for key in sorted(families):
        out.append(f"  {key}: {families[key]}")
    out.append("")
    out.append("按状态：" )
    for key in sorted(statuses):
        out.append(f"  {key}: {statuses[key]}")
    out.append("")
    out.append("按来源类型：")
    for key in sorted(sources):
        out.append(f"  {key}: {sources[key]}")
    print("\n".join(out))


def table(records: list[dict]) -> None:
    header = ["case_id", "kind", "family", "首句/输入", "期望工具", "状态", "来源", "回复", "工具结果", "数据差异", "版本", "Tokens"]
    rows = []
    for record in records:
        if record.get("kind") == "single_turn":
            first = record.get("text", "")
            tools = record.get("expected", {}).get("tools", [])
        else:
            first_turn = next((t for t in record.get("turns", []) if t.get("role") == "user"), {})
            first = first_turn.get("text", "（事件型）")
            tools = sorted({tool for turn in record.get("turns", []) for tool in (turn.get("expect", {}).get("tools") or [])})
        source = (record.get("source") or {}).get("type", "?")
        rows.append([
            record.get("case_id", "?"), record.get("kind", "?"), record.get("family", "?"),
            first if len(first) <= 24 else first[:23] + "…",
            "/".join(tools) if tools else "（零工具）",
            record.get("status", "?"), source,
            RESULT_COLUMNS_EMPTY, RESULT_COLUMNS_EMPTY, RESULT_COLUMNS_EMPTY,
            RESULT_COLUMNS_EMPTY, RESULT_COLUMNS_EMPTY,
        ])
    widths = [max(len(str(row[position])) for row in [header] + rows) for position in range(len(header))]
    def render(row):
        return " | ".join(str(cell).ljust(widths[position]) for position, cell in enumerate(row))
    print(render(header))
    print("-+-".join("-" * width for width in widths))
    for row in rows:
        print(render(row))


def main() -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except (AttributeError, ValueError):
        pass
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--summary", action="store_true", help="只输出计数摘要")
    parser.add_argument("--table", action="store_true", help="输出可读场景表（结果列保持未执行）")
    args = parser.parse_args()

    report = Report()
    if not CORPUS.exists():
        print(f"语料文件不存在: {CORPUS}")
        return 1
    records = load_jsonl(CORPUS, report)
    check_records(records, report)
    check_index(report, records)

    if args.summary:
        summary(records)
    elif args.table:
        table(records)
    else:
        summary(records)
        print()
        if report.warnings:
            print("警告：")
            for message in report.warnings:
                print(f"  - {message}")
        if report.errors:
            print("错误：")
            for message in report.errors:
                print(f"  - {message}")
        print()
        print("检查结果：", "通过" if report.ok() else f"未通过（{len(report.errors)} 个错误）")
    return 0 if report.ok() else 1


if __name__ == "__main__":
    raise SystemExit(main())
