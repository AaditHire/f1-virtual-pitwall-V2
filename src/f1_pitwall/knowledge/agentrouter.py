"""Secret-safe AgentRouter Anthropic-compatible provider adapter."""

from __future__ import annotations

import os
import random
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from time import perf_counter

import httpx
from dotenv import load_dotenv

from f1_pitwall.knowledge.generation import (
    GenerationResult,
    GroundedAnswerGenerator,
    GroundedEvidenceBundle,
    TokenUsage,
    build_generation_messages,
    parse_generated_answer,
)

AGENTROUTER_BASE_URL = "https://agentrouter.org"
AGENTROUTER_MODEL = "claude-opus-4-8"
ANTHROPIC_VERSION = "2023-06-01"
ENV_NAME = "AGENTROUTER_API_KEY"
PROJECT_ROOT = Path(__file__).resolve().parents[3]


def load_project_environment() -> bool:
    """Load the ignored project-root .env without overriding the process environment."""
    return load_dotenv(PROJECT_ROOT / ".env", override=False)


class AgentRouterError(RuntimeError):
    def __init__(self, category: str, message: str, status_code: int | None = None):
        super().__init__(f"AgentRouter {category}: {message}")
        self.category = category
        self.status_code = status_code


@dataclass(frozen=True)
class AgentRouterConfig:
    api_key: str = field(repr=False)
    model_id: str = AGENTROUTER_MODEL
    base_url: str = AGENTROUTER_BASE_URL
    temperature: float = 0.0
    max_output_tokens: int = 320
    timeout_seconds: float = 45.0
    max_retries: int = 2

    def __post_init__(self) -> None:
        if self.model_id != AGENTROUTER_MODEL:
            raise AgentRouterError("MISSING_MODEL", "Phase 13C permits only the frozen model")
        if self.base_url != AGENTROUTER_BASE_URL:
            raise AgentRouterError("INVALID_ENDPOINT", "Phase 13C permits only the frozen base URL")

    @classmethod
    def from_env(cls, model_id: str = AGENTROUTER_MODEL) -> AgentRouterConfig:
        load_project_environment()
        key = os.environ.get(ENV_NAME, "").strip()
        if not key:
            raise AgentRouterError("MISSING_KEY", f"{ENV_NAME} is not configured")
        return cls(api_key=key, model_id=model_id)

    def safe_metadata(self) -> dict:
        return {
            "provider": "AgentRouter",
            "protocol": "Anthropic-compatible Messages",
            "base_url": self.base_url,
            "model_id": self.model_id,
            "anthropic_version": ANTHROPIC_VERSION,
            "temperature": self.temperature,
            "max_output_tokens": self.max_output_tokens,
            "timeout_seconds": self.timeout_seconds,
            "max_retries": self.max_retries,
            "api_key_configured": bool(self.api_key),
        }


def _usage(payload: dict) -> TokenUsage:
    usage = payload.get("usage") or {}
    prompt = usage.get("input_tokens")
    completion = usage.get("output_tokens")
    total = prompt + completion if isinstance(prompt, int) and isinstance(completion, int) else None
    return TokenUsage(
        prompt_tokens=prompt,
        completion_tokens=completion,
        total_tokens=total,
        cached_tokens=usage.get("cache_read_input_tokens"),
    )


def _final_text(payload: dict) -> str:
    content = payload.get("content")
    if not isinstance(content, list):
        raise AgentRouterError("MALFORMED_RESPONSE", "generation response has no content array")
    text = "".join(
        block.get("text", "")
        for block in content
        if isinstance(block, dict) and block.get("type") == "text"
    ).strip()
    if not text:
        raise AgentRouterError("MALFORMED_RESPONSE", "generation response has no final text")
    return text


@dataclass(frozen=True)
class SmokeResult:
    text: str
    latency_ms: float
    usage: TokenUsage = field(default_factory=TokenUsage)
    request_count: int = 1
    retry_count: int = 0
    request_id: str | None = None


