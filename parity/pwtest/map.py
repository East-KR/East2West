"""화면 지도: 골든에 기록된 as-is 동작을 화면 단위로 합쳐 네트워크로 그린다. 파일 하나짜리 HTML.

parity map golden/<app> [--junit reports/junit-<target>.xml] --out reports/map-<app>.html

화면: 세 칸 (Playwright Trace Viewer의 구성을 따랐다)
  왼쪽  화면 목록 (검색, 상태 점, 이름, 지나간 테스트 수)
  가운데 지도: 왼쪽에서 오른쪽으로 한 방향. 열 = 시작 화면에서 몇 번 눌러 가는지, 열 안 순서는 무게중심(Sugiyama 방식)으로 선 교차를 줄인다.
        노드 = 화면 프레임(대표 캡처, 이름, 구별 문구, 상태 띠). 화살표 = 동작 이름 알약. 되돌아가는 길은 아래 차선으로 회색.
  오른쪽 선택한 화면: 큰 캡처, 시작에서 오는 길, 나가는 길, 이 화면을 지나는 테스트마다 필름스트립(단계 캡처, 이 화면 단계는 강조)과 동작·확인 값.
  --junit이 있으면 to-be에서 다른 화면은 빨간 띠, 패널에 무엇이 달랐는지.

같은 화면인지는 화면 구조(제목·입력칸·버튼)로 가리고 글자 내용(주문번호, 품목명)은 보지 않는다. 그 밖에는 기록된 산출물만 읽는다.
"""
from __future__ import annotations

import base64
import json
import re
from collections import defaultdict
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from ..crawl import landmarks, signature
from . import html, oracle
from .report import _junit

STEP_DIFF = re.compile(r"step (\d+) ")
SNAP_LINE = re.compile(r'^(?P<indent>\s*)-\s+(?P<role>[a-z]+)\b')
# 데이터에 따라 글자가 바뀌는 역할: "같은 화면" 판단에서는 역할만 남긴다 (주문번호·품목명이 달라도 같은 완료 화면)
DATA_ROLES = {"text", "paragraph", "definition", "term", "strong", "emphasis", "code", "cell", "gridcell", "listitem", "status", "log", "time"}
NUMBERY = re.compile(r"\S*\d\S*")


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


def _names(snapshot: str, url: str, title: str) -> tuple[str, str]:
    """(이름, 구별 문구). 이름은 제목·모달, 구별 문구는 alert나 안내문처럼 같은 이름의 화면을 갈라 주는 것."""
    heads, alerts, modal, texts = landmarks(snapshot)
    name = _clean_text(modal or (heads[0] if heads else "") or title or urlparse(url).path or "/")[:36] or "/"
    hint = ""
    if modal and heads:
        hint = _clean_text(heads[0])[:40]
    elif alerts:
        hint = _clean_text(alerts[0])[:40]
    return name, hint


def _fail_steps(messages: list[str], assertions: list[dict[str, Any]]) -> set[int]:
    text = "\n".join(messages)
    steps = {int(m.group(1)) for m in STEP_DIFF.finditer(text)}
    for a in assertions:
        tgt = str(a["target"])
        if tgt and (f'"{tgt}"' in text or f"'{tgt}'" in text):
            steps.add(a["step"] - 1)
    return steps


def _plain(action_html: str) -> str:
    t = re.sub("<[^>]+>", " ", action_html)
    t = re.sub(r"\s+", " ", t).strip()
    return t


