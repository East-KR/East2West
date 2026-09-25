"""통합 화면(parity ui) 오프라인 테스트: 원장 사본, API, 화면 생성, 파일 접근 제한. 저장소의 golden/legacy 를 읽기만 한다."""
import json
import shutil
import threading
from http.server import ThreadingHTTPServer
from pathlib import Path
from urllib.request import urlopen

import pytest

from parity.pwtest import hub, ledger

GOLDEN = Path("golden")


@pytest.fixture
def runs(tmp_path, monkeypatch):
    """임시 원장: 실행 두 개(JUnit·스크린샷 사본 포함), 결함 주입 결과 하나."""
    monkeypatch.setattr(ledger, "RUNS", tmp_path / "runs")
    junit = tmp_path / "junit.xml"
    junit.write_text('<testsuite name="pytest"><properties><property name="oracle_approved" value="true"/></properties>'
                     '<testcase classname="t" name="test_order_save"/></testsuite>', encoding="utf-8")
    shot = tmp_path / "test_order_save-fail.png"
    shot.write_bytes(b"\x89PNG\r\n\x1a\n")
    ledger.write_run("legacy", target="http://tobe:1", oracle={"approved_by": "east", "approved_at": "x", "ok": True},
                     cases={"test_order_save": {"status": "fail", "kind": "golden_diff", "summary": "differs", "rows": [["부가세", "120", "124"]], "screenshot": str(shot)}},
                     started=0, junit=str(junit))
    import time
    time.sleep(1.1)
    ledger.write_run("legacy", target="http://tobe:2", oracle={"approved_by": "east", "approved_at": "x", "ok": True},
                     cases={"test_order_save": {"status": "pass", "kind": "same", "summary": "", "rows": []}}, started=0, junit=str(junit))
    mut = tmp_path / "m.json"
    mut.write_text(json.dumps({"mode": "expects+golden", "oracle_approved_at": "x", "score": 1.0, "killed": 3, "total": 3, "errors": 0,
                               "generated_at": "now", "by_op": {}, "test_kills": {}, "mutants": []}), encoding="utf-8")
    ledger.save_mutation("legacy", mut)
    return tmp_path / "runs"


def test_ledger_keeps_copies(runs):
    r = ledger.load_runs("legacy")
    assert len(r) == 2
    first = r[0]
    assert Path(first["junit"]).exists() and Path(first["junit"]).parent == runs / "legacy" / first["_stamp"]
    assert Path(first["cases"]["test_order_save"]["screenshot"]).exists()
    assert ledger.mutation_for("legacy", "x") is not None and ledger.mutation_for("legacy", "other") is None


@pytest.mark.skipif(not (GOLDEN / "legacy").is_dir(), reason="golden/legacy 없음")
def test_hub_api_and_pages(runs):
    h = hub.Hub(GOLDEN, Path("e2e"))
    apps = h.apps()
    assert any(a["app"] == "legacy" and a["runs"] == 2 for a in apps)
    d = h.app("legacy")
    stamps = [r["stamp"] for r in d["runs"]]
    assert d["runs"][1]["delta"]["newly_passing"] == ["test_order_save"]
    assert d["runs"][0]["cases"]["test_order_save"]["screenshot"].startswith("/file?p=")
    assert d["mutations"] and d["mutations"][0]["current"] == (d["oracle"].get("approved_at") == "x")
    for kind in hub.PAGES:
        page = h.page("legacy", kind, stamps[0] if kind in ("map", "report") else None)
        assert "<html" in page and "legacy" in page
    assert "JUnit 사본이 없습니다" in h.page("legacy", "map", "19990101-000000")
    assert h.page("legacy", "catalog") is h.page("legacy", "catalog")  # 캐시: 산출물이 안 바뀌면 같은 객체
    # 파일 접근은 runs/ 와 golden/ 아래만
    from urllib.parse import unquote
    shot = unquote(d["runs"][0]["cases"]["test_order_save"]["screenshot"].removeprefix("/file?p="))
    assert h.file(shot) is not None
    assert h.file("../.env") is None and h.file(".env") is None and h.file("/etc/passwd") is None and h.file("") is None
    assert h.file(str(runs / "legacy" / "../../.env")) is None


@pytest.mark.skipif(not (GOLDEN / "legacy").is_dir(), reason="golden/legacy 없음")
def test_hub_http(runs):
    handler = type("H", (hub.Handler,), {"hub": hub.Hub(GOLDEN, Path("e2e"))})
    srv = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    base = f"http://127.0.0.1:{srv.server_address[1]}"
    try:
        assert b"parity" in urlopen(base + "/").read()
        apps = json.loads(urlopen(base + "/api/apps").read())
        assert apps[0]["app"]
        assert urlopen(base + "/page/legacy/catalog").status == 200
        with pytest.raises(Exception):
            urlopen(base + "/page/legacy/nope")
        with pytest.raises(Exception):
            urlopen(base + "/file?p=.env")
    finally:
        srv.shutdown()
        srv.server_close()
