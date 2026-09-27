"""목록성 화면의 반복 템플릿: 표의 행, 목록의 항목을 알아보고 행 몇 개만 대표로 고른다 (eastshift crawl).

문제: 목록 행마다 링크 이름이 다르면(ORD-1 노트북, ORD-2 볼펜 …) 탐색기가 행을 전부 누른다. 행 1,000개면 클릭 1,000번이고
상한에 걸리면 DOM 순서로 앞에서 잘려 뒤쪽 행과 다른 메뉴는 못 간다.

방법
1. 템플릿 인식 (결정론적): aria 스냅샷에서 table/grid 의 row 와 list 의 listitem 을 행으로, 그 안의 cell/텍스트를 열로 읽는다.
   같은 표의 같은 열에 있는 요소는 한 그룹이다. 항목마다 링크 하나뿐인 목록(메뉴)은 데이터 목록이 아니므로 뺀다.
2. 분기 열 고르기: 어떤 열의 값에 따라 상세 화면이 달라질 수 있는지. 픽스처 `pick`(사람) > Jev 분류(캐시, margin 게이트) > 규칙(값 종류가 적고 숫자·날짜가 아닌 열).
3. 층화 대표 선택 (결정론적): 분기 열 값의 조합(층)마다 첫 행 하나, 상한 K. 분기 열이 없으면 첫 행과 마지막 행.
4. 적응 확장은 crawl 쪽에서: 같은 층의 대표들이 다른 화면으로 가면 그 층을 더 누른다 (분류가 틀렸을 때의 안전망).
"""
from __future__ import annotations

import hashlib
import json
import re
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .snapshot import ACTIONABLE_ROLES, LINE, TEXT_LINE, _unquote

TABLE_ROLES = {"table", "grid", "treegrid"}
CELL_ROLES = {"cell", "gridcell", "rowheader"}
NUMBERY = re.compile(r"^[\s\d.,:/\-+%원$€¥₩]*\d[\s\d.,:/\-+%원$€¥₩a-zA-Z]*$")  # 금액·수량·날짜·코드
BRANCH_HINT = re.compile(r"상태|유형|구분|등급|종류|여부|타입|분류|status|type|kind|grade|category|level|flag", re.I)
MAX_DISTINCT = 6      # 값 종류가 이보다 많으면 분기 열이 아니다 (이름·번호). 종류가 행 수의 절반을 넘어도 마찬가지
MAX_VALUE_LEN = 14    # 분기 값은 짧다 (접수 / 배송중 / VIP)


@dataclass
class ListInfo:
    id: int
    kind: str                       # table | list
    heading: str                    # 바로 앞 제목 (없으면 "")
    headers: list[str]
    rows: list[list[str]] = field(default_factory=list)   # 데이터 행의 열 값
    branch_cols: list[int] = field(default_factory=list)  # 분기 열 (index)
    source: str = ""                # fixture | jev | rule | none
    margin: float | None = None     # jev 일 때
    reps: dict[str, list[int]] = field(default_factory=dict)  # 템플릿(열) → 대표 행 index

    def to_dict(self) -> dict[str, Any]:
        return {"id": self.id, "kind": self.kind, "heading": self.heading, "headers": self.headers, "rows": len(self.rows),
                "branch_cols": self.branch_cols, "branch": [self.headers[c] for c in self.branch_cols if c < len(self.headers)],
                "source": self.source, "margin": self.margin, "reps": self.reps}


@dataclass(frozen=True)
class Slot:
    list_id: int
    row: int       # 데이터 행 index (머리 행 제외)
    col: int       # 표: 셀 index, 목록: 항목 안 몇 번째 동작 요소


