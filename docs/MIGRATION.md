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
| 1 탐색 | as-is 화면을 눌러 흐름 그래프와 테스트 초안을 뽑는다 (LLM 없음) | 도구 | `eastshift crawl` → `crawl/<app>/` ([CRAWL.md](CRAWL.md)) |
| 2 시나리오 | 초안에 업무 값(금액, 문구)을 붙여 `e2e/<app>/test_*.py`로. as-is에서 두 번 연속 통과할 때까지 기대값을 as-is에 맞춘다 | 에이전트 | `pytest e2e/<app> --base-url $ASIS` |
| 3 기록 | 동작마다 as-is 화면 내용·입력값·대화상자·캡처를 저장. 통과한 테스트만 기록된다 | 도구 | `--record golden/<app>` → `golden/<app>/` |
| 4 승인 | 통합 웹 화면에서 시나리오를 확인하고 이름을 입력해 승인 | 검토자 | `eastshift ui` → `APPROVED.json` |
| 5 결함 주입 | 테스트가 결함을 잡는지 측정 (80% 이상, 생존 결함은 판정) | 도구 + 사람 판정 | `eastshift mutate` → `reports/mutation-<app>-golden.json` |
| 6 to-be 비교 | 같은 테스트, 같은 골든. 실행마다 원장에 남는다 | 도구 | `--compare golden/<app>` → JUnit, `runs/<app>/<시각>.json` |
| 7 루프 | 남은 실패 → to-be 수정 → 재실행. 지난 실행 대비 무엇이 바뀌었는지만 본다 | 개발자 + 에이전트 | `eastshift ui`(통합 화면), `eastshift status`, `eastshift report`(markdown) |

```bash
# 0) 같은 DB 스냅샷, 같은 기준 시각으로 as-is와 to-be를 띄운다 (데이터가 다르면 비교가 무의미)
uv run pytest e2e/<app> --base-url $ASIS                                            # 2) 두 번 연속 통과
uv run pytest e2e/<app> --base-url $ASIS --record golden/<app>                      # 3) 기록
uv run eastshift ui                                                                    # 4) 시나리오 승인 탭에서 바로 승인
uv run eastshift mutate e2e/<app> --base-url $ASIS --compare golden/<app> --max-per-op 100   # 5)
uv run pytest e2e/<app> --base-url $TOBE --compare golden/<app> --junitxml reports/junit-<app>.xml   # 6)
uv run eastshift status golden/<app>                                                    # 7) 남은 실패, 지난 실행 대비 변화
```

`e2e/<app>/`, `golden/<app>/`, `runs/<app>/`가 산출물이다. 저장소에 커밋한다. 골든을 다시 기록하면 재승인이 필요하고, 원장에는 어느 승인본으로 실행했는지 남는다.

사람은 `uv run eastshift ui` 한 화면에서 본다 (아래 "통합 화면").

## 에이전트가 진행해도 결과를 믿을 수 있게 하는 장치

에이전트(Claude Code)가 테스트를 쓰고 돌려도, "통과했다"는 말을 믿지 않아도 되게 만든다. 판단이 들어가는 곳마다 기계 검사나 사람 승인이 붙는다.

