# 검증 보고서: portal

생성 2026-09-25 23:10:10 · `east2west report`가 아래 산출물에서만 만들었다.

## 결과를 믿을 수 있는가

| 확인 | 결과 | 근거 |
| :--- | :--- | :--- |
| 기준이 승인됨 | ✅ | east · 2026-09-25 22:43:43 |
| 승인된 기준으로 비교함 | ✅ |  |
| 비교 결과가 현재 승인본으로 만들어짐 | ✅ |  |
| 결함 주입 실행이 아닌 실제 비교 결과 | ✅ |  |
| 기록된 테스트를 빠짐없이 실행 | ✅ | 25개 |
| 기대값을 임의로 바꾸지 않음 | ✅ |  |
| 결함 탐지 측정도 승인된 기준으로 | ✅ | 2026-09-25 23:09:16 |
| 결함 탐지 측정이 현재 승인본으로 만들어짐 | ✅ |  |
| 결함 탐지 측정이 오류 없이 실행됨 | ✅ |  |
| 기록된 테스트 전부가 결함 탐지 측정에 참여 | ✅ | 25개 |
| 테스트가 결함을 80% 이상 잡음 | ✅ | 144/148 = 97% |

**판정: 신뢰 가능**

## 비교 결과: `runs/portal/20260925-224529/junit.xml`

25개 중 통과 23, 실패 2

- **test_portal_vat_truncation** — as-is와 다름

```
ui = <east2west.pwtest.ui.UI object at 0x109bd5610>
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
…
```

- **test_portal_cancel_twice** — as-is와 다름

```
ui = <east2west.pwtest.ui.UI object at 0x109bafe00>
    def test_portal_cancel_twice(ui):
        """이미 취소된 주문을 다시 취소해도 막지 않고 이력에 취소가 한 번 더 쌓인다 (as-is 버그 보존)"""
        ui.goto('/orders')
        ui.act('link', 'ORD-3 마우스')
        ui.expect_text('취소')
        ui.act('button', '주문 취소')
>       ui.expect_dialog('이 주문을 취소할까요?')
e2e/portal/test_portal.py:323:
_ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _
self = <east2west.pwtest.ui.UI object at 0x109bafe00>, message = '이 주문을 취소할까요?'
    def expect_dialog(self, message: str) -> None:
…
```

## 결함 주입: expects+golden

`http://127.0.0.1:8820`에 결함 148개를 하나씩 주입, 탐지 144개 = **97%**

| 연산자 | 탐지 |
| :--- | :--- |
| num | 16/16 |
| cond | 2/6 |
| dialog | 2/2 |
| msg | 2/2 |
| label | 116/116 |
| http500 | 6/6 |

아무 결함도 못 잡은 테스트: 없음

생존 결함 4개 (테스트 빈틈인지, 관찰할 차이가 없는 결함인지 사람이 판정):

- `m000` cond `/`: `'[role=tab]')){const on=t⟦===→!==⟧btn;t.setAttribute('aria-`
- `m016` cond `/customers`: `'[role=tab]')){const on=t⟦===→!==⟧btn;t.setAttribute('aria-`
- `m036` cond `/customers/{id}`: `'[role=tab]')){const on=t⟦===→!==⟧btn;t.setAttribute('aria-`
- `m061` cond `/orders`: `'[role=tab]')){const on=t⟦===→!==⟧btn;t.setAttribute('aria-`

## 오라클

`golden/portal`

