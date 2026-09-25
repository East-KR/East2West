"""프로젝트 등록부(parity.json)와 통합 화면의 프로젝트 API. 작업 디렉터리 밖은 만지지 않는다."""
import json
import threading
from http.server import ThreadingHTTPServer
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import pytest

from parity.pwtest import hub, ledger, projects


@pytest.fixture
def ws(tmp_path, monkeypatch):
    """임시 작업 공간: 등록부 파일, 테스트 루트, 골든 루트, 소스 폴더 두 개."""
    monkeypatch.setattr(ledger, "RUNS", tmp_path / "runs")
    (tmp_path / "src-old").mkdir()
    (tmp_path / "src-new").mkdir()
    (tmp_path / "src-old" / "app.py").write_text("", encoding="utf-8")
    return {"file": tmp_path / "parity.json", "tests": tmp_path / "e2e", "golden": tmp_path / "golden", "old": tmp_path / "src-old", "new": tmp_path / "src-new"}


def spec(ws, **kw):
    return {"asis": {"src": str(ws["old"]), "url": "http://127.0.0.1:8001"}, "tobe": {"src": str(ws["new"]), "url": "http://127.0.0.1:8002"}, **kw}


def test_registry_roundtrip(ws):
    rec = projects.add("shop", spec(ws, note="메모"), path=ws["file"], tests_root=ws["tests"])
    assert rec["asis"]["url"] == "http://127.0.0.1:8001" and rec["note"] == "메모" and rec["created_at"]
    assert (ws["tests"] / "shop").is_dir()  # 시나리오 자리
    assert Path(rec["asis"]["src"]).is_absolute()  # 작업 디렉터리 밖이면 절대 경로
    assert projects.names(path=ws["file"], golden_root=ws["golden"], tests_root=ws["tests"]) == ["shop"]
    with pytest.raises(ValueError, match="이미 있는"):
        projects.add("shop", spec(ws), path=ws["file"], tests_root=ws["tests"])
    projects.update("shop", spec(ws, note="바뀜"), path=ws["file"])
    assert projects.load(ws["file"])["shop"]["note"] == "바뀜" and projects.load(ws["file"])["shop"]["created_at"] == rec["created_at"]
    projects.remove("shop", path=ws["file"])
    assert projects.load(ws["file"]) == {}
    assert (ws["tests"] / "shop").is_dir()  # 산출물은 남는다


def test_registry_validation(ws):
    bad = [("Shop", spec(ws)), ("a b", spec(ws)), ("shop", {**spec(ws), "asis": {"src": "", "url": ""}}),
           ("shop", {**spec(ws), "tobe": {"src": str(ws["new"] / "nope"), "url": ""}}), ("shop", {**spec(ws), "asis": {"src": str(ws["old"]), "url": "ftp://x"}})]
    for name, s in bad:
        with pytest.raises(ValueError):
            projects.normalize(name, s)
    assert not ws["file"].exists()


def test_names_union_and_discovery(ws):
    (ws["golden"] / "found-in-golden").mkdir(parents=True)
    (ws["tests"] / "found-in-e2e").mkdir(parents=True)
    (ws["tests"] / ".hidden").mkdir()
    (ws["tests"] / "Bad Name").mkdir()
    projects.add("shop", spec(ws), path=ws["file"], tests_root=ws["tests"])
    assert projects.names(path=ws["file"], golden_root=ws["golden"], tests_root=ws["tests"]) == ["shop", "found-in-golden", "found-in-e2e"]


