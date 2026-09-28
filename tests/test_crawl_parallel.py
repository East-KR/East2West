"""eastshift crawl 병렬 탐색: 브라우저 여러 개가 나눠 눌러도 그래프(상태·edge id·도착·종류)가 한 줄로 누를 때와 같다.
채우기(filled), confirm 취소(dismiss), 목록 대표·적응 확장이 모두 한 번씩 나오는 작은 앱으로 확인한다."""
import json
import threading
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import pytest

from eastshift import crawl
from eastshift.crawl import Crawler, _workers

STATUS = ["접수", "취소"]


def page(body: str) -> bytes:
    return f"<!doctype html><html lang='ko'><head><meta charset='utf-8'><title>t</title></head><body><main>{body}</main></body></html>".encode()


class App(BaseHTTPRequestHandler):
    def log_message(self, *_):
        pass

    def do_GET(self):
        u = urlparse(self.path)
        if u.path == "/":
            rows = "".join(f"<tr><td><a href='/orders/{i}'>ORD-{i}</a></td><td>{STATUS[i % 2]}</td></tr>" for i in range(1, 9))
            body = ("<h1>홈</h1><form action='/search'><label>이름 <input name='q'></label><button>조회</button></form>"
                    "<button onclick=\"if (confirm('저장할까요?')) location='/saved'\">저장</button>"
                    f"<table><tr><th>주문</th><th>상태</th></tr>{rows}</table><a href='/help'>도움말</a>")
        elif u.path == "/search":
            q = parse_qs(u.query).get("q", [""])[0]
            body = f"<h1>검색 결과</h1><p>{q} 님의 주문</p>" if q else "<h1>검색어 없음</h1>"
        elif u.path.startswith("/orders/"):
            i = int(u.path.rsplit("/", 1)[1])
            body = f"<h1>주문 상세</h1><p>{STATUS[i % 2]}</p>" + ("<h2>긴급 처리</h2>" if i % 3 == 0 else "")
        elif u.path in ("/help", "/saved"):
            body = f"<h1>{'도움말' if u.path == '/help' else '저장됨'}</h1>"
        elif u.path == "/hidden":  # 어디서도 링크하지 않는 화면: 씨앗(--seeds)으로만 간다
            body = "<h1>숨은 화면</h1><a href='/hidden/next'>다음</a>"
        elif u.path == "/hidden/next":
            body = "<h1>숨은 화면 다음</h1>"
        else:
            self.send_response(HTTPStatus.NOT_FOUND); self.end_headers(); return
        b = page(body)
        self.send_response(HTTPStatus.OK); self.send_header("Content-Type", "text/html; charset=utf-8"); self.send_header("Content-Length", str(len(b))); self.end_headers(); self.wfile.write(b)


@pytest.fixture(scope="module")
def base():
    srv = ThreadingHTTPServer(("127.0.0.1", 0), App)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{srv.server_address[1]}"
    srv.shutdown()


def _graph(c: Crawler):
    nodes = [(n.sig, n.filled, n.url, n.path, n.explored) for n in c.nodes]
    edges = [(e.id, e.src, e.dst, e.kind, e.mode, e.action.el.name, e.group, e.template, e.row, [d["message"] for d in e.dialogs], e.reason) for e in c.edges]
    return nodes, edges


def test_parallel_graph_matches_serial(base, tmp_path):
    graphs = []
    for w in (1, 4):
        c = Crawler(base + "/", max_depth=2, settle_ms=50, reps=3, inputs={"이름": "홍길동"}, workers=w)
        c.run(shots_dir=tmp_path / f"w{w}")
        graphs.append(_graph(c))
        assert all(n.screenshot and (tmp_path / f"w{w}" / f"n{n.id}.png").exists() for n in c.nodes)
    assert graphs[0] == graphs[1]
    modes = {e[4] for e in graphs[0][1]}
    assert {"as-is", "filled", "dismiss"} <= modes, modes                     # 채워서·취소 경로가 실제로 나왔다
    assert any("적응 확장" in e[10] for e in graphs[0][1])                     # 목록 적응 확장도


