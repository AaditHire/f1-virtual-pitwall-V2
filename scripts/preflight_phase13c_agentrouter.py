"""Paid-network AgentRouter preflight; never prints or persists the API key."""

from __future__ import annotations

import argparse
import json

from f1_pitwall.knowledge.agentrouter import (
    AgentRouterConfig,
    AgentRouterError,
    AgentRouterGroundedAnswerGenerator,
    select_model,
)
from f1_pitwall.knowledge.generation import EvidenceState, GroundedEvidenceBundle


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--model", help="exact ID returned by this key's /v1/models response")
    args = parser.parse_args()
    report = {
        "provider": "AgentRouter",
        "base_url": "https://co.agentrouter.org/v1",
        "protocol": "OpenAI-compatible chat completions",
        "api_key_configured": False,
        "reachable": False,
        "authentication_valid": False,
        "models_retrieved": 0,
        "selected_model_available": False,
        "minimal_generation": "NOT_RUN",
    }
    try:
        initial = AgentRouterConfig.from_env()
        report["api_key_configured"] = True
        discovery = AgentRouterGroundedAnswerGenerator(initial)
        models = discovery.list_models()
        report.update(reachable=True, authentication_valid=True, models_retrieved=len(models))
        selected, candidates = (args.model, [args.model]) if args.model else select_model(models)
        if selected not in models:
            raise AgentRouterError(
                "MISSING_MODEL", "configured model is absent from key-specific list"
            )
        report.update(
            selected_model_available=True,
            selected_model=selected,
            candidate_models_considered=candidates,
            selection_note=(
                "Selected strongest mature non-coding general model available through "
                "the documented OpenAI-compatible protocol."
            ),
        )
        generator = AgentRouterGroundedAnswerGenerator(
            AgentRouterConfig(api_key=initial.api_key, model_id=selected, max_output_tokens=80)
        )
        smoke_bundle = GroundedEvidenceBundle(
            question="Confirm that the supplied evidence says the connectivity check passed.",
            route_type="RAG_ONLY",
            evidence_state=EvidenceState.COMPLETE,
            sources=[
                {
                    "source_id": "S1",
                    "source_key": "preflight",
                    "source_name": "Local preflight fixture",
                    "source_url": "local:phase13c-preflight",
                    "authority_tier": "SYNTHETIC_TEST",
                    "provenance": "Local provider-connectivity fixture.",
                    "text": "The connectivity check passed.",
                }
            ],
        )
        result = generator.generate(smoke_bundle)
        report.update(
            minimal_generation="PASS",
            generation_latency_ms=result.latency_ms,
            request_count=result.request_count,
            retry_count=result.retry_count,
            usage=result.usage.__dict__,
        )
        print(json.dumps(report, indent=2))
    except AgentRouterError as exc:
        report["failure_category"] = exc.category
        report["failure"] = str(exc)
        print(json.dumps(report, indent=2))
        raise SystemExit(1) from None


if __name__ == "__main__":
    main()