def build(d: Path, junit: Path | None = None, tests_dir: Path | None = None) -> dict[str, Any]:
    docs = html.docstrings(tests_dir or Path("e2e") / d.name)
    run = _junit(junit) if junit else None
    cases = {c["name"]: c for c in run["cases"]} if run else {}
    nodes: dict[str, dict[str, Any]] = {}
    edges: dict[tuple[str, str], dict[str, Any]] = {}
    order: list[str] = []
    shots: dict[str, str] = {}
    tests = []
    for t in oracle.tests(d):
        data = json.loads((d / f"{t['name']}.json").read_text(encoding="utf-8"))
        steps = data.get("steps", [])
        by_step = defaultdict(list)
        for a in data.get("assertions", []):
            by_step[a["step"] - 1].append(a)
        case = cases.get(t["name"])
        failed = _fail_steps(case["messages"], data.get("assertions", [])) if case and case["status"] == "fail" else set()
        rows = html.rows_for(case["messages"])[0] if case and case["status"] == "fail" else []
        status = None if case is None else case["status"]
        seq, prev = [], None
        for o in steps:
            sig = _screen_sig(o.get("snapshot", ""))
            shot_id = ""
            if o.get("shot"):
                shot_id = f"{t['name']}#{o['index']}"
                shots[shot_id] = _img(d / o["shot"])
            if sig not in nodes:
                name, hint = _names(o.get("snapshot", ""), o["url"], o.get("title", ""))
                nodes[sig] = {"id": sig, "name": name, "hint": hint, "url": urlparse(o["url"]).path or "/", "shot": shot_id,
                              "visits": [], "local": 0, "failed": False, "tests": []}
                order.append(sig)
            n = nodes[sig]
            if not n["shot"] and shot_id:
                n["shot"] = shot_id
            action = html._action(o["kind"], o["text"])
            visit = {"test": t["name"], "index": o["index"], "action": action, "dialogs": o.get("dialogs", []),
                     "checks": [html._val(a) for a in by_step.get(o["index"], [])], "shot": shot_id, "failed": o["index"] in failed}
            n["visits"].append(visit)
            if t["name"] not in n["tests"]:
                n["tests"].append(t["name"])
            if o["index"] in failed:
                n["failed"] = True
            if prev is not None:
                if prev == sig:
                    n["local"] += 1
                else:
                    e = edges.setdefault((prev, sig), {"src": prev, "dst": sig, "actions": [], "tests": []})
                    word = _plain(action).replace("열기", "").replace("누르기", "").replace("선택", "").replace("입력", "").strip() or _plain(action)
                    if word not in e["actions"]:
                        e["actions"].append(word)
                    if t["name"] not in e["tests"]:
                        e["tests"].append(t["name"])
            seq.append({"index": o["index"], "node": sig, "shot": shot_id, "action": _plain(action), "failed": o["index"] in failed})
            prev = sig
        tests.append({"name": t["name"], "title": html._title(t["name"], docs), "status": status, "rows": rows, "seq": seq})
    # 같은 이름의 화면에 번호를 붙인다 (구별 문구가 없을 때)
    by_name: dict[str, list[str]] = defaultdict(list)
    for sid in order:
        by_name[nodes[sid]["name"]].append(sid)
    for name, ids in by_name.items():
        if len(ids) > 1:
            for i, sid in enumerate(ids, 1):
                nodes[sid]["variant"] = i
    return {"app": d.name, "start": order[0] if order else None, "order": order, "nodes": nodes, "shots": shots,
            "edges": list(edges.values()), "tests": tests, "target": (run["props"].get("base_url") if run else None),
            "compared": run is not None}


# -- 배치: 열 = 시작에서의 거리, 열 안 순서 = 무게중심 (선 교차 최소화) --------------------------------
def layout(g: dict[str, Any]) -> tuple[dict[str, tuple[int, int]], dict[str, list[str]]]:
    out, inn = defaultdict(list), defaultdict(list)
    for e in g["edges"]:
        out[e["src"]].append(e["dst"])
        inn[e["dst"]].append(e["src"])
    depth: dict[str, int] = {}
    if g["start"]:
        depth[g["start"]] = 0
        queue = [g["start"]]
        while queue:
            cur = queue.pop(0)
            for nxt in out[cur]:
                if nxt not in depth:
                    depth[nxt] = depth[cur] + 1
                    queue.append(nxt)
    for n in g["order"]:
        depth.setdefault(n, (max(depth.values()) + 1) if depth else 0)
    cols: dict[int, list[str]] = defaultdict(list)
    for n in g["order"]:
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
    if g["start"]:  # 시작에서 각 화면까지 최단 경로 (패널의 '오는 길')
        prevs = {g["start"]: None}
        queue = [g["start"]]
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


CARD_W, THUMB_H, CARD_H, GAP_X, GAP_Y, PAD = 264, 160, 250, 150, 48, 36
LANE = 34  # 되돌아가는 선 차선 간격


def _tw(s: str) -> int:
    return sum(13 if ord(ch) > 0x2E80 else 7 for ch in s) + 20


