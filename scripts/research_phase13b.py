# ruff: noqa: E501
"""Build the bounded full-season Phase 13B corpus and retrieval benchmark."""

from __future__ import annotations

import argparse
import hashlib
import json
import time
from collections import Counter, defaultdict
from copy import deepcopy
from datetime import UTC, datetime
from pathlib import Path
from time import perf_counter

import httpx
import joblib

from f1_pitwall.knowledge.chunking import chunk_documents
from f1_pitwall.knowledge.entities import build_entity_catalog
from f1_pitwall.knowledge.hardening import (
    FailureCategory,
    RouteType,
    classify_hardening_route,
    retrieve_evidence,
)
from f1_pitwall.knowledge.models import (
    EvidenceScope,
    HardeningBenchmarkQuestion,
    KnowledgeDocument,
    MetadataFilter,
    stable_id,
)
from f1_pitwall.knowledge.retrieval import BM25Retriever, DenseLSARetriever

ROOT = Path(__file__).resolve().parents[1]
DOCS = ROOT / "docs"
CACHE = ROOT / ".cache" / "phase13b"
CORPUS_PATH = DOCS / "phase13b-knowledge-corpus.json"
BENCHMARK_PATH = DOCS / "phase13b-retrieval-benchmark.json"
RESULTS_PATH = DOCS / "phase13b-retrieval-results.json"
INDEX_PATH = CACHE / "phase13b-bm25.joblib"
JOLPICA = "https://api.jolpi.ca/ergast/f1"
SEASONS = range(2021, 2026)
PHASE13A_HASHES = {
    "corpus": "4b8d4215686574094f19ef5c6b97a313de8ae62cf30525a96909a8ec59804251",
    "benchmark": "c29baf78c380ba2e749d8712dbbaeb8895c1167bda012c1d48a0824fbe4d1363",
    "results": "9b331773513e68399f0fef82985cb95d09793f2b8566e066fa231272ec7bca27",
}


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def verify_phase13a() -> None:
    paths = {
        "corpus": DOCS / "phase13a-knowledge-corpus.json",
        "benchmark": DOCS / "phase13a-retrieval-benchmark.json",
        "results": DOCS / "phase13a-retrieval-results.json",
    }
    actual = {name: sha256(path) for name, path in paths.items()}
    if actual != PHASE13A_HASHES:
        raise RuntimeError(f"Phase 13A frozen artifact mismatch: {actual}")


def fetch_season(client: httpx.Client, season: int, refresh: bool) -> list[dict]:
    CACHE.mkdir(parents=True, exist_ok=True)
    races: dict[str, dict] = {}
    offset = 0
    while True:
        path = CACHE / f"jolpica-{season}-results-{offset}.json"
        if path.exists() and not refresh:
            payload = json.loads(path.read_text(encoding="utf-8"))
        else:
            response = client.get(
                f"{JOLPICA}/{season}/results.json",
                params={"limit": 100, "offset": offset},
            )
            response.raise_for_status()
            payload = response.json()
            path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
            time.sleep(0.2)
        mrdata = payload["MRData"]
        for incoming in mrdata["RaceTable"]["Races"]:
            key = incoming["round"]
            if key not in races:
                races[key] = {**incoming, "Results": []}
            known = {row["Driver"]["driverId"] for row in races[key]["Results"]}
            races[key]["Results"].extend(
                row for row in incoming["Results"] if row["Driver"]["driverId"] not in known
            )
        offset += int(mrdata["limit"])
        if offset >= int(mrdata["total"]):
            break
    result = sorted(races.values(), key=lambda race: int(race["round"]))
    for race in result:
        race["Results"].sort(key=lambda row: int(row["position"]))
    return result


def driver_name(row: dict) -> str:
    return f"{row['Driver']['givenName']} {row['Driver']['familyName']}"


