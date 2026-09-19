FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1
WORKDIR /srv

COPY pyproject.toml ./
COPY app ./app
RUN pip install . && useradd --system --no-create-home appuser
USER appuser

# API by default; the worker service overrides this with `python -m app.worker`.
CMD ["sh", "-c", "uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000}"]
