import json

import pytest

from app.llm import Completion
from app.models import Source


class FakeLLM:
    """Scripted by `purpose`. Values are a str/dict (repeated) or a list (consumed in order)."""

    def __init__(self, script: dict):
        self.script = {k: (list(v) if isinstance(v, list) else v) for k, v in script.items()}
        self.calls: list[str] = []
        self.prompts: dict[str, str] = {}  # purpose -> last user prompt
        self.systems: dict[str, str] = {}  # purpose -> last system prompt

    def _next(self, purpose: str):
        self.calls.append(purpose)
        key = next((k for k in self.script if purpose == k or purpose.startswith(k + ":")), None)
        assert key is not None, f"no scripted reply for purpose {purpose!r}"
        v = self.script[key]
        if isinstance(v, list):
            v = v.pop(0) if len(v) > 1 else v[0]
        if isinstance(v, Exception):
            raise v
        return v

    def complete(self, system, user, *, tier="fast", purpose=""):
        self.prompts[purpose], self.systems[purpose] = user, system
        v = self._next(purpose)
        return Completion(text=v if isinstance(v, str) else json.dumps(v), tokens=100)

    def complete_json(self, system, user, schema, *, tier="fast", purpose=""):
        self.prompts[purpose] = user
        return schema.model_validate(self._next(purpose)), 100


def make_sources(n_verified=3):
    return [
        Source(title=f"Paper {i}", authors=["A. Author"], year=2022, doi=f"10.1/p{i}",
               abstract=f"Abstract {i}", verified=i <= n_verified)
        for i in range(1, 6)
    ]


@pytest.fixture
def sources():
    return make_sources()
