from __future__ import annotations

import re
from typing import Tuple


DEFAULT_EMPTY_REPLY = "我暂时没有整理出合适的回答，请再说一次。"
_NULL_LIKE = {"none", "null", "nil", "undefined"}
_COMPLETE_REASONING_BLOCK = re.compile(
    r"<(?:think|analysis)\b[^>]*>(.*?)</(?:think|analysis)\s*>",
    flags=re.IGNORECASE | re.DOTALL,
)
_OPEN_REASONING_TAG = re.compile(
    r"<(?:think|analysis)\b[^>]*>", flags=re.IGNORECASE
)
_CLOSE_REASONING_TAG = re.compile(
    r"</(?:think|analysis)\s*>", flags=re.IGNORECASE
)
_PAIRED_STRONG_MARKDOWN = re.compile(
    r"(?<!\*)\*\*(?!\*)(?=\S)([^\n]*?\S)(?<!\*)\*\*(?!\*)"
)


def split_reasoning_content(
    content: object,
    reasoning_content: object = "",
) -> Tuple[str, str]:
    """Separate provider reasoning from text that may be shown to the user."""

    text = str(content or "").strip()
    hidden = [str(reasoning_content or "").strip()]

    def collect(match: re.Match) -> str:
        value = str(match.group(1) or "").strip()
        if value:
            hidden.append(value)
        return ""

    public = _COMPLETE_REASONING_BLOCK.sub(collect, text).strip()

    dangling_open = _OPEN_REASONING_TAG.search(public)
    if dangling_open is not None:
        remainder = public[dangling_open.end() :].strip()
        if remainder:
            hidden.append(remainder)
        public = public[: dangling_open.start()].strip()

    # Some local models omit the opening tag but still emit a closing marker.
    dangling_close = _CLOSE_REASONING_TAG.search(public)
    if dangling_close is not None:
        prefix = public[: dangling_close.start()].strip()
        if prefix:
            hidden.append(prefix)
        public = public[dangling_close.end() :].strip()

    public = _OPEN_REASONING_TAG.sub("", public)
    public = _CLOSE_REASONING_TAG.sub("", public).strip()
    reasoning = "\n\n".join(item for item in hidden if item)
    return public, reasoning


def sanitize_public_reply(
    value: object,
    *,
    fallback: str = DEFAULT_EMPTY_REPLY,
) -> str:
    public = sanitize_public_text(value)
    if not public:
        return str(fallback).strip() or DEFAULT_EMPTY_REPLY
    # Natural chat is plain text. Only unwrap a well-formed **strong** span;
    # lone asterisks, multiplication, code, and triple-asterisk Markdown stay
    # untouched rather than being broadly stripped.
    return _PAIRED_STRONG_MARKDOWN.sub(r"\1", public)


def sanitize_public_text(value: object) -> str:
    public, _reasoning = split_reasoning_content(value)
    if not public or public.strip().lower() in _NULL_LIKE:
        return ""
    return public