def test_listdir_limits(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "a").mkdir()
    (tmp_path / "a" / "b").mkdir()
    (tmp_path / "a" / "f.txt").write_text("", encoding="utf-8")
    (tmp_path / ".git").mkdir()
    (tmp_path / "node_modules").mkdir()
    d = projects.listdir(None)
    assert [x["name"] for x in d["dirs"]] == ["a"] and d["display"] == "."
    a = projects.listdir(str(tmp_path / "a"))
    assert a["dirs"][0]["name"] == "b" and a["parent"] == str(tmp_path.resolve()) and a["display"] == "a"
    assert projects.listdir(str(tmp_path))["parent"] is None  # 작업 디렉터리 위로는 못 간다 (홈 아래가 아니면)
    with pytest.raises(PermissionError):
        projects.listdir("/")
    with pytest.raises(PermissionError):
        projects.listdir("/etc")
    with pytest.raises(FileNotFoundError):
        projects.listdir(str(tmp_path / "a" / "f.txt"))


def test_hub_projects_without_golden(ws):
    """골든이 없는 프로젝트도 목록·화면·API가 열린다 (다음 단계 안내)."""
    h = hub.Hub(ws["golden"], ws["tests"], ws["file"])
    assert h.apps() == [] and h.names() == []
    h.add_project("shop", spec(ws))
    a = h.apps()[0]
    assert a["app"] == "shop" and a["registered"] and not a["golden"] and a["scenarios"] == 0 and a["runs"] == 0
    (ws["tests"] / "shop" / "test_x.py").write_text("def test_a(): pass\n", encoding="utf-8")
    assert h.apps()[0]["scenarios"] == 1
    d = h.app("shop")
    assert d["project"]["tobe"]["url"] == "http://127.0.0.1:8002" and d["tests"] == [] and d["runs"] == []
    for kind in hub.PAGES:
        assert "골든이 아직 없습니다" in h.page("shop", kind) and "8001" in h.page("shop", kind)
    with pytest.raises(KeyError):
        h.app("nope")
    with pytest.raises(KeyError):
        h.approve("shop", by="x", code="x", fingerprint="x")  # 골든 없음
    h.update_project("shop", spec(ws, note="n"))
    assert h.apps()[0]["note"] == "n"
    assert h.remove_project("shop")["kept"] == [str(ws["tests"] / "shop")]
    assert h.apps()[0]["registered"] is False  # e2e/shop 이 남아 미등록으로 발견된다


def test_hub_projects_http(ws):
    handler = type("H", (hub.Handler,), {"hub": hub.Hub(ws["golden"], ws["tests"], ws["file"])})
    srv = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{srv.server_address[1]}"

    def post(path, body):
        return urlopen(Request(base + path, data=json.dumps(body).encode(), headers={"Content-Type": "application/json"}, method="POST"))

    try:
        assert json.loads(urlopen(base + "/api/projects").read()) == []
        res = json.loads(post("/api/projects", {"name": "shop", **spec(ws)}).read())
        assert res["ok"] and json.loads(ws["file"].read_text(encoding="utf-8"))["projects"]["shop"]["tobe"]["url"] == "http://127.0.0.1:8002"
        with pytest.raises(HTTPError) as ex:
            post("/api/projects", {"name": "shop", **spec(ws)})
        assert ex.value.code == 409 and "이미" in json.loads(ex.value.read())["error"]
        with pytest.raises(HTTPError) as ex:
            post("/api/projects/nope", spec(ws))
        assert ex.value.code == 409
        fs = json.loads(urlopen(base + "/api/fs").read())  # 경로 없음 = 작업 디렉터리
        assert fs["display"] == "." and fs["roots"][0]["name"] == "작업 디렉터리"
        for outside in ("/etc", str(ws["old"])):  # 임시 폴더도 작업 디렉터리·홈 밖이면 목록을 주지 않는다
            with pytest.raises(HTTPError) as ex:
                urlopen(base + "/api/fs?path=" + outside)
            assert ex.value.code == 403
        assert json.loads(post("/api/projects/shop/delete", {}).read())["ok"]
        assert projects.load(ws["file"]) == {}
        page = urlopen(base + "/").read().decode()
        assert "프로젝트 추가" in page and "폴더 고르기" in page
    finally:
        srv.shutdown()
        srv.server_close()


