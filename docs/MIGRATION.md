# 차세대 전환 검증: as-is 동작을 버그까지 to-be에서 재현하는지 확인

목표는 "to-be가 올바른가"가 아니라 "to-be가 as-is와 **똑같이** 동작하는가"다. 그래서 정답(오라클)은 코드의 의도가 아니라 **실행 중인 as-is의 실제 동작**이다.
as-is에 버그가 있으면 그 버그가 곧 기대값이다. Claude Code에서는 `e2e-tests` 스킬(migration 모드)이 이 절차를 따른다.

## 역할 분담

| 무엇 | 도구 |
| :--- | :--- |
| 핵심 업무 화면의 동등성 (값, 메시지, 버그까지) | Playwright + `ui` fixture (`e2e/<app>/`), 이 문서 |
| 전체 화면을 넓게 훑는 스모크 (열림, 조회, 상세) | Jev 러너 matrix, [SMOKE.md](SMOKE.md) (`smoke` 스킬) |

근거: [JEV_VS_PLAYWRIGHT.md](JEV_VS_PLAYWRIGHT.md), [SMOKE.md](SMOKE.md).

## 절차

| 단계 | 하는 일 | 누가 | 명령 · 산출물 |
| :--- | :--- | :--- | :--- |
| 1 탐색 | as-is 화면을 눌러 흐름 그래프와 테스트 초안을 뽑는다 (LLM 없음) | 도구 | `parity crawl` → `crawl/<app>/` ([CRAWL.md](CRAWL.md)) |
| 2 시나리오 | 초안에 업무 값(금액, 문구)을 붙여 `e2e/<app>/test_*.py`로. as-is에서 두 번 연속 통과할 때까지 기대값을 as-is에 맞춘다 | 에이전트 | `pytest e2e/<app> --base-url $ASIS` |
| 3 기록 | 동작마다 as-is 화면 내용·입력값·대화상자·캡처를 저장. 통과한 테스트만 기록된다 | 도구 | `--record golden/<app>` → `golden/<app>/`, `reports/review-<app>.html` |
| 4 승인 | 개발자가 검토 화면에서 시나리오를 하나씩 확인하고 터미널에서 승인 | **사람** | `parity approve` → `APPROVED.json` |
| 5 결함 주입 | 테스트가 결함을 잡는지 측정 (80% 이상, 생존 결함은 판정) | 도구 + 사람 판정 | `parity mutate` → `reports/mutation-<app>-golden.json` |
| 6 to-be 비교 | 같은 테스트, 같은 골든. 실행마다 원장에 남는다 | 도구 | `--compare golden/<app>` → JUnit, `runs/<app>/<시각>.json` |
| 7 루프 | 남은 실패 → to-be 수정 → 재실행. 지난 실행 대비 무엇이 바뀌었는지만 본다 | 개발자 + 에이전트 | `parity status`, `parity catalog`, `parity report`, `parity map` |

```bash
# 0) 같은 DB 스냅샷, 같은 기준 시각으로 as-is와 to-be를 띄운다 (데이터가 다르면 비교가 무의미)
uv run pytest e2e/<app> --base-url $ASIS                                            # 2) 두 번 연속 통과
uv run pytest e2e/<app> --base-url $ASIS --record golden/<app>                      # 3) 기록 + 검토 화면
uv run parity approve golden/<app> --by <이름>                                       # 4) 사람
uv run parity mutate e2e/<app> --base-url $ASIS --compare golden/<app> --max-per-op 100   # 5)
uv run pytest e2e/<app> --base-url $TOBE --compare golden/<app> --junitxml reports/junit-<app>.xml   # 6)
uv run parity status golden/<app>                                                    # 7) 남은 실패, 지난 실행 대비 변화
```

`e2e/<app>/`, `golden/<app>/`, `runs/<app>/`가 산출물이다. 저장소에 커밋한다. 골든을 다시 기록하면 재승인이 필요하고, 원장에는 어느 승인본으로 실행했는지 남는다.

## 에이전트가 진행해도 결과를 믿을 수 있게 하는 장치

에이전트(Claude Code)가 테스트를 쓰고 돌려도, "통과했다"는 말을 믿지 않아도 되게 만든다. 판단이 들어가는 곳마다 기계 검사나 사람 승인이 붙는다.

