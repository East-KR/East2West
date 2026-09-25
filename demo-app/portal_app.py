"""화면 지도 데모: 홈에서 시작해 라우트 여러 개로 갈라지고, 화면 안에 팝업·드로워·탭이 있는 업무 포털. 표준 라이브러리만 사용.

python demo-app/portal_app.py <port> asis          as-is (버그 포함)
python demo-app/portal_app.py <port> tobe          to-be. as-is 동작을 버그까지 그대로 옮김
python demo-app/portal_app.py <port> tobe-fixed    as-is 버그 두 개를 "고쳐버린" to-be (비교에서 잡혀야 하는 것)
python demo-app/portal_app.py <port> tobe-renamed  라벨 변경("신규 주문"→"주문 등록", "수량"→"주문 수량") + 부가세 반올림. 라벨 뒤에 숨은 동작 차이까지 도달하는지
python demo-app/portal_app.py <port> tobe-custom   tobe와 같은 동작, 신규 주문 팝업의 품목만 커스텀 드롭다운 (div role=combobox + listbox, React/MUI 방식)
python demo-app/portal_app.py <port> tobe-modern   tobe와 같은 동작·같은 글자·같은 역할, 겉모습만 새로 만든 to-be: 왼쪽 사이드바, 다른 색·글꼴, 카드·알약 버튼, 밑줄 탭,
                                                   아이콘(aria-hidden). 실제 전환처럼 "화면은 달라 보여도 동작은 같다"를 보여 준다 → 비교 25개 모두 같음

라우트
  /                  홈 (요약 카드, 메뉴)
  /orders            주문 목록: [필터] 드로워(상태로 거르기), [신규 주문] 팝업(고객·품목·수량 → 저장)
  /orders/<id>       주문 상세: 탭(기본 정보 / 이력), [주문 취소] confirm, [목록으로]
  /customers         고객 목록: [고객 등록] 팝업
  /customers/<id>    고객 상세: [메모] 드로워(저장), [주문 보기] → /orders?customer=<id>
  /settings          설정: 탭(일반 / 알림), [저장] alert

as-is 동작 (버그 포함)
- 주문 합계의 부가세는 10원 단위 절사 (to-be는 반올림)
- 이미 취소된 주문도 다시 취소된다: 이력에 취소가 두 번 쌓인다 (to-be는 alert로 막는다)
"""
from __future__ import annotations

import copy
import sys
import threading
import uuid
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

VARIANT = "asis"
ITEMS = {"notebook": ("노트북", 1_250_000), "mouse": ("마우스", 33_000), "pen": ("볼펜", 1_225)}
# 세션(쿠키 psid)마다 SEED의 복사본 = 세션별 테스트 DB. 브라우저 컨텍스트가 새로 뜰 때마다 초기 데이터로 시작해서
# 테스트 순서·병렬 실행·반복 실행에 상관없이 같은 결과가 나온다 (실제 프로젝트에서는 DB 스냅샷 복원이 이 역할)
SEED = {"customers": [{"id": 1, "name": "김철수", "grade": "VIP", "memo": ""}, {"id": 2, "name": "이영희", "grade": "일반", "memo": "전화 선호"}],
        "orders": [{"id": 1, "customer": 1, "item": "notebook", "qty": 1, "status": "접수", "log": ["접수"]},
                   {"id": 2, "customer": 2, "item": "pen", "qty": 4, "status": "배송중", "log": ["접수", "배송중"]},
                   {"id": 3, "customer": 1, "item": "mouse", "qty": 2, "status": "취소", "log": ["접수", "취소"]}]}
SESSIONS: dict[str, dict] = {}
_L = threading.local()


def DB() -> dict:
    return _L.db


