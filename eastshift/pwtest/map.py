"""Screen Map (eastshift ui 의 첫 탭): 골든에 기록된 as-is 동작을 라우트(주소) 단위로 합쳐 네트워크로 그린다.

hub.py 가 /page/<app>/map 요청에 만든다: src=compare 는 render(build(golden, junit, …)), src=asis|tobe 는 render(build_from_crawl(…)).

두 층
  라우트  주소가 같은 화면 (/orders, /orders/{id} …). 지도의 노드. 홈(/)이 있으면 홈이 시작이고 맨 왼쪽.
  상태    한 라우트 안에서 구조가 다른 화면: 기본, 팝업(dialog), 드로워(complementary), 탭, 알림. 라우트의 상세 페이지에 작은 그래프로 나오고, 상태를 누르면 팝업(캡처·오는 동작·가는 상태·지나는 테스트).

화면 두 장
  지도  왼쪽 라우트 목록 (검색, 상태 점, 이름·주소, 안의 상태 수) + 지도: 왼쪽에서 오른쪽으로 한 방향. 열 = 시작에서 몇 번 눌러 가는지, 열 안 순서는 무게중심(Sugiyama 방식)으로 선 교차를 줄인다.
        노드 = 화면 프레임(대표 캡처, 이름, 안의 팝업·드로워·탭, 상태 띠). 화살표 = 동작 이름 알약. 되돌아가는 길은 아래 차선으로 회색.
  상세  라우트를 누르면 넘어가는 페이지 (#<라우트>[/<상태>], 뒤로가기로 지도에 복귀): 큰 캡처(호버하면 as-is/to-be 전환)와 '상태 그래프 보기' 버튼(팝업. 상태를 누르면 그 상태의 상세로 바뀐다) | 시작에서 오는 길, 나가는 길, 지나는 테스트(누르면 시나리오 팝업).
  --junit이 있으면 to-be에서 다른 화면은 빨간 띠, 상세에 무엇이 달랐는지.

같은 상태인지는 화면 구조(제목·입력칸·버튼)로 가리고 글자 내용(주문번호, 품목명)은 보지 않는다. 주소의 숫자 조각은 {id}로 합친다. 그 밖에는 기록된 산출물만 읽는다.
"""
from __future__ import annotations

import base64
import json
import re
from collections import defaultdict
from pathlib import Path
from typing import Any
from html import unescape as _unescape
from urllib.parse import urlparse

from ..crawl import landmarks, signature
from . import html, oracle
from .mutation import route_key
from .report import _junit

STEP_DIFF = re.compile(r"step (\d+) ")
SNAP_LINE = re.compile(r'^(?P<indent>\s*)-\s+(?P<role>[a-z]+)\b')
# 데이터에 따라 글자가 바뀌는 역할: "같은 화면" 판단에서는 역할만 남긴다 (주문번호·품목명이 달라도 같은 완료 화면)
DATA_ROLES = {"text", "paragraph", "definition", "term", "strong", "emphasis", "code", "cell", "gridcell", "listitem", "status", "log", "time"}
NUMBERY = re.compile(r"\S*\d\S*")
DIALOG = re.compile(r'^\s*-\s+(?:dialog|alertdialog)\s+"(?P<name>(?:\\.|[^"\\])*)"')
DRAWER = re.compile(r'^\s*-\s+complementary\s+"(?P<name>(?:\\.|[^"\\])*)"')
TAB_SEL = re.compile(r'^\s*-\s+tab\s+"(?P<name>(?:\\.|[^"\\])*)"\s+\[selected\]')
KIND_KO = {"base": "기본", "dialog": "팝업", "drawer": "드로워", "tab": "탭", "alert": "알림"}
# 비교 지도의 화면 상태: 다름(빨강) · 미개발(노랑, as-is에는 있는데 to-be 탐색에 없음) · 새 화면(파랑, to-be 탐색에만 있음) · 같음(초록) · 비교 안 됨
STATUS_KO = {"diff": "as-is와 다름", "undeveloped": "to-be 탐색에서 미발견", "new": "to-be에서만 발견", "accepted": "승인된 차이", "same": "as-is와 같음", "untested": "비교 안 됨",
             "unreached": "코드에만 있음 (탐색·시나리오 미도달)"}
STATUS_CLS = {"diff": "bad", "undeveloped": "undev", "new": "new", "accepted": "accepted", "same": "ok", "untested": "", "unreached": "unreached"}


def _screen_sig(snapshot: str) -> str:
    lines = []
    for line in snapshot.splitlines():
        m = SNAP_LINE.match(line)
        if m and m["role"] in DATA_ROLES:
            line = f'{m["indent"]}- {m["role"]}'
        elif not m and line.strip().startswith("- /"):
            continue
        lines.append(line)
    return signature([("", "\n".join(lines))])


def _img(p: Path) -> str:
    return "data:image/jpeg;base64," + base64.b64encode(p.read_bytes()).decode() if p.exists() else ""


def _clean_text(s: str) -> str:
    return re.sub(r"\s+", " ", NUMBERY.sub("", s)).strip()


def _route(url: str) -> str:
    """주소 → 라우트. 숫자·해시 조각은 {id}, 쿼리는 뺀다 (결함 주입과 같은 규칙: mutation.route_key)."""
    return route_key(urlparse(url).path or "/")


def _state(snapshot: str, base_tab: str = "") -> tuple[str, str, str]:
    """(종류, 이름, 선택된 탭): 열린 팝업 > 드로워 > 알림 > 기본과 다른 탭 > 기본. base_tab은 그 라우트 기본 상태의 탭."""
    tab = ""
    for line in snapshot.splitlines():
        if m := DIALOG.match(line):
            return "dialog", _clean_text(m["name"])[:40], tab
        if m := DRAWER.match(line):
            return "drawer", _clean_text(m["name"])[:40], tab
        if not tab and (m := TAB_SEL.match(line)):
            tab = _clean_text(m["name"])[:40]
    _, alerts, _, _ = landmarks(snapshot)
    if alerts:
        return "alert", _clean_text(alerts[0])[:40], tab
    if tab and tab != base_tab:
        return "tab", tab, tab
    return "base", "", tab


def _heading(snapshot: str, url: str, title: str) -> str:
    heads, _, _, _ = landmarks(snapshot)
    return _clean_text((heads[0] if heads else "") or title or urlparse(url).path or "/")[:36] or "/"


def _fail_steps(messages: list[str], assertions: list[dict[str, Any]]) -> set[int]:
    text = "\n".join(messages)
    steps = {int(m.group(1)) for m in STEP_DIFF.finditer(text)}
    for a in assertions:
        tgt = str(a["target"])
        if tgt and (f'"{tgt}"' in text or f"'{tgt}'" in text):
            steps.add(a["step"] - 1)
    return steps


def _plain(action_html: str) -> str:
    """동작 HTML → 글자만 (화면에 넣을 때 다시 이스케이프하므로 엔티티는 푼다)."""
    t = re.sub("<[^>]+>", " ", action_html)
    t = re.sub(r"\s+", " ", t).strip()
    return _unescape(t)


def _word(action: str) -> str:
    w = _plain(action)
    return w.replace("열기", "").replace("누르기", "").replace("선택", "").replace("입력", "").strip() or w


def _records_from_golden(d: Path, tests_dir: Path | None) -> list[dict[str, Any]]:
    """골든(as-is 기록) → 기록 목록. 캡처 경로는 골든 폴더 기준."""
    docs = html.docstrings(tests_dir or Path("e2e") / d.name)
    out = []
    for t in oracle.tests(d):
        data = json.loads((d / f"{t['name']}.json").read_text(encoding="utf-8"))
        out.append({"name": t["name"], "title": html._title(t["name"], docs), "steps": data.get("steps", []),
                    "assertions": data.get("assertions", []), "shot_dir": d})
    return out


def _records_from_crawl(out_dir: Path) -> list[dict[str, Any]]:
    """탐색 결과(crawl/<app>/graph.json) → 기록 목록. 경로 하나(모든 전이를 한 번 이상 지나는 최대 경로) = 기록 하나. 캡처는 상태마다 한 장이라 경로들이 나눠 쓴다."""
    g = json.loads((out_dir / "graph.json").read_text(encoding="utf-8"))
    nodes = {n["id"]: n for n in g["nodes"]}
    edges = {e["id"]: e for e in g["edges"]}
    cands = [nodes[e["src"]]["path"] + [e["id"]] for e in g["edges"] if e["kind"] == "transition" and e["dst"] is not None]
    cands.sort(key=len, reverse=True)
    kept: list[list[int]] = []
    for p in cands:
        if not any(k[:len(p)] == p for k in kept):
            kept.append(p)
    kept.sort()
    if not kept and nodes:
        kept = [[]]
    start = nodes[min(nodes)] if nodes else None
    recs = []
    for i, path in enumerate(kept, 1):
        if start is None:
            break
        steps = [{"index": 0, "kind": "goto", "text": g.get("start") or urlparse(start["url"]).path or "/", "url": start["url"], "title": start["title"],
                  "snapshot": start["snapshot"], "shot": start["screenshot"], "dialogs": []}]
        for j, eid in enumerate(path, 1):
            e, dst = edges[eid], nodes[edges[eid]["dst"]]
            act = e["steps"][-1]
            text = f'{act["role"]} "{act["name"]}"' + (f' = {act["value"]}' if act.get("value") is not None else "")
            kind = "fill" if act["action"] == "fill" else ("select" if act["role"] == "option" else "act")
            dialogs = [{"type": x["type"], "message": x["message"], "action": "dismiss" if e["mode"] == "dismiss" else "accept"} for x in e.get("dialogs", [])]
            steps.append({"index": j, "kind": kind, "text": text, "url": dst["url"], "title": dst["title"], "snapshot": dst["snapshot"], "shot": dst["screenshot"], "dialogs": dialogs})
        title = " → ".join([start["label"]] + [nodes[edges[eid]["dst"]]["label"] for eid in path])
        recs.append({"name": f"crawl_{i:02d}", "title": title, "steps": steps, "assertions": [], "shot_dir": None})
    return recs


def _crawl_graph(out_dir: Path | None) -> dict[str, Any] | None:
    f = out_dir / "graph.json" if out_dir else None
    return json.loads(f.read_text(encoding="utf-8")) if f and f.exists() else None


def _dead_nodes(g: dict[str, Any]) -> set[int]:
    """문서 응답이 4xx/5xx 인 길로만 닿는 상태: 화면이 아니라 '없는 페이지'다 (개발 전 to-be의 죽은 링크). 지도의 화면으로 세지 않는다."""
    clean, dead = set(), set()
    for e in g["edges"]:
        if e.get("dst") is None:
            continue
        (dead if any(h[:1] in "45" for h in e.get("http_errors", [])) else clean).add(e["dst"])
    return dead - clean


