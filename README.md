# EastShift

as-is 앱의 실제 동작을 기준으로 to-be 앱이 **버그까지 똑같이** 동작하는지 검증하는 도구. 차세대 전환 프로젝트용.

정답(오라클)은 코드의 의도가 아니라 **실행 중인 as-is의 관찰 결과**다. 사람이 승인한 기준만 비교에 쓰고, 테스트가 결함을 실제로 잡는지 결함 주입으로 증명하며,
보고서는 산출물에서만 만든다. 에이전트(Claude Code)가 테스트를 쓰고 돌려도 결과를 믿을 수 있게 하는 것이 설계 목표다.

## 구성

| 도구 | 하는 일 | 쓰는 곳 |
| :--- | :--- | :--- |
| **Playwright `ui` fixture** (`eastshift/pwtest`, pytest 플러그인) | 프레임 무관 요소 찾기, 대화상자, 위젯 어댑터, as-is 골든 기록/비교, 사람 승인, 결함 주입, 검증 보고서 | 핵심 업무 흐름의 as-is/to-be 동등성 |
| **YAML 러너** (`eastshift run`) + `eastshift targets` | 시나리오 하나를 화면 목록에 돌림. 화면별 조회 버튼은 실행 전에 규칙으로 정하고 애매한 것만 사람이 고름 | 화면 수백 개를 넓게 훑는 스모크 |
| **실패 분류** (`eastshift run --triage`, `eastshift triage`) | 실패·diff 스텝의 원인을 Jev가 분류표(ui_changed·real_defect·environment·timing·test_bug) 안에서 고른다. 낮은 margin만 사람 검토 | 스모크 결과 정리, 재실행 여부 판단 |
| **탐색기** (`eastshift crawl`) | 화면의 동작을 모두 눌러 흐름 그래프, 시나리오, Playwright 테스트 초안 생성. 목록은 분기 열(상태·유형)의 값마다 대표 행만 누른다 (분기 열: 픽스처 > Jev 분류 > 규칙). 텍스트 생성 LLM 없음 | 흐름 지도, 스모크·테스트 초안 |
| **라우트 대조** (`eastshift routes`) | 소스의 라우팅 선언(Flask·Spring·Express·JSP·Struts·손 라우팅 …)에서 화면 주소 목록을 뽑아 지도와 대조. 코드에는 있는데 탐색·시나리오가 못 간 화면이 회색으로 뜬다. 통합 화면의 탐색 버튼이 자동으로 돌린다 | 탐색 완전성 확인 |

API 키 없이 모든 흐름이 돈다. Jev(요소 선택 모델)는 선택 사항이다 ([docs/YAML_RUNNER.md](docs/YAML_RUNNER.md)). 텍스트 생성 LLM은 쓰지 않는다. 입력값은 테스트에 고정하고, 검증은 결정론적이다. 테스트 작성은 Claude Code 스킬이 맡는다: `e2e-tests`(동등성 검증), `smoke`(전체 화면 스모크).

## 시작

```bash
uv sync
uv run playwright install chromium
# 선택: cp .env.example .env 후 TYPESAFE_API_KEY (자연어 스텝을 Jev가 고르게 할 때만. 기본 흐름에는 필요 없음)
```

## 전환 검증 흐름

```
as-is 기록 → 웹 검토·승인 → 결함 주입(탐지율) → to-be 비교 → 검증 보고서
```

```bash
uv run pytest e2e/<app> --base-url $ASIS --record golden/<app>      # as-is 기록 → golden/<app>/
uv run eastshift ui                                                       # 시나리오 승인 탭에서 각 시나리오 확인 → 이름 입력 → 승인
uv run eastshift mutate e2e/<app> --base-url $ASIS --compare golden/<app>   # 테스트가 결함을 잡는지 측정 (끊기면 같은 명령으로 이어서. 많으면 --max-mutants N)
uv run pytest e2e/<app> --base-url $TOBE --compare golden/<app> --junitxml reports/junit-<app>.xml   # 많으면 -n 4 (브라우저 4개, 원장은 한 건)
uv run eastshift report --oracle golden/<app> --junit reports/junit-<app>.xml --mutation reports/mutation-<app>-golden.json --out reports/verification-<app>.md   # 판정 markdown (에이전트·CI용)
```

사람이 보는 화면(Screen Map·시나리오·시나리오 승인·실행)은 전부 `eastshift ui` 한 곳이다. 파일로 따로 떨구는 명령은 없다.

수정 → 재실행 루프 (to-be 비교는 실행마다 `runs/<app>/`에 원장과 JUnit·스크린샷 사본을 남긴다. 사본 폴더는 최근 30개만 두고 원장 JSON은 다 남긴다, `EASTSHIFT_KEEP_RUNS`):

```bash
uv run eastshift ui                      # 통합 화면 http://127.0.0.1:8790 — 프로젝트 목록(추가: as-is/to-be 소스 위치·주소를 폴더 창에서 고름) → 프로젝트별 Screen Map(첫 탭. as-is 탐색 / to-be 탐색 / to-be 비교(다름·미개발·새 화면) 셋을 오가고, 없으면 버튼 하나로 탐색·기록)·시나리오(골든 관리)·시나리오 승인·검증 보고서·실행 이력, 지난 실행도 골라 본다
uv run eastshift status golden/<app>     # 터미널용: 남은 실패, 종류, 지난 실행 대비 변화 (통과로 바뀜 / 새로 실패)
```