| 위험 | 장치 | 어디서 |
| :--- | :--- | :--- |
| 기대값을 틀리게 쓰거나, to-be에 맞춰 고침 | 기대값은 as-is에서 통과해야 기록되고, 기록 이후 바뀌면 `expectation changed since as-is recording`으로 실패 | `ui.py` `assertion_drift` |
| 골든을 to-be에서 다시 기록 | 골든의 기록 URL과 비교 대상 URL이 같으면 거부 | `ui.py` |
| 골든·규칙·이름 매핑을 수정 | `golden/<app>/`의 파일 해시를 `APPROVED.json`에 남긴다. 승인 후 바뀌면 비교를 거부 | `oracle.py`, `plugin.py` |
| 검토 중 기준이 바뀜 | 검토 화면이 열릴 때의 지문과 승인 요청 때의 지문이 다르면 거부. 웹 승인에는 터미널 코드가 필요 없다 | `hub.py`, `oracle.approve_from_review` |
| 에이전트가 오라클 파일을 직접 편집 | Claude Code hook이 golden/ 쓰기와 APPROVED.json 쓰기를 차단. 승인은 사람이 띄운 웹 화면에서만 | `.claude/settings.json`, `tools/guard_oracle.py` |
| 마스킹 규칙이 진짜 값을 가림 | 규칙마다 실제로 가린 값을 승인 화면과 보고서에 표시 | `oracle.mask_audit` |
| 아무것도 못 잡는 약한 테스트 | 결함 주입: as-is 응답을 바꿔 결함 하나씩 넣고 탐지율 측정. 생존 결함은 테스트 보강 또는 사람이 판정한 동등 결함 | `eastshift mutate` |
| 실패하는 테스트를 지우거나 건너뜀 | 골든에 기록된 테스트가 비교 실행에 없으면 보고서가 ❌ | `report.py` |
| 옛 승인본이나 다른 코드의 결과를 섞음 | 승인 파일 SHA-256, 테스트·도구 소스 SHA-256을 비교 결과와 결함 주입 결과에 저장하고 보고서에서 현재 파일과 대조 | `plugin.py`, `mutation.py`, `report.py` |
| 테스트 일부만 골라 결함 탐지율을 높임 | 기록된 테스트 전부가 측정에 참여하지 않으면 ❌ | `report.py` |
| 결함이 실제로 안 들어갔는데 "생존"으로 셈 | 응답에 결함이 들어갔을 때만 표식을 남기고, 표식 없는 실행은 오류로 셈 (오류가 있으면 ❌) | `mutation.py` |
| 검토하는 동안 기준 파일이 바뀜 | 승인 화면을 만들 때의 지문과 승인 요청 때의 지문이 다르면 승인하지 않음 | `oracle.approve_from_review` |
| 숨긴 요소에만 있는 문구로 `expect_text` 통과 | 보이는 요소만 인정 (`visible=true`) | `ui.py` |
| 보고가 실제와 다름 | 보고서는 산출물(승인 상태, JUnit, 결함 주입 JSON)에서만 생성 | `eastshift report` |

순서: as-is 두 번 통과 → 기록 → **웹 검토·승인** → 결함 주입(전체·라벨 외 탐지율 80% 이상, 생존 결함 판정) → to-be 비교 → 보고서.

### 통합 화면 (`eastshift ui`)

```bash
uv run eastshift ui            # http://127.0.0.1:8790 (골든 루트 golden/, 테스트 루트 e2e/ 기준. --golden, --tests, --port)
```

로컬 서버 하나가 `golden/<app>/`과 `runs/<app>/`만 읽어서 아래 화면들을 요청 때마다 만들어 준다. 미리 파일을 만들 필요가 없다.

**첫 화면은 프로젝트 목록**이다. 프로젝트 = as-is와 to-be 한 쌍. 카드마다 소스 위치, 실행 주소, 시나리오·골든·비교 횟수, 승인 상태, 마지막 결과, 그리고 지금 할 다음 단계 명령(탐색 → 기록 → 승인 → 비교)이 보인다.
"프로젝트 추가"를 누르면 창이 뜨고 이름, as-is·to-be **소스 위치**(찾아보기: 작업 디렉터리·홈 아래 폴더만 서버가 보여준다), 실행 주소(선택), 메모를 넣는다. 저장하면 `eastshift.json`에 적히고 `e2e/<app>/` 자리가 생긴다.
등록부에 없어도 `golden/<app>`이나 `e2e/<app>`이 있으면 목록에 "미등록"으로 나오고 설정에서 채울 수 있다. 삭제는 등록만 지우고 산출물은 남긴다. 프로젝트가 하나도 없으면 첫 방문에 추가 창이 바로 열린다.

카드의 "열기"로 들어가면 **개요**(골든 관리)가 먼저 열리고, 왼쪽 탭으로 **Screen Map** · **시나리오 승인** · **검증 보고서** · **이력**을 오간다. 골든이 없는 프로젝트의 "Screen Map 만들기" 버튼은 Screen Map 탭으로 간다.

