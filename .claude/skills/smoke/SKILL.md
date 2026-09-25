---
name: smoke
description: Smoke-sweep many similar screens with one YAML scenario run over a screen list (matrix), each screen's query button written into the list ahead of time. Use to check that every menu/screen of an app — typically a legacy or migrated system — opens, queries and opens a detail, without per-screen scripts.
---

# Smoke sweep (explicit targets)

One YAML scenario, one screen list. Which button is "조회" on each screen is decided **before** the run and written into the list, so the run itself makes no judgement: the same list gives the same result every time, and every choice is visible for review. Template: [scenarios/smoke/screen_smoke_targets.yaml](../../../scenarios/smoke/screen_smoke_targets.yaml); format, expect keys and results: [docs/SMOKE.md](../../../docs/SMOKE.md).

Checking computed values or as-is/to-be equivalence of a flow is the `e2e-tests` skill; this sweep answers "does every screen basically work".

A screen whose flows go beyond open → query → detail (tabs, modals, multi-step forms) can get its own navigation smoke from the crawler instead of a list row: `parity crawl <path> --out crawl/<app>-<screen>` ([docs/CRAWL.md](../../../docs/CRAWL.md)). Run `--dry-run` first and show the user what it will click; it presses save/confirm buttons for real.

## 1. Build the screen list

Read the app's menu definition and router. Write `scenarios/<app>/screens.yaml`, one row per screen: `{ SCREEN: <menu name>, URL: <path> }`. Group screens by shape — list+detail, list only, form only — one list per group. Work in batches (a menu or module at a time); each batch goes through steps 2–4 on its own.

Done when every menu entry of the batch is a row in exactly one group, or listed as excluded with the reason (needs a selected record, destructive, external).

## 2. Settle each screen's target

```bash
uv run parity targets scenarios/<app>/screens.yaml --base-url <as-is> --out scenarios/<app>/screens.targets.yaml
```

It opens each screen without clicking and lists its buttons. A screen whose buttons contain exactly one common query name (조회, 검색, 찾기, 조회하기, 검색하기, Search, Find; `--names` to change) is settled `by: rule`. Every other row is `by: review` with its `candidates`.

For each `review` row, pick the button that runs the list query, using the candidates and the as-is code for that screen. Write its name into `QUERY`, set `by: picked`, and put the reason in `note` ("인쇄는 조회가 아님"). If no candidate queries (an icon without a name, a screen that loads on open), leave `QUERY` empty and move the row to the excluded list with the reason. Tell the user which rows you picked; those are the lines to review.

Done when no row is left `by: review`.

## 3. Run on the reference app

```bash
uv run parity run scenarios/<app>/screen_smoke_targets.yaml --base-url <as-is> --settle-ms 500
```

In migration work the reference is the as-is. For every failing row:

| Signal | Action |
| --- | --- |
| `target not found`, `target is ambiguous` | the name in `QUERY` is wrong or shared by two buttons → fix the row (add `nth` only when the order is stable) |
| `no link in data row 1` | the screen has no detail link → move it to a list-only group |
| another expect fails on the reference app | the screen does not fit the group's shape → regroup it; a genuine reference-app failure goes to the user |

Done when every row passes on the reference app.

## 4. Run on the target

```bash
uv run parity run scenarios/<app>/screen_smoke_targets.yaml --base-url <target> --junit reports/junit-<app>.xml
```

Each failure's reason names its class: `HTTP error: 500 …`, `JS error: …`, `unexpected dialog: …`, `data rows 0 < 1`, title mismatch — a target defect. `target not found` means the button was renamed or removed in the target; report the rename, and fix the list only after the user confirms it is intended.

## 5. Report

Screens swept per group, how many were settled by rule and how many you picked (with the picked rows), excluded screens with reasons, pass/fail per screen, and each target defect with its reason string and screenshot `reports/<stem>--NN-step<N>-fail.png`.

The Jev-picked variant (`scenarios/smoke/screen_smoke.yaml`, natural-language steps, `TYPESAFE_API_KEY`) still works; use it only when the user asks for it.
