"""Secret-safe AgentRouter OpenAI-compatible provider adapter."""

from __future__ import annotations

import os
import random
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from time import perf_counter

import httpx

from f1_pitwall.knowledge.generation import (
    GenerationResult,
    GroundedAnswerGenerator,
    GroundedEvidenceBundle,
    TokenUsage,
    build_generation_messages,
    parse_generated_answer,
)

AGENTROUTER_BASE_URL = "https://co.agentrouter.org/v1"
ENV_NAME = "AGENTROUTER_API_KEY"


class AgentRouterError(RuntimeError):
    def __init__(self, category: str, message: str, status_code: int | None = None):
        super().__init__(f"AgentRouter {category}: {message}")
        self.category = category
        self.status_code = status_code


@dataclass(frozen=True)
class AgentRouterConfig:
    api_key: str = field(repr=False)
    model_id: str | None = None
    base_url: str = AGENTROUTER_BASE_URL
    temperature: float = 0.0
    max_output_tokens: int = 320
    timeout_seconds: float = 45.0
    max_retries: int = 2

    @classmethod
    def from_env(cls, model_id: str | None = None) -> AgentRouterConfig:
        key = os.environ.get(ENV_NAME, "").strip()
        if not key:
            raise AgentRouterError("MISSING_KEY", f"{ENV_NAME} is not configured")
        return cls(api_key=key, model_id=model_id or os.environ.get("AGENTROUTER_MODEL") or None)

    def safe_metadata(self) -> dict:
        return {
            "provider": "AgentRouter",
            "base_url": self.base_url,
            "model_id": self.model_id,
            "temperature": self.temperature,
            "max_output_tokens": self.max_output_tokens,
            "timeout_seconds": self.timeout_seconds,
            "max_retries": self.max_retries,
            "api_key_configured": bool(self.api_key),
        }


def parse_model_ids(payload: dict) -> list[str]:
    rows = payload.get("data")
    if not isinstance(rows, list):
        raise AgentRouterError("MALFORMED_RESPONSE", "model listing has no data array")
    model_ids = sorted(
        {
            row.get("id")
            for row in rows
            if isinstance(row, dict) and isinstance(row.get("id"), str) and row["id"].strip()
        }
    )
    if not model_ids:
        raise AgentRouterError("MISSING_MODEL", "model listing returned no model IDs")
    return model_ids


def select_model(model_ids: list[str]) -> tuple[str, list[str]]:
    non_claude = [value for value in model_ids if "claude" not in value.casefold()]
    general = [value for value in non_claude if "code" not in value.casefold()]
    candidates = general or non_claude
    if not candidates:
        raise AgentRouterError(
            "PROTOCOL_REQUIRED",
            "only Claude-family models are available; the required Anthropic protocol "
            "was not enabled",
        )
    preferences = ("gpt-5.5", "gpt-5", "kimi-k2.6", "glm-5.2", "glm-5.1")
    ranked = sorted(
        candidates,
        key=lambda value: next(
            (index for index, token in enumerate(preferences) if token in value.casefold()),
            len(preferences),
        ),
    )
    return ranked[0], ranked[:5]


def _usage(payload: dict) -> TokenUsage:
    usage = payload.get("usage") or {}
    details = usage.get("completion_tokens_details") or {}
    prompt_details = usage.get("prompt_tokens_details") or {}
    return TokenUsage(
        prompt_tokens=usage.get("prompt_tokens"),
        completion_tokens=usage.get("completion_tokens"),
        total_tokens=usage.get("total_tokens"),
        reasoning_tokens=details.get("reasoning_tokens"),
        cached_tokens=prompt_details.get("cached_tokens"),
    )


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

    @property
    def headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self.config.api_key}",
            "Content-Type": "application/json",
        }

    def list_models(self) -> list[str]:
        response = self._request("GET", "/models")
        return parse_model_ids(self._json(response, "model listing"))

    def generate(self, bundle: GroundedEvidenceBundle) -> GenerationResult:
        if not self.config.model_id:
            raise AgentRouterError("MISSING_MODEL", "no exact AgentRouter model ID is configured")
        started = perf_counter()
        response, requests, retries = self._request(
            "POST",
            "/chat/completions",
            json={
                "model": self.config.model_id,
                "messages": build_generation_messages(bundle),
                "temperature": self.config.temperature,
                "max_tokens": self.config.max_output_tokens,
            },
            with_counts=True,
        )
        payload = self._json(response, "generation")
        try:
            text = payload["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError) as exc:
            raise AgentRouterError(
                "MALFORMED_RESPONSE", "generation response has no message text"
            ) from exc
        if not isinstance(text, str) or not text.strip():
            raise AgentRouterError("MALFORMED_RESPONSE", "generation response text is empty")
        try:
            answer = parse_generated_answer(text)
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
            request_id=response.headers.get("x-request-id"),
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

    def _request(self, method: str, path: str, *, with_counts: bool = False, **kwargs):
        requests = 0
        retries = 0
        for attempt in range(self.config.max_retries + 1):
            requests += 1
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
                    "MISSING_MODEL", "endpoint or selected model was not found", 404
                )
            if response.status_code == 429 or 500 <= response.status_code < 600:
                if attempt < self.config.max_retries:
                    retries += 1
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
            return (response, requests, retries) if with_counts else response
        raise AssertionError("unreachable")


def redact_secret(value: object, secret: str) -> str:
    rendered = str(value)
    return rendered.replace(secret, "[REDACTED]") if secret else rendered