**화면 지도는 셋**이다. 지도 위의 출처 바로 오간다 (주소 `#<app>/map/<실행>/<출처>`).
- **as-is 탐색**: `eastshift crawl`이 as-is를 훑어 찾은 화면을 그대로 잇는다 (`crawl/<app>/graph.json`). 시나리오·비교와 무관하게 as-is에 무엇이 있는지. 단위는 "경로"(탐색 경로).
- **to-be 탐색**: 같은 것을 to-be에서 (`crawl/<app>-tobe/`). as-is 탐색과 나란히 놓고 화면·팝업·드로워가 빠졌는지 본다.
- **to-be 비교 (as-is 기준)**: 사람이 승인한 골든(as-is 기록)이 뼈대. as-is 탐색이 있으면 시나리오가 닿지 않은 as-is 화면도 잇는다. 화면마다 상태가 테두리 색으로:
  **빨강** = 고른 비교 실행에서 as-is와 다르게 동작, **노랑** = as-is에는 있는데 to-be 탐색에서 못 찾음, **파랑** = to-be 탐색에서만 발견, 초록 = 같음. 노랑은 권한이나 탐색 깊이 때문에 생길 수도 있으므로 미개발로 단정하지 않고 직접 확인한다. 상세 페이지에서는 as-is/to-be 캡처를 바꿔 볼 수 있다.

탐색 지도가 없으면 그 자리에 "as-is 탐색" / "to-be 탐색" 버튼(깊이 선택)이 나오고 서버가 `eastshift crawl`을 돌려 지도를 그린다. "다시 탐색"으로 갱신한다.

**완전성 잣대 (코드에만 있는 화면).** 탐색은 "누를 수 있는 것을 다 눌렀다"까지만 보장한다. 무엇을 놓쳤는지는 탐색 자체로는 알 수 없으므로 잣대를 소스에서 뽑는다:
프로젝트에 소스 위치가 있으면 탐색 뒤에 `eastshift routes <소스> --out crawl/<app>[-tobe]/routes.json`이 자동으로 돌아 라우팅 선언(Flask·FastAPI·Django, Spring·JAX-RS, Express·React/Vue 라우터·Next 파일 라우트, JSP·PHP 파일, Struts·web.xml, Rails, Go, ASP.NET, 표준 라이브러리 손 라우팅)에서 화면 주소 목록을 만든다. 실행하지 않고 정규식으로 읽는다.
지도는 이 목록과 대조해 어떤 탐색 경로·시나리오도 닿지 않은 주소를 **회색**("코드에만 있음") 카드로 넣고, 머리에 "소스 화면 N개 중 M 도달"을 적는다. 회색 카드를 누르면 코드 위치(파일:줄)와 못 간 이유 후보(픽스처 값이 없는 입력칸, 금지 목록에 걸린 버튼, 로그인·권한, 특정 데이터가 있어야 보이는 화면, 탐색 예산)가 나온다. to-be 비교 지도는 as-is 소스의 목록을 쓴다.
이 목록은 코드가 선언한 주소의 **하한**이다. 데이터 테이블에서 만드는 메뉴, 문자열을 이어 붙인 주소, 런타임 조건으로 갈리는 주소는 못 잡는다. 그러므로 회색이 있으면 확실히 놓친 것이고, 회색이 없다고 다 본 것은 아니다. "모든 경로"는 무한(되돌아가는 길)이므로 목표는 "모든 화면과 그 화면의 모든 동작"이다.

**화면 지도 만들기**: 골든이 아직 없는 프로젝트는 골든 시나리오 비교 지도 대신 초기화 화면이 나온다. 버튼 하나로 서버가 ① as-is 탐색(`eastshift crawl`, 깊이 선택) → ② 시나리오 초안을 `e2e/<app>/`로 → ③ as-is에서 골든 기록(`pytest --record`)을 순서대로 돌리고 로그를 보여준다. `e2e/<app>/`에 시나리오가 이미 있으면 ①②는 건너뛰고 기록만 하고, as-is 탐색 지도를 이미 만들었으면 ①은 건너뛰고 그 초안을 쓴다. 끝나면 지도가 바로 그려진다. **승인은 하지 않는다** — 그 뒤 사람이 시나리오 승인 탭에서. 골든이 이미 있으면 이 버튼은 없다(골든은 사람 승인물이라 화면에서 덮어쓰지 않는다). 탐색은 저장·확정 버튼도 실제로 누르므로 테스트 DB의 as-is에서만.
오른쪽 위 "실행" 선택으로 지난 실행을 고르면 지도와 보고서가 그 실행 기준으로 다시 그려진다 (원장이 실행마다 JUnit·스크린샷 사본을 `runs/<app>/<시각>/`에 남기기 때문).

