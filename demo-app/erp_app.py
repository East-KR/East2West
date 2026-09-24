"""스모크 테스트 데모: 비슷하지만 조금씩 다른 조회 화면 12개. 표준 라이브러리만 사용.

python demo-app/erp_app.py <port> asis   모든 화면 정상
python demo-app/erp_app.py <port> tobe   화면 5개에 결함 주입 (아래 DEFECTS)

화면마다 조회 버튼 이름이 다르고 ("조회", "검색", "찾기", "Search", "목록 불러오기", 아이콘 버튼 …),
옆에 이름이 비슷한 다른 버튼이 있다 ("검색 조건 초기화", "조회 권한 요청", "상세 검색 열기" …).
조회 전에는 결과가 없고, 조회하면 결과 표가 채워진다. 첫 열은 상세 화면 링크.
"""
from __future__ import annotations

import html
import json
import sys
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse

# id, 화면명, 조회 버튼 (label, aria-label), 다른 버튼들, 조건 필드, 결과 열, 결과 행
SCREENS = [
    ("emp", "사원 조회", ("조회", None), ["초기화"], ["사원명"], ["사번", "이름", "부서"],
     [["E-1001", "홍길동", "영업"], ["E-1002", "김철수", "개발"]]),
    ("dept", "부서 목록", ("검색", None), ["검색 조건 초기화"], ["부서명"], ["부서코드", "부서명"],
     [["D-10", "영업부"], ["D-20", "개발부"]]),
    ("vendor", "거래처 관리", ("찾기", None), ["신규 등록", "엑셀 다운로드"], ["거래처명", "사업자번호"], ["거래처코드", "거래처명"],
     [["V-01", "한빛상사"], ["V-02", "대한물산"]]),
    ("item", "품목 마스터", ("조회하기", None), ["조회 권한 요청"], ["품목명"], ["품목코드", "품목명", "단가"],
     [["I-001", "볼펜", "1,235"], ["I-002", "노트", "2,000"]]),
    ("stock", "재고 현황", ("Search", None), ["Reset"], ["창고"], ["품목", "수량"],
     [["볼펜", "120"], ["노트", "45"]]),
    ("order", "주문 내역", ("목록 불러오기", None), ["상세 검색 열기"], ["주문일자"], ["주문번호", "고객", "금액"],
     [["O-501", "한빛상사", "1,355"], ["O-502", "대한물산", "0"]]),
    ("sales", "매출 집계", ("집계 실행", None), ["인쇄"], ["기간"], ["월", "매출"],
     [["2026-08", "12,000,000"], ["2026-09", "9,800,000"]]),
    ("inbound", "입고 관리", ("🔍", "검색"), ["등록"], ["입고일"], ["입고번호", "품목"],
     [["IN-01", "볼펜"], ["IN-02", "노트"]]),
    ("outbound", "출고 관리", ("조회", None), ["조회 조건 저장"], ["출고일"], ["출고번호", "품목"],
     [["OUT-01", "볼펜"], ["OUT-02", "노트"]]),
    ("pay", "급여 명세", ("검색하기", None), ["초기화"], ["지급월"], ["사번", "지급액"],
     [["E-1001", "3,200,000"], ["E-1002", "3,500,000"]]),
    ("approval", "결재 문서함", ("문서 조회", None), ["새 문서 작성"], ["문서 상태"], ["문서번호", "제목"],
     [["A-77", "출장 신청"], ["A-78", "구매 요청"]]),
    ("notice", "공지사항", ("검색", None), ["글쓰기"], ["제목"], ["번호", "제목"],
     [["N-1", "시스템 점검 안내"], ["N-2", "전환 일정 공지"]]),
]

# to-be에 주입한 결함: 화면 id → 종류
DEFECTS = {
    "vendor": "http500",      # 화면 자체가 500
    "stock": "js_error",      # 조회 버튼이 스크립트 오류로 아무 일도 안 함
    "sales": "empty",         # 조회는 되지만 결과가 비어 있음 (백엔드 결함)
    "approval": "alert",      # 조회하면 "시스템 오류" alert
    "notice": "detail404",    # 목록은 되지만 상세 화면이 404
}
VARIANT = "asis"


