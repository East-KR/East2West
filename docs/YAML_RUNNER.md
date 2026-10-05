# Jev 러너 (`east2west run`): 자연어 YAML 시나리오

자연어로 쓴 스텝을 **TypeSafe Jev**가 "어느 요소를 조작할지" 결정하고 Playwright가 실행한다. 한 번 고른 요소는 캐시로 재생하므로
두 번째 실행부터는 Jev 호출이 0이다. 텍스트 생성 LLM은 쓰지 않는다. 입력값은 시나리오에 고정하고, 검증은 결정론적 assert로.

East2West 안에서 이 러너의 자리: 전체 화면 스모크([SMOKE.md](SMOKE.md))와 crawl이 만든 시나리오 재생. 핵심 업무 흐름의 as-is/to-be 동등성 검증은
Playwright 테스트([MIGRATION.md](MIGRATION.md))가 맡는다. 근거는 [JEV_VS_PLAYWRIGHT.md](JEV_VS_PLAYWRIGHT.md).

## 실행

```bash
cp .env.example .env   # TYPESAFE_API_KEY 채우기 (캐시가 없는 스텝에서 요소를 고를 때만 필요)

uv run east2west run scenarios/naver_search.yaml scenarios/naver_hotel.yaml            # 캐시 있으면 재생, 없으면 Jev
uv run east2west run scenarios/naver_flight.yaml scenarios/coupang_home.yaml --headed   # 이 둘은 headed 필요 (아래 참고)
uv run east2west run scenarios/demoqa_form.yaml --no-cache                              # 매 스텝 Jev
```

옵션: `--margin 0.1` (1위-2위 확률 차 최소값), `--max-candidates 60`, `--settle-ms 1500`, `--no-cache`, `--headed`,
`--storage-state .auth/x.json` (로그인 상태 재사용), `--replay-only` (CI 모드: Jev를 부르지 않고 캐시만 재생, API 키 불필요),
`--base-url http://host:port` (goto 상대 경로 기준, 기본 `EAST2WEST_BASE_URL`), `--cache-dir DIR` (기본 `.east2west-cache`), `--junit reports/junit.xml` (CI 리포트),
`--workers N` (시나리오·matrix 행을 브라우저 N개로 나눠 돌린다. 행마다 캐시·골든·리포트가 따로라 결과는 같다. 서버 상태를 바꾸는 시나리오나 로그인 세션 하나를 나눠 쓰는 경우는 1).

골든 기록·비교 (`--record DIR` / `--compare DIR`, `--compare-ignore REGEX`, `--compare-url`, `--compare-unordered`)도 있다.
골든 승인, 결함 주입, 검증 보고서가 붙은 것은 Playwright 쪽이므로 전환 검증에는 그쪽을 쓴다. YAML 버전의 예제는 `examples/yaml-migration/`.

## 새 시나리오 만드는 순서 (직접 쓸 때)

1. **요소 이름 탐색**: `uv run python tools/explore.py <url> "button:팝업 닫기;tab:어디로 여행 가시나요?" 80` 로 페이지의 role/name과 앞선 라벨을 본다 (`;`로 클릭을 이어서 패널이 열린 상태를 볼 수 있음, `HEADED=1` 로 headed). 흐름이 많은 화면은 `east2west crawl`이 초안을 만든다 ([CRAWL.md](CRAWL.md)).
2. **스텝을 쓴다**: 사람이 하는 동작을 한 스텝에 하나씩. 값이 필요한 스텝은 `fill:`로 값을 준다. 사이트가 요구하는 "적용", "선택완료" 같은 확정 단계를 빠뜨리지 않는다.
3. **assert를 강하게 쓴다**: 페이지 어딘가에 있는 단어(`text: 부산`) 대신 URL 파라미터, 입력값(`field`), 스냅샷의 `[checked]`/`[selected]`처럼 우회 충족이 안 되는 것으로.
4. **`--no-cache --headed`로 첫 실행**: 스텝별로 Jev가 고른 요소, confidence, margin을 보고 문장이 모호하면(margin < 0.1) 문장을 구체화한다. 실패하면 `reports/<시나리오>-step<N>-fail.png`와 리포트 JSON의 `pool`(Jev가 본 후보 표)을 본다.
5. **두 번째 실행부터는 캐시 재생** (Jev 호출 0). UI가 바뀌어 요소를 못 찾으면 그 스텝만 Jev가 다시 골라 캐시를 고친다. 캐시를 초기화하려면 `.east2west-cache/<시나리오>.json` 삭제.
6. CI에서는 캐시 디렉터리를 저장소에 커밋하거나 아티팩트로 유지하면 평상시 LLM 비용이 0이 된다. (이 저장소는 캐시를 커밋하지 않는다.)

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
스모크용 키 `http_ok`, `no_js_error`, `no_dialog`, `rows_at_least`는 [SMOKE.md](SMOKE.md).

