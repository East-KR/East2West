"""parity 루프 데모용 작은 예약 앱. 표준 라이브러리만 사용.

python demo-app/app.py [port]   →  http://127.0.0.1:8787/
- /login        아이디/비밀번호 → 쿠키 세션
- /             예약 폼: 예약자 이름, 캘린더(두 달, 날짜 버튼 이름이 중복), 숙박 일수 select, 조식 checkbox, 예약하기
- /reserve      POST → /reservations/<id>
- /reservations/<id>  예약 상세 (총 금액 = 1박 100,000원 × 박수 + 조식 20,000원 × 박수)
"""
from __future__ import annotations

import calendar
import json
import sys
from datetime import date
from http import HTTPStatus
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

PRICE_PER_NIGHT = 100_000
BREAKFAST_PER_NIGHT = 20_000
USERS = {"tester": "pass1234"}
RESERVATIONS: dict[int, dict] = {}
MONTHS = [(2026, 10), (2026, 11)]

CSS = """body{font-family:-apple-system,sans-serif;max-width:720px;margin:32px auto;padding:0 16px}
fieldset{border:1px solid #ccc;margin:12px 0}.cal{display:inline-block;vertical-align:top;margin:0 12px 12px 0}
.cal h3{margin:4px 0}.cal .grid{display:grid;grid-template-columns:repeat(7,36px);gap:2px}
.cal button{height:32px}.cal button[aria-pressed=true]{background:#1a6;color:#fff}
.err{color:#b00}.total{font-size:1.3em;font-weight:bold}"""


def page(title: str, body: str) -> bytes:
    return f"<!doctype html><html lang='ko'><head><meta charset='utf-8'><title>{title}</title><style>{CSS}</style></head><body><h1>{title}</h1>{body}</body></html>".encode()


def calendar_html() -> str:
    out = []
    for y, m in MONTHS:
        cells = []
        first_weekday, days = calendar.monthrange(y, m)  # Monday=0
        for _ in range((first_weekday + 1) % 7):
            cells.append("<span></span>")
        for d in range(1, days + 1):
            iso = f"{y}-{m:02d}-{d:02d}"
            cells.append(f"<button type='button' class='day' data-date='{iso}' aria-pressed='false'>{d}</button>")
        out.append(f"<div class='cal'><h3>{y}.{m:02d}.</h3><div class='grid'>{''.join(cells)}</div></div>")
    return "".join(out)


FORM_JS = """
document.getElementById('open').addEventListener('click',()=>document.getElementById('confirm').showModal());
document.getElementById('cancel').addEventListener('click',()=>document.getElementById('confirm').close());
document.getElementById('ok').addEventListener('click',()=>document.getElementById('f').submit());
document.querySelectorAll('button.day').forEach(b=>b.addEventListener('click',()=>{
  document.querySelectorAll('button.day').forEach(x=>x.setAttribute('aria-pressed','false'));
  b.setAttribute('aria-pressed','true'); document.getElementById('checkin').value=b.dataset.date;
}));
"""


