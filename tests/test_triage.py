"""triage 오프라인 테스트: Jev를 부르지 않고 근거 구성, 응답 파싱, 오류 격리, 요약을 확인한다."""
from types import SimpleNamespace

import pytest

from eastshift import triage


class FakeClient:
    def __init__(self, cause="ui_changed", probs=None, retry=0.1, spread=0.7, raise_=None):
        self.probs = probs or {"ui_changed": 0.8, "real_defect": 0.1, "environment": 0.02, "timing": 0.02, "test_bug": 0.04, "abstain": 0.02}
        self.cause, self.retry, self.spread, self.raise_ = cause, retry, spread, raise_
        self.calls = []

    def ask(self, *, state, questions):
        self.calls.append((state, questions))
        if self.raise_:
            raise self.raise_
        answers = {
            "cause": SimpleNamespace(choice=self.cause, confidence=0.8, probabilities=self.probs),
            "retry_may_pass": SimpleNamespace(noul=self.retry),
            "likely_widespread": SimpleNamespace(noul=self.spread),
        }
        return SimpleNamespace(answers=answers, usage=SimpleNamespace(input_tokens=900, output_tokens=40)), 210.0


def step(**kw):
    base = {"index": 3, "kind": "do", "text": "저장 버튼 누르기", "status": "fail", "source": "cache", "target": "", "reason": 'target not found: button "저장"',
            "jev": None, "url_after": "", "healed": False, "dialogs": [], "diff": []}
    return {**base, **kw}


def test_evidence_includes_context_and_caps_long_fields():
    diff = [f"-line {i}" for i in range(100)]
    s = step(diff=diff, jev={"confidence": 0.47, "margin": 0.05, "top3": [["abstain", 0.52]], "pool": ["x"] * 30, "candidates": 13, "visible": 12})
    state = triage.evidence(s, scenario="주문 저장", history=[step(index=i, status="pass") for i in range(10)],
                            events={"http_errors": ["500 /api"], "js_errors": []}, page={"url": "http://x/", "title": "주문"},
                            snapshot="a" * 5000, mode={"compare": True})
    assert state["failed_step"]["reason"] == 'target not found: button "저장"'
    assert state["golden_diff"] == {"lines": diff[:triage.MAX_DIFF_LINES], "total_lines": 100}
    assert "pool" not in state["jev_decision"] and state["jev_decision"]["margin"] == 0.05
    assert len(state["previous_steps"]) == triage.MAX_HISTORY and state["previous_steps"][-1]["index"] == 9
    assert len(state["snapshot_excerpt"]) == triage.MAX_SNAPSHOT_CHARS
    assert state["errors_since_last_action"]["http_errors"] == ["500 /api"]
    assert state["mode"] == {"compare": True}


def test_evidence_from_report_json_only():
    state = triage.evidence(step(), scenario="s", history=[])
    assert "page" not in state and "snapshot_excerpt" not in state and "golden_diff" not in state and "errors_since_last_action" not in state


def test_classify_parses_choice_and_nouls():
    client = FakeClient()
    t = triage.classify(client, triage.evidence(step(), scenario="s", history=[]))
    assert t.category == "ui_changed" and t.margin == 0.7 and t.retry_may_pass == 0.1 and t.likely_widespread == 0.7
    assert t.input_tokens == 900 and t.ms == 210.0 and not t.error
    assert t.to_dict()["next_action"] == triage.NEXT_ACTION["ui_changed"]
    qs = client.calls[0][1]
    assert set(qs) == {"cause", "retry_may_pass", "likely_widespread"}
    assert set(qs["cause"].criteria) == set(triage.CATEGORIES)


def test_classify_never_raises():
    t = triage.classify(FakeClient(raise_=RuntimeError("boom")), {})
    assert t.category == "abstain" and t.error == "RuntimeError: boom"
    t = triage.classify(FakeClient(cause="not_a_category"), {})
    assert t.category == "abstain" and "unknown category" in t.error
    assert "triage failed" in triage.format_line(t)


def test_needs_triage_and_summary():
    assert triage.needs_triage(step())
    assert triage.needs_triage(step(status="pass", diff=["-a", "+b"]))
    assert not triage.needs_triage(step(status="pass"))
    assert not triage.needs_triage(step(status="skip"))
    t = triage.classify(FakeClient(), {}).to_dict()
    results = [{"scenario": "A", "steps": [step(triage=t), step(index=4, status="pass")]},
               {"scenario": "B", "steps": [step(triage={**t, "category": "timing"})]}]
    counts, lines = triage.summarize(results)
    assert counts == {"ui_changed": 1, "timing": 1} and len(lines) == 2
    out = triage.format_summary(counts, lines)
    assert out.startswith("triage: ui_changed 1, timing 1") and "A / step 3" in out
    assert "(low margin: review)" in triage.format_line({**t, "margin": 0.1})
    assert triage.format_summary({}, []) == "triage: nothing to classify"


@pytest.mark.parametrize("cat", list(triage.CATEGORIES))
def test_every_category_has_next_action(cat):
    assert triage.NEXT_ACTION[cat]
