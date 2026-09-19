import secrets
from pathlib import Path

from fastapi import Depends, FastAPI, Header, HTTPException, Query, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
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
    return {k: run.get(k) for k in ("id", "status", "prompt", "gate", "result", "error", "attempts",
                                    "progress")} | {
        "created_at": str(run.get("created_at")), "updated_at": str(run.get("updated_at"))}


def create_app(store: RunStore) -> FastAPI:
    app = FastAPI(title="Research Paper Agents", docs_url=None, redoc_url=None)
    origins = [o.strip().rstrip("/") for o in settings.cors_origins.split(",") if o.strip()]
    if origins:  # only needed when the UI is hosted on a different origin (e.g. Vercel)
        app.add_middleware(CORSMiddleware, allow_origins=origins, allow_methods=["GET", "POST", "DELETE"],
                           allow_headers=["Authorization", "Content-Type"], max_age=600)

    def auth(authorization: str = Header(default="")):
        token = settings.api_token
        supplied = authorization.removeprefix("Bearer ").strip()
        # Fail closed: an unset token locks the API instead of leaving it open.
        if not token or not secrets.compare_digest(supplied, token):
            raise HTTPException(401, "invalid or missing bearer token")

    @app.middleware("http")
    async def security_headers(request: Request, call_next):
        resp = await call_next(request)
        resp.headers["Content-Security-Policy"] = (
            "default-src 'self'; img-src 'self' data:; style-src 'self'; script-src 'self'; "
            "connect-src 'self'; base-uri 'none'; form-action 'none'; frame-ancestors 'none'")
        resp.headers["X-Content-Type-Options"] = "nosniff"
        resp.headers["Referrer-Policy"] = "no-referrer"
        return resp

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

    @app.get("/runs", dependencies=[Depends(auth)])
    def list_runs(limit: int = Query(50, ge=1, le=200)):
        return [
            {"id": r["id"], "status": r["status"], "prompt": r["prompt"][:160],
             "topic": (r.get("result") or {}).get("topic"),
             "needs_human_review": (r.get("result") or {}).get("needs_human_review"),
             "created_at": str(r["created_at"]), "updated_at": str(r["updated_at"])}
            for r in store.list(limit)
        ]

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

    @app.delete("/runs/{run_id}", status_code=204, dependencies=[Depends(auth)])
    def delete_run(run_id: str):
        run = load(run_id)
        if not store.delete(run_id):
            raise HTTPException(409, f"run is '{run['status']}'; cancel it before deleting")
        return Response(status_code=204)

    @app.get("/runs/{run_id}/draft", dependencies=[Depends(auth)])
    def draft(run_id: str):
        run = load(run_id)
        text = (run.get("result") or {}).get("draft")
        if not text:
            raise HTTPException(404, "no draft yet")
        return Response(text, media_type="text/markdown")

    static = Path(__file__).parent / "static"
    if static.is_dir():  # mounted last so it never shadows an API route
        app.mount("/", StaticFiles(directory=static, html=True), name="ui")
    return app
