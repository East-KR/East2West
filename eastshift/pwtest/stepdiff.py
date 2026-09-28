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

from . import fscache, ledger, oracle

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
    """파일 하나의 이력에서 해시가 맞는 판 (마지막 MAX_COMMITS개). 폴더 단위 일괄 조회(_from_git_many)가 못 찾은 것만 온다."""
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


def _from_git_many(d: Path, wanted: dict[str, str]) -> dict[str, bytes]:
    """여러 파일의 승인본을 git 이력에서 한꺼번에 찾는다: 폴더를 건드린 최근 커밋들 × 파일을 cat-file --batch-check 한 번으로 물어
    판(blob)마다 한 번씩만 내용을 받아 해시를 맞춘다. 파일마다 git을 세 번씩 부르던 것을 시나리오 수와 무관하게 서너 번으로.
    폴더 이력이 MAX_COMMITS보다 길어 잘렸으면, 못 찾은 파일은 파일별 이력(_from_git)으로 더 본다."""
    if not wanted:
        return {}
    prefix = _git(d, "rev-parse", "--show-prefix")
    if prefix is None:
        return {}
    pre = prefix.decode().strip()
    commits = (_git(d, "log", f"-{MAX_COMMITS}", "--format=%H", "--", ".") or b"").decode().split()
    found: dict[str, bytes] = {}
    if commits:
        names = sorted(wanted)
        queries = [f"{c}:{pre}{n}" for c in commits for n in names]
        checked = _git_batch(d, "--batch-check=%(objectname) %(objecttype)", queries) or b""
        blobs: dict[str, list[str]] = {n: [] for n in names}  # 파일 → 최근 순 판(blob id), 중복 없이
        for q, line in zip(queries, checked.decode(errors="replace").splitlines()):
            parts = line.split()
            if len(parts) >= 2 and parts[1] == "blob":
                n = q.split(":", 1)[1][len(pre):]
                if parts[0] not in blobs[n]:
                    blobs[n].append(parts[0])
        ids = sorted({b for bs in blobs.values() for b in bs})
        contents = _git_batch_blobs(d, ids)
        for n in names:
            for b in blobs[n]:
                body = contents.get(b)
                if body is not None and hashlib.sha256(body).hexdigest() == wanted[n]:
                    found[n] = body
                    break
    if len(commits) >= MAX_COMMITS:
        for n in sorted(set(wanted) - set(found)):
            body = _from_git(d, n, wanted[n])
            if body is not None:
                found[n] = body
    return found


def _git_batch(d: Path, mode: str, lines: list[str]) -> bytes | None:
    try:
        r = subprocess.run(["git", "-C", str(d), "cat-file", mode], input="\n".join(lines).encode() + b"\n",
                           capture_output=True, timeout=120)
    except (OSError, subprocess.TimeoutExpired):
        return None
    return r.stdout if r.returncode == 0 else None


def _git_batch_blobs(d: Path, ids: list[str]) -> dict[str, bytes]:
    """blob id → 내용 (cat-file --batch 한 번)."""
    out: dict[str, bytes] = {}
    raw = _git_batch(d, "--batch", ids) if ids else None
    if not raw:
        return out
    pos = 0
    while pos < len(raw):
        nl = raw.index(b"\n", pos)
        head = raw[pos:nl].split()
        pos = nl + 1
        if len(head) < 3:  # "<id> missing"
            continue
        size = int(head[2])
        out[head[0].decode()] = raw[pos:pos + size]
        pos += size + 1  # 내용 뒤 개행
    return out


def approved_goldens(d: Path) -> dict[str, dict[str, Any]]:
    """지난 승인 때의 골든 내용 {test: data}. 해시가 맞는 것만. 승인된 적이 없으면 빈 dict.
    찾는 순서: 지금 파일이 승인 때와 같으면 그것(재승인은 대개 몇 개만 바뀐다) → runs/<app>/approved/ 사본 → git 이력(일괄)."""
    m = d / oracle.MANIFEST
    if not m.exists():
        return {}
    files = json.loads(m.read_text(encoding="utf-8")).get("files", {})
    out: dict[str, dict[str, Any]] = {}
    missing: dict[str, str] = {}
    for name, want in files.items():
        if "/" in name or not name.endswith(".json") or name == oracle.CONFIG or name.startswith("name_map"):
            continue
        body = None
        for cand in (d / name, copy_dir(d.name) / name):
            if cand.exists() and fscache.sha256(cand) == want:
                body = cand.read_bytes()
                break
        if body is None:
            missing[name] = want
        else:
            out[name[:-len(".json")]] = json.loads(body)
    for name, body in _from_git_many(d, missing).items():
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
