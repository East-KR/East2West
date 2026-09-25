"""화면 지도 오프라인 테스트: 라우트 합치기, 화면 안의 상태 분류, 시작 화면. 저장소의 골든만 읽는다."""
from pathlib import Path

import pytest

from parity.pwtest import map as screen_map

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


@pytest.mark.skipif(not (GOLDEN / "reservation").is_dir(), reason="reservation 골든 없음")
def test_reservation_map_starts_where_tests_enter():
    g = screen_map.build(GOLDEN / "reservation", tests_dir=Path("e2e/reservation"))
    assert g["start"] == "/login"
    assert "/reservations/{id}" in g["routes"]
