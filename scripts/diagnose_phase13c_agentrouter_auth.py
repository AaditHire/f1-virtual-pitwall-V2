"""Secret-safe local diagnosis for Phase 13C AgentRouter authentication."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path

from dotenv import dotenv_values

from f1_pitwall.knowledge.agentrouter import (
    AGENTROUTER_BASE_URL,
    AGENTROUTER_MODEL,
    ENV_NAME,
    AgentRouterConfig,
)

ROOT = Path(__file__).resolve().parents[1]
DOTENV_PATH = ROOT / ".env"


def secret_metadata(value: str | None) -> dict[str, object]:
    present = bool(value)
    return {
        "present": present,
        "length": len(value) if value is not None else 0,
        "sha256_prefix": hashlib.sha256(value.encode()).hexdigest()[:10] if present else None,
    }


def dotenv_format_metadata(path: Path, variable: str) -> dict[str, object]:
    if not path.exists():
        return {
            "file_present": False,
            "utf8_bom": False,
            "surrounding_quotes": False,
            "leading_space": False,
            "trailing_space": False,
            "line_ending": None,
        }
    raw = path.read_bytes()
    text = raw.decode("utf-8-sig")
    matching = next(
        (
            line
            for line in text.splitlines(keepends=True)
            if line.lstrip().startswith(variable + "=")
        ),
        None,
    )
    if matching is None:
        return {
            "file_present": True,
            "utf8_bom": raw.startswith(b"\xef\xbb\xbf"),
            "variable_line_present": False,
            "surrounding_quotes": False,
            "leading_space": False,
            "trailing_space": False,
            "line_ending": None,
        }
    line_ending = (
        "CRLF" if matching.endswith("\r\n") else "LF" if matching.endswith("\n") else "NONE"
    )
    body = matching.rstrip("\r\n")
    raw_value = body.split("=", 1)[1]
    trimmed = raw_value.strip()
    quoted = len(trimmed) >= 2 and trimmed[0] in {'"', "'"} and trimmed[-1] == trimmed[0]
    return {
        "file_present": True,
        "utf8_bom": raw.startswith(b"\xef\xbb\xbf"),
        "variable_line_present": True,
        "surrounding_quotes": quoted,
        "leading_space": raw_value != raw_value.lstrip(),
        "trailing_space": raw_value != raw_value.rstrip(),
        "line_ending": line_ending,
    }


def build_report() -> dict[str, object]:
    process_value = os.environ.get(ENV_NAME)
    dotenv_value = dotenv_values(DOTENV_PATH, encoding="utf-8-sig").get(ENV_NAME)
    anthropic_value = os.environ.get("ANTHROPIC_AUTH_TOKEN")
    config = AgentRouterConfig.from_env()
    process_differs = bool(
        process_value and dotenv_value and process_value.strip() != str(dotenv_value).strip()
    )
    selected_source = "PROCESS_ENV" if process_value and process_value.strip() else "PROJECT_DOTENV"
    return {
        "sources": {
            "process_agentrouter_api_key": secret_metadata(process_value),
            "dotenv_agentrouter_api_key": secret_metadata(
                str(dotenv_value) if dotenv_value is not None else None
            ),
            "process_anthropic_auth_token": secret_metadata(anthropic_value),
            "selected_agentrouter_api_key": secret_metadata(config.api_key),
        },
        "selected_source": selected_source,
        "stale_process_env_override": process_differs,
        "diagnostic": "STALE_PROCESS_ENV_OVERRIDE" if process_differs else "NO_STALE_OVERRIDE",
        "selected_matches_dotenv": bool(
            dotenv_value is not None and config.api_key == str(dotenv_value).strip()
        ),
        "selected_matches_anthropic_auth_token": bool(
            anthropic_value and config.api_key == anthropic_value.strip()
        ),
        "dotenv_format": dotenv_format_metadata(DOTENV_PATH, ENV_NAME),
        "request_contract": {
            "provider": "AgentRouter",
            "protocol": "Anthropic-compatible Messages via official Anthropic SDK",
            "base_url": AGENTROUTER_BASE_URL,
            "endpoint_path": "/v1/messages",
            "model": AGENTROUTER_MODEL,
            "sdk_package": "anthropic",
            "sdk_version": config.safe_metadata()["sdk_version"],
            "credential_constructor_argument": "auth_token",
            "authentication_mode": "auth_token / Bearer",
            "protocol_construction": "delegated to official SDK",
        },
    }


def main() -> None:
    print(json.dumps(build_report(), indent=2))


if __name__ == "__main__":
    main()
