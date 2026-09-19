"""ASGI entrypoint: `uvicorn app.main:app`."""

from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool

from app.api import create_app
from app.config import settings
from app.store import PgRunStore

_pool = ConnectionPool(settings.database_url, min_size=1, max_size=5, open=True,
                       kwargs={"autocommit": True, "row_factory": dict_row})
_store = PgRunStore(_pool)
_store.init()
app = create_app(_store)
