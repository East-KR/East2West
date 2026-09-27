"""Content identifiers for reproducible comparison runs."""
from __future__ import annotations

import hashlib
from pathlib import Path


def source_hash(test_dir: Path) -> str:
    root = Path(__file__).resolve().parents[1]
    digest = hashlib.sha256()
    for prefix, folder in (("tool", root), ("tests", test_dir)):
        if not folder.exists():
            continue
        for path in sorted(folder.rglob("*.py")):
            digest.update(f"{prefix}/{path.relative_to(folder).as_posix()}\0".encode())
            digest.update(hashlib.sha256(path.read_bytes()).digest())
    return digest.hexdigest()
