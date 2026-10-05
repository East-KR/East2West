---
name: tobe-fix
description: Fix a to-be difference so the rebuilt app behaves exactly like the as-is — strange behavior and server request counts included — then re-verify and record what is strange in the as-is. Use after an East2West comparison reports a to-be difference (differs from golden, expect failure, a '서버 요청 횟수' row) and the to-be source is to be changed, not the oracle.
---

# Fix a to-be difference (match the as-is)

The comparison and its triage are the `e2e-tests` skill. This skill starts where that one ends: a difference classified as a to-be defect, and the to-be source in front of you.

## 0. The rule

The as-is is the expected value: same defaults, same actions, same values, same messages, same number of server requests per action. A difference is a to-be defect even when the as-is looks wrong and even when it comes from "just the framework". Do not improve on the as-is. What is strange goes into the as-is 이상 동작 record (step 6) so the customer can decide; until they decide `change`, the to-be copies it.

The only differences that are not defects are those where the comparison itself does not hold — `env` (data or settings), `blocked` (no asset to compare), `tool` (observer limit, confirmed in source), `invalid` (as-is observation failed) — plus `customer` (a recorded `change` decision). Never add a `difference_rules` entry, a mask rule or a name map to make a behavior difference pass, and never edit `golden/` or a test's expected value.

## 1. Read the difference

```bash
uv run east2west status golden/<app>
```

For each failing test, read its 다른 점 rows and look at the as-is capture (golden) and the to-be capture (`runs/<app>/<시각>/shots/`) of the step. Write one line per difference: what a user sees differently, at which step.

## 2. Find the as-is cause before touching the to-be

Read, in this order, until the behavior is explained:

- the screen's own code (page, script, controller);
- the shared components it uses and the framework's behavior underneath — a screen often inherits behavior from a shared base: grid load and first-row selection, a confirm callback without scope that never updates the remembered row, a store that reloads once per write request, a button handler that queries twice;
- the server path: SQL/mapper, procedures, the error text and how it reaches the screen;
- database differences when the to-be database differs: empty string vs NULL (Oracle stores '' as NULL), byte vs character length limits, the order of rows that tie on the ORDER BY columns (undefined), date and number formats, error messages;
- request count and timing: how many times each API is called per action, and which response wins when two loads overlap (the later one, if loads are not cancelled).

Write down what the as-is does, why (file:line), and whether the to-be can reproduce it exactly. If it cannot, say which difference will remain and why before you change anything.

## 3. Fix the to-be

- Reproduce the as-is behavior, including the strange part and the request count.
- Fix where the as-is behavior lives: if it comes from an as-is shared component, fix the to-be shared component — then every screen that uses it is in scope for step 4. Otherwise fix the screen.
- Expected values never come from the to-be code. Read the to-be only to change it.

## 4. Verify

- Re-run the comparison for the screen's scenarios; after a shared-component fix, for every screen that uses that component (find the usages, do not guess). Compare only — re-record the as-is only when the as-is itself changed.
- Run the project's full static checks and unit tests yourself (type check over the whole project, lint on changed files). A sub-agent's "clean" is a claim: rerun the full command before you report it.
- Report from `uv run east2west status golden/<app>`: newly passing, still failing, newly failing.

## 5. Writes during verification

Save and delete scenarios write to a database. Only on a local or test database, and only within the write scope the user allowed (for example: edit then restore, create → save → delete with a marker value, pressing save with no change).

- To observe a write without saving it, intercept the request by a path pattern that also matches query strings (`?_dc=` cache busters), and take a before/after snapshot (row hash) of the target tables. If anything changed, restore it from the snapshot and tell the user what was written and that it was restored.
- Rows created with a marker value are found and deleted at the end; confirm none remain.
- Inserting fixture data (accounts, codes, rows only one side has) needs the user's permission first.

## 6. Record what is strange in the as-is

The to-be copies it; the record lets the customer decide (현행 유지 · 수정 · 보류 in the as-is 이상 동작 tab of `uv run east2west ui`). Records go in `quirks/<app>.json`; the format is the first docstring of [east2west/pwtest/quirks.py](../../../east2west/pwtest/quirks.py).

- One record per behavior: what is strange, what users would normally expect, business impact, repro steps, what you see, cause, evidence, options for the customer, and the golden tests that show it (`tests`, plus `match` — a regex on the 다른 점 '무엇이' — when only some rows belong to it).
- `severity` is `high`, `mid` or `low`. `verified` is `run` (seen in a run) or `source` (read in the code only).
- Every evidence path is a file you opened; check each one exists before writing it.
- Use `kind: "env"` for something that only looks different because of local data or settings.
- Check the file: `uv run east2west quirks golden/<app>` prints format problems first.
- Never write `quirks/<app>.decisions.json`: decisions are the person's, saved from the tab.

## 7. Report

Per difference: the as-is cause (file:line), what changed in the to-be, the scenarios re-run and their result, any difference that remains and why, the quirk records written, and any database write with its restoration. Numbers come from `east2west status` and the checks you ran.