class App(BaseHTTPRequestHandler):
    def log_message(self, *_):
        pass

    # -- helpers ------------------------------------------------------------
    def _send(self, body: bytes, status=HTTPStatus.OK, headers: dict | None = None):
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        for k, v in (headers or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(body)

    def _redirect(self, location: str, headers: dict | None = None):
        self.send_response(HTTPStatus.SEE_OTHER)
        self.send_header("Location", location)
        for k, v in (headers or {}).items():
            self.send_header(k, v)
        self.end_headers()

    def _logged_in(self) -> bool:
        c = SimpleCookie(self.headers.get("Cookie", ""))
        return "session" in c and c["session"].value == "ok"

    def _form(self) -> dict[str, str]:
        n = int(self.headers.get("Content-Length", 0))
        raw = self.rfile.read(n).decode()
        return {k: v[0] for k, v in parse_qs(raw).items()}

    # -- routes -------------------------------------------------------------
    def do_GET(self):
        u = urlparse(self.path)
        if u.path == "/login":
            return self._send(page("로그인", LOGIN_HTML))
        if not self._logged_in():
            return self._redirect("/login")
        if u.path == "/":
            return self._send(page("객실 예약", RESERVE_HTML.replace("{CALENDAR}", calendar_html())))
        if u.path.startswith("/reservations/"):
            try:
                r = RESERVATIONS[int(u.path.rsplit("/", 1)[1])]
            except (KeyError, ValueError):
                return self._send(page("없음", "<p>예약을 찾을 수 없습니다.</p>"), HTTPStatus.NOT_FOUND)
            return self._send(page("예약 완료", DETAIL_HTML.format(**r, total_fmt=f"{r['total']:,}")))
        self._send(page("없음", "<p>404</p>"), HTTPStatus.NOT_FOUND)

    def do_POST(self):
        u = urlparse(self.path)
        f = self._form()
        if u.path == "/login":
            if USERS.get(f.get("username", "")) == f.get("password", ""):
                return self._redirect("/", {"Set-Cookie": "session=ok; Path=/"})
            return self._send(page("로그인", LOGIN_HTML + "<p class='err' role='alert'>아이디 또는 비밀번호가 올바르지 않습니다.</p>"), HTTPStatus.UNAUTHORIZED)
        if not self._logged_in():
            return self._redirect("/login")
        if u.path == "/reserve":
            name, checkin = f.get("name", "").strip(), f.get("checkin", "")
            nights = int(f.get("nights", "1"))
            breakfast = f.get("breakfast") == "on"
            if not name or not checkin:
                return self._send(page("객실 예약", "<p class='err' role='alert'>예약자 이름과 체크인 날짜는 필수입니다.</p>" + RESERVE_HTML.replace("{CALENDAR}", calendar_html())), HTTPStatus.BAD_REQUEST)
            total = PRICE_PER_NIGHT * nights + (BREAKFAST_PER_NIGHT * nights if breakfast else 0)
            rid = len(RESERVATIONS) + 1
            RESERVATIONS[rid] = {"id": rid, "name": name, "checkin": checkin, "nights": nights,
                                 "breakfast": "포함" if breakfast else "미포함", "total": total}
            return self._redirect(f"/reservations/{rid}?nights={nights}&breakfast={int(breakfast)}")
        self._send(page("없음", "<p>404</p>"), HTTPStatus.NOT_FOUND)


LOGIN_HTML = """
<form method='post' action='/login'>
  <p><label>아이디 <input name='username' autocomplete='username'></label></p>
  <p><label>비밀번호 <input name='password' type='password' autocomplete='current-password'></label></p>
  <button type='submit'>로그인</button>
</form>"""

RESERVE_HTML = """
<form method='post' action='/reserve' id='f'>
  <p><label>투숙객 이름 <input name='name'></label></p>
  <fieldset><legend>체크인 날짜</legend>{CALENDAR}
    <p><label>선택한 체크인 <input id='checkin' name='checkin' readonly></label></p></fieldset>
  <p><label>숙박 일수 <select name='nights'><option value='1'>1박</option><option value='2'>2박</option><option value='3'>3박</option></select></label></p>
  <p><label><input type='checkbox' name='breakfast'> 조식 포함</label></p>
  <button type='button' id='open'>예약 완료하기</button>
</form>
<dialog id='confirm' aria-labelledby='ct'><h2 id='ct'>예약 내용을 확정하시겠습니까?</h2>
  <p>확정 후에는 변경할 수 없습니다.</p>
  <button type='button' id='ok'>확정</button> <button type='button' id='cancel'>취소</button></dialog>
<script>""" + FORM_JS + "</script>"

DETAIL_HTML = """
<dl>
  <dt>예약 번호</dt><dd>{id}</dd>
  <dt>예약자</dt><dd>{name}</dd>
  <dt>체크인</dt><dd>{checkin}</dd>
  <dt>숙박 일수</dt><dd>{nights}박</dd>
  <dt>조식</dt><dd>{breakfast}</dd>
</dl>
<p class='total'>총 금액 {total_fmt}원</p>
<p><a href='/'>다른 예약 하기</a></p>"""

if __name__ == "__main__":
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8787
    srv = ThreadingHTTPServer(("127.0.0.1", port), App)
    print(f"demo app on http://127.0.0.1:{port}/", flush=True)
    srv.serve_forever()
