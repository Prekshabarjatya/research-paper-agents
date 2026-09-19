"""Run lifecycle store: queued -> running -> awaiting_approval | completed | failed | cancelled.

The graph's own progress lives in the LangGraph checkpointer; this table tracks
what the service needs (status, the pending gate, the human's decision, errors)
and doubles as the job queue via FOR UPDATE SKIP LOCKED."""

import threading
import time
import uuid
from pathlib import Path
from typing import Protocol

ACTIVE = ("queued", "running", "awaiting_approval")


class RunStore(Protocol):
    def create(self, prompt: str) -> dict: ...
    def get(self, run_id: str) -> dict | None: ...
    def count_active(self) -> int: ...
    def claim(self) -> dict | None: ...
    def heartbeat(self, run_id: str) -> None: ...
    def finish(self, run_id: str, status: str, *, gate=None, result=None, error=None) -> None: ...
    def approve(self, run_id: str, decision: dict) -> bool: ...
    def retry(self, run_id: str) -> bool: ...
    def cancel(self, run_id: str) -> bool: ...
    def requeue_stale(self, older_than_seconds: float) -> int: ...
    def ping(self) -> bool: ...


class MemoryRunStore:
    """Same semantics as PgRunStore, for tests and local hacking."""

    def __init__(self):
        self._runs: dict[str, dict] = {}
        self._lock = threading.Lock()

    def create(self, prompt):
        run = {"id": str(uuid.uuid4()), "status": "queued", "prompt": prompt, "gate": None,
               "resume": None, "result": None, "error": None, "attempts": 0,
               "heartbeat": None, "created_at": time.time(), "updated_at": time.time()}
        with self._lock:
            self._runs[run["id"]] = run
        return dict(run)

    def get(self, run_id):
        with self._lock:
            r = self._runs.get(run_id)
            return dict(r) if r else None

    def count_active(self):
        with self._lock:
            return sum(r["status"] in ACTIVE for r in self._runs.values())

    def claim(self):
        with self._lock:
            queued = sorted((r for r in self._runs.values() if r["status"] == "queued"),
                            key=lambda r: r["created_at"])
            if not queued:
                return None
            r = queued[0]
            r.update(status="running", heartbeat=time.time(), attempts=r["attempts"] + 1,
                     updated_at=time.time())
            return dict(r)

    def heartbeat(self, run_id):
        with self._lock:
            self._runs[run_id]["heartbeat"] = time.time()

    def finish(self, run_id, status, *, gate=None, result=None, error=None):
        with self._lock:
            r = self._runs[run_id]
            if r["status"] == "cancelled":
                return  # a cancel that raced with the worker wins
            r.update(status=status, gate=gate, result=result, error=error, updated_at=time.time())
            if status != "running":
                r["resume"] = None

    def approve(self, run_id, decision):
        with self._lock:
            r = self._runs.get(run_id)
            if not r or r["status"] != "awaiting_approval":
                return False
            r.update(status="queued", resume=decision, gate=None, updated_at=time.time())
            return True

    def retry(self, run_id):
        with self._lock:
            r = self._runs.get(run_id)
            if not r or r["status"] != "failed":
                return False
            r.update(status="queued", error=None, updated_at=time.time())
            return True

    def cancel(self, run_id):
        with self._lock:
            r = self._runs.get(run_id)
            if not r or r["status"] not in ACTIVE:
                return False
            r.update(status="cancelled", updated_at=time.time())
            return True

    def requeue_stale(self, older_than_seconds):
        cutoff = time.time() - older_than_seconds
        n = 0
        with self._lock:
            for r in self._runs.values():
                if r["status"] == "running" and (r["heartbeat"] or 0) < cutoff:
                    r["status"] = "queued"
                    n += 1
        return n

    def ping(self):
        return True


class PgRunStore:
    def __init__(self, pool):
        self.pool = pool

    def init(self) -> None:
        sql = (Path(__file__).parent / "schema.sql").read_text()
        with self.pool.connection() as conn:
            # One statement per execute(): the pool prepares queries, and Postgres
            # rejects multi-statement text in a prepared statement.
            for statement in filter(None, (part.strip() for part in sql.split(";"))):
                conn.execute(statement)

    def _one(self, sql, params=()):
        with self.pool.connection() as conn:
            row = conn.execute(sql, params).fetchone()
            return row

    def create(self, prompt):
        return self._one("INSERT INTO runs (id, prompt) VALUES (%s, %s) RETURNING *",
                         (str(uuid.uuid4()), prompt))

    def get(self, run_id):
        return self._one("SELECT * FROM runs WHERE id = %s", (run_id,))

    def count_active(self):
        row = self._one("SELECT count(*) AS n FROM runs WHERE status = ANY(%s)", (list(ACTIVE),))
        return row["n"]

    def claim(self):
        return self._one(
            """UPDATE runs SET status='running', heartbeat=now(), updated_at=now(), attempts=attempts+1
               WHERE id = (SELECT id FROM runs WHERE status='queued'
                           ORDER BY created_at LIMIT 1 FOR UPDATE SKIP LOCKED)
               RETURNING *""")

    def heartbeat(self, run_id):
        self._one("UPDATE runs SET heartbeat=now() WHERE id=%s RETURNING id", (run_id,))

    def finish(self, run_id, status, *, gate=None, result=None, error=None):
        from psycopg.types.json import Jsonb
        self._one(
            """UPDATE runs SET status=%s, gate=%s, result=%s, error=%s, updated_at=now(),
                      resume = CASE WHEN %s = 'running' THEN resume ELSE NULL END
               WHERE id=%s AND status <> 'cancelled' RETURNING id""",
            (status, Jsonb(gate) if gate is not None else None,
             Jsonb(result) if result is not None else None, error, status, run_id))

    def approve(self, run_id, decision):
        from psycopg.types.json import Jsonb
        return self._one(
            """UPDATE runs SET status='queued', resume=%s, gate=NULL, updated_at=now()
               WHERE id=%s AND status='awaiting_approval' RETURNING id""",
            (Jsonb(decision), run_id)) is not None

    def retry(self, run_id):
        return self._one("UPDATE runs SET status='queued', error=NULL, updated_at=now() "
                         "WHERE id=%s AND status='failed' RETURNING id", (run_id,)) is not None

    def cancel(self, run_id):
        return self._one("UPDATE runs SET status='cancelled', updated_at=now() "
                         "WHERE id=%s AND status = ANY(%s) RETURNING id", (run_id, list(ACTIVE))) is not None

    def requeue_stale(self, older_than_seconds):
        with self.pool.connection() as conn:
            cur = conn.execute(
                "UPDATE runs SET status='queued' WHERE status='running' "
                "AND heartbeat < now() - make_interval(secs => %s)", (older_than_seconds,))
            return cur.rowcount

    def ping(self):
        try:
            return self._one("SELECT 1 AS ok")["ok"] == 1
        except Exception:  # noqa: BLE001  health check reports, never raises
            return False