def parse_lists(snapshot: str, *, roles: tuple[str, ...] = ACTIONABLE_ROLES) -> tuple[list[ListInfo], dict[int, Slot]]:
    """스냅샷 하나 → (목록들, 동작 요소 index → 자리). index 는 parse_elements 가 매기는 순서와 같다 (이름 있는 활성 요소만 센다)."""
    lists: list[ListInfo] = []
    slots: dict[int, Slot] = {}
    stack: list[dict[str, Any]] = []   # 열린 컨테이너·행·셀: {"kind", "indent", ...}
    idx = -1
    last_heading = ""

    def top(kind: str) -> dict[str, Any] | None:
        for c in reversed(stack):
            if c["kind"] == kind:
                return c
        return None

    for line in snapshot.splitlines():
        m = LINE.match(line)
        if not m:
            continue
        indent = len(m["indent"])
        role, raw, rest = m["role"], m["name"], m["rest"]
        name = _unquote(raw) if raw else ""
        while stack and stack[-1]["indent"] >= indent:
            stack.pop()
        tm = TEXT_LINE.match(line)
        if role == "heading" and name:
            last_heading = name
        if role in TABLE_ROLES or role == "list":
            info = ListInfo(len(lists), "table" if role in TABLE_ROLES else "list", last_heading, [])
            lists.append(info)
            stack.append({"kind": "container", "indent": indent, "info": info, "nrow": 0})
            continue
        cont = top("container")
        if cont and role == "row" and cont["info"].kind == "table":
            stack.append({"kind": "row", "indent": indent, "cells": [], "header": False, "ncell": 0, "nact": 0})
            continue
        if cont and role == "listitem" and cont["info"].kind == "list":
            stack.append({"kind": "row", "indent": indent, "cells": [], "header": False, "ncell": 0, "nact": 0, "item": True})
            continue
        row = top("row")
        if row is not None and role == "columnheader":
            row["header"] = True
            row["cells"].append(name)
            if cont is not None:
                cont["info"].headers = row["cells"]
            continue
        if row is not None and role in CELL_ROLES:
            text = name or (m["rest"].split(":", 1)[1].strip() if ":" in m["rest"] else "")
            row["cells"].append(text)
            stack.append({"kind": "cell", "indent": indent, "index": len(row["cells"]) - 1})
            continue
        if role in roles and raw and "[disabled]" not in rest and name:
            idx += 1
            if row is not None and cont is not None and not row["header"]:
                cell = top("cell")
                if row.get("item"):
                    row["cells"].append(name)
                    col = row["nact"]
                    row["nact"] += 1
                else:
                    col = cell["index"] if cell else len(row["cells"])
                slots[idx] = Slot(cont["info"].id, _row_index(cont, row), col)
            continue
        if row is not None and row.get("item") and tm and top("cell") is None:
            row["cells"].append(tm["text"].strip())
    # 행을 닫으면서 셀 값을 목록에 넣는다 (스택에서 빠질 때가 아니라 끝에 한 번에): 위 루프는 행마다 _row_index 가 붙였다
    for info in lists:
        rows = info.rows
        if info.kind == "list" and not any(len(r) > 1 for r in rows):
            info.headers = []      # 항목마다 링크 하나뿐인 목록 = 메뉴. 데이터 목록이 아니다
            info.rows = []
            continue
        width = max((len(r) for r in rows), default=0)
        if not info.headers:
            info.headers = [f"열{i + 1}" for i in range(width)]
        info.rows = [r + [""] * (width - len(r)) for r in rows]
    slots = {i: s for i, s in slots.items() if lists[s.list_id].rows}
    return lists, slots


def _row_index(cont: dict[str, Any], row: dict[str, Any]) -> int:
    """행에 데이터 행 번호를 매기고 셀 값 목록을 컨테이너에 등록한다 (한 행에 한 번)."""
    if "index" not in row:
        row["index"] = cont["nrow"]
        cont["nrow"] += 1
        cont["info"].rows.append(row["cells"])
    return row["index"]


# ---- 분기 열 ----
def is_numbery(v: str) -> bool:
    return bool(v) and bool(NUMBERY.match(v))


def branch_columns_rule(info: ListInfo) -> list[int]:
    """규칙: 값 종류가 2~MAX_DISTINCT 이고, 숫자·날짜·금액이 아니고, 짧은 값이며, 행마다 다 다르지는 않은 열. 머리글에 상태·유형 같은 말이 있으면 우선."""
    n = len(info.rows)
    if n < 2:
        return []
    out = []
    for c, h in enumerate(info.headers):
        vals = [r[c] for r in info.rows if c < len(r) and r[c]]
        if not vals:
            continue
        d = len(set(vals))
        hint = bool(BRANCH_HINT.search(h))
        if d < 2 or d > MAX_DISTINCT or (d > max(2, len(vals) / 2) and not hint):
            continue
        if sum(is_numbery(v) for v in vals) > len(vals) / 2 and not hint:
            continue
        if max(len(v) for v in vals) > MAX_VALUE_LEN and not hint:
            continue
        out.append(c)
    return out


