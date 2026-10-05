"""오라클 디렉터리 (golden/<app>/): 기대값의 원천. 승인 도장(APPROVED.json)이 실행마다 어느 기준으로 돌렸는지를 묶는다.

승인은 기록·비교·결함 탐지가 자동으로 한다 (auto_approve. 2026-10-05 사용자 결정: 사람이 일일이 승인하는 것이 막힘).
기록(--record)이 끝날 때, 비교(--compare)·결함 탐지(east2west mutate)가 시작할 때 골든이 승인본과 다르면 도장을 다시 찍는다.
승인자는 사람 이름이 아니라 AUTO_BY, 메모에 무엇이 바뀌었는지(추가·바뀜·지움 파일 수). 같으면 찍지 않는다.

golden/<app>/
  <test>.json            as-is 관찰값 (--record) + 그 테스트가 단언한 기대값 목록
  oracle.json            {"ignore": [정규식…], "equivalent_mutants": [{path, op, context, reason}…], "difference_rules": [{class, reason, …}]}
                         비교 전 마스킹 규칙 (주문번호, 날짜 등), 결함 주입에서 관찰 가능한 차이가 없다고 사람이 판정한 결함,
                         결함 아닌 차이의 갈래(자료·환경, 보류, 도구 한계, 비교 불가, 고객 결정, 같은 뜻 — rules.py). 틀린 규칙이 있으면 비교를 거부한다 (config_problems)
  name_map.<target>.json {"as-is 이름": "to-be 이름"}  의도된 라벨 변경
  APPROVED.json          위 파일들의 sha256, 승인자, 시각. auto_approve 가 쓴다 (에이전트가 손으로 쓰지 않는다: tools/guard_oracle.py)
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import time
from pathlib import Path
from typing import Any

from . import fscache, gitblobs

MANIFEST = "APPROVED.json"
CONFIG = "oracle.json"


def oracle_files(d: Path) -> dict[str, str]:
    """승인 대상 전부: 골든 JSON, 규칙, 이름 매핑, 그리고 사람이 보고 승인한 단계별 화면(shots/).
    git이 색인과 같다고 보는 파일은 blob id로 기억한 sha256을 쓰고, 나머지(바뀐 것, git에 없는 것)만 직접 해시한다 (gitblobs)."""
    files = [r for r in _walk(d) if r.rsplit("/", 1)[-1] not in (MANIFEST, ".DS_Store")]
    known = gitblobs.clean_sha256(d, files)
    return {r: known.get(r) or fscache.sha256(d / r) for r in files}


def _walk(d: Path) -> list[str]:
    """d 아래 파일의 d 기준 경로 ('/'로 구분). sorted(d.rglob("*"))와 같은 순서(경로 조각 순)지만 Path를 만들지 않아 캡처 수만 장에도 빠르다.
    폴더 심볼릭 링크는 따라가지 않는다."""
    out: list[str] = []

    def walk(path: str, rel: str) -> None:
        try:
            with os.scandir(path) as it:
                entries = list(it)
        except (FileNotFoundError, NotADirectoryError):
            return
        for e in entries:
            if e.is_dir(follow_symlinks=False):
                walk(e.path, f"{rel}{e.name}/")
            elif e.is_file():
                out.append(rel + e.name)

    walk(str(d), "")
    return sorted(out, key=lambda r: r.split("/"))


def load_config(d: Path) -> dict[str, Any]:
    p = d / CONFIG
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else {"ignore": []}


def status(d: Path) -> dict[str, Any]:
    """승인 상태. ok=False면 problems에 이유."""
    m = d / MANIFEST
    if not m.exists():
        return {"ok": False, "problems": [f"{d} has never been approved (the next record/compare/mutate approves it automatically)"]}
    man = json.loads(m.read_text(encoding="utf-8"))
    now, then = oracle_files(d), man["files"]
    problems = [f"changed since approval: {n}" for n in sorted(now) if n in then and now[n] != then[n]]
    problems += [f"added since approval: {n}" for n in sorted(set(now) - set(then))]
    problems += [f"removed since approval: {n}" for n in sorted(set(then) - set(now))]
    return {"ok": not problems, "problems": problems, "approved_by": man.get("approved_by"), "approved_at": man.get("approved_at"),
            "approval_id": hashlib.sha256(m.read_bytes()).hexdigest(),
            "note": man.get("note", "")}


def recorded_setup(d: Path) -> dict[str, Any]:
    """골든을 기록할 때의 공통 설정 (초기화 경로, 고정 시각). 테스트마다 다르면 ValueError: 비교가 기록과 같은 조건으로 돌 수 없다."""
    setups = {json.dumps(fscache.json_load(p).get("setup", {}), sort_keys=True) for p in _golden_files(d)}
    if len(setups) > 1:
        raise ValueError("golden tests have different setup settings; record them with one common reset path and fixed time")
    return json.loads(next(iter(setups))) if setups else {}


PRECHECK = "EAST2WEST_ORACLE_PRECHECK"  # (내부) east2west mutate가 한 번 확인한 결과(precheck)를 적은 파일. 결함마다 띄우는 pytest가 읽는다


def precheck(d: Path) -> dict[str, Any]:
    """비교 전 확인 한 번: 승인 상태와 기록 설정. pytest 컨트롤러·east2west mutate가 구해 xdist 워커·결함마다 띄우는 pytest에 넘긴다
    (골든 전체를 프로세스마다 다시 해시하지 않게)."""
    try:
        setup, error = recorded_setup(d), ""
    except ValueError as e:
        setup, error = {}, str(e)
    return {"dir": str(d.resolve()), "status": status(d), "setup": setup, "setup_error": error,
            "config_error": "\n".join(config_problems(d))}


def config_problems(d: Path) -> list[str]:
    """oracle.json 의 틀린 규칙 (지금은 difference_rules: 모르는 갈래, 빈 사유, 틀린 정규식, 범위 없음). 있으면 비교를 거부한다.
    load_config 는 그대로 읽어 준다: 통합 화면이 틀린 파일도 사람에게 보여 줘야 하므로."""
    from . import rules
    try:
        cfg = load_config(d)
    except ValueError as e:
        return [f"{CONFIG}: JSON 을 읽지 못했습니다 ({e})"]
    return [f"{CONFIG} {p}" for p in rules.problems(cfg.get("difference_rules"))]


def given_precheck(d: Path, given: dict[str, Any] | None) -> dict[str, Any] | None:
    """넘겨받은 확인 결과가 이 오라클 폴더·지금 승인본(APPROVED.json 내용)의 것일 때만 쓴다. 아니면 None (직접 구한다)."""
    if not given or given.get("dir") != str(d.resolve()) or given.get("status", {}).get("approval_id") != approval_id(d):
        return None
    return given


def _golden_files(d: Path) -> list[Path]:
    return [g for g in sorted(d.glob("*.json")) if g.name not in (MANIFEST, CONFIG) and not g.name.startswith("name_map")]


def mask_hits(d: Path) -> list[dict[str, Any]]:
    """ignore 규칙마다 골든에서 실제로 가린 문자열과 횟수. 넓은 규칙이 금액 같은 진짜 값을 가리는지 사람이 본다."""
    from east2west.observe import flatten
    out = []
    for rule in load_config(d).get("ignore", []):
        rx = re.compile(rule)
        hits: dict[str, int] = {}
        for g in _golden_files(d):
            for step in fscache.json_load(g).get("steps", []):
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
        data = fscache.json_load(g)
        out.append({"name": g.stem, "steps": len(data.get("steps", [])), "base_url": data.get("base_url"),
                    "recorded_at": data.get("recorded_at"), "assertions": data.get("assertions", [])})
    return out


def name_maps(d: Path) -> dict[str, dict[str, str]]:
    return {g.stem.removeprefix("name_map.").removeprefix("name_map"): json.loads(g.read_text(encoding="utf-8"))
            for g in sorted(d.glob("name_map*.json"))}


def summary(d: Path) -> list[str]:
    lines = [f"  name_map.{t}.json: " + ", ".join(f"{k!r} → {v!r}" for k, v in m.items()) for t, m in name_maps(d).items()]
    for t in tests(d):
        lines.append(f"  {t['name']}.json: {t['steps']} observed steps, recorded on {t['base_url']} at {t['recorded_at'] or '?'}")
        lines += [f"      expects {a['kind']} {a['target']!r} = {a['value']!r}" for a in t["assertions"]]
    return lines


def fingerprint(d: Path) -> str:
    """승인 대상 전체의 짧은 지문 (APPROVED.json 제외). 다시 기록이 골든을 바꿨는지 본다 (hub.start_record)."""
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


AUTO_BY = "자동 승인"  # 사람이 본 승인과 구별되게 사람 이름을 쓰지 않는다


def auto_approve(d: Path, note: str = "") -> str:
    """골든이 승인본과 다르면 승인 도장을 찍는다. 같거나 골든 폴더가 없으면 찍지 않는다
    (승인본이 바뀌면 앞선 원장이 '다른 승인본으로 실행됨'이 된다). 돌려주는 것: 찍었으면 메모, 아니면 ""."""
    if not d.is_dir():
        return ""
    st = status(d)
    if st["ok"]:
        return ""
    n = lambda w: sum(p.startswith(w) for p in st["problems"])
    what = f'추가 {n("added")} · 바뀜 {n("changed")} · 지움 {n("removed")} (파일)' if (d / MANIFEST).exists() else "처음 승인"
    memo = " · ".join(x for x in (what, note) if x)
    stamp(d, AUTO_BY, memo, oracle_files(d))
    return memo
