"""Screen Map 오프라인 테스트: 라우트 합치기, 화면 안의 상태 분류, 시작 화면. 저장소의 골든만 읽는다."""
from pathlib import Path

import pytest

from east2west.pwtest import map as screen_map

GOLDEN = Path("golden")


def test_route_and_state_helpers():
    assert screen_map._route("http://x/orders/17?status=a") == "/orders/{id}"
    assert screen_map._route("http://x/") == "/"
    assert screen_map._route("/customers/0f1e2d3c4b5a6978/memo") == "/customers/{id}/memo"
    snap = '- heading "주문 목록"\n- dialog "신규 주문":\n  - textbox "수량"'
    assert screen_map._state(snap)[:2] == ("dialog", "신규 주문")
    assert screen_map._state('- complementary "필터"')[:2] == ("drawer", "필터")
    assert screen_map._state('- tab "기본 정보" [selected]\n- tab "이력"', base_tab="기본 정보")[:2] == ("base", "")
    assert screen_map._state('- tab "이력" [selected]', base_tab="기본 정보")[:2] == ("tab", "이력")
    assert screen_map._state('- alert: 저장되었습니다.')[:2] == ("alert", "저장되었습니다.")


def test_approved_difference_is_not_displayed_as_identical():
    g = {"routes": {"/orders": {"path": "/orders", "tests": ["test_order"], "failed": False,
                                  "accepted": True, "shot": ""}},
         "tests": [{"name": "test_order", "status": "accepted"}], "compared": True, "shots": {}}
    screen_map._annotate(g, None, set())
    assert g["routes"]["/orders"]["status"] == "accepted"


@pytest.mark.skipif(not (GOLDEN / "portal").is_dir(), reason="portal 골든 없음")
def test_portal_map_two_levels():
    g = screen_map.build(GOLDEN / "portal", tests_dir=Path("e2e/portal"))
    assert g["start"] == "/"
    orders = g["routes"]["/orders"]
    kinds = {g["nodes"][s]["kind"] for s in orders["states"]}
    assert {"base", "drawer", "dialog"} <= kinds
    detail = g["routes"]["/orders/{id}"]
    assert "tab" in {g["nodes"][s]["kind"] for s in detail["states"]}
    assert all(e["src"] != e["dst"] for e in g["redges"])
    page = screen_map.render(g)
    assert "화면 ${G.unit}" in page and "/customers/{id}" in page
    assert "이 화면 안의 상태" not in page and "시작에서 오는 길" not in page
    assert "여기서 갈 수 있는 곳" not in page


@pytest.mark.skipif(not (GOLDEN / "legacy").is_dir(), reason="legacy 골든 없음")
def test_frameset_app_splits_routes_by_heading():
    """주소가 항상 /인 frameset 앱: 제목이 다른 기본 화면(주문 등록, 주문관리)은 다른 라우트"""
    g = screen_map.build(GOLDEN / "legacy", tests_dir=Path("e2e/legacy"))
    assert len(g["routes"]) == 2 and all(r["path"] == "/" for r in g["routes"].values())
    assert {"주문 등록", "주문관리"} <= {r["name"] for r in g["routes"].values()}
    assert g["redges"] and g["routes"][g["start"]]["name"] == "주문 등록"


@pytest.mark.skipif(not (GOLDEN / "reservation").is_dir(), reason="reservation 골든 없음")
def test_reservation_map_starts_where_tests_enter():
    g = screen_map.build(GOLDEN / "reservation", tests_dir=Path("e2e/reservation"))
    assert g["start"] == "/login"
    assert "/reservations/{id}" in g["routes"]


def test_route_key_shared_with_mutation():
    from east2west.pwtest.mutation import route_key
    assert route_key("/orders/9") == "/orders/{id}" == screen_map._route("http://x/orders/4")
    assert route_key("/") == "/" and route_key("/orders") == "/orders"


