"""목록 템플릿 인식·분기 열·대표 선택 (parity crawl 의 목록 표본화). 브라우저 없이 스냅샷 텍스트만."""
from parity import lists
from parity.snapshot import parse_elements

TABLE = '''- main:
  - heading "주문 목록" [level=1]
  - button "필터"
  - button "신규 주문"
  - paragraph: 5건
  - table:
    - rowgroup:
      - row "주문 고객 수량 합계 상태":
        - columnheader "주문"
        - columnheader "고객"
        - columnheader "수량"
        - columnheader "합계"
        - columnheader "상태"
      - row "ORD-1 노트북 김철수 1 1,375,000원 접수":
        - cell "ORD-1 노트북":
          - link "ORD-1 노트북":
            - /url: /orders/1
        - cell "김철수"
        - cell "1"
        - cell "1,375,000원"
        - cell "접수"
      - row "ORD-2 볼펜 이영희 4 5,390원 배송중":
        - cell "ORD-2 볼펜":
          - link "ORD-2 볼펜":
            - /url: /orders/2
        - cell "이영희"
        - cell "4"
        - cell "5,390원"
        - cell "배송중"
      - row "ORD-3 마우스 김철수 2 72,600원 접수":
        - cell "ORD-3 마우스":
          - link "ORD-3 마우스":
            - /url: /orders/3
        - cell "김철수"
        - cell "2"
        - cell "72,600원"
        - cell "접수"
      - row "ORD-4 볼펜 박민수 1 1,347원 취소":
        - cell "ORD-4 볼펜":
          - link "ORD-4 볼펜":
            - /url: /orders/4
        - cell "박민수"
        - cell "1"
        - cell "1,347원"
        - cell "취소"
      - row "ORD-5 마우스 이영희 3 108,900원 접수":
        - cell "ORD-5 마우스":
          - link "ORD-5 마우스":
            - /url: /orders/5
        - cell "이영희"
        - cell "3"
        - cell "108,900원"
        - cell "접수"
  - link "다음 페이지":
    - /url: /orders?page=2
'''
MENU = '''- main:
  - heading "바로 가기" [level=2]
  - list:
    - listitem:
      - link "주문 목록 열기":
        - /url: /orders
    - listitem:
      - link "고객 목록 열기":
        - /url: /customers
    - listitem:
      - link "설정 열기":
        - /url: /settings
'''
CARDS = '''- main:
  - heading "공지사항" [level=1]
  - list:
    - listitem:
      - text: 긴급
      - link "서버 점검 안내"
      - text: 2026-09-01
    - listitem:
      - text: 일반
      - link "추석 연휴 운영"
      - text: 2026-09-02
    - listitem:
      - text: 일반
      - link "신규 기능 소개"
      - text: 2026-09-03
'''


def test_table_rows_and_slots_align_with_elements():
    ls, slots = lists.parse_lists(TABLE)
    els = parse_elements(TABLE)
    assert [e.name for e in els] == ["필터", "신규 주문", "ORD-1 노트북", "ORD-2 볼펜", "ORD-3 마우스", "ORD-4 볼펜", "ORD-5 마우스", "다음 페이지"]
    t = ls[0]
    assert t.kind == "table" and t.heading == "주문 목록" and t.headers == ["주문", "고객", "수량", "합계", "상태"] and len(t.rows) == 5
    assert t.rows[1] == ["ORD-2 볼펜", "이영희", "4", "5,390원", "배송중"]
    assert {i: (s.row, s.col) for i, s in slots.items()} == {2: (0, 0), 3: (1, 0), 4: (2, 0), 5: (3, 0), 6: (4, 0)}  # 버튼·다음 페이지는 자리 없음


def test_menu_list_is_not_data():
    ls, slots = lists.parse_lists(MENU)
    assert ls[0].rows == [] and slots == {}  # 항목마다 링크 하나뿐 → 메뉴. 전부 누른다


def test_card_list_fields_and_branch():
    ls, slots = lists.parse_lists(CARDS)
    c = ls[0]
    assert c.kind == "list" and c.rows == [["긴급", "서버 점검 안내", "2026-09-01"], ["일반", "추석 연휴 운영", "2026-09-02"], ["일반", "신규 기능 소개", "2026-09-03"]]
    assert [(s.row, s.col) for s in slots.values()] == [(0, 0), (1, 0), (2, 0)]
    assert lists.branch_columns_rule(c) == [0]  # 긴급/일반. 날짜·제목은 아님
    c.branch_cols = [0]
    assert lists.choose_reps(c, 3) == [0, 1, 2] and lists.choose_reps(c, 2) == [0, 1]  # 긴급·일반 + 남은 예산은 일반의 둘째 행


