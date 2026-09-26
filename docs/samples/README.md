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
| | | 승인 검토 화면 (포털, 25개) | **사람** | [review-sample.html](review-sample.html) |
| 4 승인 | `parity approve` (터미널) 또는 `parity ui` 승인 검토 탭(터미널에 찍힌 코드) | 승인 도장: 파일 해시 + 확인한 기대값. 웹 승인 화면 [ui-approve.png](ui-approve.png) | 도구 (비교 전 대조) | [golden/legacy/APPROVED.json](../../golden/legacy/APPROVED.json) |
| | `parity oracle-status` | 승인 상태, 마스킹이 실제로 가린 값 | 사람 | [oracle-status.txt](oracle-status.txt) |
| 5 결함 주입 | `parity mutate` | 탐지율 (연산자별, 테스트별, 생존 결함) | 보고서 입력 | [mutation-sample.json](mutation-sample.json) |
| 6 to-be 비교 | `pytest --compare golden/<app>` | 터미널 출력: 다른 점 diff | 에이전트 | [compare-output.txt](compare-output.txt) |
| | | JUnit | 보고서·지도 입력 | [junit-portal-tobe-fixed.xml](junit-portal-tobe-fixed.xml) |
| | | 실패 시점 스크린샷 | 사람 | [test_portal_vat_truncation-fail.png](test_portal_vat_truncation-fail.png), [test_portal_cancel_twice-fail.png](test_portal_cancel_twice-fail.png) |
| | | 실행 원장 (`runs/<app>/<시각>.json`) | 상태 명령·관리 화면 입력 | [run-ledger-sample.json](run-ledger-sample.json) |
| | `parity report` | 검증 보고서 (신뢰 확인 → 판정 → 다른 점 → 탐지율) | **사람** (최종) | [verification-sample.html](verification-sample.html), [.md](verification-sample.md) |
| | `parity map --junit [--tobe-crawl]` | 화면 지도 (to-be 비교, as-is 기준): 라우트 네트워크 → 화면을 누르면 상세 페이지(큰 캡처, 안의 팝업·드로워·탭 그래프, 오는 길·가는 길, 테스트). 빨강 다름 · 노랑 미개발 · 파랑 새 화면, 캡처는 to-be 우선. 개발 중 to-be 예 [map-portal-wip.html](map-portal-wip.html), 캡처 [ui-map-compare.png](ui-map-compare.png). 상세 캡처 [ui-map-detail.png](ui-map-detail.png), 상태 팝업 [ui-map-state.png](ui-map-state.png) | 사람 |
| | `parity map --crawl` | 화면 지도 (as-is 탐색 / to-be 탐색): crawl 결과를 그대로 이은 지도. 통합 화면에서는 출처 바로 셋을 오간다 [ui-map-asis.png](ui-map-asis.png), [ui-map-tobe.png](ui-map-tobe.png), 탐색 전 [ui-map-tobe-explore.png](ui-map-tobe-explore.png) | 사람 | [map-portal-asis.html](map-portal-asis.html) | [map-portal.html](map-portal.html), [map-reservation.html](map-reservation.html), [map-legacy-tobe-fixed.html](map-legacy-tobe-fixed.html), 통합 화면 캡처 [ui-map.png](ui-map.png) |
| 0 등록 | `parity ui` 첫 화면 | 프로젝트 목록 (as-is/to-be 소스 위치·주소, 다음 단계 안내) → `parity.json` | **사람** | [ui-projects.png](ui-projects.png), 추가 창 [ui-add-project.png](ui-add-project.png), [parity.json](../../parity.json) |
| 7 루프 | `parity ui` | 프로젝트 화면 (화면 지도가 첫 탭 · 개요·이력·승인 검토·보고서, 지난 실행 선택). 골든이 없으면 "화면 지도 만들기"(탐색 → 초안 → 기록) [ui-init.png](ui-init.png) | **사람** | [ui-history.png](ui-history.png), [ui-overview.png](ui-overview.png) |
| | `parity status` | 남은 실패, 지난 실행 대비 변화 | 개발자·에이전트 | [status.txt](status.txt) |
| | `parity catalog` | 골든 관리 화면 한 장 (통합 화면의 개요 탭) | 사람 | [catalog-sample.html](catalog-sample.html) |
| 스모크 | `parity targets` | 화면별 조회 버튼 결정 (rule / review) | 사람이 review 행만 | [smoke/targets-output.txt](smoke/targets-output.txt), [smoke/screens.targets.yaml](smoke/screens.targets.yaml) |
| | `parity run … --triage` | 화면별 결과, 실패 분류 (real_defect·ui_changed·environment…) | 에이전트 → 사람 | [smoke/run-output.txt](smoke/run-output.txt), [smoke/junit-smoke.xml](smoke/junit-smoke.xml) |