CSS = ("[hidden]{display:none!important}body{font-family:sans-serif;margin:0;color:#222}header{background:#20413c;color:#fff;padding:10px 20px;display:flex;gap:18px;align-items:center}"
       "header a{color:#fff}main{padding:18px 20px}table{border-collapse:collapse}td,th{border:1px solid #bbb;padding:5px 10px;text-align:left}"
       ".cards{display:flex;gap:12px}.card{border:1px solid #ccc;border-radius:8px;padding:12px 16px;min-width:140px}.card b{display:block;font-size:22px}"
       "[role=dialog]{position:fixed;inset:0;background:rgba(0,0,0,.4);display:grid;place-items:center}[role=dialog]>div{background:#fff;padding:18px 22px;border-radius:8px;min-width:320px}"
       "aside{position:fixed;right:0;top:0;bottom:0;width:280px;background:#f4f6f5;border-left:1px solid #ccc;padding:16px}"
       "[role=tablist]{display:flex;gap:4px;margin:12px 0}[role=tab]{padding:6px 12px;border:1px solid #bbb;background:#eee;cursor:pointer}[role=tab][aria-selected=true]{background:#fff;font-weight:bold}"
       "[role=tabpanel]{border:1px solid #bbb;padding:12px}label{display:block;margin:6px 0}.toolbar{display:flex;gap:8px;margin-bottom:12px}")
JS = ("function openBox(id){document.getElementById(id).hidden=false}function closeBox(id){document.getElementById(id).hidden=true}"
      "function pickTab(btn){const list=btn.parentElement;for(const t of list.querySelectorAll('[role=tab]')){const on=t===btn;t.setAttribute('aria-selected',on);"
      "document.getElementById(t.getAttribute('aria-controls')).hidden=!on}}")


