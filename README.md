# jev-e2e (프로토타입)

두 가지 E2E 도구와 as-is/to-be 비교 로직(`jev_e2e/observe.py`)을 함께 둔다.

- **Playwright `ui` fixture** (`jev_e2e/pwtest`, pytest 플러그인): 셀렉터 기반 결정론적 테스트. 프레임 무관 요소 찾기, 대화상자, 위젯 어댑터, as-is 골든 기록/비교.
- **jev-e2e 러너**: 자연어로 쓴 스텝을 **TypeSafe Jev**가 "어느 요소를 조작할지" 결정하고 Playwright가 실행한다. 한 번 고른 요소는 캐시로 재생.

텍스트 생성 LLM은 쓰지 않는다. 입력값은 테스트·시나리오에 고정하고, 검증은 결정론적 assert로.

## 실행

```bash
uv sync
uv run playwright install chromium
cp .env.example .env   # TYPESAFE_API_KEY 채우기 (jev-e2e 러너가 새로 요소를 고를 때만 필요)

uv run jev-e2e run scenarios/naver_search.yaml scenarios/naver_hotel.yaml            # 캐시 있으면 재생, 없으면 Jev
uv run jev-e2e run scenarios/naver_flight.yaml scenarios/coupang_home.yaml --headed   # 이 둘은 headed 필요 (아래 참고)
uv run jev-e2e run scenarios/demoqa_form.yaml --no-cache                              # 매 스텝 Jev
```

옵션: `--margin 0.1` (1위-2위 확률 차 최소값), `--max-candidates 60`, `--settle-ms 1500`, `--no-cache`, `--headed`,
`--storage-state .auth/x.json` (로그인 상태 재사용), `--replay-only` (CI 모드: Jev를 부르지 않고 캐시만 재생, API 키 불필요),
`--base-url http://host:port` (goto 상대 경로 기준, 기본 `JEV_BASE_URL`), `--cache-dir DIR` (기본 `.jev-cache`), `--junit reports/junit.xml` (CI 리포트).

차세대 전환 검증 (as-is 동작을 버그까지 골든으로 기록하고 to-be와 비교): `--record DIR` / `--compare DIR`, `--compare-ignore REGEX`, `--compare-url`, `--compare-unordered`
(jev-e2e 러너 옵션. Playwright 쪽은 오라클 승인, 결함 주입, 검증 보고서까지 갖춘 `jev-e2e approve / mutate / report`를 쓴다).
절차와 데모는 [docs/MIGRATION.md](docs/MIGRATION.md). Playwright 스크립트와의 비교는 [docs/JEV_VS_PLAYWRIGHT.md](docs/JEV_VS_PLAYWRIGHT.md).

화면 여러 개에 같은 시나리오 돌리기 (스모크): 시나리오에 `matrix:` 또는 `matrix_file:`, expect 키 `http_ok`, `no_js_error`, `no_dialog`, `rows_at_least`.
[docs/SMOKE.md](docs/SMOKE.md).

화면 탐색으로 시나리오 만들기: `jev-e2e crawl <시작 URL> --fixtures f.yaml --out DIR`는 누를 수 있는 동작을 모두 눌러 흐름 그래프(mermaid)와
전이를 모두 지나는 시나리오, 재생 캐시, 같은 경로의 Playwright 테스트(`test_crawl.py`)를 만든다. 저장·확정도 실제로 누르므로 `--dry-run`으로 먼저 확인한다.
흐름 지도와 초안용이다: 자동 검증은 화면 이동만 보므로 전환 검증에는 골든 비교와 함께 쓴다. [docs/CRAWL.md](docs/CRAWL.md).

개발 루프(개발 → 테스트 → 실패 유형별 수정)를 로컬 앱으로 세 바퀴 돌린 기록: [docs/DEV_LOOP.md](docs/DEV_LOOP.md).

## 역할 분담과 Claude Code 스킬

| 용도 | 도구 | 스킬 |
| :--- | :--- | :--- |
| 페이지·흐름 E2E, as-is/to-be 동등성 (값, 메시지, 버그까지) | Playwright + `ui` fixture (`e2e/<app>/`, `uv run pytest`) | `e2e-tests` |
| 비슷한 화면 여러 개를 넓게 훑는 스모크 | jev-e2e matrix (`scenarios/<app>/`, `uv run jev-e2e run`) | `jev-smoke` |

