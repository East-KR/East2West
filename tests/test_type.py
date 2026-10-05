"""입력 중 동작: ui.type 은 사람처럼 쳐서 키·벗어남 검사를 일으키고, 칸의 오류 상태를 관찰 줄로 붙인다 (aria · ExtJS 4)."""
from playwright.sync_api import sync_playwright

from east2west.observe import CompareOptions, compare, observation
from east2west.pwtest.ui import UI

HTML = """<label>코드 <input id=code aria-describedby=h1></label><div id=h1></div>
<label>이름 <input id=nm-inputEl></label>
<script>
const code = document.getElementById('code');
code.addEventListener('keyup', () => { code.value = code.value.toUpperCase(); });
code.addEventListener('blur', () => { const bad = code.value.length > 3; code.setAttribute('aria-invalid', bad);
  document.getElementById('h1').textContent = bad ? '영문 3자를 초과할 수 없습니다.' : ''; });
let err = '';
window.Ext = {getCmp: id => id === 'nm' ? {getActiveError: () => err} : null};
const nm = document.getElementById('nm-inputEl');
nm.addEventListener('blur', () => { err = nm.value ? '' : '<ul><li>값을 입력해주세요.</li><li>둘째 줄</li></ul>'; });
</script>"""


def test_type_keys_leave_and_field_errors():
    with sync_playwright() as p:
        b = p.chromium.launch()
        try:
            pg = b.new_page()
            pg.set_content(HTML)
            u = UI(pg, base_url="http://127.0.0.1:1", test_id="t")
            u._settle = lambda: pg.locator("body").aria_snapshot()
            seen = []
            u._after = lambda kind, text, extra=None: seen.append((kind, text, list(extra()) if extra else []))
            u.type("코드", "abcd")
            assert pg.locator("#code").input_value() == "ABCD"  # keyup 마스크가 돌았다 (fill 이면 안 돈다)
            assert seen[-1] == ("type", 'textbox "코드" ⌨ abcd + Tab', ["- text: 입력 오류 · 코드: 영문 3자를 초과할 수 없습니다."])
            u.type("코드", "ab")
            assert seen[-1][2] == []
            u.type("이름", "")  # ExtJS: getActiveError 의 목록을 한 줄로
            assert seen[-1][2] == ["- text: 입력 오류 · 이름: 값을 입력해주세요. 둘째 줄"]
            del u._after
            u.type("코드", "abcd")
            u.expect_field_error("코드", "영문 3자를 초과할 수 없습니다.")
            u.type("코드", "ab")
            u.expect_field_error("코드", "")
            assert [a["kind"] for a in u.assertions] == ["field_error", "field_error"]
        finally:
            b.close()


def test_field_error_line_is_compared_like_screen_content():
    o = CompareOptions()
    snap = '- textbox "코드": ABCD'
    asis = observation(index=1, kind="type", text="t", url="http://a/x", title="", snapshot=snap + "\n- text: 입력 오류 · 코드: 영문 3자를 초과할 수 없습니다.", dialogs=[], opts=o)
    tobe = observation(index=1, kind="type", text="t", url="http://b/x", title="", snapshot=snap, dialogs=[], opts=o)
    assert any("입력 오류 · 코드" in line for line in compare(asis, tobe, o))
    assert compare(asis, asis, o) == []
