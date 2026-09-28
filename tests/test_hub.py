"""통합 화면(eastshift ui) 오프라인 테스트: 원장 사본, API, 화면 생성, 파일 접근 제한. 저장소의 golden/legacy 를 읽기만 한다."""
import json
import os
import re
import shutil
import threading
from http.server import ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote
from urllib.request import urlopen

import pytest

from eastshift.pwtest import html, hub, ledger

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
        frag = h.page("legacy", kind, stamps[0] if kind in ("map", "report") else None)
        assert frag["kind"] == kind and "legacy" in frag["html"] and "<main>" in frag["html"]  # iframe 없이 끼우는 조각
        if frag["css"]:
            assert f".pg-{kind} " in frag["css"] and "\nbody" not in frag["css"]  # 조각 CSS 는 컨테이너 안으로 가둬진다
        assert "<html" in html.assemble(frag) and f"class='pg pg-{kind}'" in html.assemble(frag)
    assert "JUnit 사본이 없습니다" in h.page("legacy", "map", "19990101-000000")["html"]
    assert h.page("legacy", "catalog") is h.page("legacy", "catalog")  # 캐시: 산출물이 안 바뀌면 같은 객체
    # 파일 접근은 runs/ 와 golden/ 아래만
    shot = unquote(d["runs"][0]["cases"]["test_order_save"]["screenshot"].removeprefix("/file?p="))
    assert h.file(shot) is not None
    assert h.file("../.env") is None and h.file(".env") is None and h.file("/etc/passwd") is None and h.file("") is None
    assert h.file(str(runs / "legacy" / "../../.env")) is None


@pytest.mark.skipif(not (GOLDEN / "legacy").is_dir(), reason="golden/legacy 없음")
def test_report_fix_plans(runs):
    """보고서의 '바로 해결': to-be 비교 다시(pytest --compare, exit 1 도 정상), 결함 탐지 측정(mutate --compare). 주소가 없으면 거부."""
    h = hub.Hub(GOLDEN, Path("e2e"))
    plan = h.compare_plan("legacy")
    assert plan[0]["cmd"][-9:-2] == ["--base-url", "http://127.0.0.1:8802", "--compare", "golden/legacy", "--junitxml", "reports/junit-legacy.xml", "-q"] and plan[0]["ok_exit"] == (0, 1)
    plan = h.mutate_plan("legacy")
    assert "mutate" in plan[0]["cmd"] and plan[0]["cmd"][-6:] == ["--base-url", "http://127.0.0.1:8801", "--compare", "golden/legacy", "--max-per-op", "100"]
    with pytest.raises(ValueError, match="to-be 실행 주소"):
        h.compare_plan("reservation")
    frag = h.page("legacy", "report", runs and None)
    assert "data-fix=" in frag["html"] and "fetch(`/api/app/" in frag["js"]  # 빨간 항목 옆 버튼과 실행 스크립트


@pytest.mark.skipif(not (GOLDEN / "legacy").is_dir(), reason="golden/legacy 없음")
def test_web_approval_rules(tmp_path, runs):
    """웹 승인: 검토 화면에서 직접 승인하고, 바뀐 기준과 빈 이름은 거부한다."""
    from eastshift.pwtest import oracle
    root = tmp_path / "g"
    shutil.copytree(GOLDEN / "legacy", root / "legacy")
    (root / "legacy" / oracle.MANIFEST).unlink()
    h = hub.Hub(root, Path("e2e"))
    fp = oracle.fingerprint(root / "legacy")
    frag = h.page("legacy", "review")
    assert 'name="code"' not in frag["html"] + frag["js"] and 'name="by"' in frag["js"]  # 승인 폼은 이름만 받는다 (터미널 코드 없음)
    with pytest.raises(ValueError):
        h.approve("legacy", by="east", fingerprint="stale")
    with pytest.raises(ValueError):
        h.approve("legacy", by="  ", fingerprint=fp)
    res = h.approve("legacy", by="east", fingerprint=fp)
    assert res["ok"] and oracle.status(root / "legacy")["ok"]


@pytest.mark.skipif(not (GOLDEN / "legacy").is_dir(), reason="golden/legacy 없음")
def test_web_approval_http_without_terminal(tmp_path, monkeypatch):
    monkeypatch.setattr(ledger, "RUNS", tmp_path / "runs")  # 승인하면 승인본 사본을 원장 옆에 남긴다
    from urllib.request import Request
    from eastshift.pwtest import oracle
    root = tmp_path / "golden"
    shutil.copytree(GOLDEN / "legacy", root / "legacy")
    (root / "legacy" / oracle.MANIFEST).unlink()
    handler = type("H", (hub.Handler,), {"hub": hub.Hub(root, Path("e2e"))})
    srv = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=srv.serve_forever, daemon=True)
    thread.start()
    try:
        base = f"http://127.0.0.1:{srv.server_address[1]}"
        page = urlopen(base + "/page/legacy/review").read().decode()
        assert 'name="by"' in page and 'name="code"' not in page
        body = json.dumps({"by": "browser reviewer", "fingerprint": oracle.fingerprint(root / "legacy")}).encode()
        response = urlopen(Request(base + "/api/app/legacy/approve", data=body,
                                   headers={"Content-Type": "application/json", "Origin": base}, method="POST"))
        assert json.loads(response.read())["ok"]
        assert oracle.status(root / "legacy")["ok"]
    finally:
        srv.shutdown()
        srv.server_close()