def test_map_from_crawl_graph(tmp_path):
    """탐색 결과(graph.json)로도 같은 지도가 그려진다: 경로 = 전이를 한 번 이상 지나는 최대 경로, 상태 캡처는 경로들이 나눠 쓴다, 단위는 '경로'."""
    import json
    shots = tmp_path / "screens"
    shots.mkdir()
    for i in range(3):
        (shots / f"n{i}.png").write_bytes(b"\x89PNG\r\n\x1a\n")
    snap = lambda h, extra="": f'- banner:\n  - link "홈"\n- main:\n  - heading "{h}" [level=1]\n  - button "신규"\n{extra}'  # noqa: E731
    nodes = [{"id": 0, "sig": "a", "filled": False, "url": "http://x/", "loc": "/", "title": "홈", "path": [], "headings": ["홈"], "alerts": [], "modal": "", "texts": [], "missing_inputs": [], "snapshot": snap("홈"), "explored": True, "screenshot": str(shots / "n0.png"), "label": "홈"},
             {"id": 1, "sig": "b", "filled": False, "url": "http://x/orders", "loc": "/orders", "title": "주문", "path": [0], "headings": ["주문 목록"], "alerts": [], "modal": "", "texts": [], "missing_inputs": [], "snapshot": snap("주문 목록"), "explored": True, "screenshot": str(shots / "n1.png"), "label": "주문 목록"},
             {"id": 2, "sig": "c", "filled": False, "url": "http://x/orders", "loc": "/orders", "title": "주문", "path": [0, 1], "headings": ["주문 목록"], "alerts": [], "modal": "신규 주문", "texts": [], "missing_inputs": [], "snapshot": snap("주문 목록", '  - dialog "신규 주문":\n    - textbox "수량"'), "explored": True, "screenshot": str(shots / "n2.png"), "label": "신규 주문"}]
    el = lambda role, name: {"role": role, "name": name, "order": 0, "nth": 0, "dup": 1, "scope": "", "frame": ""}  # noqa: E731
    edges = [{"id": 0, "src": 0, "steps": [{"action": "click", **el("link", "주문 관리")}], "mode": "as-is", "kind": "transition", "dst": 1, "group": 1, "preset_len": 0, "preset_sets": {}, "sets": {}, "url_after": "http://x/orders", "dialogs": [], "js_errors": [], "http_errors": [], "reason": "", "sentence": '링크 "주문 관리" 누르기'},
             {"id": 1, "src": 1, "steps": [{"action": "click", **el("button", "신규")}], "mode": "as-is", "kind": "transition", "dst": 2, "group": 1, "preset_len": 0, "preset_sets": {}, "sets": {}, "url_after": "http://x/orders", "dialogs": [{"type": "alert", "message": "열림"}], "js_errors": [], "http_errors": [], "reason": "", "sentence": '버튼 "신규" 누르기'},
             {"id": 2, "src": 1, "steps": [{"action": "click", **el("link", "홈")}], "mode": "as-is", "kind": "local", "dst": None, "group": 1, "preset_len": 0, "preset_sets": {}, "sets": {}, "url_after": "", "dialogs": [], "js_errors": [], "http_errors": [], "reason": "", "sentence": '링크 "홈" 누르기'}]
    out = tmp_path / "crawl-shop"
    out.mkdir()
    (out / "graph.json").write_text(json.dumps({"start": "/", "start_url": "http://x/", "inputs": [], "deny": "", "nodes": nodes, "edges": edges}), encoding="utf-8")
    recs = screen_map._records_from_crawl(out)
    assert [r["name"] for r in recs] == ["crawl_01"] and recs[0]["title"] == "홈 → 주문 목록 → 신규 주문"  # 0→1→2 한 경로가 0→1을 품는다
    assert [s["kind"] for s in recs[0]["steps"]] == ["goto", "act", "act"] and recs[0]["steps"][2]["dialogs"] == [{"type": "alert", "message": "열림", "action": "accept"}]
    g = screen_map.build_from_crawl("shop", out, "tobe")
    assert g["unit"] == "경로" and g["source"]["label"] == "to-be 탐색" and g["start"] == "/"
    assert set(g["routes"]) == {"/", "/orders"} and g["routes"]["/orders"]["kinds"] == {"dialog": 1}
    assert len(g["shots"]) == 3 and all(k.endswith(".png") for k in g["shots"])  # 파일 하나 = 항목 하나
    page = screen_map.render(g)
    assert "to-be 탐색" in page and "경로 찾기" in page and "id='pl'" in page and "id='detail'" in page  # 셀렉트 대신 입력형 경로 고르기
    assert "id='fsb'" in page and "id='fsbar'" in page  # 전체화면 버튼과 떠 있는 막대