이력 탭: 승인·실행 수·마지막 결과·결함 탐지율 카드, 실행 목록(대상, 어느 승인본으로, 결과, 지난 실행 대비 +통과로/−새로 실패/계속 실패, 지도·보고서 버튼),
시나리오 × 실행 격자(초록/빨강, 열을 누르면 그 실행 선택), 선택한 실행의 테스트별 상세(종류, 첫 오류 줄, 무엇이·as-is·to-be 표, 실패 순간 화면), 결함 탐지 측정 목록(현재 승인본인지).
새로 판단하는 것은 없다. 서버는 127.0.0.1에만 열리고 `runs/`·골든 루트 밖의 파일은 주지 않는다.

**웹 승인**: `eastshift ui`의 시나리오 승인 탭에서 모든 시나리오를 확인하면 이름 입력란과 승인 버튼이 나온다. 승인하면 `APPROVED.json`이 생긴다. 터미널 코드나 TTY는 필요 없다. 검토 도중 기준 파일이 바뀌면 승인 요청은 거부된다. 이름은 기록용이며 신원 인증은 아니다. 서버는 로컬(127.0.0.1)에만 연다.

### 화면 네 장 (통합 화면 안에서만)

사람이 보는 화면은 통합 화면의 탭이 전부다. 서버가 요청 때마다 산출물에서 만들고, 파일로 따로 떨구는 명령은 없다 (모두 산출물만 읽고 새로 판단하지 않는다). 각 탭의 모습은 `docs/samples/ui-*.png`.
- **시나리오 승인** (`eastshift/pwtest/review.py`): 왼쪽 시나리오 목록(확인 체크, 지난 승인 이후 바뀐 것·새 것 표시, 전체/미확인/바뀐 것 필터), 가운데 선택한 시나리오의 단계별 큰 as-is 캡처(`golden/<app>/shots/`, 승인 해시에 포함)와 동작·새로 나타난 내용·알림창·그 시점에 확인한 값(바뀐 값은 이전 값도), 오른쪽 규칙(가리는 값과 실제로 가린 값, 이름 변경, 동등 결함).
  확인 체크는 브라우저에 남고(localStorage, 골든 해시별), 전부 확인하면 이름 입력란과 승인 버튼이 나온다. 캡처 `docs/samples/ui-approve.png`.
- **검증 보고서** (`report.py` + `html.render_report`): 결론, 다른 점(무엇이 · as-is · to-be), 신뢰 확인, 결함 탐지 능력. 고른 실행의 JUnit과 현재 승인본의 결함 주입 결과로 그린다.
  같은 내용의 markdown은 `eastshift report … --out reports/verification-<app>.md`로 만든다. 이건 에이전트와 CI가 판정 줄을 읽는 용도다.