@pytest.mark.skipif(not (GOLDEN / "legacy").is_dir(), reason="golden/legacy 없음")
def test_hub_http(runs):
    handler = type("H", (hub.Handler,), {"hub": hub.Hub(GOLDEN, Path("e2e"))})
    srv = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    base = f"http://127.0.0.1:{srv.server_address[1]}"
    try:
        assert b"EastShift" in urlopen(base + "/").read()
        apps = json.loads(urlopen(base + "/api/apps").read())
        assert apps[0]["app"]
        assert urlopen(base + "/page/legacy/catalog").status == 200
        frag = json.loads(urlopen(base + "/page/legacy/catalog?fragment=1").read())  # 통합 화면이 끼우는 조각
        assert frag["kind"] == "catalog" and "<main>" in frag["html"] and "root.querySelector" in frag["js"]
        with pytest.raises(Exception):
            urlopen(base + "/page/legacy/nope")
        with pytest.raises(Exception):
            urlopen(base + "/file?p=.env")
        from urllib.request import Request
        from urllib.error import HTTPError
        req = Request(base + "/api/app/legacy/approve", data=json.dumps({"by": "x", "code": "x", "fingerprint": "x"}).encode(), headers={"Content-Type": "application/json"}, method="POST")
        with pytest.raises(HTTPError) as ex:
            urlopen(req)
        assert ex.value.code == 409  # 오래된 검토 지문은 거부
        # 같은 브라우저에 열린 다른 사이트가 쏘는 POST 는 Origin 으로 거른다. 서버 자신의 Origin 과 Origin 없는 요청(curl)은 통과
        body = json.dumps({}).encode()
        for origin, code in (("http://evil.example", 403), ("http://127.0.0.1:9", 403), (base, 409), (None, 409)):
            headers = {"Content-Type": "application/json", **({"Origin": origin} if origin else {})}
            with pytest.raises(HTTPError) as ex:
                urlopen(Request(base + "/api/projects/no-such-app/delete", data=body, headers=headers, method="POST"))
            assert ex.value.code == code, origin
    finally:
        srv.shutdown()
        srv.server_close()


@pytest.mark.skipif(not (GOLDEN / "portal").is_dir(), reason="golden/portal 없음")
def test_pages_link_captures_instead_of_inlining(tmp_path, monkeypatch):
    # 화면이 수천 개여도 페이지가 가볍도록: 목록 화면에는 캡처가 없고, 시나리오 상세 API가 /file 주소를 준다. 그 주소는 hub.file 이 내준다
    monkeypatch.setattr(ledger, "RUNS", tmp_path / "runs")
    h = hub.Hub(GOLDEN, Path("e2e"))
    for kind in ("review", "catalog"):
        blob = json.dumps(h.page("portal", kind), ensure_ascii=False)
        assert "data:image" not in blob and "/file?p=" not in blob and "snapshot" not in blob
        name = next(iter(h.build("portal", kind)["tests"]))
        dt = h.detail("portal", kind, name)
        assert dt["test"]["steps"] and dt["shots"] and all(v.startswith("/file?p=") for v in dt["shots"].values())
        assert h.file(unquote(next(iter(dt["shots"].values())).removeprefix("/file?p="))) is not None
        with pytest.raises(KeyError):
            h.detail("portal", kind, "no_such_test")
    blob = json.dumps(h.page("portal", "map"), ensure_ascii=False)
    assert "data:image" not in blob and "/file?p=" in blob


def test_sig_sees_top_level_and_subfolder_changes(tmp_path, monkeypatch):
    monkeypatch.setattr(ledger, "RUNS", tmp_path / "runs")
    g = tmp_path / "golden"
    (g / "app" / "shots" / "t1").mkdir(parents=True)
    (g / "app" / "t1.json").write_text("{}", encoding="utf-8")
    h = hub.Hub(g, tmp_path / "e2e")
    s0 = h._sig("app")
    os.utime(g / "app" / "t1.json", (s0 + 10, s0 + 10))
    s1 = h._sig("app")
    assert s1 > s0
    (g / "app" / "shots" / "t1" / "step1.jpg").write_bytes(b"x")  # 바로 아래 폴더(shots/)의 항목은 본다
    os.utime(g / "app" / "shots" / "t1", (s1 + 10, s1 + 10))
    assert h._sig("app") > s1
