import secrets

from fastapi import Depends, FastAPI, Header, HTTPException, Response
from pydantic import BaseModel, Field

from app.config import settings
from app.store import RunStore


class NewRun(BaseModel):
    prompt: str = Field(min_length=10, max_length=20_000)


class Decision(BaseModel):
    approved: bool
    feedback: str = ""
    topic: str = ""
    thesis: str = ""


def _public(run: dict) -> dict:
    return {k: run.get(k) for k in ("id", "status", "gate", "result", "error", "attempts")} | {
        "created_at": str(run.get("created_at")), "updated_at": str(run.get("updated_at"))}


def create_app(store: RunStore) -> FastAPI:
    app = FastAPI(title="Research Paper Agents", docs_url=None, redoc_url=None)

    def auth(authorization: str = Header(default="")):
        token = settings.api_token
        supplied = authorization.removeprefix("Bearer ").strip()
        # Fail closed: an unset token locks the API instead of leaving it open.
        if not token or not secrets.compare_digest(supplied, token):
            raise HTTPException(401, "invalid or missing bearer token")

    def load(run_id: str) -> dict:
        run = store.get(run_id)
        if run is None:
            raise HTTPException(404, "run not found")
        return run

    @app.get("/health")
    def health():
        if not store.ping():
            raise HTTPException(503, "database unavailable")
        return {"ok": True}

    @app.post("/runs", status_code=202, dependencies=[Depends(auth)])
    def create_run(body: NewRun):
        if store.count_active() >= settings.max_active_runs:
            raise HTTPException(429, "too many active runs; try again later")
        return _public(store.create(body.prompt))

    @app.get("/runs/{run_id}", dependencies=[Depends(auth)])
    def get_run(run_id: str):
        return _public(load(run_id))

    @app.post("/runs/{run_id}/approve", status_code=202, dependencies=[Depends(auth)])
    def approve(run_id: str, body: Decision):
        run = load(run_id)
        decision = body.model_dump(exclude_defaults=False)
        if not store.approve(run_id, decision):
            raise HTTPException(409, f"run is '{run['status']}', not awaiting approval")
        return _public(load(run_id))

    @app.post("/runs/{run_id}/retry", status_code=202, dependencies=[Depends(auth)])
    def retry(run_id: str):
        run = load(run_id)
        if not store.retry(run_id):
            raise HTTPException(409, f"run is '{run['status']}'; only failed runs can be retried")
        return _public(load(run_id))

    @app.post("/runs/{run_id}/cancel", dependencies=[Depends(auth)])
    def cancel(run_id: str):
        run = load(run_id)
        if not store.cancel(run_id):
            raise HTTPException(409, f"run is '{run['status']}' and cannot be cancelled")
        return _public(load(run_id))

    @app.get("/runs/{run_id}/draft", dependencies=[Depends(auth)])
    def draft(run_id: str):
        run = load(run_id)
        text = (run.get("result") or {}).get("draft")
        if not text:
            raise HTTPException(404, "no draft yet")
        return Response(text, media_type="text/markdown")

    return app
