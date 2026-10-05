---
name: e2e-tests
description: Write, run and triage Playwright E2E tests (pytest `ui` fixture) from a web app's codebase and running UI. Use for E2E tests of a page or flow, and for migration equivalence — checking a rebuilt to-be app behaves exactly like the as-is (legacy) app, bugs included.
---

# E2E tests (Playwright + `ui` fixture)

Tests live in `e2e/<app>/test_*.py` and use the `ui` fixture from the `east2west.pwtest` pytest plugin (auto-registered). Its API and the reason behind each call are in [east2west/pwtest/ui.py](../../../east2west/pwtest/ui.py); options in `uv run pytest --help` under "as-is/to-be E2E". Worked example: [e2e/legacy/test_orders.py](../../../e2e/legacy/test_orders.py).

For a sweep over many similar screens ("do all menus open and query"), use the `smoke` skill instead; this skill is for flows whose values you check.

## 0. Pick the mode

| Mode | When | Oracle |
| --- | --- | --- |
| **general** | testing one app on its own | the codebase's intent. A mismatch can be an app bug |
| **migration** | an as-is app is being rebuilt as to-be and must behave the same | the **running as-is app**. As-is behavior, bugs included, is the expected value |

Ask which mode if the user has not said. Every step below names what changes in migration mode.

## 1. Ground truth from the codebase

Read the app's routes, screens, form handlers, validation and every calculation that shows on screen. Produce a target list: one line per page or flow with entry URL, fields, rules, required auth, and the displayed value for a given input (computed from the code).

Migration mode: read the **as-is** code only. Also list behavior that looks wrong — truncation instead of rounding, missing validation, 0 or negative accepted, odd messages. Each becomes a test that pins that behavior, its docstring saying "as-is 동작 보존". Include every alert/confirm/prompt message.

Done when every route reachable from the UI has a line and every displayed value has a formula or fixed string behind it.

## 2. Ground truth from the running UI

Confirm the app answers at its base URL (ask the user to start it otherwise). For each target read the real accessible names and the flow:

```bash
uv run python tools/explore.py <url> "" 80
uv run python tools/explore.py <url> "link:주문 등록;button:팝업 닫기" 80   # ; chains clicks, searches all frames
```

Names in tests come from this output, never from memory — in migration mode from the **as-is** UI.

For a screen with many flows, map them with the crawler first; it costs no LLM tokens and writes a Playwright draft of every path ([docs/CRAWL.md](../../../docs/CRAWL.md)). It clicks save/confirm buttons for real, so run it only against a test environment, and show the user the `--dry-run` list first:

```bash
uv run east2west crawl <path> --base-url <as-is> --fixtures <inputs.yaml> --out crawl/<app> --dry-run
uv run east2west crawl <path> --base-url <as-is> --fixtures <inputs.yaml> --out crawl/<app>
```

`crawl/<app>/graph.md` is the flow list for your target list; `test_crawl.py` checks navigation only. Copy the tests you keep into `e2e/<app>/`, give each a docstring, and add the business values (amounts, messages) — `crawl/<app>/` is regenerated on every crawl.

Done when every name you intend to use appeared in explorer or crawl output, including every confirmation step (적용, 확정, modal buttons) of each flow.

## 3. Write tests

- One `ui.*` call per human action. Tests say **what** (`ui.select("품목", "볼펜")`); how a widget is operated belongs in `ui.py`. When a to-be widget works differently (custom dropdown, datepicker), extend `ui.py` once rather than branching in tests.
- Every test asserts a value: `expect_field`, `expect_dialog`, `expect_text` with the full computed string. Prefer widget-agnostic checks (`expect_field("품목", "노트북")`) over snapshot fragments.
- Cancel paths call `ui.dialog("dismiss")` before the action that opens the confirm.
- Input behavior (keystroke masks, length-limit alerts, upper-casing, date clean-up, the red invalid marker and its message on leaving a field): use `ui.type(name, value)` — it clicks, clears, types key by key and presses Tab — instead of `ui.fill`, which sets the value at once and skips those handlers. Each `type` step also records the field's error state (aria-invalid/aria-errormessage/aria-describedby, or ExtJS 4 getActiveError) as an observed line, and `ui.expect_field_error(name, message)` asserts it (`""` = no error). Migration mode: probe each editable field with a mixed value (letters, digits, Hangul, space, symbols), an over-long value and an empty value.
- Assertions are exact: `expect_dialog` compares the whole message, `expect_text` gets the full string (`"합계 1,355원"`, not `"합계"`).
- Secrets come from environment variables (`os.environ["APP_PASSWORD"]`).