def test_init_plan_and_rules(ws):
    """화면 지도 초기화: 골든 없음 + as-is 주소 있을 때만. 시나리오가 없으면 탐색→초안→기록, 있으면 기록만. 골든이 있으면 거부."""
    h = hub.Hub(ws["golden"], ws["tests"], ws["file"])
    h.add_project("shop", {**spec(ws), "asis": {"src": str(ws["old"]), "url": ""}})
    with pytest.raises(ValueError, match="as-is 실행 주소"):
        h.init_plan("shop")
    h.update_project("shop", spec(ws))
    plan = h.init_plan("shop", depth=9)
    assert [s["step"] for s in plan] == ["crawl", "tests", "record"]
    assert plan[0]["cmd"][-6:] == ["crawl", "http://127.0.0.1:8001", "--out", "crawl/shop", "--depth", "5"]  # 깊이는 5까지
    assert plan[2]["cmd"][-8:-4] == [str(ws["tests"] / "shop"), "--base-url", "http://127.0.0.1:8001", "--record"] and plan[2]["cmd"][-4] == str(ws["golden"] / "shop")
    (ws["tests"] / "shop" / "test_x.py").write_text("def test_a(): pass\n", encoding="utf-8")
    plan = h.init_plan("shop")
    assert [s["step"] for s in plan] == ["crawl", "record"] and plan[0]["skip"] and "cmd" not in plan[0]
    (ws["golden"] / "shop").mkdir(parents=True)
    (ws["golden"] / "shop" / "test_a.json").write_text("{}", encoding="utf-8")
    with pytest.raises(ValueError, match="이미"):
        h.init_plan("shop")
    assert h.init_status("shop") == {"running": False}
    with pytest.raises(KeyError):
        h.init_plan("nope")


def test_init_job_runs_steps(ws):
    """작업 실행기: 명령 단계는 하위 프로세스로, 복사 단계는 파일 복사로. 실패하면 error에 남고 running이 풀린다."""
    import sys
    import time
    h = hub.Hub(ws["golden"], ws["tests"], ws["file"])
    h.add_project("shop", spec(ws))
    fake = [{"step": "crawl", "label": "탐색", "cmd": [sys.executable, "-c", "print('crawled')"]},
            {"step": "tests", "label": "초안", "copy": [str(ws["old"] / "app.py"), str(ws["tests"] / "shop" / "test_crawl.py")]},
            {"step": "record", "label": "기록", "cmd": [sys.executable, "-c", "import sys; print('boom'); sys.exit(2)"]}]
    h.init_plan = lambda app, depth=3: fake  # 실제 탐색·기록 대신
    st = h.init_project("shop")
    assert st["running"]
    for _ in range(100):
        if not h.init_status("shop")["running"]:
            break
        time.sleep(0.1)
    st = h.init_status("shop")
    assert not st["running"] and not st["ok"] and "record 실패" in st["error"]
    assert [s["state"] for s in st["steps"]] == ["done", "done", "fail"]
    assert (ws["tests"] / "shop" / "test_crawl.py").exists() and "crawled" in "\n".join(st["log"]) and "boom" in "\n".join(st["log"])


def test_init_http(ws):
    handler = type("H", (hub.Handler,), {"hub": hub.Hub(ws["golden"], ws["tests"], ws["file"])})
    srv = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{srv.server_address[1]}"
    try:
        urlopen(Request(base + "/api/projects", data=json.dumps({"name": "shop", **spec(ws), "asis": {"src": str(ws["old"]), "url": ""}}).encode(), headers={"Content-Type": "application/json"}, method="POST"))
        assert json.loads(urlopen(base + "/api/app/shop/init").read()) == {"running": False}
        with pytest.raises(HTTPError) as ex:
            urlopen(Request(base + "/api/app/shop/init", data=b"{}", headers={"Content-Type": "application/json"}, method="POST"))
        assert ex.value.code == 409 and "as-is" in json.loads(ex.value.read())["error"]
        with pytest.raises(HTTPError) as ex:
            urlopen(base + "/api/app/nope/init")
        assert ex.value.code == 404
    finally:
        srv.shutdown()
        srv.server_close()
