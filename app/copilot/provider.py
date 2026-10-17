from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field
from typing import Any, Protocol

from app.copilot.config import CopilotConfigError, get_api_key, get_model_id


_SECRET_RE = re.compile(
    r"(?i)(api[_-]?key|token|authorization|bearer|key)\s*[=:]\s*['\"]?[^\s'\"\\]+"
)
_GOOGLE_KEY_RE = re.compile(r"AIza[0-9A-Za-z_\-]{10,}")
_GEMMA_CALL_RE = re.compile(
    r"<\|tool_call\|?>\s*call:([A-Za-z0-9_]+)\{(.*?)\}\s*<tool_call\|>",
    re.DOTALL,
)
_JSON_CALL_RE = re.compile(
    r"\{[^{}]*\"(?:name|tool)\"\s*:\s*\"([A-Za-z0-9_]+)\"[^{}]*\}",
    re.DOTALL,
)


def sanitize_provider_text(text: str) -> str:
    cleaned = _GOOGLE_KEY_RE.sub("[REDACTED]", text or "")
    return _SECRET_RE.sub(r"\1=[REDACTED]", cleaned)


class ProviderError(RuntimeError):
    """Sanitized model-provider failure. Never includes API keys."""

    def __init__(
        self,
        message: str,
        *,
        http_status: int | None = None,
        provider_status: str | None = None,
        kind: str = "provider_error",
    ) -> None:
        self.http_status = http_status
        self.provider_status = provider_status
        self.kind = kind
        super().__init__(sanitize_provider_text(message))

    def sanitized_message(self) -> str:
        parts = [self.kind]
        if self.http_status is not None:
            parts.append(f"http_{self.http_status}")
        if self.provider_status:
            parts.append(str(self.provider_status))
        detail = sanitize_provider_text(str(self))
        return f"{':'.join(parts)}: {detail}"


def classify_provider_error(exc: Exception) -> ProviderError:
    http_status = getattr(exc, "code", None)
    if not isinstance(http_status, int):
        http_status = getattr(exc, "status_code", None)
    provider_status = getattr(exc, "status", None)
    message = getattr(exc, "message", None) or str(exc)
    if isinstance(http_status, int) and 500 <= http_status <= 599:
        kind = "provider_http_error"
    elif isinstance(http_status, int) and 400 <= http_status <= 499:
        kind = "provider_http_error"
    else:
        kind = "provider_error"
        http_status = http_status if isinstance(http_status, int) else None
    return ProviderError(
        str(message),
        http_status=http_status,
        provider_status=str(provider_status) if provider_status else None,
        kind=kind,
    )


@dataclass
class FunctionCall:
    name: str
    arguments: dict[str, Any] = field(default_factory=dict)
    call_id: str | None = None


@dataclass
class ModelTurn:
    text: str | None = None
    function_calls: list[FunctionCall] = field(default_factory=list)


class ModelProvider(Protocol):
    def generate(self, contents: list[dict[str, Any]], tools: list[dict[str, Any]]) -> ModelTurn:
        """Return the next model turn. Must not execute tools itself."""