## 이 샘플이 보여주는 상황

- **메인 데모 = 포털** (`demo-app/portal_app.py`, 8820~8824): 시나리오 25개(crawl 초안 18 + 업무 값 7)를 as-is에서 기록해 사람이 승인(east, 2026-09-25 22:43).
  비교 5회가 `runs/portal/`에 이력으로 있다: `tobe-fixed` 2 다름(부가세 절사→반올림, 취소된 주문 재취소 차단) → `tobe-renamed` 19 다름(라벨 변경, 이름 매핑 없이) → `tobe-custom` 모두 같음 → `tobe` 모두 같음 → `tobe-modern`(8825, 사이드바·보라색의 새 룩 [portal-tobe-modern.png](portal-tobe-modern.png)) 모두 같음 — 겉모습이 달라도 글자·동작이 같으면 통과한다.
  결함 주입은 승인본 기준 148개 중 144개 탐지(97%), 생존 4개는 탭 없는 화면의 탭 코드(동등 결함) — [mutation-sample.json](mutation-sample.json). 검증 보고서 [verification-sample.html](verification-sample.html)은 `tobe-fixed` 실행으로 만든 것: 판정 **신뢰 가능**, 다른 점 2건.
  실행 원장·JUnit·실패 화면 샘플도 같은 실행(2026-09-25 22:45, 8822)에서 왔다.
- **신뢰 가능 ≠ 같음**: 보고서의 판정은 승인본·전수 실행·탐지율이 모두 ✅라는 뜻이다. 포털 `tobe-fixed`처럼 다른 점 2건이 있어도 "그 결과를 믿어도 된다"고 말할 뿐이다.
- **레거시** (`golden/legacy`, frameset 데모): 시나리오 6개, `tobe-fixed`(8803)는 as-is 버그 두 개(부가세 10원 절사, 수량 0 허용)를 고쳐 3개가 다르다. 마스킹 규칙·이름 매핑 샘플은 이 골든에 있다.
- **루프**: 원장 `runs/legacy/`에 실행 3회 — `tobe-fixed`(8803, 3 다름) → `tobe-renamed`(8804, 이름 매핑 적용, 부가세 반올림 1 다름) → `tobe`(8802, 모두 같음).
  통합 화면 이력 탭에 "+2 통과로 / 1 계속 실패", "+1 통과로"가 찍히고, 실행을 고르면 그 실행의 지도·보고서가 다시 그려진다 (JUnit·스크린샷 사본이 `runs/legacy/<시각>/`에 있다).
- **탐색**: 예약 데모(8787) 로그인부터 깊이 4까지 → 상태 10개, 동작 26개, 시나리오 6개. 로그인 실패·필수값 누락·확정·취소 경로가 모두 잡혔다.
- **화면 지도 (포털)**: 홈 → 주문 목록(필터 드로워, 신규 주문 팝업) → 주문 상세(기본 정보/이력 탭, 취소 confirm), 고객 목록(등록 팝업) → 고객 상세(메모 드로워), 설정(탭, 저장 alert).
  [map-portal.html](map-portal.html)은 `tobe-fixed` 실행 기준이라 라우트 6개 중 2개가 빨갛다. `golden/reservation`은 crawl 초안 6개 그대로 기록한 **승인 전** 골든이라 목록에 "승인 필요"로 보인다.
- **스모크**: ERP 화면 12개를 조회 버튼 규칙으로 9개 확정, 3개는 사람이 고름. to-be(8812)에 결함 5개가 심겨 있고 5개 모두 `real_defect`로 분류됐다.

## 다시 만들기

```bash
python demo-app/app.py 8787 & python demo-app/erp_app.py 8811 asis & python demo-app/erp_app.py 8812 tobe &
uv run parity crawl http://127.0.0.1:8787/login --fixtures examples/crawl/demo_fixtures.yaml --out crawl/reservation --depth 4
python demo-app/portal_app.py 8820 asis & python demo-app/portal_app.py 8822 tobe-fixed &
uv run parity mutate e2e/portal --base-url http://127.0.0.1:8820 --compare golden/portal --max-per-op 100
uv run pytest e2e/portal --base-url http://127.0.0.1:8822 --compare golden/portal --junitxml reports/junit-portal.xml
uv run parity report --oracle golden/portal --junit reports/junit-portal.xml --mutation reports/mutation-portal-golden.json --out reports/verification-portal.md
uv run parity map golden/portal --junit reports/junit-portal.xml
uv run parity status golden/portal && uv run parity catalog golden/portal
uv run parity targets scenarios/smoke/screens.yaml --base-url http://127.0.0.1:8811 --out scenarios/smoke/screens.targets.yaml
uv run parity run scenarios/smoke/screen_smoke_targets.yaml --base-url http://127.0.0.1:8812 --junit reports/junit-smoke.xml --triage
```