- **개요** (`catalog.py`, 골든 관리): 승인 상태, 시나리오 수, 마지막 비교 결과, 실행 횟수. 시나리오 표(제목, 단계, 확인 값, 마지막 결과, 실행 이력 점), 행을 누르면 필름스트립·단계·마지막 실행에서 다른 점·실행 이력. 캡처 `docs/samples/ui-overview.png`.
- **화면 지도** (`map.py`): 출처 셋(as-is 탐색 / to-be 탐색 / to-be 비교). 단계들을 **두 층**으로 합쳐 그린다.
  - **라우트**(주소): 지도의 노드. `/orders/17`과 `/orders/3`은 `/orders/{id}` 하나로 합친다. 홈(`/`)으로 들어가는 테스트가 있으면 홈이 시작이고 맨 왼쪽.
  - **상태**(라우트 안에서 구조가 다른 화면): 기본, 팝업(`dialog`), 드로워(`complementary`), 탭(기본과 다른 탭이 선택됨), 알림. 노드 카드에 "드로워 2 · 팝업 1"처럼 개수가 붙고,
    라우트를 누르면 넘어가는 상세 페이지에 작은 그래프(기본 —[필터]→ 드로워 · 필터, —[신규 주문]→ 팝업 · 신규 주문 …)로 나온다. 상태를 누르면 팝업으로 그 상태의 캡처, 오는 동작·가는 상태, 지나는 테스트(누르면 시나리오).
  두 페이지: **지도**(왼쪽 라우트 목록 — 검색, 상태 점, 안의 상태 수 — 와 지도)와 **라우트 상세**(화면을 누르면 넘어간다. 주소 `#<라우트>[/<상태>]`, 브라우저 뒤로가기나 "← 지도"로 복귀).
  지도는 왼쪽에서 오른쪽 한 방향, 열은 시작에서 몇 번 이동하는지, 열 안 순서는 무게중심(Sugiyama 방식)으로 선 교차를 줄인다. 화살표에는 동작 이름 알약, 되돌아가는 길은 아래 차선에 점선.
  상세 페이지: 왼쪽에 큰 캡처(누르면 원본)와 화면 안의 상태 그래프, 오른쪽에 시작에서 오는 길, 여기서 갈 수 있는 곳, 지나는 테스트(누르면 단계 필름스트립과 동작·확인 값, 다른 점 표).
  to-be 비교 지도는 고른 실행의 JUnit로 다른 라우트에 빨간 띠와 "다름" 표시, 상태 그래프에서 어느 상태가 달랐는지. 이름·주소·팝업 이름 검색, 테스트별 거르기, 확대·축소·끌기.
  같은 상태인지는 화면 구조(제목, 입력칸, 버튼)로 가리고 글자 내용(주문번호, 품목명)은 보지 않는다. 기록된 산출물만 읽는다.
  캡처: `docs/samples/ui-map-compare.png` (포털 데모: 라우트 6, 상태 16 — 드로워·팝업·탭), 상세 `ui-map-detail.png`, 상태 팝업 `ui-map-state.png`.
  화면 수백 개면 모듈 단위로 골든을 나눠 지도를 따로 그린다. 주소가 바뀌지 않는 앱(frameset, 해시 없는 SPA)은 제목이 다른 기본 화면을 다른 라우트로 가른다(레거시 데모: `/` 하나에 주문 등록·주문관리 두 라우트). `{id}`가 있는 주소는 제목에 데이터가 섞이므로 가르지 않는다.

```bash
uv run eastshift oracle-status golden/<app>                          # 승인 상태, 마스킹 감사 (누구나, 터미널)
uv run eastshift ui                                                  # 시나리오 승인 탭에서 사람이 승인. 화면 네 장도 여기
uv run eastshift mutate e2e/<app> --base-url $ASIS --compare golden/<app> --max-per-op 100
uv run pytest e2e/<app> --base-url $TOBE --compare golden/<app> --junitxml reports/junit-<app>.xml
uv run eastshift report --oracle golden/<app> --junit reports/junit-<app>.xml --mutation reports/mutation-<app>-golden.json --out reports/verification-<app>.md   # markdown
uv run eastshift status golden/<app>                                 # 남은 실패, 지난 실행 대비 변화
```

데모(2026-09-24): 처음 4개 테스트의 결함 탐지율은 expect만 56%, 골든 비교를 더하면 78%. 생존 결함에서 실제 빈틈 두 개가 나왔다
(노트북·마우스 단가를 계산하는 테스트가 없음, `expect_dialog`가 부분 일치라 문구 변경을 놓침). 테스트를 보강하고 helper를 고친 뒤 전수 주입에서 34/34 = 100%.

## 두 겹의 검사

| | 무엇을 | 언제 잡나 |
| :--- | :--- | :--- |
| `ui.expect_*` | 테스트에 명시한 값 (부가세 120원, alert 문구) | 적은 것만 |
| 골든 비교 | goto/click/fill/select/check/press 직후 화면 전체 내용, 입력값, 선택값, 그 동작에서 뜬 alert/confirm/prompt | 적지 않은 것까지 |

관찰은 화면이 멈춘 뒤에 한다 (`ui.py` `_settle`): load 뒤 남은 요청이 없고 스냅샷이 `--settle-ms`(기본 500ms) 동안 안 바뀌면 멈춘 것이다. 느린 조회는 요청이 끝날 때까지 기다리고(최대 10초), 요청 없이 계속 바뀌는 화면은 3초에서 끊는다. 프레임 하나라도 스냅샷을 못 뜨면 그 테스트는 오류다 (내용이 빠진 골든을 기록하지 않는다).