def make_document(**values) -> KnowledgeDocument:
    values["document_id"] = stable_id(
        "doc",
        {
            "source_key": values["source_key"],
            "source_name": values["source_name"],
            "source_type": values["source_type"],
        },
    )
    return KnowledgeDocument.model_validate(values)


def structured_documents(races: list[dict], retrieved_at: datetime):
    documents = []
    events = {}
    driver_seasons = defaultdict(list)
    constructor_seasons = defaultdict(list)
    circuits = defaultdict(list)
    for race in races:
        season = int(race["season"])
        round_number = int(race["round"])
        results = race["Results"]
        key = f"event:{season}:{round_number}"
        classification = "; ".join(
            f"P{row['position']} {driver_name(row)} ({row['Constructor']['name']}), grid {row['grid']}, status {row['status']}, points {row['points']}"
            for row in results
        )
        doc = make_document(
            source_key=key,
            source_name="Jolpica F1",
            source_url=f"{JOLPICA}/{season}/{round_number}/results.json",
            source_type="STRUCTURED_RESULT",
            authority_tier="STRUCTURED_AUTHORITATIVE",
            license_classification="PRODUCTION_REVIEW_REQUIRED",
            license_notes="Jolpica data is CC BY-NC-SA 4.0; commercial production use requires review.",
            title=f"{season} {race['raceName']} classification",
            text=f"{race['raceName']} was held on {race['date']} at {race['Circuit']['circuitName']}. Complete classification: {classification}.",
            event_date=race["date"],
            season=season,
            event=race["raceName"],
            driver_tags=sorted(
                {
                    value
                    for row in results
                    for value in [
                        row["Driver"]["driverId"],
                        driver_name(row),
                        row["Driver"].get("code"),
                    ]
                    if value
                }
            ),
            constructor_tags=sorted(
                {
                    value
                    for row in results
                    for value in [row["Constructor"]["constructorId"], row["Constructor"]["name"]]
                }
            ),
            circuit_tags=[race["Circuit"]["circuitId"], race["Circuit"]["circuitName"]],
            topic_tags=["race result", "classification", "grid", "status"],
            provenance="Jolpica/Ergast-compatible structured classification; no narrative cause inferred from status.",
            retrieved_at=retrieved_at,
        )
        documents.append(doc)
        events[key] = {"race": race, "document": doc}
        circuits[race["Circuit"]["circuitId"]].append(race)
        for row in results:
            driver_seasons[(season, row["Driver"]["driverId"])].append((race, row))
            constructor_seasons[(season, row["Constructor"]["constructorId"])].append((race, row))

    drivers = {}
    for (season, driver_id), rows in sorted(driver_seasons.items()):
        name = driver_name(rows[0][1])
        teams = sorted({row["Constructor"]["name"] for _, row in rows})
        source_key = f"driver-season:{season}:{driver_id}"
        summary = "; ".join(
            f"{race['raceName']} P{row['position']} for {row['Constructor']['name']} ({row['status']})"
            for race, row in rows
        )
        doc = make_document(
            source_key=source_key,
            source_name="Jolpica F1",
            source_url=f"{JOLPICA}/{season}/drivers/{driver_id}/results.json",
            source_type="STRUCTURED_RESULT",
            authority_tier="STRUCTURED_AUTHORITATIVE",
            license_classification="PRODUCTION_REVIEW_REQUIRED",
            license_notes="Jolpica data is CC BY-NC-SA 4.0; commercial production use requires review.",
            title=f"{name} {season} results",
            text=f"{name} represented {', '.join(teams)} in {season}. Results: {summary}.",
            season=season,
            driver_tags=[driver_id, name, rows[0][1]["Driver"].get("code") or driver_id],
            constructor_tags=sorted(
                {
                    value
                    for _, row in rows
                    for value in [row["Constructor"]["constructorId"], row["Constructor"]["name"]]
                }
            ),
            circuit_tags=sorted({race["Circuit"]["circuitId"] for race, _ in rows}),
            topic_tags=["driver history", "team history"],
            provenance="Full-season summary derived only from cited Jolpica classifications.",
            retrieved_at=retrieved_at,
        )
        documents.append(doc)
        drivers[source_key] = {
            "document": doc,
            "rows": rows,
            "name": name,
            "teams": teams,
            "driver_id": driver_id,
        }

    constructors = {}
    for (season, constructor_id), rows in sorted(constructor_seasons.items()):
        name = rows[0][1]["Constructor"]["name"]
        source_key = f"constructor-season:{season}:{constructor_id}"
        by_event = defaultdict(list)
        for race, row in rows:
            by_event[race["raceName"]].append(f"{driver_name(row)} P{row['position']}")
        summary = "; ".join(f"{event}: {', '.join(values)}" for event, values in by_event.items())
        doc = make_document(
            source_key=source_key,
            source_name="Jolpica F1",
            source_url=f"{JOLPICA}/{season}/constructors/{constructor_id}/results.json",
            source_type="STRUCTURED_RESULT",
            authority_tier="STRUCTURED_AUTHORITATIVE",
            license_classification="PRODUCTION_REVIEW_REQUIRED",
            license_notes="Jolpica data is CC BY-NC-SA 4.0; commercial production use requires review.",
            title=f"{name} {season} results",
            text=f"Season-specific constructor identity: {name} ({constructor_id}) in {season}. Results: {summary}.",
            season=season,
            driver_tags=sorted(
                {
                    value
                    for _, row in rows
                    for value in [row["Driver"]["driverId"], driver_name(row)]
                }
            ),
            constructor_tags=[constructor_id, name],
            circuit_tags=sorted({race["Circuit"]["circuitId"] for race, _ in rows}),
            topic_tags=["constructor history", "season identity"],
            provenance="Full-season constructor summary derived only from cited Jolpica classifications; lineage is not merged.",
            retrieved_at=retrieved_at,
        )
        documents.append(doc)
        constructors[source_key] = {"document": doc, "name": name, "constructor_id": constructor_id}

    circuit_docs = {}
    for circuit_id, rows in sorted(circuits.items()):
        source_key = f"circuit-history:{circuit_id}"
        name = rows[0]["Circuit"]["circuitName"]
        summary = "; ".join(
            f"{race['season']} {race['raceName']}: {driver_name(race['Results'][0])} won for {race['Results'][0]['Constructor']['name']}"
            for race in rows
        )
        doc = make_document(
            source_key=source_key,
            source_name="Jolpica F1",
            source_url=f"{JOLPICA}/circuits/{circuit_id}/results.json",
            source_type="STRUCTURED_RESULT",
            authority_tier="STRUCTURED_AUTHORITATIVE",
            license_classification="PRODUCTION_REVIEW_REQUIRED",
            license_notes="Jolpica data is CC BY-NC-SA 4.0; commercial production use requires review.",
            title=f"{name} event history 2021–2025",
            text=f"Events at {name} in the bounded corpus: {summary}.",
            driver_tags=[driver_name(race["Results"][0]) for race in rows],
            constructor_tags=[race["Results"][0]["Constructor"]["name"] for race in rows],
            circuit_tags=[circuit_id, name],
            topic_tags=["circuit history", "event naming", "previous winners"],
            provenance="Cross-season circuit summary derived only from cited Jolpica classifications; event names remain season-specific.",
            retrieved_at=retrieved_at,
        )
        documents.append(doc)
        circuit_docs[source_key] = doc
    return documents, events, drivers, constructors, circuit_docs