def test_branch_rule_and_reps():
    t, _ = lists.parse_lists(TABLE)
    t = t[0]
    assert lists.branch_columns_rule(t) == [4]  # 상태만. 주문은 전부 다르고, 고객은 종류(3)가 행 수(5)의 절반을 넘는 이름 열, 수량·합계는 숫자
    t.branch_cols = [4]
    assert lists.choose_reps(t, 3) == [0, 1, 3]  # 접수 · 배송중 · 취소
    assert lists.choose_reps(t, 5) == [0, 1, 2, 3, 4] and lists.choose_reps(t, 4) == [0, 1, 2, 3]  # 남은 예산은 접수의 둘째 행부터
    assert lists.stratum(t, 3) == {"상태": "취소"}
    assert lists.more_rows(t, 0, exclude={0}, limit=2) == [2, 4]  # 접수 층의 다른 행
    assert lists.choose_reps(t, 2) == [0, 1]
    t.branch_cols = []
    assert lists.choose_reps(t, 3) == [0, 4] and lists.choose_reps(t, 1) == [0]


def test_fixture_pick_and_decide():
    t, _ = lists.parse_lists(TABLE)
    t = t[0]
    assert lists.branch_columns_fixture(t, {"주문 목록": ["고객", "상태"]}) == [1, 4]
    assert lists.branch_columns_fixture(t, {"*": ["상태"]}) == [4]
    assert lists.branch_columns_fixture(t, {"고객 목록": ["등급"]}) is None
    lists.decide(t, pick={"주문 목록": ["고객"]}, jev=None, page_title="주문", cache={})
    assert (t.branch_cols, t.source) == ([1], "fixture")
    lists.decide(t, pick=None, jev=None, page_title="주문", cache={})
    assert (t.branch_cols, t.source) == ([4], "rule")


class FakeAnswer:
    def __init__(self, choice, probs):
        self.choice, self.probabilities, self.confidence = choice, probs, max(probs.values())


class FakeResponse:
    def __init__(self, answers):
        self.answers = answers


class FakeJev:
    """열 머리글로 답을 정하는 가짜 Jev: 상태·고객은 branch, 나머지는 key/measure. margin 은 호출자가 정한다."""
    def __init__(self, margin=0.6):
        self.calls = 0
        self.margin = margin

    def ask(self, *, state, questions):
        self.calls += 1
        ans = {}
        for col in state["columns"]:
            branch = col["header"] in ("상태", "고객")
            top = "branch" if branch else "key"
            ans[col["id"]] = FakeAnswer(top, {top: 0.5 + self.margin / 2, "measure": 0.5 - self.margin / 2})
        ans["varies"] = FakeAnswer(None, {"true": 0.8, "false": 0.2})
        return FakeResponse(ans), 12.0


def test_jev_classification_cache_and_gate():
    t, _ = lists.parse_lists(TABLE)
    t = t[0]
    jev, cache = FakeJev(), {}
    lists.decide(t, pick=None, jev=jev, page_title="주문", cache=cache)
    assert (t.branch_cols, t.source, t.margin) == ([1, 4], "jev", 0.6) and jev.calls == 1
    lists.decide(t, pick=None, jev=jev, page_title="주문", cache=cache)
    assert jev.calls == 1  # 같은 목록 서명은 캐시 재생
    assert list(cache.values())[0]["headers"] == t.headers
    low = FakeJev(margin=0.1)
    t2, _ = lists.parse_lists(TABLE)
    t2 = t2[0]
    lists.decide(t2, pick=None, jev=low, page_title="주문", cache={})
    assert t2.source == "rule (jev abstain)" and t2.branch_cols == [4] and t2.margin == 0.1  # margin 미달 → 규칙 + 검토 표시

    class Broken:
        def ask(self, **kw):
            raise RuntimeError("api down")
    t3, _ = lists.parse_lists(TABLE)
    t3 = t3[0]
    c3: dict = {}
    lists.decide(t3, pick=None, jev=Broken(), page_title="주문", cache=c3)
    assert t3.source == "rule (jev abstain)" and t3.branch_cols == [4] and "api down" in list(c3.values())[0]["error"]
    assert lists.branch_columns_fixture(t3, {"*": ["상태"]}) == [4]  # 픽스처는 여전히 최우선
