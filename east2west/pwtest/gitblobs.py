"""git이 아는 파일의 sha256: 승인 상태(oracle.status)를 구할 때 골든 전체(캡처 수만 장)를 매번 읽지 않게 한다.

git은 파일마다 내용 해시(blob id)를 색인에 들고 있고, `git status`는 파일 시각·크기·ctime·inode로 '색인과 같은지'를 빠르게 안다
(시각이 애매하면 내용을 다시 읽어 확인한다). 그래서
  색인과 같은 파일  → blob id로 기억해 둔 sha256을 쓴다 (<git 공통 폴더>/east2west/blob-sha256, 모든 프로세스·작업 트리가 같이 쓴다)
  나머지            → 호출자가 직접 해시한다 (바뀐 것, git에 없는 것, 충돌 중인 것, 심볼릭 링크)
기억은 blob id(내용의 해시)가 열쇠라 낡지 않는다. 기억할 때는 읽은 바이트의 blob id를 다시 계산해 기대한 것과 같을 때만 적는다
(status 뒤에 파일이 바뀌어도 틀린 짝을 남기지 않는다).

쓰지 않는 경우 (전부 직접 해시, 전과 같다): 파일이 GIT_MIN_FILES개 미만, git이 없거나 저장소 밖, core.autocrlf 켜짐, 그리고
text·eol·filter·working-tree-encoding·ident 속성이 걸린 파일 (git이 변환하므로 작업 폴더의 바이트와 blob 내용이 다를 수 있다).
git 명령은 GIT_OPTIONAL_LOCKS=0으로 돌려 색인을 쓰지 않는다 (통합 화면이 자주 불러도 사용자의 git 작업과 잠금이 부딪히지 않게).
"""
from __future__ import annotations

import hashlib
import os
import subprocess
from pathlib import Path

GIT_MIN_FILES = 200  # 이보다 적으면 직접 해시가 git 명령 세 번보다 싸다
CONVERT_ATTRS = ("text", "eol", "filter", "working-tree-encoding", "ident")
STORE = "east2west/blob-sha256"


def _git(cwd: Path, *args: str, stdin: bytes | None = None) -> bytes:
    return subprocess.run(["git", "-C", str(cwd), *args], input=stdin, capture_output=True, check=True, timeout=120,
                          env={**os.environ, "GIT_OPTIONAL_LOCKS": "0"}).stdout


def _blob_id(data: bytes, algo_len: int) -> str:
    h = hashlib.sha256() if algo_len == 64 else hashlib.sha1()
    h.update(b"blob %d\0" % len(data))
    h.update(data)
    return h.hexdigest()


def _load(store: Path) -> dict[str, str]:
    out: dict[str, str] = {}
    try:
        text = store.read_text(encoding="ascii")
    except (OSError, UnicodeDecodeError):
        return out
    for line in text.splitlines():
        blob, _, sha = line.partition(" ")
        if len(sha) == 64:  # 쓰다 끊긴 줄은 건너뛴다
            out[blob] = sha
    return out


def clean_sha256(d: Path, files: list[str]) -> dict[str, str]:
    """files(d 기준 경로, '/' 구분) 중 git이 색인과 같다고 보는 파일의 sha256. 쓸 수 없으면 {} (호출자가 전부 직접 해시)."""
    if len(files) < GIT_MIN_FILES:
        return {}
    try:
        common, prefix = _git(d, "rev-parse", "--path-format=absolute", "--git-common-dir", "--show-prefix").decode().split("\n")[:2]
        if _git(d, "config", "--default", "false", "--get", "core.autocrlf").decode().strip().lower() in ("true", "input"):
            return {}
        index: dict[str, str] = {}  # d 기준 경로 → blob id
        for rec in _git(d, "ls-files", "-s", "-z", "--", ".").split(b"\0"):
            if not rec:
                continue
            meta, _, rel = rec.partition(b"\t")
            mode, blob, stage = meta.decode().split()
            if stage == "0" and mode in ("100644", "100755"):  # 충돌 중인 것, 심볼릭 링크, 하위 모듈은 직접
                index[os.fsdecode(rel)] = blob
        dirty: set[str] = set()
        for rec in _git(d, "status", "--porcelain=v1", "-z", "--no-renames", "--untracked-files=no", "--ignore-submodules=all", "--", ".").split(b"\0"):
            if len(rec) > 3 and rec[1:2] != b" ":  # 둘째 글자 = 작업 폴더와 색인의 차이. 경로는 저장소 맨 위 기준
                dirty.add(os.fsdecode(rec[3:]).removeprefix(prefix))
        clean = [r for r in index if r not in dirty]
        if clean:
            attrs = _git(d, "check-attr", "-z", "--stdin", *CONVERT_ATTRS, stdin=b"\0".join(map(os.fsencode, clean)) + b"\0").split(b"\0")
            for i in range(0, len(attrs) - 2, 3):
                if attrs[i + 2] not in (b"unspecified", b"unset"):
                    dirty.add(os.fsdecode(attrs[i]))
    except (OSError, ValueError, subprocess.SubprocessError):
        return {}
    store = Path(common) / STORE
    known = _load(store)
    out: dict[str, str] = {}
    learned: list[str] = []
    for rel in files:
        blob = index.get(rel)
        if blob is None or rel in dirty:
            continue
        sha = known.get(blob)
        if sha is None:
            try:
                data = (d / rel).read_bytes()
            except OSError:
                continue
            sha = hashlib.sha256(data).hexdigest()
            if _blob_id(data, len(blob)) != blob:  # status 뒤에 바뀌었다: 지금 내용의 해시를 쓰되 기억하지 않는다
                out[rel] = sha
                continue
            known[blob] = sha
            learned.append(f"{blob} {sha}\n")
        out[rel] = sha
    if learned:
        _append(store, "".join(learned).encode("ascii"))
    return out


def _append(store: Path, data: bytes) -> None:
    """한 번의 O_APPEND 쓰기로 덧붙인다: 여러 프로세스가 같이 써도 줄이 섞이지 않는다. 못 쓰면 다음에 다시 계산할 뿐이다."""
    try:
        store.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(store, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o644)
        try:
            view = memoryview(data)
            while view:
                view = view[os.write(fd, view):]
        finally:
            os.close(fd)
    except OSError:
        pass
