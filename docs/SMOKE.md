# 전체 화면 스모크: 시나리오 하나로 화면 N개

비슷하지만 조금씩 다른 조회 화면이 많을 때, 화면마다 스크립트를 쓰지 않고 공통 시나리오 하나를 화면 목록(matrix)에 돌린다.
화면마다 "조회 버튼이 어느 것인지"는 **실행 전에 정해서 목록에 적는다**. 실행 중에는 판단하지 않으므로 같은 목록이면 매번 같은 결과가 나오고,
고른 내용이 파일에 남아 사람이 검토할 수 있다. Jev(API 키)는 필요 없다. Claude Code에서는 `smoke` 스킬이 이 절차를 따른다.

## 절차

```bash
# 1) 화면 목록: as-is 메뉴·라우터에서 뽑는다. 모듈 단위로 나눠서 진행한다
#    scenarios/<app>/screens.yaml  →  [{SCREEN: 사원 조회, URL: /emp}, …]

# 2) 대상 정하기: 화면을 열기만 하고(누르지 않음) 버튼 목록을 본다. 규칙으로 확정되지 않는 화면만 표시된다
uv run eastshift targets scenarios/<app>/screens.yaml --base-url $ASIS --out scenarios/<app>/screens.targets.yaml
#    by: review 행은 사람이나 Claude Code가 candidates에서 골라 QUERY를 채우고 by: picked로 바꾼다

# 3) 기준 앱에서 통과 확인, 4) 대상 앱 점검
uv run eastshift run scenarios/<app>/screen_smoke_targets.yaml --base-url $ASIS
uv run eastshift run scenarios/<app>/screen_smoke_targets.yaml --base-url $TOBE --junit reports/junit-<app>.xml
# 화면이 수백 개면 브라우저 여러 개로: 행마다 독립이라 결과는 같다 (서버 상태를 바꾸는 시나리오, 공유 로그인 세션은 1)
uv run eastshift run scenarios/<app>/screen_smoke_targets.yaml --base-url $TOBE --junit reports/junit-<app>.xml --workers 4
```

**규칙**: 화면의 버튼 중 이름이 조회, 검색, 찾기, 조회하기, 검색하기, Search, Find와 **정확히 같은 것이 하나뿐**이면 확정 (`by: rule`).
"검색 조건 초기화", "조회 권한 요청"처럼 단어를 포함하기만 한 버튼은 규칙에 걸리지 않는다. 이름 목록은 `--names 조회|검색|…`로 바꾼다.

```yaml
# scenarios/smoke/screens.targets.yaml (일부)
- {SCREEN: 사원 조회, URL: /emp, QUERY: 조회, by: rule}
- SCREEN: 매출 집계
  URL: /sales
  QUERY: 집계 실행
  by: picked
  candidates: [집계 실행, 인쇄]
  note: 'picked by Claude Code: 인쇄는 조회가 아님'
```

```yaml
# scenarios/smoke/screen_smoke_targets.yaml
name: "스모크 - ${SCREEN}: 열기, 기본 조회, 첫 항목 상세"
matrix_file: screens.targets.yaml
steps:
  - goto: ${URL}
  - expect: { http_ok: true, no_js_error: true }
  - do: 목록 조회 실행
    target: { role: button, name: "${QUERY}" }      # 적힌 이름 그대로. 없거나 여러 개면 실패 (다른 버튼으로 대신 누르지 않는다)
  - expect: { no_dialog: true, no_js_error: true, rows_at_least: 1 }
  - do: 결과 첫 행의 항목 열기
    target: { role: link, row: 1 }                  # 첫 데이터 행 안의 첫 링크
  - expect: { http_ok: true, title_contains: "상세" }
```

스모크용 expect 키 (마지막 goto/do/press 이후 기준): `http_ok` (문서 응답 4xx/5xx 없음), `no_js_error` (페이지 스크립트 예외 없음),
`no_dialog` (alert/confirm 없음), `rows_at_least: N` (cell이 있는 표 행 수). `target`은 `{role, name[, nth]}` 또는 `{role, row: N}`.

## 데모 (`demo-app/erp_app.py`, 화면 12개)

조회 버튼 이름이 화면마다 다르고 (조회, 검색, 찾기, 조회하기, Search, 목록 불러오기, 집계 실행, 🔍(aria-label 검색), 검색하기, 문서 조회),
옆에 비슷한 이름의 다른 버튼이 있다 (검색 조건 초기화, 조회 권한 요청, 상세 검색 열기, 조회 조건 저장, Reset …). to-be에는 결함 5개를 넣었다.

```bash
python demo-app/erp_app.py 8811 asis & python demo-app/erp_app.py 8812 tobe &
```

2026-09-25 결과 (API 키 없이):

| 단계 | 결과 |
| :--- | :--- |
| 대상 정하기 | 규칙으로 **9/12** 확정 (아이콘 버튼 🔍은 aria-label "검색"으로 확정). 유사 버튼 3종은 규칙에 안 걸림. 검토 3개: 주문 내역 → 목록 불러오기, 매출 집계 → 집계 실행, 결재 문서함 → 문서 조회 |
| as-is | 12/12 PASS, 화면당 약 1.9초 |
| to-be (결함 5개) | **5/5 탐지, 오탐 0**. 500, JS 오류, 빈 결과, 오류 alert, 상세 404 |

## 이전 방식과 비교

2026-09-24에는 같은 데모를 자연어 스텝 + Jev 선택(`scenarios/smoke/screen_smoke.yaml`)으로 돌렸다. 결과는 같았다.

| | 대상 명시 (지금) | Jev 선택 (이전) | Playwright 일반 규칙 (비교용) |
| :--- | :--- | :--- | :--- |
| as-is | 12/12 | 12/12 (Jev 24회 전부 정답) | 10/12. 주문 내역은 "상세 검색 열기"를 **잘못 눌러** 실패 |
| to-be 결함 5개 | 5/5, 오탐 0 | 5/5, 오탐 0 | 4/5 + 오탐 1 |
| 조회 버튼 결정 | 실행 전, 파일에 기록 (규칙 9 + 검토 3) | 실행 중 Jev (결과는 캐시에) | 실행 중 정규식 |
| API 키 | 불필요 | 첫 실행과 복구 때 필요 | 불필요 |
| 라벨이 바뀌면 | `target not found`로 실패, 사람이 확인 | Jev가 복구 시도 | 조용히 다른 버튼을 누를 수 있음 |

지금 방식을 기본으로 둔 이유는 신뢰성이다. 모든 선택이 파일에 드러나고, 실행 중 판단이 없고, 바뀐 라벨을 조용히 고치지 않는다.
작성 비용(검토가 필요한 화면을 고르는 일)은 규칙이 대부분을 확정하므로 작다. Jev 방식은 화면 수천 개를 한꺼번에 처음 훑을 때처럼 필요할 때만 쓴다.

한계: 화면 12개는 직접 만든 데모다. 실제 시스템에서 규칙이 확정하는 비율은 명명 규칙이 얼마나 일정한지에 달려 있다.
