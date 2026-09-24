"""차세대 전환 검증 데모: 같은 주문 화면의 as-is(레거시)와 to-be 구현. 표준 라이브러리만 사용.

python demo-app/legacy_app.py <port> asis        frameset(상단 menu 프레임 + main 프레임), table 레이아웃, alert/confirm
python demo-app/legacy_app.py <port> tobe        단일 페이지, div 레이아웃, URL 체계 변경. as-is 동작을 버그까지 그대로 옮김
python demo-app/legacy_app.py <port> tobe-fixed  tobe와 같지만 as-is 버그 두 개를 "고쳐버린" 전환 (비교에서 잡혀야 하는 것)
python demo-app/legacy_app.py <port> tobe-renamed  라벨 변경("수량"→"주문 수량", "저장"→"등록") + 부가세 반올림으로 바뀜.
                                                   라벨 뒤에 숨은 동작 차이까지 도달하는지 보는 용도
python demo-app/legacy_app.py <port> tobe-custom  tobe와 같은 동작, 품목만 커스텀 드롭다운 (div role=combobox + listbox, React/MUI 방식)

as-is 동작 (버그 포함, 전환 시 그대로 유지해야 함)
- 부가세 = 공급가액 × 10%를 10원 단위 절사 (볼펜 1,235원 × 1 → 부가세 120원. 정상 반올림이면 124원)
- 수량 0을 막지 않는다 (빈 값만 alert). 0개 주문이 합계 0원으로 저장된다
- 저장은 confirm("저장하시겠습니까?")을 거친다. 취소하면 입력이 그대로 남는다
"""
from __future__ import annotations

import json
import sys
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

ITEMS = {"notebook": ("노트북", 1_250_000), "mouse": ("마우스", 33_000), "pen": ("볼펜", 1_235)}
VARIANT = "asis"
ORDERS: list[dict] = []


