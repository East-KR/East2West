"""결함 주입(mutation.py)의 자리 찾기·적용·생성: 응답 문자열만으로 검사한다. 브라우저·pytest 서브프로세스는 돌리지 않는다."""
from eastshift.pwtest import mutation as m

HTML = """<html><head><title>주문 등록 2024</title><style>.a{width:120px}</style></head>
<body><h1>주문 등록</h1><input name="qty" value="10" size="5"><span data-price="1,200">단가 1,200원</span>
<p>부가세 120원</p><button onclick="save()">저장</button>
<script>
function calc(q){ var vat = Math.floor(q * 1200 * 0.1); if (q === 0) { alert('수량을 입력하세요.'); return; } if (!confirm("저장할까요?")) return; return vat <= 0 ? 0 : vat; }
</script></body></html>"""


def test_num_sites_skip_layout_numbers_and_title():
    hits = {HTML[s:e] for s, e, _, _ in m.sites(HTML, "num", "document")}
    assert {"1,200", "120", "1200", "0.1", "10"} <= hits  # 표시 금액(원 단위 포함), value=, data-*, 계산 상수
    assert "5" not in hits  # size="5" 레이아웃 속성
    assert "12px" not in {HTML[s:e + 2] for s, e, _, _ in m.sites("<p>a</p><i style='x'>12px</i>", "num", "document")}
    assert "2024" not in hits and "120px" not in hits  # <title>, <style>


def test_apply_changes_exactly_one_site_and_is_deterministic():
    ss = m.sites(HTML, "num", "document")
    for i in range(len(ss)):
        out = m.apply(HTML, "num", i, "document")
        assert out != HTML and len(out) == len(HTML)
        assert sum(a != b for a, b in zip(out, HTML)) == 1  # 마지막 자리 한 글자만
    assert m.apply(HTML, "num", len(ss) + 5, "document") == HTML  # 없는 자리는 그대로 (적용 안 됨 → 생존이 아니라 오류로 셈)
    assert m.apply(HTML, "math", 0, "document").count("Math.ceil") == 1


def test_cond_only_inside_scripts_and_dialog_msg_label():
    cond = m.sites(HTML, "cond", "document")
    assert [HTML[s:e] for s, e, _, _ in cond] == ["===", "<="]
    assert [r for _, _, r, _ in cond] == ["!==", "<"]
    dialog = m.sites(HTML, "dialog", "document")
    assert [r for _, _, r, _ in dialog] == ["void(", "(function(){return true;})("]
    msg = m.sites(HTML, "msg", "document")
    assert [HTML[s:e] for s, e, _, _ in msg] == ["수량을 입력하세요.", "저장할까요?"]
    label = [HTML[s:e] for s, e, _, _ in m.sites(HTML, "label", "document")]
    assert "주문 등록" in label and "저장" in label and "단가" in label
    assert "수량을 입력하세요" not in " ".join(label)  # 스크립트 안 문구는 msg 의 몫


def test_script_kind_treats_whole_body_as_code():
    js = "if (a == b) { x = Math.round(1.5); }"
    assert [HTML_ for _, _, HTML_, _ in m.sites(js, "cond", "script")] == ["!="]
    assert m.sites("[1, 2]", "cond", "xhr") == [] and m.sites('{"n": 7}', "num", "xhr")[0][2] == "8"


def test_route_key_collapses_ids():
    assert m.route_key("/orders/17") == "/orders/{id}" == m.route_key("/orders/3")
    assert m.route_key("/api/x/deadbeefcafe/y") == "/api/x/{id}/y" and m.route_key("") == "/"


def test_generate_spreads_picks_and_honours_oracle_rules():
    bodies = {"/order.jsp": {"kind": "document", "body": HTML}, "/app.js": {"kind": "script", "body": "var a = 1; if (a === 1) {}"}}
    all_ = m.generate(bodies, max_per_op=100)
    ids = [x["id"] for x in all_]
    assert ids == sorted(ids) and len(set(ids)) == len(ids)
    assert sum(x["op"] == "http500" for x in all_) == 1  # 문서 응답만 500
    few = m.generate(bodies, max_per_op=2)
    per = {}
    for x in few:
        per[(x["path"], x["op"])] = per.get((x["path"], x["op"]), 0) + 1
    assert max(per.values()) <= 2
    nums = [x for x in few if x["path"] == "/order.jsp" and x["op"] == "num"]
    assert nums[0]["site"] == 0 and nums[-1]["site"] == len(m.sites(HTML, "num", "document")) - 1  # 처음과 끝을 고르게
    # 마스킹된 값은 비교하지 않으니 그 자리의 결함은 뽑지 않고, 승인된 동등 결함도 뽑지 않는다
    rules = {"ignore": [r"부가세 \d+원"], "equivalent_mutants": [{"path": "/order.jsp", "op": "cond", "context": "⟦===→!==⟧", "reason": "x"}]}
    kept = m.generate(bodies, max_per_op=100, rules=rules)
    assert not any(x["path"] == "/order.jsp" and x["op"] == "cond" and "===" in x["desc"] for x in kept)
    masked = [i for i, (s, e, _, _) in enumerate(m.sites(HTML, "num", "document")) if HTML[s:e] == "120"]
    assert masked and not any(x["path"] == "/order.jsp" and x["op"] == "num" and x["site"] in masked for x in kept)


def test_capture_keeps_first_body_per_route_and_paths_per_test():
    c = m.Capture()
    c.add("t1", "/orders/{id}", "document", "first")
    c.add("t2", "/orders/{id}", "document", "second")
    c.add("t1", "/orders/{id}", "document", "third")
    assert c.bodies["/orders/{id}"]["body"] == "first" and c.tests == {"t1": ["/orders/{id}"], "t2": ["/orders/{id}"]}
