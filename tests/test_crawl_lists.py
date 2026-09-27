"""eastshift crawl 의 목록 표본화를 실제 브라우저로: 12행 목록에서 대표만 누르고, 같은 층의 대표가 다른 화면으로 가면 그 층을 더 누른다."""
import threading
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from eastshift.crawl import Crawler

STATUS = ["접수", "취소"]


def page(body: str) -> bytes:
    return f"<!doctype html><html lang='ko'><head><meta charset='utf-8'><title>t</title></head><body><main>{body}</main></body></html>".encode()


class App(BaseHTTPRequestHandler):
    def log_message(self, *_):
        pass

    def do_GET(self):
        if self.path == "/":
            rows = "".join(f"<tr><td><a href='/orders/{i}'>ORD-{i} 품목{i}</a></td><td>고객{i}</td><td>{STATUS[i % 2]}</td></tr>" for i in range(1, 13))
            body = f"<h1>주문 목록</h1><table><tr><th>주문</th><th>고객</th><th>상태</th></tr>{rows}</table><a href='/help'>도움말</a>"
        elif self.path.startswith("/orders/"):
            i = int(self.path.rsplit("/", 1)[1])
            # 숨은 분기: 상태 열에는 안 보이는 속성(id 가 3의 배수)에 따라 상세에 '긴급' 구역이 생긴다
            body = f"<h1>주문 상세 ORD-{i}</h1><p>상태: {STATUS[i % 2]}</p>" + ("<section><h2>긴급 처리</h2><button>승인</button></section>" if i % 3 == 0 else "")
        elif self.path == "/help":
            body = "<h1>도움말</h1>"
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


def test_list_sampling_and_adaptive_expansion(base):
    c = Crawler(base + "/", max_depth=1, settle_ms=50, reps=3)
    c.run()
    root = c.nodes[0]
    assert root.lists and root.lists[0]["heading"] == "주문 목록" and root.lists[0]["rows"] == 12
    assert root.lists[0]["branch"] == ["상태"] and root.lists[0]["source"] == "rule"
    tpl = [e for e in c.edges if e.template]
    rows = sorted(e.row for e in tpl)
    # 대표: 취소(1행 ORD-1)·접수(2행)·취소 둘째(3행 = ORD-3, 긴급 구역) → 취소 층의 두 대표가 다른 화면 → 취소 층 확장 2행 (ORD-5, ORD-7)
    assert rows == [0, 1, 2, 4, 6], rows
    assert sum(1 for e in tpl if "적응 확장" in e.reason) == 2
    assert all(e.stratum == {"상태": "취소"} for e in tpl if e.row in (0, 2, 4, 6)) and [e.stratum for e in tpl if e.row == 1] == [{"상태": "접수"}]
    assert [e.action.el.name for e in c.edges if not e.template and e.kind == "transition"] == ["도움말"]  # 목록 밖 링크는 그대로
    assert len({e.dst for e in tpl}) == 3  # 상세 화면 구조 3가지: 접수, 취소, 접수+긴급
    md = c.markdown()
    assert "## 목록 표본" in md and "| n0 | 주문 목록 | 12 | 상태 (rule) |" in md and "목록 대표: 주문 목록:주문 3행 (상태=취소) / 12행 중" in md
    assert root.lists[0]["reps"] == {"주문 목록:주문:link": [0, 1, 2, 4, 6]}


def test_fixture_pick_overrides(base):
    c = Crawler(base + "/", max_depth=1, settle_ms=50, reps=2, pick={"주문 목록": ["고객"]})
    c.run()
    L = c.nodes[0].lists[0]
    assert L["source"] == "fixture" and L["branch"] == ["고객"]
    assert sorted(e.row for e in c.edges if e.template) == [0, 1]  # 고객은 행마다 다르니 층이 12개 → 상한 2


def test_dry_run_marks_list_reps(base):
    c = Crawler(base + "/", max_depth=1, settle_ms=50, reps=3)
    pv = c.preview()  # 브라우저는 preview 가 스스로 띄운다
    marked = [s for s, _ in pv["click"] if "[목록" in s]
    assert len(marked) == 3 and all("주문 목록:주문" in s for s in marked) and any(s for s, _ in pv["click"] if "도움말" in s)