MAP_CSS = """
main{max-width:none;padding-block:24px 40px;gap:20px}
.head{gap:6px 24px}.head h1{font-size:26px}
.legend{display:flex;flex-wrap:wrap;gap:6px 18px;font-size:13px;color:var(--muted);align-items:center}
.legend span{display:inline-flex;align-items:center;gap:6px}
.legend i{display:inline-block;width:14px;height:14px;border-radius:4px}
.legend i.ok{background:var(--ok)}.legend i.bad{background:var(--bad)}.legend i.st{background:var(--accent)}
.legend .ln{width:26px;height:0;border-top:2px solid var(--accent)}.legend .ln.back{border-top:2px dashed var(--faint)}
.toolbar{display:flex;flex-wrap:wrap;gap:8px;align-items:center}
.toolbar input,.toolbar select{font:inherit;font-size:14px;padding:7px 11px;border:1px solid var(--line);border-radius:8px;background:var(--surface);color:var(--ink)}
.toolbar input{min-width:240px}
.zoom{display:inline-flex;border:1px solid var(--line);border-radius:8px;overflow:hidden;margin-left:auto}
.zoom button{border:0;background:var(--surface);color:var(--ink);font:600 14px var(--sans);padding:7px 13px;cursor:pointer}
.zoom button+button{border-left:1px solid var(--line)}.zoom button:hover{background:var(--sunk)}

/* 세 칸: 왼쪽 목록(접힘 가능) · 지도 · 상세(선택했을 때만) */
.stage{display:grid;grid-template-columns:260px minmax(0,1fr);gap:14px;align-items:stretch;height:calc(100vh - 230px);min-height:560px;transition:grid-template-columns .2s}
.stage.open{grid-template-columns:260px minmax(0,1fr) 440px}
.stage.nol{grid-template-columns:48px minmax(0,1fr)}.stage.nol.open{grid-template-columns:48px minmax(0,1fr) 440px}
.pane{background:var(--surface);border:1px solid var(--line);border-radius:12px;min-height:0;display:flex;flex-direction:column;overflow:hidden}
.pane>h2{display:flex;align-items:center;justify-content:space-between;gap:8px;font-size:12.5px;font-weight:600;letter-spacing:.06em;color:var(--muted);padding:10px 10px 8px 14px;border-bottom:1px solid var(--line);margin:0}
.ib{border:1px solid var(--line);background:var(--surface);color:var(--muted);border-radius:6px;width:26px;height:26px;cursor:pointer;font:600 14px var(--sans);display:grid;place-items:center;flex:none}
.ib:hover{background:var(--sunk);color:var(--ink)}
.stage.nol .pane.left>h2{padding:10px 0;justify-content:center;border-bottom:0}.stage.nol .pane.left>h2 span,.stage.nol .pane.left .list{display:none}
.list{overflow:auto;padding:6px}
.row{display:grid;grid-template-columns:10px 1fr auto;gap:10px;align-items:center;padding:8px;border-radius:8px;cursor:pointer;border:0;background:none;text-align:left;font:inherit;color:inherit;width:100%}
.row:hover{background:var(--sunk)}.row.sel{background:var(--accent-soft)}
.row .dot{width:10px;height:10px;border-radius:50%;background:var(--line)}.row .dot.ok{background:var(--ok)}.row .dot.bad{background:var(--bad)}
.row .nm{font-size:14px;font-weight:600;line-height:1.25}.row .nm small{display:block;font-weight:400;color:var(--muted);font-size:12px;margin-top:1px}
.row .ct{font:12px var(--mono);color:var(--faint)}.row.dim{opacity:.35}

/* 지도 */
.canvas{position:relative;overflow:auto;cursor:grab;background:radial-gradient(circle at 1px 1px, var(--line) 1px, transparent 0) 0 0/24px 24px, var(--bg)}
.canvas.drag{cursor:grabbing;user-select:none}
.canvas svg.links{position:absolute;left:0;top:0;overflow:visible}
.edge path{fill:none;stroke:var(--accent);stroke-width:2;transition:opacity .15s}
.edge.back path{stroke:var(--faint);stroke-dasharray:6 5}
.edge .pill{fill:var(--surface);stroke:var(--line)}
.edge text{font-size:12.5px;font-weight:600;fill:var(--ink)}
.edge.dim{opacity:.12}.edge.hot path{stroke-width:3}.edge.hot .pill{stroke:var(--accent)}
.node{position:absolute;width:__CW__px;height:__CH__px;border:1.5px solid var(--line);border-radius:12px;background:var(--surface);cursor:pointer;display:flex;flex-direction:column;
 text-align:left;font:inherit;color:inherit;padding:0;overflow:hidden;box-shadow:0 1px 2px rgba(15,20,19,.06);transition:box-shadow .15s,opacity .15s}
.node:hover{box-shadow:0 6px 18px rgba(15,20,19,.12);border-color:var(--accent)}
.node.sel{border-color:var(--accent);box-shadow:0 0 0 3px var(--accent-soft),0 6px 18px rgba(15,20,19,.12)}
.node.dim{opacity:.28}
.node .strip{height:5px;background:var(--line)}.node.ok .strip{background:var(--ok)}.node.bad .strip{background:var(--bad)}
.node .chrome{display:flex;gap:4px;padding:6px 10px 0}.node .chrome i{width:7px;height:7px;border-radius:50%;background:var(--line);display:block}
.node .th{margin:5px 8px 0;height:__TH__px;border-radius:6px;overflow:hidden;background:#fff;border:1px solid var(--line);position:relative}
.node .th img{width:150%;max-width:none;display:block}
.node .th .no{position:absolute;inset:0;display:grid;place-items:center;color:var(--faint);font-size:12px}
.node .body{padding:8px 10px 0;display:flex;flex-direction:column;gap:2px;min-height:0}
.node .nm{font-size:15px;font-weight:700;line-height:1.25;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.node .nm em{font-style:normal;font-weight:500;color:var(--muted);font-size:12.5px;margin-left:4px}
.node .hint{font-size:12px;color:var(--muted);white-space:nowrap;overflow:hidden;text-overflow:ellipsis;min-height:18px}
.node .meta{margin-top:auto;display:flex;justify-content:space-between;align-items:center;padding:0 10px 8px;font:11.5px var(--mono);color:var(--faint)}
.node .meta b{font-family:var(--sans);font-weight:600;color:var(--muted)}
.node .tag{position:absolute;top:12px;left:10px;font-size:11px;font-weight:700;letter-spacing:.04em;background:var(--accent);color:#fff;border-radius:4px;padding:1px 7px}
.node.bad .tag{background:var(--bad)}

/* 상세 패널 */
.detail{display:block;overflow:auto;padding:0 18px 18px}
.stage:not(.open) .pane.side{display:none}  /* 선택한 화면이 있을 때만 */
.detail .dh{position:sticky;top:0;background:var(--surface);padding:14px 0 10px;border-bottom:1px solid var(--line);z-index:1;display:grid;grid-template-columns:1fr auto;gap:4px 10px}
.detail h3{font-size:19px;font-weight:700;line-height:1.3;grid-column:1}
.detail .dh .ib{grid-column:2;grid-row:1}
.detail .sub{grid-column:1/-1;display:flex;flex-wrap:wrap;gap:6px 10px;align-items:center;font:12.5px var(--mono);color:var(--muted)}
.detail .prev{margin-top:14px;border:1px solid var(--line);border-radius:10px;overflow:hidden;background:#fff;cursor:zoom-in}
.detail .prev img{width:100%;display:block}
.detail .empty{padding:40px 0;color:var(--faint);text-align:center}
.sec{margin-top:16px}.sec h4{margin:0 0 8px;font-size:12.5px;font-weight:600;letter-spacing:.06em;color:var(--muted);display:flex;justify-content:space-between}
.sec h4 span{font-weight:400;letter-spacing:0}
/* 오는 길·가는 길: 같은 모양의 한 줄 — [동작] 화면 이름 · 경로 */
.routes{display:flex;flex-direction:column;gap:4px}
.route{display:grid;grid-template-columns:auto minmax(0,1fr) auto;gap:10px;align-items:center;padding:6px 10px;border:1px solid var(--line);border-radius:8px;
 color:var(--ink);text-decoration:none;cursor:pointer;font-size:13.5px;background:var(--surface)}
.route:hover{border-color:var(--accent);background:var(--accent-soft)}
.route.here{border-color:var(--accent);background:var(--accent-soft);cursor:default}
.route .act{font-size:11.5px;font-weight:600;color:var(--accent);background:var(--accent-soft);border-radius:99px;padding:1px 8px;white-space:nowrap;max-width:120px;overflow:hidden;text-overflow:ellipsis}
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
@media (max-width:1180px){.stage,.stage.open,.stage.nol,.stage.nol.open{grid-template-columns:220px minmax(0,1fr);height:auto}
 .stage.open .side{grid-column:1/-1;max-height:70vh}.canvas{height:60vh}}
@media (max-width:760px){.stage,.stage.open,.stage.nol,.stage.nol.open{grid-template-columns:1fr}.pane.left{max-height:40vh}}
""".replace("__CW__", str(CARD_W)).replace("__CH__", str(CARD_H)).replace("__TH__", str(THUMB_H))