Migration mode: expected values come from the as-is only. The to-be code may be read for one purpose — making `ui.py` operate a to-be widget — and never to choose an expected value.

Migration mode scenario checklist — per screen, write a test for each line that applies (values come from the as-is screen: its options, the rows of its first query result, its length limits). Each catches a kind of difference that a single happy-path query misses:

| Perspective | Test |
| --- | --- |
| Entry | open the screen and do nothing: auto-query or not, focus, default values, first row selected or not |
| Same query twice | query, then query again with nothing changed — each press must send the same requests as the as-is (count included) |
| Back and forth | condition A → B → A: the later result must not be overwritten by an earlier response |
| Options | for each combo, pick a few options spread over the list (first, middle, last, not "전체") and query; a combo whose choice changes another combo's options, re-pick the dependent one |
| Text conditions | a value that is in the as-is result, its first two characters, a value that matches nothing |
| Required condition | query with the required condition empty: the guidance message |
| Buttons | press every button once with no condition and no selection: message, popup or screen change |
| Save/delete guidance | save with no change, delete with no selection: the exact message |
| Grid | sort a column twice (ascending → descending); the second page when the first is full; select a row and check the detail form |
| Reset | change a condition, query, press 초기화, query again: back to the first result |
| Window | shrink the window height: the save button stays reachable |
| Round trip | save a value with special characters (`<>&"'`), query again, read it back, restore |
| Length | per text field, the as-is limit plus one character (bytes if the as-is counts bytes) — saved, cut or refused, and the message |
| Required field | clear a required field and save: the message and which field is marked |
| Create and delete | new row with required fields filled (a marker value), save, find it, delete it, confirm it is gone |

Rows that write need a test database and the user's agreement on the write scope; the `tobe-fix` skill has the safety rules for writes.

## 4. Run and triage

General mode:

```bash
uv run pytest e2e/<app> --base-url <url>
```

| Signal | Class | Action |
| --- | --- | --- |
| `not found in any frame`, `found in N frames` | test error | fix the name from explorer output, or make it unique |
| expect fails, screenshot `reports/<test>-fail.png` shows the app doing something other than the code says | app bug candidate | report with test, screenshot, expected vs actual; leave the test as is |
| expect fails, your expected value was wrong | test error | recompute from the code, rerun |

Migration mode runs on an **oracle**: `golden/<app>/` holds the as-is observations, the expectations each test asserted, mask rules (`oracle.json`) and name maps. Approval is automatic: record, compare and mutate stamp a changed golden (`oracle.auto_approve`, approver `자동 승인`), so every run is tied to the exact golden it used; a hook blocks hand edits to `golden/` and hand-written approvals. Rules and reasons: [east2west/pwtest/oracle.py](../../../east2west/pwtest/oracle.py).

```bash
uv run pytest e2e/<app> --base-url <as-is>                  # until green: every failure here is a test error, fix the expected value to what as-is shows
uv run pytest e2e/<app> --base-url <as-is>                  # second green run: values that differ between runs are mask-rule proposals
uv run pytest e2e/<app> --base-url <as-is> --record golden/<app>
```

The record run approves the golden itself (no approval step to wait for). Tell the user what was recorded; they can look at it in the 시나리오 tab of `uv run east2west ui`, started in their own terminal. Mask rules, name maps and equivalent-mutant entries are proposals you write in your message; the user puts them in `golden/<app>/` (the next compare approves the change).

Next, prove the tests catch defects:

```bash
uv run east2west mutate e2e/<app> --base-url <as-is> --compare golden/<app> --max-per-op 100
```

Each survivor is either a test gap (add or extend a test, expected values from a green as-is run, re-record) or a defect with no observable effect, which you propose to the user as an `equivalent_mutants` entry with its reason. Repeat until the score is at least 80% and every survivor is classified; a re-record approves itself.

It runs on the as-is only and needs a recorded golden (approved automatically at start) plus a green discovery run; it does not depend on any to-be result. Each mutant stops at its first detecting test, so a test with zero kills in the report is not a weak test. Finished mutants accumulate in `<out>.partial.jsonl`: if the run is interrupted, rerun the same command and only the remaining mutants run. For a very large app cap the total with `--max-mutants N`.

