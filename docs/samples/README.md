# 산출물 샘플

parity를 한 바퀴 돌렸을 때 나오는 파일들. 전부 데모 앱(`demo-app/`)에서 2026-09-25에 실제로 만든 것이고, 손으로 고치지 않았다.
HTML은 파일로 바로 열린다(서버 없음). 단계 번호는 [MIGRATION.md](../MIGRATION.md)의 절차 표와 같다.

| 단계 | 명령 | 산출물 | 누가 읽나 | 샘플 |
| :--- | :--- | :--- | :--- | :--- |
| 1 탐색 | `parity crawl … --dry-run` | 누를 버튼 목록 (누르기 전 확인) | 사람 | [crawl/dry-run.txt](crawl/dry-run.txt) |
| | `parity crawl …` | 탐색 로그 | 에이전트 | [crawl/run-output.txt](crawl/run-output.txt) |
| | | 흐름 그래프 (mermaid + 동작 표) | 사람·에이전트 | [crawl/graph.md](crawl/graph.md) |
| | | 화면 캡처 (상태마다 한 장) | 사람 | [crawl/screen-n2.png](crawl/screen-n2.png) |
| | | YAML 시나리오 초안 (경로마다 하나) | 에이전트 | [crawl/crawl_03.yaml](crawl/crawl_03.yaml) |
| | | Playwright 테스트 초안 | 에이전트 → `e2e/<app>/`로 옮김 | [crawl/test_crawl.py](crawl/test_crawl.py) |
| 2 시나리오 | 에이전트가 작성 | 업무 값이 붙은 테스트 | 사람 검토 | [e2e/legacy/test_orders.py](../../e2e/legacy/test_orders.py) |
| 3 기록 | `pytest --record golden/<app>` | 골든: 단계별 화면·입력값·대화상자·캡처 | 도구 (비교 기준) | [golden/legacy/test_order_save.json](../../golden/legacy/test_order_save.json), [shots/](../../golden/legacy/shots/test_order_save/) |
| | | 마스킹 규칙, 이름 매핑 | 사람이 편집 | [golden/legacy/oracle.json](../../golden/legacy/oracle.json), [name_map.tobe-renamed.json](../../golden/legacy/name_map.tobe-renamed.json) |
| | | 승인 검토 화면 | **사람** | [review-sample.html](review-sample.html) |
| 4 승인 | `parity approve` (터미널) | 승인 도장: 파일 해시 + 확인한 기대값 | 도구 (비교 전 대조) | [golden/legacy/APPROVED.json](../../golden/legacy/APPROVED.json) |
| | `parity oracle-status` | 승인 상태, 마스킹이 실제로 가린 값 | 사람 | [oracle-status.txt](oracle-status.txt) |
| 5 결함 주입 | `parity mutate` | 탐지율 (연산자별, 테스트별, 생존 결함) | 보고서 입력 | [mutation-sample.json](mutation-sample.json) |
| 6 to-be 비교 | `pytest --compare golden/<app>` | 터미널 출력: 다른 점 diff | 에이전트 | [compare-output.txt](compare-output.txt) |
| | | JUnit | 보고서·지도 입력 | [junit-legacy-tobe-fixed.xml](junit-legacy-tobe-fixed.xml) |
| | | 실패 시점 스크린샷 | 사람 | [test_order_save-fail.png](test_order_save-fail.png) |
| | | 실행 원장 (`runs/<app>/<시각>.json`) | 상태 명령·관리 화면 입력 | [run-ledger-sample.json](run-ledger-sample.json) |
| | `parity report` | 검증 보고서 (신뢰 확인 → 판정 → 다른 점 → 탐지율) | **사람** (최종) | [verification-sample.html](verification-sample.html), [.md](verification-sample.md) |
| | `parity map --junit` | 화면 지도: 라우트 네트워크 + 라우트 안의 팝업·드로워·탭 그래프 (다른 화면은 빨갛게) | 사람 | [map-portal.html](map-portal.html), [map-reservation.html](map-reservation.html), [map-legacy-tobe-fixed.html](map-legacy-tobe-fixed.html), 통합 화면 캡처 [ui-map.png](ui-map.png) |
| 7 루프 | `parity ui` | 통합 화면 (개요·이력·승인 검토·지도·보고서, 지난 실행 선택) | **사람** | [ui-history.png](ui-history.png), [ui-overview.png](ui-overview.png) |
| | `parity status` | 남은 실패, 지난 실행 대비 변화 | 개발자·에이전트 | [status.txt](status.txt) |
| | `parity catalog` | 골든 관리 화면 한 장 (통합 화면의 개요 탭) | 사람 | [catalog-sample.html](catalog-sample.html) |
| 스모크 | `parity targets` | 화면별 조회 버튼 결정 (rule / review) | 사람이 review 행만 | [smoke/targets-output.txt](smoke/targets-output.txt), [smoke/screens.targets.yaml](smoke/screens.targets.yaml) |
| | `parity run … --triage` | 화면별 결과, 실패 분류 (real_defect·ui_changed·environment…) | 에이전트 → 사람 | [smoke/run-output.txt](smoke/run-output.txt), [smoke/junit-smoke.xml](smoke/junit-smoke.xml) |