근거는 [docs/JEV_VS_PLAYWRIGHT.md](docs/JEV_VS_PLAYWRIGHT.md)(요소 찾기 방식과 무관하게 탐지력은 같고, 동등성 검증에는 결정론적 스크립트가 낫다)와
[docs/SMOKE.md](docs/SMOKE.md)(화면별 정보 없이 12개 화면을 한 시나리오로, Playwright 일반 규칙은 유사 버튼을 잘못 누름).
Claude Code에서 "이 화면 E2E 테스트 만들어줘", "to-be가 as-is와 같은지 검증해줘"는 `e2e-tests`, "전체 메뉴 스모크 돌려줘"는 `jev-smoke`가 받는다.

```bash
uv run pytest e2e/<app> --base-url <as-is> --record golden/<app>     # Playwright: as-is 골든
uv run pytest e2e/<app> --base-url <to-be> --compare golden/<app>    # Playwright: to-be 비교
uv run jev-e2e run scenarios/<app>/screen_smoke.yaml --base-url <url> --cache-dir .jev-cache/<app> [--replay-only]   # 스모크
```

아래는 jev-e2e 러너(자연어 YAML) 자체의 설명이다.

## 새 시나리오 만드는 순서 (직접 쓸 때)

1. **요소 이름 탐색**: `uv run python tools/explore.py <url> "button:팝업 닫기;tab:어디로 여행 가시나요?" 80` 로 페이지의 role/name과 앞선 라벨을 본다 (`;`로 클릭을 이어서 패널이 열린 상태를 볼 수 있음, `HEADED=1` 로 headed).
2. **스텝을 쓴다**: 사람이 하는 동작을 한 스텝에 하나씩. 값이 필요한 스텝은 `fill:`로 값을 준다. 사이트가 요구하는 "적용", "선택완료" 같은 확정 단계를 빠뜨리지 않는다.
3. **assert를 강하게 쓴다**: 페이지 어딘가에 있는 단어(`text: 부산`) 대신 URL 파라미터, 입력값(`field`), 스냅샷의 `[checked]`/`[selected]`처럼 우회 충족이 안 되는 것으로.
4. **`--no-cache --headed`로 첫 실행**: 스텝별로 Jev가 고른 요소, confidence, margin을 보고 문장이 모호하면(margin < 0.1) 문장을 구체화한다. 실패하면 `reports/<시나리오>-step<N>-fail.png`와 리포트 JSON의 `pool`(Jev가 본 후보 표)을 본다.
5. **두 번째 실행부터는 캐시 재생** (Jev 호출 0). UI가 바뀌어 요소를 못 찾으면 그 스텝만 Jev가 다시 골라 캐시를 고친다. 캐시를 초기화하려면 `.jev-cache/<시나리오>.json` 삭제.
6. CI에서는 캐시 디렉터리를 저장소에 커밋하거나 아티팩트로 유지하면 평상시 LLM 비용이 0이 된다.

## 시나리오 형식

```yaml
name: 네이버 호텔 - 목적지, 캘린더, 인원 선택 후 검색
steps:
  - goto: https://hotels.naver.com/
  - do: 광고 팝업을 7일간 보지 않기로 닫기          # Jev가 대상 선택 (click)
  - do: 여행지 검색 패널의 지역 버튼 목록에서 부산 선택
  - do: 캘린더에서 2026년 10월 15일 선택             # 중복 이름 "15"는 앞선 라벨 "2026.10."로 구분
  - do: 성인 인원 1명 늘리기                         # "+" 버튼은 라벨 "성인 만 18세 이상 2"로 구분
  - do: 검색창에 검색어 입력                          # fill이 있으면 입력 요소만 후보, 값은 픽스처
    fill: 아이폰 17
  - press: Enter                                       # 결정론
  - do: 옛날 스타일 색상 셀렉트에서 Green 선택         # 네이티브 select의 option → select_option
  - do: 성별 남성 선택                                 # radio/checkbox → check
  - expect: { url_contains: "checkIn=2026-10-15" }    # 결정론 assert (10초 폴링)
  - expect: { field: "쿠팡 상품 검색", value: "무선 키보드" }
  - expect: { snapshot_contains: 'option "Green" [selected]' }
```

`expect` 키: `url_contains`, `url_path` (경로 정확히 일치), `text`, `text_matches` (정규식, 매번 바뀌는 숫자는 `\d+`), `no_text` (문구가 사라짐),
`title_contains`, `field`+`value` (입력값), `snapshot_contains` (aria 스냅샷 부분 문자열),
`dialog` (가장 최근 alert/confirm/prompt 메시지). `text`, `field`, `snapshot_contains`는 모든 프레임을 본다.
그 밖의 스텝:
- `save_storage_state: .auth/demo.json` (로그인 후 쿠키·스토리지 저장 → 다음 시나리오에서 `--storage-state`로 재사용).
- `dialog: accept | dismiss | {accept: "prompt 입력값"}`: 다음에 뜨는 alert/confirm/prompt 하나의 처리. 지정이 없으면 accept
  (Playwright 기본값은 dismiss라 confirm이 "취소"로 처리되던 것을 바꿨다). 뜬 대화상자는 스텝 줄과 리포트에 남는다.