| 위험 | 장치 | 어디서 |
| :--- | :--- | :--- |
| 기대값을 틀리게 쓰거나, to-be에 맞춰 고침 | 기대값은 as-is에서 통과해야 기록되고, 기록 이후 바뀌면 `expectation changed since as-is recording`으로 실패 | `ui.py` `assertion_drift` |
| 골든을 to-be에서 다시 기록 | 골든의 기록 URL과 비교 대상 URL이 같으면 거부 | `ui.py` |
| 골든·마스킹 규칙·이름 매핑을 몰래 수정 | `golden/<app>/` 전체의 sha256을 사람이 승인 (`APPROVED.json`). 승인 후 바뀌면 비교 자체를 거부 | `oracle.py`, `plugin.py` |
| 에이전트가 스스로 승인 | 승인은 터미널에서 디렉터리 이름을 직접 입력해야 한다. 에이전트 도구에는 터미널이 없다 | `parity approve` |
| 에이전트가 오라클 파일을 직접 편집 | Claude Code hook이 golden/ 쓰기와 승인 명령을 차단 | `.claude/settings.json`, `tools/guard_oracle.py` |
| 마스킹 규칙이 진짜 값을 가림 | 규칙마다 실제로 가린 값을 승인 화면과 보고서에 표시 | `oracle.mask_audit` |
| 아무것도 못 잡는 약한 테스트 | 결함 주입: as-is 응답을 바꿔 결함 하나씩 넣고 탐지율 측정. 생존 결함은 테스트 보강 또는 사람이 판정한 동등 결함 | `parity mutate` |
| 실패하는 테스트를 지우거나 건너뜀 | 골든에 기록된 테스트가 비교 실행에 없으면 보고서가 ❌ | `report.py` |
| 옛 승인본으로 만든 결과를 새 승인본 보고서에 섞음 | 비교 결과(JUnit)와 결함 탐지 결과에 승인 시각이 찍히고, 현재 승인본과 다르면 ❌ | `plugin.py`, `mutation.py`, `report.py` |
| 테스트 일부만 골라 결함 탐지율을 높임 | 기록된 테스트 전부가 측정에 참여하지 않으면 ❌ | `report.py` |
| 결함이 실제로 안 들어갔는데 "생존"으로 셈 | 응답에 결함이 들어갔을 때만 표식을 남기고, 표식 없는 실행은 오류로 셈 (오류가 있으면 ❌) | `mutation.py` |
| 검토하는 동안 기준 파일이 바뀜 | 승인 화면을 만들 때의 해시와 확인 입력 뒤의 해시가 다르면 승인하지 않음 | `oracle.approve` |
| 숨긴 요소에만 있는 문구로 `expect_text` 통과 | 보이는 요소만 인정 (`visible=true`) | `ui.py` |
| 보고가 실제와 다름 | 보고서는 산출물(승인 상태, JUnit, 결함 주입 JSON)에서만 생성 | `parity report` |

순서: as-is 두 번 통과 → 기록 → **사람 승인** → 결함 주입(탐지율 80% 이상, 생존 결함 판정) → to-be 비교 → 보고서.

사람이 보는 화면은 HTML 네 장이다 (서버 없이 파일로 연다. 모두 산출물만 읽고 새로 판단하지 않는다).
- `reports/review-<app>.html`: 승인 검토 (`parity/pwtest/review.py`). `--record`가 끝나면 자동으로 만들어지고, `parity approve`가 다시 연다.
  왼쪽 시나리오 목록(확인 체크, 지난 승인 이후 바뀐 것·새 것 표시, 전체/미확인/바뀐 것 필터), 가운데 선택한 시나리오의 단계별 큰 as-is 캡처(`golden/<app>/shots/`, 승인 해시에 포함)와 동작·새로 나타난 내용·알림창·그 시점에 확인한 값(바뀐 값은 이전 값도), 오른쪽 규칙(가리는 값과 실제로 가린 값, 이름 변경, 동등 결함).
  확인 체크는 보는 사람의 브라우저에만 남고(localStorage, 골든 해시별), 전부 확인되면 아래 바에 승인 명령이 나타난다. 승인 자체는 여전히 터미널에서만.
  예: `docs/samples/review-sample.html`.
