"""Secret-safe AgentRouter provider backed by the official Anthropic SDK."""

from __future__ import annotations

import os
import random
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from time import perf_counter
from typing import Any

import anthropic
from dotenv import load_dotenv

from f1_pitwall.knowledge.generation import (
    GeneratedAnswer,
    GenerationResult,
    GroundedAnswerGenerator,
    GroundedEvidenceBundle,
    TokenUsage,
    build_generation_messages,
    validate_citations,
)

AGENTROUTER_BASE_URL = "https://agentrouter.org"
AGENTROUTER_MODEL = "claude-opus-4-8"
ENV_NAME = "AGENTROUTER_API_KEY"
PROJECT_ROOT = Path(__file__).resolve().parents[3]
GROUNDED_ANSWER_TOOL_NAME = "grounded_answer"
GROUNDED_ANSWER_TOOL = {
    "name": GROUNDED_ANSWER_TOOL_NAME,
    "description": "Return the grounded answer payload. This does not execute an action.",
    "input_schema": GeneratedAnswer.model_json_schema(),
}


def load_project_environment() -> bool:
    """Load the ignored project-root .env without overriding the process environment."""
    return load_dotenv(PROJECT_ROOT / ".env", override=False, encoding="utf-8-sig")


class AgentRouterError(RuntimeError):
    def __init__(
        self,
        category: str,
        message: str,
        status_code: int | None = None,
        *,
        usage: TokenUsage | None = None,
        latency_ms: float | None = None,
        request_count: int = 0,
        retry_count: int = 0,
        request_id: str | None = None,
        generated_answer: GeneratedAnswer | None = None,
    ):
        super().__init__(f"AgentRouter {category}: {message}")
        self.category = category
        self.status_code = status_code
        self.usage = usage or TokenUsage()
        self.latency_ms = latency_ms
        self.request_count = request_count
        self.retry_count = retry_count
        self.request_id = request_id
        self.generated_answer = generated_answer


@dataclass(frozen=True)
class AgentRouterConfig:
    api_key: str = field(repr=False)
    model_id: str = AGENTROUTER_MODEL
    base_url: str = AGENTROUTER_BASE_URL
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
            "protocol": "Anthropic-compatible Messages via official Anthropic SDK",
            "sdk_package": "anthropic",
            "sdk_version": anthropic.__version__,
            "authentication_mode": "auth_token / Bearer",
            "base_url": self.base_url,
            "model_id": self.model_id,
            "temperature": "SDK default (not overridden)",
            "max_output_tokens": self.max_output_tokens,
            "timeout_seconds": self.timeout_seconds,
            "max_retries": self.max_retries,
            "api_key_configured": bool(self.api_key),
        }


def _usage(message: Any) -> TokenUsage:
    usage = getattr(message, "usage", None)
    prompt = getattr(usage, "input_tokens", None)
    completion = getattr(usage, "output_tokens", None)
    total = prompt + completion if isinstance(prompt, int) and isinstance(completion, int) else None
    return TokenUsage(
        prompt_tokens=prompt,
        completion_tokens=completion,
        total_tokens=total,
        cached_tokens=getattr(usage, "cache_read_input_tokens", None),
    )


def _final_text(message: Any) -> str:
    content = getattr(message, "content", None)
    if not isinstance(content, list):
        raise AgentRouterError("MALFORMED_RESPONSE", "generation response has no content array")
    text = "".join(
        getattr(block, "text", "") for block in content if getattr(block, "type", None) == "text"
    ).strip()
    if not text:
        raise AgentRouterError("MALFORMED_RESPONSE", "generation response has no final text")
    return text


