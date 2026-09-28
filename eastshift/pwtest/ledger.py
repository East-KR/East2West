"""실행 원장: to-be 비교(`pytest --compare`)가 끝날 때마다 결과를 runs/<app>/ 에 남긴다. 관리 화면·상태 명령·통합 화면(eastshift ui)이 이 원장만 읽는다.

runs/<app>/<시각>.json           한 실행 = {app, target, started, finished, junit, oracle: {approved_by, approved_at, ok}, cases: {test: {…}}, totals}
runs/<app>/<시각>/junit.xml      그 실행의 JUnit 사본 (검증 보고서·Screen Map을 나중에 다시 그릴 수 있게)
runs/<app>/<시각>/shots/         실패 순간 스크린샷 사본
runs/<app>/mutations/<시각>.json eastshift mutate 결과 사본 (승인본마다 하나면 된다)
사본 폴더는 최근 EASTSHIFT_KEEP_RUNS개(기본 30)만 남긴다 (prune). 원장 JSON은 지우지 않는다.
  case kind: same | drift(기대값이 기록 이후 바뀜) | golden_diff(화면·값·대화상자가 다름) | assert(명시한 확인 값 실패) | error(실행 못 함)

eastshift status golden/<app>   남은 실패, 분류, 지난 실행 대비 변화 (수정 → 재실행 루프에서 이것만 본다)
"""
from __future__ import annotations

import json
import os
import shutil
import time
from pathlib import Path
from typing import Any

from . import fscache

RUNS = Path("runs")


def run_dir(app: str) -> Path:
    return RUNS / app


def _claim(d: Path) -> Path:
    """이 실행의 원장 파일 이름을 배타적으로 만든다 (O_EXCL). 같은 초에 끝난 실행이 서로 덮어쓰지 않게 뒤에 온 쪽이 -2, -3 … 을 붙인다."""
    stamp = time.strftime("%Y%m%d-%H%M%S")
    for n in range(1, 1000):
        out = d / (f"{stamp}.json" if n == 1 else f"{stamp}-{n}.json")
        try:
            out.open("x").close()
            return out
        except FileExistsError:
            continue
    raise RuntimeError(f"{d}: could not claim a ledger name for {stamp}")


def _ordered(files) -> list[Path]:
    """시각 순. 같은 초의 -2 가 원본 뒤에 온다 (이름순이면 '-' < '.' 이라 앞에 온다)."""
    return sorted(files, key=lambda p: (p.stem[:15], len(p.stem), p.stem))


def write_run(app: str, *, target: str, oracle: dict[str, Any], cases: dict[str, dict[str, Any]], started: float,
              junit: str | None = None) -> Path:
    d = run_dir(app)
    d.mkdir(parents=True, exist_ok=True)
    out = _claim(d)
    keep = d / out.stem  # 실행 산출물 사본. reports/ 는 다음 실행이 덮어쓰므로 여기 남겨야 이력이 된다
    if junit and Path(junit).exists():
        keep.mkdir(exist_ok=True)
        shutil.copyfile(junit, keep / "junit.xml")
        junit = str(keep / "junit.xml")
    for c in cases.values():
        shot = c.get("screenshot")
        if shot and Path(shot).exists():
            (keep / "shots").mkdir(parents=True, exist_ok=True)
            dst = keep / "shots" / Path(shot).name
            shutil.copyfile(shot, dst)
            c["screenshot"] = str(dst)
    rec = {"app": app, "target": target, "started": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(started)),
           "finished": time.strftime("%Y-%m-%d %H:%M:%S"), "junit": junit,
           "oracle": {"approved_by": oracle.get("approved_by"), "approved_at": oracle.get("approved_at"),
                      "approval_id": oracle.get("approval_id"), "ok": bool(oracle.get("ok"))},
           "cases": cases,
           "totals": {"pass": sum(c["status"] == "pass" for c in cases.values()), "fail": sum(c["status"] != "pass" for c in cases.values())}}
    out.write_text(json.dumps(rec, ensure_ascii=False, indent=1), encoding="utf-8")
    prune(app)
    return out


