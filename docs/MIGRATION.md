# 차세대 전환 검증: as-is 동작을 버그까지 to-be에서 재현하는지 확인

목표는 "to-be가 올바른가"가 아니라 "to-be가 as-is와 **똑같이** 동작하는가"다. 그래서 정답(오라클)은 코드의 의도가 아니라 **실행 중인 as-is의 실제 동작**이다.
as-is에 버그가 있으면 그 버그가 곧 기대값이다. Claude Code에서는 `e2e-tests` 스킬(migration 모드)이 이 절차를 따른다.

## 역할 분담

| 무엇 | 도구 |
| :--- | :--- |
| 핵심 업무 화면의 동등성 (값, 메시지, 버그까지) | Playwright + `ui` fixture (`e2e/<app>/`), 이 문서 |
| 전체 화면을 넓게 훑는 스모크 (열림, 조회, 상세) | jev-e2e matrix, [SMOKE.md](SMOKE.md) (`jev-smoke` 스킬) |

근거: [JEV_VS_PLAYWRIGHT.md](JEV_VS_PLAYWRIGHT.md), [SMOKE.md](SMOKE.md).

## 절차

```bash
# 0) 같은 DB 스냅샷, 같은 기준 시각으로 as-is와 to-be를 띄운다 (데이터가 다르면 비교가 무의미)

# 1) as-is 코드와 화면(tools/explore.py)을 보고 e2e/<app>/test_*.py 작성. as-is에서 두 번 연속 통과할 때까지 기대값을 as-is에 맞춘다
uv run pytest e2e/<app> --base-url $ASIS

# 2) as-is 골든 기록: 동작마다 화면 내용·입력값·대화상자를 저장. 통과한 테스트만 기록된다
uv run pytest e2e/<app> --base-url $ASIS --record golden/<app>

# 3) to-be 비교. 같은 테스트, 같은 골든
uv run pytest e2e/<app> --base-url $TOBE --compare golden/<app> --junitxml reports/junit-<app>.xml
```

`e2e/<app>/`와 `golden/<app>/`가 산출물이다. 저장소에 커밋한다.

## 에이전트가 진행해도 결과를 믿을 수 있게 하는 장치

에이전트(Claude Code)가 테스트를 쓰고 돌려도, "통과했다"는 말을 믿지 않아도 되게 만든다. 판단이 들어가는 곳마다 기계 검사나 사람 승인이 붙는다.

| 위험 | 장치 | 어디서 |
| :--- | :--- | :--- |
| 기대값을 틀리게 쓰거나, to-be에 맞춰 고침 | 기대값은 as-is에서 통과해야 기록되고, 기록 이후 바뀌면 `expectation changed since as-is recording`으로 실패 | `ui.py` `assertion_drift` |
| 골든을 to-be에서 다시 기록 | 골든의 기록 URL과 비교 대상 URL이 같으면 거부 | `ui.py` |
| 골든·마스킹 규칙·이름 매핑을 몰래 수정 | `golden/<app>/` 전체의 sha256을 사람이 승인 (`APPROVED.json`). 승인 후 바뀌면 비교 자체를 거부 | `oracle.py`, `plugin.py` |
| 에이전트가 스스로 승인 | 승인은 터미널에서 디렉터리 이름을 직접 입력해야 한다. 에이전트 도구에는 터미널이 없다 | `jev-e2e approve` |
| 에이전트가 오라클 파일을 직접 편집 | Claude Code hook이 golden/ 쓰기와 승인 명령을 차단 | `.claude/settings.json`, `tools/guard_oracle.py` |
| 마스킹 규칙이 진짜 값을 가림 | 규칙마다 실제로 가린 값을 승인 화면과 보고서에 표시 | `oracle.mask_audit` |
| 아무것도 못 잡는 약한 테스트 | 결함 주입: as-is 응답을 바꿔 결함 하나씩 넣고 탐지율 측정. 생존 결함은 테스트 보강 또는 사람이 판정한 동등 결함 | `jev-e2e mutate` |
| 실패하는 테스트를 지우거나 건너뜀 | 골든에 기록된 테스트가 비교 실행에 없으면 보고서가 ❌ | `report.py` |
| 보고가 실제와 다름 | 보고서는 산출물(승인 상태, JUnit, 결함 주입 JSON)에서만 생성 | `jev-e2e report` |

