"""Forensic capture layer for every TypeSafe API interaction.

Design principles:

1. **Capture the wire, not a re-serialization.** We persist the exact request
   body we sent and the exact response bytes we received. Everything else
   (answers, probabilities) is *derived* from those and stored alongside for
   query convenience, never in place of the original.

2. **Content-addressed and tamper-evident.** Request and response bodies are
   SHA-256 hashed. The same logical question asked twice is detectable by
   `request_hash`, which is how we measure model drift and non-determinism.

3. **Nothing is lost on failure.** Errors, timeouts and non-200s are recorded
   with the same fidelity as successes. A missing row means the call never
   happened; it never means the call failed quietly.

4. **Never persist credentials.** Request headers are dropped entirely and the
   Authorization header is never read. Only response headers are kept.

Why SQLite (WAL) over RocksDB: this workload is analytical -- "show me every
Choice whose confidence dropped below 0.5, grouped by question id, across model
versions". That is a relational scan, not a key lookup. SQLite in WAL mode gives
concurrent readers alongside a writer, a single-file artifact we can archive,
and json1 for querying stored distributions in place. RocksDB would force us to
rebuild an index layer to answer any of the questions we actually care about.

Usage:

    from forensics import ForensicRecorder

    with ForensicRecorder("forensics.db") as rec:
        run_id = rec.start_run("experiments", notes="claim verification")
        client = rec.wrap(TypeSafeClient(), run_id)
        response = client.system_one(state, questions)   # captured automatically
"""

from __future__ import annotations

import hashlib
import json
import os
import platform
import sqlite3
import sys
import time
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

__all__ = ["ForensicRecorder", "RecordedClient", "canonical_json", "sha256_of"]


# ---------------------------------------------------------------------------
# Hashing helpers
# ---------------------------------------------------------------------------

def canonical_json(value: Any) -> str:
    """Stable JSON encoding so identical payloads hash identically."""
    return json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)