그 밖의 스텝:
- `save_storage_state: .auth/demo.json` (로그인 후 쿠키·스토리지 저장 → 다음 시나리오에서 `--storage-state`로 재사용).
- `dialog: accept | dismiss | {accept: "prompt 입력값"}`: 다음에 뜨는 alert/confirm/prompt 하나의 처리. 지정이 없으면 accept
  (Playwright 기본값은 dismiss라 confirm이 "취소"로 처리되던 것을 바꿨다). 뜬 대화상자는 스텝 줄과 리포트에 남는다.

시나리오 머리 키:
- `storage_state: none | <path>`: 이 시나리오의 세션. `none`이면 `--storage-state`를 줘도 로그인 안 된 상태로 시작 (인증 리다이렉트 시나리오).
- `matrix:` / `matrix_file:`: 같은 시나리오를 화면 목록에 돌린다 ([SMOKE.md](SMOKE.md)).
- `compare: {ignore: [정규식…], url: bool, unordered: bool}`: 골든 비교 설정.

값 치환: 문자열 안의 `${VAR}`는 matrix 변수, 없으면 환경변수로 바꾼다 (`fill: ${APP_PASSWORD}`). 설정 안 된 변수는 그 스텝의 실패다.
상대 경로 `goto: /orders`는 `--base-url`에 붙는다. 같은 시나리오를 dev/stage, as-is/to-be에 그대로 돌리려면 상대 경로로 쓴다.

## 동작

1. `page.locator("body").aria_snapshot()`에서 link/button/textbox/combobox/tab/checkbox/radio/option 등 이름 있는 활성 요소를 뽑는다.
2. **범위 라벨**: 같은 이름이 여러 개면(캘린더 날짜, 인원 +/-) 바로 앞의 heading/짧은 text/이름 있는 컨테이너를 라벨로 붙인다.
   text 라벨은 "바로 다음 형제 컨테이너의 자손 + 6줄 이내 형제"에만, 컨테이너 라벨은 자손에만 적용한다. 멀리 떨어진 텍스트(호텔 가격 등)가 푸터·모달에 붙는 것을 막는다.
3. 스텝 문장과 문자 겹침(이름 + 라벨)이 큰 순으로 **화면에 보이는** 요소만 최대 60개 후보로 만들고 `abstain`을 더한다.
4. Jev Choice에 후보 설명(`click button "15" in "2026.10."`)만 보낸다. 1위와 2위 확률 차이가 `--margin` 미만이면 "모호함"으로 실패.
5. `get_by_role(role, name=, exact=True, disabled=False)`로 실행. 중복 이름은 스냅샷 순번 `nth`로 찾는다. option은 부모 select로, checkbox/radio는 check로. 클릭이 실패하면 그 요소를 빼고 한 번 더 묻는다. 새 탭이 열리면 따라간다.
6. **프레임**: 최상위 문서와 보이는 frame/iframe을 모두 후보로 본다 (`button "저장" @frame "main"`). frameset 문서처럼 body가 없어도 동작한다.
   캐시된 프레임에 요소가 없으면 다른 프레임에서 같은 (role, name)을 찾는다. as-is(frameset)에서 만든 캐시가 to-be(단일 페이지)에서 그대로 재생되는 이유.
   스스로 닫히는 팝업(우편번호 찾기 등)이 닫히면 남은 창으로 돌아온다.