순서: as-is 두 번 통과 → 기록 → **사람 승인** → 결함 주입(탐지율 80% 이상, 생존 결함 판정) → to-be 비교 → 보고서.

사람이 보는 화면은 HTML 두 장이다 (서버 없이 파일로 연다).
- `reports/review-<app>.html`: 승인 검토. `--record`가 끝나면 자동으로 만들어지고, `jev-e2e approve`가 다시 연다. 테스트마다 단계별 as-is 화면(기록 때 찍은 스크린샷, `golden/<app>/shots/`, 승인 해시에 포함), 동작, 새로 나타난 내용, 알림창, 그 시점에 확인한 기대값. 가리는 값과 실제로 가린 값, 이름 변경, 동등 결함, 지난 승인 이후 바뀐 기대값.
- `reports/verification-<app>.html`: 검증 결과. `jev-e2e report`가 `.md`와 함께 만든다. 결론, 다른 점(무엇이 · as-is · to-be), 신뢰 확인, 결함 탐지 능력.

```bash
uv run jev-e2e oracle-status golden/<app>                          # 승인 상태, 마스킹 감사 (누구나)
uv run jev-e2e review golden/<app>                                 # 승인 검토 화면 reports/review-<app>.html (누구나, 승인은 안 함)
uv run jev-e2e approve golden/<app> --by <이름>                     # 사람이 자기 터미널에서
uv run jev-e2e mutate e2e/<app> --base-url $ASIS --compare golden/<app> --max-per-op 100
uv run pytest e2e/<app> --base-url $TOBE --compare golden/<app> --junitxml reports/junit-<app>.xml
uv run jev-e2e report --oracle golden/<app> --junit reports/junit-<app>.xml --mutation reports/mutation-<app>-golden.json --out reports/verification-<app>.md
```

데모(2026-09-24): 처음 4개 테스트의 결함 탐지율은 expect만 56%, 골든 비교를 더하면 78%. 생존 결함에서 실제 빈틈 두 개가 나왔다
(노트북·마우스 단가를 계산하는 테스트가 없음, `expect_dialog`가 부분 일치라 문구 변경을 놓침). 테스트를 보강하고 helper를 고친 뒤 전수 주입에서 34/34 = 100%.

## 두 겹의 검사

| | 무엇을 | 언제 잡나 |
| :--- | :--- | :--- |
| `ui.expect_*` | 테스트에 명시한 값 (부가세 120원, alert 문구) | 적은 것만 |
| 골든 비교 | goto/click/fill/select/check/press 직후 화면 전체 내용, 입력값, 선택값, 그 동작에서 뜬 alert/confirm/prompt | 적지 않은 것까지 |

골든 비교는 레이아웃에 둔감하다 (`jev_e2e/observe.py` `flatten`). 비대화형 요소는 텍스트만, 대화형 요소는 역할·이름·값·상태만 남기고,
드롭다운은 위젯 종류와 무관하게 `combobox "이름": 선택값` 한 줄이 된다. frameset → 단일 페이지, table → div, 네이티브 select → 커스텀 드롭다운,
heading level, 구분자(`|`), 컨테이너의 aria-label 차이는 무시된다. URL과 제목은 기본으로 비교하지 않는다.

매번 바뀌는 값은 오라클의 `golden/<app>/oracle.json` `{"ignore": ["주문번호 \\d+"]}`로 마스킹한다. 승인 대상이라 테스트 코드나 명령행으로는 바꿀 수 없다.
골든에는 스냅샷 원문이 들어 있어서 ignore나 정규화 규칙을 바꿔도 as-is를 다시 기록할 필요가 없다.

## to-be 화면이 달라졌을 때

