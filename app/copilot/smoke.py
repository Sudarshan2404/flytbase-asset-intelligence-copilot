"""Optional live smoke test. Disabled unless GEMINI_API_KEY is set.

Run: python -m app.copilot.smoke
"""

from __future__ import annotations

import argparse
import json
import sys

from app.config import get_database_path
from app.copilot.config import CopilotConfigError, get_api_key, get_model_id
from app.copilot.schemas import ChatRequest
from app.copilot.service import handle_chat
from app.db import connect, init_schema


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Live copilot smoke test")
    parser.add_argument("--user-id", default="U-201")
    parser.add_argument("--message", default="Is WELL-0001 producing oil right now?")
    args = parser.parse_args(argv)
    try:
        get_api_key()
    except CopilotConfigError as exc:
        print(exc, file=sys.stderr)
        return 2
    print(f"Using model {get_model_id()}", file=sys.stderr)
    print("Native tool-calling is attempted once; Gemma HTTP 500 falls back once to prompt tools.", file=sys.stderr)
    conn = connect(get_database_path())
    init_schema(conn)
    try:
        response = handle_chat(conn, ChatRequest(message=args.message, user_id=args.user_id))
    finally:
        conn.close()
    print(json.dumps(response.model_dump(), indent=2))
    return 0 if not response.error else 1


if __name__ == "__main__":
    raise SystemExit(main())