def _structured_tool_answer(message: Any) -> GeneratedAnswer:
    content = getattr(message, "content", None)
    if not isinstance(content, list):
        raise ValueError("generation response has no content array")
    tool_blocks = [block for block in content if getattr(block, "type", None) == "tool_use"]
    if len(tool_blocks) != 1:
        raise ValueError("generation response must contain exactly one tool-use block")
    block = tool_blocks[0]
    if getattr(block, "name", None) != GROUNDED_ANSWER_TOOL_NAME:
        raise ValueError("generation response used an unexpected tool name")
    return GeneratedAnswer.model_validate(getattr(block, "input", None))


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
        client: Any | None = None,
        sleep: Callable[[float], None] = time.sleep,
    ):
        self.config = config
        self.client = client or anthropic.Anthropic(
            auth_token=config.api_key,
            base_url=config.base_url,
            timeout=config.timeout_seconds,
            max_retries=0,
        )
        self.sleep = sleep
        self.last_request_count = 0
        self.last_retry_count = 0

    def smoke(self) -> SmokeResult:
        started = perf_counter()
        message, requests, retries = self._create_message(
            system="Reply exactly as requested.",
            messages=[{"role": "user", "content": "Reply only with OK."}],
            max_tokens=8,
        )
        return SmokeResult(
            text=_final_text(message),
            latency_ms=(perf_counter() - started) * 1000,
            usage=_usage(message),
            request_count=requests,
            retry_count=retries,
            request_id=getattr(message, "_request_id", None),
        )

    def generate(self, bundle: GroundedEvidenceBundle) -> GenerationResult:
        started = perf_counter()
        prompt = build_generation_messages(bundle)
        message, requests, retries = self._create_message(
            system=prompt[0]["content"],
            messages=prompt[1:],
            max_tokens=self.config.max_output_tokens,
            structured=True,
        )
        latency_ms = (perf_counter() - started) * 1000
        usage = _usage(message)
        request_id = getattr(message, "_request_id", None)
        try:
            answer = _structured_tool_answer(message)
        except (ValueError, TypeError) as exc:
            raise AgentRouterError(
                "MALFORMED_RESPONSE",
                "generation answer did not satisfy the forced grounded-answer schema",
                usage=usage,
                latency_ms=latency_ms,
                request_count=requests,
                retry_count=retries,
                request_id=request_id,
            ) from exc
        citation_validation = validate_citations(answer, bundle)
        if citation_validation.unknown or citation_validation.malformed:
            raise AgentRouterError(
                "INVALID_CITATION",
                "generation answer contains a source ID outside the evidence bundle",
                usage=usage,
                latency_ms=latency_ms,
                request_count=requests,
                retry_count=retries,
                request_id=request_id,
                generated_answer=answer,
            )
        return GenerationResult(
            answer=answer,
            model_id=self.config.model_id,
            latency_ms=latency_ms,
            usage=usage,
            request_count=requests,
            retry_count=retries,
            request_id=request_id,
        )

    def _create_message(
        self,
        *,
        system: str,
        messages: list[dict[str, str]],
        max_tokens: int,
        structured: bool = False,
    ) -> tuple[Any, int, int]:
        requests = 0
        retries = 0
        for attempt in range(self.config.max_retries + 1):
            requests += 1
            self.last_request_count = requests
            self.last_retry_count = retries
            try:
                request = {
                    "model": self.config.model_id,
                    "system": system,
                    "messages": messages,
                    "max_tokens": max_tokens,
                }
                if structured:
                    request.update(
                        tools=[GROUNDED_ANSWER_TOOL],
                        tool_choice={
                            "type": "tool",
                            "name": GROUNDED_ANSWER_TOOL_NAME,
                            "disable_parallel_tool_use": True,
                        },
                    )
                message = self.client.messages.create(**request)
                return message, requests, retries
            except (
                anthropic.APITimeoutError,
                anthropic.RateLimitError,
                anthropic.InternalServerError,
            ) as exc:
                if attempt >= self.config.max_retries:
                    category = (
                        "TIMEOUT"
                        if isinstance(exc, anthropic.APITimeoutError)
                        else "RATE_LIMIT"
                        if isinstance(exc, anthropic.RateLimitError)
                        else "SERVER_ERROR"
                    )
                    raise AgentRouterError(
                        category,
                        "transient provider failure exhausted retries",
                        getattr(exc, "status_code", None),
                    ) from exc
                retries += 1
                self.last_retry_count = retries
                self.sleep(0.25 * (2**attempt) + random.uniform(0, 0.05))
            except anthropic.AuthenticationError as exc:
                raise AgentRouterError(
                    "AUTH_FAILURE", "provider rejected authentication", exc.status_code
                ) from exc
            except anthropic.PermissionDeniedError as exc:
                raise AgentRouterError(
                    "AUTHORIZATION_FAILURE", "provider rejected authorization", exc.status_code
                ) from exc
            except anthropic.NotFoundError as exc:
                raise AgentRouterError(
                    "MISSING_MODEL",
                    "message endpoint or frozen model was not found",
                    exc.status_code,
                ) from exc
            except anthropic.APIConnectionError as exc:
                raise AgentRouterError("CONNECTION_FAILURE", "provider connection failed") from exc
            except anthropic.APIResponseValidationError as exc:
                raise AgentRouterError(
                    "MALFORMED_RESPONSE", "provider returned an invalid response schema"
                ) from exc
            except anthropic.APIStatusError as exc:
                raise AgentRouterError(
                    "REQUEST_FAILURE",
                    f"provider returned HTTP {exc.status_code}",
                    exc.status_code,
                ) from exc
        raise AssertionError("unreachable")


def redact_secret(value: object, secret: str) -> str:
    rendered = str(value)
    return rendered.replace(secret, "[REDACTED]") if secret else rendered
