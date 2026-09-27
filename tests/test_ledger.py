"""실행 원장 오프라인 테스트: 기록·이력·지난 실행 대비 변화·상태 문구."""
from pathlib import Path

from eastshift.pwtest import ledger


def _cases(**status):
    return {n: {"status": s, "kind": "same" if s == "pass" else "golden_diff", "summary": "" if s == "pass" else f"{n} differs", "rows": []}
            for n, s in status.items()}


def test_write_history_and_delta(tmp_path, monkeypatch):
    monkeypatch.setattr(ledger, "RUNS", tmp_path)
    oracle = {"approved_by": "east", "approved_at": "2026-09-24 18:10:42", "ok": True}
    ledger.write_run("app", target="http://tobe", oracle=oracle, cases=_cases(a="fail", b="pass"), started=0)
    ledger.write_run("app", target="http://tobe", oracle=oracle, cases=_cases(a="pass", b="fail", c="pass"), started=0)
    runs = ledger.load_runs("app")
    assert len(runs) == 2 and runs[-1]["totals"] == {"pass": 2, "fail": 1}
    delta = ledger.compare_runs(runs[-1], runs[-2])
    assert delta == {"newly_passing": ["a"], "newly_failing": ["b"], "still_failing": [], "new_tests": ["c"], "gone_tests": []}
    hist = ledger.history("app")
    assert [h["status"] for h in hist["a"]] == ["fail", "pass"] and hist["c"][0]["approved_at"] == oracle["approved_at"]
    text = ledger.status_text("app", oracle)
    assert "남은 실패 1" in text and "통과로 바뀜 1" in text and "새로 실패 1" in text and "b (새로)" in text


def test_status_warns_on_stale_approval(tmp_path, monkeypatch):
    monkeypatch.setattr(ledger, "RUNS", tmp_path)
    ledger.write_run("app", target="http://tobe", oracle={"approved_at": "old", "ok": True}, cases=_cases(a="pass"), started=0)
    text = ledger.status_text("app", {"approved_at": "new", "ok": True})
    assert "다른 승인본으로 실행" in text
    assert "실행 기록이 없습니다" in ledger.status_text("none", {"ok": True})


def test_same_second_runs_do_not_overwrite(tmp_path, monkeypatch):
    """같은 초에 끝난 실행 셋: 파일 셋이 따로 남고, 끝난 순서대로 읽힌다 (-2 가 원본 뒤, -10 이 -9 뒤)."""
    monkeypatch.setattr(ledger, "RUNS", tmp_path)
    monkeypatch.setattr(ledger.time, "strftime", lambda fmt, *a: "20260927-120000" if fmt == "%Y%m%d-%H%M%S" else "2026-09-27 12:00:00")
    for i in range(11):
        ledger.write_run("app", target=f"http://tobe/{i}", oracle={"ok": True}, cases=_cases(a="pass"), started=0)
    runs = ledger.load_runs("app")
    assert [r["target"] for r in runs] == [f"http://tobe/{i}" for i in range(11)]
    assert runs[1]["_stamp"] == "20260927-120000-2" and runs[-1]["_stamp"] == "20260927-120000-11"
    m = tmp_path / "m.json"
    m.write_text("{}")
    assert ledger.save_mutation("app", m) != ledger.save_mutation("app", m)
