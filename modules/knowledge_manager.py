from __future__ import annotations

import re
from pathlib import Path
from typing import Dict, List, Optional


SUPPORTED_EXTENSIONS = {".txt", ".md"}
IGNORED_FILE_NAMES = {"readme.md"}


class KnowledgeManager:
    """Small, read-only local knowledge loader shared by desktop and Web."""

    def __init__(
        self,
        directory: Path,
        *,
        max_file_chars: int = 12000,
        max_snippet_chars: int = 900,
        max_matches: int = 3,
        max_context_chars: int = 2400,
    ) -> None:
        self.directory = Path(directory)
        self.max_file_chars = max(500, int(max_file_chars))
        self.max_snippet_chars = max(200, int(max_snippet_chars))
        self.max_matches = max(1, min(int(max_matches), 8))
        self.max_context_chars = max(500, int(max_context_chars))
        self.last_file_count = 0
        self.last_match_count = 0

    def scan(self) -> List[Dict[str, str]]:
        try:
            self.directory.mkdir(parents=True, exist_ok=True)
            paths = sorted(
                (
                    path
                    for path in self.directory.iterdir()
                    if path.is_file()
                    and path.suffix.lower() in SUPPORTED_EXTENSIONS
                    and path.name.lower() not in IGNORED_FILE_NAMES
                ),
                key=lambda path: path.name.lower(),
            )
        except OSError as error:
            self.last_file_count = 0
            print(
                f"[Knowledge] scan failed: {type(error).__name__}",
                flush=True,
            )
            return []

        items: List[Dict[str, str]] = []
        for path in paths:
            try:
                content = path.read_text(encoding="utf-8")
            except UnicodeDecodeError:
                print(
                    f"[Knowledge] decode failed: {path.name}",
                    flush=True,
                )
                continue
            except OSError as error:
                print(
                    f"[Knowledge] read failed: {path.name} "
                    f"{type(error).__name__}",
                    flush=True,
                )
                continue
            items.append(
                {
                    "name": path.name,
                    "content": content[: self.max_file_chars],
                }
            )

        self.last_file_count = len(items)
        print(f"[Knowledge] loaded files={self.last_file_count}", flush=True)
        return items

    def find_relevant(
        self,
        user_text: str,
        items: Optional[List[Dict[str, str]]] = None,
    ) -> List[Dict[str, str]]:
        knowledge_items = self.scan() if items is None else items
        keywords = self.extract_keywords(user_text)
        if not keywords:
            self.last_match_count = 0
            return []

        scored = []
        for item in knowledge_items:
            name = str(item.get("name", ""))
            content = str(item.get("content", ""))
            lowered_name = name.lower()
            lowered_content = content.lower()
            score = 0
            matched_keywords: List[str] = []
            for keyword in keywords:
                lowered = keyword.lower()
                if lowered in lowered_name:
                    score += 3
                    matched_keywords.append(keyword)
                if lowered in lowered_content:
                    score += 1
                    matched_keywords.append(keyword)
            if score:
                scored.append(
                    {
                        "score": score,
                        "name": name,
                        "snippet": self.extract_snippet(content, matched_keywords),
                    }
                )

        scored.sort(
            key=lambda item: (-int(item["score"]), str(item["name"]).lower())
        )
        matches = [
            {"name": str(item["name"]), "snippet": str(item["snippet"])}
            for item in scored[: self.max_matches]
        ]
        self.last_match_count = len(matches)
        print(f"[Knowledge] matched snippets={self.last_match_count}", flush=True)
        return matches

    def build_context(
        self,
        user_text: str,
        items: Optional[List[Dict[str, str]]] = None,
    ) -> str:
        knowledge_items = self.scan() if items is None else items
        matches = self.find_relevant(user_text, knowledge_items)
        if not matches:
            return ""

        lines = ["本地知识库匹配片段："]
        used_chars = len(lines[0])
        for match in matches:
            block = f"文件：{match['name']}\n{match['snippet']}"
            remaining = self.max_context_chars - used_chars
            if remaining <= 0:
                break
            block = block[:remaining]
            lines.append(block)
            used_chars += len(block)
        return "\n".join(lines)

    def file_count(self) -> int:
        return len(self.scan())

    @staticmethod
    def extract_keywords(user_text: str) -> List[str]:
        tokens = re.findall(r"[A-Za-z0-9_]{2,}|[\u4e00-\u9fff]{2,}", user_text)
        keywords: List[str] = []
        for token in tokens:
            if token not in keywords:
                keywords.append(token)
            if re.fullmatch(r"[\u4e00-\u9fff]{3,}", token):
                for size in (2, 3):
                    for index in range(0, max(0, len(token) - size + 1)):
                        piece = token[index : index + size]
                        if piece not in keywords:
                            keywords.append(piece)
        return keywords[:24]

    def extract_snippet(self, content: str, keywords: List[str]) -> str:
        normalized = content.strip()
        if not normalized:
            return "（文件为空）"

        lowered = normalized.lower()
        hit_index = -1
        for keyword in keywords:
            if keyword:
                hit_index = lowered.find(keyword.lower())
                if hit_index >= 0:
                    break
        if hit_index < 0:
            return normalized[: self.max_snippet_chars]

        half_window = self.max_snippet_chars // 2
        start = max(0, hit_index - half_window)
        end = min(len(normalized), start + self.max_snippet_chars)
        snippet = normalized[start:end].strip()
        if start > 0:
            snippet = "..." + snippet
        if end < len(normalized):
            snippet += "..."
        return snippet