def official_documents() -> list[KnowledgeDocument]:
    rows = json.loads((DOCS / "phase13a-knowledge-corpus.json").read_text(encoding="utf-8"))
    documents = []
    for row in rows:
        if row["authority_tier"] == "OFFICIAL":
            row = deepcopy(row)
            row["license_classification"] = "PRODUCTION_REVIEW_REQUIRED"
            row["license_notes"] = (
                "Official copyrighted material stored only as concise paraphrase; production rights require review."
            )
            documents.append(KnowledgeDocument.model_validate(row))
    return documents


def q(query, category, route, sources, facts, *, metadata=None, scopes=None, available=True):
    requirement = "ALL_REQUIRED" if len(sources) > 1 else "ANY_RELEVANT"
    identity = {"query": query, "category": category, "relevant_source_keys": sources}
    return HardeningBenchmarkQuestion(
        question_id=stable_id("q13b", identity),
        query=query,
        category=category,
        route_type=route,
        evidence_requirement=requirement,
        relevant_source_keys=sources,
        key_facts=facts,
        metadata_filter=metadata or MetadataFilter(),
        decomposition_scopes=scopes or [],
        evidence_available=available,
    )


def event_scope(race: dict, query: str) -> EvidenceScope:
    return EvidenceScope(
        query=query,
        metadata_filter=MetadataFilter(season=int(race["season"]), event=race["raceName"]),
    )


