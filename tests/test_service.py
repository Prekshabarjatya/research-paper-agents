import pytest
from fastapi.testclient import TestClient
from langgraph.checkpoint.memory import MemorySaver

from app.api import create_app
from app.config import settings
from app.graph import build_graph
from app.llm import LLMError
from app.store import MemoryRunStore
from app.worker import process
from tests.conftest import FakeLLM
from tests.test_graph import OUTLINE, approve, script, tools

PROMPT = "Write a 1500 word paper on AI routing in logistics."


def drain(store, graph):
    while (run := store.claim()) is not None:
        process(store, graph, run)


@pytest.fixture
def store():
    return MemoryRunStore()


@pytest.fixture
def client(store, monkeypatch):
    monkeypatch.setattr(settings, "api_token", "secret")
    return TestClient(create_app(store), headers={"Authorization": "Bearer secret"})


# ---- worker ----------------------------------------------------------------

def test_worker_drives_run_through_both_gates_to_completion(store):
    graph = build_graph(FakeLLM(script()), tools(), MemorySaver())
    run = store.create(PROMPT)

    drain(store, graph)
    r = store.get(run["id"])
    assert r["status"] == "awaiting_approval" and r["gate"]["gate"] == "topic"

    assert store.approve(run["id"], approve())
    drain(store, graph)
    assert store.get(run["id"])["gate"]["gate"] == "thesis"

    assert store.approve(run["id"], approve())
    drain(store, graph)
    done = store.get(run["id"])
    assert done["status"] == "completed"
    assert done["result"]["approved"] and "## Introduction" in done["result"]["draft"]
    assert done["gate"] is None and done["resume"] is None


def test_failed_run_keeps_checkpoint_and_retry_resumes_without_redoing_work(store):
    llm = FakeLLM(script(planner=[LLMError("groq down"), OUTLINE]))
    graph = build_graph(llm, tools(), MemorySaver())
    run = store.create(PROMPT)
    drain(store, graph)
    store.approve(run["id"], approve())
    drain(store, graph)
    store.approve(run["id"], approve())
    drain(store, graph)

    failed = store.get(run["id"])
    assert failed["status"] == "failed" and "groq down" in failed["error"]

    assert store.retry(run["id"])
    drain(store, graph)
    assert store.get(run["id"])["status"] == "completed"
    assert llm.calls.count("analyst") == 1 and llm.calls.count("strategist") == 1
    assert llm.calls.count("planner") == 2  # only the failed step re-ran


def test_stale_running_run_is_requeued_and_continues_from_checkpoint(store):
    llm = FakeLLM(script())
    graph = build_graph(llm, tools(), MemorySaver())
    run = store.create(PROMPT)
    drain(store, graph)                        # stops at the topic gate
    store.approve(run["id"], approve())
    store.claim()                              # a worker claims it, then "dies": no finish, no heartbeat
    assert store.get(run["id"])["status"] == "running"

    assert store.requeue_stale(0) == 1
    drain(store, graph)
    assert store.get(run["id"])["gate"]["gate"] == "thesis"
    assert llm.calls.count("strategist") == 1


def test_budget_overrun_fails_the_run_with_partial_result(store, monkeypatch):
    from app import nodes
    monkeypatch.setattr(nodes.settings, "max_tokens_per_run", 150)
    graph = build_graph(FakeLLM(script()), tools(), MemorySaver())
    run = store.create(PROMPT)
    drain(store, graph)
    store.approve(run["id"], approve())
    drain(store, graph)
    r = store.get(run["id"])
    assert r["status"] == "failed" and "BudgetExceeded" in r["error"]
    assert r["result"]["tokens_used"] == 200


def test_cancel_wins_over_a_worker_that_finishes_afterwards(store):
    graph = build_graph(FakeLLM(script()), tools(), MemorySaver())
    run = store.create(PROMPT)
    claimed = store.claim()
    assert store.cancel(run["id"])
    process(store, graph, claimed)
    assert store.get(run["id"])["status"] == "cancelled"


