# EastShift — 에이전트 작업 안내

무엇을 하는 도구인지는 README.md. 여기에는 작업별로 먼저 읽을 곳과, 코드만 봐서는 모르는 약속만 적는다. 모듈마다 첫 docstring이 그 모듈의 설명서다.

## 작업별로 먼저 읽을 곳

| 작업 | 먼저 읽을 곳 |
|---|---|
| e2e 동등성 테스트 작성·실행·분류 | `e2e-tests` 스킬 |
| 화면 N개 스모크 | `smoke` 스킬, docs/SMOKE.md |
| 전환 검증 절차 (기록 → 승인 → 결함 주입 → 비교 → 보고서) | docs/MIGRATION.md |
| 골든 기록·비교 규칙 (정규화, 가림, 이름 매핑) | `eastshift/observe.py`, `eastshift/pwtest/oracle.py` |
| 승인 화면, 재승인 diff | `eastshift/pwtest/review.py`, `eastshift/pwtest/stepdiff.py` |
| 실행 원장 (runs/) · 상태 · 보고서 | `eastshift/pwtest/ledger.py`, `eastshift/pwtest/report.py` |
| 통합 화면 (eastshift ui) 경로·작업 | `eastshift/pwtest/hub.py` 첫 docstring |
| 탐색기 (crawl) · 목록 대표 행 · 라우트 대조 | docs/CRAWL.md, `eastshift/crawl.py`, `eastshift/lists.py`, `eastshift/routes.py` |
| YAML 러너 · Jev · triage | docs/YAML_RUNNER.md |
| 결함 주입 (mutate) | `eastshift/pwtest/mutation.py` |

## 약속

- **골든은 사람 것이다.** golden/ 쓰기와 승인은 사람이 `eastshift ui`에서 한다. `tools/guard_oracle.py` hook이 막고 있으니, 골든을 바꿔야 하면 무엇을 왜 바꿀지 사용자에게 제안한다. 골든 JSON 형식은 그대로 둔다.
- **사용자 화면은 안정적으로 둔다.** 명령·옵션, 통합 화면, 승인 흐름을 바꾸는 일은 사용자가 정한다. 내부 개선은 자유롭다.
- **결정론.** 텍스트 생성 LLM은 쓰지 않는다. Jev는 후보 중 하나를 고를 뿐이고 선택 사항이다. 보고서는 산출물(골든, JUnit, 결함 주입 결과)에서만 만든다.
- **테스트는 임시 원장을 쓴다.** 승인·비교를 거치는 테스트는 `monkeypatch.setattr(ledger, "RUNS", tmp_path / "runs")`로 원장을 옮긴다. 안 옮기면 저장소의 runs/에 사본이 생긴다.
- **문서의 명령은 검사된다.** README·docs·스킬에 적힌 `eastshift …`와 `pytest …` 옵션은 `tests/test_docs.py`가 실제 CLI·플러그인과 대조한다. 명령을 바꾸면 같은 변경에서 문서도 고친다.

## 확인

```bash
uv run pytest -q tests
```
