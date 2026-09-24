"""오라클 디렉터리 (golden/<app>/): 기대값의 원천. 사람이 승인하고, 승인 후 바뀌면 비교를 거부한다.

golden/<app>/
  <test>.json            as-is 관찰값 (--record) + 그 테스트가 단언한 기대값 목록
  oracle.json            {"ignore": [정규식…], "equivalent_mutants": [{path, op, context, reason}…]}
                         비교 전 마스킹 규칙 (주문번호, 날짜 등), 결함 주입에서 관찰 가능한 차이가 없다고 사람이 판정한 결함
  name_map.<target>.json {"as-is 이름": "to-be 이름"}  의도된 라벨 변경
  APPROVED.json          위 파일들의 sha256, 승인자, 시각. `jev-e2e approve`로만 만든다 (터미널 필요)

에이전트는 기록(--record)까지 할 수 있지만, 기록하면 해시가 바뀌어 사람이 다시 승인해야 비교가 돈다.
"""
from __future__ import annotations

import hashlib
import json
import re
import sys
import time
from pathlib import Path
from typing import Any

MANIFEST = "APPROVED.json"
CONFIG = "oracle.json"


def oracle_files(d: Path) -> dict[str, str]:
    """승인 대상 전부: 골든 JSON, 규칙, 이름 매핑, 그리고 사람이 보고 승인한 단계별 화면(shots/)."""
    return {p.relative_to(d).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in sorted(d.rglob("*")) if p.is_file() and p.name != MANIFEST}


def load_config(d: Path) -> dict[str, Any]:
    p = d / CONFIG
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else {"ignore": []}


def status(d: Path) -> dict[str, Any]:
    """승인 상태. ok=False면 problems에 이유."""
    m = d / MANIFEST
    if not m.exists():
        return {"ok": False, "problems": [f"{d} has never been approved (run `jev-e2e approve {d}` in a terminal)"]}
    man = json.loads(m.read_text(encoding="utf-8"))
    now, then = oracle_files(d), man["files"]
    problems = [f"changed since approval: {n}" for n in sorted(now) if n in then and now[n] != then[n]]
    problems += [f"added since approval: {n}" for n in sorted(set(now) - set(then))]
    problems += [f"removed since approval: {n}" for n in sorted(set(then) - set(now))]
    return {"ok": not problems, "problems": problems, "approved_by": man.get("approved_by"), "approved_at": man.get("approved_at"),
            "note": man.get("note", "")}


def _golden_files(d: Path) -> list[Path]:
    return [g for g in sorted(d.glob("*.json")) if g.name not in (MANIFEST, CONFIG) and not g.name.startswith("name_map")]


def mask_hits(d: Path) -> list[dict[str, Any]]:
    """ignore 규칙마다 골든에서 실제로 가린 문자열과 횟수. 넓은 규칙이 금액 같은 진짜 값을 가리는지 사람이 본다."""
    from jev_e2e.observe import flatten
    out = []
    for rule in load_config(d).get("ignore", []):
        rx = re.compile(rule)
        hits: dict[str, int] = {}
        for g in _golden_files(d):
            for step in json.loads(g.read_text(encoding="utf-8")).get("steps", []):
                for line in flatten(step.get("snapshot", ""), keep_urls=False):
                    for m in rx.finditer(line):
                        hits[m.group(0)] = hits.get(m.group(0), 0) + 1
        out.append({"rule": rule, "total": sum(hits.values()), "samples": sorted(hits.items(), key=lambda kv: -kv[1])[:8]})
    return out


def mask_audit(d: Path) -> list[str]:
    return [f"  {h['rule']!r}: masks {h['total']} occurrences"
            + (" — " + ", ".join(f"{k!r}×{v}" for k, v in h["samples"]) if h["samples"] else " (matches nothing)")
            for h in mask_hits(d)]


def tests(d: Path) -> list[dict[str, Any]]:
    out = []
    for g in _golden_files(d):
        data = json.loads(g.read_text(encoding="utf-8"))
        out.append({"name": g.stem, "steps": len(data.get("steps", [])), "base_url": data.get("base_url"),
                    "recorded_at": data.get("recorded_at"), "assertions": data.get("assertions", [])})
    return out


def name_maps(d: Path) -> dict[str, dict[str, str]]:
    return {g.stem.removeprefix("name_map.").removeprefix("name_map"): json.loads(g.read_text(encoding="utf-8"))
            for g in sorted(d.glob("name_map*.json"))}


def approved_assertions(d: Path) -> dict[str, list[dict[str, Any]]] | None:
    """지난 승인 때의 테스트별 기대값 (승인 화면에서 바뀐 기대값을 보여 주려고). 없으면 None."""
    m = d / MANIFEST
    return json.loads(m.read_text(encoding="utf-8")).get("assertions") if m.exists() else None


def summary(d: Path) -> list[str]:
    lines = [f"  name_map.{t}.json: " + ", ".join(f"{k!r} → {v!r}" for k, v in m.items()) for t, m in name_maps(d).items()]
    for t in tests(d):
        lines.append(f"  {t['name']}.json: {t['steps']} observed steps, recorded on {t['base_url']} at {t['recorded_at'] or '?'}")
        lines += [f"      expects {a['kind']} {a['target']!r} = {a['value']!r}" for a in t["assertions"]]
    return lines


def approve(d: Path, by: str, note: str = "", tests_dir: Path | None = None) -> None:
    if not sys.stdin.isatty():
        sys.exit("approve needs an interactive terminal: a person reviews and types the confirmation (agents cannot approve).")
    from . import html
    page = html.write_review(d, tests_dir=tests_dir)
    st = status(d)
    ts = tests(d)
    print(f"\n기준 승인: {d}")
    print(f"  테스트 {len(ts)}개 · 기대값 {sum(len(t['assertions']) for t in ts)}개 · 가림 규칙 {len(load_config(d).get('ignore', []))}개"
          f" · 이름 매핑 {sum(len(m) for m in name_maps(d).values())}개 · 동등 결함 {len(load_config(d).get('equivalent_mutants', []))}개")
    if (d / MANIFEST).exists():
        print("  지난 승인 이후 변경: " + (", ".join(p.split(": ", 1)[-1] for p in st["problems"]) or "없음"))
    print(f"\n  검토 화면: {page.resolve().as_uri()}")
    html.open_in_browser(page)
    answer = input(f"\n브라우저에서 검토했으면, 승인하려면 '{d.name}'을(를) 입력하세요: ")
    if answer.strip() != d.name:
        sys.exit("승인하지 않았습니다")
    (d / MANIFEST).write_text(json.dumps({"approved_by": by, "approved_at": time.strftime("%Y-%m-%d %H:%M:%S"), "note": note,
                                          "files": oracle_files(d), "assertions": {t["name"]: t["assertions"] for t in ts}},
                                         ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"승인됨: {by} · {time.strftime('%Y-%m-%d %H:%M:%S')}")
