"""e2e/legacy: 주문 화면 as-is/to-be 동등성. 요소 이름은 tools/explore.py 출력 (as-is). 기대값은 as-is 동작 (버그 포함)."""

import pytest

def open_order_form(ui):
    ui.goto("/")
    ui.click("link", "주문 등록")


def test_order_save(ui):
    """볼펜 1개 계산 후 저장 (부가세 10원 절사 동작 보존)"""
    open_order_form(ui)
    ui.select("품목", "볼펜")
    ui.fill("수량", "1")
    ui.click("button", "계산")
    ui.expect_field("부가세", "120")  # as-is: 123.5원 → 10원 단위 절사
    ui.expect_field("합계", "1,355")
    ui.click("button", "저장")
    ui.expect_dialog("저장하시겠습니까?")
    ui.expect_text("주문이 저장되었습니다")
    ui.expect_text("합계 1,355원")


def test_qty_required_alert(ui):
    """수량 없이 저장하면 alert, 저장 안 됨"""
    open_order_form(ui)
    ui.select("품목", "마우스")
    ui.click("button", "저장")
    ui.expect_dialog("수량을 입력하세요.")
    ui.expect_field("수량", "")


def test_save_cancel(ui):
    """저장 확인 창에서 취소하면 입력이 남는다"""
    open_order_form(ui)
    ui.select("품목", "노트북")
    ui.fill("수량", "2")
    ui.dialog("dismiss")
    ui.click("button", "저장")
    ui.expect_dialog("저장하시겠습니까?")
    ui.expect_field("수량", "2")
    ui.expect_field("품목", "노트북")


def test_qty_zero_saved(ui):
    """수량 0은 막지 않고 합계 0원으로 저장된다 (as-is 버그 보존)"""
    open_order_form(ui)
    ui.fill("수량", "0")
    ui.click("button", "저장")
    ui.expect_dialog("저장하시겠습니까?")
    ui.expect_text("노트북 0개")
    ui.expect_text("합계 0원")


@pytest.mark.parametrize("item, qty, supply, vat, total", [
    ("노트북", "2", "2,500,000", "250,000", "2,750,000"),
    ("마우스", "3", "99,000", "9,900", "108,900"),
], ids=["notebook", "mouse"])
def test_order_calc_each_item(ui, item, qty, supply, vat, total):
    """품목별 단가와 10원 절사 (as-is 동작 보존). 결함 주입에서 계산을 거치지 않는 품목 단가가 나와 추가 (볼펜은 test_order_save)"""
    open_order_form(ui)
    ui.select("품목", item)
    ui.fill("수량", qty)
    ui.click("button", "계산")
    ui.expect_field("공급가액", supply)
    ui.expect_field("부가세", vat)
    ui.expect_field("합계", total)
