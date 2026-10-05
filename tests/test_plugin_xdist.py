"""pytest-xdist 로 to-be 비교를 나눠 돌릴 때: 워커 결과를 컨트롤러가 모아 원장 하나로, testsuite 속성도 컨트롤러 JUnit에. 브라우저 없이 훅만 검사한다."""
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from _pytest.junitxml import xml_key

from east2west.pwtest import plugin


class _Xml:
    def __init__(self):
        self.props = []

    def add_global_property(self, k, v):
        self.props.append((k, v))


def _config(**opts):
    stash = pytest.Stash()
    xml = _Xml()
    stash[xml_key] = xml
    cfg = SimpleNamespace(stash=stash, getoption=lambda name, default=None: opts.get(name, default),
                          _jev_oracle={"ok": True, "approved_by": "east", "approved_at": "2026-09-28 10:00:00"})
    return cfg, xml


def test_controller_merges_worker_cases_and_adds_suite_properties_once(monkeypatch, tmp_path):
    monkeypatch.setattr(plugin, "_cases", {})
    cfg, xml = _config(**{"--base-url": "http://tobe", "--compare": tmp_path, "--jev-mutant": None})
    w1 = SimpleNamespace(config=cfg, workeroutput={"east2west_cases": {"test_a": {"status": "pass"}},
                                                   "east2west_source_hash": "abc", "east2west_source_dir": "/e2e/app"})
    w2 = SimpleNamespace(config=cfg, workeroutput={"east2west_cases": {"test_b": {"status": "fail"}},
                                                   "east2west_source_hash": "abc", "east2west_source_dir": "/e2e/app"})
    plugin.pytest_testnodedown(w1, None)
    plugin.pytest_testnodedown(w2, None)
    assert set(plugin._cases) == {"test_a", "test_b"}
    names = [k for k, _ in xml.props]
    assert names.count("source_sha256") == 1 and ("source_sha256", "abc") in xml.props and ("oracle_approved", "true") in xml.props
    assert cfg._east2west_source_dir == Path("/e2e/app")


def test_worker_without_output_is_harmless(monkeypatch):
    monkeypatch.setattr(plugin, "_cases", {})
    cfg, xml = _config()
    plugin.pytest_testnodedown(SimpleNamespace(config=cfg, workeroutput=None), None)
    assert plugin._cases == {} and xml.props == []


def test_controller_copies_worker_tobe_shots_and_removes_temp_dirs(monkeypatch, tmp_path):
    """워커가 찍은 단계별 to-be 캡처(임시 폴더, 같은 기계)를 컨트롤러가 원장 사본 폴더로 복사하고 임시 폴더를 지운다."""
    from east2west.pwtest import ledger
    monkeypatch.setattr(ledger, "RUNS", tmp_path / "runs")
    monkeypatch.setattr(plugin, "_cases", {})
    cfg, _ = _config(**{"--base-url": "http://tobe", "--compare": tmp_path / "golden" / "app", "--jev-mutant": None})
    temps = []
    for w in ("a", "b"):
        d = tmp_path / f"east2west-shots-{w}"
        d.mkdir()
        (d / "00.jpg").write_bytes(w.encode())
        temps.append(d)
        case = {"status": "pass", "kind": "same", "summary": "", "rows": [], "tobe_shots": {"0": str(d / "00.jpg")}}
        plugin.pytest_testnodedown(SimpleNamespace(config=cfg, workeroutput={"east2west_cases": {f"test_{w}": case}}), None)
    plugin.pytest_sessionfinish(SimpleNamespace(config=cfg))
    [run] = ledger.load_runs("app")
    assert Path(run["cases"]["test_b"]["tobe_shots"]["0"]).read_bytes() == b"b" and Path(run["cases"]["test_a"]["tobe_shots"]["0"]).name == "test_a-tobe-00.jpg"
    assert not any(d.exists() for d in temps)


class _Page:
    def __init__(self):
        self.went, self.context = [], SimpleNamespace(on=lambda *a, **k: None)

    def on(self, *a, **k):
        pass

    def goto(self, url):
        self.went.append(url)


def test_compare_captures_steps_except_mutants_and_redaction(tmp_path):
    """비교도 기록처럼 단계마다 캡처할 임시 폴더를 연다. 결함 주입(as-is 자신과 비교)과 가림 정규식이 있는 오라클은 찍지 않는다."""
    import shutil
    from east2west.pwtest.ui import UI
    g = tmp_path / "golden"
    g.mkdir()
    (g / "test_x.json").write_text(json.dumps({"test": "test_x", "base_url": "http://asis", "setup": {}, "steps": []}), encoding="utf-8")
    u = UI(_Page(), base_url="http://tobe", test_id="test_x", compare_dir=g)
    assert u._shots is not None and u._shots.name.startswith("east2west-shots-") and u.tobe_shots == {}
    shutil.rmtree(u._shots)
    assert UI(_Page(), base_url="http://asis", test_id="test_x", compare_dir=g, self_compare=True)._shots is None
    (g / "oracle.json").write_text(json.dumps({"ignore": [], "redact_patterns": [r"\d{6}-\d{7}"]}), encoding="utf-8")
    assert UI(_Page(), base_url="http://tobe", test_id="test_x", compare_dir=g)._shots is None
