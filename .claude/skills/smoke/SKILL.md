---
name: smoke
description: Smoke-sweep many similar screens with one natural-language YAML scenario (`parity run`) run over a screen list (matrix), Jev picking each screen's buttons. Use to check that every menu/screen of an app — typically a legacy or migrated system — opens, queries and opens a detail, without per-screen scripts.
---

# Smoke sweep (parity matrix)

One YAML scenario, one screen list. Jev decides per screen which element a step means ("조회" / "검색" / "목록 불러오기" / icon), once; the choice is cached and later runs replay it with no Jev call. Format, expect keys and a worked example with results: [docs/SMOKE.md](../../../docs/SMOKE.md). Template: [scenarios/smoke/screen_smoke.yaml](../../../scenarios/smoke/screen_smoke.yaml).

Checking computed values or as-is/to-be equivalence of a flow is the `e2e-tests` skill; this sweep answers "does every screen basically work".

A screen whose flows go beyond open → query → detail (tabs, modals, multi-step forms) can get its own navigation smoke from the crawler instead of a matrix row: `parity crawl <path> --out crawl/<app>-<screen>` writes `crawl_NN.yaml` with a replay cache ([docs/CRAWL.md](../../../docs/CRAWL.md)). Run `--dry-run` first and show the user what it will click; it presses save/confirm buttons for real.

## 1. Build the screen list

Read the app's menu definition and router. Write `scenarios/<app>/screens.yaml`, one row per screen: `{ SCREEN: <menu name>, URL: <path> }`, plus any variable a step needs. Group screens by shape — list+detail, list only, form only — one list per group.

Done when every menu entry reachable by the user is a row in exactly one group, or listed as excluded with the reason (needs a selected record, destructive, external).

## 2. Write one scenario per group

Start from the template. Steps describe the human action generically ("검색 조건 그대로 목록 조회를 실행하는 버튼 누르기"), never a specific button name. Each action is followed by an expect built from `http_ok`, `no_js_error`, `no_dialog`, `rows_at_least`, `title_contains`. Screens that need a session: `storage_state` at the top, secrets as `${VAR}`.

## 3. Run on the reference app with Jev

```bash
uv run parity run scenarios/<app>/<group>.yaml --base-url <url> --cache-dir .parity-cache/<app> --settle-ms 500
```

In migration work the reference is the as-is. For every failing row:

| Signal | Action |
| --- | --- |
| `Jev abstained`, `ambiguous: margin` | read the row's report `pool`; sharpen the step sentence for the whole group, or move the screen to its own group or an `e2e-tests` test |
| expect fails on the reference app | the screen does not fit the group's shape → regroup it; a genuine reference-app failure goes to the user |

Done when every row passes on the reference app with margin ≥ 0.1 — the cache in `.parity-cache/<app>/` is then complete.

## 4. Run on the target

```bash
uv run parity run scenarios/<app>/*.yaml --base-url <target> --cache-dir .parity-cache/<app> --replay-only --junit reports/junit-<app>.xml
```

Each failure's reason names its class: `HTTP error: 500 …`, `JS error: …`, `unexpected dialog: …`, `data rows 0 < 1`, title mismatch — a target defect. `replay-only: cached target not found` means the screen's button was renamed or removed; rerun that row without `--replay-only` so Jev re-picks (`⚠ HEALED`), and report the rename.

## 5. Report

Screens swept per group, excluded screens with reasons, pass/fail per screen, each target defect with its reason string and screenshot `reports/<stem>--NN-step<N>-fail.png`, and every `HEALED` rename.