골든 비교는 레이아웃에 둔감하다 (`eastshift/observe.py` `flatten`). 비대화형 요소는 텍스트만, 대화형 요소는 역할·이름·값·상태만 남기고,
드롭다운은 위젯 종류와 무관하게 `combobox "이름": 선택값` 한 줄이 된다. frameset → 단일 페이지, table → div, 네이티브 select → 커스텀 드롭다운,
heading level, 구분자(`|`), 컨테이너의 aria-label 차이는 무시된다. URL과 제목은 기본으로 비교하지 않는다.

매번 바뀌는 값은 오라클의 `golden/<app>/oracle.json` `{"ignore": ["주문번호 \\d+"]}`로 마스킹한다. 승인 대상이라 테스트 코드나 명령행으로는 바꿀 수 없다.

### 실행 조건과 업무 범위

대상 앱에 테스트용 초기화 POST 경로가 있으면 `pytest --record`에 `--reset-path /test/reset`을 지정한다. 각 테스트 전에 호출한다. 브라우저 시각은 `--fixed-time 2026-09-27T09:00:00+09:00`처럼 시간대까지 지정한다. 두 설정은 골든 JSON에 기록되고 이후 비교·결함 주입에서 자동 재사용된다. 초기화 경로가 없는 앱에서는 준비된 고정 계정과 데이터로 실행하고, 상태를 바꾸는 테스트가 서로 영향을 주는지는 별도로 확인해야 한다.
초기화 응답에 `X-EastShift-Data-Id` 헤더가 있으면 그 데이터 버전도 기록해 비교 실행 때 일치 여부를 검사한다. 헤더가 없는 앱의 데이터 동일성은 도구가 증명할 수 없다.
문서 응답에 `X-EastShift-Build-Id` 헤더가 있으면 대상 앱 배포 ID를 실행 기록과 보고서에 표시하고, 한 실행 중 ID가 바뀌면 증거 확인에 실패한다. 헤더가 없으면 보고서에 `헤더 없음`으로 표시한다.

`oracle.json`의 `coverage`는 필수 업무 경우를 테스트와 연결한다. 선언되지 않은 업무 범위는 보고서가 완료로 판정하지 않는다. `allowed_differences`는 승인된 의도적 변경이다. 최초 비교 실패에 표시된 `diff sha256`을 정확히 지정하고 사유를 적은 뒤 웹에서 재승인한다. 같은 테스트의 같은 단계에서 정확히 같은 차이가 날 때만 승인된 차이로 분리되며, 내용이 다시 달라지면 실패한다.

```json
{
  "coverage": [{"case": "주문 저장: 수량 0", "tests": ["test_order_zero"]}],
  "api_compare": ["/api/orders(?:/[0-9]+)?"],
  "allowed_differences": [{"test": "test_order_zero", "step": 3, "sha256": "비교 실패에서 나온 64자리 SHA-256", "reason": "합의된 오류 문구 개선"}],
  "redact_fields": ["주민등록번호"],
  "redact_patterns": ["\\b[0-9]{6}-[0-9]{7}\\b"]
}
```

`redact_fields`에 지정한 입력칸, 비밀번호·토큰 이름의 입력칸, `redact_patterns`에 맞는 문자열은 저장 전에 `<redacted>`로 바뀐다. 비밀 입력이나 패턴이 쓰인 실행에서는 단계 화면과 실패 캡처를 저장하지 않는다. 이 규칙은 해당 값의 동등성 검사도 가리므로 구체적인 범위로 작성한다.

`api_compare`는 화면에 보이지 않는 응답 결과를 비교할 API 경로의 정규식이다. `fetch`/XHR 응답의 경로·메서드·상태·본문을 동작 단계에 묶어 기록하고 시나리오 승인 화면에 보여 준다. as-is와 to-be가 같은 API 경로를 쓸 때 적용하며, 매번 바뀌는 응답 값은 `redact_patterns`로 가린다. API 응답은 골든에 저장되므로 이 규칙과 민감정보 제거 규칙을 **골든 기록 전에** 설정한다. 나중에 추가했으면 as-is에서 다시 기록하고 재승인한다.

