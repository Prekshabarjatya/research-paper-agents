"""Integration tests against a real Postgres. Skipped when none is reachable.

    docker run -d --rm --name rpa-test-pg -e POSTGRES_USER=research -e POSTGRES_PASSWORD=research \
        -e POSTGRES_DB=research -p 55432:5432 postgres:16-alpine
"""

import os
import threading

import psycopg
import pytest
from langgraph.checkpoint.postgres import PostgresSaver
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool

from app.graph import build_graph
from app.store import PgRunStore
from app.worker import process
from tests.conftest import FakeLLM
from tests.test_graph import approve, script, tools

URL = os.environ.get("TEST_DATABASE_URL", "postgresql://research:research@localhost:55432/research")
PROMPT = "Write a 1500 word paper on AI routing in logistics."

try:
    psycopg.connect(URL, connect_timeout=2).close()
except psycopg.OperationalError:
    pytest.skip("no test Postgres reachable", allow_module_level=True)


@pytest.fixture
def pool():
    p = ConnectionPool(URL, min_size=1, max_size=8, open=True,
                       kwargs={"autocommit": True, "prepare_threshold": 0, "row_factory": dict_row})
    with p.connection() as c:
        c.execute("DROP TABLE IF EXISTS runs, checkpoints, checkpoint_blobs, checkpoint_writes, checkpoint_migrations")
    yield p
    p.close()


@pytest.fixture
def store(pool):
    s = PgRunStore(pool)
    s.init()
    return s


def make_graph(pool, llm):
    saver = PostgresSaver(pool)
    saver.setup()
    return build_graph(llm, tools(), saver)


def drain(store, graph):
    while (run := store.claim()) is not None:
        process(store, graph, run)


def test_two_workers_never_claim_the_same_run(store):
    for _ in range(6):
        store.create(PROMPT)
    claimed, lock = [], threading.Lock()

    def worker():
        while (r := store.claim()) is not None:
            with lock:
                claimed.append(r["id"])

    threads = [threading.Thread(target=worker) for _ in range(4)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    assert len(claimed) == 6 and len(set(claimed)) == 6


def test_state_transitions_are_guarded(store):
    run = store.create(PROMPT)
    assert not store.approve(run["id"], approve())      # not awaiting approval
    assert not store.retry(run["id"])                   # not failed
    assert store.cancel(run["id"]) and not store.cancel(run["id"])
    store.finish(run["id"], "completed", result={"x": 1})
    assert store.get(run["id"])["status"] == "cancelled"  # cancel is sticky


def test_full_run_on_postgres_survives_a_worker_restart_at_each_gate(pool, store):
    llm = FakeLLM(script())
    run = store.create(PROMPT)

    drain(store, make_graph(pool, llm))
    assert store.get(run["id"])["gate"]["gate"] == "topic"

    # "Deploy": brand-new graph and saver objects, same database. Nothing in memory carries over.
    store.approve(run["id"], approve())
    drain(store, make_graph(pool, llm))
    assert store.get(run["id"])["gate"]["gate"] == "thesis"

    store.approve(run["id"], approve())
    drain(store, make_graph(pool, llm))
    done = store.get(run["id"])
    assert done["status"] == "completed" and done["result"]["approved"]
    assert "## References" in done["result"]["draft"]
    assert llm.calls.count("analyst") == 1 and llm.calls.count("strategist") == 1


def test_crash_mid_run_recovers_from_postgres_checkpoint(pool, store):
    llm = FakeLLM(script())
    run = store.create(PROMPT)
    drain(store, make_graph(pool, llm))
    store.approve(run["id"], approve())
    store.claim()                                       # worker claims, then dies silently
    assert store.requeue_stale(0) == 1
    drain(store, make_graph(pool, llm))
    assert store.get(run["id"])["gate"]["gate"] == "thesis"
    assert llm.calls.count("strategist") == 1


def test_progress_appends_and_list_returns_compact_rows_newest_first(store):
    a, b = store.create(PROMPT), store.create(PROMPT + " B")
    store.add_progress(a["id"], {"node": "analyst", "log": ["x"], "at": "t1"})
    store.add_progress(a["id"], {"node": "scout", "log": ["y"], "at": "t2"})
    assert [p["node"] for p in store.get(a["id"])["progress"]] == ["analyst", "scout"]
    store.finish(a["id"], "completed", result={"topic": "T", "needs_human_review": False, "draft": "big"})
    rows = store.list()
    assert [r["id"] for r in rows] == [b["id"], a["id"]]
    assert rows[1]["result"] == {"topic": "T", "needs_human_review": False}  # draft is not shipped in the list
