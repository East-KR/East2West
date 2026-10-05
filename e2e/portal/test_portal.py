"""화면 지도 데모 포털(demo-app/portal_app.py)의 as-is 흐름. east2west crawl 초안 176개 중 팝업·드로워·탭·확인창을 지나는 경로 18개를 골랐다.

as-is 동작 보존: 부가세 10원 절사, 이미 취소된 주문도 다시 취소(이력에 두 번). to-be(tobe 모드)는 둘 다 "고쳐서" 비교에서 잡혀야 한다.
"""
import pytest

from east2west.runner import _expand


def test_portal_01(ui):
    """포털 홈 → 주문 목록 → 포털 홈"""
    ui.goto('/')
    ui.expect_title('홈')
    ui.act('link', '주문 관리')
    ui.expect_url_path('/orders')
    ui.expect_text('김철수')
    ui.expect_no_text('포털 홈')
    ui.act('link', '홈')
    ui.expect_url_path('/')
    ui.expect_text('포털 홈')
    ui.expect_no_text('김철수')

def test_portal_02(ui):
    """포털 홈 → 주문 목록 → 주문 목록 → 포털 홈"""
    ui.goto('/')
    ui.expect_title('홈')
    ui.act('link', '주문 관리')
    ui.expect_url_path('/orders')
    ui.expect_text('김철수')
    ui.expect_no_text('포털 홈')
    ui.act('button', '필터')
    ui.act('link', '홈')
    ui.expect_url_path('/')
    ui.expect_text('포털 홈')
    ui.expect_no_text('필터')

def test_portal_03(ui):
    """포털 홈 → 주문 목록 → 주문 목록 → 설정"""
    ui.goto('/')
    ui.expect_title('홈')
    ui.act('link', '주문 관리')
    ui.expect_url_path('/orders')
    ui.expect_text('김철수')
    ui.expect_no_text('포털 홈')
    ui.act('button', '필터')
    ui.act('link', '설정')
    ui.expect_url_path('/settings')
    ui.expect_text('회사명')
    ui.expect_no_text('주문 목록')

def test_portal_04(ui):
    """포털 홈 → 주문 목록 → 주문 목록 → 신규 주문 → 신규 주문"""
    ui.goto('/')
    ui.expect_title('홈')
    ui.act('link', '주문 관리')
    ui.expect_url_path('/orders')
    ui.expect_text('김철수')
    ui.expect_no_text('포털 홈')
    ui.act('button', '필터')
    ui.act('button', '신규 주문')
    ui.expect_text('품목')
    ui.act('option', '전체')

def test_portal_05(ui):
    """포털 홈 → 주문 목록 → 주문 목록 → 신규 주문 → 주문 상세 ORD-1"""
    ui.goto('/')
    ui.expect_title('홈')
    ui.act('link', '주문 관리')
    ui.expect_url_path('/orders')
    ui.expect_text('김철수')
    ui.expect_no_text('포털 홈')
    ui.act('button', '필터')
    ui.act('button', '신규 주문')
    ui.expect_text('품목')
    ui.act('button', '저장')
    ui.expect_url_contains('/orders/')
    ui.expect_text_matches('주문\\s+상세\\s+ORD\\-\\d+')
    ui.expect_no_text('신규 주문')

def test_portal_06(ui):
    """포털 홈 → 주문 목록 → 주문 목록 → 신규 주문 → 주문 목록 (입력 후)"""
    ui.goto('/')
    ui.expect_title('홈')
    ui.act('link', '주문 관리')
    ui.expect_url_path('/orders')
    ui.expect_text('김철수')
    ui.expect_no_text('포털 홈')
    ui.act('button', '필터')
    ui.act('button', '신규 주문')
    ui.expect_text('품목')
    ui.fill('수량', '2', role='textbox')
    ui.act('button', '닫기', nth=1)
    ui.expect_no_text('품목')

def test_portal_07(ui):
    """포털 홈 → 주문 목록 → 주문 목록 → 주문 상세 ORD-1"""
    ui.goto('/')
    ui.expect_title('홈')
    ui.act('link', '주문 관리')
    ui.expect_url_path('/orders')
    ui.expect_text('김철수')
    ui.expect_no_text('포털 홈')
    ui.act('button', '필터')
    ui.act('link', 'ORD-1 노트북')
    ui.expect_url_contains('/orders/')
    ui.expect_text_matches('주문\\s+상세\\s+ORD\\-\\d+')
    ui.expect_no_text('주문 목록')

def test_portal_08(ui):
    """포털 홈 → 주문 목록 → 주문 상세 ORD-1 → 고객 상세 김철수"""
    ui.goto('/')
    ui.expect_title('홈')
    ui.act('link', '주문 관리')
    ui.expect_url_path('/orders')
    ui.expect_text('김철수')
    ui.expect_no_text('포털 홈')
    ui.act('link', 'ORD-1 노트북')
    ui.expect_url_contains('/orders/')
    ui.expect_text_matches('주문\\s+상세\\s+ORD\\-\\d+')
    ui.expect_no_text('주문 목록')
    ui.act('link', '김철수')
    ui.expect_url_contains('/customers/')
    ui.expect_text('고객 상세 김철수')
    ui.expect_no_text('"상태:"')

