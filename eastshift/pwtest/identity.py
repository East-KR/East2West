"""Stable test artifact names; preserve existing names unless two modules collide."""
from __future__ import annotations

import hashlib
from collections import Counter
from pathlib import Path


def canonical_ids(keys: list[str]) -> dict[str, str]:
    names = Counter(key.rsplit("::", 1)[-1] for key in keys)
    out = {}
    for key in keys:
        path, name = key.rsplit("::", 1)
        if names[name] == 1:
            out[key] = name
        else:
            digest = hashlib.sha256(str(Path(path).resolve()).encode()).hexdigest()[:10]
            out[key] = f"{digest}__{name}"
    return out
