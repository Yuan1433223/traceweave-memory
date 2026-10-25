import hashlib
import importlib.metadata
import json
import platform
import sqlite3
import time
from pathlib import Path

from traceweave_memory.adapters.media import FileMedia
from traceweave_memory.adapters.sqlite import SQLiteRepository
from traceweave_memory.api.schemas import AddRequest, SearchRequest
from traceweave_memory.application import MemoryService
from traceweave_memory.evaluation.mem_gallery import prepare_memory, prepare_query


def retrieval_metrics(labels: list[set[str]], gold: set[str], k: int) -> dict:
    if not gold:
        return {"recall": None, "complete": None, "mrr": None}
    observed = set().union(*labels[:k]) if labels else set()
    first = next((i for i, item in enumerate(labels[:k], 1) if item & gold), None)
    return {
        "recall": len(observed & gold) / len(gold),
        "complete": float(gold <= observed),
        "mrr": 1 / first if first else 0.0,
    }


def run_gallery(root: Path, topic: str, output: Path) -> dict:
    if Path(topic).name != topic or "/" in topic or "\\" in topic:
        raise ValueError("topic must be a dataset identifier, not a path")
    # A new output directory is an empty experiment. Refuse accidental state reuse.
    output.mkdir(parents=True, exist_ok=False)
    source = root / "data" / "dialog" / f"{topic}.json"
    document = json.loads(source.read_text(encoding="utf-8"))
    manifest = json.loads((root / "manifest.json").read_text(encoding="utf-8"))
    if topic not in manifest["topics"]:
        raise ValueError("topic is not in frozen manifest")
    for item in manifest["files"]:
        path = root / item["path"]
        with path.open("rb") as stream:
            actual = hashlib.file_digest(stream, "sha256").hexdigest()
        if actual != item["sha256"]:
            raise ValueError("dataset manifest integrity mismatch")
    service = MemoryService(
        SQLiteRepository(output / "memory.sqlite3"), FileMedia(output / "media")
    )
    scope = f"mem-gallery:{topic}"
    memory = prepare_memory(document, root, scope)
    started = time.perf_counter()
    for write in memory.writes:
        service.add(AddRequest.model_validate(write).model_dump(exclude_none=True))
    ingestion_seconds = time.perf_counter() - started
    all_source_labels = set().union(*memory.source_ids_by_evidence.values())
    records = []
    cutoffs = [1, 5, 10, 20, 100]
    for index, question in enumerate(document["human-annotated QAs"]):
        gold = {str(value) for value in question.get("clue", [])}
        # Unknown labels are an adapter error, never an silently excluded hard question.
        if gold - all_source_labels:
            raise ValueError(f"unmapped source clue at question index {index}")
        query = SearchRequest.model_validate(prepare_query(question, root, scope, 100))
        started = time.perf_counter()
        result = service.search(query.model_dump(exclude_none=True))
        duration = time.perf_counter() - started
        labels = [memory.source_ids_by_evidence[item["id"]] for item in result["data"]]
        records.append(
            {
                "index": index,
                "category": question["point"],
                "has_gold_clues": bool(gold),
                "result_count": len(labels),
                "search_seconds": duration,
                "metrics": {str(k): retrieval_metrics(labels, gold, k) for k in cutoffs},
            }
        )
    eligible = [item for item in records if item["has_gold_clues"]]
    summary = {
        "system": "B0-lexical",
        "dataset": manifest["dataset"],
        "revision": manifest["revision"],
        "topic": topic,
        "split": manifest["topics"][topic],
        "source_unit": "round clue; hit by either original role message",
        "questions": len(records),
        "clue_questions": len(eligible),
        "writes": len(memory.writes),
        "messages": len(memory.source_ids_by_evidence),
        "ingestion_seconds": ingestion_seconds,
        "model_calls": 0,
        "model_usd": 0,
        "answer_eval": "not run",
        "metrics": {},
    }
    code_digest = hashlib.sha256()
    package_root = Path(__file__).resolve().parents[1]
    for code_path in sorted(package_root.rglob("*.py")):
        code_digest.update(code_path.relative_to(package_root).as_posix().encode())
        code_digest.update(code_path.read_bytes())
    summary["reproduction"] = {
        "source_sha256": code_digest.hexdigest(),
        "python": platform.python_version(),
        "platform": platform.system(),
        "sqlite": sqlite3.sqlite_version,
        "dependencies": {
            name: importlib.metadata.version(name) for name in ("fastapi", "pydantic", "pillow")
        },
        "config": {
            "top_k": 100,
            "bm25_k1": 1.2,
            "bm25_b": 0.75,
            "tokenizer": "unicode-cjk-unigram-bigram-v1",
            "media_limit_bytes": 30 * 1024 * 1024,
            "input": "raw role messages plus published image captions",
        },
    }
    for k in cutoffs:
        summary["metrics"][str(k)] = {
            name: sum(item["metrics"][str(k)][name] for item in eligible) / len(eligible)
            if eligible
            else None
            for name in ("recall", "complete", "mrr")
        }
    (output / "summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    (output / "retrieval.json").write_text(json.dumps(records, indent=2), encoding="utf-8")
    (output / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    return summary
