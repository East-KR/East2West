"""pytest-xdist 로 to-be 비교를 나눠 돌릴 때: 워커 결과를 컨트롤러가 모아 원장 하나로, testsuite 속성도 컨트롤러 JUnit에. 브라우저 없이 훅만 검사한다."""
from pathlib import Path
from types import SimpleNamespace

import pytest
from _pytest.junitxml import xml_key

from eastshift.pwtest import plugin


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
    w1 = SimpleNamespace(config=cfg, workeroutput={"eastshift_cases": {"test_a": {"status": "pass"}},
                                                   "eastshift_source_hash": "abc", "eastshift_source_dir": "/e2e/app"})
    w2 = SimpleNamespace(config=cfg, workeroutput={"eastshift_cases": {"test_b": {"status": "fail"}},
                                                   "eastshift_source_hash": "abc", "eastshift_source_dir": "/e2e/app"})
    plugin.pytest_testnodedown(w1, None)
    plugin.pytest_testnodedown(w2, None)
    assert set(plugin._cases) == {"test_a", "test_b"}
    names = [k for k, _ in xml.props]
    assert names.count("source_sha256") == 1 and ("source_sha256", "abc") in xml.props and ("oracle_approved", "true") in xml.props
    assert cfg._eastshift_source_dir == Path("/e2e/app")


def test_worker_without_output_is_harmless(monkeypatch):
    monkeypatch.setattr(plugin, "_cases", {})
    cfg, xml = _config()
    plugin.pytest_testnodedown(SimpleNamespace(config=cfg, workeroutput=None), None)
    assert plugin._cases == {} and xml.props == []