- `reports/verification-<app>.html`: 검증 결과. `parity report`가 `.md`와 함께 만든다. 결론, 다른 점(무엇이 · as-is · to-be), 신뢰 확인, 결함 탐지 능력.
- `reports/catalog-<app>.html`: 골든 관리 (`parity catalog golden/<app>`). 승인 상태, 시나리오 수, 마지막 비교 결과, 실행 횟수. 시나리오 표(제목, 단계, 확인 값, 마지막 결과, 실행 이력 점), 행을 누르면 필름스트립·단계·마지막 실행에서 다른 점·실행 이력. 화면 지도·검증 보고서·승인 검토로 가는 링크. 예: `docs/samples/catalog-sample.html`.
- `reports/map-<app>[-<대상>].html`: 화면 지도. `parity map golden/<app> [--junit …]`. 골든의 단계들을 화면 단위로 합쳐 네트워크로 그린다.
  세 칸 구성(Playwright Trace Viewer의 동작 목록·필름스트립·미리보기 구성을 따랐다): 왼쪽 화면 목록(검색, 상태 점), 가운데 지도, 오른쪽 선택한 화면.
  지도는 왼쪽에서 오른쪽 한 방향, 열은 시작 화면에서 몇 번 눌러 가는지, 열 안 순서는 무게중심(Sugiyama 방식)으로 선 교차를 줄인다.
  노드는 브라우저 프레임 모양의 화면 카드(대표 캡처, 이름, 같은 이름을 가르는 문구, 상태 띠), 화살표에는 동작 이름 알약, 되돌아가는 길은 아래 차선에 점선.
  오른쪽 패널: 큰 캡처(누르면 원본), 시작에서 오는 길(빵부스러기), 여기서 갈 수 있는 곳, 이 화면을 지나는 테스트마다 단계 필름스트립(이 화면 단계는 강조)과 동작·확인 값.
  `--junit`을 주면 다른 화면이 빨간 띠와 "다름" 표시, 패널에 무엇이 달랐는지 표. 화면 이름·주소·문구 검색, 테스트별 거르기, 확대·축소·끌기.
  같은 화면인지는 화면 구조(제목, 입력칸, 버튼)로 가리고 글자 내용(주문번호, 품목명)은 보지 않는다. 기록된 산출물만 읽는다.
  예: `docs/samples/map-reservation.html` (crawl 테스트 6개 → 화면 7개, 연결 9개). 화면 수백 개면 모듈 단위로 골든을 나눠 지도를 따로 그린다.

```bash
uv run parity oracle-status golden/<app>                          # 승인 상태, 마스킹 감사 (누구나)
uv run parity review golden/<app>                                 # 승인 검토 화면 reports/review-<app>.html (누구나, 승인은 안 함)
uv run parity approve golden/<app> --by <이름>                     # 사람이 자기 터미널에서
uv run parity mutate e2e/<app> --base-url $ASIS --compare golden/<app> --max-per-op 100
uv run pytest e2e/<app> --base-url $TOBE --compare golden/<app> --junitxml reports/junit-<app>.xml
uv run parity report --oracle golden/<app> --junit reports/junit-<app>.xml --mutation reports/mutation-<app>-golden.json --out reports/verification-<app>.md
uv run parity status golden/<app>                                 # 남은 실패, 지난 실행 대비 변화
uv run parity catalog golden/<app>                                # 골든 관리 화면 reports/catalog-<app>.html
```

데모(2026-09-24): 처음 4개 테스트의 결함 탐지율은 expect만 56%, 골든 비교를 더하면 78%. 생존 결함에서 실제 빈틈 두 개가 나왔다
(노트북·마우스 단가를 계산하는 테스트가 없음, `expect_dialog`가 부분 일치라 문구 변경을 놓침). 테스트를 보강하고 helper를 고친 뒤 전수 주입에서 34/34 = 100%.

## 두 겹의 검사

| | 무엇을 | 언제 잡나 |
| :--- | :--- | :--- |
| `ui.expect_*` | 테스트에 명시한 값 (부가세 120원, alert 문구) | 적은 것만 |
| 골든 비교 | goto/click/fill/select/check/press 직후 화면 전체 내용, 입력값, 선택값, 그 동작에서 뜬 alert/confirm/prompt | 적지 않은 것까지 |

골든 비교는 레이아웃에 둔감하다 (`parity/observe.py` `flatten`). 비대화형 요소는 텍스트만, 대화형 요소는 역할·이름·값·상태만 남기고,
드롭다운은 위젯 종류와 무관하게 `combobox "이름": 선택값` 한 줄이 된다. frameset → 단일 페이지, table → div, 네이티브 select → 커스텀 드롭다운,
heading level, 구분자(`|`), 컨테이너의 aria-label 차이는 무시된다. URL과 제목은 기본으로 비교하지 않는다.