## 이 샘플이 보여주는 상황

- **동등성 검증**: as-is `legacy`(8801)에서 기록·승인한 시나리오 6개를 `tobe-fixed`(8803)와 비교. to-be가 as-is 버그 두 개(부가세 10원 절사, 수량 0 허용)를 "고쳐버려서" 3개가 다르다.
  보고서 판정은 **신뢰 가능**(승인본·전수 실행·탐지율 100% 모두 ✅)이고 다른 점 3건이 나열된다. 신뢰 가능은 "결과를 믿어도 된다"는 뜻이지 "같다"는 뜻이 아니다.
- **루프**: 원장 `runs/legacy/`에 실행 3회 — `tobe-fixed`(8803, 3 다름) → `tobe-renamed`(8804, 이름 매핑 적용, 부가세 반올림 1 다름) → `tobe`(8802, 모두 같음).
  통합 화면 이력 탭에 "+2 통과로 / 1 계속 실패", "+1 통과로"가 찍히고, 실행을 고르면 그 실행의 지도·보고서가 다시 그려진다 (JUnit·스크린샷 사본이 `runs/legacy/<시각>/`에 있다).
- **탐색**: 예약 데모(8787) 로그인부터 깊이 4까지 → 상태 10개, 동작 26개, 시나리오 6개. 로그인 실패·필수값 누락·확정·취소 경로가 모두 잡혔다.
- **화면 지도 (포털 데모, `demo-app/portal_app.py`)**: 홈 → 주문 목록(필터 드로워, 신규 주문 팝업) → 주문 상세(기본 정보/이력 탭, 취소 confirm), 고객 목록(등록 팝업) → 고객 상세(메모 드로워), 설정(탭, 저장 alert).
  crawl 초안 176개 중 18개 + 업무 값 테스트 2개를 `e2e/portal/`에 두고 `golden/portal`에 기록(승인 전). to-be(8821)는 as-is 버그 두 개(부가세 10원 절사, 취소된 주문 재취소)를 고쳐서 라우트 6개 중 2개가 빨갛다.
  `golden/reservation`도 같은 방식(crawl 초안 6개 그대로). 두 골든은 **승인되지 않은 데모**라 비교는 `--allow-unapproved`로 돌렸고 보고서는 신뢰 불가로 표시된다 — 승인은 사람이 터미널에서.
- **스모크**: ERP 화면 12개를 조회 버튼 규칙으로 9개 확정, 3개는 사람이 고름. to-be(8812)에 결함 5개가 심겨 있고 5개 모두 `real_defect`로 분류됐다.

## 다시 만들기

```bash
python demo-app/legacy_app.py 8801 asis & python demo-app/legacy_app.py 8803 tobe-fixed &
python demo-app/app.py 8787 & python demo-app/erp_app.py 8811 asis & python demo-app/erp_app.py 8812 tobe &
uv run parity crawl http://127.0.0.1:8787/login --fixtures examples/crawl/demo_fixtures.yaml --out crawl/reservation --depth 4
uv run pytest e2e/legacy --base-url http://127.0.0.1:8803 --compare golden/legacy --junitxml reports/junit-legacy-tobe-fixed.xml
uv run parity report --oracle golden/legacy --junit reports/junit-legacy-tobe-fixed.xml --mutation reports/mutation-legacy-golden.json --out reports/verification-legacy-tobe-fixed.md
uv run parity map golden/legacy --junit reports/junit-legacy-tobe-fixed.xml
uv run parity status golden/legacy && uv run parity catalog golden/legacy
uv run parity targets scenarios/smoke/screens.yaml --base-url http://127.0.0.1:8811 --out scenarios/smoke/screens.targets.yaml
uv run parity run scenarios/smoke/screen_smoke_targets.yaml --base-url http://127.0.0.1:8812 --junit reports/junit-smoke.xml --triage
```
