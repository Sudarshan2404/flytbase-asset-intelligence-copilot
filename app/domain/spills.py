from __future__ import annotations

from typing import Any

CLASS_MINOR = "minor"
CLASS_MODERATE = "moderate"
CLASS_REPORTABLE = "reportable"
CLASS_MAJOR = "major"
CLASS_UNKNOWN_VOLUME = "unknown_volume"


def classify_spill_volume(volume_bbl: Any) -> str:
    if volume_bbl is None or volume_bbl == "":
        return CLASS_UNKNOWN_VOLUME
    volume = float(volume_bbl)
    if volume < 1:
        return CLASS_MINOR
    if volume < 5:
        return CLASS_MODERATE
    if volume < 25:
        return CLASS_REPORTABLE
    return CLASS_MAJOR