매번 바뀌는 값은 오라클의 `golden/<app>/oracle.json` `{"ignore": ["주문번호 \\d+"]}`로 마스킹한다. 승인 대상이라 테스트 코드나 명령행으로는 바꿀 수 없다.
골든에는 스냅샷 원문이 들어 있어서 ignore나 정규화 규칙을 바꿔도 as-is를 다시 기록할 필요가 없다.

## to-be 화면이 달라졌을 때

테스트는 as-is 이름과 "무엇을 하는지"만 쓴다. to-be 차이는 테스트 밖에서 흡수한다.

| to-be 차이 | 흡수하는 곳 |
| :--- | :--- |
| 구조 (frameset, table, div) | 없음. 요소를 역할·이름으로 모든 프레임에서 찾는다 |
| 위젯 조작 방식 (커스텀 드롭다운, datepicker) | `parity/pwtest/ui.py`에 한 번. `ui.select`는 네이티브 select와 커스텀 드롭다운을 모두 다룬다 |
| 의도된 라벨 변경 ("수량" → "주문 수량") | `--name-map golden/<app>/name_map.<target>.json` (`{"as-is 이름": "to-be 이름"}`, 오라클 안에 두고 승인) |

to-be 코드를 보는 목적은 조작 방법(`ui.py`)뿐이다. 기대값은 as-is에서만 온다.

## 결과 읽기 (to-be 실행)

| 신호 | 원장 kind | 뜻 |
| :--- | :--- | :--- |
| pass | `same` | as-is와 같은 동작 |
| `not found in any frame` | `error` | to-be에서 이름이나 위젯이 바뀜 → name-map이나 `ui.py` 확장, 의도치 않았으면 결함 |
| `differs from golden` (teardown error) | `golden_diff` | 화면 내용·값·대화상자가 다름 → to-be 결함 |
| expect 실패 | `assert` | 명시한 as-is 동작이 깨짐 → to-be 결함 |
| `expectation changed since as-is recording` | `drift` | 기록 이후 테스트의 기대값이 편집됨 → 되돌린다. as-is 기록이 진실 |
| 실패 스크린샷 | | `reports/<test>-fail.png` (원장에도 경로가 남는다) |

### 수정 → 재실행 루프

`--compare` 실행마다 `runs/<app>/<시각>.json`에 대상 URL, 실행한 승인본(승인자·승인 시각), 테스트별 결과(상태, kind, 첫 오류 줄, 다른 점 표, 스크린샷)가 남는다 (`parity/pwtest/ledger.py`).
`parity status golden/<app>`은 마지막 실행의 남은 실패를 kind별로 나열하고, 직전 실행과 비교해 **통과로 바뀜 / 새로 실패 / 여전히 실패 / 새 테스트**를 보여준다. 실행한 승인본이 현재 승인본과 다르면 경고한다.
개발자는 이 출력과 `parity catalog`의 이력 점만 보고 다음 수정으로 간다. 결함 주입 실행(`--jev-mutant`)은 원장에 남기지 않는다.

데모(2026-09-25): `tobe-fixed`(8803) 비교 → 6개 중 3개 `golden_diff` → 원장 기록 → `tobe`(8802)로 재실행 → `parity status`: "통과 6 · 남은 실패 0 · 통과로 바뀜 3".

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

자연어 YAML로 같은 일을 하는 Jev 러너 버전(`parity run --record`/`--compare`)은 `examples/yaml-migration/`에 있다.

## 아직 안 되는 것

- 이름 없는 요소: `div onclick`, alt 없는 이미지 버튼, label도 title도 없는 input. `tools/explore.py`로 대표 화면을 먼저 확인할 것.
- IE 전용 기능: ActiveX, `showModalDialog`. Chromium에서 as-is가 안 돌면 기록 자체가 불가능하다.
- 화면에 안 나오는 차이: DB에 저장된 값, 배치, 외부 연동. 별도의 DB 결과 비교가 필요하다.
- `ui.py`에 아직 없는 위젯: datepicker, 가상 스크롤 그리드, 파일 업로드. 처음 만날 때 한 번 추가한다.