def vat(supply: int) -> int:
    if VARIANT in ("tobe-fixed", "tobe-renamed"):
        return round(supply * 0.1)
    return int(supply * 0.1 // 10 * 10)  # as-is: 10원 단위 절사


def html(title: str, body: str, head: str = "") -> bytes:
    return (f"<!doctype html><html lang='ko'><head><meta charset='utf-8'><title>{title}</title>{head}"
            "<style>body{font-family:sans-serif;margin:16px}td,th{border:1px solid #999;padding:4px 8px}nav a{margin-right:12px}</style>"
            f"</head><body>{body}</body></html>").encode()


def order_form(action: str) -> str:
    fixed = VARIANT == "tobe-fixed"
    renamed = VARIANT == "tobe-renamed"
    qty_label, save_label = ("주문 수량", "등록") if renamed else ("수량", "저장")
    vat_js = "Math.round(s*0.1)" if (fixed or renamed) else "Math.floor(s*0.1/10)*10"
    qty_check = ("if(q===''||Number(q)<1){alert('수량은 1 이상이어야 합니다.');return;}" if fixed
                 else "if(q===''){alert('수량을 입력하세요.');return;}")
    options = "".join(f"<option value='{k}' data-price='{p}'>{n}</option>" for k, (n, p) in ITEMS.items())
    prices = json.dumps({k: p for k, (_, p) in ITEMS.items()})
    js = f"""<script>
var PRICES={prices};
function fmt(n){{return n.toLocaleString('ko-KR');}}
function calc(){{var q=Number(document.f.qty.value||0);
 var s=PRICES[document.f.item.value]*q;var v={vat_js};document.f.supply.value=fmt(s);document.f.vat.value=fmt(v);document.f.total.value=fmt(s+v);}}
function save(){{var q=document.f.qty.value;{qty_check}if(confirm('저장하시겠습니까?')){{document.f.submit();}}}}
</script>"""
    if VARIANT == "asis":  # table 레이아웃, label 대신 title 속성 (레거시에 흔한 형태)
        return js + f"""<h2>주문 등록</h2><form name='f' method='post' action='{action}'><table>
<tr><th>품목</th><td><select name='item' title='품목'>{options}</select></td></tr>
<tr><th>수량</th><td><input name='qty' title='수량' size='5'></td></tr>
<tr><th>공급가액</th><td><input name='supply' title='공급가액' readonly></td></tr>
<tr><th>부가세</th><td><input name='vat' title='부가세' readonly></td></tr>
<tr><th>합계</th><td><input name='total' title='합계' readonly></td></tr>
</table><input type='button' value='계산' onclick='calc()'> <input type='button' value='저장' onclick='save()'></form>"""
    if VARIANT == "tobe-custom":
        lis = "".join(f"<li role='option' data-v='{k}' aria-selected='{str(i == 0).lower()}'>{n}</li>" for i, (k, (n, _)) in enumerate(ITEMS.items()))
        item_field = (f"<div><span id='lbl-item'>품목</span> <div role='combobox' id='cb' tabindex='0' aria-labelledby='lbl-item' "
                      f"aria-expanded='false' aria-controls='lb'>노트북</div><ul role='listbox' id='lb' hidden>{lis}</ul>"
                      "<input type='hidden' name='item' value='notebook'></div>"
                      "<script>var cb=document.getElementById('cb'),lb=document.getElementById('lb');"
                      "cb.onclick=function(){lb.hidden=!lb.hidden;cb.setAttribute('aria-expanded',String(!lb.hidden));};"
                      "lb.querySelectorAll('li').forEach(function(li){li.onclick=function(){cb.textContent=li.textContent;"
                      "document.f.item.value=li.dataset.v;lb.querySelectorAll('li').forEach(function(x){x.setAttribute('aria-selected','false');});"
                      "li.setAttribute('aria-selected','true');lb.hidden=true;cb.setAttribute('aria-expanded','false');};});</script>")
    else:
        item_field = f"<div><label>품목 <select name='item'>{options}</select></label></div>"
    return js + f"""<main><h1>주문 등록</h1><form name='f' method='post' action='{action}'>
{item_field}
<div><label>{qty_label} <input name='qty' type='text' size='5'></label></div>
<div><label>공급가액 <input name='supply' readonly></label></div>
<div><label>부가세 <input name='vat' readonly></label></div>
<div><label>합계 <input name='total' readonly></label></div>
<div><button type='button' onclick='calc()'>계산</button> <button type='button' onclick='save()'>{save_label}</button></div></form></main>"""


def done_body(o: dict) -> str:
    return (f"<p>주문이 저장되었습니다. 주문번호 {o['no']}</p>"
            f"<p>{o['name']} {o['qty']}개, 공급가액 {o['supply']:,}원, 부가세 {o['vat']:,}원, 합계 {o['total']:,}원</p>")


def list_body() -> str:
    rows = "".join(f"<tr><td>{o['no']}</td><td>{o['name']}</td><td>{o['qty']}</td><td>{o['total']:,}</td></tr>" for o in ORDERS)
    return f"<h2>주문 조회</h2><table><tr><th>번호</th><th>품목</th><th>수량</th><th>합계</th></tr>{rows}</table>" + ("" if ORDERS else "<p>주문이 없습니다.</p>")


TOBE_NAV = "<nav aria-label='업무 메뉴'><a href='/app/orders/new'>주문 등록</a><a href='/app/orders'>주문 조회</a></nav>"


class App(BaseHTTPRequestHandler):
    def log_message(self, *_):
        pass

    def _send(self, body: bytes, status=HTTPStatus.OK):
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _redirect(self, location: str):
        self.send_response(HTTPStatus.SEE_OTHER)
        self.send_header("Location", location)
        self.end_headers()

    def _save(self) -> dict:
        n = int(self.headers.get("Content-Length", 0))
        f = {k: v[0] for k, v in parse_qs(self.rfile.read(n).decode()).items()}
        name, price = ITEMS[f.get("item", "pen")]
        qty = int(f.get("qty") or 0)
        supply = price * qty
        o = {"no": len(ORDERS) + 1, "name": name, "qty": qty, "supply": supply, "vat": vat(supply), "total": supply + vat(supply)}
        ORDERS.append(o)
        return o

    def do_GET(self):
        p = urlparse(self.path).path
        if VARIANT == "asis":
            if p == "/":
                return self._send("<!doctype html><html lang='ko'><head><meta charset='utf-8'><title>주문관리</title></head>"
                                  "<frameset rows='50,*'><frame name='menu' src='/menu.jsp'><frame name='main' src='/order.jsp'></frameset></html>".encode())
            if p == "/menu.jsp":
                return self._send(html("메뉴", "<a href='/order.jsp' target='main'>주문 등록</a> | <a href='/list.jsp' target='main'>주문 조회</a>"))
            if p == "/order.jsp":
                return self._send(html("주문 등록", order_form("/save.jsp")))
            if p == "/list.jsp":
                return self._send(html("주문 조회", list_body()))
            if p == "/done.jsp":
                no = int(parse_qs(urlparse(self.path).query).get("no", ["0"])[0])
                return self._send(html("저장 완료", done_body(ORDERS[no - 1])))
        else:
            if p in ("/", "/app/orders/new"):
                return self._send(html("주문관리", TOBE_NAV + order_form("/app/orders")))
            if p == "/app/orders":
                return self._send(html("주문관리", TOBE_NAV + f"<main>{list_body()}</main>"))
            if p.startswith("/app/orders/"):
                return self._send(html("주문관리", TOBE_NAV + f"<main>{done_body(ORDERS[int(p.rsplit('/', 1)[1]) - 1])}</main>"))
        self._send(html("없음", "<p>404</p>"), HTTPStatus.NOT_FOUND)

    def do_POST(self):
        p = urlparse(self.path).path
        if VARIANT == "asis" and p == "/save.jsp":
            return self._redirect(f"/done.jsp?no={self._save()['no']}")
        if VARIANT != "asis" and p == "/app/orders":
            return self._redirect(f"/app/orders/{self._save()['no']}")
        self._send(html("없음", "<p>404</p>"), HTTPStatus.NOT_FOUND)


if __name__ == "__main__":
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8801
    VARIANT = sys.argv[2] if len(sys.argv) > 2 else "asis"
    assert VARIANT in ("asis", "tobe", "tobe-fixed", "tobe-renamed", "tobe-custom"), VARIANT
    print(f"legacy demo ({VARIANT}) on http://127.0.0.1:{port}/", flush=True)
    ThreadingHTTPServer(("127.0.0.1", port), App).serve_forever()