def vat(supply: int) -> int:
    return int(supply * 0.1 + 0.5) if VARIANT in ("tobe-fixed", "tobe-renamed") else int(supply * 0.1 // 10 * 10)  # 고친 변형: 반올림, as-is: 10원 절사


def L(label: str) -> str:
    """tobe-renamed 변형의 라벨. 나머지 변형은 as-is 라벨 그대로."""
    return {"신규 주문": "주문 등록", "수량": "주문 수량"}.get(label, label) if VARIANT == "tobe-renamed" else label


# tobe-modern: 겉모습만 다른 to-be. 스냅샷 비교에 들어가는 것(글자, 요소 이름·역할·순서, 주소, 제목, 대화상자)은 그대로 두고 CSS와 aria-hidden 아이콘만 다르다
CSS_MODERN = ("[hidden]{display:none!important}*{box-sizing:border-box}body{margin:0;font-family:'Pretendard','Inter',-apple-system,'Apple SD Gothic Neo','Malgun Gothic',system-ui,sans-serif;"
              "color:#1c1b2e;background:#f3f2fa;display:flex;min-height:100vh}"
              "header{width:224px;flex:none;background:linear-gradient(180deg,#3b2fa8,#5b46d6);color:#fff;padding:22px 16px;display:flex;flex-direction:column;gap:6px}"
              "header b{font-size:18px;letter-spacing:-.01em;margin:0 8px 18px;display:block}header a{color:#fff;text-decoration:none;padding:9px 12px;border-radius:10px;display:flex;gap:9px;align-items:center;font-size:14.5px;opacity:.86}"
              "header a:hover{background:rgba(255,255,255,.14);opacity:1}header a i{font-style:normal;width:18px;text-align:center;opacity:.9}"
              "main{flex:1;padding:30px 36px;max-width:1100px}h1{font-size:26px;font-weight:700;margin:0 0 18px;letter-spacing:-.02em}h2{font-size:17px;margin:0 0 12px}"
              "table{border-collapse:separate;border-spacing:0;width:100%;background:#fff;border-radius:14px;box-shadow:0 1px 3px rgba(28,27,46,.08);overflow:hidden}"
              "th{font-size:11.5px;text-transform:uppercase;letter-spacing:.08em;color:#6f6c8c;background:#f8f7fd;padding:11px 16px;text-align:left;border-bottom:1px solid #e6e4f2}"
              "td{padding:12px 16px;border-bottom:1px solid #eeecf6;text-align:left}tr:last-child td{border-bottom:0}tbody tr:hover td{background:#faf9ff}td a{color:#4a3bc9;font-weight:600;text-decoration:none}"
              ".cards{display:grid;grid-template-columns:repeat(3,minmax(140px,220px));gap:14px;margin-bottom:26px}"
              ".card{background:linear-gradient(135deg,#fff,#f1eefc);border:1px solid #e3dff5;border-radius:16px;padding:16px 18px;color:#6f6c8c;font-size:13px;box-shadow:0 1px 3px rgba(28,27,46,.06)}"
              ".card b{display:block;font-size:30px;color:#2d2470;margin-top:6px;letter-spacing:-.02em}"
              "ul{padding-left:0;list-style:none;display:flex;gap:10px;flex-wrap:wrap}main>ul a,li>a{display:inline-block;background:#fff;border:1px solid #e3dff5;border-radius:999px;padding:8px 16px;color:#4a3bc9;text-decoration:none;font-weight:600}"
              "button{font:inherit;font-size:14px;border:1px solid #d9d5ee;background:#fff;color:#2d2470;padding:8px 16px;border-radius:999px;cursor:pointer;font-weight:600}"
              "button:hover{border-color:#5b46d6}.toolbar{display:flex;gap:8px;margin-bottom:16px;align-items:center}.toolbar button:last-child,button[type=submit]{background:#4a3bc9;color:#fff;border-color:#4a3bc9}"
              "p{color:#6f6c8c;margin:0 0 12px}strong{color:#4a3bc9}"
              "[role=dialog]{position:fixed;inset:0;background:rgba(28,27,46,.45);backdrop-filter:blur(3px);display:grid;place-items:center}"
              "[role=dialog]>div{background:#fff;padding:24px 28px;border-radius:18px;min-width:360px;box-shadow:0 30px 80px rgba(28,27,46,.35)}"
              "aside{position:fixed;right:0;top:0;bottom:0;width:320px;background:#fff;box-shadow:-12px 0 40px rgba(28,27,46,.18);padding:24px 26px;border-left:0}"
              "[role=tablist]{display:flex;gap:18px;margin:14px 0 0;border-bottom:2px solid #e6e4f2}[role=tab]{border:0;background:none;border-radius:0;padding:8px 2px 10px;margin-bottom:-2px;color:#6f6c8c;border-bottom:2px solid transparent}"
              "[role=tab][aria-selected=true]{color:#4a3bc9;border-bottom-color:#4a3bc9}[role=tabpanel]{background:#fff;border-radius:0 0 14px 14px;padding:16px 18px;box-shadow:0 1px 3px rgba(28,27,46,.08);margin-bottom:16px}"
              "[role=tabpanel] table{box-shadow:none;border-radius:0}label{display:block;margin:10px 0;font-size:14px;color:#3d3a5c}"
              "input,select,textarea{font:inherit;padding:8px 10px;border:1px solid #d9d5ee;border-radius:10px;background:#fbfbfe;min-width:180px}form>a{margin-left:10px;color:#6f6c8c}")
ICONS = {"홈": "⌂", "주문 관리": "▤", "고객 관리": "◉", "설정": "⚙"}


def page(title: str, body: str) -> bytes:
    if VARIANT == "tobe-modern":  # 사이드바: 같은 글자·같은 순서의 링크. 아이콘은 aria-hidden이라 접근성 이름에 들어가지 않는다
        links = "".join(f"<a href='{h}'><i aria-hidden='true'>{ICONS[t]}</i>{t}</a>" for h, t in (("/", "홈"), ("/orders", "주문 관리"), ("/customers", "고객 관리"), ("/settings", "설정")))
        nav, css = f"<header><b>업무 포털</b>{links}</header>", CSS_MODERN
    else:
        nav, css = "<header><b>업무 포털</b><a href='/'>홈</a><a href='/orders'>주문 관리</a><a href='/customers'>고객 관리</a><a href='/settings'>설정</a></header>", CSS
    return (f"<!doctype html><html lang='ko'><head><meta charset='utf-8'><title>{title}</title><style>{css}</style><script>{JS}</script></head>"
            f"<body>{nav}<main>{body}</main></body></html>").encode()


def won(n: int) -> str:
    return f"{n:,}원"


def customer(cid: int) -> dict:
    return next(c for c in DB()["customers"] if c["id"] == cid)


def order_total(o: dict) -> tuple[int, int, int]:
    supply = ITEMS[o["item"]][1] * o["qty"]
    v = vat(supply)
    return supply, v, supply + v


def home() -> bytes:
    open_n = sum(o["status"] not in ("취소", "완료") for o in DB()["orders"])
    return page("홈", "<h1>포털 홈</h1><div class='cards'>"
                f"<div class='card'>주문<b>{len(DB()["orders"])}건</b></div><div class='card'>미처리 주문<b>{open_n}건</b></div><div class='card'>고객<b>{len(DB()["customers"])}명</b></div></div>"
                "<h2>바로 가기</h2><ul><li><a href='/orders'>주문 목록 열기</a></li><li><a href='/customers'>고객 목록 열기</a></li><li><a href='/settings'>설정 열기</a></li></ul>")


def orders_list(q: dict) -> bytes:
    status = (q.get("status") or [""])[0]
    cust = (q.get("customer") or [""])[0]
    rows = [o for o in DB()["orders"] if (not status or o["status"] == status) and (not cust or str(o["customer"]) == cust)]
    trs = "".join(f"<tr><td><a href='/orders/{o['id']}'>ORD-{o['id']} {ITEMS[o['item']][0]}</a></td><td>{customer(o['customer'])['name']}</td>"
                  f"<td>{o['qty']}</td><td>{won(order_total(o)[2])}</td><td>{o['status']}</td></tr>" for o in rows)
    opts = "".join(f"<option value='{s}' {'selected' if s == status else ''}>{s or '전체'}</option>" for s in ("", "접수", "배송중", "완료", "취소"))
    cust_opts = "".join(f"<option value='{c['id']}'>{c['name']}</option>" for c in DB()["customers"])
    item_opts = "".join(f"<option value='{k}'>{v[0]}</option>" for k, v in ITEMS.items())
    if VARIANT == "tobe-custom":
        lis = "".join(f"<li role='option' data-v='{k}' aria-selected='{str(i == 0).lower()}'>{v[0]}</li>" for i, (k, v) in enumerate(ITEMS.items()))
        item_field = (f"<div><span id='lbl-item'>품목</span> <div role='combobox' id='cb' tabindex='0' aria-labelledby='lbl-item' aria-expanded='false' aria-controls='lb'>노트북</div>"
                      f"<ul role='listbox' id='lb' hidden>{lis}</ul><input type='hidden' name='item' value='notebook'></div>"
                      "<script>var cb=document.getElementById('cb'),lb=document.getElementById('lb');"
                      "cb.onclick=function(){lb.hidden=!lb.hidden;cb.setAttribute('aria-expanded',String(!lb.hidden));};"
                      "lb.querySelectorAll('li').forEach(function(li){li.onclick=function(){cb.textContent=li.textContent;"
                      "cb.parentElement.querySelector('input[name=item]').value=li.dataset.v;lb.querySelectorAll('li').forEach(function(x){x.setAttribute('aria-selected','false');});"
                      "li.setAttribute('aria-selected','true');lb.hidden=true;cb.setAttribute('aria-expanded','false');};});</script>")
    else:
        item_field = f"<label>품목 <select name='item'>{item_opts}</select></label>"
    body = (f"<h1>주문 목록</h1><div class='toolbar'><button type='button' onclick=\"openBox('filter')\">필터</button><button type='button' onclick=\"openBox('new')\">{L('신규 주문')}</button></div>"
            f"<p>{len(rows)}건" + (f" · 상태 {status}" if status else "") + (f" · 고객 {customer(int(cust))['name']}" if cust else "") + "</p>"
            f"<table><tr><th>주문</th><th>고객</th><th>수량</th><th>합계</th><th>상태</th></tr>{trs}</table>"
            f"<aside id='filter' aria-label='필터' hidden><h2>필터</h2><form method='get' action='/orders'><label>상태 <select name='status'>{opts}</select></label>"
            "<button type='submit'>적용</button> <button type='button' onclick=\"closeBox('filter')\">닫기</button></form></aside>"
            f"<div id='new' role='dialog' aria-label='{L('신규 주문')}' hidden><div><h2>{L('신규 주문')}</h2><form method='post' action='/orders'>"
            f"<label>고객 <select name='customer'>{cust_opts}</select></label>{item_field}"
            f"<label>{L('수량')} <input name='qty' value='1'></label><button type='submit'>저장</button> <button type='button' onclick=\"closeBox('new')\">닫기</button></form></div></div>")
    return page("주문 목록", body)


def order_detail(o: dict) -> bytes:
    supply, v, total = order_total(o)
    c = customer(o["customer"])
    log = "".join(f"<li>{i + 1}. {s}</li>" for i, s in enumerate(o["log"]))
    body = (f"<h1>주문 상세 ORD-{o['id']}</h1><p>상태: <strong>{o['status']}</strong></p>"
            "<div role='tablist'><button type='button' role='tab' aria-selected='true' aria-controls='p-basic' onclick='pickTab(this)'>기본 정보</button>"
            "<button type='button' role='tab' aria-selected='false' aria-controls='p-log' onclick='pickTab(this)'>이력</button></div>"
            f"<div id='p-basic' role='tabpanel' aria-label='기본 정보'><table><tr><th>고객</th><td><a href='/customers/{c['id']}'>{c['name']}</a></td></tr>"
            f"<tr><th>품목</th><td>{ITEMS[o['item']][0]}</td></tr><tr><th>수량</th><td>{o['qty']}</td></tr><tr><th>공급가액</th><td>{won(supply)}</td></tr>"
            f"<tr><th>부가세</th><td>{won(v)}</td></tr><tr><th>합계</th><td>{won(total)}</td></tr></table></div>"
            f"<div id='p-log' role='tabpanel' aria-label='이력' hidden><ul>{log}</ul></div>"
            f"<form method='post' action='/orders/{o['id']}/cancel' onsubmit=\"return confirm('이 주문을 취소할까요?')\"><button type='submit'>주문 취소</button> <a href='/orders'>목록으로</a></form>")
    return page(f"주문 상세 ORD-{o['id']}", body)


def customers_list() -> bytes:
    trs = "".join(f"<tr><td><a href='/customers/{c['id']}'>{c['name']}</a></td><td>{c['grade']}</td><td>{sum(o['customer'] == c['id'] for o in DB()["orders"])}</td></tr>" for c in DB()["customers"])
    body = ("<h1>고객 목록</h1><div class='toolbar'><button type='button' onclick=\"openBox('reg')\">고객 등록</button></div>"
            f"<table><tr><th>이름</th><th>등급</th><th>주문 수</th></tr>{trs}</table>"
            "<div id='reg' role='dialog' aria-label='고객 등록' hidden><div><h2>고객 등록</h2><form method='post' action='/customers'>"
            "<label>이름 <input name='name'></label><label>등급 <select name='grade'><option>일반</option><option>VIP</option></select></label>"
            "<button type='submit'>등록</button> <button type='button' onclick=\"closeBox('reg')\">닫기</button></form></div></div>")
    return page("고객 목록", body)


def customer_detail(c: dict) -> bytes:
    body = (f"<h1>고객 상세 {c['name']}</h1><table><tr><th>등급</th><td>{c['grade']}</td></tr><tr><th>메모</th><td>{c['memo'] or '(없음)'}</td></tr></table>"
            f"<div class='toolbar'><button type='button' onclick=\"openBox('memo')\">메모</button><a href='/orders?customer={c['id']}'>주문 보기</a></div>"
            f"<aside id='memo' aria-label='메모' hidden><h2>메모</h2><form method='post' action='/customers/{c['id']}/memo'><label>메모 내용 <textarea name='memo'>{c['memo']}</textarea></label>"
            "<button type='submit'>저장</button> <button type='button' onclick=\"closeBox('memo')\">닫기</button></form></aside>")
    return page(f"고객 상세 {c['name']}", body)


def settings() -> bytes:
    body = ("<h1>설정</h1><div role='tablist'><button type='button' role='tab' aria-selected='true' aria-controls='s-general' onclick='pickTab(this)'>일반</button>"
            "<button type='button' role='tab' aria-selected='false' aria-controls='s-noti' onclick='pickTab(this)'>알림</button></div>"
            "<form onsubmit=\"alert('저장되었습니다.');return false\"><div id='s-general' role='tabpanel' aria-label='일반'><label>회사명 <input name='company' value='데모상사'></label></div>"
            "<div id='s-noti' role='tabpanel' aria-label='알림' hidden><label><input type='checkbox' name='mail' checked> 메일 알림</label><label><input type='checkbox' name='sms'> 문자 알림</label></div>"
            "<button type='submit'>저장</button></form>")
    return page("설정", body)


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *_):
        pass

    def _begin(self) -> None:
        """쿠키의 세션을 찾거나 새로 만든다. 새 세션이면 응답에 Set-Cookie."""
        cookies = dict(kv.strip().split("=", 1) for kv in self.headers.get("Cookie", "").split(";") if "=" in kv)
        sid = cookies.get("psid")
        _L.new_sid = None
        if not sid or sid not in SESSIONS:
            sid = uuid.uuid4().hex
            if len(SESSIONS) > 500:
                SESSIONS.pop(next(iter(SESSIONS)))
            SESSIONS[sid] = copy.deepcopy(SEED)
            _L.new_sid = sid
        _L.db = SESSIONS[sid]

    def _cookie(self) -> None:
        if _L.new_sid:
            self.send_header("Set-Cookie", f"psid={_L.new_sid}; Path=/")

    def _send(self, body: bytes, status=HTTPStatus.OK) -> None:
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self._cookie()
        self.end_headers()
        self.wfile.write(body)

    def _redirect(self, to: str) -> None:
        self.send_response(HTTPStatus.SEE_OTHER)
        self.send_header("Location", to)
        self._cookie()
        self.end_headers()

    def _form(self) -> dict[str, str]:
        raw = self.rfile.read(int(self.headers.get("Content-Length", 0))).decode()
        return {k: v[0] for k, v in parse_qs(raw, keep_blank_values=True).items()}

    def do_GET(self):
        self._begin()
        u = urlparse(self.path)
        parts = [p for p in u.path.split("/") if p]
        if not parts:
            return self._send(home())
        if parts == ["orders"]:
            return self._send(orders_list(parse_qs(u.query)))
        if parts[0] == "orders" and len(parts) == 2 and parts[1].isdigit():
            o = next((x for x in DB()["orders"] if x["id"] == int(parts[1])), None)
            return self._send(order_detail(o)) if o else self._send(page("없음", "<h1>주문이 없습니다</h1>"), HTTPStatus.NOT_FOUND)
        if parts == ["customers"]:
            return self._send(customers_list())
        if parts[0] == "customers" and len(parts) == 2 and parts[1].isdigit():
            c = next((x for x in DB()["customers"] if x["id"] == int(parts[1])), None)
            return self._send(customer_detail(c)) if c else self._send(page("없음", "<h1>고객이 없습니다</h1>"), HTTPStatus.NOT_FOUND)
        if parts == ["settings"]:
            return self._send(settings())
        self._send(page("없음", "<h1>페이지가 없습니다</h1>"), HTTPStatus.NOT_FOUND)

    def do_POST(self):
        self._begin()
        u = urlparse(self.path)
        parts = [p for p in u.path.split("/") if p]
        f = self._form()
        if parts == ["orders"]:
            qty = int(f.get("qty") or 0)
            o = {"id": max(x["id"] for x in DB()["orders"]) + 1, "customer": int(f.get("customer") or 1), "item": f.get("item") or "pen", "qty": qty, "status": "접수", "log": ["접수"]}
            DB()["orders"].append(o)
            return self._redirect(f"/orders/{o['id']}")
        if parts[0] == "orders" and len(parts) == 3 and parts[2] == "cancel":
            o = next(x for x in DB()["orders"] if x["id"] == int(parts[1]))
            if VARIANT == "tobe-fixed" and o["status"] == "취소":
                return self._send(page("주문 상세", f"<script>alert('이미 취소된 주문입니다.');location.href='/orders/{o['id']}'</script>"))
            o["status"] = "취소"
            o["log"].append("취소")  # as-is: 중복 취소도 이력에 쌓인다
            return self._redirect(f"/orders/{o['id']}")
        if parts == ["customers"]:
            c = {"id": max(x["id"] for x in DB()["customers"]) + 1, "name": f.get("name") or "(이름 없음)", "grade": f.get("grade") or "일반", "memo": ""}
            DB()["customers"].append(c)
            return self._redirect(f"/customers/{c['id']}")
        if parts[0] == "customers" and len(parts) == 3 and parts[2] == "memo":
            c = customer(int(parts[1]))
            c["memo"] = f.get("memo", "")
            return self._redirect(f"/customers/{c['id']}")
        self._send(page("없음", "<h1>페이지가 없습니다</h1>"), HTTPStatus.NOT_FOUND)


if __name__ == "__main__":
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8820
    VARIANT = sys.argv[2] if len(sys.argv) > 2 else "asis"
    assert VARIANT in ("asis", "tobe", "tobe-fixed", "tobe-renamed", "tobe-custom", "tobe-modern"), VARIANT
    print(f"portal demo ({VARIANT}) on http://127.0.0.1:{port}/")
    ThreadingHTTPServer(("127.0.0.1", port), Handler).serve_forever()