def test_portal_09(ui):
    """포털 홈 → 주문 목록 → 주문 상세 ORD-2 → 주문 상세 ORD-2 → 주문 상세 ORD-2"""
    ui.goto('/')
    ui.expect_title('홈')
    ui.act('link', '주문 관리')
    ui.expect_url_path('/orders')
    ui.expect_text('김철수')
    ui.expect_no_text('포털 홈')
    ui.act('link', 'ORD-2 볼펜')
    ui.expect_url_contains('/orders/')
    ui.expect_text_matches('주문\\s+상세\\s+ORD\\-\\d+')
    ui.expect_no_text('주문 목록')
    ui.act('tab', '이력')
    ui.expect_text_matches('\\d+\\.\\s+접수')
    ui.expect_no_text('볼펜')
    ui.act('tab', '기본 정보')
    ui.expect_text('볼펜')

def test_portal_10(ui):
    """포털 홈 → 주문 목록 → 주문 상세 ORD-2 → 주문 상세 ORD-2 → 주문 상세 ORD-2 (취소)"""
    ui.goto('/')
    ui.expect_title('홈')
    ui.act('link', '주문 관리')
    ui.expect_url_path('/orders')
    ui.expect_text('김철수')
    ui.expect_no_text('포털 홈')
    ui.act('link', 'ORD-2 볼펜')
    ui.expect_url_contains('/orders/')
    ui.expect_text_matches('주문\\s+상세\\s+ORD\\-\\d+')
    ui.expect_no_text('주문 목록')
    ui.act('button', '주문 취소')
    ui.expect_dialog('이 주문을 취소할까요?')
    ui.dialog('dismiss')
    ui.act('button', '주문 취소')
    ui.expect_dialog('이 주문을 취소할까요?')

def test_portal_11(ui):
    """포털 홈 → 주문 목록 → 주문 상세 ORD-3 → 주문 상세 ORD-3 (취소)"""
    ui.goto('/')
    ui.expect_title('홈')
    ui.act('link', '주문 관리')
    ui.expect_url_path('/orders')
    ui.expect_text('김철수')
    ui.expect_no_text('포털 홈')
    ui.act('link', 'ORD-3 마우스')
    ui.expect_url_contains('/orders/')
    ui.expect_text_matches('주문\\s+상세\\s+ORD\\-\\d+')
    ui.expect_no_text('주문 목록')
    ui.dialog('dismiss')
    ui.act('button', '주문 취소')
    ui.expect_dialog('이 주문을 취소할까요?')

def test_portal_12(ui):
    """포털 홈 → 고객 목록 → 고객 등록 → 고객 상세 박민수 → 포털 홈"""
    ui.goto('/')
    ui.expect_title('홈')
    ui.act('link', '고객 관리')
    ui.expect_url_path('/customers')
    ui.expect_text('VIP')
    ui.expect_no_text('포털 홈')
    ui.act('button', '고객 등록')
    ui.fill('이름', '박민수', role='textbox')
    ui.act('option', 'VIP')
    ui.act('button', '등록')
    ui.expect_url_contains('/customers/')
    ui.expect_text('고객 상세 박민수')
    ui.expect_no_text('고객 등록')
    ui.act('link', '홈')
    ui.expect_url_path('/')
    ui.expect_text('포털 홈')
    ui.expect_no_text('고객 상세 박민수')

def test_portal_13(ui):
    """포털 홈 → 고객 목록 → 고객 등록 → 고객 목록 (입력 후) → 고객 상세 김철수"""
    ui.goto('/')
    ui.expect_title('홈')
    ui.act('link', '고객 관리')
    ui.expect_url_path('/customers')
    ui.expect_text('VIP')
    ui.expect_no_text('포털 홈')
    ui.act('button', '고객 등록')
    ui.fill('이름', '박민수', role='textbox')
    ui.act('option', 'VIP')
    ui.act('button', '닫기')
    ui.act('link', '김철수')
    ui.expect_url_contains('/customers/')
    ui.expect_text('고객 상세 김철수')
    ui.expect_no_text('고객 목록')

def test_portal_14(ui):
    """포털 홈 → 고객 목록 → 고객 상세 김철수 → 고객 상세 김철수 → 포털 홈"""
    ui.goto('/')
    ui.expect_title('홈')
    ui.act('link', '고객 관리')
    ui.expect_url_path('/customers')
    ui.expect_text('VIP')
    ui.expect_no_text('포털 홈')
    ui.act('link', '김철수')
    ui.expect_url_contains('/customers/')
    ui.expect_text('고객 상세 김철수')
    ui.expect_no_text('고객 목록')
    ui.act('button', '메모')
    ui.expect_text('메모 내용')
    ui.act('link', '홈')
    ui.expect_url_path('/')
    ui.expect_text('포털 홈')
    ui.expect_no_text('고객 상세 김철수')