테스트마다 서버 상태를 초기화하고 브라우저 시각을 고정할 수 있다. 초기화 경로는 대상 앱이 제공하는 테스트 전용 POST 엔드포인트다. `--record`에서 지정한 설정은 골든에 기록되고 `--compare`와 `eastshift mutate`가 자동으로 재사용한다.

```bash
uv run pytest e2e/<app> --base-url $ASIS --record golden/<app> --reset-path /test/reset --fixed-time 2026-09-27T09:00:00+09:00
```

`golden/<app>/oracle.json`에는 `coverage`(필수 업무 경우와 테스트 연결), `allowed_differences`(정확한 diff SHA-256과 사유), `api_compare`(화면에 보이지 않는 API 응답도 비교할 경로), `redact_fields`·`redact_patterns`(저장 전 민감정보 제거)를 선언할 수 있다. API 비교와 민감정보 규칙은 **기록 전에** 설정한다. 이 파일을 바꾸면 웹에서 다시 승인해야 한다. 보고서는 증거 유효성·실행한 시나리오의 동등성·등록된 업무 범위를 각각 판정한다. 탐색에서 못 찾은 화면은 미개발 확정으로 취급하지 않는다.

절차, 신뢰 장치, 데모 결과: [docs/MIGRATION.md](docs/MIGRATION.md).

## 스모크와 탐색

```bash
uv run eastshift targets scenarios/<app>/screens.yaml --base-url $ASIS --out scenarios/<app>/screens.targets.yaml   # 조회 버튼 정하기 (누르지 않음)
uv run eastshift run scenarios/<app>/screen_smoke_targets.yaml --base-url $TOBE --junit reports/junit-smoke.xml --workers 4   # 화면이 많으면 브라우저 여러 개
uv run eastshift crawl <시작 URL> --fixtures f.yaml --out crawl/<app> --dry-run   # 누를 버튼 확인 (저장·확정도 실제로 누른다)
uv run eastshift routes <소스 폴더> --out crawl/<app>/routes.json                  # 소스가 선언한 화면 주소 → 지도가 못 간 화면을 회색으로 (완전성 잣대)
```

## 문서

| 문서 | 내용 |
| :--- | :--- |
| [docs/MIGRATION.md](docs/MIGRATION.md) | 전환 검증 절차(탐색 → 시나리오 → 기록 → 승인 → 비교 → 원장 → 루프), 신뢰 장치 표, 관리 화면, 데모 결과 |
| [docs/samples/README.md](docs/samples/README.md) | 산출물 샘플: 단계마다 무엇이 나오고 누가 읽는지 (실제 데모 실행 결과) |
| [docs/SMOKE.md](docs/SMOKE.md) | 시나리오 하나로 화면 N개 스모크 (matrix) |
| [docs/CRAWL.md](docs/CRAWL.md) | 화면 탐색기: 동작 방식, 안전장치, 산출물 |
| [docs/YAML_RUNNER.md](docs/YAML_RUNNER.md) | Jev 러너: 시나리오 형식, expect 키, 동작 원리, 한계 |
| 실험 기록 | [docs/JEV_VS_PLAYWRIGHT.md](docs/JEV_VS_PLAYWRIGHT.md) (요소 찾기 방식 비교 → 역할 분담의 근거), [docs/DEV_LOOP.md](docs/DEV_LOOP.md) (개발 루프 세 바퀴), [docs/COST_COMPARISON.md](docs/COST_COMPARISON.md) (Jev vs 일반 LLM 토큰 비용) |

## 저장소 구성

```
eastshift/            패키지. pwtest/ (Playwright 검증), runner.py·snapshot.py·jev.py (Jev 러너), crawl.py, observe.py (비교 정규화)
e2e/<app>/         Playwright 테스트            golden/<app>/   승인된 오라클 (골든, 스크린샷, 규칙, APPROVED.json)
eastshift.json        프로젝트 등록부: as-is/to-be 소스 위치·실행 주소 (eastshift ui 첫 화면에서 추가·설정)
runs/<app>/        to-be 비교 실행 원장 (실행마다 JSON + JUnit·스크린샷 사본, mutations/ 결함 주입 결과) → eastshift ui / status
scenarios/         YAML 시나리오 (스모크, 데모)  examples/       YAML 전환 예제, 비교 실험 코드, crawl 픽스처
demo-app/          데모 서버. 포털(메인 데모: 라우트 7개 + 팝업·드로워·탭, as-is에만 있는 공지사항 화면, as-is/to-be 변형 6개 — 새 룩 tobe-modern 포함), 레거시 주문(frameset 특수 케이스), 예약(crawl), ERP 화면 12개(스모크)
tools/             explore.py (요소 이름 탐색), guard_oracle.py (Claude Code hook: golden/ 편집·승인 차단)
.claude/skills/    e2e-tests, smoke
```

`reports/`, `.eastshift-cache/`, `crawl/`, `.env`, `.auth/`는 커밋하지 않는다.
