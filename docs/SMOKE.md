# 전체 화면 스모크: 시나리오 하나로 화면 N개 (2026-09-24)

비슷하지만 조금씩 다른 조회 화면이 많을 때, 화면마다 스크립트를 쓰지 않고 공통 시나리오 하나를 화면 목록(matrix)에 돌린다.

```yaml
# scenarios/smoke/screen_smoke.yaml
name: "스모크 - ${SCREEN}: 열기, 기본 조회, 첫 항목 상세"
matrix_file: screens.yaml          # [{SCREEN: 사원 조회, URL: /emp}, …]  as-is 메뉴/라우터에서 뽑는다
steps:
  - goto: ${URL}
  - expect: { http_ok: true, no_js_error: true }
  - do: 검색 조건 그대로 목록 조회를 실행하는 버튼 누르기
  - expect: { no_dialog: true, no_js_error: true, rows_at_least: 1 }
  - do: 결과 목록 첫 번째 행의 항목 열기
  - expect: { http_ok: true, title_contains: "상세" }
```

스모크용 expect 키 (마지막 goto/do/press 이후 기준): `http_ok` (문서 응답 4xx/5xx 없음), `no_js_error` (페이지 스크립트 예외 없음),
`no_dialog` (alert/confirm 없음), `rows_at_least: N` (cell이 있는 표 행 수). 행마다 캐시·리포트가 따로 생긴다 (`screen_smoke--03`).

```bash
python demo-app/erp_app.py 8811 asis & python demo-app/erp_app.py 8812 tobe &
uv run parity run scenarios/smoke/screen_smoke.yaml --base-url http://127.0.0.1:8811 --cache-dir .parity-cache/smoke   # as-is: Jev가 화면별 요소 결정 → 캐시
uv run parity run scenarios/smoke/screen_smoke.yaml --base-url http://127.0.0.1:8812 --cache-dir .parity-cache/smoke --replay-only --junit reports/junit-smoke.xml
```

## 데모 (`demo-app/erp_app.py`, 화면 12개)

조회 버튼 이름이 화면마다 다르고 (조회, 검색, 찾기, 조회하기, Search, 목록 불러오기, 집계 실행, 🔍(aria-label 검색), 검색하기, 문서 조회),
옆에 비슷한 이름의 다른 버튼이 있다 (검색 조건 초기화, 조회 권한 요청, 상세 검색 열기, 조회 조건 저장, Reset …). to-be에는 결함 5개를 넣었다.

| | Jev 러너 (`parity run`) | Playwright (일반 규칙: 이름에 조회/검색/찾기/search 포함한 첫 버튼, `examples/comparison/test_smoke_regex_baseline.py`) |
| :--- | :--- | :--- |
| as-is (결함 없음) | **12/12 PASS**. Jev 24회 전부 정답, 유사 버튼 오클릭 0, margin 최소 0.29 | 10/12. 주문 내역은 "상세 검색 열기"를 **잘못 눌러** 실패, 매출 집계는 버튼을 못 찾아 실패 |
| to-be (결함 5개) | **5/5 탐지, 오탐 0**, Jev 호출 0 (캐시 재생) | 결함 5개 중 4개 + 오탐 1 (주문 내역). 매출 집계의 결함(빈 결과)은 스크립트 실패에 가려짐 |
| 화면별 추가 작업 | 없음 (화면 목록만) | 정규식 보강 또는 화면별 버튼 이름 목록 |
| 실행 시간 | as-is 첫 실행 약 28s (Jev 24회, 약 5.4s), 이후 재생 | 약 12s |

to-be에서 잡은 결함: 500 (`HTTP error: 500`), 조회 버튼 스크립트 오류 (`JS error: undefinedFn is not defined`), 빈 결과 (`data rows 0 < 1`),
오류 alert (`unexpected dialog: 시스템 오류가 발생했습니다.`), 상세 404 (`HTTP error: 404 …/notice/detail/0`).

## 해석

- Playwright도 정규식을 늘리거나 as-is 코드에서 화면별 버튼 이름을 뽑아 목록에 넣으면 12/12가 된다. 차이는 그 작업이 화면 수에 비례한다는 것이다.
  수백 개 화면에서 정규식은 유사 버튼("상세 검색 열기", "검색 조건 초기화")을 조용히 누를 위험이 커진다. Jev는 모호하면 margin 게이트로 멈추고 추측하지 않는다.
- 한 번 Jev가 고른 결과는 캐시가 되므로 이후 실행은 Playwright와 같은 결정론적 재생이다 (CI에서 API 키 불필요).
- 한계: 화면 12개는 직접 만든 데모다. 실제 화면 수백 개에서는 margin 미달로 멈추는 화면이 나올 수 있고, 그 화면은 스텝 문장을 구체화하거나 개별 시나리오로 뺀다.