MAP_JS = r"""<script>
const G = JSON.parse(document.getElementById('g').textContent);
const esc = s => String(s ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const shot = id => G.shots[id] || '';
const nodeName = id => { const n = G.nodes[id]; return esc(n.name) + (n.variant ? ` <em>${n.variant}</em>` : ''); };
const stage = document.getElementById('stage'), side = document.getElementById('side'), lb = document.getElementById('lb'), modal = document.getElementById('modal');
const testByName = Object.fromEntries(G.tests.map(t => [t.name, t]));
const edgeBetween = (a, b) => G.edges.find(e => e.src === a && e.dst === b);
let current = null;

function status(n){ if(!G.compared) return ''; if(n.failed) return 'bad'; return n.tests.some(t => (testByName[t] || {}).status) ? 'ok' : ''; }
function openLb(src, cap){ if(!src) return; lb.querySelector('img').src = src; lb.querySelector('.cap').textContent = cap || ''; lb.classList.add('on'); }
lb.addEventListener('click', () => lb.classList.remove('on'));
function closeModal(){ modal.classList.remove('on'); }
modal.addEventListener('click', e => { if(e.target === modal) closeModal(); });
addEventListener('keydown', e => { if(e.key !== 'Escape') return; if(lb.classList.contains('on')) lb.classList.remove('on'); else if(modal.classList.contains('on')) closeModal(); else closeSide(); });

const routeRow = (id, act, here) => `<a class="route ${here ? 'here' : ''}" ${here ? '' : `data-go="${id}"`}><span class="act">${esc(act)}</span><span class="nm">${nodeName(id)}</span><span class="path">${esc(G.nodes[id].url)}</span></a>`;

function show(id){
  const n = G.nodes[id]; if(!n) return;
  current = id;
  stage.classList.add('open'); fit();
  document.querySelectorAll('.node').forEach(el => el.classList.toggle('sel', el.dataset.id === id));
  document.querySelectorAll('.row').forEach(el => el.classList.toggle('sel', el.dataset.id === id));
  document.querySelectorAll('.edge').forEach(el => el.classList.toggle('hot', el.dataset.src === id || el.dataset.dst === id));
  const st = status(n);
  const path = G.paths[id] || [id];
  const outs = G.edges.filter(e => e.src === id);
  let h = `<div class="dh"><h3>${nodeName(id)}</h3><button class="ib" id="closeSide" type="button" aria-label="닫기">×</button><div class="sub"><span>${esc(n.url)}</span>`
    + (st === 'bad' ? '<span class="pill bad">as-is와 다름</span>' : st === 'ok' ? '<span class="pill ok">as-is와 같음</span>' : '')
    + `<span>테스트 ${n.tests.length}개</span>${n.local ? `<span>화면 안 동작 ${n.local}</span>` : ''}</div></div>`;
  h += n.shot ? `<div class="prev" title="크게 보기" data-lb="${n.shot}"><img src="${shot(n.shot)}" alt="${esc(n.name)} 화면"></div>` : `<div class="empty">캡처 없음</div>`;
  if(n.hint) h += `<div class="sec"><h4>이 화면의 특징</h4><div>${esc(n.hint)}</div></div>`;
  h += `<div class="sec"><h4>시작에서 오는 길 <span>${path.length - 1}번 눌러서</span></h4><div class="routes">`
    + path.map((p, i) => { const e = i ? edgeBetween(path[i - 1], p) : null; return routeRow(p, i ? (e ? e.actions[0] : '…') : '시작', p === id); }).join('') + `</div></div>`;
  h += `<div class="sec"><h4>여기서 갈 수 있는 곳 <span>${outs.length}곳</span></h4><div class="routes">`
    + (outs.map(e => routeRow(e.dst, e.actions.join(' · '), false)).join('') || '<span class="tid">없음</span>') + `</div></div>`;
  const byTest = {};
  n.visits.forEach(v => (byTest[v.test] = byTest[v.test] || []).push(v));
  const names = Object.keys(byTest).sort((a, b) => (byTest[b].some(v => v.failed) - byTest[a].some(v => v.failed)));
  h += `<div class="sec"><h4>이 화면을 지나는 테스트 <span>${names.length}개 · 누르면 시나리오</span></h4><div class="trows">`
    + names.map(t => { const test = testByName[t], failedHere = byTest[t].some(v => v.failed);
        const pill = test.status === 'fail' ? '<span class="pill bad">다름</span>' : test.status === 'pass' ? '<span class="pill ok">같음</span>' : '<span class="pill">기록</span>';
        return `<button type="button" class="trow ${failedHere ? 'fail' : ''}" data-test="${esc(t)}"><span><b>${esc(test.title)}</b><small>${esc(t)}</small></span>${pill}<span class="chev">›</span></button>`; }).join('')
    + `</div></div>`;
  side.innerHTML = h;
  side.scrollTop = 0;
  side.querySelector('#closeSide').addEventListener('click', closeSide);
  side.querySelectorAll('[data-go]').forEach(a => a.addEventListener('click', () => go(a.dataset.go)));
  side.querySelectorAll('[data-lb]').forEach(a => a.addEventListener('click', () => openLb(shot(a.dataset.lb), n.name)));
  side.querySelectorAll('[data-test]').forEach(b => b.addEventListener('click', () => openTest(b.dataset.test, id)));
  try { history.replaceState(null, '', '#' + id); } catch(e) {}
}
function closeSide(){
  stage.classList.remove('open'); current = null;
  document.querySelectorAll('.node.sel, .row.sel').forEach(el => el.classList.remove('sel'));
  document.querySelectorAll('.edge.hot').forEach(el => el.classList.remove('hot'));
  try { history.replaceState(null, '', location.pathname); } catch(e) {}
  fit();
}
/* 시나리오 팝업: 필름스트립 + 단계 + (다르면) 무엇이 달랐는지 */
function openTest(name, hereId){
  const test = testByName[name]; if(!test) return;
  const hereSteps = new Set(test.seq.filter(s => s.node === hereId).map(s => s.index));
  const pill = test.status === 'fail' ? '<span class="pill bad">as-is와 다름</span>' : test.status === 'pass' ? '<span class="pill ok">as-is와 같음</span>' : '';
  let h = `<div class="mh"><div><b>${esc(test.title)}</b><small>${esc(name)} · ${test.seq.length}단계</small></div>${pill}<button class="ib" type="button" id="closeModal" aria-label="닫기">×</button></div><div class="mb">`;
  h += `<div class="film">` + test.seq.map(s => `<button type="button" class="${hereSteps.has(s.index) ? 'here' : ''} ${s.failed ? 'fail' : ''}" data-lb="${s.shot}" data-cap="${esc(s.index + 1)}단계 · ${esc(s.action)}" title="${esc(s.action)}">`
      + (s.shot ? `<img src="${shot(s.shot)}" alt="">` : '') + `<span class="k">${s.index + 1}</span><span class="cap">${esc(s.action)}</span></button>`).join('') + `</div>`;
  h += `<div class="steps">` + test.seq.map(s => {
      const node = G.nodes[s.node] || {}, v = (node.visits || []).find(x => x.test === name && x.index === s.index) || {};
      return `<div class="visit ${s.failed ? 'fail' : ''} ${hereSteps.has(s.index) ? 'here' : ''}"><span class="no">${s.index + 1}</span><div><div>${v.action || esc(s.action)} <span class="tid">· ${nodeName(s.node)}</span></div>`
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
function go(id){ show(id); const el = document.querySelector(`.node[data-id="${id}"]`); if(el) el.scrollIntoView({block: 'nearest', inline: 'center', behavior: 'smooth'}); }
document.querySelectorAll('.node, .row').forEach(el => el.addEventListener('click', () => go(el.dataset.id)));
document.querySelectorAll('.node').forEach(el => {
  el.addEventListener('mouseenter', () => document.querySelectorAll('.edge').forEach(e => e.classList.toggle('dim', e.dataset.src !== el.dataset.id && e.dataset.dst !== el.dataset.id)));
  el.addEventListener('mouseleave', () => document.querySelectorAll('.edge').forEach(e => e.classList.remove('dim')));
});
/* 왼쪽 목록 접기 */
document.getElementById('tl').addEventListener('click', () => { stage.classList.toggle('nol'); document.getElementById('tl').textContent = stage.classList.contains('nol') ? '›' : '‹'; fit(); });
/* 검색·거르기 */
const q = document.getElementById('q'), f = document.getElementById('f');
function filter(){
  const s = q.value.trim().toLowerCase(), t = f.value;
  const keep = new Set();
  for(const [id, n] of Object.entries(G.nodes)){
    const hit = (!s || n.name.toLowerCase().includes(s) || n.url.toLowerCase().includes(s) || (n.hint || '').toLowerCase().includes(s)) && (!t || n.tests.includes(t));
    if(hit) keep.add(id);
  }
  document.querySelectorAll('.node, .row').forEach(el => el.classList.toggle('dim', !keep.has(el.dataset.id)));
  document.querySelectorAll('.edge').forEach(el => el.classList.toggle('dim', !(keep.has(el.dataset.src) && keep.has(el.dataset.dst)) || (t && !el.dataset.tests.split('|').includes(t))));
}
q.addEventListener('input', filter); f.addEventListener('change', filter);
/* 확대·축소·끌기 */
const inner = document.getElementById('inner'), holder = document.getElementById('holder'), cv = document.getElementById('canvas');
const W = +inner.dataset.w, H = +inner.dataset.h; let z = 1;
function zoom(v){ z = Math.max(0.3, Math.min(1.6, v)); inner.style.transform = `scale(${z})`; holder.style.width = W * z + 'px'; holder.style.height = H * z + 'px'; }
function fit(){ requestAnimationFrame(() => zoom(Math.min(1, Math.max(0.7, (cv.clientWidth - 24) / W)))); }
document.getElementById('zf').onclick = fit; document.getElementById('zi').onclick = () => zoom(z + 0.15); document.getElementById('zo').onclick = () => zoom(z - 0.15);
let drag = null;
cv.addEventListener('mousedown', e => { if(e.target.closest('.node')) return; drag = {x: e.clientX, y: e.clientY, l: cv.scrollLeft, t: cv.scrollTop}; cv.classList.add('drag'); });
addEventListener('mousemove', e => { if(!drag) return; cv.scrollLeft = drag.l - (e.clientX - drag.x); cv.scrollTop = drag.t - (e.clientY - drag.y); });
addEventListener('mouseup', () => { drag = null; cv.classList.remove('drag'); });
cv.addEventListener('wheel', e => { if(!e.ctrlKey && !e.metaKey) return; e.preventDefault(); zoom(z - Math.sign(e.deltaY) * 0.1); }, {passive: false});
fit(); addEventListener('resize', fit);
const first = (location.hash || '').slice(1);
if(G.nodes[first]) go(first);
</script>"""


