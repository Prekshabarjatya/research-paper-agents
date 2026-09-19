"""ASGI entrypoint: `uvicorn app.main:app`.

With RUN_WORKER=1 the worker runs in a background thread of this process, so a single web
service plus a database is a complete deployment. Runs are checkpointed in Postgres, so a
restart loses at most the step in progress; the run resumes once its heartbeat goes stale."""

import logging
import threading

from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool

from app.api import create_app
from app.config import settings
from app.store import PgRunStore

log = logging.getLogger("main")

_pool = ConnectionPool(settings.database_url, min_size=1, max_size=5, open=True,
                       kwargs={"autocommit": True, "prepare_threshold": 0, "row_factory": dict_row})
_store = PgRunStore(_pool)
_store.init()  # before the worker thread starts, so the two never race to create tables
app = create_app(_store)


def _embedded_worker() -> None:
    from app.worker import build_worker, run_forever

    try:
        store, graph = build_worker()
    except Exception:
        log.exception("embedded worker failed to start; the API is up but runs will not progress")
        return
    run_forever(store, graph)


if settings.run_worker:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    threading.Thread(target=_embedded_worker, daemon=True, name="embedded-worker").start()