def branch_columns_fixture(info: ListInfo, pick: dict[str, list[str]] | None) -> list[int] | None:
    """픽스처 pick: {목록 제목 | "*": [열 이름…]}. 맞는 항목이 없으면 None."""
    if not pick:
        return None
    cols = pick.get(info.heading) if info.heading else None
    if cols is None:
        cols = pick.get("*")
    if cols is None:
        return None
    return [i for i, h in enumerate(info.headers) if h in cols]


def choose_reps(info: ListInfo, k: int) -> list[int]:
    """층(분기 열 값 조합)마다 첫 행, 상한 k. 예산이 남으면 층마다 둘째 행. 분기 열이 없으면 첫 행과 마지막 행 (값이 다른 두 행을 보는 값싼 보험)."""
    n = len(info.rows)
    if n == 0:
        return []
    k = max(1, k)
    if not info.branch_cols:
        return [0] if n == 1 or k == 1 else [0, n - 1]
    by_key: dict[tuple[str, ...], list[int]] = {}
    for i, r in enumerate(info.rows):
        by_key.setdefault(tuple(r[c] if c < len(r) else "" for c in info.branch_cols), []).append(i)
    reps = [rows[0] for rows in by_key.values()][:k]
    # 예산이 남으면 층마다 둘째 행을 돌아가며 더한다: 같은 층의 두 대표가 다른 화면으로 가면 분기 열이 틀렸다는 신호가 된다 (적응 확장의 재료)
    depth = 1
    while len(reps) < k and any(len(rows) > depth for rows in by_key.values()):
        for rows in by_key.values():
            if len(rows) > depth and len(reps) < k:
                reps.append(rows[depth])
        depth += 1
    return sorted(reps)


def stratum(info: ListInfo, row: int) -> dict[str, str]:
    r = info.rows[row] if row < len(info.rows) else []
    return {info.headers[c]: (r[c] if c < len(r) else "") for c in info.branch_cols if c < len(info.headers)}


def more_rows(info: ListInfo, row: int, exclude: set[int], limit: int) -> list[int]:
    """같은 층의 다른 행 (적응 확장용)."""
    key = tuple(stratum(info, row).values())
    out = []
    for i in range(len(info.rows)):
        if i in exclude:
            continue
        if tuple(stratum(info, i).values()) == key:
            out.append(i)
        if len(out) >= limit:
            break
    return out


# ---- Jev 분류 (선택) ----
COLUMN_KINDS = {
    "branch": "A category or state that changes what the detail screen shows or allows (status, type, grade, approval, flag).",
    "key": "An identifier that names the row: order number, code, name, title.",
    "measure": "A number, amount, quantity, date or time.",
    "text": "Free text: memo, description, address.",
    "action": "A button or link column: edit, delete, view.",
}


def list_signature(info: ListInfo) -> str:
    sample = [r[:8] for r in info.rows[:5]]
    return hashlib.sha256(json.dumps([info.kind, info.headers, sample], ensure_ascii=False).encode()).hexdigest()[:16]


def jev_state(info: ListInfo, page_title: str) -> dict[str, Any]:
    cols = []
    for c, h in enumerate(info.headers):
        vals = [r[c] for r in info.rows if c < len(r) and r[c]]
        cnt = Counter(vals)
        cols.append({"id": f"col{c}", "header": h, "distinct": len(cnt), "rows": len(vals), "samples": [v for v, _ in cnt.most_common(5)]})
    return {"page": page_title, "list": info.heading, "kind": info.kind, "rows": len(info.rows), "columns": cols,
            "rule": "Classify each column of this list. 'branch' means rows with different values here lead to detail screens that look or behave differently."}