7. 성공한 선택은 `.east2west-cache/<시나리오>.json`에 **스텝 문장을 키로** 저장. 다음 실행은 캐시된 요소가 있으면 **Jev 없이 재생**, 없으면 Jev로 다시 골라 캐시를 고치고 `⚠ HEALED`로 표시한다. `--replay-only`에서는 캐시 미스가 곧 실패다.
8. `reports/<시나리오>-<시각>.json`에 스텝별 결과, Jev 확률 top3, 보낸 후보 표를 기록. 실패 스크린샷과 최종 스크린샷을 남긴다.

## 실패 원인 분류 (`--triage`, `east2west triage`)

실패하거나 골든과 다른 스텝의 근거를 Jev Choice에 보내 **정해진 분류표 안에서** 원인을 고른다 (`east2west/triage.py`). 근거는 러너가 이미 남기는 것뿐이다:
실패 사유 문구, 직전 스텝 이력(6개), 골든 diff(40줄), 마지막 동작 이후 HTTP/JS 오류, 대화상자, Jev 확률 top3, 화면 스냅샷 앞 1,500자.
텍스트 생성은 없고, 확률 분포가 나오므로 margin이 낮은 것만 사람이 본다. 스텝당 200~300ms, 입력 1천 토큰 안팎.

| 분류 | 뜻 | 후속 조치 |
| :--- | :--- | :--- |
| `ui_changed` | 라벨·구조가 바뀜, 기능은 정상 | 이름 매핑을 제안하고 의도된 변경인지 사람이 확인 (자동 복구는 권하지 않는다) |
| `real_defect` | to-be가 as-is와 다르게 동작 (값·문구·행 수·오류) | diff·실패 스크린샷과 함께 개발자 확인 |
| `environment` | 서버 다운, 세션 만료, 봇 차단, 5xx | 서버·세션·네트워크 확인 후 재실행 |
| `timing` | 로딩 중 조작, settle 부족 | `--settle-ms` 늘려 재실행 |
| `test_bug` | 문장 모호, 단계 누락, 약한 expect, 변수 미설정, 캐시 미준비 | 시나리오 문장·단계·expect 검토 |
| `abstain` | 근거 부족 (Jev 호출 실패 포함) | 사람 검토 |

분류는 제안이다. 조치 문구도 "…으로 보임"으로 쓰고, 최종 판정은 사람이 한다.

같은 요청에 Noul 두 개가 붙는다: `retry_may_pass` (그대로 재실행하면 통과할 가능성), `likely_widespread` (같은 원인이 다른 화면에도 퍼져 있을 가능성).

```bash
uv run east2west run scenarios/<app>/*.yaml --base-url $TOBE --compare golden/<app> --triage --junit reports/junit.xml   # 실행 중 분류
uv run east2west triage reports/<stem>-<시각>.json                                                                       # 리포트 JSON만으로 사후 분류
```

- 실행 중 분류는 스텝 줄 아래 `⚑ triage ui_changed p=0.77 margin=0.65 | retry 0.12 | widespread 0.70 | <조치>`로 찍히고, 리포트 JSON의 스텝에 `triage`, 시나리오에 분류별 건수가 들어간다. JUnit 실패 메시지 앞에도 `[triage <분류> p=…]`가 붙는다.
- `east2west triage`는 브라우저 없이 리포트 JSON만 쓴다. 스냅샷·오류 이벤트가 없어 근거가 적으므로, 실행 중 `--triage`가 더 정확하다. 결과는 `<리포트>-triage.json`.
- Jev 호출이 실패해도 테스트 실행은 깨지지 않는다 (`abstain` + `error`로 기록).
- 같은 원인이 퍼진 경우: 요약에서 한 분류가 4건 이상이면 앞 3줄만 보이고 나머지는 "외 N건"으로 묶는다. 서버가 죽어 같은 사유로 화면마다
  실패하면 처음 한 번만 Jev에 묻고(`environment`, widespread ≥ 0.7) 나머지는 그 결과를 재사용한다 (`reused: true`).
