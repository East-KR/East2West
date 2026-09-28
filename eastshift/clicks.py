"""탐색(crawl)이 누른 결과의 기억: 한 번 누른 동작은 다음 탐색에서 브라우저를 열지 않고 기억한 결과를 쓴다.

동작 하나 = 시작 주소부터 재생한 스텝들(내용) + 누른 스텝 + 방식(그대로·채워서·취소). 이것이 같고 앱이 같으면 결과(도착 화면 관찰,
대화상자, 오류, 캡처)도 같다. 탐색 알고리즘은 처음부터 그대로 돌고 누르기만 기억에서 꺼내므로, 상한(깊이, 상태 수, 화면당 동작 수)을
올려 다시 탐색하면 새로 생긴 동작만 실제로 누르고 결과는 큰 상한으로 한 번에 돌린 것과 같다.

<cache-dir>/clicks.jsonl      첫 줄 = 기준 {"key": …}. 이후 한 줄에 결과 하나 {"k": 동작 해시, "r": 결과}, 스냅샷은 같은 것을 한 번만 {"s": 해시, "t": 원문}
<cache-dir>/clicks/<sha256>.png   결과의 캡처

버리는 때 (파일을 비우고 새로 시작한다)
- 기준이 다르다: 소스 버전, 화면 대기(settle), 동작 제한 시간, 로그인 상태 파일.
  소스 버전 = 시작 주소가 등록부(eastshift.json)의 as-is/to-be 주소와 같은 origin이면 그 소스 폴더의 커밋된 내용(git 트리 해시) + 커밋 안 된 변경
  (개발 서버는 작업 폴더를 그대로 띄우므로). to-be는 개발 중이라 커밋이 바뀌면 버린다. 소스 위치를 모르면 이 검사는 못 한다.
- 시작 화면의 구조 서명이 지난번과 다르다 (시작 화면은 늘 실제로 연다).
오류로 끝난 동작(시간 초과 등)은 다음 탐색에서 한 번 더 누르고, 두 번 연속 오류면 그 뒤로는 기억을 쓴다 (일시적인 느림은 다시 누르고,
늘 가려진 버튼에 탐색마다 제한 시간을 쓰지 않게). 새 상태가 될 결과인데 캡처가 없으면 다시 누른다.
기억은 앱의 데이터 상태까지는 모른다: 저장 버튼으로 데이터가 쌓이는 앱이면 탐색 전에 테스트 DB를 초기화하고, 처음부터 다시 누르려면 clicks.jsonl을 지운다.
"""
from __future__ import annotations

import hashlib
import json
import os
import subprocess
from dataclasses import asdict
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from .lists import ListInfo, Slot
from .snapshot import Element

VERSION = 1


def _h(obj: Any) -> str:
    return hashlib.sha256(json.dumps(obj, ensure_ascii=False, sort_keys=True, default=str).encode()).hexdigest()[:32]


class ClickMemo:
    def __init__(self, path: Path, key: dict[str, Any]):
        self.path = path
        self.shots = path.with_suffix("")
        self.header = json.dumps({"key": key}, ensure_ascii=False, sort_keys=True)
        self.index: dict[str, int] = {}   # 동작 해시 → 줄 위치
        self.snaps: dict[str, int] = {}   # 스냅샷 해시 → 줄 위치
        self.hits = self.misses = 0
        self.reason = ""                  # 비우고 시작했으면 그 이유
        path.parent.mkdir(parents=True, exist_ok=True)
        self._load()
        self.f = path.open("r+b")
        self.f.seek(0, os.SEEK_END)

    def _load(self) -> None:
        good = 0
        try:
            with self.path.open("rb") as f:
                first = f.readline()
                if first.rstrip(b"\n").decode("utf-8", "replace") != self.header:
                    self.reason = "기준이 바뀜 (소스·화면 대기·제한 시간·로그인 상태)" if first else ""
                    raise FileNotFoundError
                pos = good = len(first)
                for line in f:
                    if not line.endswith(b"}\n"):  # 쓰다 끊긴 마지막 줄
                        break
                    if line.startswith(b'{"k":"'):
                        self.index[line[6:38].decode()] = pos
                    elif line.startswith(b'{"s":"'):
                        self.snaps[line[6:38].decode()] = pos
                    pos = good = pos + len(line)
        except FileNotFoundError:
            self.reason = self.reason or "처음 탐색"
            self._reset()
            return
        with self.path.open("r+b") as f:
            f.truncate(good)

    def _reset(self) -> None:
        self.path.write_text(self.header + "\n", encoding="utf-8")
        self.index.clear()
        self.snaps.clear()
        if self.shots.is_dir():
            for p in self.shots.iterdir():
                p.unlink(missing_ok=True)

    def clear(self, reason: str) -> None:
        self.f.close()
        self._reset()
        self.reason = reason
        self.f = self.path.open("r+b")
        self.f.seek(0, os.SEEK_END)

    def close(self) -> None:
        self.f.close()

    def _read(self, pos: int) -> dict[str, Any]:
        self.f.seek(pos)
        rec = json.loads(self.f.readline())
        self.f.seek(0, os.SEEK_END)
        return rec

    def _write(self, line: str) -> int:
        pos = self.f.tell()
        self.f.write(line.encode("utf-8"))
        self.f.flush()
        return pos

    def get(self, key: Any) -> dict[str, Any] | None:
        pos = self.index.get(_h(key))
        if pos is None:
            return None
        r = self._unpack(self._read(pos)["r"])  # 누른 결과는 r["obs"]에, 화면 관찰(look)은 r 자체에 스냅샷이 있다
        if isinstance(r.get("obs"), dict):
            r["obs"] = self._unpack(r["obs"])
        return r

    def _pack(self, obs: dict[str, Any]) -> dict[str, Any]:
        """스냅샷 원문은 따로 한 번만 적고 해시로 가리킨다 (같은 화면에 가는 동작이 많다)."""
        if isinstance(obs.get("snapshot"), str):
            sh = _h(obs["snapshot"])
            if sh not in self.snaps:
                self.snaps[sh] = self._write(f'{{"s":"{sh}","t":{json.dumps(obs["snapshot"], ensure_ascii=False)}}}\n')
            obs = {**obs, "snapshot": {"$s": sh}}
        return obs

    def _unpack(self, obs: dict[str, Any]) -> dict[str, Any]:
        if isinstance(obs.get("snapshot"), dict):
            obs["snapshot"] = self._read(self.snaps[obs["snapshot"]["$s"]])["t"]
        return obs

    def put(self, key: Any, r: dict[str, Any]) -> None:
        r = self._pack(dict(r))
        if isinstance(r.get("obs"), dict):
            r["obs"] = self._pack(r["obs"])
        if isinstance(r.get("shot"), (bytes, bytearray)):
            sha = hashlib.sha256(r["shot"]).hexdigest()
            self.shots.mkdir(exist_ok=True)
            if not (self.shots / f"{sha}.png").exists():
                (self.shots / f"{sha}.png").write_bytes(r["shot"])
            r["shot"] = sha
        k = _h(key)
        self.index[k] = self._write(f'{{"k":"{k}","r":{json.dumps(r, ensure_ascii=False, sort_keys=True)}}}\n')

    def shot(self, sha: str) -> bytes | None:
        try:
            return (self.shots / f"{sha}.png").read_bytes()
        except OSError:
            return None


