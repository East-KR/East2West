"""화면 지도 오프라인 테스트: 라우트 합치기, 화면 안의 상태 분류, 시작 화면. 저장소의 골든만 읽는다."""
from pathlib import Path

import pytest

from eastshift.pwtest import map as screen_map

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
    assert "이 화면 안의 상태" in page and "/customers/{id}" in page


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
    from eastshift.pwtest.mutation import route_key
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
    assert "to-be 탐색" in page and "모든 경로" in page and "id='detail'" in page


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
