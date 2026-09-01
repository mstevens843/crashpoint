"""Anthropic Messages API sampler for the optional real-model arm.

This script is intentionally tiny and stdlib-only. It reads the prompt from stdin, reads
credentials from environment variables, and prints exactly the first text block from the response.
It is meant to be used as:

    CRASHPOINT_NONDET_SOURCE=model \
    CRASHPOINT_MODEL_SAMPLER_CMD='python scripts/anthropic_sampler.py' \
    uv run python -m crashpoint.harness.matrix ...

Required environment:
  ANTHROPIC_API_KEY
  ANTHROPIC_MODEL

Optional environment:
  ANTHROPIC_WORKSPACE_ID
  ANTHROPIC_MAX_TOKENS
  ANTHROPIC_VERSION
  ANTHROPIC_API_URL
"""

from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path

_DEFAULT_API_URL = "https://api.anthropic.com/v1/messages"
_DEFAULT_VERSION = "2023-06-01"
_DEFAULT_MAX_TOKENS = "32"
_MAX_ERROR_CHARS = 600


def _load_dotenv() -> None:
    """Load a local .env file without overriding already-exported variables."""
    env_path = Path(__file__).resolve().parents[1] / ".env"
    if not env_path.exists():
        return
    for raw_line in env_path.read_text().splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line.removeprefix("export ").strip()
        key, sep, value = line.partition("=")
        if not sep:
            continue
        key = key.strip()
        value = value.strip().strip("'\"")
        if key and key not in os.environ:
            os.environ[key] = value


def _required_env(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise RuntimeError(f"{name} is required")
    return value


def _extract_text(payload: dict[str, object]) -> str:
    content = payload.get("content")
    if not isinstance(content, list):
        raise RuntimeError("Anthropic response has no content list")
    for block in content:
        if isinstance(block, dict) and block.get("type") == "text":
            text = block.get("text")
            if isinstance(text, str) and text.strip():
                return text.strip()
    raise RuntimeError("Anthropic response has no non-empty text block")


def sample(prompt: str) -> str:
    api_key = _required_env("ANTHROPIC_API_KEY")
    model = _required_env("ANTHROPIC_MODEL")
    try:
        max_tokens = int(os.environ.get("ANTHROPIC_MAX_TOKENS", _DEFAULT_MAX_TOKENS))
    except ValueError as exc:
        raise RuntimeError("ANTHROPIC_MAX_TOKENS must be an integer") from exc
    if max_tokens < 1:
        raise RuntimeError("ANTHROPIC_MAX_TOKENS must be positive")

    body = {
        "model": model,
        "max_tokens": max_tokens,
        "messages": [{"role": "user", "content": prompt}],
    }
    headers = {
        "content-type": "application/json",
        "x-api-key": api_key,
        "anthropic-version": os.environ.get("ANTHROPIC_VERSION", _DEFAULT_VERSION),
    }
    workspace_id = os.environ.get("ANTHROPIC_WORKSPACE_ID", "").strip()
    if workspace_id:
        headers["anthropic-workspace-id"] = workspace_id

    request = urllib.request.Request(
        os.environ.get("ANTHROPIC_API_URL", _DEFAULT_API_URL),
        data=json.dumps(body).encode("utf-8"),
        headers=headers,
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            response_body = response.read()
    except urllib.error.HTTPError as exc:
        body_text = exc.read().decode("utf-8", errors="replace")[:_MAX_ERROR_CHARS]
        raise RuntimeError(f"Anthropic API returned HTTP {exc.code}: {body_text}") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"Anthropic API request failed: {exc}") from exc

    decoded = json.loads(response_body)
    if not isinstance(decoded, dict):
        raise RuntimeError("Anthropic response was not a JSON object")
    return _extract_text(decoded)


def main() -> int:
    _load_dotenv()
    prompt = sys.stdin.read().strip()
    if not prompt:
        print("prompt on stdin is required", file=sys.stderr)
        return 2
    try:
        print(sample(prompt))
    except RuntimeError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
