from __future__ import annotations

from typing import Any

# T200 / T350 manuals, section "Production temperature rule".
PRODUCTION_DELTA_MIN_C = {
    "T200": 15.0,
    "T350": 20.0,
    "T500": 25.0,
}

STATUS_PRODUCING = "producing"
STATUS_NOT_PRODUCING = "not_producing"
STATUS_MISSING_MEASUREMENTS = "missing_measurements"
STATUS_UNKNOWN_WELL_TYPE = "unknown_well_type"
STATUS_NO_INSPECTION = "no_inspection"


def temperature_delta(wellhead_temp_c: Any, ambient_temp_c: Any) -> float | None:
    if wellhead_temp_c is None or ambient_temp_c is None:
        return None
    if wellhead_temp_c == "" or ambient_temp_c == "":
        return None
    return float(wellhead_temp_c) - float(ambient_temp_c)


def production_from_temps(
    well_type: str | None,
    wellhead_temp_c: Any,
    ambient_temp_c: Any,
) -> dict[str, Any]:
    """Return production status from a temperature pair. Never uses recorded_status."""
    delta = temperature_delta(wellhead_temp_c, ambient_temp_c)
    if well_type not in PRODUCTION_DELTA_MIN_C:
        return {
            "status": STATUS_UNKNOWN_WELL_TYPE,
            "producing": None,
            "delta_c": delta,
            "threshold_c": None,
            "well_type": well_type,
        }
    threshold = PRODUCTION_DELTA_MIN_C[well_type]
    if delta is None:
        return {
            "status": STATUS_MISSING_MEASUREMENTS,
            "producing": None,
            "delta_c": None,
            "threshold_c": threshold,
            "well_type": well_type,
        }
    producing = delta >= threshold
    return {
        "status": STATUS_PRODUCING if producing else STATUS_NOT_PRODUCING,
        "producing": producing,
        "delta_c": delta,
        "threshold_c": threshold,
        "well_type": well_type,
    }
