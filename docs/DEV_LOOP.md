# 개발 루프 데모: 개발 → Jev 러너(`eastshift run`) → 실패 수정 (2026-09-23)

`demo-app/app.py`(표준 라이브러리, 포트 8787)를 대상으로 "화면·백엔드 수정 → 테스트 → 실패 유형별 수정" 루프를 세 바퀴 돌렸다.
시나리오는 `scenarios/demo/01_login.yaml`(로그인 후 세션 저장)과 `scenarios/demo/02_reservation.yaml`(이름, 캘린더, 숙박 일수 select, 조식 checkbox, 제출, 금액 검증).

```bash
python demo-app/app.py 8787 &
export TYPESAFE_API_KEY=...
uv run eastshift run scenarios/demo/01_login.yaml                                          # 로그인 → .auth/demo.json 저장
uv run eastshift run scenarios/demo/02_reservation.yaml --storage-state .auth/demo.json      # 로그인 상태 재사용
# CI (API 키 없음): 캐시만 재생, 캐시 미스는 실패
uv run eastshift run scenarios/demo/*.yaml --storage-state .auth/demo.json --replay-only
```

## 기준 실행

| | Jev 결정 모드 | CI 재생 모드 (`--replay-only`, 키 없음) |
| :--- | :--- | :--- |
| 01 로그인 | PASS, Jev 3회 | PASS, Jev 0회 |
| 02 예약 | PASS, Jev 5회, 11.2s | PASS, Jev 0회, 9.5s |

## 1바퀴: 백엔드 버그 (앱을 고쳐야 하는 실패)

조식 요금을 박수만큼 곱하지 않도록 바꿈 (2박+조식 240,000원 → 220,000원).

- CI 재생: **13번 스텝 `expect text "총 금액 240,000원"` 실패.** 스크린샷 `reports/02_reservation-step13-fail.png`에 220,000원이 찍힘. Jev 호출 0.
- 수정 후 CI 재생: PASS.

## 2바퀴: UI 라벨 변경 (아무도 안 고쳐도 되는 실패)

"예약자 이름" → "투숙객 이름", 버튼 "예약하기" → "예약 완료하기".

- CI 재생: **2번 스텝에서 1.8초 만에 실패**, 이유 `replay-only: cached target not found on page (run locally with Jev to repair the cache)`.
- 로컬 Jev 실행: 두 스텝을 `⚠ HEALED`로 자동 복구하고 PASS. 요약 줄에 `⚠ healed steps 2 (cache changed, review the diff)`.
- 캐시 diff는 라벨 변경 그대로:

```diff
-  "name": "예약자 이름",
+  "name": "투숙객 이름",
-  "name": "예약하기",
+  "name": "예약 완료하기",
```

- 고친 캐시로 CI 재생: PASS. 시나리오 문장("예약자 이름 입력", "예약하기 버튼 누르기")은 그대로다.

## 3바퀴: 흐름 변경 (시나리오를 고쳐야 하는 실패)

제출 버튼이 확인 모달("예약 내용을 확정하시겠습니까?" / 확정 / 취소)을 열도록 바꿈.

- CI 재생: 버튼 클릭은 성공하지만 **9번 `expect url_contains "/reservations/"` 실패** (URL이 `/`에 머묾).
- 로컬 Jev 실행: **똑같이 실패.** 요소는 다 찾았으니 자동 복구할 게 없다. 빠진 건 스텝이지 셀렉터가 아니다.
- 시나리오에 두 줄 추가:

```yaml
  - do: 예약하기 버튼 누르기
  - expect: { text: "예약 내용을 확정하시겠습니까?" }
  - do: 확인 모달에서 확정 버튼 누르기
```

- 로컬 Jev 실행: 새 스텝만 Jev가 결정(`button "확정"`, conf 0.96), 나머지는 캐시. PASS.
- CI 재생: 2/2 PASS.

## 결론

| 실패 원인 | CI가 보여주는 것 | 처리 | Jev 호출 |
| :--- | :--- | :--- | ---: |
| 앱 버그 | assert 실패 + 스크린샷 | 코드 수정 | 0 |
| 라벨·구조 변경 (의미 동일) | `replay-only: cached target not found` | 로컬에서 한 번 실행, 캐시 diff 커밋 | 바뀐 스텝 수만큼 |
| 흐름 변경 | 다음 assert 실패, 로컬에서도 동일 | 시나리오에 스텝 추가 | 새 스텝 수만큼 |

세 종류가 러너 출력에서 구분된다. 라벨 변경은 `HEALED` 표시와 캐시 diff로, 흐름 변경은 "로컬 Jev 실행도 실패"로, 앱 버그는 "요소는 다 찾았는데 값이 다름"으로 드러난다.

이 데모에서 붙인 것: `save_storage_state` 스텝과 `--storage-state`(로그인 재사용), `--replay-only`(CI), `healed` 표시, 스텝 문장 키 캐시(스텝을 끼워 넣어도 다른 스텝 캐시가 밀리지 않음).

## 아직 없는 것

- 테스트 데이터 초기화 훅(시나리오 전후 DB 리셋). 데모 앱은 메모리라 재시작으로 대신했다.
- 병렬 실행, JUnit 리포트, 실패 시 재시도.
- iframe·shadow DOM 내부 요소, 파일 업로드, 호버, 드래그.
- 캐시 diff를 PR 코멘트로 올리는 자동화.

## 스킬로 시나리오 생성 (2026-09-23)

당시 `jev-e2e-scenarios` 스킬(지금은 `e2e-tests`, `smoke`로 나뉨)의 절차(코드에서 대상·기대값 → explorer로 실제 이름·흐름 → 관점별 작성 → 실행·분류)를 데모 앱에 적용했다. 생성은 Claude Code 세션 안에서 했으므로 별도 LLM 비용 없음. 실행은 Jev.

| 파일 | 관점 | 첫 실행 | 분류와 조치 |
| :--- | :--- | :--- | :--- |
| 03_auth_redirect | 인증 | PASS (Jev 0회) | - |
| 04_login_wrong_password | 인증 | FAIL: 실패 후 아이디 값 `''` | 코드가 값을 보존하지 않음. 기대값을 코드에 맞추고 **개선 후보로 보고** |
| 05_validation_required | 유효성 | PASS | 통과했지만 오류 페이지 URL이 `/reserve`(POST 응답)에 머묾 → 새로고침 시 재전송. **개선 후보로 보고** |
| 06_boundary_1night_no_breakfast | 경계값 | PASS | 100,000원, `breakfast=0` |
| 07_boundary_3nights_breakfast | 경계값 | PASS | 360,000원. "30"이 월 라벨 대신 fieldset 라벨로 잡혀 형제 라벨 범위를 6→45줄로 넓힘 |
| 08_cancel_modal | 내비게이션 | FAIL: `field "예약자 이름"` 없음 | 2바퀴에서 라벨이 "투숙객 이름"으로 바뀐 것을 반영 못 한 **시나리오 오류**. 수정 |
| 09_navigation_back | 내비게이션 | FAIL: 같은 원인 | 수정 |

수정 후 9개 전부 Jev 모드 PASS, `--replay-only` PASS (Jev 0회). 실패 3건 중 앱 버그는 없었고 개선 후보 2건, 시나리오 오류 2건이었다. 시나리오 오류 2건은 "요소 이름은 explorer 출력에서 가져올 것" 규칙을 내가 어긴 결과다(코드의 옛 라벨을 기억으로 썼다).