def jev_questions(info: ListInfo) -> dict[str, Any]:
    from typesafe_sdk import Choice, Noul

    qs: dict[str, Any] = {f"col{c}": Choice(instructions=f'What kind of column is "{h}"?', criteria=dict(COLUMN_KINDS)) for c, h in enumerate(info.headers)}
    qs["varies"] = Noul(instructions="Do rows of this list lead to detail screens whose shape depends on some column value?",
                        criteria={"true": "Detail differs by a column value (status, type, grade …).", "false": "All rows open the same kind of detail screen."})
    return qs


def classify_columns_jev(client: Any, info: ListInfo, page_title: str, *, cache: dict[str, Any], min_margin: float = 0.2) -> tuple[list[int] | None, float | None]:
    """Jev(Choice/Noul)로 분기 열을 고른다. (열 목록, margin).

    열마다 따로 판단한다: 'branch'로 골랐고 margin(1위-2위 확률 차)이 min_margin 이상이면 분기 열. 분기일 가능성이 있는데(branch 확률 0.3 이상) 확신이 없는 열이
    하나라도 있으면 (None, margin) → 규칙으로 대신하고 graph.md 에 검토 표시. 확신 있게 '아니다'라고 한 열은 게이트에 걸리지 않는다. 오류도 (None, None). 결과는 cache 에 남는다.
    """
    key = list_signature(info)
    if key in cache:
        c = cache[key]
        return (c["branch_cols"], c["margin"]) if c["branch_cols"] is not None else (None, c["margin"])
    try:
        response, _ms = client.ask(state=jev_state(info, page_title), questions=jev_questions(info))
        answers = response.answers
        cols, margins, undecided, detail = [], [], [], {}
        for c, h in enumerate(info.headers):
            a = answers[f"col{c}"]
            probs = {str(k): float(v) for k, v in dict(a.probabilities or {}).items()}
            ranked = sorted(probs.values(), reverse=True)
            m = (ranked[0] - ranked[1]) if len(ranked) > 1 else (ranked[0] if ranked else 0.0)
            pb, choice = probs.get("branch", 0.0), str(a.choice)
            detail[h] = {"choice": choice, "branch": round(pb, 2), "margin": round(m, 3)}
            if choice == "branch" and m >= min_margin:
                cols.append(c)
                margins.append(m)
            elif choice == "branch" or pb >= 0.3:  # 분기인지 아닌지 갈리는 열: 여기서 확신이 없으면 목록 전체를 규칙에 넘기고 검토 표시
                if m < min_margin:
                    undecided.append(c)
        # margin: 분기로 정한 열들 중 최소. 분기 열이 없으면 가장 애매했던 열의 margin (그 값이 게이트를 넘었다는 뜻)
        margin = round(min(margins), 3) if margins else round(min([detail[h]["margin"] for h in detail] or [0.0]), 3)
        result = None if undecided else cols
    except Exception as e:  # noqa: BLE001 - 분류가 탐색을 깨면 안 된다
        result, margin = None, None
        cache[key] = {"branch_cols": None, "margin": None, "error": f"{type(e).__name__}: {str(e).splitlines()[0][:120] if str(e) else ''}"}
        return None, None
    cache[key] = {"branch_cols": result, "margin": margin, "headers": info.headers, "list": info.heading, "columns": detail}
    return result, margin


def load_cache(path: Path | None) -> dict[str, Any]:
    if path and path.exists():
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            return {}
    return {}


def save_cache(path: Path | None, cache: dict[str, Any]) -> None:
    if path:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(cache, ensure_ascii=False, indent=1), encoding="utf-8")


def decide(info: ListInfo, *, pick: dict[str, list[str]] | None, jev: Any | None, page_title: str, cache: dict[str, Any], min_margin: float = 0.2) -> None:
    """분기 열을 정해 info 에 적는다: 픽스처 > Jev > 규칙."""
    cols = branch_columns_fixture(info, pick)
    if cols is not None:
        info.branch_cols, info.source = cols, "fixture"
        return
    if jev is not None and len(info.rows) >= 2:
        cols, margin = classify_columns_jev(jev, info, page_title, cache=cache, min_margin=min_margin)
        info.margin = margin
        if cols is not None:
            info.branch_cols, info.source = cols, "jev"
            return
        info.source = "rule (jev abstain)"
        info.branch_cols = branch_columns_rule(info)
        return
    info.branch_cols, info.source = branch_columns_rule(info), "rule"
