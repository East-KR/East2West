"""실행 원장 오프라인 테스트: 기록·이력·지난 실행 대비 변화·상태 문구."""
import os
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


def test_fscache_rereads_only_when_file_changes(tmp_path):
    from eastshift.pwtest import fscache
    p = tmp_path / "a.json"
    p.write_text('{"v": 1}', encoding="utf-8")
    assert fscache.json_load(p) == {"v": 1} and fscache.json_load(p) is fscache.json_load(p)
    h1 = fscache.sha256(p)
    p.write_text('{"v": 2}', encoding="utf-8")  # 크기 같음: mtime_ns 로 가린다
    os.utime(p, ns=(p.stat().st_atime_ns, p.stat().st_mtime_ns + 1_000_000))
    assert fscache.json_load(p) == {"v": 2} and fscache.sha256(p) != h1


def test_prune_keeps_recent_artifact_folders_and_all_ledger_json(tmp_path, monkeypatch):
    monkeypatch.setattr(ledger, "RUNS", tmp_path / "runs")
    monkeypatch.setenv("EASTSHIFT_KEEP_RUNS", "2")
    junit = tmp_path / "j.xml"
    junit.write_text("<testsuite/>", encoding="utf-8")
    stamps = []
    for i in range(4):
        out = ledger.write_run("app", target="t", oracle={}, cases=_cases(a="pass"), started=0, junit=str(junit))
        stamps.append(out.stem)
        os.utime(out, ns=(0, (i + 1) * 10**9))  # 같은 초에 만들어도 순서가 있게 이름은 이미 -2, -3 …
    runs = ledger.load_runs("app")
    assert [r["_stamp"] for r in runs] == stamps  # 원장 JSON은 넷 다 남는다
    kept = [s for s in stamps if (tmp_path / "runs" / "app" / s).is_dir()]
    assert kept == stamps[-2:]  # 사본 폴더는 최근 둘만
    monkeypatch.setenv("EASTSHIFT_KEEP_RUNS", "0")
    assert ledger.prune("app") == []  # 0이면 정리하지 않는다