시나리오 머리 키:
- `storage_state: none | <path>`: 이 시나리오의 세션. `none`이면 `--storage-state`를 줘도 로그인 안 된 상태로 시작 (인증 리다이렉트 시나리오).
- `compare: {ignore: [정규식…], url: bool, unordered: bool}`: 골든 비교 설정 ([docs/MIGRATION.md](docs/MIGRATION.md)).

값 치환: 문자열 안의 `${VAR}`는 환경변수로 바꾼다 (`fill: ${APP_PASSWORD}`). 설정 안 된 변수는 그 스텝의 실패다.
상대 경로 `goto: /orders`는 `--base-url`에 붙는다. 같은 시나리오를 dev/stage, as-is/to-be에 그대로 돌리려면 상대 경로로 쓴다.

## 동작

1. `page.locator("body").aria_snapshot()`에서 link/button/textbox/combobox/tab/checkbox/radio/option 등 이름 있는 활성 요소를 뽑는다.
2. **범위 라벨**: 같은 이름이 여러 개면(캘린더 날짜, 인원 +/-) 바로 앞의 heading/짧은 text/이름 있는 컨테이너를 라벨로 붙인다.
   text 라벨은 "바로 다음 형제 컨테이너의 자손 + 6줄 이내 형제"에만, 컨테이너 라벨은 자손에만 적용한다. 멀리 떨어진 텍스트(호텔 가격 등)가 푸터·모달에 붙는 것을 막는다.
3. 스텝 문장과 문자 겹침(이름 + 라벨)이 큰 순으로 **화면에 보이는** 요소만 최대 60개 후보로 만들고 `abstain`을 더한다.
4. Jev Choice에 후보 설명(`click button "15" in "2026.10."`)만 보낸다. 1위와 2위 확률 차이가 `--margin` 미만이면 "모호함"으로 실패.
5. `get_by_role(role, name=, exact=True, disabled=False)`로 실행. 중복 이름은 스냅샷 순번 `nth`로 찾는다. option은 부모 select로, checkbox/radio는 check로. 클릭이 실패하면 그 요소를 빼고 한 번 더 묻는다. 새 탭이 열리면 따라간다.
5-1. **프레임**: 최상위 문서와 보이는 frame/iframe을 모두 후보로 본다 (`button "저장" @frame "main"`). frameset 문서처럼 body가 없어도 동작한다.
   캐시된 프레임에 요소가 없으면 다른 프레임에서 같은 (role, name)을 찾는다. as-is(frameset)에서 만든 캐시가 to-be(단일 페이지)에서 그대로 재생되는 이유.
   스스로 닫히는 팝업(우편번호 찾기 등)이 닫히면 남은 창으로 돌아온다.
6. 성공한 선택은 `.jev-cache/<시나리오>.json`에 **스텝 문장을 키로** 저장. 다음 실행은 캐시된 요소가 있으면 **Jev 없이 재생**, 없으면 Jev로 다시 골라 캐시를 고치고 `⚠ HEALED`로 표시한다. `--replay-only`에서는 캐시 미스가 곧 실패다.
7. `reports/<시나리오>-<시각>.json`에 스텝별 결과, Jev 확률 top3, 보낸 후보 표를 기록. 실패 스크린샷과 최종 스크린샷을 남긴다.

## 2026-09-22 실행 기록 (7개 시나리오, 총 37 Jev 스텝)