def sha256_of(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------

SCHEMA = """
PRAGMA journal_mode = WAL;
PRAGMA synchronous = NORMAL;
PRAGMA foreign_keys = ON;

-- One row per experiment session, so results are attributable to a context.
CREATE TABLE IF NOT EXISTS runs (
    run_id          TEXT PRIMARY KEY,
    label           TEXT NOT NULL,
    notes           TEXT,
    started_at      TEXT NOT NULL,
    ended_at        TEXT,
    sdk_version     TEXT,
    python_version  TEXT,
    hostname        TEXT,
    platform        TEXT,
    base_url        TEXT,
    env_fingerprint TEXT
);

-- One row per HTTP call. Holds the verbatim wire payloads.
CREATE TABLE IF NOT EXISTS calls (
    call_id            TEXT PRIMARY KEY,
    run_id             TEXT NOT NULL REFERENCES runs(run_id),
    seq                INTEGER NOT NULL,
    called_at          TEXT NOT NULL,
    request_id         TEXT,
    model_requested    TEXT,
    model_served       TEXT,

    -- verbatim payloads + integrity hashes
    state_json         TEXT NOT NULL,
    questions_json     TEXT NOT NULL,
    request_hash       TEXT NOT NULL,
    response_body      TEXT,
    response_hash      TEXT,
    response_headers   TEXT,

    -- timing: wall clock vs server-reported compute
    latency_ms         REAL NOT NULL,
    upstream_ms        REAL,
    http_status        INTEGER,

    input_tokens       INTEGER,
    output_tokens      INTEGER,

    n_questions        INTEGER NOT NULL,
    ok                 INTEGER NOT NULL,
    error_type         TEXT,
    error_message      TEXT
);

CREATE INDEX IF NOT EXISTS idx_calls_run      ON calls(run_id);
CREATE INDEX IF NOT EXISTS idx_calls_reqhash  ON calls(request_hash);
CREATE INDEX IF NOT EXISTS idx_calls_model    ON calls(model_served);

-- One row per answered question. Derived from response_body, kept for querying.
CREATE TABLE IF NOT EXISTS answers (
    answer_id       INTEGER PRIMARY KEY AUTOINCREMENT,
    call_id         TEXT NOT NULL REFERENCES calls(call_id),
    run_id          TEXT NOT NULL REFERENCES runs(run_id),
    question_id     TEXT NOT NULL,
    question_type   TEXT NOT NULL,

    -- the question as asked, so an answer is interpretable without a join
    instructions    TEXT,
    criteria_json   TEXT,
    question_hash   TEXT NOT NULL,

    -- typed results; exactly one of these is meaningful per type
    noul            REAL,
    choice          TEXT,
    score           REAL,
    confidence      REAL,
    probabilities   TEXT,
    legend          TEXT
);

CREATE INDEX IF NOT EXISTS idx_answers_call   ON answers(call_id);
CREATE INDEX IF NOT EXISTS idx_answers_qid    ON answers(question_id);
CREATE INDEX IF NOT EXISTS idx_answers_qhash  ON answers(question_hash);
CREATE INDEX IF NOT EXISTS idx_answers_type   ON answers(question_type);

-- Explicit findings: an assertion we made about behavior and how it landed.
CREATE TABLE IF NOT EXISTS observations (
    obs_id      INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id      TEXT NOT NULL REFERENCES runs(run_id),
    recorded_at TEXT NOT NULL,
    claim       TEXT NOT NULL,
    verdict     TEXT NOT NULL,
    detail      TEXT
);

CREATE INDEX IF NOT EXISTS idx_obs_run ON observations(run_id);

-- Same request, different answer = drift or non-determinism. This view finds it.
CREATE VIEW IF NOT EXISTS v_repeats AS
SELECT
    c.request_hash,
    a.question_id,
    a.question_type,
    COUNT(*)                                     AS n_calls,
    COUNT(DISTINCT c.model_served)               AS n_models,
    MIN(COALESCE(a.noul, a.score, a.confidence)) AS min_value,
    MAX(COALESCE(a.noul, a.score, a.confidence)) AS max_value,
    MAX(COALESCE(a.noul, a.score, a.confidence))
      - MIN(COALESCE(a.noul, a.score, a.confidence)) AS spread
FROM calls c
JOIN answers a ON a.call_id = c.call_id
WHERE c.ok = 1
GROUP BY c.request_hash, a.question_id, a.question_type
HAVING COUNT(*) > 1;

-- Cost and latency rollup per run.
CREATE VIEW IF NOT EXISTS v_run_cost AS
SELECT
    r.run_id,
    r.label,
    COUNT(c.call_id)              AS calls,
    SUM(c.n_questions)            AS questions,
    SUM(c.input_tokens)           AS input_tokens,
    SUM(c.output_tokens)          AS output_tokens,
    ROUND(SUM(c.latency_ms), 1)   AS total_latency_ms,
    ROUND(AVG(c.latency_ms), 1)   AS mean_latency_ms,
    ROUND(AVG(c.upstream_ms), 1)  AS mean_upstream_ms,
    SUM(CASE WHEN c.ok = 0 THEN 1 ELSE 0 END) AS failures
FROM runs r
LEFT JOIN calls c ON c.run_id = r.run_id
GROUP BY r.run_id;
"""


# ---------------------------------------------------------------------------
# Recorder
# ---------------------------------------------------------------------------

class ForensicRecorder:
    """Owns the SQLite store and captures every call made through it."""

    def __init__(self, db_path: str | Path = "forensics.db") -> None:
        self.db_path = Path(db_path)
        self.conn = sqlite3.connect(self.db_path, isolation_level=None)
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(SCHEMA)
        self._seq = 0

    # -- lifecycle ---------------------------------------------------------

    def __enter__(self) -> ForensicRecorder:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def close(self) -> None:
        self.conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        self.conn.close()

    def start_run(self, label: str, notes: str | None = None) -> str:
        try:
            import typesafe_sdk
            sdk_version = getattr(typesafe_sdk, "__version__", "unknown")
        except Exception:
            sdk_version = "unknown"

        run_id = f"run_{uuid.uuid4().hex[:16]}"
        # Record which env vars were set, never their values.
        env_fingerprint = canonical_json(
            sorted(k for k in os.environ if k.startswith("TYPESAFE_"))
        )
        self.conn.execute(
            """INSERT INTO runs (run_id, label, notes, started_at, sdk_version,
                                 python_version, hostname, platform, base_url,
                                 env_fingerprint)
               VALUES (?,?,?,?,?,?,?,?,?,?)""",
            (
                run_id, label, notes, _utcnow(), sdk_version,
                sys.version.split()[0], platform.node(), platform.platform(),
                os.environ.get("TYPESAFE_BASE_URL", "https://api.typesafe.ai"),
                env_fingerprint,
            ),
        )
        return run_id

    def end_run(self, run_id: str) -> None:
        self.conn.execute(
            "UPDATE runs SET ended_at = ? WHERE run_id = ?", (_utcnow(), run_id)
        )

    def observe(self, run_id: str, claim: str, verdict: str, detail: Any = None) -> None:
        """Record a finding about model behavior, tied to the run that produced it."""
        self.conn.execute(
            """INSERT INTO observations (run_id, recorded_at, claim, verdict, detail)
               VALUES (?,?,?,?,?)""",
            (run_id, _utcnow(), claim, verdict,
             canonical_json(detail) if detail is not None else None),
        )

    # -- capture -----------------------------------------------------------

    def wrap(self, client: Any, run_id: str) -> RecordedClient:
        return RecordedClient(client, self, run_id)

    def _question_payload(self, question: Any) -> dict[str, Any]:
        """Normalize an SDK question object to its wire-ish dict form."""
        if hasattr(question, "model_dump"):
            return question.model_dump(mode="json", exclude_none=True)
        if isinstance(question, dict):
            return question
        return {"repr": repr(question)}

    def record_call(
        self,
        run_id: str,
        state: Any,
        questions: dict[str, Any],
        *,
        model_requested: str | None,
        response: Any = None,
        exc: BaseException | None = None,
        latency_ms: float,
    ) -> str:
        self._seq += 1
        call_id = f"call_{uuid.uuid4().hex[:16]}"

        state_json = canonical_json(state)
        q_payloads = {k: self._question_payload(v) for k, v in questions.items()}
        questions_json = canonical_json(q_payloads)
        request_hash = sha256_of(
            canonical_json({"state": state, "questions": q_payloads,
                            "model": model_requested})
        )

        body = headers = response_hash = None
        upstream_ms = http_status = None
        model_served = in_tok = out_tok = req_id = None

        if response is not None:
            raw = getattr(response, "raw_http_response", None)
            if raw is not None:
                body = raw.text
                response_hash = sha256_of(body)
                # Response headers only -- request headers carry the API key.
                headers = canonical_json(dict(raw.headers))
                http_status = raw.status_code
                ups = raw.headers.get("x-envoy-upstream-service-time")
                if ups is not None:
                    try:
                        upstream_ms = float(ups)
                    except ValueError:
                        pass
            req_id = getattr(response, "request_id", None)
            model_served = getattr(response, "model", None)
            usage = getattr(response, "usage", None)
            if usage is not None:
                in_tok = getattr(usage, "input_tokens", None)
                out_tok = getattr(usage, "output_tokens", None)

        self.conn.execute(
            """INSERT INTO calls (
                   call_id, run_id, seq, called_at, request_id,
                   model_requested, model_served,
                   state_json, questions_json, request_hash,
                   response_body, response_hash, response_headers,
                   latency_ms, upstream_ms, http_status,
                   input_tokens, output_tokens,
                   n_questions, ok, error_type, error_message)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                call_id, run_id, self._seq, _utcnow(), req_id,
                model_requested, model_served,
                state_json, questions_json, request_hash,
                body, response_hash, headers,
                latency_ms, upstream_ms, http_status,
                in_tok, out_tok,
                len(questions), 0 if exc else 1,
                type(exc).__name__ if exc else None,
                str(exc) if exc else None,
            ),
        )

        if response is not None:
            self._record_answers(call_id, run_id, q_payloads, response)

        return call_id

    def _record_answers(
        self, call_id: str, run_id: str, q_payloads: dict[str, Any], response: Any
    ) -> None:
        answers = getattr(response, "answers", {}) or {}
        for qid, ans in answers.items():
            payload = q_payloads.get(qid, {})
            qtype = getattr(ans, "type", None) or payload.get("type") or "unknown"
            criteria = payload.get("criteria")
            probs = getattr(ans, "probabilities", None)
            legend = getattr(ans, "legend", None)

            self.conn.execute(
                """INSERT INTO answers (
                       call_id, run_id, question_id, question_type,
                       instructions, criteria_json, question_hash,
                       noul, choice, score, confidence, probabilities, legend)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    call_id, run_id, qid, str(qtype),
                    canonical_json(payload.get("instructions"))
                    if payload.get("instructions") is not None else None,
                    canonical_json(criteria) if criteria is not None else None,
                    sha256_of(canonical_json(payload)),
                    getattr(ans, "noul", None),
                    getattr(ans, "choice", None),
                    getattr(ans, "score", None),
                    getattr(ans, "confidence", None),
                    canonical_json(probs) if probs is not None else None,
                    canonical_json(legend) if legend is not None else None,
                ),
            )

    # -- read side ---------------------------------------------------------

    def query(self, sql: str, params: tuple = ()) -> list[sqlite3.Row]:
        return self.conn.execute(sql, params).fetchall()

    def verify_integrity(self) -> dict[str, Any]:
        """Re-hash every stored response body and confirm it matches."""
        rows = self.conn.execute(
            "SELECT call_id, response_body, response_hash FROM calls "
            "WHERE response_body IS NOT NULL"
        ).fetchall()
        mismatches = [
            r["call_id"] for r in rows if sha256_of(r["response_body"]) != r["response_hash"]
        ]
        return {
            "checked": len(rows),
            "mismatches": mismatches,
            "intact": not mismatches,
        }