class AgentRouterGroundedAnswerGenerator(GroundedAnswerGenerator):
    def __init__(
        self,
        config: AgentRouterConfig,
        client: httpx.Client | None = None,
        sleep: Callable[[float], None] = time.sleep,
    ):
        self.config = config
        self.client = client or httpx.Client(timeout=config.timeout_seconds)
        self.sleep = sleep
        self.last_request_count = 0
        self.last_retry_count = 0

    @property
    def headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self.config.api_key}",
            "anthropic-version": ANTHROPIC_VERSION,
            "Content-Type": "application/json",
        }

    def smoke(self) -> SmokeResult:
        started = perf_counter()
        response, requests, retries = self._message_request(
            system="Reply exactly as requested.",
            messages=[{"role": "user", "content": "Reply only with OK."}],
            max_tokens=8,
        )
        payload = self._json(response, "smoke generation")
        return SmokeResult(
            text=_final_text(payload),
            latency_ms=(perf_counter() - started) * 1000,
            usage=_usage(payload),
            request_count=requests,
            retry_count=retries,
            request_id=response.headers.get("request-id") or response.headers.get("x-request-id"),
        )

    def generate(self, bundle: GroundedEvidenceBundle) -> GenerationResult:
        started = perf_counter()
        messages = build_generation_messages(bundle)
        response, requests, retries = self._message_request(
            system=messages[0]["content"],
            messages=messages[1:],
            max_tokens=self.config.max_output_tokens,
        )
        payload = self._json(response, "generation")
        try:
            answer = parse_generated_answer(_final_text(payload))
        except (ValueError, TypeError) as exc:
            raise AgentRouterError(
                "MALFORMED_RESPONSE", "generation answer is not valid structured JSON"
            ) from exc
        return GenerationResult(
            answer=answer,
            model_id=self.config.model_id,
            latency_ms=(perf_counter() - started) * 1000,
            usage=_usage(payload),
            request_count=requests,
            retry_count=retries,
            request_id=response.headers.get("request-id") or response.headers.get("x-request-id"),
        )

    def _message_request(
        self, *, system: str, messages: list[dict[str, str]], max_tokens: int
    ) -> tuple[httpx.Response, int, int]:
        return self._request(
            "POST",
            "/v1/messages",
            json={
                "model": self.config.model_id,
                "system": system,
                "messages": messages,
                "temperature": self.config.temperature,
                "max_tokens": max_tokens,
            },
        )

    def _json(self, response: httpx.Response, operation: str) -> dict:
        try:
            payload = response.json()
        except ValueError as exc:
            raise AgentRouterError(
                "MALFORMED_RESPONSE", f"{operation} returned malformed JSON"
            ) from exc
        if not isinstance(payload, dict):
            raise AgentRouterError(
                "MALFORMED_RESPONSE", f"{operation} returned an unexpected schema"
            )
        return payload

    def _request(self, method: str, path: str, **kwargs) -> tuple[httpx.Response, int, int]:
        requests = 0
        retries = 0
        for attempt in range(self.config.max_retries + 1):
            requests += 1
            self.last_request_count = requests
            self.last_retry_count = retries
            try:
                response = self.client.request(
                    method,
                    self.config.base_url.rstrip("/") + path,
                    headers=self.headers,
                    **kwargs,
                )
            except httpx.TimeoutException as exc:
                if attempt >= self.config.max_retries:
                    raise AgentRouterError(
                        "TIMEOUT", "request timed out after bounded retries"
                    ) from exc
                retries += 1
                self.last_retry_count = retries
                self.sleep(0.25 * (2**attempt) + random.uniform(0, 0.05))
                continue
            except httpx.RequestError as exc:
                raise AgentRouterError("CONNECTION_FAILURE", "provider connection failed") from exc
            if response.status_code in {401, 403}:
                category = (
                    "AUTH_FAILURE" if response.status_code == 401 else "AUTHORIZATION_FAILURE"
                )
                raise AgentRouterError(
                    category, "provider rejected authentication", response.status_code
                )
            if response.status_code == 404:
                raise AgentRouterError(
                    "MISSING_MODEL", "message endpoint or frozen model was not found", 404
                )
            if response.status_code == 429 or 500 <= response.status_code < 600:
                if attempt < self.config.max_retries:
                    retries += 1
                    self.last_retry_count = retries
                    self.sleep(0.25 * (2**attempt) + random.uniform(0, 0.05))
                    continue
                category = "RATE_LIMIT" if response.status_code == 429 else "SERVER_ERROR"
                raise AgentRouterError(
                    category, "transient provider failure exhausted retries", response.status_code
                )
            if response.is_error:
                raise AgentRouterError(
                    "REQUEST_FAILURE",
                    f"provider returned HTTP {response.status_code}",
                    response.status_code,
                )
            return response, requests, retries
        raise AssertionError("unreachable")


def redact_secret(value: object, secret: str) -> str:
    rendered = str(value)
    return rendered.replace(secret, "[REDACTED]") if secret else rendered
