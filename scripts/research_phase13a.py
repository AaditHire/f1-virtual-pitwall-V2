# ruff: noqa: E501
"""Build and evaluate the bounded Phase 13A historical F1 retrieval corpus.

This is a research-only workflow. It deliberately uses no radio artifacts, production
RaceState data, language model, remote embedding API, or production vector service.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import time
from collections import defaultdict
from datetime import UTC, datetime
from pathlib import Path
from time import perf_counter

import httpx
import joblib

from f1_pitwall.knowledge.chunking import chunk_documents
from f1_pitwall.knowledge.evaluation import evaluate_retriever
from f1_pitwall.knowledge.models import (
    BenchmarkQuestion,
    KnowledgeDocument,
    MetadataFilter,
    stable_id,
)
from f1_pitwall.knowledge.retrieval import BM25Retriever, DenseLSARetriever, HybridRetriever
from f1_pitwall.knowledge.routing import classify_query_boundary

ROOT = Path(__file__).resolve().parents[1]
DOCS = ROOT / "docs"
CACHE = ROOT / ".cache" / "phase13a"
CORPUS_PATH = DOCS / "phase13a-knowledge-corpus.json"
BENCHMARK_PATH = DOCS / "phase13a-retrieval-benchmark.json"
RESULTS_PATH = DOCS / "phase13a-retrieval-results.json"
INDEX_PATH = CACHE / "phase13a-index.joblib"
SEASONS = range(2021, 2026)
CIRCUITS = {"bahrain", "monaco", "monza", "yas_marina"}
JOLPICA = "https://api.jolpi.ca/ergast/f1"


OFFICIAL_DOCUMENTS = [
    {
        "source_key": "official:f1-glossary",
        "source_name": "Formula 1",
        "source_url": "https://www.formula1.com/en/page/f1-glossary",
        "source_type": "OFFICIAL_EDITORIAL",
        "title": "Formula 1 terminology: parc ferme, undercut, DRS and dirty air",
        "topic_tags": ["parc ferme", "undercut", "DRS", "dirty air"],
        "text": """## Parc ferme
Parc ferme is the controlled condition under which cars are restricted from substantial setup changes. In a Grand Prix weekend it begins after cars leave the pit lane for qualifying, subject to the applicable sporting regulations and permitted exceptions.

## Undercut and overcut
An undercut is an attempt to stop before a rival, use the performance of fresher tyres and emerge ahead after the rival stops. An overcut keeps the car out longer and depends on the extra track time, clear air or tyre performance producing a better outcome.

## DRS and dirty air
The drag reduction system opens an adjustable rear-wing element only under the rules and in designated zones. Dirty air is disturbed airflow behind another car; it can reduce aerodynamic performance and make close following harder.""",
        "provenance": "Concise factual paraphrase of Formula 1's official glossary; no article mirrored.",
    },
    {
        "source_key": "official:fia-2021-sporting-safety-car",
        "source_name": "FIA",
        "source_url": "https://www.fia.com/sites/default/files/2021_formula_1_sporting_regulations_-_2019-10-31_1.pdf",
        "source_type": "OFFICIAL_REGULATION",
        "title": "2021 Formula One sporting rules: Safety Car and Virtual Safety Car",
        "season": 2021,
        "topic_tags": ["safety car", "virtual safety car", "restart"],
        "text": """## Safety Car
The 2021 sporting regulations define the Safety Car procedure, including the order in which cars follow it, limits on overtaking, lapped-car procedure and the restart process. Drivers must follow prescribed signals and timing requirements.

