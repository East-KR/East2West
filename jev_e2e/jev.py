"""TypeSafe Jev Choice 호출. 텍스트 생성 없음, 후보 ID 하나만 받는다."""
from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass, field
from typing import Any

from .snapshot import Element

ABSTAIN = "abstain"
DEFAULT_MODEL = os.environ.get("TYPESAFE_MODEL", "jev-latest")


@dataclass
class Decision:
    element: Element | None  # None == abstain
    confidence: float
    probabilities: dict[str, float]
    margin: float  # 1위 확률 - 2위 확률
    ms: float
    candidates: int
    top: list[tuple[str, float]] = field(default_factory=list)
    input_tokens: int = 0
    output_tokens: int = 0
    prompt_chars: int = 0


class JevClient:
    def __init__(self, api_key: str | None = None, model: str = DEFAULT_MODEL, timeout_s: float = 15):
        from typesafe_sdk import RetryPolicy, TypeSafeClient

        key = api_key or os.environ.get("TYPESAFE_API_KEY") or os.environ.get("TYPESAFEAI_API_KEY")
        if not key:
            raise RuntimeError("TYPESAFE_API_KEY is not set")
        self.model = model
        self.timeout_s = timeout_s
        self.client = TypeSafeClient(api_key=key, timeout=timeout_s, retry=RetryPolicy(max_retries=0))

    def close(self) -> None:
        self.client.close()

    def choose(self, *, goal: str, action: str, elements: list[Element], page: dict[str, Any], history: list[str]) -> Decision:
        from typesafe_sdk import Choice

        ids = {f"c{i}": e for i, e in enumerate(elements)}
        criteria = {cid: e.describe(action) for cid, e in ids.items()}
        criteria[ABSTAIN] = "No candidate matches this step on the current page; do not act."
        state = {
            "step": goal,
            "action": action,
            "page": page,
            "history": history[-6:],
            "rule": "Pick exactly one candidate id that performs this test step on the current page. Never invent targets.",
        }
        question = Choice(
            instructions="Which single candidate should the test runner act on to perform this step? Pick exactly one candidate id.",
            criteria=criteria,
        )
        prompt_chars = len(json.dumps({'state': state, 'instructions': question.instructions, 'criteria': criteria}, ensure_ascii=False))
        t0 = time.perf_counter()
        response = self.client.system_one(state=state, questions={"target": question}, model=self.model, timeout=self.timeout_s)
        ms = (time.perf_counter() - t0) * 1000
        answer = (response.choices or response.answers)["target"]
        probs = {str(k): float(v) for k, v in dict(answer.probabilities or {}).items()}
        ranked = sorted(probs.items(), key=lambda kv: -kv[1])
        margin = (ranked[0][1] - ranked[1][1]) if len(ranked) > 1 else ranked[0][1]
        choice = answer.choice
        if choice != ABSTAIN and choice not in ids:
            raise RuntimeError(f"Jev returned unknown id {choice!r}")
        top = [((ids[k].label() if k in ids else k), v) for k, v in ranked[:3]]
        return Decision(element=None if choice == ABSTAIN else ids[choice], confidence=float(answer.confidence),
                        probabilities=probs, margin=round(margin, 3), ms=round(ms, 1), candidates=len(criteria), top=top,
                        input_tokens=int(getattr(response.usage, 'input_tokens', 0) or 0), output_tokens=int(getattr(response.usage, 'output_tokens', 0) or 0), prompt_chars=prompt_chars)