def test_worker_default(monkeypatch, tmp_path):
    monkeypatch.delenv("EASTSHIFT_CRAWL_WORKERS", raising=False)
    assert _workers(None) == 4 and _workers(tmp_path / "state.json") == 1   # 로그인 세션을 나눠 쓰면 한 줄로
    monkeypatch.setenv("EASTSHIFT_CRAWL_WORKERS", "2")
    assert _workers(tmp_path / "state.json") == 2


def test_resume_after_interrupt_matches_uninterrupted(base, tmp_path, monkeypatch):
    """상태를 몇 개 누른 뒤 끊겨도, 같은 설정으로 다시 돌리면 이어 가서 한 번에 끝낸 것과 같은 그래프가 된다. 끝나면 기록을 지운다."""
    opts = dict(max_depth=2, settle_ms=50, reps=3, inputs={"이름": "홍길동"}, workers=2)
    whole = Crawler(base + "/", **opts)
    whole.run(shots_dir=tmp_path / "a")

    ck = tmp_path / "b" / ".crawl-partial.jsonl"
    ck.parent.mkdir()
    real, calls = Crawler._explore, []

    def flaky(self, node):
        if len(calls) == 2:
            raise RuntimeError("끊김")
        calls.append(node.id)
        real(self, node)

    monkeypatch.setattr(Crawler, "_explore", flaky)
    with pytest.raises(RuntimeError):
        Crawler(base + "/", **opts).run(shots_dir=tmp_path / "b" / "screens", checkpoint=ck)
    assert ck.exists()
    with ck.open("a", encoding="utf-8") as f:
        f.write('{"next": 9, "nodes": [')  # 쓰다 끊긴 줄
    resumed: list[int] = []
    monkeypatch.setattr(Crawler, "_explore", lambda self, node: (resumed.append(node.id), real(self, node)))

    again = Crawler(base + "/", **opts)
    again.run(shots_dir=tmp_path / "b" / "screens", checkpoint=ck)
    assert resumed and not set(resumed) & set(calls)  # 끊기기 전에 누른 상태는 다시 누르지 않았다
    assert _graph(again) == _graph(whole)
    assert [e.steps for e in again.edges] == [e.steps for e in whole.edges]
    assert not ck.exists()

    other = Crawler(base + "/", **{**opts, "max_depth": 1})  # 설정이 다르면 지난 기록을 쓰지 않는다
    ck.write_text('{"key": "old"}\n', encoding="utf-8")
    other.run(shots_dir=tmp_path / "c", checkpoint=ck)
    assert all(len(n.path) <= 1 for n in other.nodes)


