"""Worker: claims queued runs and advances their graph until it needs a human
or finishes. Stateless. Everything it needs to resume lives in the checkpointer,
so a crash or deploy loses at most the node that was executing."""

import logging
import signal
import threading

import httpx
from langgraph.types import Command

from app.citations import render_final
from app.config import settings
from app.models import Source
from app.store import RunStore

log = logging.getLogger("worker")


def _cfg(run_id: str) -> dict:
    return {"configurable": {"thread_id": run_id}, "recursion_limit": 60}


def _pending_gate(snap):
    for task in snap.tasks:
        if task.interrupts:
            return task.interrupts[0].value
    return None


def advance(graph, run: dict) -> None:
    """Move the graph forward from wherever the checkpoint says it is.

    Decides from checkpointed state, not from the run's status, so it is correct
    after a crash at any point: fresh start, mid-run continue, or resume-from-gate."""
    cfg = _cfg(run["id"])
    snap = graph.get_state(cfg)
    if not snap.values and not snap.next:
        graph.invoke({"prompt": run["prompt"]}, cfg)
    elif _pending_gate(snap) is not None:
        if run.get("resume") is None:
            return  # still waiting on the human; caller re-reads the gate
        graph.invoke(Command(resume=run["resume"]), cfg)
    elif snap.next:
        graph.invoke(None, cfg)  # crashed mid-run: continue from the last checkpoint


def summarize(values: dict) -> dict:
    crit = values.get("critique") or {}
    best = values.get("best")
    approved = bool(crit.get("approved"))
    draft, issues, note = values.get("draft", ""), crit.get("issues", []), ""
    if not approved and best:
        # The run ended on the revision cap. Return the last version that met every mechanical
        # requirement (length, sections, verified citations), not whatever the final rewrite produced.
        if best["draft"] != draft:
            note = (f"Returned draft v{best['revision']}, the last version that met all length, section and "
                    f"citation checks; later rewrites broke them.")
        draft, issues = best["draft"], best["issues"]
    sources = values.get("sources", [])
    srcs = [Source.model_validate(x) for x in sources]
    return {
        "draft": render_final(draft, srcs) if draft else "",
        "approved": approved,
        "needs_human_review": not approved,
        "open_issues": issues,
        "note": note,
        "revision_count": values.get("revision_count", 0),
        "tokens_used": values.get("tokens_used", 0),
        "verified_sources": len(sources),
        "log": values.get("log", []),
    }


def process(store: RunStore, graph, run: dict) -> None:
    stop = threading.Event()

    def beat():
        while not stop.wait(max(settings.stale_run_seconds / 4, 1)):
            store.heartbeat(run["id"])

    threading.Thread(target=beat, daemon=True).start()
    try:
        advance(graph, run)
        snap = graph.get_state(_cfg(run["id"]))
        gate = _pending_gate(snap)
        if gate is not None:
            store.finish(run["id"], "awaiting_approval", gate=gate)
        else:
            store.finish(run["id"], "completed", result=summarize(snap.values))
    except Exception as exc:  # any failure becomes a recorded, retryable state
        log.exception("run %s failed", run["id"])
        partial = {}
        try:
            partial = summarize(graph.get_state(_cfg(run["id"])).values)
        except Exception:
            log.warning("run %s: could not read partial state", run["id"], exc_info=True)
        store.finish(run["id"], "failed", result=partial or None, error=f"{type(exc).__name__}: {exc}")
    finally:
        stop.set()


def run_forever(store: RunStore, graph, stop: threading.Event | None = None) -> None:
    stop = stop or threading.Event()
    while not stop.is_set():
        n = store.requeue_stale(settings.stale_run_seconds)
        if n:
            log.warning("re-queued %d stale run(s)", n)
        run = store.claim()
        if run is None:
            stop.wait(settings.worker_poll_seconds)
            continue
        log.info("claimed run %s (attempt %s)", run["id"], run["attempts"])
        process(store, graph, run)


def main() -> None:
    from langgraph.checkpoint.postgres import PostgresSaver
    from psycopg.rows import dict_row
    from psycopg_pool import ConnectionPool

    from app.citations import verify_source
    from app.graph import build_graph
    from app.literature import search_all
    from app.llm import RoutedLLM, build_providers
    from app.nodes import Tools
    from app.store import PgRunStore

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    pool = ConnectionPool(settings.database_url, min_size=1, max_size=4, open=True,
                          kwargs={"autocommit": True, "prepare_threshold": 0, "row_factory": dict_row})
    store = PgRunStore(pool)
    store.init()
    saver = PostgresSaver(pool)
    saver.setup()

    http = httpx.Client(headers={"User-Agent": "research-paper-agents/0.1"})
    tools = Tools(search=lambda q: search_all(q, http), verify=lambda s: verify_source(s, http))
    graph = build_graph(RoutedLLM(build_providers()), tools, saver)

    stop = threading.Event()
    for sig in (signal.SIGINT, signal.SIGTERM):
        signal.signal(sig, lambda *_: stop.set())  # finish the current run, then exit
    run_forever(store, graph, stop)


if __name__ == "__main__":
    main()
