# 전환 검증: jev-e2e vs Playwright 스크립트 (2026-09-24)

같은 4개 시나리오(`examples/yaml-migration/scenarios/*.yaml` ↔ `e2e/legacy/test_orders.py`), 같은 as-is/to-be 앱(`demo-app/legacy_app.py`),
같은 골든 비교 로직(`jev_e2e/observe.py`). 다른 것은 요소를 찾는 방법뿐이다: Jev(자연어 → 요소, 캐시) vs `getByRole` + 프레임 무관 helper(`jev_e2e/pwtest/ui.py`).

```bash
uv run pytest e2e/legacy --base-url http://127.0.0.1:8801 --record golden/legacy     # as-is
uv run pytest e2e/legacy --base-url http://127.0.0.1:8803 --compare golden/legacy    # to-be
uv run pytest e2e/legacy --base-url http://127.0.0.1:8804 --compare golden/legacy --name-map e2e/legacy/name_map_renamed.json
```

| to-be | jev-e2e | Playwright |
| :--- | :--- | :--- |
| `tobe` 충실한 전환 (frameset→단일 페이지, URL 변경) | 4/4 PASS, 차이 0 | 4/4 PASS, 차이 0 |
| `tobe-fixed` as-is 버그 2개를 고침 | 3/4 탐지 (부가세 120→124, alert 문구, 수량 0) | 3/4 탐지, 같은 diff |
| `tobe-renamed` 라벨 변경 + 부가세 변경, CI 모드 | 4/4 라벨에서 멈춤, 부가세 미도달 | 4/4 라벨에서 멈춤, 부가세 미도달 |
| `tobe-renamed`, 복구 시도 | Jev 복구: 수량→주문 수량 4/4 성공, 저장→등록 1/3 (나머지 abstain·margin 미달). expect의 이름은 복구 안 됨. **부가세 탐지 1/1, 4개 중 1개만 끝까지** | 이름 매핑 2줄: **4개 모두 끝까지, 부가세 탐지** |
| 실행 시간 (4개, settle 500ms) | 11.3s | 10.7s |
| 시나리오 분량 | YAML 48줄 | Python 42줄 |
| 실행 시 외부 의존 | 캐시 미스 시 Jev API | 없음 |

결론
- 버그 탐지력은 골든 비교가 만든다. 요소 찾기 방식과 무관하게 같은 결과였다.
- Jev의 고유 이점은 라벨 변경 자동 복구인데, 의미가 가까운 변경(수량→주문 수량)만 안정적이고 의미가 먼 변경(저장→등록)은 margin 게이트에 걸린다 (추측하지 않는 설계라 맞는 동작).
  전환 프로젝트의 라벨 변경은 목록으로 관리되는 경우가 많아 매핑 파일이 더 확실하다.
- Jev가 더 나은 곳: 라벨이 자주, 예측 없이 바뀌는 UI, 비개발자가 읽는 자연어 시나리오, 셀렉터를 미리 알기 어려운 외부 사이트.

이 비교에 따라 역할을 나눴다: 동등성 검증은 Playwright(`e2e/`, `e2e-tests` 스킬), 전체 화면 스모크는 jev-e2e matrix([SMOKE.md](SMOKE.md), `jev-smoke` 스킬).
