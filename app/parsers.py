from __future__ import annotations

import json
import re
from collections.abc import Iterator
from pathlib import Path
from typing import Any

DOCUMENT_ID_RE = re.compile(r"Document ID:\s*([A-Za-z0-9-]+)")
TITLE_RE = re.compile(r"^#\s+(.+)$", re.MULTILINE)
REPORT_ID_RE = re.compile(r"Maintenance Report\s+[—-]\s+(MNT-\S+)")


def relative_source(data_dir: Path, path: Path) -> str:
    return path.resolve().relative_to(data_dir.resolve()).as_posix()


def parse_json_text(text: str) -> Any:
    return json.loads(text)


def records_from_json_value(value: Any, wrapper_keys: tuple[str, ...] = ()) -> list[dict[str, Any]]:
    if isinstance(value, list):
        return [item for item in value if isinstance(item, dict)]
    if isinstance(value, dict):
        for key in wrapper_keys:
            nested = value.get(key)
            if isinstance(nested, list):
                return [item for item in nested if isinstance(item, dict)]
        return [value]
    return []


def load_json_file(path: Path, wrapper_keys: tuple[str, ...] = ()) -> list[dict[str, Any]]:
    payload = parse_json_text(path.read_text(encoding="utf-8"))
    return records_from_json_value(payload, wrapper_keys)


def iter_jsonl_lines(path: Path) -> Iterator[tuple[int, str]]:
    with path.open(encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, start=1):
            if line.strip():
                yield line_number, line.rstrip("\n")


def parse_jsonl_object(line: str) -> dict[str, Any]:
    payload = parse_json_text(line)
    if not isinstance(payload, dict):
        raise ValueError("JSONL line must be a JSON object")
    return payload


def as_optional_str(value: Any) -> str | None:
    if value is None:
        return None
    return str(value)


def as_optional_float(value: Any) -> float | None:
    if value is None or value == "":
        return None
    return float(value)


def as_optional_int_bool(value: Any) -> int | None:
    if value is None:
        return None
    return 1 if value else 0


def nested_get(record: dict[str, Any], *keys: str) -> Any:
    current: Any = record
    for key in keys:
        if not isinstance(current, dict):
            return None
        current = current.get(key)
    return current


def extract_document_id(content: str) -> str | None:
    match = DOCUMENT_ID_RE.search(content)
    return match.group(1) if match else None


def extract_title(content: str) -> str | None:
    match = TITLE_RE.search(content)
    return match.group(1).strip() if match else None


def extract_maintenance_report_id(content: str, filename: str) -> str | None:
    match = REPORT_ID_RE.search(content)
    if match:
        return match.group(1)
    stem = Path(filename).stem
    if stem.startswith("MNT-"):
        return stem.split(".")[0]
    return None


def generated_id(entity_type: str, source_path: str, line_number: int | None) -> str:
    suffix = str(line_number) if line_number is not None else "file"
    return f"generated:{entity_type}:{source_path}:{suffix}"
