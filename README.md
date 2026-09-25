# parity

as-is 앱의 실제 동작을 기준으로 to-be 앱이 **버그까지 똑같이** 동작하는지 검증하는 도구. 차세대 전환 프로젝트용.

정답(오라클)은 코드의 의도가 아니라 **실행 중인 as-is의 관찰 결과**다. 사람이 승인한 기준만 비교에 쓰고, 테스트가 결함을 실제로 잡는지 결함 주입으로 증명하며,
보고서는 산출물에서만 만든다. 에이전트(Claude Code)가 테스트를 쓰고 돌려도 결과를 믿을 수 있게 하는 것이 설계 목표다.

## 구성

| 도구 | 하는 일 | 쓰는 곳 |
| :--- | :--- | :--- |
| **Playwright `ui` fixture** (`parity/pwtest`, pytest 플러그인) | 프레임 무관 요소 찾기, 대화상자, 위젯 어댑터, as-is 골든 기록/비교, 사람 승인, 결함 주입, 검증 보고서 | 핵심 업무 흐름의 as-is/to-be 동등성 |
| **YAML 러너** (`parity run`) + `parity targets` | 시나리오 하나를 화면 목록에 돌림. 화면별 조회 버튼은 실행 전에 규칙으로 정하고 애매한 것만 사람이 고름 | 화면 수백 개를 넓게 훑는 스모크 |
| **실패 분류** (`parity run --triage`, `parity triage`) | 실패·diff 스텝의 원인을 Jev가 분류표(ui_changed·real_defect·environment·timing·test_bug) 안에서 고른다. 낮은 margin만 사람 검토 | 스모크 결과 정리, 재실행 여부 판단 |
| **탐색기** (`parity crawl`) | 화면의 동작을 모두 눌러 흐름 그래프, 시나리오, Playwright 테스트 초안 생성. LLM 호출 없음 | 흐름 지도, 스모크·테스트 초안 |

API 키 없이 모든 흐름이 돈다. Jev(요소 선택 모델)는 선택 사항이다 ([docs/YAML_RUNNER.md](docs/YAML_RUNNER.md)). 텍스트 생성 LLM은 쓰지 않는다. 입력값은 테스트에 고정하고, 검증은 결정론적이다. 테스트 작성은 Claude Code 스킬이 맡는다: `e2e-tests`(동등성 검증), `smoke`(전체 화면 스모크).

## 시작

```bash
uv sync
uv run playwright install chromium
# 선택: cp .env.example .env 후 TYPESAFE_API_KEY (자연어 스텝을 Jev가 고르게 할 때만. 기본 흐름에는 필요 없음)
```

## 전환 검증 흐름

```
as-is 기록 → 검토 화면 → 사람 승인 → 결함 주입(탐지율) → to-be 비교 → 검증 보고서
```

```bash
uv run pytest e2e/<app> --base-url $ASIS --record golden/<app>      # as-is 기록 + 승인 검토 화면 (reports/review-<app>.html)
uv run parity approve golden/<app> --by <이름>                        # 사람이 터미널에서 (에이전트는 못 한다)
uv run parity mutate e2e/<app> --base-url $ASIS --compare golden/<app>   # 테스트가 결함을 잡는지 측정
uv run pytest e2e/<app> --base-url $TOBE --compare golden/<app> --junitxml reports/junit-<app>.xml
uv run parity report --oracle golden/<app> --junit reports/junit-<app>.xml --mutation reports/mutation-<app>-golden.json --out reports/verification-<app>.md
uv run parity map golden/<app> --junit reports/junit-<app>.xml     # 화면 지도: 화면 네트워크 + 대표 캡처, 다른 화면은 빨갛게
```

절차, 신뢰 장치, 데모 결과: [docs/MIGRATION.md](docs/MIGRATION.md).

## 스모크와 탐색

```bash
uv run parity targets scenarios/<app>/screens.yaml --base-url $ASIS --out scenarios/<app>/screens.targets.yaml   # 조회 버튼 정하기 (누르지 않음)
uv run parity run scenarios/<app>/screen_smoke_targets.yaml --base-url $TOBE --junit reports/junit-smoke.xml
uv run parity crawl <시작 URL> --fixtures f.yaml --out crawl/<app> --dry-run   # 누를 버튼 확인 (저장·확정도 실제로 누른다)
```

## 문서

| 문서 | 내용 |
| :--- | :--- |
| [docs/MIGRATION.md](docs/MIGRATION.md) | 전환 검증 절차, 신뢰 장치 표, 데모 결과 |
| [docs/SMOKE.md](docs/SMOKE.md) | 시나리오 하나로 화면 N개 스모크 (matrix) |
| [docs/CRAWL.md](docs/CRAWL.md) | 화면 탐색기: 동작 방식, 안전장치, 산출물 |
| [docs/YAML_RUNNER.md](docs/YAML_RUNNER.md) | Jev 러너: 시나리오 형식, expect 키, 동작 원리, 한계 |
| 실험 기록 | [docs/JEV_VS_PLAYWRIGHT.md](docs/JEV_VS_PLAYWRIGHT.md) (요소 찾기 방식 비교 → 역할 분담의 근거), [docs/DEV_LOOP.md](docs/DEV_LOOP.md) (개발 루프 세 바퀴), [docs/COST_COMPARISON.md](docs/COST_COMPARISON.md) (Jev vs 일반 LLM 토큰 비용) |

## 저장소 구성

```
parity/            패키지. pwtest/ (Playwright 검증), runner.py·snapshot.py·jev.py (Jev 러너), crawl.py, observe.py (비교 정규화)
e2e/<app>/         Playwright 테스트            golden/<app>/   승인된 오라클 (골든, 스크린샷, 규칙, APPROVED.json)
scenarios/         YAML 시나리오 (스모크, 데모)  examples/       YAML 전환 예제, 비교 실험 코드, crawl 픽스처
demo-app/          데모 서버 (예약, 레거시 주문 as-is/to-be 변형, ERP 화면 12개)
tools/             explore.py (요소 이름 탐색), guard_oracle.py (Claude Code hook: golden/ 편집·승인 차단)
.claude/skills/    e2e-tests, smoke
```

`reports/`, `.parity-cache/`, `crawl/`, `.env`, `.auth/`는 커밋하지 않는다.
