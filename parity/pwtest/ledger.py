"""실행 원장: to-be 비교(`pytest --compare`)가 끝날 때마다 결과를 runs/<app>/<시각>.json 에 남긴다. 관리 화면과 상태 명령이 이 원장만 읽는다.

한 실행 = {app, target, started, finished, oracle: {approved_by, approved_at, ok}, cases: {test: {status, kind, summary, rows, screenshot}}}
  kind: same | drift(기대값이 기록 이후 바뀜) | golden_diff(화면·값·대화상자가 다름) | assert(명시한 확인 값 실패) | error(실행 못 함)

parity status golden/<app>   남은 실패, 분류, 지난 실행 대비 변화 (수정 → 재실행 루프에서 이것만 본다)
"""
from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

RUNS = Path("runs")


def run_dir(app: str) -> Path:
    return RUNS / app


def write_run(app: str, *, target: str, oracle: dict[str, Any], cases: dict[str, dict[str, Any]], started: float,
              junit: str | None = None) -> Path:
    d = run_dir(app)
    d.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    out = d / f"{stamp}.json"
    rec = {"app": app, "target": target, "started": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(started)),
           "finished": time.strftime("%Y-%m-%d %H:%M:%S"), "junit": junit,
           "oracle": {"approved_by": oracle.get("approved_by"), "approved_at": oracle.get("approved_at"), "ok": bool(oracle.get("ok"))},
           "cases": cases,
           "totals": {"pass": sum(c["status"] == "pass" for c in cases.values()), "fail": sum(c["status"] != "pass" for c in cases.values())}}
    out.write_text(json.dumps(rec, ensure_ascii=False, indent=1), encoding="utf-8")
    return out


def load_runs(app: str) -> list[dict[str, Any]]:
    d = run_dir(app)
    if not d.exists():
        return []
    runs = []
    for p in sorted(d.glob("*.json")):
        try:
            runs.append({**json.loads(p.read_text(encoding="utf-8")), "_file": p.name})
        except (OSError, ValueError):
            continue
    return runs


def history(app: str) -> dict[str, list[dict[str, Any]]]:
    """테스트별 실행 이력 (오래된 것부터): [{run, target, status, kind, approved_at}]."""
    out: dict[str, list[dict[str, Any]]] = {}
    for r in load_runs(app):
        for name, c in r["cases"].items():
            out.setdefault(name, []).append({"run": r["_file"].removesuffix(".json"), "finished": r["finished"], "target": r["target"],
                                             "status": c["status"], "kind": c.get("kind", ""), "approved_at": r["oracle"].get("approved_at")})
    return out


def compare_runs(latest: dict[str, Any], previous: dict[str, Any] | None) -> dict[str, list[str]]:
    now = {n: c["status"] for n, c in latest["cases"].items()}
    before = {n: c["status"] for n, c in (previous or {}).get("cases", {}).items()}
    return {
        "newly_passing": sorted(n for n, s in now.items() if s == "pass" and before.get(n) not in (None, "pass")),
        "newly_failing": sorted(n for n, s in now.items() if s != "pass" and before.get(n) == "pass"),
        "still_failing": sorted(n for n, s in now.items() if s != "pass" and before.get(n) not in (None, "pass")),
        "new_tests": sorted(n for n in now if n not in before),
        "gone_tests": sorted(n for n in before if n not in now),
    }


def status_text(app: str, oracle_status: dict[str, Any]) -> str:
    runs = load_runs(app)
    if not runs:
        return f"{app}: to-be 비교 실행 기록이 없습니다 (pytest e2e/{app} --base-url <to-be> --compare golden/{app})"
    latest, previous = runs[-1], (runs[-2] if len(runs) > 1 else None)
    delta = compare_runs(latest, previous)
    fails = {n: c for n, c in latest["cases"].items() if c["status"] != "pass"}
    L = [f"{app} · 마지막 비교 {latest['finished']} · 대상 {latest['target']} · 승인본 {latest['oracle'].get('approved_at') or '없음'}"]
    if oracle_status.get("approved_at") and latest["oracle"].get("approved_at") != oracle_status.get("approved_at"):
        L.append(f"  ! 현재 승인본({oracle_status['approved_at']})과 다른 승인본으로 실행됐습니다. 다시 비교하세요.")
    if not oracle_status.get("ok"):
        L.append("  ! 오라클이 승인되지 않은 상태입니다.")
    L.append(f"  통과 {latest['totals']['pass']} · 남은 실패 {latest['totals']['fail']} (전체 {len(latest['cases'])})")
    if previous:
        L.append(f"  지난 실행({previous['finished']}) 대비: 통과로 바뀜 {len(delta['newly_passing'])}, 새로 실패 {len(delta['newly_failing'])}, "
                 f"계속 실패 {len(delta['still_failing'])}"
                 + (f", 새 테스트 {len(delta['new_tests'])}" if delta["new_tests"] else "") + (f", 없어진 테스트 {len(delta['gone_tests'])}" if delta["gone_tests"] else ""))
    by_kind: dict[str, list[str]] = {}
    for n, c in fails.items():
        by_kind.setdefault(c.get("kind", "fail"), []).append(n)
    labels = {"golden_diff": "as-is와 다름", "assert": "확인 값 실패", "drift": "기대값이 기록 이후 바뀜 (테스트 되돌릴 것)", "error": "실행 못 함"}
    for kind, names in by_kind.items():
        L.append(f"  {labels.get(kind, kind)} {len(names)}:")
        for n in names:
            mark = " (새로)" if n in delta["newly_failing"] else ""
            L.append(f"    - {n}{mark}: {fails[n].get('summary', '')[:110]}")
    if delta["newly_passing"]:
        L.append("  통과로 바뀜: " + ", ".join(delta["newly_passing"]))
    if not fails:
        L.append("  모두 통과. 검증 보고서를 만들 차례입니다 (parity report …).")
    return "\n".join(L)