def test_graph_md_diagram_falls_back_to_routes_then_none():
    """mermaid 한도(선 500개)를 넘으면 주소 단위로 합쳐 그리고, 그래도 넘으면 그림을 빼고 Screen Map을 가리킨다. 동작 표는 그대로."""
    from eastshift.crawl import MERMAID_EDGES, Edge, Node, Step
    from eastshift.snapshot import Element

    def crawler(n_routes: int, per_route: int) -> Crawler:
        c = Crawler("http://127.0.0.1:1/")
        for i in range(n_routes * per_route):
            name = "".join("abcdefghij"[int(d)] for d in str(i // per_route))  # 주소 조각에 숫자가 있으면 stable_path가 자른다
            c.nodes.append(Node(i, f"s{i}", False, f"http://127.0.0.1:1/{name}/{i}", "", f"화면 {i}", []))
        for i in range(1, len(c.nodes)):
            c.edges.append(Edge(len(c.edges), i - 1, [Step(Element("link", f"다음 {i}", 0), "click")], "as-is", "transition", dst=i))
        return c

    small = crawler(3, 2).markdown()
    assert "flowchart TD" in small and "n0 -->" in small
    grouped = crawler(20, 40).markdown()  # 전이 799개, 주소 20개
    assert "flowchart LR" in grouped and "r0 -->" in grouped and "주소 단위로 합쳤다" in grouped
    none = crawler(MERMAID_EDGES + 50, 2).markdown()  # 주소끼리 선만 500개 넘음
    assert "```mermaid" not in none and "흐름도 생략" in none and "Screen Map" in none
    assert none.count("| n") >= MERMAID_EDGES  # 동작 표는 남는다


@pytest.mark.parametrize("small,big", [
    (dict(max_depth=2, max_states=4), dict(max_depth=2, max_states=9)),      # 상태 상한
    (dict(max_depth=2, max_actions=3), dict(max_depth=2, max_actions=40)),   # 화면당 동작 상한
    (dict(max_depth=1), dict(max_depth=2)),                                   # 깊이
], ids=["states", "actions", "depth"])
def test_click_memo_widening_clicks_only_new_actions(base, tmp_path, monkeypatch, small, big):
    """상한을 올려 다시 탐색하면 새로 생긴 동작만 실제로 누르고, 결과는 큰 상한으로 한 번에 돌린 것과 같다 (eastshift.clicks)."""
    real, clicked = Crawler._attempt, []
    monkeypatch.setattr(Crawler, "_attempt", lambda self, *a, **k: (clicked.append(1), real(self, *a, **k))[1])
    opts = dict(settle_ms=50, reps=3, inputs={"이름": "홍길동"}, workers=2)
    memo = tmp_path / "cache" / "clicks.jsonl"

    def crawl(limits, out, **kw):
        clicked.clear()
        c = Crawler(base + "/", **opts, **limits)
        c.run(shots_dir=tmp_path / out, **kw)
        return c, len(clicked)

    _, n_small = crawl(small, "small", clicks=memo)
    wide, n_incr = crawl(big, "wide", clicks=memo)
    whole, n_whole = crawl(big, "whole")
    assert n_incr == n_whole - n_small and n_incr < n_whole
    assert _graph(wide) == _graph(whole) and [e.steps for e in wide.edges] == [e.steps for e in whole.edges]
    strip = lambda g: {**g, "nodes": [{**n, "screenshot": ""} for n in g["nodes"]]}
    assert strip(wide.to_dict()) == strip(whole.to_dict())
    assert all(n.screenshot and Path(n.screenshot).exists() for n in wide.nodes)    # 기억에서 만든 상태도 캡처가 있다
    assert wide.memo.hits > 0


def test_click_memo_is_dropped_when_source_or_start_screen_changes(base, tmp_path):
    from eastshift.clicks import ClickMemo
    memo = tmp_path / "clicks.jsonl"
    opts = dict(max_depth=1, settle_ms=50, workers=2)
    Crawler(base + "/", **opts).run(clicks=memo, sources={"tobe-src": "git:aaa"})
    again = Crawler(base + "/", **opts)
    again.run(clicks=memo, sources={"tobe-src": "git:aaa"})
    assert again.memo.misses == 0 and again.memo.hits > 0                       # 그대로면 전부 기억에서
    moved = Crawler(base + "/", **opts)
    moved.run(clicks=memo, sources={"tobe-src": "git:bbb"})                    # to-be 커밋이 바뀜
    assert "기준" in moved.memo.reason and moved.memo.hits == 0

    key = json.loads(memo.read_text(encoding="utf-8").splitlines()[0])["key"]
    m = ClickMemo(memo, key)
    m.put(["root", base + "/"], {"sig": "different"})                          # 시작 화면 구조가 바뀐 것처럼
    m.close()
    changed = Crawler(base + "/", **opts)
    changed.run(clicks=memo, sources={"tobe-src": "git:bbb"})
    assert changed.memo.reason.startswith("시작 화면") and changed.memo.hits == 0


def test_click_memo_file_format(tmp_path):
    from eastshift.clicks import ClickMemo
    p = tmp_path / "clicks.jsonl"
    m = ClickMemo(p, {"v": 1})
    assert m.reason == "처음 탐색"
    snap = "- heading \"주문\" [level=1]\n" * 50
    m.put(["a"], {"kind": "transition", "obs": {"sig": "s1", "snapshot": snap}, "shot": b"\x89PNG..."})
    m.put(["b"], {"kind": "transition", "obs": {"sig": "s1", "snapshot": snap}, "shot": None})
    m.put(["look"], {"sig": "s1", "snapshot": snap, "elements": []})
    m.close()
    assert p.read_text(encoding="utf-8").count(json.dumps(snap, ensure_ascii=False)) == 1   # 같은 스냅샷은 한 번만
    with p.open("a", encoding="utf-8") as f:
        f.write('{"k":"0123')                                                   # 쓰다 끊긴 줄
    m = ClickMemo(p, {"v": 1})
    a = m.get(["a"])
    assert m.reason == "" and a["obs"]["snapshot"] == snap and m.shot(a["shot"]) == b"\x89PNG..."
    assert m.get(["look"])["snapshot"] == snap and m.get(["zzz"]) is None
    m.put(["c"], {"kind": "local", "obs": None, "shot": None})                 # 끊긴 줄을 걷어 내고 이어 쓴다
    m.close()
    m = ClickMemo(p, {"v": 1})
    assert m.get(["c"])["kind"] == "local" and len(m.index) == 4
    m.close()
    assert ClickMemo(p, {"v": 2}).reason.startswith("기준")                     # 기준이 다르면 비운다


def test_sources_for_follows_registry_and_git_state(tmp_path):
    """소스 버전: 그 폴더의 커밋된 내용과 커밋 안 된 변경. 같은 저장소의 다른 폴더 커밋에는 바뀌지 않는다."""
    import subprocess
    from eastshift.clicks import source_version, sources_for
    repo = tmp_path / "repo"
    src = repo / "tobe"
    src.mkdir(parents=True)
    (repo / "docs").mkdir()
    (src / "app.py").write_text("print(1)\n", encoding="utf-8")
    (repo / "docs" / "note.md").write_text("x\n", encoding="utf-8")
    g = lambda *a: subprocess.run(["git", "-C", str(repo), "-c", "user.name=t", "-c", "user.email=t@t", *a], check=True, capture_output=True)
    g("init", "-q"); g("add", "-A"); g("commit", "-qm", "1")
    reg = tmp_path / "eastshift.json"
    reg.write_text(json.dumps({"projects": {"shop": {"tobe": {"src": str(src), "url": "http://127.0.0.1:8821"}}}}), encoding="utf-8")
    assert sources_for("http://127.0.0.1:9999/", reg) == {}                     # 등록부에 없는 주소
    v1 = sources_for("http://127.0.0.1:8821/login", reg)[str(src)]
    assert v1.startswith("git:")
    (repo / "docs" / "note.md").write_text("y\n", encoding="utf-8")
    g("commit", "-qam", "docs")
    assert source_version(src) == v1                                          # 다른 폴더의 커밋
    (src / "app.py").write_text("print(2)\n", encoding="utf-8")                # 커밋 안 된 변경 (개발 서버는 작업 폴더를 띄운다)
    v2 = source_version(src)
    g("commit", "-qam", "2")
    v3 = source_version(src)
    assert len({v1, v2, v3}) == 3 and source_version(src) == v3
    one = src / "app.py"                                                      # 소스가 파일 하나인 등록 (portal 데모처럼)
    f1 = source_version(one)
    (repo / "docs" / "note.md").write_text("z\n", encoding="utf-8")
    g("commit", "-qam", "docs 2")
    assert source_version(one) == f1
    one.write_text("print(3)\n", encoding="utf-8")
    assert source_version(one) != f1


def test_click_memo_retries_an_error_once_then_keeps_it(base, tmp_path, monkeypatch):
    """오류로 끝난 동작은 다음 탐색에서 한 번 더 누르고, 두 번 연속 오류면 그 뒤로는 누르지 않는다 (늘 가려진 버튼에 매번 제한 시간을 쓰지 않게)."""
    real, pressed = Crawler._attempt, []

    def covered(self, s, node, steps, mode, **kw):
        r = real(self, s, node, steps, mode, **kw)
        if steps[-1].el.name == "도움말":
            pressed.append(mode)
            r.update(kind="error", reason="Locator.click: Timeout 3000ms exceeded.", obs=None, shot=None)
        return r

    monkeypatch.setattr(Crawler, "_attempt", covered)
    memo = tmp_path / "clicks.jsonl"
    counts, graphs = [], []
    for _ in range(3):
        pressed.clear()
        c = Crawler(base + "/", max_depth=1, settle_ms=50, inputs={"이름": "홍길동"}, workers=2)
        c.run(clicks=memo)
        counts.append(len(pressed))
        graphs.append(_graph(c))
    assert counts[0] == counts[1] > 0 and counts[2] == 0
    assert graphs[0] == graphs[1] == graphs[2]
    assert any(e[3] == "error" and e[5] == "도움말" for e in graphs[2][1])


def test_route_state_budget_keeps_breadth(base, tmp_path):
    """라우트마다 상태 상한: 주문 상세(/orders/{id})의 변형은 하나만 두고, 다른 화면으로 가는 탐색은 계속한다."""
    opts = dict(max_depth=2, settle_ms=50, reps=3, inputs={"이름": "홍길동"}, workers=2)
    free = Crawler(base + "/", **opts)
    free.run()
    capped = Crawler(base + "/", **opts, max_route_states=1)
    capped.run()
    routes = lambda c: sorted({crawl.route_of(n.loc) for n in c.nodes})
    assert routes(capped) == routes(free)                                        # 닿은 화면은 같다
    assert sum(crawl.route_of(n.loc) == "/orders/{id}" for n in free.nodes) > 1
    assert sum(crawl.route_of(n.loc) == "/orders/{id}" for n in capped.nodes) == 1
    assert any(e.reason == "route state budget 1 reached (/orders/{id})" for e in capped.edges)
    assert "라우트 상한(1)에 걸린 화면" in capped.markdown()


def test_seeds_open_screens_that_clicking_never_reaches(base, tmp_path, monkeypatch):
    """씨앗: 시작점에서 더 갈 곳이 없으면 선언된 화면 중 못 간 것만 주소로 직접 열어 거기서 다시 탐색한다. 끊겨도 이어 간다."""
    seeds = ["/help", "/hidden", "/nope"]               # 이미 닿은 화면, 링크 없는 화면, 404
    opts = dict(max_depth=2, settle_ms=50, workers=2, seeds=seeds)
    whole = Crawler(base + "/", **opts)
    whole.run(shots_dir=tmp_path / "a")
    assert [crawl._relative(u) for u in whole.roots] == ["/", "/hidden"]
    hidden = [n for n in whole.nodes if n.root == 1]
    assert [n.label for n in hidden] == ["숨은 화면", "숨은 화면 다음"] and all(n.explored for n in hidden)
    tests = whole.pytest_module()
    assert "ui.goto('/hidden')" in tests and "ui.goto('/nope')" not in tests
    assert "## 직접 연 화면 (씨앗)" in whole.markdown()
    assert [r for r, _ in whole.scenario_paths()][-1] == 1                       # 씨앗 경로는 뒤에: 시작점 경로의 테스트 이름은 그대로
    plain = Crawler(base + "/", max_depth=2, settle_ms=50, workers=2)
    plain.run()
    assert plain.paths() == [p for r, p in whole.scenario_paths() if r == 0]

    real, calls = Crawler._explore, []                  # 씨앗을 탐색하다 끊겨도 이어서 같은 결과
    def flaky(self, node):
        if node.root == 1 and not calls:
            calls.append(node.id)
            raise RuntimeError("끊김")
        real(self, node)
    monkeypatch.setattr(Crawler, "_explore", flaky)
    ck = tmp_path / "b" / ".crawl-partial.jsonl"
    ck.parent.mkdir()
    with pytest.raises(RuntimeError):
        Crawler(base + "/", **opts).run(shots_dir=tmp_path / "b" / "s", checkpoint=ck)
    again = Crawler(base + "/", **opts)
    again.run(shots_dir=tmp_path / "b" / "s", checkpoint=ck)
    assert _graph(again) == _graph(whole) and again.roots == whole.roots
