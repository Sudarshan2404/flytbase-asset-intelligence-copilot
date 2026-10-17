from __future__ import annotations

import os

DEFAULT_MODEL = "gemma-4-26b-a4b-it"
MAX_TOOL_ITERATIONS = 6


class CopilotConfigError(RuntimeError):
    """Raised when the model provider is not configured."""


def get_api_key() -> str:
    key = os.environ.get("GEMINI_API_KEY", "").strip()
    if not key:
        raise CopilotConfigError(
            "GEMINI_API_KEY is not set. Configure a Google AI Studio key before using /chat."
        )
    return key


def get_model_id() -> str:
    return os.environ.get("COPILOT_MODEL", DEFAULT_MODEL).strip() or DEFAULT_MODEL