보고서는 **증거 유효성**, **실행한 시나리오의 동등성**, **등록된 업무 범위**를 별도로 보여준다. 전체 결함 탐지율은 생성한 결함에만 대한 점수이므로 라벨 변경을 제외한 탐지율도 별도 기준으로 확인한다.
골든에는 스냅샷 원문이 들어 있어서 ignore나 정규화 규칙을 바꿔도 as-is를 다시 기록할 필요가 없다.

## to-be 화면이 달라졌을 때

테스트는 as-is 이름과 "무엇을 하는지"만 쓴다. to-be 차이는 테스트 밖에서 흡수한다.

| to-be 차이 | 흡수하는 곳 |
| :--- | :--- |
| 구조 (frameset, table, div) | 없음. 요소를 역할·이름으로 모든 프레임에서 찾는다 |
| 위젯 조작 방식 (커스텀 드롭다운, datepicker) | `eastshift/pwtest/ui.py`에 한 번. `ui.select`는 네이티브 select와 커스텀 드롭다운을 모두 다룬다 |
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

`--compare` 실행마다 `runs/<app>/<시각>.json`에 대상 URL, 실행한 승인본(승인자·승인 시각), 테스트별 결과(상태, kind, 첫 오류 줄, 다른 점 표, 스크린샷)가 남고,
`runs/<app>/<시각>/`에 그 실행의 JUnit과 실패 스크린샷 사본이 남는다 (`reports/`는 다음 실행이 덮어쓰므로). `eastshift mutate --compare` 결과는 `runs/<app>/mutations/`에도 복사된다 (`eastshift/pwtest/ledger.py`).
`eastshift status golden/<app>`은 마지막 실행의 남은 실패를 kind별로 나열하고, 직전 실행과 비교해 **통과로 바뀜 / 새로 실패 / 여전히 실패 / 새 테스트**를 보여준다. 실행한 승인본이 현재 승인본과 다르면 경고한다.
개발자는 이 출력과 통합 화면 이력 탭의 이력 점만 보고 다음 수정으로 간다. 결함 주입 실행(`--jev-mutant`)은 원장에 남기지 않는다.

데모(2026-09-25): `tobe-fixed`(8803) 비교 → 6개 중 3개 `golden_diff` → 원장 기록 → `tobe`(8802)로 재실행 → `eastshift status`: "통과 6 · 남은 실패 0 · 통과로 바뀜 3".

## 데모 1: 포털 (`demo-app/portal_app.py`, `e2e/portal/`) — 메인 데모

홈에서 시작해 라우트 6개(주문 목록·상세, 고객 목록·상세, 설정)로 갈라지고 화면 안에 팝업·드로워·탭·confirm·alert가 있는 업무 포털. 테스트 20개(crawl 초안 18 + 업무 값 2).
as-is 버그 두 개: 부가세 10원 절사, 이미 취소된 주문을 다시 취소해도 막지 않음(이력에 취소가 두 번).

```bash
python demo-app/portal_app.py 8820 asis &          # as-is
python demo-app/portal_app.py 8821 tobe &          # 버그까지 그대로 옮긴 to-be
python demo-app/portal_app.py 8822 tobe-fixed &    # 버그 2개를 "고쳐버림"
python demo-app/portal_app.py 8823 tobe-renamed &  # "신규 주문"→"주문 등록", "수량"→"주문 수량" + 부가세 반올림
python demo-app/portal_app.py 8824 tobe-custom &   # 신규 주문 팝업의 품목이 커스텀 드롭다운 (React/MUI 방식)
python demo-app/portal_app.py 8825 tobe-modern &   # 새 룩: 왼쪽 사이드바, 다른 색·글꼴, 카드·알약 버튼, 밑줄 탭. 글자·역할·동작은 같음 (프로젝트의 to-be 주소)
python demo-app/portal_app.py 8826 tobe-wip &      # 개발 중: 새 룩 + 설정 화면 없음(미개발) + 보고서 화면 새로(새 화면) + 부가세 반올림(다름). 비교 지도 색 데모
uv run pytest e2e/portal --base-url http://127.0.0.1:8820 --record golden/portal
uv run eastshift ui                                                                 # 사람: 시나리오 승인 탭에서 승인
uv run eastshift mutate e2e/portal --base-url http://127.0.0.1:8820 --compare golden/portal --max-per-op 100
uv run pytest e2e/portal --base-url http://127.0.0.1:8822 --compare golden/portal --junitxml reports/junit-portal.xml
uv run eastshift ui
```