def _graph(tmp_path, name, urls, dead=()):
    """탐색 그래프 흉내: 시작 노드에서 urls 각각으로 가는 전이 하나씩. dead 에 든 주소는 404 로 닿는 죽은 링크."""
    import json
    d = tmp_path / name
    (d / "screens").mkdir(parents=True)
    nodes, edges = [], []
    for i, (url, title) in enumerate([("http://x/", "포털 홈")] + urls):  # 시작 화면 제목은 골든과 같게 (제목이 다르면 다른 화면으로 가른다)
        (d / "screens" / f"n{i}.png").write_bytes(b"\x89PNG\r\n\x1a\n")
        nodes.append({"id": i, "sig": f"s{i}", "filled": False, "url": url, "loc": url, "title": title, "path": [] if i == 0 else [i - 1], "headings": [title], "alerts": [], "modal": "",
                      "texts": [], "missing_inputs": [], "snapshot": f'- heading "{title}" [level=1]\n- button "b{i}"', "explored": True, "screenshot": str(d / "screens" / f"n{i}.png"), "label": title})
        if i:
            edges.append({"id": i - 1, "src": 0, "steps": [{"action": "click", "role": "link", "name": title, "order": 0, "nth": 0, "dup": 1, "scope": "", "frame": ""}], "mode": "as-is",
                          "kind": "transition", "dst": i, "group": 1, "preset_len": 0, "preset_sets": {}, "sets": {}, "url_after": url, "dialogs": [], "js_errors": [], "http_errors": [f"404 {url}"] if url in dead else [], "reason": "", "sentence": ""})
    (d / "graph.json").write_text(json.dumps({"start": "/", "start_url": "http://x/", "inputs": [], "deny": "", "nodes": nodes, "edges": edges}), encoding="utf-8")
    return d


@pytest.mark.skipif(not (GOLDEN / "portal").is_dir(), reason="portal 골든 없음")
def test_compare_map_statuses(tmp_path):
    """as-is 기준 to-be 비교: to-be 탐색에 없는 화면 = 미개발, to-be에만 있는 화면 = 새 화면, 캡처는 to-be 우선(미개발만 as-is). as-is 탐색만 있는 화면도 잇는다."""
    asis = _graph(tmp_path, "crawl-asis", [("http://x/orders", "주문 목록"), ("http://x/help", "도움말")])   # /help: 시나리오가 닿지 않은 as-is 화면
    tobe = _graph(tmp_path, "crawl-tobe", [("http://x/orders", "주문 목록"), ("http://x/customers", "고객 목록"), ("http://x/reports", "보고서"), ("http://x/settings", "없음")], dead={"http://x/settings"})  # /settings 는 죽은 링크(404) → 없는 것, /reports 새로
    g = screen_map.build(GOLDEN / "portal", None, Path("e2e/portal"), asis, tobe)
    st = {r["path"]: r["status"] for r in g["routes"].values()}
    assert st["/reports"] == "new" and st["/settings"] == "undeveloped" and st["/help"] == "undeveloped" and st["/orders"] == "untested"  # 비교 실행 없음 → 같음 판정 없음
    assert st["/customers/{id}"] == "undeveloped"  # to-be 탐색에 없는 상세 화면
    r = {x["path"]: x for x in g["routes"].values()}
    assert r["/orders"]["shot"] == r["/orders"]["tobe_shot"] and r["/orders"]["tobe_shot"].endswith("crawl-tobe/screens/n1.png") and r["/orders"]["asis_shot"].startswith(str(GOLDEN / "portal"))
    assert r["/settings"]["shot"] == r["/settings"]["asis_shot"] and not r["/settings"]["tobe_shot"]
    assert r["/reports"]["shot"] == r["/reports"]["tobe_shot"] and not r["/reports"]["asis_shot"]
    assert g["counts"] == {"new": 1, "undeveloped": 4, "untested": 3}  # 미개발: /orders/{id} /customers/{id} /settings(404) /help · 비교 안 됨: / /orders /customers
    assert not any(r["path"] == "/settings" and r["id"] != "/settings" for r in g["routes"].values())  # 404 페이지가 다른 화면으로 끼어들지 않는다
    assert [t["side"] for t in g["tests"] if t["name"].startswith("asis-crawl")] == ["asis"] and any(t["name"].startswith("tobe-crawl") for t in g["tests"])
    page = screen_map.render(g)
    assert "to-be 탐색에서 미발견" in page and "to-be에서만 발견" in page and "node new" in page and "node undev" in page
    # to-be 탐색이 없으면 미개발·새 화면 판정 없음, 캡처는 as-is
    g2 = screen_map.build(GOLDEN / "portal", None, Path("e2e/portal"), None, None)
    assert set(r["status"] for r in g2["routes"].values()) == {"untested"} and all(r["shot"] == r["asis_shot"] for r in g2["routes"].values())