## Virtual Safety Car
The Virtual Safety Car procedure uses marshal-light and dashboard signals and requires drivers to remain above a minimum sector time. It neutralises the race without deploying the physical Safety Car.""",
        "provenance": "Research summary of the official FIA 2021 Sporting Regulations, Articles 48 and 50.",
    },
    {
        "source_key": "official:fia-2022-technical-ground-effect",
        "source_name": "FIA",
        "source_url": "https://www.fia.com/sites/default/files/2022_formula_1_technical_regulations_-_iss_5_-_2021-07-08.pdf",
        "source_type": "OFFICIAL_REGULATION",
        "title": "2022 Formula One technical regulations and ground-effect floors",
        "season": 2022,
        "topic_tags": ["2022 regulations", "ground effect", "technical regulations"],
        "text": """## Aerodynamic concept
The 2022 technical rules introduced a substantially revised aerodynamic package. Shaped floor tunnels generate a greater share of downforce through ground effect, while the bodywork and wings were tightly prescribed.

## Intended racing effect
The revised aerodynamic concept was designed to make a following car less sensitive to the wake of the car ahead, supporting closer racing. The regulations also introduced 18-inch wheels and extensive dimensional and safety requirements.""",
        "provenance": "Research summary of the official FIA 2022 Formula One Technical Regulations.",
    },
    {
        "source_key": "official:fia-porpoising-response-2022",
        "source_name": "FIA",
        "source_url": "https://www.fia.com/news/fia-takes-steps-reduce-porpoising-interests-safety",
        "source_type": "OFFICIAL_EDITORIAL",
        "title": "FIA safety response to porpoising in 2022",
        "season": 2022,
        "topic_tags": ["porpoising", "safety", "technical directive"],
        "text": """## Safety response
In June 2022 the FIA said it would take steps to reduce excessive vertical oscillation of Formula One cars on safety grounds. Measures included closer scrutiny of the plank and skids and work on a quantitative limit for acceptable vertical oscillation.

## Context
The phenomenon became prominent with the 2022 ground-effect cars. The FIA framed its intervention as a driver-safety matter rather than a prediction of competitive performance.""",
        "provenance": "Concise factual paraphrase of an official FIA safety announcement dated 16 June 2022.",
    },
    {
        "source_key": "official:f1-monaco-circuit",
        "source_name": "Formula 1",
        "source_url": "https://www.formula1.com/en/latest/article/circuit-guide-everything-you-need-to-know-about-the-circuit-de-monaco.vFsmfGHr6RWyLFtxi58wi",
        "source_type": "OFFICIAL_EDITORIAL",
        "title": "Circuit de Monaco characteristics",
        "circuit_tags": ["monaco", "Circuit de Monaco"],
        "topic_tags": ["circuit characteristics", "overtaking"],
        "text": """## Circuit character
Monaco is a narrow, low-speed street circuit with close barriers, heavy steering demand and little margin for error. Qualifying position is unusually important because passing opportunities are limited.

## Race context
Low average speed does not make the circuit easy: precision, traction and confidence over bumps and kerbs are central. Pit timing, traffic and interruptions can strongly influence track position.""",
        "provenance": "Concise factual paraphrase of Formula 1's official Monaco circuit guide.",
    },
    {
        "source_key": "official:f1-monza-circuit",
        "source_name": "Formula 1",
        "source_url": "https://www.formula1.com/en/latest/article/what-makes-the-italian-grand-prix-special-and-why-you-should-see-it.iAdwIYmBJXcxJQlnKgGfw",
        "source_type": "OFFICIAL_EDITORIAL",
        "title": "Monza circuit characteristics",
        "circuit_tags": ["monza", "Autodromo Nazionale di Monza"],
        "topic_tags": ["circuit characteristics", "low downforce"],
        "text": """## Circuit character
Monza is dominated by long straights, major braking zones and fast corners. Teams normally pursue a low-downforce configuration to reduce drag while retaining braking stability and traction through the chicanes.