2026-09-25 결과 (테스트 25개, 승인 east 22:43). 비교 4회를 순서대로 돌려 `runs/portal/`에 이력으로 남아 있다:

| to-be | 결과 | 원장 kind |
| :--- | :--- | :--- |
| `tobe-fixed` | 25개 중 2개 다름 — `test_portal_vat_truncation`: 부가세 360원 → 368원, 합계 4,035원 → 4,043원. `test_portal_cancel_twice`: confirm 뒤 alert "이미 취소된 주문입니다." 가 새로 뜸 | golden_diff 2 |
| `tobe-renamed` (이름 매핑 없이) | 25개 중 19개 다름 — 라벨 "신규 주문"·"수량"이 바뀐 화면(`/orders` 목록·팝업)을 지나는 테스트 전부. `golden/portal/name_map.tobe-renamed.json` (`{"신규 주문": "주문 등록", "수량": "주문 수량"}`)을 두고 재승인하면 라벨 차이는 흡수되고 부가세 반올림 1건만 남는다 | golden_diff 19 |
| `tobe-custom` | 25개 모두 같음. 테스트 수정 없음 (`ui.select` 어댑터가 커스텀 드롭다운을 연다) | same 25 |
| `tobe` | 25개 모두 같음 (겉모습까지 as-is와 같은 변형) | same 25 |
| `tobe-modern` (프로젝트 설정의 to-be) | 25개 모두 같음. 화면은 전혀 다르게 생겼지만(사이드바·보라색·카드) 글자·요소 이름·순서·주소·대화상자가 같아서 비교는 겉모습을 보지 않는다 — 실제 전환에 가장 가까운 경우. 캡처 `docs/samples/portal-tobe-modern.png` | same 25 |

결함 주입(승인본 기준, 25분 소요): 148개 중 144개 탐지 = **97%** (`runs/portal/mutations/20260925-230916.json`). 생존 4개는 전부 탭이 없는 화면(`/`, `/customers`, `/customers/{id}`, `/orders`)의 탭 선택 코드 `===→!==` — 그 화면에서는 실행되지 않는 코드라 화면이 달라질 수 없는 **동등 결함**이다. `golden/portal/oracle.json`의 `equivalent_mutants`에 네 개를 적고 재승인하면 100%로 집계된다 (골든이라 사람이 적는다).

검증 보고서(`tobe-fixed` 실행 + 이 측정): 판정 **신뢰 가능**, 다른 점 2건 — `docs/samples/verification-sample.md`.

## 데모 2: 레거시 주문 (`demo-app/legacy_app.py`, `e2e/legacy/`) — frameset 특수 케이스

주소가 항상 `/`인 frameset, table 레이아웃, title 속성 라벨, alert/confirm. 라우트 지도는 제목으로 가른 화면 2개뿐이지만, 옛날 방식의 as-is를 단일 페이지 to-be와 비교하는 본보기.

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

자연어 YAML로 같은 일을 하는 Jev 러너 버전(`eastshift run --record`/`--compare`)은 `examples/yaml-migration/`에 있다.

## 아직 안 되는 것

- 이름 없는 요소: alt 없는 이미지 버튼, label도 title도 없는 input. 글자가 있는 `div onclick`은 `ui.act('button', '조회')`가 역할로 못 찾으면 보이는 글자가 정확히 같은 요소가 하나뿐일 때 그것을 누른다 (제목·문단은 제외). 그 밖은 `tools/explore.py`로 대표 화면을 먼저 확인할 것.
- IE 전용 기능: ActiveX, `showModalDialog`. Chromium에서 as-is가 안 돌면 기록 자체가 불가능하다.
- 화면에 안 나오는 차이: DB에 저장된 값, 배치, 외부 연동. 별도의 DB 결과 비교가 필요하다.
- `ui.py`에 아직 없는 위젯: datepicker, 가상 스크롤 그리드, 파일 업로드. 처음 만날 때 한 번 추가한다.