Then compare and report (`-n 4` splits the comparison over four browsers; the ledger still gets one run. Not with `--reset-path`):

```bash
uv run pytest e2e/<app> --base-url <to-be> --compare golden/<app> --junitxml reports/junit-<app>.xml
uv run east2west report --oracle golden/<app> --junit reports/junit-<app>.xml --mutation reports/mutation-<app>-golden.json --out reports/verification-<app>.md   # markdown verdict you quote
uv run east2west status golden/<app>                                          # fix → rerun loop: remaining failures and what changed since the last run
```

Every `--compare` run writes a ledger entry to `runs/<app>/` (with a copy of the JUnit and failure screenshots); `east2west status` reads it. When the user is iterating on to-be fixes, report the status output (newly passing / newly failing / still failing) rather than raw pytest output. The person views everything in `uv run east2west ui` (first screen: project list; per project the Screen Map opens first — three sources: as-is crawl, to-be crawl, and the comparison (golden scenarios vs to-be: red = differs, yellow = not built in to-be yet, blue = new in to-be) — then 시나리오 (scenario list, with the 'as-is에서 다시 기록' button) and 실행 (the run list with the verdict of the selected run), any past run selectable; a project with no golden shows a "Screen Map 만들기" button that crawls the as-is, copies the draft tests into `e2e/<app>/` and records (approved automatically) — the same steps you would run by hand; the UI crawls "to the end" by default: until no new state appears, at most `--route-states 10` states per route, with the source's unreached routes opened directly via `--seeds`, and it can be stopped and resumed) — tell them to start it in their terminal; there are no separate HTML files to generate (the map, overview and report pages exist only in that UI). Crawl samples list rows: per list it clicks one row per value of the branch columns (status/type; picked by fixture `pick:` > Jev > rule, up to `--reps`), so read graph.md's "목록 표본" table and add `pick:` to the fixtures when a column you know matters was not chosen. When you crawl by hand, also run `uv run east2west routes <as-is src> --out crawl/<app>/routes.json` (static scan of the routing declarations; the UI's crawl button does this itself): the map then shows screens the code declares but no crawl path or scenario reached as gray cards, and you report each gray screen with the reason you found (missing fixture value, deny list, login, data, budget) or as a real gap. `east2west.json` is the project registry (as-is/to-be source dirs and URLs, written from that first screen): read it for the app's base URLs before asking; you may add an entry there yourself if the user gives you the paths (it is not part of the oracle).

| to-be signal | Class |
| --- | --- |
| pass | same behavior |
| `not found in any frame` | element renamed or re-widgeted in to-be. Intended rename: propose a name map entry (`golden/<app>/name_map.<target>.json`); different widget: extend `ui.py`; otherwise a to-be defect |
| `differs from golden` or expect fails | behavior differs: to-be defect; quote the diff lines. To fix it in the to-be, use the `tobe-fix` skill |
| a `서버 요청 횟수 · GET /…` row (`requests: … ×2 → ×1`) | the to-be calls an API both sides use a different number of times per action: to-be defect. Only a path whose count depends on timing (polling, notifications) is a `request_ignore` proposal in `oracle.json` |
| `expectation changed since as-is recording` | a test's expected value was edited after recording: restore it; the as-is recording is the truth |
| diff lines that are pure layout, not content, value or dialog | comparator noise: propose a mask rule, or change `east2west/observe.py` `flatten`; tell the user which |

Done when general-mode tests pass or each failure is classified, and in migration mode when `reports/verification-<app>.md` exists and every to-be failure in it is classified.

## 5. Report

List tests written, pass/fail per test, and the perspectives covered (happy path, validation, auth, boundary, navigation) with any skipped and why. General mode: app bug candidates with evidence. Migration mode: quote the verdict line of `reports/verification-<app>.md` and tell the user the same report is the 실행 tab of `uv run east2west ui` (the page people read), then the preserved as-is behaviors you pinned, the mutation score and each survivor's classification, each to-be defect with its diff lines, and every proposal waiting on the user (mask rules, name maps, equivalents). Every number you state comes from that report. `e2e/<app>/`, `golden/<app>/` and the report are the deliverable.
