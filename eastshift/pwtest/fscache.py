"""산출물 파일의 읽기 캐시: 파일 시각(ns)·크기가 같으면 지난번 결과를 다시 준다.

골든 JSON·원장 JSON·캡처는 한 번 쓰면 바뀌지 않고, 통합 화면은 요청마다 그 전부를 다시 읽는다 (승인 상태 = 모든 파일의 해시,
시나리오 목록 = 모든 골든 JSON). 시나리오 수천 개·캡처 수만 장이어도 새로 생기거나 바뀐 파일만 읽게 한다.
돌려준 dict·list는 공유되니 고치지 않는다. 잘못 판단할 수 있는 경우: 같은 초 안에 같은 크기로 덮어쓰기 — mtime_ns 해상도 문제라 실제로는 드물다.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

_HASH: dict[str, tuple[int, int, str]] = {}
_JSON: dict[str, tuple[int, int, Any]] = {}


def _key(p: Path) -> tuple[int, int]:
    st = p.stat()
    return st.st_mtime_ns, st.st_size


def sha256(p: Path) -> str:
    k = _key(p)
    hit = _HASH.get(str(p))
    if hit and hit[:2] == k:
        return hit[2]
    h = hashlib.sha256(p.read_bytes()).hexdigest()
    _HASH[str(p)] = (*k, h)
    return h


def json_load(p: Path) -> Any:
    k = _key(p)
    hit = _JSON.get(str(p))
    if hit and hit[:2] == k:
        return hit[2]
    data = json.loads(p.read_text(encoding="utf-8"))
    _JSON[str(p)] = (*k, data)
    return data


def clear() -> None:
    _HASH.clear()
    _JSON.clear()