def sdk_function_declarations(tools: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Official Gemini/Gemma tool format: name, description, parameters.

    The hosted Gemma docs pass these dicts to ``types.Tool(function_declarations=...)``.
    They use the ``parameters`` field, not ``parameters_json_schema``.
    """
    declarations = []
    for tool in tools:
        schema = tool.get("parameters") or {"type": "object", "properties": {}}
        if not isinstance(schema, dict):
            schema = {"type": "object", "properties": {}}
        declarations.append(
            {
                "name": tool["name"],
                "description": tool.get("description", ""),
                "parameters": schema,
            }
        )
    return declarations


def parse_text_function_calls(text: str | None) -> list[FunctionCall]:
    if not text:
        return []
    calls: list[FunctionCall] = []
    for match in _GEMMA_CALL_RE.finditer(text):
        calls.append(FunctionCall(name=match.group(1), arguments=_parse_gemma_args(match.group(2))))
    if calls:
        return calls
    stripped = text.strip()
    try:
        payload = json.loads(stripped)
    except json.JSONDecodeError:
        match = _JSON_CALL_RE.search(stripped)
        if not match:
            return []
        try:
            payload = json.loads(match.group(0))
        except json.JSONDecodeError:
            return []
    if not isinstance(payload, dict):
        return []
    name = payload.get("name") or payload.get("tool")
    arguments = payload.get("arguments") or payload.get("args") or {}
    if isinstance(name, str) and name and isinstance(arguments, dict) and "answer" not in payload:
        return [FunctionCall(name=name, arguments=arguments)]
    return []


def _parse_gemma_args(raw: str) -> dict[str, Any]:
    args: dict[str, Any] = {}
    for piece in re.finditer(r"([A-Za-z0-9_]+)\s*:\s*(?:<\|\"\|>)?(.*?)(?:<\|\"\|>)?(?=,|$)", raw):
        key = piece.group(1)
        value = piece.group(2).strip().rstrip(",")
        if value.startswith("<|\"|>") and value.endswith("<|\"|>"):
            value = value[5:-5]
        args[key] = value
    return args


def parse_generate_content_response(response: Any) -> ModelTurn:
    calls: list[FunctionCall] = []
    for fc in getattr(response, "function_calls", None) or []:
        name = getattr(fc, "name", None)
        if not name:
            continue
        raw_args = getattr(fc, "args", None) or {}
        calls.append(FunctionCall(name=str(name), arguments=dict(raw_args), call_id=getattr(fc, "id", None)))

    text_parts: list[str] = []
    candidates = getattr(response, "candidates", None) or []
    if candidates:
        content = getattr(candidates[0], "content", None)
        parts = getattr(content, "parts", None) or []
        for part in parts:
            function_call = getattr(part, "function_call", None)
            if function_call and getattr(function_call, "name", None):
                if not any(item.name == function_call.name for item in calls):
                    raw_args = getattr(function_call, "args", None) or {}
                    calls.append(
                        FunctionCall(
                            name=str(function_call.name),
                            arguments=dict(raw_args),
                            call_id=getattr(function_call, "id", None),
                        )
                    )
            elif getattr(part, "text", None):
                text_parts.append(part.text)
    elif getattr(response, "text", None):
        text_parts.append(response.text)

    text = "".join(text_parts) or getattr(response, "text", None)
    if not calls:
        calls = parse_text_function_calls(text)
        if calls:
            text = None
    if calls:
        text = None
    return ModelTurn(text=text, function_calls=calls)


def _split_system_instruction(contents: list[dict[str, Any]]) -> tuple[str | None, list[dict[str, Any]]]:
    if not contents:
        return None, contents
    first = contents[0]
    parts = first.get("parts") or []
    text = parts[0].get("text") if parts and isinstance(parts[0], dict) else None
    if isinstance(text, str) and text.startswith("You are the FlytBase Asset Intelligence Copilot"):
        return text, contents[1:]
    return None, contents


def _catalog_instruction(tools: list[dict[str, Any]]) -> str:
    catalog = json.dumps(sdk_function_declarations(tools), separators=(",", ":"))
    return (
        "Use only these tools. If a tool is required, reply with JSON only: "
        '{"name":"<tool>","arguments":{...}}. '
        "Do not invent record IDs. Do not write Python, SQL, or shell. "
        f"Tools: {catalog}"
    )


class GeminiProvider:
    """Thin google-genai adapter. Swap this class to change providers."""

    def __init__(self, api_key: str, model: str, client: Any | None = None) -> None:
        self._api_key = api_key
        self.model = model
        self._client = client
        self._prompt_tools = os.environ.get("COPILOT_TOOL_MODE", "").strip().lower() == "prompt"

    @classmethod
    def from_env(cls) -> GeminiProvider:
        return cls(get_api_key(), get_model_id())

    def _sdk_client(self) -> Any:
        if self._client is not None:
            return self._client
        try:
            from google import genai
        except ImportError as exc:
            raise CopilotConfigError(
                "The google-genai package is not installed. Run pip install -r requirements.txt."
            ) from exc
        return genai.Client(api_key=self._api_key)

    def _generate_content(self, client: Any, contents: list[dict[str, Any]], config: Any) -> Any:
        try:
            return client.models.generate_content(model=self.model, contents=contents, config=config)
        except Exception as exc:
            raise classify_provider_error(exc) from exc

    def generate(self, contents: list[dict[str, Any]], tools: list[dict[str, Any]]) -> ModelTurn:
        try:
            from google.genai import types
        except ImportError as exc:
            raise CopilotConfigError(
                "The google-genai package is not installed. Run pip install -r requirements.txt."
            ) from exc

        client = self._sdk_client()
        system_instruction, request_contents = _split_system_instruction(contents)
        declarations = sdk_function_declarations(tools)

        if self._prompt_tools:
            return parse_generate_content_response(
                self._generate_prompt(client, types, request_contents, system_instruction, tools)
            )

        config_kwargs: dict[str, Any] = {}
        if system_instruction:
            config_kwargs["system_instruction"] = system_instruction
        if declarations:
            # Official Gemma/Gemini hosted format: Tool + parameters dicts. No AFC.
            config_kwargs["tools"] = [types.Tool(function_declarations=declarations)]
        try:
            response = self._generate_content(
                client,
                request_contents,
                types.GenerateContentConfig(**config_kwargs) if config_kwargs else None,
            )
            return parse_generate_content_response(response)
        except ProviderError as exc:
            if self._should_fallback_to_prompt(exc, tools):
                self._prompt_tools = True
                return parse_generate_content_response(
                    self._generate_prompt(client, types, request_contents, system_instruction, tools)
                )
            raise

    def _should_fallback_to_prompt(self, exc: ProviderError, tools: list[dict[str, Any]]) -> bool:
        if not tools:
            return False
        if exc.http_status != 500:
            return False
        return self.model.startswith("gemma-")

    def _generate_prompt(
        self,
        client: Any,
        types: Any,
        contents: list[dict[str, Any]],
        system_instruction: str | None,
        tools: list[dict[str, Any]],
    ) -> Any:
        instruction = " ".join(part for part in (system_instruction, _catalog_instruction(tools)) if part)
        return self._generate_content(
            client,
            contents,
            types.GenerateContentConfig(system_instruction=instruction),
        )


_override_provider: ModelProvider | None = None


def set_provider(provider: ModelProvider | None) -> None:
    global _override_provider
    _override_provider = provider


def get_provider() -> ModelProvider:
    if _override_provider is not None:
        return _override_provider
    return GeminiProvider.from_env()