```
test_portal_01.json: 3 observed steps, recorded on http://127.0.0.1:8820 at 2026-09-25 22:35:24
expects title '홈' = None
expects url_path '/orders' = None
expects text '김철수' = None
expects no_text '포털 홈' = None
expects url_path '/' = None
expects text '포털 홈' = None
expects no_text '김철수' = None
test_portal_02.json: 4 observed steps, recorded on http://127.0.0.1:8820 at 2026-09-25 22:35:26
expects title '홈' = None
expects url_path '/orders' = None
expects text '김철수' = None
expects no_text '포털 홈' = None
expects url_path '/' = None
expects text '포털 홈' = None
expects no_text '필터' = None
test_portal_03.json: 4 observed steps, recorded on http://127.0.0.1:8820 at 2026-09-25 22:35:29
expects title '홈' = None
expects url_path '/orders' = None
expects text '김철수' = None
expects no_text '포털 홈' = None
expects url_path '/settings' = None
expects text '회사명' = None
expects no_text '주문 목록' = None
test_portal_04.json: 5 observed steps, recorded on http://127.0.0.1:8820 at 2026-09-25 22:35:32
expects title '홈' = None
expects url_path '/orders' = None
expects text '김철수' = None
expects no_text '포털 홈' = None
expects text '품목' = None
test_portal_05.json: 5 observed steps, recorded on http://127.0.0.1:8820 at 2026-09-25 22:35:34
expects title '홈' = None
expects url_path '/orders' = None
expects text '김철수' = None
expects no_text '포털 홈' = None
expects text '품목' = None
expects url_contains '/orders/' = None
expects text_matches '주문\\s+상세\\s+ORD\\-\\d+' = None
expects no_text '신규 주문' = None
test_portal_06.json: 6 observed steps, recorded on http://127.0.0.1:8820 at 2026-09-25 22:35:38
expects title '홈' = None
expects url_path '/orders' = None
expects text '김철수' = None
expects no_text '포털 홈' = None
expects text '품목' = None
expects no_text '품목' = None
test_portal_07.json: 4 observed steps, recorded on http://127.0.0.1:8820 at 2026-09-25 22:35:40
expects title '홈' = None
expects url_path '/orders' = None
expects text '김철수' = None
expects no_text '포털 홈' = None
expects url_contains '/orders/' = None
expects text_matches '주문\\s+상세\\s+ORD\\-\\d+' = None
expects no_text '주문 목록' = None
test_portal_08.json: 4 observed steps, recorded on http://127.0.0.1:8820 at 2026-09-25 22:35:42
expects title '홈' = None
expects url_path '/orders' = None
expects text '김철수' = None
expects no_text '포털 홈' = None
expects url_contains '/orders/' = None
expects text_matches '주문\\s+상세\\s+ORD\\-\\d+' = None
expects no_text '주문 목록' = None
expects url_contains '/customers/' = None
expects text '고객 상세 김철수' = None
expects no_text '"상태:"' = None
test_portal_09.json: 5 observed steps, recorded on http://127.0.0.1:8820 at 2026-09-25 22:35:45
expects title '홈' = None
expects url_path '/orders' = None
expects text '김철수' = None
expects no_text '포털 홈' = None
expects url_contains '/orders/' = None
expects text_matches '주문\\s+상세\\s+ORD\\-\\d+' = None
expects no_text '주문 목록' = None
expects text_matches '\\d+\\.\\s+접수' = None
expects no_text '볼펜' = None
expects text '볼펜' = None
test_portal_10.json: 5 observed steps, recorded on http://127.0.0.1:8820 at 2026-09-25 22:35:48
expects title '홈' = None
expects url_path '/orders' = None
expects text '김철수' = None
expects no_text '포털 홈' = None
expects url_contains '/orders/' = None
expects text_matches '주문\\s+상세\\s+ORD\\-\\d+' = None
expects no_text '주문 목록' = None
expects dialog '이 주문을 취소할까요?' = None
expects dialog '이 주문을 취소할까요?' = None
test_portal_11.json: 4 observed steps, recorded on http://127.0.0.1:8820 at 2026-09-25 22:35:50
expects title '홈' = None
expects url_path '/orders' = None
expects text '김철수' = None
expects no_text '포털 홈' = None
expects url_contains '/orders/' = None
expects text_matches '주문\\s+상세\\s+ORD\\-\\d+' = None
expects no_text '주문 목록' = None
expects dialog '이 주문을 취소할까요?' = None
test_portal_12.json: 7 observed steps, recorded on http://127.0.0.1:8820 at 2026-09-25 22:35:54
expects title '홈' = None
expects url_path '/customers' = None
expects text 'VIP' = None
expects no_text '포털 홈' = None
expects url_contains '/customers/' = None
expects text '고객 상세 박민수' = None
expects no_text '고객 등록' = None
expects url_path '/' = None
expects text '포털 홈' = None
expects no_text '고객 상세 박민수' = None
test_portal_13.json: 7 observed steps, recorded on http://127.0.0.1:8820 at 2026-09-25 22:35:58
expects title '홈' = None
expects url_path '/customers' = None
expects text 'VIP' = None
expects no_text '포털 홈' = None
expects url_contains '/customers/' = None
expects text '고객 상세 김철수' = None
expects no_text '고객 목록' = None
test_portal_14.json: 5 observed steps, recorded on http://127.0.0.1:8820 at 2026-09-25 22:36:01
expects title '홈' = None
expects url_path '/customers' = None
expects text 'VIP' = None
expects no_text '포털 홈' = None
expects url_contains '/customers/' = None
expects text '고객 상세 김철수' = None
expects no_text '고객 목록' = None
expects text '메모 내용' = None
expects url_path '/' = None
expects text '포털 홈' = None
expects no_text '고객 상세 김철수' = None
test_portal_15.json: 5 observed steps, recorded on http://127.0.0.1:8820 at 2026-09-25 22:36:04
expects title '홈' = None
expects url_path '/customers' = None
expects text 'VIP' = None
expects no_text '포털 홈' = None
expects url_contains '/customers/' = None
expects text '고객 상세 김철수' = None
expects no_text '고객 목록' = None
expects text '메모 내용' = None
expects no_text '메모 내용' = None
test_portal_16.json: 5 observed steps, recorded on http://127.0.0.1:8820 at 2026-09-25 22:36:07
expects title '홈' = None
expects url_path '/customers' = None
expects text 'VIP' = None
expects no_text '포털 홈' = None
expects url_contains '/customers/' = None
expects text '고객 상세 김철수' = None
expects no_text '고객 목록' = None
expects url_path '/orders' = None
expects text '주문 목록' = None
expects no_text '고객 상세 김철수' = None
expects url_contains '/orders/' = None
expects text_matches '주문\\s+상세\\s+ORD\\-\\d+' = None
expects no_text '주문 목록' = None
test_portal_17.json: 4 observed steps, recorded on http://127.0.0.1:8820 at 2026-09-25 22:36:09
expects title '홈' = None
expects url_path '/settings' = None
expects text '회사명' = None
expects no_text '포털 홈' = None
expects text '메일 알림' = None
expects no_text '회사명' = None
expects dialog '저장되었습니다.' = None
test_portal_18.json: 5 observed steps, recorded on http://127.0.0.1:8820 at 2026-09-25 22:36:12
expects title '홈' = None
expects url_path '/settings' = None
expects text '회사명' = None
expects no_text '포털 홈' = None
expects text '메일 알림' = None
expects no_text '회사명' = None
expects dialog '저장되었습니다.' = None
test_portal_cancel_twice.json: 4 observed steps, recorded on http://127.0.0.1:8820 at 2026-09-25 22:36:17
expects text '취소' = None
expects dialog '이 주문을 취소할까요?' = None
expects text '3. 취소' = None
test_portal_filter_by_status[\ubc30\uc1a1\uc911-ORD-2 \ubcfc\ud39c].json: 4 observed steps, recorded on http://127.0.0.1:8820 at 2026-09-25 22:36:22
expects url_contains 'status=' = None
expects text '상태 배송중' = None
expects text 'ORD-2 볼펜' = None
test_portal_filter_by_status[\uc644\ub8cc-0\uac74].json: 4 observed steps, recorded on http://127.0.0.1:8820 at 2026-09-25 22:36:24
expects url_contains 'status=' = None
expects text '상태 완료' = None
expects text '0건' = None
test_portal_filter_by_status[\uc811\uc218-ORD-1 \ub178\ud2b8\ubd81].json: 4 observed steps, recorded on http://127.0.0.1:8820 at 2026-09-25 22:36:20
expects url_contains 'status=' = None
expects text '상태 접수' = None
expects text 'ORD-1 노트북' = None
test_portal_filter_by_status[\ucde8\uc18c-ORD-3 \ub9c8\uc6b0\uc2a4].json: 4 observed steps, recorded on http://127.0.0.1:8820 at 2026-09-25 22:36:26
expects url_contains 'status=' = None
expects text '상태 취소' = None
expects text 'ORD-3 마우스' = None
test_portal_new_order_other_customer.json: 6 observed steps, recorded on http://127.0.0.1:8820 at 2026-09-25 22:36:30
expects url_contains '/orders/' = None
expects text '이영희' = None
expects text '마우스' = None
expects text '66,000원' = None
expects text '6,600원' = None
expects text '72,600원' = None
test_portal_vat_truncation.json: 6 observed steps, recorded on http://127.0.0.1:8820 at 2026-09-25 22:36:15
expects url_contains '/orders/' = None
expects text '공급가액' = None
expects text '3,675원' = None
expects text '360원' = None
expects text '4,035원' = None
```

마스킹 규칙과 실제로 가린 값:

```
(없음)
```