# -- 관찰(Crawler._observe)을 JSON으로 -----------------------------------------------------
def encode_obs(obs: dict[str, Any]) -> dict[str, Any]:
    return {**obs, "elements": [asdict(e) for e in obs["elements"]], "lists": [asdict(i) for i in obs["lists"]],
            "slots": {str(k): [s.list_id, s.row, s.col] for k, s in obs["slots"].items()}}


def decode_obs(d: dict[str, Any]) -> dict[str, Any]:
    return {**d, "elements": [Element(**e) for e in d["elements"]], "lists": [ListInfo(**i) for i in d["lists"]],
            "slots": {int(k): Slot(*v) for k, v in d["slots"].items()}}


# -- 소스 버전 ------------------------------------------------------------------------------
def sources_for(start_url: str, registry: Path) -> dict[str, str]:
    """시작 주소와 같은 origin인 등록부의 소스(폴더 또는 파일)들과 그 버전. 모르면 {}."""
    from .pwtest import projects
    origin = urlparse(start_url)[:2]
    out: dict[str, str] = {}
    for spec in projects.load(registry).values():
        for side in projects.SIDES:
            s = spec.get(side) or {}
            if s.get("url") and s.get("src") and urlparse(s["url"])[:2] == origin and Path(s["src"]).expanduser().exists():
                src = Path(s["src"]).expanduser()
                out[str(src)] = source_version(src)
    return out


def source_version(src: Path) -> str:
    """git이면 커밋된 그 소스의 내용(폴더는 트리 해시, 파일은 blob id: 저장소의 다른 곳 커밋에는 안 바뀐다) + 커밋 안 된 변경(바뀐·새 파일 내용).
    git이 아니면 파일 목록·크기·시각 (routes.SKIP_DIRS 제외)."""
    h = hashlib.sha256()
    cwd, target = (src, ".") if src.is_dir() else (src.parent, src.name)
    try:
        run = lambda *a: subprocess.run(["git", "-C", str(cwd), *a], capture_output=True, check=True, timeout=120,
                                        env={**os.environ, "GIT_OPTIONAL_LOCKS": "0"}).stdout
        top = Path(run("rev-parse", "--show-toplevel").decode().strip())
        h.update(run("rev-parse", f"HEAD:./{'' if target == '.' else target}"))
        for rec in run("status", "--porcelain=v1", "-z", "--no-renames", "--untracked-files=all", "--", target).split(b"\0"):
            if len(rec) > 3:
                h.update(rec + b"\0")
                f = top / os.fsdecode(rec[3:])
                if f.is_file():
                    h.update(hashlib.sha256(f.read_bytes()).digest())
        return "git:" + h.hexdigest()[:16]
    except (OSError, ValueError, subprocess.SubprocessError):
        pass
    from .routes import SKIP_DIRS
    if src.is_file():
        st = src.stat()
        return f"file:{st.st_size}:{st.st_mtime_ns}"
    for root, dirs, files in os.walk(src):
        dirs[:] = sorted(d for d in dirs if d not in SKIP_DIRS and not d.startswith("."))
        for name in sorted(files):
            st = os.stat(os.path.join(root, name))
            h.update(f"{os.path.relpath(os.path.join(root, name), src)}\0{st.st_size}\0{st.st_mtime_ns}\0".encode())
    return "files:" + h.hexdigest()[:16]