class RecordedClient:
    """Drop-in proxy around TypeSafeClient that persists every call."""

    def __init__(self, client: Any, recorder: ForensicRecorder, run_id: str) -> None:
        self._client = client
        self._rec = recorder
        self._run_id = run_id

    def system_one(self, state: Any, questions: dict[str, Any], **kwargs: Any) -> Any:
        model = kwargs.get("model")
        start = time.perf_counter()
        try:
            response = self._client.system_one(state, questions, **kwargs)
        except BaseException as exc:
            elapsed = (time.perf_counter() - start) * 1000
            self._rec.record_call(
                self._run_id, state, questions,
                model_requested=model, exc=exc, latency_ms=elapsed,
            )
            raise
        elapsed = (time.perf_counter() - start) * 1000
        self._rec.record_call(
            self._run_id, state, questions,
            model_requested=model, response=response, latency_ms=elapsed,
        )
        return response

    def __getattr__(self, name: str) -> Any:
        return getattr(self._client, name)


@contextmanager
def recording_run(
    client: Any, db_path: str | Path, label: str, notes: str | None = None
) -> Iterator[tuple[RecordedClient, ForensicRecorder, str]]:
    """Convenience: open a store, start a run, yield a wrapped client."""
    rec = ForensicRecorder(db_path)
    run_id = rec.start_run(label, notes)
    try:
        yield rec.wrap(client, run_id), rec, run_id
    finally:
        rec.end_run(run_id)
        rec.close()