@pytest.mark.skipif(not (GOLDEN / "portal").is_dir(), reason="portal 골든 없음")
def test_map_has_detail_page():
    """화면을 누르면 옆 패널이 아니라 상세 페이지(#<라우트>)로 넘어간다: 지도와 상세가 따로 있고, 옆 패널은 없다."""
    page = screen_map.render(screen_map.build(GOLDEN / "portal", None, Path("e2e/portal")))
    assert "id='overview'" in page and "id='detail'" in page and "hidden></section>" in page
    assert "id='side'" not in page and "closeSide" not in page and "hashchange" in page


def test_path_name_map_in_goto_and_label_rename():
    """name_map 의 '/'로 시작하는 키 = 주소 매핑: goto 는 to-be 주소를 열고 단계 글은 as-is 주소 그대로. 라벨 이름 바꾸기(observe.rename)에는 끼지 않는다."""
    from types import SimpleNamespace

    from east2west.observe import rename
    from east2west.pwtest.ui import UI, path_pairs, tobe_path
    m = {"/sys/UserList/index.do": "/sys/user-list", "/orders/{id}": "/order/{id}/view", "수량": "주문 수량"}
    assert path_pairs(m) == [("/sys/UserList/index.do", "/sys/user-list"), ("/orders/{id}", "/order/{id}/view")]
    assert tobe_path("/sys/UserList/index.do?x=1#h", m) == "/sys/user-list?x=1#h" and tobe_path("/orders/17", m) == "/order/17/view"
    assert tobe_path("/orders/17/memo", m) == "/orders/17/memo" and tobe_path("/orders", m) == "/orders"  # 한 단계만, 맞는 것이 없으면 그대로
    assert rename(['button "/orders/{id}"', 'textbox "수량"'], m) == ['button "/orders/{id}"', 'textbox "주문 수량"']

    class Page:
        went: list[str] = []
        context = SimpleNamespace(on=lambda *a, **k: None)

        def on(self, *a, **k):
            pass

        def goto(self, url):
            self.went.append(url)

    page, steps = Page(), []
    u = UI(page, base_url="http://tobe:1", test_id="t", name_map=m)
    u._after = lambda kind, text: steps.append((kind, text))
    u.goto("/sys/UserList/index.do?x=1")
    assert page.went == ["http://tobe:1/sys/user-list?x=1"] and steps == [("goto", "/sys/UserList/index.do?x=1")]


def _portal_copy(tmp_path, name_map=None):
    """골든 portal 을 읽기만 해서 임시 폴더에 복제한다 (이름 매핑을 더해 보려고. 저장소 골든은 건드리지 않는다)."""
    import json
    import shutil
    d = tmp_path / "golden" / "portal"
    shutil.copytree(GOLDEN / "portal", d)
    if name_map:
        (d / "name_map.tobe.json").write_text(json.dumps(name_map, ensure_ascii=False), encoding="utf-8")
    return d


@pytest.mark.skipif(not (GOLDEN / "portal").is_dir(), reason="portal 골든 없음")
def test_compare_map_translates_tobe_crawl_routes_by_path_name_map(tmp_path):
    """주소가 바뀐 to-be 화면: 이름 매핑의 주소 항목을 뒤집어 to-be 탐색 라우트를 as-is 라우트로 바꿔 같은 화면으로 잇는다 (미개발로도, 새 화면으로도 세지 않는다)."""
    d = _portal_copy(tmp_path, {"/orders": "/order-list", "/customers/{id}": "/client/{no}", "수량": "주문 수량"})
    tobe = _graph(tmp_path, "crawl-tobe", [("http://x/order-list", "주문 목록"), ("http://x/client/7", "고객 상세"), ("http://x/reports", "보고서")])
    g = screen_map.build(d, None, Path("e2e/portal"), None, tobe)
    st = {r["path"]: r["status"] for r in g["routes"].values()}
    assert st["/orders"] == "untested" and st["/customers/{id}"] == "untested" and st["/reports"] == "new"
    assert "/order-list" not in st and "/client/{id}" not in st and st["/customers"] == "undeveloped"
    r = {x["path"]: x for x in g["routes"].values()}
    assert r["/orders"]["tobe_shot"].endswith("crawl-tobe/screens/n1.png") and r["/orders"]["asis_shot"].startswith(str(d))