def test_portal_15(ui):
    """포털 홈 → 고객 목록 → 고객 상세 김철수 → 고객 상세 김철수 → 고객 상세 김철수"""
    ui.goto('/')
    ui.expect_title('홈')
    ui.act('link', '고객 관리')
    ui.expect_url_path('/customers')
    ui.expect_text('VIP')
    ui.expect_no_text('포털 홈')
    ui.act('link', '김철수')
    ui.expect_url_contains('/customers/')
    ui.expect_text('고객 상세 김철수')
    ui.expect_no_text('고객 목록')
    ui.act('button', '메모')
    ui.expect_text('메모 내용')
    ui.act('button', '저장')
    ui.expect_no_text('메모 내용')

def test_portal_16(ui):
    """포털 홈 → 고객 목록 → 고객 상세 김철수 → 주문 목록 → 주문 상세 ORD-3"""
    ui.goto('/')
    ui.expect_title('홈')
    ui.act('link', '고객 관리')
    ui.expect_url_path('/customers')
    ui.expect_text('VIP')
    ui.expect_no_text('포털 홈')
    ui.act('link', '김철수')
    ui.expect_url_contains('/customers/')
    ui.expect_text('고객 상세 김철수')
    ui.expect_no_text('고객 목록')
    ui.act('link', '주문 보기')
    ui.expect_url_path('/orders')
    ui.expect_text('주문 목록')
    ui.expect_no_text('고객 상세 김철수')
    ui.act('link', 'ORD-1 노트북')
    ui.expect_url_contains('/orders/')
    ui.expect_text_matches('주문\\s+상세\\s+ORD\\-\\d+')
    ui.expect_no_text('주문 목록')

def test_portal_17(ui):
    """포털 홈 → 설정 → 설정 → 설정 (대화상자)"""
    ui.goto('/')
    ui.expect_title('홈')
    ui.act('link', '설정')
    ui.expect_url_path('/settings')
    ui.expect_text('회사명')
    ui.expect_no_text('포털 홈')
    ui.act('tab', '알림')
    ui.expect_text('메일 알림')
    ui.expect_no_text('회사명')
    ui.act('button', '저장')
    ui.expect_dialog('저장되었습니다.')

def test_portal_18(ui):
    """포털 홈 → 설정 → 설정 (입력 후) → 설정 (입력 후) (대화상자)"""
    ui.goto('/')
    ui.expect_title('홈')
    ui.act('link', '설정')
    ui.expect_url_path('/settings')
    ui.expect_text('회사명')
    ui.expect_no_text('포털 홈')
    ui.fill('회사명', '데모상사', role='textbox')
    ui.act('tab', '알림')
    ui.expect_text('메일 알림')
    ui.expect_no_text('회사명')
    ui.act('button', '저장')
    ui.expect_dialog('저장되었습니다.')


def test_portal_vat_truncation(ui):
    """신규 주문 팝업에서 볼펜 3개 저장 → 상세의 부가세는 10원 절사 (as-is 동작 보존: 3,675원의 10%는 367.5원 → 360원)"""
    ui.goto('/orders')
    ui.act('button', '신규 주문')
    ui.select('고객', '김철수')
    ui.select('품목', '볼펜')
    ui.fill('수량', '3')
    ui.act('button', '저장')
    ui.expect_url_contains('/orders/')
    ui.expect_text('공급가액')
    ui.expect_text('3,675원')
    ui.expect_text('360원')
    ui.expect_text('4,035원')


def test_portal_cancel_twice(ui):
    """이미 취소된 주문을 다시 취소해도 막지 않고 이력에 취소가 한 번 더 쌓인다 (as-is 버그 보존)"""
    ui.goto('/orders')
    ui.act('link', 'ORD-3 마우스')
    ui.expect_text('취소')
    ui.act('button', '주문 취소')
    ui.expect_dialog('이 주문을 취소할까요?')
    ui.act('tab', '이력')
    ui.expect_text('3. 취소')


@pytest.mark.parametrize("status,shown", [("접수", "ORD-1 노트북"), ("배송중", "ORD-2 볼펜"), ("완료", "0건"), ("취소", "ORD-3 마우스")])
def test_portal_filter_by_status(ui, status, shown):
    """필터 드로워에서 상태를 골라 적용하면 그 상태의 주문만 남는다 (상태 라벨 네 개 모두 선택해 본다)"""
    ui.goto('/orders')
    ui.act('button', '필터')
    ui.select('상태', status)
    ui.act('button', '적용')
    ui.expect_url_contains('status=')
    ui.expect_text(f'상태 {status}')
    ui.expect_text(shown)


def test_portal_new_order_other_customer(ui):
    """신규 주문 팝업에서 이영희·마우스 2개 저장 → 상세에 고객 이영희, 공급가액 66,000원, 부가세 6,600원"""
    ui.goto('/orders')
    ui.act('button', '신규 주문')
    ui.select('고객', '이영희')
    ui.select('품목', '마우스')
    ui.fill('수량', '2')
    ui.act('button', '저장')
    ui.expect_url_contains('/orders/')
    ui.expect_text('이영희')
    ui.expect_text('마우스')
    ui.expect_text('66,000원')
    ui.expect_text('6,600원')
    ui.expect_text('72,600원')
