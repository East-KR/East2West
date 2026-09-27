"""재승인 diff: 지난 승인본과 지금 기록을 단계별로 비교해 검토자가 바뀐 곳만 보게 한다.

지난 승인본의 내용은 APPROVED.json 에 해시로만 남는다. 그래서 내용은 두 곳에서 찾고, 매니페스트 해시와 맞는 것만 믿는다.
  1. runs/<app>/approved/<test>.json   승인할 때 남기는 사본 (oracle.approve_from_review)
  2. git 이력                           사본이 없던 옛 승인본. 그 파일의 커밋들 중 해시가 맞는 판
둘 다 없거나 해시가 안 맞으면 그 시나리오는 비교하지 않는다 (틀린 기준으로 "안 바뀜"이라 말하지 않는다).

비교는 to-be 비교(observe.compare)와 같은 정규화를 쓴다: 스냅샷을 flatten 하고 가림 규칙을 적용한 뒤 줄 단위로 본다.
기록 시각·주소·캡처 바이트는 다시 기록하면 늘 바뀌는 잡음이라 보지 않는다.
"""
from __future__ import annotations

import hashlib
import json
import subprocess
from collections import Counter
from pathlib import Path
from typing import Any

from . import ledger, oracle

MAX_COMMITS = 50


def copy_dir(app: str) -> Path:
    return ledger.run_dir(app) / "approved"


def keep_copy(d: Path) -> None:
    """승인 직후 골든 JSON 사본을 원장 옆에 둔다. 다음 재승인 때 비교 기준이 된다 (캡처는 잡음이라 두지 않는다)."""
    out = copy_dir(d.name)
    out.mkdir(parents=True, exist_ok=True)
    for old in out.glob("*.json"):
        old.unlink()
    for g in oracle._golden_files(d):
        (out / g.name).write_bytes(g.read_bytes())


def _git(d: Path, *args: str) -> bytes | None:
    try:
        r = subprocess.run(["git", "-C", str(d), *args], capture_output=True, timeout=10)
    except (OSError, subprocess.TimeoutExpired):
        return None
    return r.stdout if r.returncode == 0 else None


def _from_git(d: Path, name: str, want: str) -> bytes | None:
    prefix = _git(d, "rev-parse", "--show-prefix")
    if prefix is None:
        return None
    path = prefix.decode().strip() + name
    commits = (_git(d, "log", f"-{MAX_COMMITS}", "--format=%H", "--", name) or b"").decode().split()
    for c in commits:
        body = _git(d, "show", f"{c}:{path}")
        if body is not None and hashlib.sha256(body).hexdigest() == want:
            return body
    return None


def approved_goldens(d: Path) -> dict[str, dict[str, Any]]:
    """지난 승인 때의 골든 내용 {test: data}. 해시가 맞는 것만. 승인된 적이 없으면 빈 dict."""
    m = d / oracle.MANIFEST
    if not m.exists():
        return {}
    files = json.loads(m.read_text(encoding="utf-8")).get("files", {})
    out: dict[str, dict[str, Any]] = {}
    for name, want in files.items():
        if "/" in name or not name.endswith(".json") or name == oracle.CONFIG or name.startswith("name_map"):
            continue
        copy = copy_dir(d.name) / name
        body = copy.read_bytes() if copy.exists() else None
        if body is None or hashlib.sha256(body).hexdigest() != want:
            body = _from_git(d, name, want)
        if body is not None:
            out[name[:-len(".json")]] = json.loads(body)
    return out


def _lines(step: dict[str, Any], rules: list[str]) -> Counter:
    from ..observe import flatten, mask
    raw = flatten(step["snapshot"], keep_urls=False) if "snapshot" in step else step.get("content", [])
    return Counter(mask(raw, rules))


def _dialogs(step: dict[str, Any], rules: list[str]) -> list[str]:
    from ..observe import mask
    return [f"{d['type']}: {mask([d['message']], rules)[0]} → {d['action']}" for d in step.get("dialogs", [])]


def _api(step: dict[str, Any]) -> dict[str, Any]:
    return {f"{a.get('method')} {a.get('path')}": (a.get("status"), a.get("body")) for a in step.get("api", [])}


def diff(old: dict[str, Any], new: dict[str, Any], rules: list[str]) -> list[dict[str, Any]]:
    """단계별 의미 차이. 바뀐 단계만 {index, action?, added, removed, dialogs?, api?, gone?, new?} 로 돌려준다."""
    out: list[dict[str, Any]] = []
    os_, ns = old.get("steps", []), new.get("steps", [])
    for i in range(max(len(os_), len(ns))):
        a = os_[i] if i < len(os_) else None
        b = ns[i] if i < len(ns) else None
        if a is None:
            out.append({"index": i, "new": True, "action": [None, [b["kind"], b["text"]]], "added": [], "removed": []})
            continue
        if b is None:
            out.append({"index": i, "gone": True, "action": [[a["kind"], a["text"]], None], "added": [], "removed": []})
            continue
        ch: dict[str, Any] = {"index": i}
        if (a["kind"], a["text"]) != (b["kind"], b["text"]):
            ch["action"] = [[a["kind"], a["text"]], [b["kind"], b["text"]]]
        la, lb = _lines(a, rules), _lines(b, rules)
        ch["added"], ch["removed"] = list((lb - la).elements()), list((la - lb).elements())
        da, db = _dialogs(a, rules), _dialogs(b, rules)
        if da != db:
            ch["dialogs"] = [da, db]
        aa, ab = _api(a), _api(b)
        if aa != ab:
            ch["api"] = sorted(k for k in set(aa) | set(ab) if aa.get(k) != ab.get(k))
        if len(ch) > 3 or ch["added"] or ch["removed"]:
            out.append(ch)
    return out