def _run_with_captures(tmp_path, d, tobe_steps):
    """원장 기록 흉내: test_portal_16 의 to-be 캡처는 tobe_steps 단계에만 (나머지는 to-be 가 가지 못한 단계)."""
    shots = tmp_path / "shots"
    shots.mkdir()
    got = {}
    for i in tobe_steps:
        (shots / f"test_portal_16-tobe-{i:02d}.jpg").write_bytes(b"\xff\xd8")
        got[str(i)] = str(shots / f"test_portal_16-tobe-{i:02d}.jpg")
    junit = tmp_path / "junit.xml"
    junit.write_text('<testsuite name="pytest"><testcase classname="t" name="test_portal_01"><properties><property name="east2west_id" value="test_portal_01"/></properties></testcase>'
                     '<testcase classname="t" name="test_portal_16"><properties><property name="east2west_id" value="test_portal_16"/></properties>'
                     '<failure message="x">step 3 link "주문 보기": differs from golden</failure></testcase>'
                     '<testcase classname="t" name="test_portal_03"><properties><property name="east2west_id" value="test_portal_03"/>'
                     '<property name="east2west_accepted_differences" value=\'[{"step": 3, "sha256": "y", "reason": "탭 이름 변경 승인"}]\'/></properties></testcase></testsuite>',
                     encoding="utf-8")
    run = {"_stamp": "20261005-000000", "cases": {"test_portal_16": {"status": "fail", "kind": "golden_diff", "rows": [], "tobe_shots": got},
                                                  "test_portal_01": {"status": "pass", "kind": "same", "rows": []}}}
    return junit, run


@pytest.mark.skipif(not (GOLDEN / "portal").is_dir(), reason="portal 골든 없음")
def test_compare_map_uses_run_captures_and_reached_routes(tmp_path):
    """그 실행의 단계별 to-be 캡처: 시나리오 필름은 단계마다 as-is · to-be (못 간 단계는 빈 자리), 화면 캡처의 to-be 쪽은 탐색 캡처보다 비교 캡처가 먼저.
    to-be 탐색이 못 찾은 화면이라도 그 실행에서 to-be 가 닿았으면(캡처가 있거나 그 화면을 지나는 시나리오가 통과) 미개발로 두지 않는다."""
    d = _portal_copy(tmp_path, {"/": "/main"})
    junit, run = _run_with_captures(tmp_path, d, (0, 1, 2))
    tobe = _graph(tmp_path, "crawl-tobe", [("http://x/settings", "설정")])  # to-be 탐색은 홈과 설정만 찾았다
    g = screen_map.build(d, junit, Path("e2e/portal"), None, tobe, run=run)
    st = {r["path"]: r["status"] for r in g["routes"].values()}
    assert st["/customers/{id}"] == "same"   # 탐색은 못 찾았지만 test_portal_16 이 to-be 에서 캡처를 남겼다
    assert st["/orders"] == "diff"            # test_portal_01 이 to-be 에서 끝까지 통과했다 → 개발됨, test_portal_16 이 다름
    assert st["/orders/{id}"] == "undeveloped" and st["/settings"] == "accepted"  # 아무도 닿지 못함 · 비교 제외(보라)
    r = {x["path"]: x for x in g["routes"].values()}
    assert r["/customers/{id}"]["tobe_shot"].endswith("test_portal_16-tobe-02.jpg") and r["/customers/{id}"]["shot"] == r["/customers/{id}"]["tobe_shot"]
    t16 = next(t for t in g["tests"] if t["name"] == "test_portal_16")
    assert [Path(s["tobe"]).name if s["tobe"] else "" for s in t16["seq"]] == [f"test_portal_16-tobe-{i:02d}.jpg" for i in range(3)] + ["", ""]
    assert t16["seq"][0]["tobe_action"] == "열기 /main" and t16["seq"][1]["tobe_action"] == ""  # 주소가 바뀐 열기 단계는 to-be 주소로
    assert all("tobe" not in s for t in g["tests"] if t["name"] != "test_portal_16" for s in t["seq"])  # 캡처가 없는 시나리오는 as-is만
    t03 = next(t for t in g["tests"] if t["name"] == "test_portal_03")
    assert t03["status"] == "accepted" and t03["reason"] == "탭 이름 변경 승인" and g["run"] == "20261005-000000"
    page = screen_map.render(g)
    assert "<i class='accepted'></i>비교 제외" in page and "pill accepted" in page and ".node.accepted{border-color:var(--accepted)}" in page
    assert "function pairFilm" in page and "film pair" in page