def build_benchmark(events, drivers, constructors, circuits) -> list[HardeningBenchmarkQuestion]:
    questions = []
    ordered_events = sorted(
        events.items(),
        key=lambda item: (int(item[1]["race"]["season"]), int(item[1]["race"]["round"])),
    )
    selected_events = ordered_events[::2][:50]
    for source_key, item in selected_events:
        race = item["race"]
        winner = driver_name(race["Results"][0])
        query = f"Who won the {race['season']} {race['raceName']}?"
        questions.append(
            q(
                query,
                "SINGLE_EVENT_HISTORY",
                "STRUCTURED_ONLY",
                [source_key],
                [winner],
                metadata=MetadataFilter(season=int(race["season"]), event=race["raceName"]),
            )
        )

    driver_items = list(sorted(drivers.items()))
    for source_key, item in driver_items[:20]:
        season = item["document"].season
        query = f"What is the recorded {season} season history for {item['name']}?"
        questions.append(
            q(
                query,
                "DRIVER_HISTORY",
                "RAG_ONLY",
                [source_key],
                item["teams"],
                metadata=MetadataFilter(
                    season=season, driver=item["driver_id"], topic="driver history"
                ),
            )
        )

    for source_key, item in list(sorted(constructors.items()))[:15]:
        season = item["document"].season
        query = f"How did {item['name']} perform across the recorded {season} season?"
        questions.append(
            q(
                query,
                "TEAM_HISTORY",
                "RAG_ONLY",
                [source_key],
                [item["name"]],
                metadata=MetadataFilter(
                    season=season, constructor=item["constructor_id"], topic="constructor history"
                ),
            )
        )

    for source_key, doc in list(sorted(circuits.items()))[:10]:
        circuit_id = source_key.split(":", 1)[1]
        query = f"Which events and winners are recorded at {doc.title.replace(' event history 2021–2025', '')} from 2021 to 2025?"
        questions.append(
            q(
                query,
                "CIRCUIT_HISTORY",
                "RAG_ONLY",
                [source_key],
                [circuit_id],
                metadata=MetadataFilter(circuit=circuit_id, topic="circuit history"),
            )
        )

    official = [
        ("What does parc ferme restrict?", "official:f1-glossary", "parc ferme"),
        (
            "How does a Virtual Safety Car neutralise a race?",
            "official:fia-2021-sporting-safety-car",
            "virtual safety car",
        ),
        (
            "What changed aerodynamically in the 2022 regulations?",
            "official:fia-2022-technical-ground-effect",
            "ground effect",
        ),
        (
            "Why did the FIA respond to porpoising in 2022?",
            "official:fia-porpoising-response-2022",
            "porpoising",
        ),
        (
            "Why do teams favour low downforce at Monza?",
            "official:f1-monza-circuit",
            "low downforce",
        ),
    ]
    for query, source, topic in official:
        questions.append(
            q(
                query,
                "REGULATIONS_TERMINOLOGY",
                "RAG_ONLY",
                [source],
                [topic],
                metadata=MetadataFilter(topic=topic),
            )
        )

    by_circuit = defaultdict(list)
    for source_key, item in ordered_events:
        by_circuit[item["race"]["Circuit"]["circuitId"]].append((source_key, item["race"]))
    multi_candidates = [rows for rows in by_circuit.values() if len(rows) >= 2]
    for rows in multi_candidates[:10]:
        first, second = rows[0], rows[-1]
        circuit_name = first[1]["Circuit"]["circuitName"]
        query = f"Compare the complete results at {circuit_name} in {first[1]['season']} and {second[1]['season']}."
        scopes = [
            event_scope(first[1], f"{first[1]['raceName']} {first[1]['season']} complete result"),
            event_scope(
                second[1], f"{second[1]['raceName']} {second[1]['season']} complete result"
            ),
        ]
        questions.append(
            q(
                query,
                "MULTI_EVENT",
                "STRUCTURED_ONLY",
                [first[0], second[0]],
                [str(first[1]["season"]), str(second[1]["season"])],
                scopes=scopes,
            )
        )

    # Driver-transfer questions retain season-specific constructor identity.
    by_driver = defaultdict(list)
    for source_key, item in driver_items:
        by_driver[item["driver_id"]].append((source_key, item))
    transfers = []
    for rows in by_driver.values():
        rows.sort(key=lambda value: value[1]["document"].season)
        for first, second in zip(rows, rows[1:], strict=False):
            if set(first[1]["teams"]) != set(second[1]["teams"]):
                transfers.append((first, second))
                break
    for (first_key, first), (second_key, second) in transfers[:5]:
        y1, y2 = first["document"].season, second["document"].season
        query = f"Compare {first['name']}'s team and results in {y1} and {y2}."
        scopes = [
            EvidenceScope(
                query=f"{first['name']} {y1} team results",
                metadata_filter=MetadataFilter(
                    season=y1, driver=first["driver_id"], topic="driver history"
                ),
            ),
            EvidenceScope(
                query=f"{second['name']} {y2} team results",
                metadata_filter=MetadataFilter(
                    season=y2, driver=second["driver_id"], topic="driver history"
                ),
            ),
        ]
        questions.append(
            q(
                query,
                "TEAM_TRANSFER_AMBIGUITY",
                "STRUCTURED_ONLY",
                [first_key, second_key],
                first["teams"] + second["teams"],
                scopes=scopes,
            )
        )

    # Five mixed evidence bundles combine exact event facts with official circuit context.
    official_by_circuit = {
        "monaco": "official:f1-monaco-circuit",
        "monza": "official:f1-monza-circuit",
        "yas_marina": "official:f1-yas-marina-circuit",
    }
    mixed_rows = [
        (key, item)
        for key, item in ordered_events
        if item["race"]["Circuit"]["circuitId"] in official_by_circuit
    ][:5]
    for source_key, item in mixed_rows:
        race = item["race"]
        circuit_id = race["Circuit"]["circuitId"]
        official_key = official_by_circuit[circuit_id]
        query = f"Who won the {race['season']} {race['raceName']} and why is {race['Circuit']['circuitName']} notable or difficult?"
        scopes = [
            event_scope(race, f"Who won {race['season']} {race['raceName']}?"),
            EvidenceScope(
                query=f"Why is {race['Circuit']['circuitName']} difficult or notable?",
                metadata_filter=MetadataFilter(circuit=circuit_id),
            ),
        ]
        questions.append(
            q(
                query,
                "MIXED",
                "MIXED",
                [source_key, official_key],
                [driver_name(race["Results"][0]), circuit_id],
                scopes=scopes,
            )
        )

    # Five honest corpus-coverage diagnostics: structured status cannot establish narrative cause.
    for _source_key, item in ordered_events[:5]:
        race = item["race"]
        query = f"What privately reported mechanical cause explained every retirement at the {race['season']} {race['raceName']}?"
        questions.append(q(query, "CORPUS_COVERAGE", "RAG_ONLY", [], [], available=False))

    if len(questions) != 125:
        raise RuntimeError(f"Phase 13B benchmark must contain 125 questions, got {len(questions)}")
    return questions