def _crawl_routes(g: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """탐색 그래프 → 라우트(주소)별 대표 캡처. 기본 상태(팝업·드로워가 안 열린)의 캡처를 고른다. 없는 페이지(4xx/5xx)는 뺀다."""
    info: dict[str, dict[str, Any]] = {}
    dead = _dead_nodes(g)
    for n in sorted(g["nodes"], key=lambda x: x["id"]):
        if n["id"] in dead:
            continue
        path = route_key(urlparse(n["url"]).path or "/")
        kind = _state(n.get("snapshot", ""))[0]
        if path not in info or (info[path]["kind"] != "base" and kind == "base"):
            info[path] = {"shot": n.get("screenshot", ""), "kind": kind, "title": n.get("title", ""), "label": n.get("label", "")}
    return info


def _crawl_records_touching(g: dict[str, Any], want: set[str], side: str) -> list[dict[str, Any]]:
    """탐색 그래프에서 want 라우트로 들어가거나 나오는 전이 하나 = 두 단계짜리 기록. 비교 지도에 '시나리오가 닿지 않은 as-is 화면'과 'to-be에만 있는 화면'을 잇는 데 쓴다."""
    if not want:
        return []
    nodes = {n["id"]: n for n in g["nodes"]}
    who = "as-is" if side == "asis" else "to-be"
    dead = _dead_nodes(g)

    def step(i: int, n: dict[str, Any], kind: str, text: str, dialogs=()) -> dict[str, Any]:
        return {"index": i, "kind": kind, "text": text, "url": n["url"], "title": n["title"], "snapshot": n["snapshot"], "shot": n["screenshot"], "dialogs": list(dialogs)}

    recs, k = [], 0
    for e in g["edges"]:
        if e["kind"] != "transition" or e["dst"] is None or e["dst"] in dead or e["src"] in dead:
            continue
        src, dst = nodes[e["src"]], nodes[e["dst"]]
        sp, dp = (route_key(urlparse(n["url"]).path or "/") for n in (src, dst))
        if sp not in want and dp not in want:
            continue
        act = e["steps"][-1]
        text = f'{act["role"]} "{act["name"]}"' + (f' = {act["value"]}' if act.get("value") is not None else "")
        kind = "fill" if act["action"] == "fill" else ("select" if act["role"] == "option" else "act")
        dialogs = [{"type": x["type"], "message": x["message"], "action": "dismiss" if e["mode"] == "dismiss" else "accept"} for x in e.get("dialogs", [])]
        k += 1
        recs.append({"name": f"{side}-crawl-{k:02d}", "title": f"{who} 탐색: {src['label']} → {dst['label']}", "side": side, "assertions": [], "shot_dir": None,
                     "steps": [step(0, src, "goto", urlparse(src["url"]).path or "/"), step(1, dst, kind, text, dialogs)]})
    if not recs and g["nodes"]:
        n0 = min(g["nodes"], key=lambda x: x["id"])
        if route_key(urlparse(n0["url"]).path or "/") in want:
            recs.append({"name": f"{side}-crawl-01", "title": f"{who} 탐색: {n0['label']}", "side": side, "assertions": [], "shot_dir": None,
                         "steps": [step(0, n0, "goto", urlparse(n0["url"]).path or "/")]})
    return recs


def build(d: Path, junit: Path | None = None, tests_dir: Path | None = None, asis_crawl: Path | None = None, tobe_crawl: Path | None = None) -> dict[str, Any]:
    """as-is 기준 to-be 비교 지도. 골든(as-is 기록)이 뼈대, --junit 실행 결과로 다름, to-be 탐색으로 미개발·새 화면, as-is 탐색으로 시나리오가 닿지 않은 화면.

    캡처는 to-be 탐색 캡처가 있으면 그것(같음·새 화면·다름·비교 안 됨), 미개발이면 as-is 캡처.
    """
    records = _records_from_golden(d, tests_dir)
    golden_paths = {_route(o["url"]) for t in records for o in t["steps"]}
    ga, gt = _crawl_graph(asis_crawl), _crawl_graph(tobe_crawl)
    asis_paths = set(golden_paths)
    if ga:
        ar = _crawl_routes(ga)
        records += _crawl_records_touching(ga, set(ar) - golden_paths, "asis")
        asis_paths |= set(ar)
    tobe_routes = _crawl_routes(gt) if gt else None
    new_paths = set(tobe_routes) - asis_paths if tobe_routes else set()
    if gt:
        records += _crawl_records_touching(gt, new_paths, "tobe")
    g = _build(d.name, records, _junit(junit) if junit else None,
               {"kind": "golden", "label": "to-be 비교 (as-is 기준)", "base": f"golden/{d.name}", "unit": "테스트",
                "asis_crawl": str(asis_crawl) if ga else None, "tobe_crawl": str(tobe_crawl) if gt else None})
    _annotate(g, tobe_routes, new_paths)
    _add_code_routes(g, asis_crawl)  # as-is 소스가 선언한 주소가 잣대: 골든도 as-is 탐색도 닿지 않은 화면
    return g


def build_from_crawl(app: str, out_dir: Path, side: str = "asis") -> dict[str, Any]:
    """탐색 기반: eastshift crawl 이 찾은 화면을 잇는다 (as-is 또는 to-be 한쪽만, 비교 없음)."""
    label = {"asis": "as-is 탐색", "tobe": "to-be 탐색"}.get(side, f"{side} 탐색")
    g = _build(app, _records_from_crawl(out_dir), None, {"kind": "crawl", "side": side, "label": label, "base": str(out_dir), "unit": "경로"})
    _annotate(g, None, set())
    _add_code_routes(g, out_dir)
    return g


def _add_code_routes(g: dict[str, Any], crawl_dir: Path | None) -> None:
    """crawl/<app>/routes.json (eastshift routes 가 소스에서 뽑은 주소)이 있으면 잣대로 쓴다:
    지도의 어느 라우트에도 없는 화면 주소를 회색 '코드에만 있음' 카드로 넣는다. 상태·캡처·테스트는 없다 (닿지 못했으니)."""
    from eastshift import routes as code

    data = code.load(crawl_dir / "routes.json") if crawl_dir else None
    g["code_routes"] = None
    if not data:
        return
    have = {r["path"] for r in g["routes"].values()}
    screens = [r for r in data["routes"] if r["kind"] == "screen"]
    missing = [r for r in screens if r["path"] not in have]
    for r in missing:
        rid = r["path"] if r["path"] not in g["routes"] else f"{r['path']}#code"
        g["routes"][rid] = {"id": rid, "path": r["path"], "name": r["path"], "base": "", "states": [], "shot": "", "tests": [], "failed": False, "accepted": False,
                            "kinds": {}, "tab": "", "status": "unreached", "asis_shot": "", "tobe_shot": "", "methods": r.get("methods", []),
                            "evidence": [f"{r['file']}:{r['line']}", *r.get("also", [])]}
        g["rorder"].append(rid)
    g.setdefault("counts", {})["unreached"] = len(missing)
    g["code_routes"] = {"src": data["src"], "file": str(crawl_dir / "routes.json"), "generated_at": data.get("generated_at"), "total": len(data["routes"]),
                        "screens": len(screens), "reached": len(screens) - len(missing), "unreached": [r["path"] for r in missing]}


def _annotate(g: dict[str, Any], tobe_routes: dict[str, dict[str, Any]] | None, new_paths: set[str]) -> None:
    """라우트마다 상태(status)와 캡처(as-is·to-be·표시용)를 정한다."""
    counts: dict[str, int] = defaultdict(int)
    for r in g["routes"].values():
        p = r["path"]
        tested = g["compared"] and any(t["status"] for t in g["tests"] if t["name"] in r["tests"])
        if tobe_routes is not None and p in new_paths:
            status, asis_shot, tobe_shot = "new", "", r["shot"]
        else:
            asis_shot = r["shot"]
            tobe_shot = tobe_routes[p]["shot"] if tobe_routes and p in tobe_routes else ""
            # 미개발이 다름보다 먼저: to-be에 아직 없는 화면은 비교 실행에서도 실패하지만, 원인은 '미개발'이다
            status = ("undeveloped" if tobe_routes is not None and p not in tobe_routes else "diff" if r["failed"] else "accepted" if r.get("accepted") else "same" if tested else "untested")
        if tobe_shot and tobe_shot not in g["shots"]:
            g["shots"][tobe_shot] = _img(Path(tobe_shot))
        r["status"], r["asis_shot"], r["tobe_shot"] = status, asis_shot, tobe_shot
        r["shot"] = tobe_shot if status != "undeveloped" and tobe_shot else asis_shot
        counts[status] += 1
    g["counts"] = dict(counts)
    g["status_ko"] = STATUS_KO


def _build(app: str, records: list[dict[str, Any]], run: dict[str, Any] | None, source: dict[str, Any]) -> dict[str, Any]:
    cases = {c["name"]: c for c in run["cases"]} if run else {}
    nodes: dict[str, dict[str, Any]] = {}          # 상태 (구조 서명 기준)
    routes: dict[str, dict[str, Any]] = {}         # 라우트 (주소 기준)
    sedges: dict[tuple[str, str], dict[str, Any]] = {}
    redges: dict[tuple[str, str], dict[str, Any]] = {}
    order: list[str] = []
    rorder: list[str] = []
    shots: dict[str, str] = {}
    tests = []
    for t in records:
        steps = t["steps"]
        by_step = defaultdict(list)
        for a in t["assertions"]:
            by_step[a["step"] - 1].append(a)
        case = cases.get(t["name"])
        failed = _fail_steps(case["messages"], t["assertions"]) if case and case["status"] == "fail" else set()
        rows = html.rows_for(case["messages"])[0] if case and case["status"] == "fail" else []
        status = None if case is None else ("accepted" if case.get("accepted_differences") and case["status"] == "pass" else case["status"])
        accepted_steps = {x["step"] for x in case.get("accepted_differences", [])} if case else set()
        seq, prev = [], None
        for o in steps:
            snap = o.get("snapshot", "")
            sig = _screen_sig(snap)
            shot_id = ""
            if o.get("shot"):
                shot_path = (t["shot_dir"] / o["shot"]) if t["shot_dir"] else Path(o["shot"])
                shot_id = str(shot_path)  # 파일 하나 = 항목 하나 (탐색 지도는 경로들이 같은 캡처를 나눠 쓴다)
                if shot_id not in shots:
                    shots[shot_id] = _img(shot_path)
            path = _route(o["url"])
            head = _heading(snap, o["url"], o.get("title", ""))
            kind0, _, tab0 = _state(snap)
            if sig in nodes:
                rid = nodes[sig]["route"]
            else:
                # 같은 주소의 라우트 중 제목이 같은 것. 주소가 안 바뀌는 앱(frameset, 해시 없는 SPA)은 제목이 다른 기본 화면을 다른 라우트로 가른다.
                # {id}가 있는 주소는 제목에 데이터(고객 이름)가 섞이므로 가르지 않는다
                same_path = [r for r in routes.values() if r["path"] == path]
                match = next((r["id"] for r in same_path if r["name"] == head), None)
                if match:
                    rid = match
                elif same_path and kind0 in ("base", "tab") and "{id}" not in path:
                    rid = f"{path}#{head}"
                else:
                    rid = same_path[0]["id"] if same_path else path
            if rid not in routes:
                routes[rid] = {"id": rid, "path": path, "name": head, "base": sig, "states": [],
                               "shot": "", "tests": [], "failed": False, "accepted": False, "kinds": {}, "tab": tab0}
                rorder.append(rid)
            r = routes[rid]
            if sig not in nodes:
                kind, label, _ = _state(snap, r["tab"])
                if kind == "base" and head != r["name"]:
                    label = head
                nodes[sig] = {"id": sig, "route": rid, "kind": kind, "label": label, "name": head, "url": urlparse(o["url"]).path or "/",
                              "shot": shot_id, "visits": [], "local": 0, "failed": False, "tests": []}
                order.append(sig)
                r["states"].append(sig)
            n = nodes[sig]
            if not n["shot"] and shot_id:
                n["shot"] = shot_id
            if not r["shot"] and shot_id and sig == r["base"]:
                r["shot"] = shot_id
            action = html._action(o["kind"], o["text"])
            visit = {"test": t["name"], "index": o["index"], "action": action, "dialogs": o.get("dialogs", []),
                     "checks": [html._val(a) for a in by_step.get(o["index"], [])], "shot": shot_id, "failed": o["index"] in failed}
            n["visits"].append(visit)
            for coll in (n["tests"], r["tests"]):
                if t["name"] not in coll:
                    coll.append(t["name"])
            if o["index"] in failed:
                n["failed"] = r["failed"] = True
            if o["index"] in accepted_steps:
                r["accepted"] = True
            if prev is not None:
                if prev == sig:
                    n["local"] += 1
                else:
                    e = sedges.setdefault((prev, sig), {"src": prev, "dst": sig, "actions": [], "tests": []})
                    if (w := _word(action)) not in e["actions"]:
                        e["actions"].append(w)
                    if t["name"] not in e["tests"]:
                        e["tests"].append(t["name"])
                    pr = nodes[prev]["route"]
                    if pr != rid:
                        re_ = redges.setdefault((pr, rid), {"src": pr, "dst": rid, "actions": [], "tests": [], "from": []})
                        if (w := _word(action)) not in re_["actions"]:
                            re_["actions"].append(w)
                        if t["name"] not in re_["tests"]:
                            re_["tests"].append(t["name"])
                        if prev not in re_["from"]:
                            re_["from"].append(prev)
            seq.append({"index": o["index"], "node": sig, "route": rid, "shot": shot_id, "action": _plain(action), "failed": o["index"] in failed})
            prev = sig
        tests.append({"name": t["name"], "title": t["title"], "status": status, "rows": rows, "seq": seq, "side": t.get("side")})
    # 라우트 안 상태 이름: 같은 이름이 여럿이면 번호, 종류별 개수
    for r in routes.values():
        seen: dict[str, int] = defaultdict(int)
        for sid in r["states"]:
            n = nodes[sid]
            if sid == r["base"]:
                n["kind"], n["label"] = "base", ""
            key = f"{n['kind']}:{n['label']}"
            seen[key] += 1
            n["variant"] = seen[key]
            if sid != r["base"]:
                r["kinds"][n["kind"]] = r["kinds"].get(n["kind"], 0) + 1
        if not r["shot"]:
            r["shot"] = next((nodes[s]["shot"] for s in r["states"] if nodes[s]["shot"]), "")
        counts: dict[str, int] = defaultdict(int)
        for sid in r["states"]:
            counts[f"{nodes[sid]['kind']}:{nodes[sid]['label']}"] += 1
        for sid in r["states"]:
            n = nodes[sid]
            if counts[f"{n['kind']}:{n['label']}"] == 1:
                n["variant"] = 0
    # 시작 = 테스트가 실제로 들어가는 첫 화면. 홈(/)으로 들어가는 테스트가 하나라도 있으면 홈이 맨 앞
    entries = [t["seq"][0]["route"] for t in tests if t["seq"]]
    home = next((r for r in entries if routes[r]["path"] == "/"), None)
    start = home or (entries[0] if entries else (rorder[0] if rorder else None))
    return {"app": app, "source": source, "unit": source["unit"], "start": start, "order": order, "rorder": rorder, "nodes": nodes, "routes": routes, "shots": shots,
            "sedges": list(sedges.values()), "redges": list(redges.values()), "tests": tests,
            "target": (run["props"].get("base_url") if run else None), "compared": run is not None, "kind_ko": KIND_KO}


# -- 배치: 열 = 시작에서의 거리, 열 안 순서 = 무게중심 (선 교차 최소화) --------------------------------
def layout(order: list[str], edges: list[dict[str, Any]], start: str | None) -> tuple[dict[str, tuple[int, int]], dict[str, list[str]]]:
    out, inn = defaultdict(list), defaultdict(list)
    for e in edges:
        out[e["src"]].append(e["dst"])
        inn[e["dst"]].append(e["src"])
    depth: dict[str, int] = {}
    if start:
        depth[start] = 0
        queue = [start]
        while queue:
            cur = queue.pop(0)
            for nxt in out[cur]:
                if nxt not in depth:
                    depth[nxt] = depth[cur] + 1
                    queue.append(nxt)
    for n in order:
        depth.setdefault(n, (max(depth.values()) + 1) if depth else 0)
    cols: dict[int, list[str]] = defaultdict(list)
    for n in order:
        cols[depth[n]].append(n)
    ncol = max(cols) + 1 if cols else 0
    row = {n: i for c in range(ncol) for i, n in enumerate(cols[c])}
    for _ in range(4):  # 아래로, 위로 번갈아 정렬
        for c in list(range(1, ncol)) + list(range(ncol - 2, -1, -1)):
            def bary(n: str) -> float:
                nb = [row[m] for m in inn[n] + out[n] if depth.get(m) == (c - 1 if c > 0 else c + 1)]
                return sum(nb) / len(nb) if nb else row[n]
            cols[c].sort(key=lambda n: (bary(n), row[n]))
            for i, n in enumerate(cols[c]):
                row[n] = i
    pos = {n: (c, row[n]) for c in cols for n in cols[c]}
    paths = {}
    if start:  # 시작에서 각 노드까지 최단 경로 (패널의 '오는 길')
        prevs = {start: None}
        queue = [start]
        while queue:
            cur = queue.pop(0)
            for nxt in out[cur]:
                if nxt not in prevs:
                    prevs[nxt] = cur
                    queue.append(nxt)
        for n in prevs:
            p, cur = [], n
            while cur is not None:
                p.append(cur)
                cur = prevs[cur]
            paths[n] = p[::-1]
    return pos, paths


CARD_W, THUMB_H, CARD_H, GAP_X, GAP_Y, PAD = 264, 160, 256, 150, 48, 36
LANE = 34  # 되돌아가는 선 차선 간격


def _tw(s: str) -> int:
    return sum(13 if ord(ch) > 0x2E80 else 7 for ch in s) + 20


MAP_CSS = """
main{max-width:none;padding-block:24px 40px;gap:20px}
:root{--new:#2F6FDE;--new-soft:#E2ECFC}
.pill.new{background:var(--new-soft);color:var(--new)}
.toolbar{display:flex;flex-wrap:wrap;gap:8px;align-items:center}
.toolbar input,.toolbar select{font:inherit;font-size:14px;padding:7px 11px;border:1px solid var(--line);border-radius:8px;background:var(--surface);color:var(--ink)}
.toolbar input{min-width:240px}
/* 경로 고르기 (입력형 목록) */
.pick{position:relative;flex:1 1 280px;max-width:460px}.pick input{width:100%;box-sizing:border-box}.pick.has input{border-color:var(--accent);color:var(--accent);font-weight:600;background:var(--accent-soft)}
.pl{position:absolute;top:100%;left:0;right:0;z-index:30;margin-top:4px;background:var(--surface);border:1px solid var(--line);border-radius:10px;box-shadow:0 12px 32px rgba(15,20,19,.14);max-height:min(52vh,460px);overflow:auto}
.pl button{display:flex;width:100%;align-items:center;gap:10px;text-align:left;padding:8px 12px;border:0;border-bottom:1px solid var(--line);background:none;cursor:pointer;font:inherit;font-size:13.5px;color:var(--ink)}
.pl button:last-child{border-bottom:0}.pl button:hover{background:var(--accent-soft)}.pl button>span:first-child{flex:1;min-width:0}
.pl b{font-weight:600;display:block;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}.pl small{display:block;font:11px var(--mono);color:var(--faint)}
.pl .more{padding:8px 12px;font-size:12px;color:var(--faint)}
.strow{display:flex;align-items:center;gap:10px;flex-wrap:wrap}.strow .kinds{display:inline-flex;gap:4px;flex-wrap:wrap}
.stbtn{font:600 13px var(--sans);padding:6px 12px;border:1px solid var(--accent);border-radius:999px;background:var(--surface);color:var(--accent);cursor:pointer;white-space:nowrap}.stbtn:hover{background:var(--accent);color:#fff}
.mback{display:block;font:600 12.5px var(--sans);color:var(--accent);text-decoration:none;margin-bottom:3px}.mback:hover{text-decoration:underline}
.mh .only{font:600 12px var(--sans);padding:4px 10px;border-radius:999px;border:1px solid var(--line);background:var(--surface);color:var(--muted);cursor:pointer;white-space:nowrap}.mh .only:hover{color:var(--accent);border-color:var(--accent)}
/* 전체화면: 지도만 화면 가득. 검색·목록·칩은 숨기고 떠 있는 막대의 버튼으로 켠다 */
.fsb{font:600 13px var(--sans);padding:7px 12px;border:1px solid var(--line);border-radius:8px;background:var(--surface);color:var(--ink);cursor:pointer;white-space:nowrap}.fsb:hover{border-color:var(--accent);color:var(--accent)}
body:not(.fs) .fsbar{display:none}
body.fs{overflow:hidden}body.fs main{padding:0;gap:0;max-width:none}
body.fs .pgh,body.fs .legend,body.fs .toolbar,body.fs .pane.left,body.fs .chips{display:none}
body.fs #overview{gap:0}body.fs .stage,body.fs .stage.nol{position:fixed;inset:0;height:100vh;min-height:0;grid-template-columns:minmax(0,1fr);gap:0}
body.fs .pane.canvas{border:0;border-radius:0}
body.fs.tools .toolbar{display:flex;flex-wrap:nowrap;position:fixed;top:10px;left:50%;transform:translateX(-50%);z-index:40;background:var(--surface);padding:8px;border:1px solid var(--line);border-radius:12px;box-shadow:0 12px 32px rgba(15,20,19,.16);width:max-content;max-width:min(760px,calc(100vw - 680px))}
body.fs.tools .toolbar .zoom,body.fs.tools .toolbar .fsb{display:none}body.fs.tools .toolbar #q{min-width:220px;width:220px}body.fs.tools .toolbar .pick{flex:none;width:440px;max-width:none}
body.fs.tools .pane.left{display:flex;position:fixed;left:10px;top:10px;bottom:10px;width:300px;z-index:40;box-shadow:0 12px 32px rgba(15,20,19,.16)}
body.fs.tools .pgh{display:flex;position:fixed;top:66px;left:50%;transform:translateX(-50%);z-index:40;background:var(--surface);padding:8px;border:1px solid var(--line);border-radius:12px;box-shadow:0 12px 32px rgba(15,20,19,.16);width:max-content;max-width:min(760px,calc(100vw - 680px))}
body.fs.tools .chips{display:flex;justify-content:center}
@media (max-width:1180px){body.fs.tools .toolbar,body.fs.tools .chips{left:322px;transform:none;max-width:calc(100vw - 660px)}}
@media (max-width:900px){body.fs.tools .toolbar,body.fs.tools .chips{left:322px;max-width:calc(100vw - 340px);flex-wrap:wrap}body.fs.tools .chips{top:auto;bottom:10px}}
.fsbar{position:fixed;top:10px;right:10px;z-index:50;display:flex;gap:6px;align-items:center;background:var(--surface);padding:6px;border:1px solid var(--line);border-radius:12px;box-shadow:0 12px 32px rgba(15,20,19,.16)}
.fsbar button{font:600 13px var(--sans);padding:6px 11px;border:1px solid var(--line);border-radius:8px;background:var(--surface);color:var(--ink);cursor:pointer;white-space:nowrap}
.fsbar button:hover{border-color:var(--accent);color:var(--accent)}.fsbar button.on{background:var(--accent-soft);border-color:var(--accent);color:var(--accent)}
.fsbar .sep{width:1px;height:20px;background:var(--line);margin:0 2px}
body.fs .detail{padding:16px 20px 60px;max-width:none}
.zoom{display:inline-flex;border:1px solid var(--line);border-radius:8px;overflow:hidden;margin-left:auto}
.zoom button{border:0;background:var(--surface);color:var(--ink);font:600 15px/1 var(--sans);padding:9px 13px;min-width:38px;cursor:pointer}
.zoom button+button{border-left:1px solid var(--line)}.zoom button:hover{background:var(--sunk)}

[hidden]{display:none!important}
/* 지도 화면: 왼쪽 목록(접힘 가능) · 지도. 화면을 누르면 상세 페이지로 넘어간다 */
#overview{display:flex;flex-direction:column;gap:20px}
.stage{display:grid;grid-template-columns:260px minmax(0,1fr);gap:14px;align-items:stretch;height:calc(100vh - 230px);min-height:560px}
.stage.nol{grid-template-columns:48px minmax(0,1fr)}
.pane{background:var(--surface);border:1px solid var(--line);border-radius:12px;min-height:0;display:flex;flex-direction:column;overflow:hidden}
.pane>h2{display:flex;align-items:center;justify-content:space-between;gap:8px;font-size:12.5px;font-weight:600;letter-spacing:.06em;color:var(--muted);padding:10px 10px 8px 14px;border-bottom:1px solid var(--line);margin:0}
.ib{border:1px solid var(--line);background:var(--surface);color:var(--muted);border-radius:6px;width:26px;height:26px;cursor:pointer;font:600 14px var(--sans);display:grid;place-items:center;flex:none}
.ib:hover{background:var(--sunk);color:var(--ink)}
.stage.nol .pane.left>h2{padding:10px 0;justify-content:center;border-bottom:0}.stage.nol .pane.left>h2 span,.stage.nol .pane.left .list{display:none}
.list{overflow:auto;padding:6px}
.row{display:grid;grid-template-columns:10px 1fr auto;gap:10px;align-items:center;padding:8px;border-radius:8px;cursor:pointer;border:0;background:none;text-align:left;font:inherit;color:inherit;width:100%}
.row:hover{background:var(--sunk)}.row.sel{background:var(--accent-soft)}
.row .dot{width:10px;height:10px;border-radius:50%;background:var(--line);visibility:hidden}.row .dot.ok,.row .dot.bad,.row .dot.undev,.row .dot.new,.row .dot.accepted,.row .dot.unreached{visibility:visible}.row .dot.ok{background:var(--ok)}.row .dot.bad{background:var(--bad)}.row .dot.undev{background:var(--warn)}.row .dot.new{background:var(--new)}.row .dot.accepted{background:var(--accent)}
.row .nm{font-size:14px;font-weight:600;line-height:1.25;min-width:0}.row .nm small{display:block;font:400 12px var(--mono);color:var(--muted);margin-top:1px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.row .ct{font:12px var(--mono);color:var(--faint);text-align:right;line-height:1.3}.row.dim{opacity:.35}

/* 지도 */
.canvas{position:relative;overflow:auto;cursor:grab;background:radial-gradient(circle at 1px 1px, var(--line) 1px, transparent 0) 0 0/24px 24px, var(--bg)}
.canvas.drag{cursor:grabbing;user-select:none}
.canvas svg.links{position:absolute;left:0;top:0;overflow:visible}
.edge path{fill:none;stroke:var(--accent);stroke-width:2;transition:opacity .15s}
.edge.back path{stroke:var(--faint);stroke-dasharray:6 5}
.edge .pill{fill:var(--surface);stroke:var(--line)}
.edge text{font-size:12.5px;font-weight:600;fill:var(--ink)}
body.nolabels .edge .pill,body.nolabels .edge text{display:none}
.edge.merged{display:none}body.nolabels .edge.merged{display:inline}body.nolabels .edge.back:not(.merged){display:none}
.fsb.lbt.on,.fsbar .lbt.on{background:var(--accent);border-color:var(--accent);color:#fff}
.edge.dim{opacity:.12}.edge.hot path{stroke-width:3}.edge.hot .pill{stroke:var(--accent)}
.node{position:absolute;width:__CW__px;height:__CH__px;border:1.5px solid var(--line);border-radius:12px;background:var(--surface);cursor:pointer;display:flex;flex-direction:column;
 text-align:left;font:inherit;color:inherit;padding:0;overflow:hidden;box-shadow:0 1px 2px rgba(15,20,19,.06);transition:box-shadow .15s,opacity .15s}
.node:hover{box-shadow:0 6px 18px rgba(15,20,19,.12);border-color:var(--accent)}
.node.sel{border-color:var(--accent);box-shadow:0 0 0 3px var(--accent-soft),0 6px 18px rgba(15,20,19,.12)}
.node.dim{opacity:.28}
.node .strip{height:5px;background:var(--line)}.node.ok .strip{background:var(--ok)}.node.bad .strip{background:var(--bad)}.node.undev .strip{background:var(--warn)}.node.new .strip{background:var(--new)}.node.accepted .strip{background:var(--accent)}
.node.bad{border-color:var(--bad)}.node.undev{border-color:var(--warn)}.node.new{border-color:var(--new)}.node.ok{border-color:var(--ok)}.node.accepted{border-color:var(--accent)}
.node.unreached{border-style:dashed;border-color:var(--faint);background:var(--sunk)}.node.unreached .strip{background:var(--faint)}.node.unreached .tag{background:var(--faint)}
.row .dot.unreached{background:var(--faint)}.legend i.unreached{background:var(--faint)}.pill.unreached{background:var(--sunk);color:var(--muted)}
.node .chrome{display:flex;gap:4px;padding:6px 10px 0}.node .chrome i{width:7px;height:7px;border-radius:50%;background:var(--line);display:block}
.node .th{margin:5px 8px 0;height:__TH__px;border-radius:6px;overflow:hidden;background:#fff;border:1px solid var(--line);position:relative}
.node .th img{width:150%;max-width:none;display:block}
.node .th .no{position:absolute;inset:0;display:grid;place-items:center;color:var(--faint);font-size:12px}
.node .body{padding:8px 10px 0;display:flex;flex-direction:column;gap:3px;min-height:0}
.node .nm{font-size:15px;font-weight:700;line-height:1.25;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.node .nm em{font-style:normal;font-weight:500;color:var(--muted);font-size:12.5px;margin-left:4px}
.node .kinds{display:flex;gap:4px;flex-wrap:nowrap;overflow:hidden;min-height:18px}
.kd{font:600 11px var(--sans);padding:1px 7px;border-radius:4px;background:var(--sunk);color:var(--muted);white-space:nowrap}
.kd.dialog{background:var(--accent-soft);color:var(--accent)}.kd.drawer{background:var(--warn-soft);color:var(--warn)}.kd.tab{background:var(--sunk);color:var(--ink)}.kd.alert{background:var(--bad-soft);color:var(--bad)}
.node .meta{margin-top:auto;display:flex;justify-content:space-between;align-items:center;padding:0 10px 8px;font:11.5px var(--mono);color:var(--faint)}
.node .meta b{font-family:var(--sans);font-weight:600;color:var(--muted)}
.node .tag{position:absolute;top:12px;left:10px;font-size:11px;font-weight:700;letter-spacing:.04em;background:var(--accent);color:#fff;border-radius:4px;padding:1px 7px}
.node.bad .tag{background:var(--bad)}.node.undev .tag{background:var(--warn)}.node.new .tag{background:var(--new)}.node.accepted .tag{background:var(--accent)}
.detail .capsw{position:absolute;left:10px;top:10px;z-index:2;display:inline-flex;border:1px solid var(--line);border-radius:8px;overflow:hidden;background:var(--surface);box-shadow:0 4px 14px rgba(15,20,19,.18);opacity:0;transition:opacity .15s}
.detail .prev:hover .capsw,.detail .prev:focus-within .capsw{opacity:1}.detail .capsw button{border:0;background:var(--surface);color:var(--muted);font:600 12.5px var(--sans);padding:5px 12px;cursor:pointer}
.detail .capsw button+button{border-left:1px solid var(--line)}.detail .capsw button.on{background:var(--accent-soft);color:var(--accent)}

/* 상세 페이지: 화면 하나. 지도로 돌아가는 링크, 큰 캡처 + 화면 안의 상태 그래프 | 오는 길·가는 길·테스트 */
.detail{padding:0 0 30px}
.detail .dh{display:flex;align-items:flex-start;gap:18px;padding:4px 0 16px;border-bottom:1px solid var(--line);margin-bottom:20px}
.detail .back{font-size:13.5px;font-weight:600;color:var(--accent);text-decoration:none;border:1px solid var(--line);border-radius:8px;padding:7px 13px;white-space:nowrap;background:var(--surface);margin-top:4px}
.detail .back:hover{border-color:var(--accent);background:var(--accent-soft)}
.detail h3{font-size:24px;font-weight:700;line-height:1.25;margin:0}
.detail .sub{display:flex;flex-wrap:wrap;gap:6px 12px;align-items:center;font:13px var(--mono);color:var(--muted);margin-top:6px}
.dgrid{display:grid;grid-template-columns:minmax(0,1.25fr) minmax(320px,.9fr);gap:26px;align-items:start}
.dgrid .col{display:flex;flex-direction:column;gap:20px;min-width:0}
.detail .prev{border:1px solid var(--line);border-radius:12px;overflow:hidden;background:#fff;cursor:zoom-in;position:relative}
.detail .prev img{width:100%;display:block}
.detail .prev .cap{position:absolute;left:10px;bottom:10px;font:600 12.5px var(--sans);background:rgba(15,20,19,.75);color:#fff;border-radius:5px;padding:3px 9px}
.detail .empty{padding:60px 0;color:var(--faint);text-align:center;border:1px dashed var(--line);border-radius:12px}
.sec h4{margin:0 0 8px;font-size:12.5px;font-weight:600;letter-spacing:.06em;color:var(--muted);display:flex;justify-content:space-between}
.sec h4 span{font-weight:400;letter-spacing:0}
/* 화면 안의 상태: 작은 그래프 */
.mini{position:relative;overflow:auto;border:1px solid var(--line);border-radius:10px;background:radial-gradient(circle at 1px 1px, var(--line) 1px, transparent 0) 0 0/16px 16px, var(--bg)}
.mini svg{position:absolute;left:0;top:0;overflow:visible}
.mini .medge path{fill:none;stroke:var(--accent);stroke-width:1.5}.mini .medge.back path{stroke:var(--faint);stroke-dasharray:4 4}
.mini .medge rect{fill:var(--surface);stroke:var(--line)}.mini .medge text{font-size:10.5px;font-weight:600;fill:var(--ink)}
.mnode{position:absolute;width:__MW__px;height:__MH__px;border:1.5px solid var(--line);border-radius:8px;background:var(--surface);cursor:pointer;padding:0;text-align:left;font:inherit;color:inherit;overflow:hidden;display:flex;flex-direction:column}
.mnode:hover{border-color:var(--accent)}.mnode.sel{border-color:var(--accent);box-shadow:0 0 0 2px var(--accent-soft)}.mnode.bad{border-color:var(--bad)}
.mnode .th{height:__MT__px;background:#fff;overflow:hidden;border-bottom:1px solid var(--line)}.mnode .th img{width:140%;max-width:none;display:block}
.mnode .lab{padding:4px 7px;display:flex;flex-direction:column;gap:1px;min-width:0}
.mnode .lab b{font-size:12px;line-height:1.25;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.mnode .lab small{font:11px var(--mono);color:var(--faint)}
/* 오는 길·가는 길: 같은 모양의 한 줄 — [동작] 화면 이름 · 경로 */
.routes{display:flex;flex-direction:column;gap:4px}
.route{display:grid;grid-template-columns:auto minmax(0,1fr) auto;gap:10px;align-items:center;padding:6px 10px;border:1px solid var(--line);border-radius:8px;
 color:var(--ink);text-decoration:none;cursor:pointer;font-size:13.5px;background:var(--surface)}
.route:hover{border-color:var(--accent);background:var(--accent-soft)}
.route.here{border-color:var(--accent);background:var(--accent-soft);cursor:default}
.route .act{font-size:11.5px;font-weight:600;color:var(--accent);background:var(--accent-soft);border-radius:99px;padding:1px 8px;white-space:nowrap;max-width:130px;overflow:hidden;text-overflow:ellipsis}
.route.here .act{background:var(--accent);color:#fff}
.route .nm{font-weight:600;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}.route .nm em{font-style:normal;font-weight:500;color:var(--muted);font-size:12px;margin-left:3px}
.route .path{font:11.5px var(--mono);color:var(--faint);white-space:nowrap;max-width:150px;overflow:hidden;text-overflow:ellipsis}
/* 테스트 목록: 행만, 누르면 팝업 */
.trows{display:flex;flex-direction:column;gap:4px}
.trow{display:grid;grid-template-columns:minmax(0,1fr) auto auto;gap:10px;align-items:center;padding:8px 10px;border:1px solid var(--line);border-radius:8px;background:var(--surface);
 cursor:pointer;text-align:left;font:inherit;color:inherit}
.trow:hover{border-color:var(--accent);background:var(--accent-soft)}.trow.fail{border-left:3px solid var(--bad)}
.trow b{font-size:13.5px;line-height:1.3;display:block}.trow small{display:block;font:11.5px var(--mono);color:var(--faint);margin-top:1px}
.trow .chev{color:var(--faint);font-size:16px}
/* 팝업: 시나리오 상세 */
.modal{position:fixed;inset:0;background:rgba(15,20,19,.55);display:none;place-items:center;z-index:40;padding:24px}
.modal.on{display:grid}
.modal .box{background:var(--surface);color:var(--ink);border-radius:14px;width:min(960px,96vw);max-height:88vh;display:flex;flex-direction:column;overflow:hidden;box-shadow:0 30px 80px rgba(0,0,0,.4)}
.modal .mh{display:grid;grid-template-columns:1fr auto auto;gap:12px;align-items:center;padding:14px 18px;border-bottom:1px solid var(--line)}
.modal .mh b{font-size:16px}.modal .mh small{display:block;font:12px var(--mono);color:var(--faint)}
.modal .mb{overflow:auto;padding:0 18px 18px}
.modal .sb{display:flex;flex-direction:column;gap:16px;padding-top:16px}
.modal .sb .prev{flex:none;border:1px solid var(--line);border-radius:10px;overflow:hidden;background:#fff;cursor:zoom-in;position:relative;max-height:52vh}
.modal .sb .sec,.modal .sb .sgrid{flex:none}
.modal .sb .prev img{width:100%;display:block;max-height:52vh;object-fit:contain;object-position:top}
.modal .sb .prev .cap{position:absolute;left:10px;bottom:10px;font:600 12.5px var(--sans);background:rgba(15,20,19,.75);color:#fff;border-radius:5px;padding:3px 9px}
.modal .sgrid{display:grid;grid-template-columns:1fr 1fr;gap:16px}
@media (max-width:760px){.modal .sgrid{grid-template-columns:1fr}}
.film{display:flex;gap:6px;overflow-x:auto;padding:14px 0 10px;scroll-snap-type:x proximity}
.film button{flex:none;width:96px;border:2px solid var(--line);border-radius:6px;padding:0;background:#fff;cursor:pointer;position:relative;scroll-snap-align:start;overflow:hidden}
.film button img{width:100%;height:60px;object-fit:cover;object-position:top left;display:block}
.film button .k{position:absolute;left:3px;top:3px;font:600 10.5px var(--mono);background:rgba(15,20,19,.7);color:#fff;border-radius:3px;padding:0 4px}
.film button.here{border-color:var(--accent)}.film button.fail{border-color:var(--bad)}.film button:hover{border-color:var(--ink)}
.film button .cap{display:block;font-size:10.5px;color:var(--muted);padding:2px 4px;text-align:left;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;background:var(--surface)}
.steps{border:1px solid var(--line);border-radius:10px;overflow:hidden}
.visit{display:grid;grid-template-columns:auto 1fr;gap:10px;padding:10px 12px;border-top:1px solid var(--line);font-size:13.5px;align-items:start}
.visit:first-child{border-top:0}.visit.fail{background:var(--bad-soft)}.visit.here{background:var(--accent-soft)}.visit.here.fail{background:var(--bad-soft)}
.visit .no{width:24px;height:24px;border-radius:50%;border:1.5px solid var(--accent);color:var(--accent);font:600 12px/22px var(--mono);text-align:center}
.visit.fail .no{border-color:var(--bad);color:var(--bad)}
.visit .dlg{margin-top:4px;font-size:12.5px;color:var(--muted)}.visit .ck{margin-top:6px;display:flex;flex-wrap:wrap;gap:4px}
.dtab{margin-top:14px;overflow-x:auto}.dtab table{font-size:13px}
.lb{position:fixed;inset:0;background:rgba(15,20,19,.82);display:none;place-items:center;z-index:50;padding:24px;cursor:zoom-out}
.lb.on{display:grid}.lb img{max-width:min(96vw,1280px);max-height:80vh;border-radius:8px;background:#fff;box-shadow:0 20px 60px rgba(0,0,0,.5)}
.lb .cap{color:#fff;margin-top:12px;font-size:14px;text-align:center}
@media (max-width:1180px){.stage,.stage.nol{grid-template-columns:220px minmax(0,1fr);height:auto}.canvas{height:60vh}}
@media (max-width:1000px){.dgrid{grid-template-columns:1fr}}
@media (max-width:760px){.stage,.stage.nol{grid-template-columns:1fr}.pane.left{max-height:40vh}.detail .dh{flex-direction:column;gap:10px}}
/* 창 높이에 맞춤: 지도도 상세도 페이지 스크롤 없이. 상세의 목록은 5개까지 보이고 그 안에서 스크롤 */
main{height:100%;display:flex;flex-direction:column;overflow:hidden;padding-block:14px 14px;gap:12px}
#overview{flex:1;min-height:0}.toolbar{flex:none}
.stage,.stage.nol{flex:1;min-height:0;height:auto}
.detail{flex:1;min-height:0;display:flex;flex-direction:column;padding:0}
.detail .dh{align-items:center;gap:14px;padding:0 0 10px;margin-bottom:12px;flex:none}
.detail .dh>div{display:flex;align-items:baseline;gap:6px 14px;flex-wrap:wrap;min-width:0}
.detail h3{font-size:19px}.detail .sub{margin-top:0}
.detail .back{padding:5px 11px;margin-top:0}
.dgrid{flex:1;min-height:0;align-items:stretch}
.dgrid .col{min-height:0;overflow:auto;gap:16px;padding-right:2px}
.detail .sec{flex:none}
.routes{max-height:214px;overflow:auto;padding-right:2px}.trows{max-height:292px;overflow:auto;padding-right:2px}
""".replace("__CW__", str(CARD_W)).replace("__CH__", str(CARD_H)).replace("__TH__", str(THUMB_H)).replace("__MW__", "150").replace("__MH__", "116").replace("__MT__", "72")

MAP_JS = r"""
const $ = s => root.querySelector(s), $$ = s => root.querySelectorAll(s);
const G = JSON.parse($('#g').textContent);
const esc = s => String(s ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const shot = id => G.shots[id] || '';
const routeName = rid => esc(G.routes[rid].name);
const stateName = sid => { const n = G.nodes[sid]; const base = n.kind === 'base' ? (n.label || '기본') : `${G.kind_ko[n.kind]} · ${n.label}`; return esc(base) + (n.variant ? ` ${n.variant}` : ''); };
const stage = $('#stage'), overview = $('#overview'), detail = $('#detail'), lb = $('#lb'), modal = $('#modal');
const testByName = Object.fromEntries(G.tests.map(t => [t.name, t]));
const redgeBetween = (a, b) => G.redges.find(e => e.src === a && e.dst === b);
let current = null, currentState = null;

const SCLS = {diff:'bad', undeveloped:'undev', new:'new', accepted:'accepted', same:'ok', untested:'', unreached:'unreached'};
function rstatus(r){ return SCLS[r.status] || ''; }
function statusPill(r){ const c = SCLS[r.status]; return c ? `<span class="pill ${c === 'undev' || c === 'accepted' ? 'warn' : c}">${esc(G.status_ko[r.status])}</span>` : ''; }
function openLb(src, cap){ if(!src) return; lb.querySelector('img').src = src; lb.querySelector('.cap').textContent = cap || ''; lb.classList.add('on'); }
lb.addEventListener('click', () => lb.classList.remove('on'));
function closeModal(){ modal.classList.remove('on'); currentState = null; }
modal.addEventListener('click', e => { if(e.target === modal) closeModal(); });
ctx.listen(window, 'keydown', e => { if(e.key !== 'Escape') return; if(lb.classList.contains('on')) lb.classList.remove('on'); else if(modal.classList.contains('on')) closeModal(); else if(!detail.hidden) back(); else if(root.classList.contains('fs') && !document.fullscreenElement) exitFs(); });

const routeRow = (rid, act, here) => `<a class="route ${here ? 'here' : ''}" ${here ? '' : `data-go="${rid}"`}><span class="act">${esc(act)}</span><span class="nm">${routeName(rid)}</span><span class="path">${esc(G.routes[rid].path)}</span></a>`;

/* 화면 안의 상태 그래프: 열 = 기본 상태에서 몇 번 눌러 가는지 */
const MW = 150, MH = 116, MGX = 64, MGY = 14, MP = 12;
function miniGraph(rid){
  const r = G.routes[rid], ids = r.states, set = new Set(ids);
  const es = G.sedges.filter(e => set.has(e.src) && set.has(e.dst) && e.src !== e.dst);
  const out = {}; es.forEach(e => (out[e.src] = out[e.src] || []).push(e.dst));
  const depth = {[r.base]: 0}, q = [r.base];
  while(q.length){ const c = q.shift(); for(const n of (out[c] || [])) if(!(n in depth)){ depth[n] = depth[c] + 1; q.push(n); } }
  ids.forEach(s => { if(!(s in depth)) depth[s] = Math.max(0, ...Object.values(depth)) + 1; });
  const cols = {}; ids.forEach(s => (cols[depth[s]] = cols[depth[s]] || []).push(s));
  const pos = {}; Object.keys(cols).forEach(c => cols[c].forEach((s, i) => pos[s] = [MP + c * (MW + MGX), MP + i * (MH + MGY)]));
  const ncol = Object.keys(cols).length, nrow = Math.max(...Object.values(cols).map(a => a.length));
  const backs = es.filter(e => depth[e.dst] <= depth[e.src]);
  const bodyH = MP + nrow * MH + (nrow - 1) * MGY;
  const W = MP * 2 + ncol * MW + (ncol - 1) * MGX, H = bodyH + MP + backs.length * 22;
  let lane = 0;
  const paths = es.map(e => {
    const [x1, y1] = pos[e.src], [x2, y2] = pos[e.dst];
    const label = e.actions[0].slice(0, 14) + (e.actions.length > 1 ? ` +${e.actions.length - 1}` : '');
    let d, mx, my, cls = '';
    if(depth[e.dst] > depth[e.src]){ const sx = x1 + MW, sy = y1 + MH / 2, tx = x2, ty = y2 + MH / 2; d = `M${sx},${sy} C${sx + MGX * .5},${sy} ${tx - MGX * .5},${ty} ${tx},${ty}`; mx = (sx + tx) / 2; my = (sy + ty) / 2; }
    else { lane++; const ly = bodyH + lane * 22 - 8, sx = x1 + MW * .5, tx = x2 + MW * .5; d = `M${sx},${y1 + MH} L${sx},${ly} L${tx},${ly} L${tx},${y2 + MH + 4}`; mx = (sx + tx) / 2; my = ly; cls = 'back'; }
    const tw = label.length * 7 + 14;
    return `<g class="medge ${cls}"><path d="${d}" marker-end="url(#${cls ? 'marrb' : 'marr'})"/><rect x="${mx - tw / 2}" y="${my - 9}" width="${tw}" height="18" rx="9"/><text x="${mx}" y="${my + 4}" text-anchor="middle">${esc(label)}</text></g>`;
  }).join('');
  const svg = `<svg width="${W}" height="${H}" viewBox="0 0 ${W} ${H}"><defs><marker id="marr" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto"><path d="M0,0 L10,5 L0,10 z" fill="var(--accent)"/></marker><marker id="marrb" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto"><path d="M0,0 L10,5 L0,10 z" fill="var(--faint)"/></marker></defs>${paths}</svg>`;
  const cards = ids.map(s => { const n = G.nodes[s], [x, y] = pos[s];
    return `<button type="button" class="mnode ${n.failed && G.compared ? 'bad' : ''} ${s === currentState ? 'sel' : ''}" data-state="${s}" style="left:${x}px;top:${y}px" title="${stateName(s)}"><div class="th">${n.shot ? `<img src="${shot(n.shot)}" alt="">` : ''}</div><div class="lab"><b>${stateName(s)}</b><small>${G.unit} ${n.tests.length}${n.failed && G.compared ? ' · 다름' : ''}</small></div></button>`; }).join('');
  return `<div class="mini" style="height:${Math.min(H, 520)}px"><div style="position:relative;width:${W}px;height:${H}px">${svg}${cards}</div></div>`;
}

function testRows(names, failedIn){
  return `<div class="trows">` + names.map(t => { const test = testByName[t], failedHere = failedIn(t);
    const pill = test.status === 'fail' ? '<span class="pill bad">다름</span>' : test.status === 'accepted' ? '<span class="pill warn">승인된 차이</span>' : test.status === 'pass' ? '<span class="pill ok">같음</span>' : test.side ? `<span class="pill ${test.side === 'tobe' ? 'new' : ''}">${test.side === 'asis' ? 'as-is 탐색' : 'to-be 탐색'}</span>` : '<span class="pill">기록</span>';
    return `<button type="button" class="trow ${failedHere ? 'fail' : ''}" data-test="${esc(t)}"><span><b>${esc(test.title)}</b><small>${esc(t)}</small></span>${pill}<span class="chev">›</span></button>`; }).join('') + `</div>`;
}

/* 상세 페이지: 화면 하나를 통째로 본다. 주소는 #<라우트>[/<상태>] — 브라우저 뒤로가기로 지도에 돌아간다 */
function show(rid, sid){
  const r = G.routes[rid]; if(!r) return;
  current = rid; currentState = sid && r.states.includes(sid) ? sid : null;
  overview.hidden = true; detail.hidden = false;
  const st = rstatus(r), path = G.paths[rid] || [rid], outs = G.redges.filter(e => e.src === rid);
  const focus = currentState ? G.nodes[currentState] : (G.nodes[r.base] || {shot: '', visits: [], tests: []});  // 코드에만 있는 주소는 상태가 없다
  const kinds = Object.entries(r.kinds).map(([k, n]) => `<span class="kd ${k}">${G.kind_ko[k]} ${n}</span>`).join('');
  let h = `<div class="dh"><a class="back" href="#" data-back>← 지도</a><div><h3>${routeName(rid)}${currentState ? ` <span style="color:var(--muted);font-weight:500">› ${stateName(currentState)}</span>` : ''}</h3><div class="sub"><span>${esc(r.path)}</span>`
    + statusPill(r)
    + `<span>${G.unit} ${r.tests.length}개</span><span>상태 ${r.states.length}</span>${kinds}</div></div></div><div class="dgrid"><div class="col">`;
  // 기본 상태의 캡처: 비교 지도는 to-be 우선(미개발만 as-is). 둘 다 있으면 as-is/to-be 를 바꿔 볼 수 있다
  const both = !currentState && r.asis_shot && r.tobe_shot && r.asis_shot !== r.tobe_shot;
  const mainShot = currentState ? focus.shot : (r.shot || focus.shot), mainCap = currentState ? stateName(focus.id) : (r.tobe_shot && r.shot === r.tobe_shot ? 'to-be' : 'as-is');
  // as-is/to-be 캡처 전환은 캡처 왼쪽 위에 겹쳐 두고 호버할 때만 보인다
  const sw = both ? `<div class="capsw"><button type="button" data-side="${r.tobe_shot}" data-capn="to-be" class="${mainShot === r.tobe_shot ? 'on' : ''}">to-be 캡처</button><button type="button" data-side="${r.asis_shot}" data-capn="as-is" class="${mainShot === r.asis_shot ? 'on' : ''}">as-is 캡처</button></div>` : '';
  h += mainShot ? `<div class="prev" title="크게 보기" data-lb="${mainShot}" data-cap="${mainCap}"><img src="${shot(mainShot)}" alt="${routeName(rid)} 화면">${sw}<span class="cap">${mainCap}</span></div>` : `<div class="empty">${r.status === 'unreached' ? '닿지 못한 화면 · 캡처 없음' : '캡처 없음'}</div>`;
  if(r.status === 'unreached') h += `<div class="sec"><h4>코드 위치 <span>${(r.evidence || []).length}곳${r.methods && r.methods.length ? ' · ' + r.methods.join(',') : ''}</span></h4><div class="tid">${(r.evidence || []).map(esc).join('<br>')}</div>`
    + `<p class="tid" style="margin-top:8px">소스 코드가 선언한 주소인데 어떤 탐색 경로·시나리오도 여기에 닿지 않았습니다. 막은 것이 무엇인지 확인하세요: 픽스처 값이 없는 입력칸, 금지 목록(deny)에 걸린 버튼, 로그인·권한, 특정 데이터가 있어야 보이는 화면, 탐색 예산(상태·동작·깊이). 그래도 갈 수 없는 화면이면 소스에서 실제로 쓰이는지 봅니다.</p></div>`;
  else if(r.states.length > 1) h += `<div class="sec"><h4>이 화면 안의 상태 <span>${r.states.length}개</span></h4><div class="strow"><span class="kinds">${kinds}</span><button type="button" class="stbtn" data-states>상태 그래프 보기 ›</button></div></div>`;
  else h += `<div class="sec"><h4>이 화면 안의 상태</h4><div class="tid">기본 상태뿐 (팝업·드로워·탭 없음)</div></div>`;
  h += `</div><div class="col">`;
  if(G.paths[rid]) h += `<div class="sec"><h4>시작에서 오는 길 <span>${path.length - 1}번 이동</span></h4><div class="routes">`
    + path.map((p, i) => { const e = i ? redgeBetween(path[i - 1], p) : null; return routeRow(p, i ? (e ? e.actions[0] : '…') : '시작', p === rid); }).join('') + `</div></div>`;
  else h += `<div class="sec"><h4>시작에서 오는 길</h4><div class="tid">시작 화면에서 이어지는 길이 기록에 없음 (${G.unit}가 이 주소로 바로 들어감)</div></div>`;
  h += `<div class="sec"><h4>여기서 갈 수 있는 곳 <span>${outs.length}곳</span></h4><div class="routes">`
    + (outs.map(e => routeRow(e.dst, e.actions.join(' · '), false)).join('') || '<span class="tid">없음</span>') + `</div></div>`;
  const scope = currentState ? G.nodes[currentState] : null;
  const names = (scope ? scope.tests : r.tests).slice().sort((a, b) => ((testByName[b].status === 'fail') - (testByName[a].status === 'fail')));
  const failedIn = t => (scope ? scope.visits : r.states.flatMap(s => G.nodes[s].visits)).some(v => v.test === t && v.failed);
  h += `<div class="sec"><h4>${scope ? `이 상태를 지나는 ${G.unit}` : `이 화면을 지나는 ${G.unit}`} <span>${names.length}개 · 누르면 단계</span></h4>${testRows(names, failedIn)}</div>`;
  h += `</div></div>`;
  detail.innerHTML = h;
  detail.querySelector('[data-back]').addEventListener('click', e => { e.preventDefault(); back(); });
  detail.querySelectorAll('[data-go]').forEach(a => a.addEventListener('click', () => go(a.dataset.go)));
  detail.querySelectorAll('[data-lb]').forEach(a => a.addEventListener('click', () => openLb(shot(a.dataset.lb), a.dataset.cap || r.name)));
  detail.querySelectorAll('[data-test]').forEach(b => b.addEventListener('click', () => openTest(b.dataset.test, rid)));
  detail.querySelectorAll('[data-state]').forEach(b => b.addEventListener('click', () => openState(rid, b.dataset.state)));
  detail.querySelectorAll('[data-states]').forEach(b => b.addEventListener('click', () => openStates(rid)));
  detail.querySelectorAll('[data-side]').forEach(b => b.addEventListener('click', e => { e.stopPropagation(); const pv = detail.querySelector('.prev'); if(!pv) return; pv.querySelector('img').src = shot(b.dataset.side); pv.querySelector('.cap').textContent = b.dataset.capn; pv.dataset.lb = b.dataset.side; pv.dataset.cap = b.dataset.capn; detail.querySelectorAll('[data-side]').forEach(x => x.classList.toggle('on', x === b)); }));
  root.scrollTo({top: 0});
}
function back(){ if(ctx.getSub()) ctx.setSub(''); else showOverview(); }
function showOverview(){
  current = null; currentState = null;
  detail.hidden = true; overview.hidden = false;
  $$('.node.sel, .row.sel').forEach(el => el.classList.remove('sel'));
  fit();
}
function route(){
  const [h0, h1] = (ctx.getSub() || '').split('/').map(x => { try { return decodeURIComponent(x); } catch(e) { return x; } });  // 라우트 id에 /가 있어 먼저 나누고 푼다. 통합 화면에서는 해시의 뒷부분
  if(G.routes[h0]){ const keep = detail.querySelector('.mini') ? detail.querySelector('.mini').scrollLeft : 0; show(h0, h1 || null); const m = detail.querySelector('.mini'); if(m) m.scrollLeft = keep; }
  else showOverview();
}
/* 시나리오 팝업: 필름스트립 + 단계 + (다르면) 무엇이 달랐는지 */
/* 상태 팝업: 화면 안의 상태 하나 (팝업·드로워·탭·알림). 큰 캡처, 여기로 오는 동작, 여기서 가는 상태, 지나는 테스트(누르면 시나리오 팝업으로 바뀐다) */
/* 상태 그래프 팝업: 화면 안의 상태를 그래프로. 상태를 누르면 같은 팝업이 그 상태의 상세로 바뀌고, ← 로 그래프에 돌아온다 */
function openStates(rid){
  const r = G.routes[rid]; if(!r) return;
  currentState = null;
  modal.querySelector('.box').innerHTML = `<div class="mh"><div><b>${routeName(rid)} › 화면 안의 상태</b><small>${esc(r.path)} · ${r.states.length}개 · 상태를 누르면 그 상태의 캡처·동작·${G.unit}</small></div><span></span><button class="ib" type="button" id="closeModal" aria-label="닫기">×</button></div><div class="mb">${miniGraph(rid)}</div>`;
  modal.classList.add('on');
  modal.querySelector('#closeModal').addEventListener('click', closeModal);
  modal.querySelectorAll('[data-state]').forEach(b => b.addEventListener('click', () => openState(rid, b.dataset.state, true)));
  modal.querySelector('.mb').scrollTop = 0;
}
function openState(rid, sid, fromGraph){
  const n = G.nodes[sid], r = G.routes[rid]; if(!n || !r) return;
  currentState = sid;  // 시나리오 팝업에서 이 상태의 단계를 강조하기 위해
  const ins = G.sedges.filter(e => e.dst === sid && e.src !== sid), outs = G.sedges.filter(e => e.src === sid && e.dst !== sid);
  const names = n.tests.slice().sort((a, b) => ((testByName[b].status === 'fail') - (testByName[a].status === 'fail')));
  const failedIn = t => n.visits.some(v => v.test === t && v.failed);
  const kindPill = n.kind === 'base' ? '<span class="kd">기본</span>' : `<span class="kd ${n.kind}">${G.kind_ko[n.kind]}</span>`;
  const link = (e, other) => `<a class="route" data-state-go="${other}"><span class="act">${esc(e.actions.join(' · '))}</span><span class="nm">${stateName(other)}</span><span class="path">${esc(G.nodes[other].kind === 'base' ? '기본' : G.kind_ko[G.nodes[other].kind])}</span></a>`;
  let h = `<div class="mh"><div>${fromGraph ? '<a class="mback" href="#" data-graph>← 상태 그래프</a>' : ''}<b>${routeName(rid)} › ${stateName(sid)}</b><small>${esc(r.path)} · ${G.unit} ${n.tests.length}개${n.failed && G.compared ? ' · as-is와 다름' : ''}</small></div>${kindPill}<button class="ib" type="button" id="closeModal" aria-label="닫기">×</button></div><div class="mb sb">`;
  h += n.shot ? `<div class="prev" title="크게 보기" data-lb="${n.shot}" data-cap="${stateName(sid)}"><img src="${shot(n.shot)}" alt="${stateName(sid)}"><span class="cap">${stateName(sid)}</span></div>` : `<div class="empty">캡처 없음</div>`;
  h += `<div class="sgrid"><div class="sec"><h4>이 상태로 오는 동작 <span>${ins.length}</span></h4><div class="routes">${ins.map(e => link(e, e.src)).join('') || '<span class="tid">기록에 없음 (이 상태로 바로 들어감)</span>'}</div></div>`
     + `<div class="sec"><h4>여기서 가는 상태 <span>${outs.length}</span></h4><div class="routes">${outs.map(e => link(e, e.dst)).join('') || '<span class="tid">없음</span>'}</div></div></div>`;
  h += `<div class="sec"><h4>이 상태를 지나는 ${G.unit} <span>${names.length}개 · 누르면 단계</span></h4>${testRows(names, failedIn)}</div></div>`;
  modal.querySelector('.box').innerHTML = h;
  modal.classList.add('on');
  modal.querySelector('#closeModal').addEventListener('click', closeModal);
  modal.querySelectorAll('[data-lb]').forEach(b => b.addEventListener('click', () => openLb(shot(b.dataset.lb), b.dataset.cap)));
  modal.querySelectorAll('[data-state-go]').forEach(a => a.addEventListener('click', () => openState(rid, a.dataset.stateGo, fromGraph)));
  modal.querySelectorAll('[data-graph]').forEach(a => a.addEventListener('click', e => { e.preventDefault(); openStates(rid); }));
  modal.querySelectorAll('[data-test]').forEach(b => b.addEventListener('click', () => openTest(b.dataset.test, rid)));
  modal.querySelector('.mb').scrollTop = 0;
}
function openTest(name, hereRid){
  const test = testByName[name]; if(!test) return;
  const hereSteps = new Set(test.seq.filter(s => currentState ? s.node === currentState : s.route === hereRid).map(s => s.index));
  const pill = test.status === 'fail' ? '<span class="pill bad">as-is와 다름</span>' : test.status === 'accepted' ? '<span class="pill warn">승인된 차이</span>' : test.status === 'pass' ? '<span class="pill ok">as-is와 같음</span>' : '';
  let h = `<div class="mh"><div><b>${esc(test.title)}</b><small>${esc(name)} · ${test.seq.length}단계</small></div>${pill}<button type="button" class="only" data-only="${esc(name)}" title="지도에서 이 ${G.unit}가 지나는 화면만 남긴다">지도에서 이 ${G.unit}만</button><button class="ib" type="button" id="closeModal" aria-label="닫기">×</button></div><div class="mb">`;
  h += `<div class="film">` + test.seq.map(s => `<button type="button" class="${hereSteps.has(s.index) ? 'here' : ''} ${s.failed ? 'fail' : ''}" data-lb="${s.shot}" data-cap="${esc(s.index + 1)}단계 · ${esc(s.action)}" title="${esc(s.action)}">`
      + (s.shot ? `<img src="${shot(s.shot)}" alt="">` : '') + `<span class="k">${s.index + 1}</span><span class="cap">${esc(s.action)}</span></button>`).join('') + `</div>`;
  h += `<div class="steps">` + test.seq.map(s => {
      const node = G.nodes[s.node] || {}, v = (node.visits || []).find(x => x.test === name && x.index === s.index) || {};
      const where = node.kind === 'base' ? routeName(s.route) : `${routeName(s.route)} › ${stateName(s.node)}`;
      return `<div class="visit ${s.failed ? 'fail' : ''} ${hereSteps.has(s.index) ? 'here' : ''}"><span class="no">${s.index + 1}</span><div><div>${v.action || esc(s.action)} <span class="tid">· ${where}</span></div>`
        + (v.dialogs || []).map(d => `<div class="dlg">${d.type === 'confirm' ? '확인창' : '알림창'} “${esc(d.message)}” → ${d.action === 'accept' ? '확인' : '취소'}</div>`).join('')
        + ((v.checks || []).length ? `<div class="ck">${v.checks.join('')}</div>` : '') + `</div></div>`; }).join('') + `</div>`;
  if(test.rows.length) h += `<div class="dtab"><table><tr><th>무엇이</th><th>as-is</th><th>to-be</th></tr>` + test.rows.slice(0, 12).map(r =>
      `<tr><td>${esc(r[0])}</td><td class="m was">${esc(r[1])}</td><td class="m now">${esc(r[2])}</td></tr>`).join('') + `</table></div>`;
  h += `</div>`;
  modal.querySelector('.box').innerHTML = h;
  modal.classList.add('on');
  modal.querySelector('#closeModal').addEventListener('click', closeModal);
  modal.querySelectorAll('[data-lb]').forEach(b => b.addEventListener('click', () => openLb(shot(b.dataset.lb), b.dataset.cap)));
}
function go(rid, sid){ const h = encodeURIComponent(rid) + (sid ? '/' + encodeURIComponent(sid) : ''); if(ctx.getSub() === h) route(); else ctx.setSub(h); }
$$('.node, .row').forEach(el => el.addEventListener('click', () => go(el.dataset.id)));
$$('.node').forEach(el => {
  el.addEventListener('mouseenter', () => $$('.edge').forEach(e => e.classList.toggle('dim', e.dataset.src !== el.dataset.id && e.dataset.dst !== el.dataset.id && !(e.dataset.srcs || '').split('|').includes(el.dataset.id))));
  el.addEventListener('mouseleave', () => $$('.edge').forEach(e => e.classList.remove('dim')));
});
/* 왼쪽 목록 접기 */
$('#tl').addEventListener('click', () => { stage.classList.toggle('nol'); $('#tl').textContent = stage.classList.contains('nol') ? '›' : '‹'; fit(); });
/* 검색·거르기 */
const q = $('#q'), f = $('#f'), pl = $('#pl'), pick = $('#pick');
let stFilter = '', tFilter = '';  // 머리 칩으로 고른 상태, 고른 경로(테스트) 이름 ('' = 전부)
function filter(){
  const s = q.value.trim().toLowerCase(), t = tFilter;
  const keep = new Set();
  for(const [rid, r] of Object.entries(G.routes)){
    const text = [r.name, r.path, ...r.states.map(x => G.nodes[x].label)].join(' ').toLowerCase();
    if((!s || text.includes(s)) && (!t || r.tests.includes(t)) && (!stFilter || r.status === stFilter)) keep.add(rid);
  }
  $$('.node, .row').forEach(el => el.classList.toggle('dim', !keep.has(el.dataset.id)));
  $$('.edge').forEach(el => el.classList.toggle('dim', !(keep.has(el.dataset.src) && keep.has(el.dataset.dst)) || (t && !el.dataset.tests.split('|').includes(t))));
}
q.addEventListener('input', filter);
/* 경로 고르기: 입력하면 제목·이름·지나는 화면 이름으로 맞는 경로 목록, 고르면 그 경로가 지나는 화면만 남는다. 비우면 해제 */
const statusWord = t => t.status === 'fail' ? '<span class="pill bad">다름</span>' : t.status === 'accepted' ? '<span class="pill warn">승인된 차이</span>' : t.status === 'pass' ? '<span class="pill ok">같음</span>' : '';
function pickList(){
  const s = f.value.trim().toLowerCase();
  const hits = G.tests.filter(t => !s || [t.title, t.name, ...t.seq.map(x => G.routes[x.route] ? G.routes[x.route].name : '')].join(' ').toLowerCase().includes(s));
  const show = hits.slice(0, 40);
  pl.innerHTML = show.map(t => `<button type="button" data-pick="${esc(t.name)}"><span><b>${esc(t.title)}</b><small>${esc(t.name)} · ${t.seq.length}단계</small></span>${statusWord(t)}</button>`).join('')
    + (hits.length > show.length ? `<div class="more">${hits.length - show.length}개 더 · 더 입력해 좁히세요</div>` : (hits.length ? '' : '<div class="more">맞는 경로가 없습니다</div>'));
  pl.hidden = false;
}
function pickTest(name){
  tFilter = name; const t = testByName[name];
  f.value = t ? t.title : ''; pl.hidden = true; pick.classList.toggle('has', !!tFilter);
  if(tFilter && !detail.hidden) back();
  filter();
}
f.addEventListener('input', () => { if(tFilter){ tFilter = ''; pick.classList.remove('has'); filter(); } pickList(); });
f.addEventListener('focus', pickList);
f.addEventListener('keydown', e => { if(e.key === 'Escape'){ e.stopPropagation(); pl.hidden = true; f.blur(); } if(e.key === 'Enter'){ const b = pl.querySelector('[data-pick]'); if(b) pickTest(b.dataset.pick); } });
pl.addEventListener('mousedown', e => { const b = e.target.closest('[data-pick]'); if(b){ e.preventDefault(); pickTest(b.dataset.pick); } });
ctx.listen(document, 'click', e => { if(!e.target.closest('#pick')) pl.hidden = true; });
modal.addEventListener('click', e => { const b = e.target.closest('[data-only]'); if(b){ closeModal(); pickTest(b.dataset.only); } });
const chipBtns = [...$$('#chips .fchip[data-st]')];  // 머리가 출처 바로 옮겨져도 (root 밖) 같은 요소를 다룬다
chipBtns.forEach(b => b.addEventListener('click', () => {
  stFilter = (b.dataset.st && stFilter !== b.dataset.st) ? b.dataset.st : '';
  chipBtns.forEach(x => x.classList.toggle('on', x.dataset.st === stFilter));
  if(!detail.hidden) back();
  filter();
}));
/* 확대·축소·끌기 */
const inner = $('#inner'), holder = $('#holder'), cv = $('#canvas');
const W = +inner.dataset.w, H = +inner.dataset.h; let z = 1;
function zoom(v){ z = Math.max(0.3, Math.min(1.6, v)); inner.style.transform = `scale(${z})`; holder.style.width = W * z + 'px'; holder.style.height = H * z + 'px'; }
function fit(){ requestAnimationFrame(() => zoom(Math.min(1, Math.max(0.7, (cv.clientWidth - 24) / W)))); }
$('#zf').onclick = fit; $('#zi').onclick = () => zoom(z + 0.15); $('#zo').onclick = () => zoom(z - 0.15);
let drag = null;
cv.addEventListener('mousedown', e => { if(e.target.closest('.node')) return; drag = {x: e.clientX, y: e.clientY, l: cv.scrollLeft, t: cv.scrollTop}; cv.classList.add('drag'); });
ctx.listen(window, 'mousemove', e => { if(!drag) return; cv.scrollLeft = drag.l - (e.clientX - drag.x); cv.scrollTop = drag.t - (e.clientY - drag.y); });
ctx.listen(window, 'mouseup', () => { drag = null; cv.classList.remove('drag'); });
cv.addEventListener('wheel', e => { if(!e.ctrlKey && !e.metaKey) return; e.preventDefault(); zoom(z - Math.sign(e.deltaY) * 0.1); }, {passive: false});
fit(); addEventListener('resize', fit);
/* 전체화면: 브라우저 전체화면(허용되면) + 지도만 남기는 배치. 검색·목록·칩은 '검색·목록' 버튼으로 켠다 */
function setTools(on){ root.classList.toggle('tools', on); $('#fst').classList.toggle('on', on); if(on) q.focus(); }  // 버튼 표시와 실제 상태를 늘 같이 바꾼다
// 머리(상태 칩 + (i))는 통합 화면 안에서는 출처 바 오른쪽(ctx.slot)에 놓고, 전체화면에서는 조각 안으로 되돌린다 (fs 배치 CSS 가 조각 안 기준)
const pgh = $('.pgh');
function placeHead(inside){ if(!pgh) return; if(!inside && ctx.slot) ctx.slot.appendChild(pgh); else if(!root.contains(pgh)) root.querySelector('main').prepend(pgh); }
placeHead(false);
function enterFs(){
  placeHead(true); root.classList.add('fs'); setTools(false);
  const el = document.documentElement;
  if(el.requestFullscreen && !document.fullscreenElement) el.requestFullscreen().catch(() => {});  // iframe 이 허용하지 않으면 배치만 바꾼다
  setTimeout(fit, 60);
}
function exitFs(){
  root.classList.remove('fs'); setTools(false); placeHead(false);
  if(document.fullscreenElement && document.exitFullscreen) document.exitFullscreen().catch(() => {});
  setTimeout(fit, 60);
}
$('#fsb').onclick = enterFs;
/* 연결선 위 동작 이름 켜고 끄기: 끄면 화면과 연결선만 남는다. 브라우저에 기억 */
const LBL_KEY = 'eastshift-map-labels';
function setLabels(on){ root.classList.toggle('nolabels', !on); ['#lbt', '#flbt'].forEach(s => { const b = $(s); if(b) b.classList.toggle('on', on); }); try { localStorage.setItem(LBL_KEY, on ? '1' : '0'); } catch(_) {} }
let lblOn = true; try { lblOn = localStorage.getItem(LBL_KEY) !== '0'; } catch(_) {}
setLabels(lblOn);
['#lbt', '#flbt'].forEach(s => { const b = $(s); if(b) b.onclick = () => setLabels(root.classList.contains('nolabels')); });
$('#fsx').onclick = exitFs;
$('#fst').onclick = () => setTools(!root.classList.contains('tools'));
$('#fzf').onclick = fit; $('#fzi').onclick = () => zoom(z + 0.15); $('#fzo').onclick = () => zoom(z - 0.15);
ctx.listen(document, 'fullscreenchange', () => { if(!document.fullscreenElement && root.classList.contains('fs')) exitFs(); else setTimeout(fit, 60); });
ctx.onSub(route);
route();
"""


def fragment(g: dict[str, Any]) -> dict[str, Any]:
    """Screen Map 조각 (html.fragment 형식). 통합 화면이 iframe 없이 끼운다."""
    pos, paths = layout(g["rorder"], g["redges"], g["start"])
    g["paths"] = paths
    ncol = max((c for c, _ in pos.values()), default=0) + 1
    nrow = max((r for _, r in pos.values()), default=0) + 1
    backs = [e for e in g["redges"] if pos[e["dst"]][0] <= pos[e["src"]][0]]
    w = PAD * 2 + ncol * CARD_W + (ncol - 1) * GAP_X
    body_h = PAD + nrow * CARD_H + (nrow - 1) * GAP_Y
    hgt = body_h + PAD + (len(backs) + 1) * LANE
    xy = {n: (PAD + c * (CARD_W + GAP_X), PAD + r * (CARD_H + GAP_Y)) for n, (c, r) in pos.items()}

    def pill(x: float, y: float, text: str, tests: list[str], cls: str) -> str:
        tw = _tw(text)
        return (f"<g class='edge {cls}' data-src='{{src}}' data-dst='{{dst}}' data-tests='{'|'.join(tests)}'>{{path}}"
                f"<rect class='pill' x='{x - tw / 2:.0f}' y='{y - 11}' width='{tw}' height='22' rx='11'/>"
                f"<text x='{x:.0f}' y='{y + 4}' text-anchor='middle'>{html._e(text)}</text></g>")

    parts = []
    lane_i = 0
    for e in g["redges"]:
        (x1, y1), (x2, y2) = xy[e["src"]], xy[e["dst"]]
        label = e["actions"][0][:16]  # 동작이 여럿이면 첫 동작만 (나머지는 상세 페이지의 '여기서 갈 수 있는 곳')
        if pos[e["dst"]][0] > pos[e["src"]][0]:
            sx, sy, tx, ty = x1 + CARD_W, y1 + CARD_H / 2, x2, y2 + CARD_H / 2
            d = f"M{sx},{sy} C{sx + GAP_X * .5},{sy} {tx - GAP_X * .5},{ty} {tx},{ty}"
            mx, my = (sx + tx) / 2, (sy + ty) / 2
            cls = ""
        else:
            lane_i += 1
            ly = body_h + lane_i * LANE
            sx, sy, tx, ty = x1 + CARD_W * .5, y1 + CARD_H, x2 + CARD_W * .5, y2 + CARD_H
            d = f"M{sx},{sy} L{sx},{ly - 14} Q{sx},{ly} {sx - 14},{ly} L{tx + 14},{ly} Q{tx},{ly} {tx},{ly - 14} L{tx},{ty + 6}"
            mx, my = (sx + tx) / 2, ly
            cls = "back"
        p = f"<path d='{d}' marker-end='url(#{'arrb' if cls else 'arr'})'/>"
        parts.append(pill(mx, my, label, e["tests"], cls).replace("{src}", html._e(e["src"])).replace("{dst}", html._e(e["dst"])).replace("{path}", p))
    # 동작 이름을 끈 때의 되돌아가는 선: 같은 화면으로 돌아오는 선들을 차선 하나로 합친다 (이름이 없으면 따로 그을 이유가 없다)
    by_dst: dict[str, list[str]] = defaultdict(list)
    for e in backs:
        by_dst[e["dst"]].append(e["src"])
    for k, (dst, srcs) in enumerate(by_dst.items(), 1):
        ly = body_h + k * LANE
        tx, ty = xy[dst][0] + CARD_W * .5, xy[dst][1] + CARD_H
        xs = [xy[s][0] + CARD_W * .5 for s in srcs]
        lo, hi = min(xs + [tx]), max(xs + [tx])
        d = "".join(f"M{sx},{xy[s][1] + CARD_H} L{sx},{ly} " for s, sx in zip(srcs, xs)) + f"M{lo},{ly} L{hi},{ly} M{tx},{ly} L{tx},{ty + 6}"
        tests = sorted({t for e in backs if e["dst"] == dst for t in e["tests"]})
        parts.append(f"<g class='edge back merged' data-src='{html._e(dst)}' data-dst='{html._e(dst)}' data-srcs='{html._e('|'.join(srcs))}' data-tests='{'|'.join(tests)}'>"
                     f"<path d='{d}' marker-end='url(#arrb)'/></g>")
    svg = (f"<svg class='links' width='{w}' height='{hgt}' viewBox='0 0 {w} {hgt}'><defs>"
           "<marker id='arr' viewBox='0 0 10 10' refX='9' refY='5' markerWidth='8' markerHeight='8' orient='auto'><path d='M0,0 L10,5 L0,10 z' fill='var(--accent)'/></marker>"
           "<marker id='arrb' viewBox='0 0 10 10' refX='9' refY='5' markerWidth='8' markerHeight='8' orient='auto'><path d='M0,0 L10,5 L0,10 z' fill='var(--faint)'/></marker>"
           "</defs>" + "".join(parts) + "</svg>")

    unit = g["unit"]
    tags = {"diff": "다름", "undeveloped": "탐색 미발견", "new": "to-be에서만 발견", "accepted": "승인된 차이", "unreached": "코드에만 있음"}
    cards, rows = [], []
    for rid in g["rorder"]:
        r, (x, y) = g["routes"][rid], xy[rid]
        st = STATUS_CLS.get(r.get("status", "untested"), "")
        tag = "<span class='tag'>시작</span>" if rid == g["start"] else (f"<span class='tag'>{tags[r['status']]}</span>" if r.get("status") in tags else "")
        thumb = f"<img src='{g['shots'][r['shot']]}' alt='' loading='lazy'>" if r["shot"] else "<span class='no'>캡처 없음</span>"
        kinds = ("<span class='kd'>탐색·시나리오 미도달</span>" if r.get("status") == "unreached"
                 else "".join(f"<span class='kd {k}'>{KIND_KO[k]} {n}</span>" for k, n in r["kinds"].items()) or "<span class='kd'>기본만</span>")
        cards.append(f"<button class='node {st}' data-id='{html._e(rid)}' style='left:{x}px;top:{y}px' type='button' title='{html._e(r['name'])} {html._e(r['path'])}'>"
                     f"<div class='strip'></div><div class='chrome'><i></i><i></i><i></i></div><div class='th'>{thumb}</div>{tag}"
                     f"<div class='body'><div class='nm'>{html._e(r['name'])}</div><div class='kinds'>{kinds}</div></div>"
                     f"<div class='meta'><span>{html._e(r['path'])}</span><b>{unit} {len(r['tests'])}</b></div></button>")
        rows.append(f"<button class='row' data-id='{html._e(rid)}' type='button'><span class='dot {st}'></span>"
                    f"<span class='nm'>{html._e(r['name'])}<small>{html._e(r['path'])}</small></span><span class='ct'>{len(r['tests'])}<br>상태 {len(r['states'])}</span></button>")

    failed = sum(r["failed"] for r in g["routes"].values())
    n_states = len(g["nodes"])
    src, unit = g["source"], g["unit"]
    c = g.get("counts", {})
    has_tobe = bool(src.get("tobe_crawl"))
    cr = g.get("code_routes")
    n_found = len(g["routes"]) - c.get("unreached", 0)
    # 머리의 칩: 화면 수와 상태별 개수. 누르면 그 상태의 화면만 남긴다 (다시 누르면 해제). 마지막 칩은 소스 라우트 대조 결과(정보만)
    chip_order = [("diff", "다름"), ("undeveloped", "탐색 미발견"), ("new", "to-be에서만"), ("accepted", "승인된 차이"), ("same", "같음"), ("unreached", "코드에만 있음")]
    chips = [f"<button type='button' class='fchip all on' data-st=''>화면<b>{n_found}</b></button>"]
    chips += [f"<button type='button' class='fchip {STATUS_CLS[k]}' data-st='{k}' title='{html._e(STATUS_KO[k])}'><i></i>{label}<b>{c[k]}</b></button>"
              for k, label in chip_order if c.get(k) and (k != "same" or g["compared"])]
    if cr:
        chips.append(f"<span class='fchip info' title='eastshift routes 가 소스에서 뽑은 화면 주소와 대조'>소스 화면 {cr['screens']} 중 {cr['reached']} 도달</span>")
    legend = ("<div class='legend'><span><i class='st'></i>시작 화면</span>"
              + ("<span><i class='ok'></i>as-is와 같음</span><span><i class='bad'></i>as-is와 다름</span>" if g["compared"] else "")
              + ("<span><i class='undev'></i>to-be 탐색에서 미발견</span><span><i class='new'></i>to-be에서만 발견</span>" if has_tobe else "")
              + ("<span><i class='unreached'></i>코드에만 있음 (탐색·시나리오 미도달)</span>" if cr else "")
              + "<span><span class='ln'></span>동작 → 다음 화면</span><span><span class='ln back'></span>되돌아가기</span>"
              "<span><span class='kd dialog'>팝업</span><span class='kd drawer'>드로워</span><span class='kd tab'>탭</span> 화면 안의 상태</span></div>")
    facts = [f"<b>출처</b> {html._e(src['label'])}", f"<b>기준</b> {html._e(src['base'])}"]
    if g["target"]:
        facts.append(f"<b>비교 대상</b> {html._e(g['target'])}")
    if has_tobe:
        facts.append(f"<b>to-be 탐색</b> {html._e(src['tobe_crawl'])}")
    if src.get("asis_crawl"):
        facts.append(f"<b>as-is 탐색</b> {html._e(src['asis_crawl'])}")
    if cr:
        facts.append(f"<b>소스 라우트</b> {html._e(cr['src'])} · 화면 {cr['screens']}개 중 {cr['reached']} 도달")
    facts += [f"<b>{unit}</b> {len(g['tests'])}개", f"<b>상태</b> {n_states}개"]
    info = ("화면(주소)을 이은 지도입니다. 화면을 누르면 상세 페이지로 넘어가 그 안의 팝업·드로워·탭, 오는 길·가는 길, 지나는 " + unit + "를 봅니다. "
            + ("빨강 = 비교 실행에서 다른 화면, 노랑 = to-be 탐색에서 못 찾은 화면(미개발 확정 아님), 파랑 = to-be 탐색에서만 찾은 화면. " if src["kind"] != "crawl" else "")
            + ("회색 = 소스 코드에는 선언돼 있는데 어떤 탐색·시나리오도 닿지 않은 주소(eastshift routes)." if cr else ""))
    body = (html.head(chips, chips_id="chips", info=info, facts=facts, extra=legend) + "<div id='overview'>"
            f"<div class='toolbar'><input id='q' type='search' placeholder='화면 이름·주소·팝업 이름으로 찾기' aria-label='화면 찾기'>"
            f"<div class='pick' id='pick'><input id='f' type='search' placeholder='{unit} 찾기 · 제목이나 지나는 화면 이름' aria-label='{unit}로 거르기' autocomplete='off'><div class='pl' id='pl' hidden></div></div>"
            f"<span class='zoom'><button type='button' id='zo' aria-label='축소'>−</button><button type='button' id='zf'>맞춤</button><button type='button' id='zi' aria-label='확대'>+</button></span>"
            f"<button type='button' class='fsb lbt on' id='lbt' title='연결선 위 동작 이름 보이기/숨기기'>동작 이름</button>"
            f"<button type='button' class='fsb' id='fsb' title='지도만 화면 가득. 검색·목록은 버튼으로 켠다'>⛶ 전체화면</button></div>"
            f"<div class='fsbar' id='fsbar'><button type='button' id='fst' title='검색·화면 목록·상태 칩 보이기/숨기기'>검색·목록</button><button type='button' class='lbt on' id='flbt' title='연결선 위 동작 이름 보이기/숨기기'>동작 이름</button><span class='sep'></span>"
            f"<button type='button' id='fzo' aria-label='축소'>−</button><button type='button' id='fzf'>맞춤</button><button type='button' id='fzi' aria-label='확대'>+</button><span class='sep'></span>"
            f"<button type='button' id='fsx' title='전체화면 나가기 (Esc)'>나가기</button></div>"
            f"<div class='stage' id='stage'><div class='pane left'><h2><span>화면 {len(g['routes'])}개</span><button class='ib' id='tl' type='button' aria-label='목록 접기'>‹</button></h2><div class='list'>{''.join(rows)}</div></div>"
            f"<div class='pane canvas' id='canvas'><div id='holder' style='position:relative;width:{w}px;height:{hgt}px'>"
            f"<div id='inner' data-w='{w}' data-h='{hgt}' style='position:absolute;left:0;top:0;width:{w}px;height:{hgt}px;transform-origin:0 0'>{svg}{''.join(cards)}</div></div></div>"
            f"</div></div><section class='detail' id='detail' aria-live='polite' hidden></section>"
            f"<div class='modal' id='modal' role='dialog' aria-label='시나리오 상세'><div class='box'></div></div>"
            f"<div class='lb' id='lb' role='dialog' aria-label='화면 크게 보기'><div><img src='' alt=''><div class='cap'></div></div></div>")
    return html.fragment("map", f"{g['app']} Screen Map · {src['label']}", body, css=MAP_CSS, js=MAP_JS, data=("g", g))


def render(g: dict[str, Any]) -> str:
    """혼자 열리는 문서 (테스트, /page/… 직접 접속). 통합 화면은 fragment() 를 끼운다."""
    return html.assemble(fragment(g))