def keep_count() -> int:
    """산출물 사본을 남길 최근 실행 수 (EASTSHIFT_KEEP_RUNS, 기본 30. 0이면 정리하지 않는다)."""
    try:
        return int(os.environ.get("EASTSHIFT_KEEP_RUNS", "30"))
    except ValueError:
        return 30


def prune(app: str, keep: int | None = None) -> list[Path]:
    """오래된 실행의 사본 폴더(JUnit·스크린샷)를 지운다. 원장 JSON은 그대로라 이력·상태·변화는 남고, 그 실행의 지도·보고서만 다시 그릴 수 없다.
    수정 → 재실행 루프에서는 실행마다 실패 스크린샷 수백 장이 쌓이므로 상한이 없으면 runs/ 가 몇 GB가 된다."""
    keep = keep_count() if keep is None else keep
    d = run_dir(app)
    if keep <= 0 or not d.exists():
        return []
    stamps = [p.stem for p in _ordered(d.glob("*.json"))]
    removed = []
    for stem in stamps[:-keep] if len(stamps) > keep else []:
        folder = d / stem
        if folder.is_dir():
            shutil.rmtree(folder, ignore_errors=True)
            removed.append(folder)
    return removed


def save_mutation(app: str, result: Path) -> Path:
    """eastshift mutate 결과를 원장 옆에 복사한다. 보고서는 현재 승인본과 승인 시각이 같은 최신 결과를 쓴다."""
    d = run_dir(app) / "mutations"
    d.mkdir(parents=True, exist_ok=True)
    dst = _claim(d)
    shutil.copyfile(result, dst)
    return dst


def load_runs(app: str) -> list[dict[str, Any]]:
    d = run_dir(app)
    if not d.exists():
        return []
    runs = []
    for p in _ordered(d.glob("*.json")):
        try:
            runs.append({**fscache.json_load(p), "_file": p.name, "_stamp": p.stem})
        except (OSError, ValueError):
            continue
    return runs


def load_mutations(app: str) -> list[dict[str, Any]]:
    """오래된 것부터. 각 항목에 _file 이 붙는다."""
    d = run_dir(app) / "mutations"
    if not d.exists():
        return []
    out = []
    for p in _ordered(d.glob("*.json")):
        try:
            out.append({**fscache.json_load(p), "_file": str(p)})
        except (OSError, ValueError):
            continue
    return out


def mutation_for(app: str, approved_at: str | None, approval_id: str | None = None) -> Path | None:
    """현재 승인본으로 측정한 최신 결함 주입 결과. 없으면 None (보고서는 ❌로 표시한다)."""
    for m in reversed(load_mutations(app)):
        if (m.get("mode") == "expects+golden" and m.get("oracle_approved_at") == approved_at
                and (approval_id is None or m.get("oracle_approval_id") == approval_id)):
            return Path(m["_file"])
    return None


def history(app: str) -> dict[str, list[dict[str, Any]]]:
    """테스트별 실행 이력 (오래된 것부터): [{run, target, status, kind, approved_at}]."""
    out: dict[str, list[dict[str, Any]]] = {}
    for r in load_runs(app):
        for name, c in r["cases"].items():
            out.setdefault(name, []).append({"run": r["_stamp"], "finished": r["finished"], "target": r["target"],
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
    stale = (latest["oracle"].get("approval_id") != oracle_status.get("approval_id")
             if oracle_status.get("approval_id") else
             bool(oracle_status.get("approved_at") and latest["oracle"].get("approved_at") != oracle_status.get("approved_at")))
    if stale:
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
        L.append("  모두 통과. 검증 보고서를 만들 차례입니다 (eastshift report …).")
    return "\n".join(L)