def render(g: dict[str, Any]) -> str:
    pos, paths = layout(g)
    g["paths"] = paths
    ncol = max((c for c, _ in pos.values()), default=0) + 1
    nrow = max((r for _, r in pos.values()), default=0) + 1
    backs = [e for e in g["edges"] if pos[e["dst"]][0] <= pos[e["src"]][0]]
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
    for e in g["edges"]:
        (x1, y1), (x2, y2) = xy[e["src"]], xy[e["dst"]]
        label = e["actions"][0][:16] + (f" +{len(e['actions']) - 1}" if len(e["actions"]) > 1 else "")
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
        parts.append(pill(mx, my, label, e["tests"], cls).replace("{src}", e["src"]).replace("{dst}", e["dst"]).replace("{path}", p))
    svg = (f"<svg class='links' width='{w}' height='{hgt}' viewBox='0 0 {w} {hgt}'><defs>"
           "<marker id='arr' viewBox='0 0 10 10' refX='9' refY='5' markerWidth='8' markerHeight='8' orient='auto'><path d='M0,0 L10,5 L0,10 z' fill='var(--accent)'/></marker>"
           "<marker id='arrb' viewBox='0 0 10 10' refX='9' refY='5' markerWidth='8' markerHeight='8' orient='auto'><path d='M0,0 L10,5 L0,10 z' fill='var(--faint)'/></marker>"
           "</defs>" + "".join(parts) + "</svg>")

    def node_status(n: dict[str, Any]) -> str:
        if not g["compared"]:
            return ""
        if n["failed"]:
            return "bad"
        return "ok" if any(t["status"] for t in g["tests"] if t["name"] in n["tests"]) else ""

    cards, rows = [], []
    for nid in g["order"]:
        n, (x, y) = g["nodes"][nid], xy[nid]
        st = node_status(n)
        name = html._e(n["name"]) + (f"<em>{n['variant']}</em>" if n.get("variant") else "")
        tag = "<span class='tag'>시작</span>" if nid == g["start"] else ("<span class='tag'>다름</span>" if st == "bad" else "")
        thumb = f"<img src='{g['shots'][n['shot']]}' alt='' loading='lazy'>" if n["shot"] else "<span class='no'>캡처 없음</span>"
        cards.append(f"<button class='node {st}' data-id='{nid}' style='left:{x}px;top:{y}px' type='button' title='{html._e(n['name'])}'>"
                     f"<div class='strip'></div><div class='chrome'><i></i><i></i><i></i></div><div class='th'>{thumb}</div>{tag}"
                     f"<div class='body'><div class='nm'>{name}</div><div class='hint'>{html._e(n['hint'])}</div></div>"
                     f"<div class='meta'><span>{html._e(n['url'])}</span><b>테스트 {len(n['tests'])}</b></div></button>")
        rows.append(f"<button class='row' data-id='{nid}' type='button'><span class='dot {st}'></span>"
                    f"<span class='nm'>{name}{'<small>' + html._e(n['hint']) + '</small>' if n['hint'] else ''}</span><span class='ct'>{len(n['tests'])}</span></button>")

    failed = sum(n["failed"] for n in g["nodes"].values())
    if not g["compared"]:
        stamp, lede = ("ok", f"화면 {len(g['nodes'])}", f"연결 {len(g['edges'])}"), "골든에 기록된 as-is 동작을 화면 단위로 이은 지도입니다. 화면을 누르면 오는 길·가는 길과 거기를 지나는 테스트가 나옵니다."
    elif failed:
        stamp, lede = ("bad", f"다른 화면 {failed}", f"전체 {len(g['nodes'])}개 중"), "빨간 화면에서 to-be가 as-is와 다르게 동작했습니다. 누르면 무엇이 달랐는지 나옵니다."
    else:
        stamp, lede = ("ok", "모두 같음", f"화면 {len(g['nodes'])}개"), "모든 화면에서 to-be가 as-is와 같게 동작했습니다."
    opts = "".join(f"<option value='{html._e(t['name'])}'>{html._e(t['title'])}</option>" for t in g["tests"])
    legend = ("<div class='legend'><span><i class='st'></i>시작 화면</span>"
              + ("<span><i class='ok'></i>as-is와 같음</span><span><i class='bad'></i>as-is와 다름</span>" if g["compared"] else "")
              + "<span><span class='ln'></span>동작 → 다음 화면</span><span><span class='ln back'></span>되돌아가기</span></div>")
    body = (f"<header class='head'><div class='eyebrow'>화면 지도</div><div class='stamp {stamp[0]}'>{stamp[1]}<small>{stamp[2]}</small></div>"
            f"<h1>{html._e(g['app'])}</h1><p class='lede'>{lede}</p>"
            f"<div class='prov'><span><b>기준</b> golden/{html._e(g['app'])}</span>"
            + (f"<span><b>비교 대상</b> {html._e(g['target'])}</span>" if g["target"] else "")
            + f"<span><b>테스트</b> {len(g['tests'])}개</span></div></header>{legend}"
            f"<div class='toolbar'><input id='q' type='search' placeholder='화면 이름·주소·문구로 찾기' aria-label='화면 찾기'>"
            f"<select id='f' aria-label='테스트로 거르기'><option value=''>모든 테스트</option>{opts}</select>"
            f"<span class='zoom'><button type='button' id='zo' aria-label='축소'>－</button><button type='button' id='zf'>맞춤</button><button type='button' id='zi' aria-label='확대'>＋</button></span></div>"
            f"<div class='stage' id='stage'><div class='pane left'><h2><span>화면 {len(g['nodes'])}개</span><button class='ib' id='tl' type='button' aria-label='목록 접기'>‹</button></h2><div class='list'>{''.join(rows)}</div></div>"
            f"<div class='pane canvas' id='canvas'><div id='holder' style='position:relative;width:{w}px;height:{hgt}px'>"
            f"<div id='inner' data-w='{w}' data-h='{hgt}' style='position:absolute;left:0;top:0;width:{w}px;height:{hgt}px;transform-origin:0 0'>{svg}{''.join(cards)}</div></div></div>"
            f"<aside class='pane side detail' id='side' aria-live='polite'></aside></div>"
            f"<div class='modal' id='modal' role='dialog' aria-label='시나리오 상세'><div class='box'></div></div>"
            f"<div class='lb' id='lb' role='dialog' aria-label='화면 크게 보기'><div><img src='' alt=''><div class='cap'></div></div></div>")
    data = json.dumps(g, ensure_ascii=False).replace("</", "<\\/")
    page = html._page(f"{g['app']} 화면 지도", body, script=f"<script type='application/json' id='g'>{data}</script>{MAP_JS}")
    return page.replace("</style>", MAP_CSS + "</style>", 1)


def write(d: Path, out: Path, junit: Path | None = None, tests_dir: Path | None = None) -> Path:
    g = build(d, junit, tests_dir)
    if not g["tests"]:
        raise SystemExit(f"{d} has no recorded tests (record on as-is with pytest --record {d} first)")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(render(g), encoding="utf-8")
    print(f"{out} · 화면 {len(g['nodes'])}, 연결 {len(g['edges'])}, 테스트 {len(g['tests'])}"
          + (f", 다른 화면 {sum(n['failed'] for n in g['nodes'].values())}" if junit else ""))
    return out