def evaluate(retriever, questions, mode: str) -> dict:
    rows = []
    latencies = []
    for question in questions:
        start = perf_counter()
        scopes = question.decomposition_scopes or None if mode == "decomposed" else None
        metadata = question.metadata_filter if mode != "unfiltered" else None
        try:
            bundle = retrieve_evidence(
                retriever,
                question.query,
                RouteType(question.route_type),
                question.relevant_source_keys,
                metadata_filter=metadata,
                scopes=scopes,
                limit=10,
                evidence_available=question.evidence_available,
            )
            failure = bundle.failure_category.value if bundle.failure_category else None
        except ValueError:
            bundle = None
            failure = FailureCategory.QUERY_AMBIGUITY.value
        latencies.append((perf_counter() - start) * 1000)
        retrieved = [row.chunk.source_key for row in bundle.retrieved] if bundle else []
        relevant = set(question.relevant_source_keys)
        coverage = {
            k: len(relevant.intersection(retrieved[:k])) / len(relevant) if relevant else 0.0
            for k in (1, 3, 5, 10)
        }
        first_rank = next(
            (index for index, key in enumerate(retrieved, 1) if key in relevant), None
        )
        if classify_hardening_route(question.query).value != question.route_type:
            failure = FailureCategory.ROUTING_FAILURE.value
        rows.append(
            {
                "question_id": question.question_id,
                "category": question.category,
                "route_type": question.route_type,
                "evidence_available": question.evidence_available,
                "required": question.relevant_source_keys,
                "retrieved": retrieved,
                "coverage": coverage,
                "first_rank": first_rank,
                "failure_category": failure,
            }
        )
    supported = [row for row in rows if row["evidence_available"]]
    multi = [row for row in supported if len(row["required"]) > 1]
    return {
        "questions": len(rows),
        "supported_questions": len(supported),
        "multi_document_questions": len(multi),
        "recall_at_1": sum(row["coverage"][1] for row in supported) / len(supported),
        "recall_at_3": sum(row["coverage"][3] for row in supported) / len(supported),
        "recall_at_5": sum(row["coverage"][5] for row in supported) / len(supported),
        "mrr": sum(1 / row["first_rank"] for row in supported if row["first_rank"])
        / len(supported),
        "evidence_coverage_at_3": sum(row["coverage"][3] for row in multi) / len(multi),
        "evidence_coverage_at_5": sum(row["coverage"][5] for row in multi) / len(multi),
        "evidence_coverage_at_10": sum(row["coverage"][10] for row in multi) / len(multi),
        "complete_evidence_at_3": sum(row["coverage"][3] == 1 for row in multi) / len(multi),
        "complete_evidence_at_5": sum(row["coverage"][5] == 1 for row in multi) / len(multi),
        "complete_evidence_at_10": sum(row["coverage"][10] == 1 for row in multi) / len(multi),
        "median_query_ms": sorted(latencies)[len(latencies) // 2],
        "p90_query_ms": sorted(latencies)[int(len(latencies) * 0.9) - 1],
        "failure_categories": dict(
            sorted(
                Counter(row["failure_category"] for row in rows if row["failure_category"]).items()
            )
        ),
        "rows": rows,
    }


def ablations(retriever, questions):
    applicable = [q for q in questions if q.evidence_available and not q.decomposition_scopes]
    results = {}
    for field in ("season", "event", "driver", "circuit"):
        selected = [q for q in applicable if getattr(q.metadata_filter, field) is not None]
        if not selected:
            continue
        full_hits = removed_hits = wrong_scope = 0
        for question in selected:
            full = retriever.search(question.query, 1, question.metadata_filter)
            values = question.metadata_filter.model_dump()
            values[field] = None
            removed = retriever.search(question.query, 1, MetadataFilter(**values))
            full_hits += bool(full and full[0].chunk.source_key in question.relevant_source_keys)
            removed_hits += bool(
                removed and removed[0].chunk.source_key in question.relevant_source_keys
            )
            if removed and removed[0].chunk.source_key not in question.relevant_source_keys:
                wrong_scope += 1
        results[field] = {
            "questions": len(selected),
            "full_top1": full_hits / len(selected),
            "ablated_top1": removed_hits / len(selected),
            "wrong_scope_top1": wrong_scope / len(selected),
        }
    return results


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--refresh", action="store_true")
    args = parser.parse_args()
    verify_phase13a()
    started = perf_counter()
    retrieved_at = datetime.now(UTC)
    races = []
    with httpx.Client(
        timeout=30, headers={"User-Agent": "f1-pitwall-phase13b-research/1.0"}
    ) as client:
        for season in SEASONS:
            races.extend(fetch_season(client, season, args.refresh))
    structured, events, drivers, constructors, circuits = structured_documents(races, retrieved_at)
    documents = structured + official_documents()
    catalog = build_entity_catalog(races)
    questions = build_benchmark(events, drivers, constructors, circuits)
    corpus_seconds = perf_counter() - started
    CORPUS_PATH.write_text(
        json.dumps([d.model_dump(mode="json") for d in documents], indent=2), encoding="utf-8"
    )
    BENCHMARK_PATH.write_text(
        json.dumps([q.model_dump(mode="json") for q in questions], indent=2), encoding="utf-8"
    )
    chunks = chunk_documents(documents, "section")
    index_start = perf_counter()
    bm25 = BM25Retriever(chunks)
    dense = DenseLSARetriever(chunks, dimensions=64)
    index_seconds = perf_counter() - index_start
    CACHE.mkdir(parents=True, exist_ok=True)
    joblib.dump(bm25, INDEX_PATH)
    evaluations = {
        mode: evaluate(bm25, questions, mode) for mode in ("unfiltered", "filtered", "decomposed")
    }
    dense_diagnostic = evaluate(dense, questions, "filtered")
    route_accuracy = sum(
        classify_hardening_route(q.query).value == q.route_type for q in questions
    ) / len(questions)
    results = {
        "phase13a_verified_hashes": PHASE13A_HASHES,
        "built_at": retrieved_at.isoformat(),
        "seasons": list(SEASONS),
        "event_count": len(events),
        "document_count": len(documents),
        "chunk_count": len(chunks),
        "document_types": dict(Counter(d.source_type for d in documents)),
        "benchmark_questions": len(questions),
        "benchmark_categories": dict(Counter(q.category for q in questions)),
        "benchmark_sha256": sha256(BENCHMARK_PATH),
        "corpus_sha256": sha256(CORPUS_PATH),
        "route_accuracy": route_accuracy,
        "corpus_build_seconds": corpus_seconds,
        "bm25_index_build_seconds": index_seconds,
        "index_size_bytes": INDEX_PATH.stat().st_size,
        "entity_counts": {
            "drivers": len(catalog.driver_aliases),
            "constructors": len(catalog.constructor_aliases),
            "circuits": len(circuits),
        },
        "evaluations": evaluations,
        "dense_lsa_filtered_diagnostic": dense_diagnostic,
        "metadata_ablations": ablations(bm25, questions),
    }
    RESULTS_PATH.write_text(json.dumps(results, indent=2), encoding="utf-8")
    print(
        json.dumps(
            {
                key: results[key]
                for key in [
                    "event_count",
                    "document_count",
                    "chunk_count",
                    "benchmark_questions",
                    "benchmark_sha256",
                    "route_accuracy",
                    "corpus_build_seconds",
                    "index_size_bytes",
                ]
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