| 시나리오 | 입력 종류 | Jev 매 스텝 | 캐시 재생 |
| :--- | :--- | :--- | :--- |
| 네이버 검색 → Enter → 쇼핑 탭(새 창) | 텍스트 | PASS, Jev 2회, 8.7s | PASS, Jev 0회, 7.0s |
| 네이버 메인 → 뉴스 → 스포츠 | 링크 | PASS, Jev 2회, 7.2s | PASS, Jev 0회, 5.8s |
| 네이버 로그인 → 비밀번호 찾기 | 링크 | PASS, Jev 2회, 6.6s | PASS, Jev 0회, 5.9s |
| 네이버 항공권: 도착지 자동완성, 캘린더 2일, 검색 (headed) | 텍스트, 자동완성, **캘린더** | PASS, Jev 8회, 18.4s | PASS, Jev 0회, 15.0s |
| 네이버 호텔: 지역→구→선택완료, 캘린더+적용, 인원 +, 검색 | 2단계 지역, **캘린더**, **스피너** | PASS, Jev 13회, 27.4s | PASS, Jev 0회, 22.7s |
| demoqa 폼: 텍스트 4개, 라디오, 체크박스 2개, 셀렉트 2개 | **라디오, 체크박스, select** | PASS, Jev 9회, 23.6s | PASS, Jev 0회, 20.8s |
| 쿠팡 홈 검색어 입력 (headed) | 텍스트 | PASS, Jev 1회, 5.9s | PASS, Jev 0회, 5.3s |

- Jev 스텝 37개 기준: 지연 p50 246ms, p95 540ms, 최대 581ms. confidence 중앙값 0.92 (최소 0.50). margin 중앙값 0.89 (최소 0.28).
- 캘린더 429개 요소 중 "15" in "2026.10." 선택, 가격이 붙은 날("20 11.2만")도 라벨 덕분에 선택. 인원은 "+" in "성인 만 18세 이상 2" 선택.
- 호텔 최종 URL: `/accommodation/search/부산광역시 해운대구/domestic?checkIn=2026-10-15&checkOut=2026-10-17&adultCnt=3`.

## 만들면서 배운 것

- **약한 assert는 깨진 흐름을 통과시킨다.** 호텔 시나리오는 처음에 `text: "부산"`으로 검증했는데 인기호텔 차트의 "부산" 탭 때문에 목적지가 비어 있어도 PASS였다. 최종 스크린샷에는 "검색어를 입력해주세요" 경고가 떠 있었다. URL 파라미터(checkIn, adultCnt)처럼 우회 충족이 불가능한 값으로 검증해야 한다. 러너가 최종 스크린샷을 항상 남기게 한 이유.
- **후보 표 품질이 Jev 정확도만큼 중요하다.** 보이지 않는 요소(숨김 위젯의 "로그인하기"), 라벨 없는 중복 이름(날짜 "15"), 엉뚱한 라벨(가격 텍스트가 "적용" 버튼에 붙음)이 각각 한 번씩 실패를 만들었고, 모두 후보 생성 쪽 수정으로 해결됐다.
- **문장이 두 요소에 다 맞으면 margin 게이트가 잡는다.** "뉴스 서비스 홈으로 이동"은 "뉴스"와 "뉴스홈" 사이에서 margin 0.05로 실패했다. "상단 주요 서비스 메뉴에서 뉴스로 이동"으로 바꾸자 0.98. 테스트 작성자가 문장을 구체화하는 것이 정답이고, 러너가 추측으로 통과시키면 안 된다.
- **사이트 UI 흐름은 사람이 알아야 한다.** 호텔 지역 선택은 시/도 → 구 → "선택완료" 3단계이고 캘린더는 "적용"이 필요했다. Jev는 각 단계의 대상을 고를 뿐, 단계가 몇 개인지는 시나리오가 말해줘야 한다.
- **봇 차단**: 쿠팡은 headless에서 Access Denied, headed에서도 홈만 열리고 검색·카테고리는 차단. 야놀자는 headless 차단. 네이버 항공권 자동완성 API는 headless에서 빈 결과를 돌려준다(headed 필요).

## 비용

같은 37 스텝을 일반 LLM으로 돌렸을 때와의 토큰·비용 비교는 [docs/COST_COMPARISON.md](docs/COST_COMPARISON.md). 실측: Jev 입력 57,171 / 출력 13,624 토큰, 1회 $0.0024. 캐시 재생 시 $0.

## 한계 (프로토타입)

- 이름 없는 아이콘 버튼, canvas, shadow DOM은 후보가 되지 못한다. 레거시 화면의 `div onclick`, label 없는 input도 마찬가지다 (`title` 속성은 이름으로 잡힌다).
- 스크롤, 호버, 드래그, 키보드 내비게이션, 파일 업로드는 없다.
- 라벨 규칙은 휴리스틱이다. 평면적인 접근성 트리(네이버 호텔처럼 루트에 200개 줄)에서는 여전히 무의미한 라벨이 붙을 수 있다.
- 후보 축소가 문자 겹침 기반이라 스텝 문장이 요소 이름과 전혀 다른 표현이면 상위 60개에서 밀려날 수 있다.
- 시나리오 7개, 사이트 4개에서 확인한 결과다. 통계적 근거로 보기엔 적다.
