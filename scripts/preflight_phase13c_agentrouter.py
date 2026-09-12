"""One-request paid AgentRouter Anthropic-compatible preflight."""

from __future__ import annotations

import argparse
import json
from dataclasses import replace
from time import perf_counter

from f1_pitwall.knowledge.agentrouter import (
    AGENTROUTER_BASE_URL,
    AGENTROUTER_MODEL,
    AgentRouterConfig,
    AgentRouterError,
    AgentRouterGroundedAnswerGenerator,
)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.parse_args()
    started = perf_counter()
    generator = None
    report = {
        "provider": "AgentRouter",
        "protocol": "Anthropic-compatible Messages",
        "sdk_package": "anthropic",
        "base_url": AGENTROUTER_BASE_URL,
        "model": AGENTROUTER_MODEL,
        "api_key_configured": False,
        "reachable": False,
        "authentication": "NOT_TESTED",
        "minimal_generation": "NOT_RUN",
        "request_count": 0,
        "retry_count": 0,
    }
    try:
        config = AgentRouterConfig.from_env()
        config = replace(config, max_retries=0, max_output_tokens=8)
        report["sdk_version"] = config.safe_metadata()["sdk_version"]
        report["authentication_mode"] = "auth_token / Bearer"
        report["api_key_configured"] = True
        generator = AgentRouterGroundedAnswerGenerator(config)
        result = generator.smoke()
        report.update(
            reachable=True,
            authentication="PASS",
            minimal_generation="PASS" if result.text.strip() == "OK" else "FAIL",
            latency_ms=result.latency_ms,
            request_count=result.request_count,
            retry_count=result.retry_count,
            usage=result.usage.__dict__,
        )
        if result.text.strip() != "OK":
            report["failure_category"] = "MODEL_CAPABILITY_FAILURE"
            report["failure"] = "minimal response did not exactly match the requested token"
            print(json.dumps(report, indent=2))
            raise SystemExit(1)
        print(json.dumps(report, indent=2))
    except AgentRouterError as exc:
        report["reachable"] = exc.status_code is not None
        report["authentication"] = (
            "FAIL" if exc.category in {"AUTH_FAILURE", "AUTHORIZATION_FAILURE"} else "UNKNOWN"
        )
        report["http_status"] = exc.status_code
        report["failure_category"] = exc.category
        report["failure"] = str(exc)
        report["latency_ms"] = (perf_counter() - started) * 1000
        report["request_count"] = generator.last_request_count if generator else 0
        report["retry_count"] = generator.last_retry_count if generator else 0
        print(json.dumps(report, indent=2))
        raise SystemExit(1) from None


if __name__ == "__main__":
    main()