- `--replay-only`(API 키 없는 CI)와는 같이 못 쓴다.

**한계**: Jev는 화면을 다시 열어 보지 않고 diff와 문구로 고른다. `real_defect`와 `ui_changed`의 구분은 diff에 값 변화가 보일 때만 정확하다.
첫 스텝이 실패하면 뒤 스텝은 skip이라 분류 대상이 아니다. 최종 판정은 사람이 한다. 데모 앱 3종(결함 주입, 라벨 변경, 죽은 포트)에서 확인한 결과이며 통계적 근거는 아니다.

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
- 같은 37 스텝을 일반 LLM으로 돌렸을 때와의 토큰·비용 비교는 [COST_COMPARISON.md](COST_COMPARISON.md). 실측: Jev 입력 57,171 / 출력 13,624 토큰, 1회 $0.0024. 캐시 재생 시 $0.

## 만들면서 배운 것

- **약한 assert는 깨진 흐름을 통과시킨다.** 호텔 시나리오는 처음에 `text: "부산"`으로 검증했는데 인기호텔 차트의 "부산" 탭 때문에 목적지가 비어 있어도 PASS였다. 최종 스크린샷에는 "검색어를 입력해주세요" 경고가 떠 있었다. URL 파라미터(checkIn, adultCnt)처럼 우회 충족이 불가능한 값으로 검증해야 한다. 러너가 최종 스크린샷을 항상 남기게 한 이유.
- **후보 표 품질이 Jev 정확도만큼 중요하다.** 보이지 않는 요소(숨김 위젯의 "로그인하기"), 라벨 없는 중복 이름(날짜 "15"), 엉뚱한 라벨(가격 텍스트가 "적용" 버튼에 붙음)이 각각 한 번씩 실패를 만들었고, 모두 후보 생성 쪽 수정으로 해결됐다.
- **문장이 두 요소에 다 맞으면 margin 게이트가 잡는다.** "뉴스 서비스 홈으로 이동"은 "뉴스"와 "뉴스홈" 사이에서 margin 0.05로 실패했다. "상단 주요 서비스 메뉴에서 뉴스로 이동"으로 바꾸자 0.98. 테스트 작성자가 문장을 구체화하는 것이 정답이고, 러너가 추측으로 통과시키면 안 된다.
- **사이트 UI 흐름은 사람이 알아야 한다.** 호텔 지역 선택은 시/도 → 구 → "선택완료" 3단계이고 캘린더는 "적용"이 필요했다. Jev는 각 단계의 대상을 고를 뿐, 단계가 몇 개인지는 시나리오가 말해줘야 한다.
- **봇 차단**: 쿠팡은 headless에서 Access Denied, headed에서도 홈만 열리고 검색·카테고리는 차단. 야놀자는 headless 차단. 네이버 항공권 자동완성 API는 headless에서 빈 결과를 돌려준다(headed 필요).

## 한계

- 이름 없는 아이콘 버튼, canvas, shadow DOM은 후보가 되지 못한다. 레거시 화면의 `div onclick`, label 없는 input도 마찬가지다 (`title` 속성은 이름으로 잡힌다).
- 스크롤, 호버, 드래그, 키보드 내비게이션, 파일 업로드는 없다.
- 라벨 규칙은 휴리스틱이다. 평면적인 접근성 트리(네이버 호텔처럼 루트에 200개 줄)에서는 여전히 무의미한 라벨이 붙을 수 있다.
- 후보 축소가 문자 겹침 기반이라 스텝 문장이 요소 이름과 전혀 다른 표현이면 상위 60개에서 밀려날 수 있다.
- 시나리오 7개, 사이트 4개에서 확인한 결과다. 통계적 근거로 보기엔 적다.