## Racing context
The slipstream is important and the first chicane is a prominent overtaking and incident point. Kerb use, energy deployment and efficient straight-line speed shape lap performance.""",
        "provenance": "Concise factual paraphrase of Formula 1's official Monza editorial material.",
    },
    {
        "source_key": "official:f1-yas-marina-circuit",
        "source_name": "Formula 1",
        "source_url": "https://www.formula1.com/en/latest/article/why-the-yas-marina-circuit-makes-for-a-great-season-finale.4z4Y9uuDhIo7V2Fl2xRFmY",
        "source_type": "OFFICIAL_EDITORIAL",
        "title": "Yas Marina Circuit and the Abu Dhabi season finale",
        "circuit_tags": ["yas_marina", "Yas Marina Circuit"],
        "topic_tags": ["circuit characteristics", "season finale"],
        "text": """## Circuit character
Yas Marina combines long straights and slow-to-medium-speed corners. Its layout offers heavy-braking overtaking opportunities, while traction and tyre temperature remain important across an evening race.

## Event context
The Abu Dhabi Grand Prix commonly closes the Formula One season. Racing begins around sunset and continues under floodlights, so track and air conditions can evolve during the event.""",
        "provenance": "Concise factual paraphrase of Formula 1's official Yas Marina editorial material.",
    },
]


def fetch_season(client: httpx.Client, season: int, refresh: bool) -> list[dict]:
    CACHE.mkdir(parents=True, exist_ok=True)
    races = []
    # Jolpica paginates this endpoint by classified-result rows, not races. Querying
    # the four deliberately selected circuits avoids a broad crawl and pagination traps.
    for circuit in sorted(CIRCUITS):
        path = CACHE / f"jolpica-{season}-{circuit}-results.json"
        if path.exists() and not refresh:
            payload = json.loads(path.read_text(encoding="utf-8"))
        else:
            response = client.get(
                f"{JOLPICA}/{season}/circuits/{circuit}/results.json",
                params={"limit": 100},
            )
            response.raise_for_status()
            payload = response.json()
            path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
            time.sleep(0.25)
        races.extend(payload["MRData"]["RaceTable"]["Races"])
    return races


def _driver_name(result: dict) -> str:
    driver = result["Driver"]
    return f"{driver['givenName']} {driver['familyName']}"


def _doc(**values) -> KnowledgeDocument:
    identity = {
        "source_key": values["source_key"],
        "source_name": values["source_name"],
        "source_type": values["source_type"],
    }
    values["document_id"] = stable_id("doc", identity)
    return KnowledgeDocument.model_validate(values)


def build_structured_documents(races: list[dict], retrieved_at: datetime):
    documents = []
    event_rows = {}
    driver_rows = defaultdict(list)
    circuit_rows = defaultdict(list)
    for race in races:
        circuit_id = race["Circuit"]["circuitId"]
        if circuit_id not in CIRCUITS:
            continue
        season = int(race["season"])
        results = race["Results"]
        source_key = f"event:{season}:{race['round']}"
        podium = results[:3]
        retirements = [
            row
            for row in results
            if row.get("status") != "Finished" and not row.get("status", "").startswith("+")
        ]
        classifications = "; ".join(
            f"P{row['position']} {_driver_name(row)} ({row['Constructor']['name']}), status {row['status']}"
            for row in results
        )
        text = (
            f"{race['raceName']} was held on {race['date']} at {race['Circuit']['circuitName']}. "
            f"The winner was {_driver_name(results[0])} for {results[0]['Constructor']['name']}. "
            f"The podium was "
            + ", ".join(
                f"P{row['position']} {_driver_name(row)} for {row['Constructor']['name']}"
                for row in podium
            )
            + ". Classified result and status: "
            + classifications
            + "."
        )
        doc = _doc(
            source_key=source_key,
            source_name="Jolpica F1",
            source_url=f"{JOLPICA}/{season}/{race['round']}/results.json",
            source_type="STRUCTURED_RESULT",
            authority_tier="STRUCTURED_AUTHORITATIVE",
            title=f"{season} {race['raceName']} classified results",
            text=text,
            event_date=race["date"],
            season=season,
            event=race["raceName"],
            driver_tags=[_driver_name(row) for row in results],
            constructor_tags=sorted({row["Constructor"]["name"] for row in results}),
            circuit_tags=[circuit_id, race["Circuit"]["circuitName"]],
            topic_tags=["race result", "podium", "retirement"],
            provenance="Jolpica/Ergast-compatible structured race classification; normalized without inferred facts.",
            retrieved_at=retrieved_at,
        )
        documents.append(doc)
        event_rows[source_key] = {"race": race, "retirements": retirements, "document": doc}
        circuit_rows[circuit_id].append(race)
        for row in results:
            driver_rows[(season, row["Driver"]["driverId"])].append((race, row))

    selected_driver_seasons = [
        (2021, "hamilton"),
        (2024, "hamilton"),
        (2025, "hamilton"),
        (2021, "max_verstappen"),
        (2024, "max_verstappen"),
        (2025, "max_verstappen"),
        (2021, "vettel"),
        (2022, "vettel"),
        (2021, "alonso"),
        (2023, "alonso"),
        (2023, "piastri"),
        (2025, "piastri"),
        (2024, "sainz"),
        (2025, "sainz"),
    ]
    driver_docs = {}
    for identity in selected_driver_seasons:
        rows = driver_rows.get(identity, [])
        if not rows:
            continue
        season, driver_id = identity
        name = _driver_name(rows[0][1])
        teams = sorted({row["Constructor"]["name"] for _, row in rows})
        summaries = "; ".join(
            f"{race['raceName']}: P{row['position']} for {row['Constructor']['name']} ({row['status']})"
            for race, row in rows
        )
        source_key = f"driver-season:{season}:{driver_id}"
        doc = _doc(
            source_key=source_key,
            source_name="Jolpica F1",
            source_url=f"{JOLPICA}/{season}/drivers/{driver_id}/results.json",
            source_type="STRUCTURED_RESULT",
            authority_tier="STRUCTURED_AUTHORITATIVE",
            title=f"{name} in the selected {season} event sample",
            text=f"In the bounded {season} sample, {name} drove for {', '.join(teams)}. Results: {summaries}.",
            season=season,
            driver_tags=[name, driver_id],
            constructor_tags=teams,
            circuit_tags=sorted({race["Circuit"]["circuitId"] for race, _ in rows}),
            topic_tags=["driver history", "team history"],
            provenance="Derived only from the cited Jolpica structured classifications in the bounded corpus.",
            retrieved_at=retrieved_at,
        )
        documents.append(doc)
        driver_docs[source_key] = {"document": doc, "rows": rows, "name": name, "teams": teams}

    circuit_docs = {}
    for circuit_id, rows in circuit_rows.items():
        winners = "; ".join(
            f"{race['season']}: {_driver_name(race['Results'][0])} ({race['Results'][0]['Constructor']['name']})"
            for race in sorted(rows, key=lambda value: value["season"])
        )
        source_key = f"circuit-history:{circuit_id}"
        circuit_name = rows[0]["Circuit"]["circuitName"]
        doc = _doc(
            source_key=source_key,
            source_name="Jolpica F1",
            source_url=f"{JOLPICA}/circuits/{circuit_id}/results.json",
            source_type="STRUCTURED_RESULT",
            authority_tier="STRUCTURED_AUTHORITATIVE",
            title=f"{circuit_name} winners in the 2021–2025 sample",
            text=f"Recorded winners at {circuit_name} in the bounded corpus: {winners}.",
            driver_tags=[_driver_name(race["Results"][0]) for race in rows],
            constructor_tags=[race["Results"][0]["Constructor"]["name"] for race in rows],
            circuit_tags=[circuit_id, circuit_name],
            topic_tags=["circuit history", "previous winners"],
            provenance="Cross-event summary derived only from the cited Jolpica structured classifications.",
            retrieved_at=retrieved_at,
        )
        documents.append(doc)
        circuit_docs[source_key] = doc
    return documents, event_rows, driver_docs, circuit_docs


def build_official_documents(retrieved_at: datetime) -> list[KnowledgeDocument]:
    documents = []
    for item in OFFICIAL_DOCUMENTS:
        values = dict(item)
        values.update(
            authority_tier="OFFICIAL",
            retrieved_at=retrieved_at,
            driver_tags=[],
            constructor_tags=[],
        )
        values.setdefault("circuit_tags", [])
        values.setdefault("topic_tags", [])
        documents.append(_doc(**values))
    return documents


def _question(query, category, route, sources, facts, metadata=None):
    identity = {"query": query, "category": category, "relevant_source_keys": sources}
    return BenchmarkQuestion(
        question_id=stable_id("q", identity),
        query=query,
        category=category,
        expected_route=route,
        relevant_source_keys=sources,
        key_facts=facts,
        metadata_filter=metadata or MetadataFilter(),
    )


def build_benchmark(event_rows, driver_docs, circuit_docs) -> list[BenchmarkQuestion]:
    questions = []
    events = sorted(
        event_rows.items(),
        key=lambda item: (int(item[1]["race"]["season"]), int(item[1]["race"]["round"])),
    )
    for source_key, item in events[:8]:
        race = item["race"]
        winner = _driver_name(race["Results"][0])
        questions.append(
            _question(
                f"Who won the {race['season']} {race['raceName']}?",
                "EVENT_HISTORY",
                "STRUCTURED_FACT",
                [source_key],
                [winner],
                MetadataFilter(season=int(race["season"]), event=race["raceName"]),
            )
        )
    for source_key, item in events[8:20]:
        race = item["race"]
        podium = [_driver_name(row) for row in race["Results"][:3]]
        questions.append(
            _question(
                f"What was the podium and notable non-finish context at the {race['season']} {race['raceName']}?",
                "EVENT_HISTORY",
                "STRUCTURED_FACT",
                [source_key],
                podium,
                MetadataFilter(season=int(race["season"]), event=race["raceName"]),
            )
        )
    for source_key, item in list(sorted(driver_docs.items()))[:6]:
        doc = item["document"]
        questions.append(
            _question(
                f"Which team did {item['name']} represent in the selected {doc.season} historical sample, and how did the sampled races go?",
                "DRIVER_TEAM",
                "KNOWLEDGE_RETRIEVAL",
                [source_key],
                item["teams"],
                MetadataFilter(season=doc.season, driver=item["name"]),
            )
        )
    circuit_names = {
        "bahrain": "Bahrain International Circuit",
        "monaco": "Circuit de Monaco",
        "monza": "Autodromo Nazionale di Monza",
        "yas_marina": "Yas Marina Circuit",
    }
    for circuit_id, name in circuit_names.items():
        source_key = f"circuit-history:{circuit_id}"
        questions.append(
            _question(
                f"Who were the recorded winners at {name} across the 2021 to 2025 corpus?",
                "CIRCUIT_HISTORY",
                "KNOWLEDGE_RETRIEVAL",
                [source_key],
                ["2021", "2025"],
                MetadataFilter(circuit=circuit_id, topic="circuit history"),
            )
        )
    official_questions = [
        ("What does parc ferme mean in Formula One?", "official:f1-glossary", "parc ferme"),
        (
            "What is the difference between an undercut and an overcut?",
            "official:f1-glossary",
            "undercut",
        ),
        (
            "How did the 2021 Safety Car and Virtual Safety Car procedures neutralise a race?",
            "official:fia-2021-sporting-safety-car",
            "safety car",
        ),
        (
            "What major aerodynamic concept changed under the 2022 Formula One technical rules?",
            "official:fia-2022-technical-ground-effect",
            "ground effect",
        ),
        (
            "Why did the FIA intervene over porpoising in 2022?",
            "official:fia-porpoising-response-2022",
            "porpoising",
        ),
        (
            "Why is overtaking particularly difficult at Monaco?",
            "official:f1-monaco-circuit",
            "overtaking",
        ),
        (
            "Why do Formula One teams use low-downforce configurations at Monza?",
            "official:f1-monza-circuit",
            "low downforce",
        ),
    ]
    for query, source_key, topic in official_questions:
        questions.append(
            _question(
                query,
                "REGULATION_TERMINOLOGY"
                if source_key.startswith("official:fia") or source_key == "official:f1-glossary"
                else "CIRCUIT_HISTORY",
                "KNOWLEDGE_RETRIEVAL",
                [source_key],
                [topic],
                MetadataFilter(topic=topic),
            )
        )
    multi = [
        (
            "Compare the complete Bahrain Grand Prix podiums in 2021 and 2024.",
            [
                key
                for key, row in events
                if row["race"]["Circuit"]["circuitId"] == "bahrain"
                and int(row["race"]["season"]) in {2021, 2024}
            ],
            ["2021", "2024"],
        ),
        (
            "How did the Monaco non-finish context differ between 2021 and 2023?",
            [
                key
                for key, row in events
                if row["race"]["Circuit"]["circuitId"] == "monaco"
                and int(row["race"]["season"]) in {2021, 2023}
            ],
            ["2021", "2023"],
        ),
        (
            "Connect the 2022 ground-effect rules with the FIA's porpoising safety response.",
            ["official:fia-2022-technical-ground-effect", "official:fia-porpoising-response-2022"],
            ["ground effect", "safety"],
        ),
    ]
    for query, sources, facts in multi:
        questions.append(_question(query, "MULTI_DOCUMENT", "KNOWLEDGE_RETRIEVAL", sources, facts))
    if len(questions) != 40:
        raise RuntimeError(f"benchmark must contain 40 questions, got {len(questions)}")
    return questions


def failure_category(row, chunks_by_source):
    if row["first_relevant_rank"] is not None:
        return None
    if row["expected_route"] == "STRUCTURED_FACT":
        return "structured_query_should_route_to_deterministic_data"
    retrieved = row["retrieved_source_keys"]
    relevant = row["relevant_source_keys"]
    if not retrieved:
        return "metadata_filter_or_vocabulary_returned_no_evidence"
    expected_chunks = [chunk for source in relevant for chunk in chunks_by_source.get(source, [])]
    retrieved_chunks = [chunk for source in retrieved for chunk in chunks_by_source.get(source, [])]
    if expected_chunks and retrieved_chunks:
        seasons = {chunk.season for chunk in expected_chunks if chunk.season}
        retrieved_seasons = {chunk.season for chunk in retrieved_chunks if chunk.season}
        if seasons and retrieved_seasons and seasons.isdisjoint(retrieved_seasons):
            return "wrong_season"
        events = {chunk.event for chunk in expected_chunks if chunk.event}
        retrieved_events = {chunk.event for chunk in retrieved_chunks if chunk.event}
        if events and retrieved_events and events.isdisjoint(retrieved_events):
            return "wrong_event"
    return "entity_or_semantic_confusion"


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--refresh", action="store_true", help="refetch the five Jolpica seasons")
    args = parser.parse_args()
    CACHE.mkdir(parents=True, exist_ok=True)
    started = perf_counter()
    retrieved_at = datetime.now(UTC)
    races = []
    with httpx.Client(
        timeout=30, headers={"User-Agent": "f1-pitwall-phase13a-research/1.0"}
    ) as client:
        for season in SEASONS:
            races.extend(fetch_season(client, season, args.refresh))
    structured, event_rows, driver_docs, circuit_docs = build_structured_documents(
        races, retrieved_at
    )
    documents = structured + build_official_documents(retrieved_at)
    questions = build_benchmark(event_rows, driver_docs, circuit_docs)
    corpus_build_seconds = perf_counter() - started

    CORPUS_PATH.write_text(
        json.dumps([doc.model_dump(mode="json") for doc in documents], indent=2), encoding="utf-8"
    )
    BENCHMARK_PATH.write_text(
        json.dumps([q.model_dump(mode="json") for q in questions], indent=2), encoding="utf-8"
    )
    corpus_sha256 = hashlib.sha256(CORPUS_PATH.read_bytes()).hexdigest()
    benchmark_sha256 = hashlib.sha256(BENCHMARK_PATH.read_bytes()).hexdigest()

    experiments = {}
    best_objects = None
    for strategy in ("section", "fixed"):
        chunks = chunk_documents(documents, strategy)
        index_started = perf_counter()
        lexical = BM25Retriever(chunks)
        dense = DenseLSARetriever(chunks, dimensions=64)
        hybrid = HybridRetriever(lexical, dense)
        index_seconds = perf_counter() - index_started
        for retriever in (lexical, dense, hybrid):
            for filtered in (False, True):
                name = f"{strategy}:{retriever.name}:{'filtered' if filtered else 'unfiltered'}"
                evaluation = evaluate_retriever(retriever, questions, filtered)
                failures = defaultdict(int)
                chunks_by_source = defaultdict(list)
                for chunk in chunks:
                    chunks_by_source[chunk.source_key].append(chunk)
                for row in evaluation["rows"]:
                    category = failure_category(row, chunks_by_source)
                    if category:
                        failures[category] += 1
                evaluation.update(
                    chunk_count=len(chunks),
                    dense_dimensions=dense.dimensions,
                    dense_vector_bytes=dense.storage_bytes,
                    index_build_seconds=index_seconds,
                    failures=dict(sorted(failures.items())),
                )
                experiments[name] = evaluation
        candidate = max(
            ((name, data) for name, data in experiments.items() if name.startswith(f"{strategy}:")),
            key=lambda item: (item[1]["recall_at_5"], item[1]["recall_at_1"], item[1]["mrr"]),
        )
        if (
            best_objects is None
            or (candidate[1]["recall_at_5"], candidate[1]["recall_at_1"], candidate[1]["mrr"])
            > best_objects[0]
        ):
            best_objects = (
                (candidate[1]["recall_at_5"], candidate[1]["recall_at_1"], candidate[1]["mrr"]),
                candidate[0],
                chunks,
                lexical,
                dense,
                hybrid,
            )

    _, best_name, best_chunks, best_lexical, best_dense, best_hybrid = best_objects
    joblib.dump(
        {"chunks": best_chunks, "bm25": best_lexical, "dense": best_dense, "hybrid": best_hybrid},
        INDEX_PATH,
    )
    route_accuracy = sum(
        classify_query_boundary(q.query).value == q.expected_route for q in questions
    ) / len(questions)
    results = {
        "research_scope": "Phase 13A retrieval-only; no generation or production integration",
        "built_at": retrieved_at.isoformat(),
        "seasons": list(SEASONS),
        "circuits": sorted(CIRCUITS),
        "document_count": len(documents),
        "source_document_counts": {
            "structured": len(structured),
            "official": len(documents) - len(structured),
        },
        "corpus_build_seconds": corpus_build_seconds,
        "benchmark_questions": len(questions),
        "corpus_sha256": corpus_sha256,
        "benchmark_sha256": benchmark_sha256,
        "benchmark_categories": dict(
            sorted(
                defaultdict(
                    int,
                    {
                        category: sum(q.category == category for q in questions)
                        for category in sorted({q.category for q in questions})
                    },
                ).items()
            )
        ),
        "route_boundary_accuracy": route_accuracy,
        "best_experiment": best_name,
        "index_path": str(INDEX_PATH.relative_to(ROOT)),
        "index_size_bytes": INDEX_PATH.stat().st_size,
        "experiments": experiments,
    }
    RESULTS_PATH.write_text(json.dumps(results, indent=2), encoding="utf-8")
    print(
        json.dumps(
            {
                key: results[key]
                for key in [
                    "document_count",
                    "benchmark_questions",
                    "best_experiment",
                    "index_size_bytes",
                    "corpus_build_seconds",
                ]
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
