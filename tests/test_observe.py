"""골든 비교의 정규화(observe.py): 레이아웃 차이는 같다고 보고, 값·대화상자·이름 차이는 잡아야 한다. 브라우저 없이 스냅샷 문자열로 검사한다."""
from east2west.observe import CompareOptions, compare, flatten, mask, observation, relative_url, rename

FRAMESET = """\
## frame top
- navigation "업무 메뉴":
  - link "주문 등록"
## frame main
- heading "주문 등록" [level=1]
- table:
  - row "품목 노트북":
    - cell "품목"
    - cell "노트북"
- textbox "수량": 2
- combobox "품목":
  - option "볼펜"
  - option "노트북" [selected]
- checkbox "조식" [checked]
- text: "|"
- button "저장"
- /url: http://asis/order.jsp
"""

SINGLE_PAGE = """\
- main:
  - list:
    - listitem:
      - link "주문 등록"
  - heading "주문 등록" [level=2]
  - generic "상품 정보":
    - paragraph: 품목
    - paragraph: 노트북
  - textbox "수량": "2"
  - combobox "품목": 노트북
  - checkbox "조식" [checked]
  - button "저장"
- /url: http://tobe/orders/new
"""


def test_flatten_is_layout_insensitive():
    """frameset+table+네이티브 select 와 단일 페이지+div+커스텀 드롭다운이 같은 줄 목록이 된다."""
    a, b = flatten(FRAMESET, keep_urls=False), flatten(SINGLE_PAGE, keep_urls=False)
    assert a == b
    assert a == ["text: 주문 등록"[:0] + 'link "주문 등록"', "text: 주문 등록", "text: 품목", "text: 노트북", 'textbox "수량": 2',
                 'combobox "품목": 노트북', 'checkbox "조식" [checked]', 'button "저장"']


def test_flatten_keeps_url_only_when_asked():
    assert "url: http://asis/order.jsp" in flatten(FRAMESET, keep_urls=True)
    assert not any(l.startswith("url:") for l in flatten(FRAMESET, keep_urls=False))


def test_flatten_drops_container_names_but_keeps_leaf_names():
    lines = flatten('- region "검색 조건":\n  - cell "이름"\n- cell "값"\n', keep_urls=False)
    assert lines == ["text: 이름", "text: 값"]  # 컨테이너 aria-label 은 레이아웃, 잎의 이름은 내용


def test_flatten_native_select_without_selection_keeps_empty_value():
    lines = flatten('- combobox "품목":\n  - option "볼펜"\n  - option "노트북"\n- button "저장"\n', keep_urls=False)
    assert lines == ['combobox "품목":', 'button "저장"']


def test_mask_and_rename():
    lines = ["text: 주문번호 1234 저장", 'textbox "수량": 3', "text: 수량", "text: 수량을 입력하세요"]
    assert mask(lines, [r"주문번호 \d+"])[0] == "text: <masked> 저장"
    out = rename(lines, {"수량": "주문 수량"})
    assert out[1] == 'textbox "주문 수량": 3' and out[2] == "text: 주문 수량"
    assert out[3] == "text: 수량을 입력하세요"  # 문장 속 단어는 그대로


def _obs(snapshot: str, dialogs=(), url="http://x/a?b=1", title="t", ignore=()):
    return observation(index=0, kind="click", text="저장", url=url, title=title, snapshot=snapshot, dialogs=list(dialogs),
                       opts=CompareOptions(ignore=list(ignore)))


def test_observation_stores_raw_snapshot_and_masked_content():
    o = _obs("- text: 주문번호 77 완료\n", ignore=[r"주문번호 \d+"])
    assert o["snapshot"].startswith("- text: 주문번호 77") and o["content"] == ["text: <masked> 완료"]
    assert o["url"] == "/a?b=1" and relative_url("http://h:1/p/q?x=1#f") == "/p/q?x=1"


def test_compare_same_layout_different_is_equal():
    g, a = _obs(FRAMESET), _obs(SINGLE_PAGE)
    assert compare(g, a, CompareOptions()) == []


def test_compare_reports_value_and_dialog_changes():
    g = _obs('- textbox "부가세": 120\n', dialogs=[{"type": "alert", "message": "수량을 입력하세요.", "action": "accept"}])
    a = _obs('- textbox "부가세": 124\n', dialogs=[{"type": "alert", "message": "수량은 1 이상이어야 합니다.", "action": "accept"}])
    out = compare(g, a, CompareOptions())
    assert any(l.startswith("dialogs:") for l in out)
    assert any(l.startswith('-textbox "부가세": 120') for l in out) and any(l.startswith('+textbox "부가세": 124') for l in out)