# ---- API -------------------------------------------------------------------

def test_health_is_open_everything_else_needs_the_token(store, monkeypatch):
    monkeypatch.setattr(settings, "api_token", "secret")
    c = TestClient(create_app(store))
    assert c.get("/health").status_code == 200
    assert c.post("/runs", json={"prompt": PROMPT}).status_code == 401
    bad = {"Authorization": "Bearer nope"}
    assert c.post("/runs", json={"prompt": PROMPT}, headers=bad).status_code == 401


def test_unset_token_locks_the_api_instead_of_opening_it(store, monkeypatch):
    monkeypatch.setattr(settings, "api_token", "")
    c = TestClient(create_app(store), headers={"Authorization": "Bearer "})
    assert c.post("/runs", json={"prompt": PROMPT}).status_code == 401


def test_create_validates_prompt_and_caps_active_runs(client, monkeypatch):
    assert client.post("/runs", json={"prompt": "short"}).status_code == 422
    monkeypatch.setattr(settings, "max_active_runs", 1)
    assert client.post("/runs", json={"prompt": PROMPT}).status_code == 202
    assert client.post("/runs", json={"prompt": PROMPT}).status_code == 429


def test_full_lifecycle_over_http(client, store):
    graph = build_graph(FakeLLM(script()), tools(), MemorySaver())
    run_id = client.post("/runs", json={"prompt": PROMPT}).json()["id"]

    assert client.get(f"/runs/{run_id}/draft").status_code == 404
    assert client.post(f"/runs/{run_id}/approve", json={"approved": True}).status_code == 409

    drain(store, graph)
    body = client.get(f"/runs/{run_id}").json()
    assert body["status"] == "awaiting_approval" and body["gate"]["gate"] == "topic"

    assert client.post(f"/runs/{run_id}/approve", json={"approved": True}).status_code == 202
    drain(store, graph)
    assert client.post(f"/runs/{run_id}/approve", json={"approved": True}).status_code == 202
    drain(store, graph)

    assert client.get(f"/runs/{run_id}").json()["status"] == "completed"
    draft = client.get(f"/runs/{run_id}/draft")
    assert draft.headers["content-type"].startswith("text/markdown") and "References" in draft.text


def test_unknown_run_and_bad_transitions(client):
    assert client.get("/runs/nope").status_code == 404
    run_id = client.post("/runs", json={"prompt": PROMPT}).json()["id"]
    assert client.post(f"/runs/{run_id}/retry").status_code == 409
    assert client.post(f"/runs/{run_id}/cancel").status_code == 200
    assert client.post(f"/runs/{run_id}/cancel").status_code == 409


def test_capped_run_returns_the_best_draft_not_the_last_one(store, monkeypatch):
    from app.config import settings as cfg
    from app.worker import summarize
    from tests.test_graph import CONSTRAINTS, prose
    monkeypatch.setattr(cfg, "max_revisions", 3)
    long_ = "word " * 300
    llm = FakeLLM(script(
        analyst={**CONSTRAINTS, "max_words": 250},
        **{"writer:Introduction": [long_, prose("[S1]", 80)],
           "writer:Analysis": [long_, prose("[S2]", 80), long_]},   # v3 rewrites Analysis, overshoots
        critic={"approved": False, "issues": ["thin argument"], "sections_to_fix": ["Analysis"]},
    ))
    graph = build_graph(llm, tools(), MemorySaver())
    run = store.create(PROMPT)
    drain(store, graph)
    store.approve(run["id"], approve())
    drain(store, graph)
    store.approve(run["id"], approve())
    drain(store, graph)

    result = store.get(run["id"])["result"]
    assert result["revision_count"] == 3 and not result["approved"]
    assert "Returned draft v2" in result["note"]
    assert result["open_issues"] == ["thin argument"]           # the LLM critic's issues for v2
    assert "word word word" not in result["draft"]              # v3's overlong text is not returned
    assert summarize(graph.get_state({"configurable": {"thread_id": run["id"]}}).values)["draft"]
