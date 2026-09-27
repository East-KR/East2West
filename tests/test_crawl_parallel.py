"""eastshift crawl 병렬 탐색: 브라우저 여러 개가 나눠 눌러도 그래프(상태·edge id·도착·종류)가 한 줄로 누를 때와 같다.
채우기(filled), confirm 취소(dismiss), 목록 대표·적응 확장이 모두 한 번씩 나오는 작은 앱으로 확인한다."""
import threading
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

import pytest

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