테스트는 as-is 이름과 "무엇을 하는지"만 쓴다. to-be 차이는 테스트 밖에서 흡수한다.

| to-be 차이 | 흡수하는 곳 |
| :--- | :--- |
| 구조 (frameset, table, div) | 없음. 요소를 역할·이름으로 모든 프레임에서 찾는다 |
| 위젯 조작 방식 (커스텀 드롭다운, datepicker) | `jev_e2e/pwtest/ui.py`에 한 번. `ui.select`는 네이티브 select와 커스텀 드롭다운을 모두 다룬다 |
| 의도된 라벨 변경 ("수량" → "주문 수량") | `--name-map golden/<app>/name_map.<target>.json` (`{"as-is 이름": "to-be 이름"}`, 오라클 안에 두고 승인) |

to-be 코드를 보는 목적은 조작 방법(`ui.py`)뿐이다. 기대값은 as-is에서만 온다.

## 결과 읽기 (to-be 실행)

| 신호 | 뜻 |
| :--- | :--- |
| pass | as-is와 같은 동작 |
| `not found in any frame` | to-be에서 이름이나 위젯이 바뀜 → name-map이나 `ui.py` 확장, 의도치 않았으면 결함 |
| `differs from golden` (teardown error) | 화면 내용·값·대화상자가 다름 → to-be 결함 |
| expect 실패 | 명시한 as-is 동작이 깨짐 → to-be 결함 |
| 실패 스크린샷 | `reports/<test>-fail.png` |

## 데모 (`demo-app/legacy_app.py`, `e2e/legacy/`)

```bash
python demo-app/legacy_app.py 8801 asis &          # frameset, table, title 속성 라벨, alert/confirm, 버그 2개 (부가세 10원 절사, 수량 0 허용)
python demo-app/legacy_app.py 8802 tobe &          # 단일 페이지, URL 변경, 버그까지 그대로
python demo-app/legacy_app.py 8803 tobe-fixed &    # 버그 2개를 "고쳐버림"
python demo-app/legacy_app.py 8804 tobe-renamed &  # 라벨 변경 + 부가세 반올림
python demo-app/legacy_app.py 8805 tobe-custom &   # 품목이 커스텀 드롭다운 (React/MUI 방식)
uv run pytest e2e/legacy --base-url http://127.0.0.1:8801 --record golden/legacy
uv run pytest e2e/legacy --base-url http://127.0.0.1:8802 --compare golden/legacy
```

2026-09-24 결과 (테스트 4개):

| to-be | 결과 |
| :--- | :--- |
| `tobe` | 4/4 pass, 차이 0 |
| `tobe-custom` | 4/4 pass, 차이 0. 테스트 수정 없음 (`ui.select` 어댑터) |
| `tobe-fixed` | 3/4 실패, 차이는 바뀐 값만: `textbox "부가세": 120 → 124`, `alert: 수량을 입력하세요. → 수량은 1 이상이어야 합니다.`, 수량 0 저장 → alert |
| `tobe-renamed` + name-map 2줄 | 4개 모두 끝까지 실행, 라벨 변경은 차이로 보고, 라벨 뒤 부가세 변경도 탐지 |

자연어 YAML로 같은 일을 하는 jev-e2e 버전(`--record`/`--compare`)은 `examples/yaml-migration/`에 있다.

## 아직 안 되는 것

- 이름 없는 요소: `div onclick`, alt 없는 이미지 버튼, label도 title도 없는 input. `tools/explore.py`로 대표 화면을 먼저 확인할 것.
- IE 전용 기능: ActiveX, `showModalDialog`. Chromium에서 as-is가 안 돌면 기록 자체가 불가능하다.
- 화면에 안 나오는 차이: DB에 저장된 값, 배치, 외부 연동. 별도의 DB 결과 비교가 필요하다.
- `ui.py`에 아직 없는 위젯: datepicker, 가상 스크롤 그리드, 파일 업로드. 처음 만날 때 한 번 추가한다.
