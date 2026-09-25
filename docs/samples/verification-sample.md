# 검증 보고서: legacy

생성 2026-09-25 18:23:33 · `parity report`가 아래 산출물에서만 만들었다.

## 결과를 믿을 수 있는가

| 확인 | 결과 | 근거 |
| :--- | :--- | :--- |
| 기준이 승인됨 | ✅ | east · 2026-09-24 18:10:42 |
| 승인된 기준으로 비교함 | ✅ |  |
| 비교 결과가 현재 승인본으로 만들어짐 | ✅ |  |
| 결함 주입 실행이 아닌 실제 비교 결과 | ✅ |  |
| 기록된 테스트를 빠짐없이 실행 | ✅ | 6개 |
| 기대값을 임의로 바꾸지 않음 | ✅ |  |
| 결함 탐지 측정도 승인된 기준으로 | ✅ | 2026-09-24 22:25:49 |
| 결함 탐지 측정이 현재 승인본으로 만들어짐 | ✅ |  |
| 결함 탐지 측정이 오류 없이 실행됨 | ✅ |  |
| 기록된 테스트 전부가 결함 탐지 측정에 참여 | ✅ | 6개 |
| 테스트가 결함을 80% 이상 잡음 | ✅ | 34/34 = 100% |

**판정: 신뢰 가능**

## 비교 결과: `reports/junit-legacy-tobe-fixed.xml`

6개 중 통과 3, 실패 3

- **test_order_save** — as-is와 다름

```
ui = <parity.pwtest.ui.UI object at 0x10adf9b80>
    def test_order_save(ui):
        """볼펜 1개 계산 후 저장 (부가세 10원 절사 동작 보존)"""
        open_order_form(ui)
        ui.select("품목", "볼펜")
        ui.fill("수량", "1")
        ui.click("button", "계산")
>       ui.expect_field("부가세", "120")  # as-is: 123.5원 → 10원 단위 절사
        ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
e2e/legacy/test_orders.py:16:
_ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _
parity/pwtest/ui.py:232: in expect_field
…
```

- **test_qty_required_alert** — as-is와 다름

```
ui = <parity.pwtest.ui.UI object at 0x10ad6d010>
    def test_qty_required_alert(ui):
        """수량 없이 저장하면 alert, 저장 안 됨"""
        open_order_form(ui)
        ui.select("품목", "마우스")
        ui.click("button", "저장")
>       ui.expect_dialog("수량을 입력하세요.")
e2e/legacy/test_orders.py:29:
_ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _
self = <parity.pwtest.ui.UI object at 0x10ad6d010>, message = '수량을 입력하세요.'
    def expect_dialog(self, message: str) -> None:
        """가장 최근 alert/confirm/prompt 문구와 정확히 같아야 한다 (부분 일치는 문구 변경을 놓친다). 평범한 assert는 -O에서 사라지므로 쓰지 않는다."""
…
```

- **test_qty_zero_saved** — as-is와 다름

```
ui = <parity.pwtest.ui.UI object at 0x10b125820>
    def test_qty_zero_saved(ui):
        """수량 0은 막지 않고 합계 0원으로 저장된다 (as-is 버그 보존)"""
        open_order_form(ui)
        ui.fill("수량", "0")
        ui.click("button", "저장")
>       ui.expect_dialog("저장하시겠습니까?")
e2e/legacy/test_orders.py:50:
_ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _
self = <parity.pwtest.ui.UI object at 0x10b125820>, message = '저장하시겠습니까?'
    def expect_dialog(self, message: str) -> None:
        """가장 최근 alert/confirm/prompt 문구와 정확히 같아야 한다 (부분 일치는 문구 변경을 놓친다). 평범한 assert는 -O에서 사라지므로 쓰지 않는다."""
…
```

## 결함 주입: expects+golden

`http://127.0.0.1:8801`에 결함 34개를 하나씩 주입, 탐지 34개 = **100%**

| 연산자 | 탐지 |
| :--- | :--- |
| num | 8/8 |
| math | 1/1 |
| cond | 1/1 |
| dialog | 2/2 |
| msg | 2/2 |
| label | 16/16 |
| http500 | 4/4 |

아무 결함도 못 잡은 테스트: 없음

## 오라클

`golden/legacy`

```
name_map.tobe-renamed.json: '수량' → '주문 수량', '저장' → '등록'
test_order_calc_each_item[mouse].json: 5 observed steps, recorded on http://127.0.0.1:8801 at 2026-09-24 18:04:27
expects field '공급가액' = '99,000'
expects field '부가세' = '9,900'
expects field '합계' = '108,900'
test_order_calc_each_item[notebook].json: 5 observed steps, recorded on http://127.0.0.1:8801 at 2026-09-24 18:04:24
expects field '공급가액' = '2,500,000'
expects field '부가세' = '250,000'
expects field '합계' = '2,750,000'
test_order_save.json: 6 observed steps, recorded on http://127.0.0.1:8801 at 2026-09-24 18:04:14
expects field '부가세' = '120'
expects field '합계' = '1,355'
expects dialog '저장하시겠습니까?' = None
expects text '주문이 저장되었습니다' = None
expects text '합계 1,355원' = None
test_qty_required_alert.json: 4 observed steps, recorded on http://127.0.0.1:8801 at 2026-09-24 18:04:16
expects dialog '수량을 입력하세요.' = None
expects field '수량' = ''
test_qty_zero_saved.json: 4 observed steps, recorded on http://127.0.0.1:8801 at 2026-09-24 18:04:21
expects dialog '저장하시겠습니까?' = None
expects text '노트북 0개' = None
expects text '합계 0원' = None
test_save_cancel.json: 5 observed steps, recorded on http://127.0.0.1:8801 at 2026-09-24 18:04:19
expects dialog '저장하시겠습니까?' = None
expects field '수량' = '2'
expects field '품목' = '노트북'
```

마스킹 규칙과 실제로 가린 값:

```
'주문번호 \\d+': masks 2 occurrences — '주문번호 323'×1, '주문번호 324'×1
```
