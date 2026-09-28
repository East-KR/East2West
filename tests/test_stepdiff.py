"""재승인 diff(stepdiff.py): 지난 승인본을 해시로 검증해 찾고, 잡음(시각·주소·캡처·가린 값)을 뺀 단계별 차이만 낸다. 임시 디렉터리에서만 쓴다."""
import json
import subprocess
from pathlib import Path

import pytest

from eastshift.pwtest import ledger, oracle, review, stepdiff


def _step(i, snapshot, kind="click", text="저장", dialogs=(), api=()):
    return {"index": i, "kind": kind, "text": text, "url": "/", "title": "t", "content": [], "snapshot": snapshot,
            "dialogs": list(dialogs), "api": list(api)}


def _rec(*steps, recorded_at="2026-01-01 00:00:00", base_url="http://asis"):
    return {"test": "test_a", "base_url": base_url, "recorded_at": recorded_at, "assertions": [], "steps": list(steps)}


def _write(d: Path, data):
    (d / "test_a.json").write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")


@pytest.fixture
def app(tmp_path, monkeypatch):
    monkeypatch.setattr(ledger, "RUNS", tmp_path / "runs")
    d = tmp_path / "golden" / "app"
    d.mkdir(parents=True)
    (d / oracle.CONFIG).write_text(json.dumps({"ignore": [r"주문번호 \d+"]}), encoding="utf-8")
    _write(d, _rec(_step(0, "- text: 주문번호 1\n- text: 합계 1,200원\n", kind="goto", text="/")))
    return d


def test_noise_is_not_a_change():
    old = _rec(_step(0, "- text: 주문번호 1\n"))
    new = _rec(_step(0, "- text: 주문번호 2\n"), recorded_at="2026-02-02 00:00:00", base_url="http://other")
    new["steps"][0]["shot"] = "shots/test_a/00.jpg"
    assert stepdiff.diff(old, new, [r"주문번호 \d+"]) == []


def test_lines_action_dialog_api_and_step_count():
    old = _rec(_step(0, "- text: 합계 1,200원\n"),
               _step(1, "- text: x\n", dialogs=[{"type": "alert", "message": "저장됨", "action": "accept"}],
                     api=[{"method": "GET", "path": "/api/o", "status": 200, "body": "{}"}]),
               _step(2, "- text: y\n", text="닫기"))
    new = _rec(_step(0, "- text: 합계 1,300원\n", text="계산"),
               _step(1, "- text: x\n", dialogs=[{"type": "alert", "message": "실패", "action": "accept"}],
                     api=[{"method": "GET", "path": "/api/o", "status": 500, "body": "{}"}]))
    d = {x["index"]: x for x in stepdiff.diff(old, new, [])}
    assert d[0]["added"] == ["text: 합계 1,300원"] and d[0]["removed"] == ["text: 합계 1,200원"]
    assert d[0]["action"] == [["click", "저장"], ["click", "계산"]]
    assert d[1]["dialogs"] == [["alert: 저장됨 → accept"], ["alert: 실패 → accept"]] and d[1]["api"] == ["GET /api/o"]
    assert d[2]["gone"] and d[2]["action"] == [["click", "닫기"], None]
    assert stepdiff.diff(new, old, [])[-1]["new"]


def test_never_approved_has_no_baseline(app):
    assert stepdiff.approved_goldens(app) == {}


def test_copy_is_used_only_when_its_hash_matches(app, monkeypatch):
    oracle.stamp(app, "east", "", oracle.oracle_files(app))
    stepdiff.keep_copy(app)
    assert stepdiff.approved_goldens(app)["test_a"]["steps"][0]["text"] == "/"
    # 사본이 승인본과 다르면(손으로 고침, 다른 승인의 사본) 믿지 않는다. git 도 없으면 비교 기준이 없다
    monkeypatch.setattr(stepdiff, "_from_git_many", lambda d, wanted: {})
    (stepdiff.copy_dir("app") / "test_a.json").write_text("{}", encoding="utf-8")
    assert stepdiff.approved_goldens(app)["test_a"]["steps"][0]["text"] == "/"  # 지금 파일이 승인 때 그대로면 그것이 기준
    _write(app, _rec(_step(0, "- text: 합계 9원\n", kind="goto", text="/")))  # 다시 기록해 바뀌면 기준이 없다
    assert stepdiff.approved_goldens(app) == {}


def test_git_batch_lookup_needs_no_per_file_calls(app, monkeypatch):
    def git(*a):
        subprocess.run(["git", "-C", str(app), *a], check=True, capture_output=True)
    git("init", "-q")
    for i in range(3):  # 시나리오 셋을 같이 승인하고 셋 다 다시 기록
        (app / f"test_{i}.json").write_text(json.dumps(_rec(_step(0, f"- text: 합계 {i}원\n", kind="goto", text="/"))), encoding="utf-8")
    git("add", ".")
    git("-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "-m", "golden")
    oracle.stamp(app, "east", "", oracle.oracle_files(app))
    for i in range(3):
        (app / f"test_{i}.json").write_text(json.dumps(_rec(_step(0, f"- text: 합계 {i}00원\n", kind="goto", text="/"))), encoding="utf-8")
    calls = []
    monkeypatch.setattr(stepdiff, "_from_git", lambda d, name, want: calls.append(name))
    got = stepdiff.approved_goldens(app)
    assert {k: v["steps"][0]["snapshot"] for k, v in got.items() if k != "test_a"} == {f"test_{i}": f"- text: 합계 {i}원\n" for i in range(3)}
    assert calls == []  # 폴더 이력이 짧으면 파일별 조회는 하지 않는다


def test_git_history_is_the_fallback(app):
    def git(*a):
        subprocess.run(["git", "-C", str(app), *a], check=True, capture_output=True)
    git("init", "-q")
    git("-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "--allow-empty", "-m", "root")
    git("add", ".")
    git("-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "-m", "golden")
    oracle.stamp(app, "east", "", oracle.oracle_files(app))  # 사본 없이 승인된 옛 승인본
    _write(app, _rec(_step(0, "- text: 합계 9원\n", kind="goto", text="/")))
    git("add", ".")
    git("-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "-m", "re-record")
    assert stepdiff.approved_goldens(app)["test_a"]["steps"][0]["snapshot"].endswith("합계 1,200원\n")


def test_review_marks_screen_changes_since_approval(app):
    oracle.stamp(app, "east", "", oracle.oracle_files(app))
    stepdiff.keep_copy(app)
    assert review.build(app, app)["tests"]["test_a"]["flag"] == ""
    _write(app, _rec(_step(0, "- text: 주문번호 7\n- text: 합계 1,300원\n", kind="goto", text="/")))
    t = review.build(app, app)["tests"]["test_a"]
    assert t["flag"] == "obs" and t["gone"] == []
    delta = t["steps"][0]["delta"]
    assert delta["added"] == ["text: 합계 1,300원"] and delta["removed"] == ["text: 합계 1,200원"]  # 주문번호는 가림 규칙으로 잡음
    frag = review.fragment(review.build(app, app))
    assert "화면 바뀜" in frag["html"]