def test_compare_applies_ignore_to_both_sides_at_compare_time():
    """골든에 원문이 있으므로 기록 뒤에 규칙을 바꿔도 다시 기록할 필요가 없다."""
    g, a = _obs("- text: 주문번호 12 완료\n"), _obs("- text: 주문번호 99 완료\n")
    assert compare(g, a, CompareOptions()) != []
    assert compare(g, a, CompareOptions(ignore=[r"주문번호 \d+"])) == []


def test_compare_name_map_absorbs_intended_relabel_only():
    g, a = _obs('- textbox "수량": 2\n- textbox "부가세": 120\n'), _obs('- textbox "주문 수량": 2\n- textbox "부가세": 124\n')
    out = compare(g, a, CompareOptions(), name_map={"수량": "주문 수량"})
    assert not any("수량" in l and "주문 수량" not in l for l in out if l.startswith(("-", "+")))
    assert any("부가세" in l for l in out)  # 라벨은 흡수되고 값 변경은 남는다


def test_compare_url_and_title_only_when_enabled():
    g, a = _obs("- text: x\n", url="http://asis/a.jsp", title="A"), _obs("- text: x\n", url="http://tobe/a", title="B")
    assert compare(g, a, CompareOptions()) == []
    out = compare(g, a, CompareOptions(url=True))
    assert any(l.startswith("url:") for l in out) and any(l.startswith("title:") for l in out)


def test_compare_unordered_and_legacy_golden_without_snapshot():
    g, a = _obs("- text: a\n- text: b\n"), _obs("- text: b\n- text: a\n")
    assert compare(g, a, CompareOptions()) != [] and compare(g, a, CompareOptions(unordered=True)) == []
    old = {k: v for k, v in g.items() if k != "snapshot"}  # 원문이 없는 옛 골든은 저장된 content 로 비교
    assert compare(old, _obs("- text: a\n- text: b\n"), CompareOptions()) == []


def test_compare_flags_step_text_change():
    g = _obs("- text: x\n")
    a = dict(_obs("- text: x\n"), text="등록")
    assert compare(g, a, CompareOptions())[0].startswith("! step text changed")


def _reqs(*reqs: str, snapshot: str = "- text: x\n"):
    return observation(index=0, kind="click", text="조회", url="http://x/a", title="t", snapshot=snapshot, dialogs=[],
                       opts=CompareOptions(), requests=list(reqs))


def test_compare_flags_a_shared_request_called_a_different_number_of_times():
    """요청 횟수도 동작이다: as-is 가 한 번 누름에 목록을 두 번 읽으면 to-be 도 두 번이어야 한다."""
    g = _reqs("GET /orders/list", "GET /orders/list", "GET /codes")
    assert compare(g, _reqs("GET /orders/list", "GET /codes"), CompareOptions()) == ["requests: GET /orders/list ×2 → ×1"]
    assert compare(g, _reqs("GET /codes", "GET /orders/list", "GET /orders/list"), CompareOptions()) == []


def test_request_counts_skip_one_sided_paths_old_goldens_and_ignored_paths():
    g = _reqs("GET /legacy/list.do", "GET /poll", "GET /poll")
    assert compare(g, _reqs("GET /api/list", "GET /api/list", "GET /poll"), CompareOptions(request_ignore=[r"^/poll$"])) == []  # API 가 바뀐 주소는 한쪽만 부른 것
    assert compare({k: v for k, v in g.items() if k != "requests"}, _reqs("GET /poll"), CompareOptions()) == []  # 요청을 기록하지 않은 옛 골든
    assert observation(index=0, kind="goto", text="/", url="http://x/", title="", snapshot="", dialogs=[], opts=CompareOptions()).get("requests") is None


def test_request_counts_follow_the_address_map():
    g = _reqs("GET /sys/UserList/show.do", "GET /sys/UserList/show.do")
    a = _reqs("GET /api/v1/sys/UserList/show")
    nm = {"/sys/{screen}/show.do": "/api/v1/sys/{screen}/show"}
    assert compare(g, a, CompareOptions(), name_map=nm) == ["requests: GET /api/v1/sys/UserList/show ×2 → ×1"]
    assert compare(g, a, CompareOptions()) == []  # 매핑이 없으면 다른 주소라 보지 않는다
