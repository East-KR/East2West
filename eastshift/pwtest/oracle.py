"""오라클 디렉터리 (golden/<app>/): 기대값의 원천. 사람이 승인하고, 승인 후 바뀌면 비교를 거부한다.

golden/<app>/
  <test>.json            as-is 관찰값 (--record) + 그 테스트가 단언한 기대값 목록
  oracle.json            {"ignore": [정규식…], "equivalent_mutants": [{path, op, context, reason}…]}
                         비교 전 마스킹 규칙 (주문번호, 날짜 등), 결함 주입에서 관찰 가능한 차이가 없다고 사람이 판정한 결함
  name_map.<target>.json {"as-is 이름": "to-be 이름"}  의도된 라벨 변경
  APPROVED.json          위 파일들의 sha256, 승인자, 시각. 통합 화면(eastshift ui)의 시나리오 승인 탭에서 사람이 만든다

기록(--record) 뒤 파일 해시가 바뀌면 웹에서 다시 승인해야 비교가 돈다.
"""
from __future__ import annotations

import hashlib
import json
import re
import time
from pathlib import Path
from typing import Any

MANIFEST = "APPROVED.json"
CONFIG = "oracle.json"


def oracle_files(d: Path) -> dict[str, str]:
    """승인 대상 전부: 골든 JSON, 규칙, 이름 매핑, 그리고 사람이 보고 승인한 단계별 화면(shots/)."""
    return {p.relative_to(d).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in sorted(d.rglob("*")) if p.is_file() and p.name not in (MANIFEST, ".DS_Store")}


def load_config(d: Path) -> dict[str, Any]:
    p = d / CONFIG
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else {"ignore": []}


def status(d: Path) -> dict[str, Any]:
    """승인 상태. ok=False면 problems에 이유."""
    m = d / MANIFEST
    if not m.exists():
        return {"ok": False, "problems": [f"{d} has never been approved (a person approves in `eastshift ui`, 시나리오 승인 tab)"]}
    man = json.loads(m.read_text(encoding="utf-8"))
    now, then = oracle_files(d), man["files"]
    problems = [f"changed since approval: {n}" for n in sorted(now) if n in then and now[n] != then[n]]
    problems += [f"added since approval: {n}" for n in sorted(set(now) - set(then))]
    problems += [f"removed since approval: {n}" for n in sorted(set(then) - set(now))]
    return {"ok": not problems, "problems": problems, "approved_by": man.get("approved_by"), "approved_at": man.get("approved_at"),
            "approval_id": hashlib.sha256(m.read_bytes()).hexdigest(),
            "note": man.get("note", "")}


def _golden_files(d: Path) -> list[Path]:
    return [g for g in sorted(d.glob("*.json")) if g.name not in (MANIFEST, CONFIG) and not g.name.startswith("name_map")]


def mask_hits(d: Path) -> list[dict[str, Any]]:
    """ignore 규칙마다 골든에서 실제로 가린 문자열과 횟수. 넓은 규칙이 금액 같은 진짜 값을 가리는지 사람이 본다."""
    from eastshift.observe import flatten
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


def fingerprint(d: Path) -> str:
    """승인 대상 전체의 짧은 지문. 검토 화면이 만들어질 때와 승인 순간이 같은 기준인지 대조한다."""
    return hashlib.sha256(json.dumps(sorted(oracle_files(d).items())).encode()).hexdigest()[:12]


def approval_id(d: Path) -> str | None:
    """Exact approved manifest content used by a run, independent of its timestamp."""
    p = d / MANIFEST
    return hashlib.sha256(p.read_bytes()).hexdigest() if p.exists() else None


def stamp(d: Path, by: str, note: str, reviewed: dict[str, str]) -> dict[str, Any]:
    """승인 도장: 검토한 파일 해시와 확인한 기대값을 APPROVED.json에 쓴다."""
    rec = {"approved_by": by, "approved_at": time.strftime("%Y-%m-%d %H:%M:%S"), "note": note,
           "files": reviewed, "assertions": {t["name"]: t["assertions"] for t in tests(d)}}
    (d / MANIFEST).write_text(json.dumps(rec, ensure_ascii=False, indent=1), encoding="utf-8")
    return rec


def approve_from_review(d: Path, by: str, note: str, reviewed_fingerprint: str) -> dict[str, Any]:
    """웹 승인 (eastshift ui). 검토 화면에서 이름을 입력하고 승인할 때 부른다.
    검토 화면을 만들 때의 지문과 지금 지문이 다르면 (검토 중 기준이 바뀜) 승인하지 않는다."""
    by = by.strip()
    if not by:
        raise ValueError("승인자 이름이 없습니다")
    if fingerprint(d) != reviewed_fingerprint:
        raise ValueError("검토하는 동안 기준 파일이 바뀌었습니다. 검토 화면을 새로 열어 바뀐 내용을 다시 보세요")
    return stamp(d, by, note, oracle_files(d))