def page(title: str, body: str) -> bytes:
    return (f"<!doctype html><html lang='ko'><head><meta charset='utf-8'><title>{html.escape(title)}</title>"
            "<style>body{font-family:sans-serif;margin:16px}td,th{border:1px solid #999;padding:4px 8px}</style>"
            f"</head><body>{body}</body></html>").encode()


def screen_html(s) -> str:
    sid, title, (btn, aria), others, fields, cols, rows = s
    defect = DEFECTS.get(sid) if VARIANT == "tobe" else None
    data = [] if defect == "empty" else rows
    inputs = "".join(f"<label>{f} <input name='f{i}'></label> " for i, f in enumerate(fields))
    aria_attr = f" aria-label='{aria}'" if aria else ""
    buttons = f"<button type='button' id='q'{aria_attr}>{btn}</button> " + "".join(f"<button type='button'>{o}</button> " for o in others)
    head = "".join(f"<th>{c}</th>" for c in cols)
    if defect == "js_error":
        handler = "document.getElementById('q').onclick=function(){ undefinedFn(); render(); };"
    elif defect == "alert":
        handler = "document.getElementById('q').onclick=function(){ alert('시스템 오류가 발생했습니다.'); };"
    else:
        handler = "document.getElementById('q').onclick=render;"
    js = f"""<script>
var DATA={json.dumps(data, ensure_ascii=False)};
function render(){{var tb=document.getElementById('tb');tb.innerHTML='';
 DATA.forEach(function(r,i){{var tr=document.createElement('tr');
  tr.innerHTML=r.map(function(c,j){{return j==0?'<td><a href="/{sid}/detail/'+i+'">'+c+'</a></td>':'<td>'+c+'</td>';}}).join('');tb.appendChild(tr);}});
 document.getElementById('msg').textContent=DATA.length?('총 '+DATA.length+'건'):'조회 결과가 없습니다.';}}
{handler}
</script>"""
    return (f"<h1>{title}</h1><form onsubmit='return false'>{inputs}<div>{buttons}</div></form>"
            f"<p id='msg'>조회 버튼을 눌러 주세요.</p><table><thead><tr>{head}</tr></thead><tbody id='tb'></tbody></table>{js}")


class App(BaseHTTPRequestHandler):
    def log_message(self, *_):
        pass

    def _send(self, body: bytes, status=HTTPStatus.OK):
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        parts = [p for p in urlparse(self.path).path.split("/") if p]
        by_id = {s[0]: s for s in SCREENS}
        if not parts:
            links = "".join(f"<li><a href='/{s[0]}'>{s[1]}</a></li>" for s in SCREENS)
            return self._send(page("업무 메뉴", f"<h1>업무 메뉴</h1><ul>{links}</ul>"))
        s = by_id.get(parts[0])
        if s is None:
            return self._send(page("없음", "<p>404</p>"), HTTPStatus.NOT_FOUND)
        defect = DEFECTS.get(s[0]) if VARIANT == "tobe" else None
        if len(parts) == 1:
            if defect == "http500":
                return self._send(page("오류", "<h1>500 Internal Server Error</h1>"), HTTPStatus.INTERNAL_SERVER_ERROR)
            return self._send(page(s[1], screen_html(s)))
        if len(parts) == 3 and parts[1] == "detail":
            if defect == "detail404":
                return self._send(page("없음", "<p>404 Not Found</p>"), HTTPStatus.NOT_FOUND)
            row = s[6][int(parts[2])]
            dl = "".join(f"<dt>{c}</dt><dd>{v}</dd>" for c, v in zip(s[5], row))
            return self._send(page(f"{s[1]} 상세", f"<h1>{s[1]} 상세</h1><dl>{dl}</dl><a href='/{s[0]}'>목록</a>"))
        self._send(page("없음", "<p>404</p>"), HTTPStatus.NOT_FOUND)


if __name__ == "__main__":
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8811
    VARIANT = sys.argv[2] if len(sys.argv) > 2 else "asis"
    assert VARIANT in ("asis", "tobe"), VARIANT
    print(f"erp demo ({VARIANT}) on http://127.0.0.1:{port}/", flush=True)
    ThreadingHTTPServer(("127.0.0.1", port), App).serve_forever()
