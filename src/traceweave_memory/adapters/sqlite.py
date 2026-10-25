"""Atomic B0 evidence/FTS persistence; BM25 statistics are strictly scope-local."""

import json
import math
import sqlite3
from collections import Counter
from contextlib import contextmanager
from pathlib import Path

from traceweave_memory.domain import Evidence, WriteConflict, lexical_tokens, text_view

SCHEMA = """
CREATE TABLE requests (
    user_id TEXT NOT NULL, request_id TEXT NOT NULL, body_hash TEXT NOT NULL,
    session_id TEXT NOT NULL, status TEXT NOT NULL CHECK(status='READY'),
    PRIMARY KEY(user_id,request_id)
);
CREATE TABLE evidence (
    row_id INTEGER PRIMARY KEY,
    user_id TEXT NOT NULL, id TEXT NOT NULL, request_id TEXT NOT NULL,
    position INTEGER NOT NULL, content TEXT NOT NULL, created_at TEXT NOT NULL,
    media_bytes INTEGER NOT NULL, token_count INTEGER NOT NULL,
    frequencies TEXT NOT NULL, role TEXT NOT NULL, source_timestamp INTEGER,
    UNIQUE(user_id,id), UNIQUE(user_id,request_id,position),
    FOREIGN KEY(user_id,request_id) REFERENCES requests(user_id,request_id)
);
CREATE INDEX evidence_scope ON evidence(user_id);
CREATE VIRTUAL TABLE lexical USING fts5(tokens, tokenize='unicode61 remove_diacritics 0');
PRAGMA user_version=1;
"""


class SQLiteRepository:
    def __init__(self, path: Path):
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as db:
            db.execute("PRAGMA journal_mode=WAL")
            version = db.execute("PRAGMA user_version").fetchone()[0]
            if version == 0:
                # Fails on unexpected existing tables, rather than replacing them.
                db.executescript("BEGIN IMMEDIATE;" + SCHEMA + "COMMIT;")
            elif version != 1:
                raise RuntimeError("unsupported database schema version")

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.path, timeout=30)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys=ON")
        try:
            yield db
        finally:
            db.close()

    def add(
        self, scope: str, request_id: str, session_id: str, body_hash: str, records: list[Evidence]
    ) -> None:
        with self.connect() as db, db:
            db.execute("BEGIN IMMEDIATE")
            previous = db.execute(
                "SELECT body_hash FROM requests WHERE user_id=? AND request_id=?",
                (scope, request_id),
            ).fetchone()
            if previous:
                if previous["body_hash"] != body_hash:
                    raise WriteConflict("request_id already belongs to a different payload")
                return
            db.execute(
                "INSERT INTO requests VALUES (?,?,?,?,'READY')",
                (scope, request_id, body_hash, session_id),
            )
            for position, record in enumerate(records):
                tokens = lexical_tokens(text_view(record.content))
                cursor = db.execute(
                    "INSERT INTO evidence(user_id,id,request_id,position,content,created_at,"
                    "media_bytes,token_count,frequencies,role,source_timestamp) "
                    "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                    (
                        scope,
                        record.id,
                        request_id,
                        position,
                        json.dumps(record.content, ensure_ascii=False),
                        record.created_at,
                        record.media_bytes,
                        len(tokens),
                        json.dumps(Counter(tokens)),
                        record.role,
                        record.source_timestamp,
                    ),
                )
                db.execute(
                    "INSERT INTO lexical(rowid,tokens) VALUES (?,?)",
                    (cursor.lastrowid, " ".join(tokens)),
                )

    def search(self, scope: str, tokens: list[str], limit: int) -> list[Evidence]:
        terms = sorted(set(tokens))
        if not terms:
            return []
        match = " OR ".join('"' + term.replace('"', '""') + '"' for term in terms)
        with self.connect() as db:
            # A read transaction keeps scope statistics and candidates on one snapshot.
            db.execute("BEGIN")
            statistics = db.execute(
                "SELECT COUNT(*) AS n, COALESCE(SUM(token_count),0) AS length "
                "FROM evidence WHERE user_id=?",
                (scope,),
            ).fetchone()
            if not statistics["n"]:
                return []
            rows = db.execute(
                "SELECT e.* FROM lexical JOIN evidence e ON e.row_id=lexical.rowid "
                "WHERE lexical MATCH ? AND e.user_id=? ORDER BY e.row_id",
                (match, scope),
            ).fetchall()
        # All documents with any query token are included; no global rank/IDF is used.
        frequencies = [json.loads(row["frequencies"]) for row in rows]
        dfs = {term: sum(term in freq for freq in frequencies) for term in terms}
        average = max(statistics["length"] / statistics["n"], 1)
        results = []
        for row, freq in zip(rows, frequencies, strict=True):
            score = 0.0
            for term in terms:
                tf = freq.get(term, 0)
                if not tf:
                    continue
                idf = math.log(1 + (statistics["n"] - dfs[term] + 0.5) / (dfs[term] + 0.5))
                norm = tf + 1.2 * (0.25 + 0.75 * row["token_count"] / average)
                score += idf * tf * 2.2 / norm
            if score > 0:
                results.append(
                    Evidence(
                        row["id"],
                        json.loads(row["content"]),
                        row["created_at"],
                        row["media_bytes"],
                        score,
                    )
                )
        return sorted(results, key=lambda item: (-item.score, item.id))[:limit]
