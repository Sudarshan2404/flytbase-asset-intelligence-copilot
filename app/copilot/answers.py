"""Strip model deliberation so only a user-facing answer leaves the backend."""

from __future__ import annotations

import json
import re
from typing import Any

FALLBACK_ANSWER = (
    "The copilot could not produce a concise answer from the model output. "
    "No additional facts were invented."
)

_THINK_BLOCK_RE = re.compile(
    r"(?is)<(?:think|thought|reasoning|analysis)>.*?</(?:think|thought|reasoning|analysis)>"
)

_DELIBERATION_LINE_RE = re.compile(
    r"""(?ix)^\s*(?:
        let\ me\b
        | let['’]s\b
        | i(?:['’]ll|\ will|\ am|['’]m)\b
        | i\ need\ to\b
        | i\ should\b
        | i\ can\ (?:call|check|look|see|use|try)
        | looking\ at\b
        | re-?examin
        | first(?:ly)?[,:]
        | step[- ]by[- ]step
        | wait[,.]
        | hmm\b
        | okay[,.]?\s+so\b
        | here(?:['’]s|\ is) (?:the|a) (?:list of )?tools
        | the (?:available )?tools? (?:are|include|available)
        | tool (?:list|definitions?|catalog|inventory)
        | i have (?:the following )?tools
        | now i (?:will|need)
        | checking (?:the )?(?:tools?|schema|functions?)
        | my (?:plan|approach|reasoning)
        | internally
        | chain.of.thought
        | i will now
        | next i (?:will|should)
    )"""
)

_TOOL_INVENTORY_RE = re.compile(
    r'(?is)("name"\s*:\s*"get_[a-z0-9_]+".*){3,}|use only these tools|function_declarations'
)
_TOOL_NAME_LIST_RE = re.compile(r"^(?:get_[a-z0-9_]+(?:\s*,\s*)?){2,}$", re.I)

_DELIBERATION_MARKERS = (
    "let me re-examine",
    "tool definitions",
    "tool inventory",
    "available tools",
    "i will now call",
    "step by step",
    "chain of thought",
    "my reasoning",
    "looking at the tools",
    "here are the tools",
    "re-examine the tool",
)


def strip_think_blocks(text: str) -> str:
    return _THINK_BLOCK_RE.sub("", text or "").strip()


def _iter_json_objects(text: str) -> list[dict[str, Any]]:
    objects: list[dict[str, Any]] = []
    decoder = json.JSONDecoder()
    index = 0
    while index < len(text):
        start = text.find("{", index)
        if start < 0:
            break
        try:
            payload, end = decoder.raw_decode(text, start)
        except json.JSONDecodeError:
            index = start + 1
            continue
        if isinstance(payload, dict):
            objects.append(payload)
        index = end
    return objects


def extract_structured_answer(text: str) -> tuple[str | None, dict[str, Any] | None]:
    """Return the last JSON object that carries a user-facing answer."""
    visual = None
    answer = None
    for payload in _iter_json_objects(text):
        if payload.get("name") or payload.get("tool"):
            continue
        raw_answer = payload.get("answer")
        if raw_answer in (None, ""):
            continue
        answer = str(raw_answer)
        raw_visual = payload.get("visual")
        visual = raw_visual if isinstance(raw_visual, dict) else None
    return answer, visual


def looks_like_deliberation(text: str) -> bool:
    cleaned = strip_think_blocks(text)
    if not cleaned:
        return False
    lowered = cleaned.lower()
    hits = sum(1 for marker in _DELIBERATION_MARKERS if marker in lowered)
    if _TOOL_INVENTORY_RE.search(cleaned):
        return True
    if hits >= 2:
        return True
    if hits >= 1 and len(cleaned) > 400:
        return True
    lines = [line for line in cleaned.splitlines() if line.strip()]
    if not lines:
        return False
    flagged = sum(1 for line in lines if _DELIBERATION_LINE_RE.match(line.strip()))
    return flagged >= max(2, int(len(lines) * 0.6))


def sanitize_user_answer(text: str | None, *, empty: str = FALLBACK_ANSWER) -> str:
    """Keep only the user-facing conclusion. Never return chain-of-thought."""
    if text is None:
        return empty
    cleaned = strip_think_blocks(str(text))
    if not cleaned:
        return empty
    kept: list[str] = []
    for paragraph in re.split(r"\n{2,}", cleaned):
        lines = [
            line
            for line in paragraph.splitlines()
            if line.strip()
            and not _DELIBERATION_LINE_RE.match(line.strip())
            and not _TOOL_INVENTORY_RE.search(line)
            and not _TOOL_NAME_LIST_RE.match(line.strip())
        ]
        block = "\n".join(lines).strip()
        if block:
            kept.append(block)
    result = "\n\n".join(kept).strip()
    if not result or looks_like_deliberation(result):
        return empty
    return result


def user_facing_answer(text: str | None) -> tuple[str, dict[str, Any] | None]:
    """Parse a model turn into a sanitized answer and optional visual hint."""
    if not text or not str(text).strip():
        return "The model did not return an answer.", None
    cleaned = strip_think_blocks(str(text))
    structured, visual = extract_structured_answer(cleaned)
    source = structured if structured is not None else cleaned
    return sanitize_user_answer(source), visual
