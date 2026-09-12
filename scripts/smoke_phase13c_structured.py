"""Run one paid synthetic test of the Phase 13C structured-output contract."""

from __future__ import annotations

import json

from f1_pitwall.knowledge.agentrouter import (
    AGENTROUTER_BASE_URL,
    AGENTROUTER_MODEL,
    AgentRouterConfig,
    AgentRouterError,
    AgentRouterGroundedAnswerGenerator,
)
from f1_pitwall.knowledge.generation import (
    EvidenceState,
    GroundedEvidenceBundle,
    SourceEvidence,
    validate_citations,
)


def synthetic_bundle() -> GroundedEvidenceBundle:
    return GroundedEvidenceBundle(
        question="What does the supplied evidence say?",
        route_type="RAG_ONLY",
        sources=[
            SourceEvidence(
                source_id="S1",
                source_key="synthetic:structured-output:1",
                source_name="Synthetic interface fixture",
                source_url="local:phase13c-structured-output",
                authority_tier="SYNTHETIC_TEST",
                provenance="Isolated non-benchmark interface fixture.",
                text="The test event occurred in 2024.",
            )
        ],
        evidence_state=EvidenceState.COMPLETE,
    )


def main() -> None:
    generator = None
    report = {
        "provider": "AgentRouter",
        "protocol": "Anthropic-compatible Messages",
        "base_url": AGENTROUTER_BASE_URL,
        "model": AGENTROUTER_MODEL,
        "output_contract": "forced grounded_answer tool",
        "synthetic_non_benchmark": True,
        "request_count": 0,
        "retry_count": 0,
    }
    try:
        generator = AgentRouterGroundedAnswerGenerator(AgentRouterConfig.from_env())
        bundle = synthetic_bundle()
        result = generator.generate(bundle)
        validation = validate_citations(result.answer, bundle)
        expected = "2024" in result.answer.answer and validation.valid
        report.update(
            status="PASS" if expected else "FAIL",
            answer=result.answer.answer,
            evidence_status=result.answer.status,
            citations=result.answer.citations,
            citation_validation=validation.__dict__,
            latency_ms=result.latency_ms,
            usage=result.usage.__dict__,
            request_count=result.request_count,
            retry_count=result.retry_count,
        )
        print(json.dumps(report, indent=2))
        if not expected:
            raise SystemExit(1)
    except AgentRouterError as exc:
        report.update(
            status="FAIL",
            failure_category=exc.category,
            failure=str(exc),
            http_status=exc.status_code,
            latency_ms=exc.latency_ms,
            usage=exc.usage.__dict__,
            request_count=exc.request_count,
            retry_count=exc.retry_count,
        )
        print(json.dumps(report, indent=2))
        raise SystemExit(1) from None


if __name__ == "__main__":
    main()
