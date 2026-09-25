"""프로젝트 등록부 (parity.json): 비교할 앱 하나 = as-is와 to-be의 소스 위치·실행 주소, 그리고 그 이름으로 묶이는 산출물(e2e/<app>, golden/<app>, runs/<app>).

통합 화면의 첫 화면이 이 목록이다. 등록부에 없어도 golden/<app> 이나 e2e/<app> 이 있으면 목록에 나오고(경로 미설정), 화면에서 채울 수 있다.

파일 형식 (작업 디렉터리의 parity.json):
  {"projects": {"portal": {"asis": {"src": "demo-app", "url": "http://127.0.0.1:8820"},
                           "tobe": {"src": "demo-app", "url": "http://127.0.0.1:8821"},
                           "note": "...", "created_at": "2026-09-25T23:10:00"}}}
소스 경로는 작업 디렉터리 안이면 상대 경로로, 밖이면 절대 경로로 저장한다 (저장소를 옮겨도 안쪽 경로는 그대로 맞는다).
"""
from __future__ import annotations

import json
import re
from datetime import datetime
from pathlib import Path
from typing import Any

FILE = Path("parity.json")
NAME_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,39}$")
SIDES = ("asis", "tobe")


def load(path: Path = FILE) -> dict[str, dict[str, Any]]:
    if not path.exists():
        return {}
    data = json.loads(path.read_text(encoding="utf-8"))
    return dict(data.get("projects", {}))


def save(projects: dict[str, dict[str, Any]], path: Path = FILE) -> None:
    path.write_text(json.dumps({"projects": projects}, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _rel(p: Path) -> str:
    """작업 디렉터리 안이면 상대 경로, 아니면 절대 경로."""
    full = p.expanduser().resolve()
    cwd = Path.cwd().resolve()
    return full.relative_to(cwd).as_posix() if full.is_relative_to(cwd) else str(full)


def normalize(name: str, spec: dict[str, Any]) -> dict[str, Any]:
    """웹 폼이나 CLI에서 온 값을 검사해 저장 형태로. 잘못되면 ValueError."""
    if not NAME_RE.match(name or ""):
        raise ValueError("프로젝트 이름은 소문자·숫자·-·_ 로 40자 이내 (예: portal)")
    out: dict[str, Any] = {}
    for side in SIDES:
        s = spec.get(side) or {}
        src = str(s.get("src") or "").strip()
        url = str(s.get("url") or "").strip()
        if not src:
            raise ValueError(f"{'as-is' if side == 'asis' else 'to-be'} 소스 위치를 고르세요")
        p = Path(src).expanduser()
        if not p.is_dir():
            raise ValueError(f"{'as-is' if side == 'asis' else 'to-be'} 소스 위치가 폴더가 아닙니다: {src}")
        if url and not re.match(r"^https?://", url):
            raise ValueError(f"실행 주소는 http:// 또는 https:// 로 시작해야 합니다: {url}")
        out[side] = {"src": _rel(p), "url": url}
    out["note"] = str(spec.get("note") or "").strip()
    return out


def add(name: str, spec: dict[str, Any], *, path: Path = FILE, tests_root: Path = Path("e2e")) -> dict[str, Any]:
    projects = load(path)
    if name in projects:
        raise ValueError(f"이미 있는 프로젝트: {name}")
    rec = normalize(name, spec)
    rec["created_at"] = datetime.now().isoformat(timespec="seconds")
    projects[name] = rec
    save(projects, path)
    (tests_root / name).mkdir(parents=True, exist_ok=True)  # 시나리오가 들어갈 자리
    return rec


def update(name: str, spec: dict[str, Any], *, path: Path = FILE) -> dict[str, Any]:
    projects = load(path)
    rec = normalize(name, spec)
    rec["created_at"] = projects.get(name, {}).get("created_at") or datetime.now().isoformat(timespec="seconds")
    projects[name] = rec
    save(projects, path)
    return rec


def remove(name: str, *, path: Path = FILE) -> None:
    """등록만 지운다. e2e/·golden/·runs/ 의 산출물은 그대로 둔다 (골든은 사람 승인물이라 도구가 지우지 않는다)."""
    projects = load(path)
    projects.pop(name, None)
    save(projects, path)


def names(*, path: Path = FILE, golden_root: Path = Path("golden"), tests_root: Path = Path("e2e")) -> list[str]:
    """등록부 ∪ golden/<app> ∪ e2e/<app>. 등록 순서 먼저, 나머지는 이름순."""
    reg = list(load(path))
    found = set(reg)
    for root in (golden_root, tests_root):
        if root.is_dir():
            for p in sorted(root.iterdir()):
                if p.is_dir() and not p.name.startswith((".", "_")) and p.name not in found and NAME_RE.match(p.name):
                    found.add(p.name)
                    reg.append(p.name)
    return reg


# ---- 폴더 고르기 (웹 화면의 파일 시스템 탐색) ----
def roots() -> list[Path]:
    """탐색을 허용하는 뿌리: 작업 디렉터리와 홈. 그 밖은 목록을 주지 않는다."""
    out = [Path.cwd().resolve()]
    home = Path.home().resolve()
    if home not in out:
        out.append(home)
    return out


def allowed(p: Path) -> bool:
    full = p.expanduser().resolve()
    return any(full == r or full.is_relative_to(r) for r in roots())


def listdir(path: str | None) -> dict[str, Any]:
    """폴더 하나의 하위 폴더 목록. 숨김 폴더·node_modules·.venv 는 뺀다. 허용 범위 밖이면 PermissionError."""
    p = (Path(path).expanduser() if path else Path.cwd()).resolve()
    if not allowed(p):
        raise PermissionError(f"작업 디렉터리나 홈 아래만 볼 수 있습니다: {p}")
    if not p.is_dir():
        raise FileNotFoundError(str(p))
    skip = {"node_modules", "__pycache__", ".venv", "venv", "dist", "build"}
    dirs = []
    for c in sorted(p.iterdir(), key=lambda x: x.name.lower()):
        if c.is_dir() and not c.name.startswith(".") and c.name not in skip:
            try:
                hint = ", ".join(sorted(x.name for x in c.iterdir() if x.is_file() and not x.name.startswith("."))[:3])
            except PermissionError:
                hint = ""
            dirs.append({"name": c.name, "path": str(c), "hint": hint})
    parent = p.parent if p.parent != p and allowed(p.parent) else None
    return {"path": str(p), "display": _rel(p) or ".", "parent": str(parent) if parent else None,
            "roots": [{"name": "작업 디렉터리" if i == 0 else "홈", "path": str(r)} for i, r in enumerate(roots())], "dirs": dirs}
