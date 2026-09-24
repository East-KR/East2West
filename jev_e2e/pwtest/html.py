"""사람이 보는 화면 두 장: 승인 검토(write_review)와 검증 결과(write_report). 파일 하나짜리 HTML, 서버 없이 브라우저로 연다.

원칙: 결론 먼저, 문장은 짧게, "무엇이 · 기준 · 실제"만. 원본 로그는 접어 둔다.
디자인: 검수 서류. 차분한 청록 회색 바탕, 상태는 도장(stamp)과 점 표시, 값은 고정폭 숫자. 라이트/다크 모두.
"""
from __future__ import annotations

import ast
import base64
import html as h
import json
import os
import re
import subprocess
import sys
import time
import webbrowser
from collections import Counter
from pathlib import Path
from typing import Any

from . import oracle

FONTS = ("<link rel='preconnect' href='https://fonts.googleapis.com'><link rel='preconnect' href='https://fonts.gstatic.com' crossorigin>"
         "<link rel='stylesheet' href='https://fonts.googleapis.com/css2?family=IBM+Plex+Mono:wght@400;500&"
         "family=IBM+Plex+Sans+KR:wght@400;500;600;700&display=swap'>")

CSS = """
:root{--bg:#F4F6F5;--surface:#FFFFFF;--sunk:#EDF1EF;--ink:#18211F;--muted:#5C6864;--faint:#8A9590;--line:#DCE2DF;
--accent:#0E6B61;--accent-soft:#E0EFEB;--warn:#A85807;--warn-soft:#FBEEDB;--bad:#B3261E;--bad-soft:#FBE7E4;--ok:#1B7443;--ok-soft:#E1F1E7;
--sans:"IBM Plex Sans KR","Apple SD Gothic Neo","Malgun Gothic",system-ui,sans-serif;--mono:"IBM Plex Mono",ui-monospace,"SF Mono",Menlo,monospace;color-scheme:light}
@media (prefers-color-scheme:dark){:root:not([data-theme="light"]){--bg:#0F1413;--surface:#161C1B;--sunk:#1D2524;--ink:#E3EAE7;--muted:#9AA6A1;--faint:#6D7975;
--line:#29322F;--accent:#5CC2B3;--accent-soft:#132E2A;--warn:#E9A553;--warn-soft:#30230F;--bad:#F2877D;--bad-soft:#381B18;--ok:#6BCB92;--ok-soft:#13301E;color-scheme:dark}}
:root[data-theme="dark"]{--bg:#0F1413;--surface:#161C1B;--sunk:#1D2524;--ink:#E3EAE7;--muted:#9AA6A1;--faint:#6D7975;
--line:#29322F;--accent:#5CC2B3;--accent-soft:#132E2A;--warn:#E9A553;--warn-soft:#30230F;--bad:#F2877D;--bad-soft:#381B18;--ok:#6BCB92;--ok-soft:#13301E;color-scheme:dark}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--ink);font:15px/1.65 var(--sans);-webkit-font-smoothing:antialiased}
main{max-width:1040px;margin:0 auto;padding-inline:clamp(16px,4vw,32px);padding-block:40px 140px;display:flex;flex-direction:column;gap:36px}
h1,h2,h3{margin:0;text-wrap:balance}
code,.mono{font-family:var(--mono);font-variant-numeric:tabular-nums}
a{color:var(--accent)}
:focus-visible{outline:2px solid var(--accent);outline-offset:2px;border-radius:4px}

/* 머리 */
.head{display:grid;grid-template-columns:1fr auto;gap:8px 24px;align-items:start}
.eyebrow{font-size:12px;font-weight:600;letter-spacing:.08em;color:var(--accent)}
.head h1{font-size:30px;font-weight:700;letter-spacing:-.01em;line-height:1.25}
.lede{grid-column:1/-1;color:var(--muted);max-width:62ch;margin:0}
.prov{grid-column:1/-1;display:flex;flex-wrap:wrap;gap:4px 18px;font-size:12.5px;color:var(--faint)}
.prov b{color:var(--muted);font-weight:500}
.stamp{grid-row:1/3;grid-column:2;align-self:center;border:2px solid currentColor;border-radius:6px;padding:8px 14px;text-align:center;
transform:rotate(-2deg);font-weight:700;font-size:17px;line-height:1.3;letter-spacing:.02em}
.stamp small{display:block;font-size:11.5px;font-weight:500;letter-spacing:0;opacity:.85}
.stamp.ok{color:var(--ok);background:var(--ok-soft)}.stamp.warn{color:var(--warn);background:var(--warn-soft)}.stamp.bad{color:var(--bad);background:var(--bad-soft)}

/* 요약 수치 */
.facts{display:flex;flex-wrap:wrap;margin:0;border-block:1px solid var(--line)}
.facts div{flex:1 1 120px;padding:14px 18px;border-left:1px solid var(--line)}.facts div:first-child{border-left:0;padding-left:0}
.facts dt{font-size:12.5px;color:var(--muted)}.facts dd{margin:0;font:600 24px/1.2 var(--mono);font-variant-numeric:tabular-nums}

/* 섹션 */
section{display:flex;flex-direction:column;gap:12px}
.sh{display:flex;align-items:baseline;gap:12px;flex-wrap:wrap}
.sh h2{font-size:18px;font-weight:600}.sh p{margin:0;color:var(--muted);font-size:13.5px}
.panel{background:var(--surface);border:1px solid var(--line);border-radius:10px}
.empty{padding:14px 18px;color:var(--faint);font-size:14px}

/* 검토 순서 */
.todo{list-style:none;margin:0;padding:0;display:grid;grid-template-columns:repeat(auto-fit,minmax(230px,1fr));gap:10px}
.todo li{display:flex;gap:12px;padding:14px 16px;background:var(--surface);border:1px solid var(--line);border-radius:10px;font-size:14px}
.todo .k{flex:none;width:24px;height:24px;border-radius:50%;background:var(--accent-soft);color:var(--accent);font:600 12.5px/24px var(--mono);text-align:center}
.todo a{color:inherit;text-decoration:none}.todo a:hover b{text-decoration:underline}

.notice{padding:12px 16px;border-radius:10px;background:var(--warn-soft);color:var(--ink);font-size:14px}
.notice b{color:var(--warn)}

/* 테스트 */
.tests{display:flex;flex-direction:column;gap:10px}
details.test{background:var(--surface);border:1px solid var(--line);border-radius:10px}
details.test>summary{list-style:none;cursor:pointer;padding:16px 18px;display:grid;grid-template-columns:1fr auto;gap:6px 16px}
details.test>summary::-webkit-details-marker{display:none}
details.test[open]>summary{border-bottom:1px solid var(--line)}
.tt{font-weight:600;font-size:15.5px}.tid{font:12px var(--mono);color:var(--faint)}
.tog{grid-row:1/3;grid-column:2;align-self:center;font-size:13px;color:var(--accent);white-space:nowrap}
.tog::after{content:" ↓"}details[open] .tog::after{content:" ↑"}
.vals{grid-column:1/-1;display:flex;flex-wrap:wrap;gap:6px;margin-top:6px}
.val{display:inline-flex;align-items:baseline;gap:6px;background:var(--sunk);border-radius:6px;padding:3px 9px;font-size:13px}
.val .k{color:var(--muted)}.val .v{font:500 13px var(--mono);font-variant-numeric:tabular-nums}
.val.chg{background:var(--warn-soft)}.val.add{background:var(--accent-soft)}.val s{color:var(--faint)}
.tag{display:inline-block;border-radius:4px;padding:0 7px;font-size:11.5px;font-weight:600;line-height:20px;vertical-align:1px;margin-left:6px}
.tag.new{background:var(--accent-soft);color:var(--accent)}.tag.chg{background:var(--warn-soft);color:var(--warn)}

/* 단계 타임라인 */
ol.steps{list-style:none;margin:0;padding:6px 18px 10px}
ol.steps li{position:relative;display:grid;grid-template-columns:36px minmax(0,300px) minmax(0,1fr);gap:16px;padding:14px 0}
ol.steps li+li{border-top:1px dashed var(--line)}
.no{width:28px;height:28px;border-radius:50%;border:1.5px solid var(--accent);color:var(--accent);font:600 13px/25px var(--mono);text-align:center;background:var(--surface)}
.thumb{display:block;width:100%;aspect-ratio:16/10;overflow:hidden;border:1px solid var(--line);border-radius:6px;background:#fff;position:relative}
.thumb img{width:200%;max-width:none;display:block}
.thumb span{position:absolute;right:6px;bottom:6px;font-size:11px;background:rgba(15,20,19,.72);color:#fff;border-radius:4px;padding:1px 7px}
.noshot{aspect-ratio:16/10;border:1px dashed var(--line);border-radius:6px;display:grid;place-items:center;color:var(--faint);font-size:12.5px}
.act{font-size:15px}.verb{display:inline-block;min-width:44px;font-size:12px;font-weight:600;color:var(--muted);letter-spacing:.02em}
.act b{font-weight:600}
.row{margin-top:8px;display:flex;flex-wrap:wrap;gap:4px 6px;align-items:baseline;font-size:13px}
.row .lab{color:var(--faint);font-size:12px;min-width:44px}
.row .it{font-family:var(--mono);background:var(--sunk);border-radius:4px;padding:0 6px}
.check{margin-top:6px;display:flex;gap:8px;align-items:baseline;font-size:13.5px;color:var(--ok)}
.check svg{flex:none;transform:translateY(2px)}.check .k{color:var(--muted)}.check .v{font-family:var(--mono);font-weight:500;color:var(--ink)}
.dlg{margin-top:8px;display:inline-flex;gap:8px;align-items:center;font-size:13px;border:1px solid var(--line);border-radius:6px;padding:4px 10px;background:var(--sunk)}
.dlg .ans{font-size:12px;color:var(--accent);font-weight:600}

/* 표 */
.tbl{overflow-x:auto}
table{border-collapse:collapse;width:100%;font-size:14px}
th,td{text-align:left;padding:10px 16px;border-bottom:1px solid var(--line);vertical-align:top}
tr:last-child td{border-bottom:0}
th{font-size:12px;font-weight:600;color:var(--muted);letter-spacing:.03em;background:var(--sunk)}
td.m{font-family:var(--mono);font-variant-numeric:tabular-nums}
td.was{color:var(--muted)}td.now{color:var(--bad);font-weight:600}
.chip{display:inline-block;font:12.5px var(--mono);background:var(--sunk);border-radius:4px;padding:1px 7px;margin:1px 3px 1px 0}
.warnchip{color:var(--warn);font-weight:600;font-size:13px}

/* 결과 카드 */
.diff{padding:16px 18px;display:flex;flex-direction:column;gap:10px}
.diff .ttl{display:flex;gap:10px;align-items:baseline;flex-wrap:wrap}
.pill{display:inline-block;border-radius:99px;padding:1px 10px;font-size:12px;font-weight:600}
.pill.bad{background:var(--bad-soft);color:var(--bad)}.pill.warn{background:var(--warn-soft);color:var(--warn)}.pill.ok{background:var(--ok-soft);color:var(--ok)}
details.more>summary{cursor:pointer;color:var(--muted);font-size:13px}
pre{background:var(--sunk);border-radius:8px;padding:12px;overflow:auto;font:12px/1.5 var(--mono);white-space:pre-wrap}
img.shot{max-width:100%;border:1px solid var(--line);border-radius:6px;margin-top:8px}
.checks{list-style:none;margin:0;padding:4px 18px}
.checks li{display:grid;grid-template-columns:18px 1fr auto;gap:12px;align-items:baseline;padding:10px 0;border-bottom:1px solid var(--line);font-size:14px}
.checks li:last-child{border-bottom:0}.checks .d{color:var(--faint);font:12.5px var(--mono);text-align:right}
.dot{width:10px;height:10px;border-radius:50%;display:inline-block}.dot.ok{background:var(--ok)}.dot.bad{background:var(--bad)}
.meter{padding:16px 18px}.meter .n{font:600 28px var(--mono)}.meter .bar{height:6px;border-radius:99px;background:var(--sunk);margin-top:10px;overflow:hidden}
.meter .bar i{display:block;height:100%;background:var(--accent)}
.same{display:flex;flex-wrap:wrap;gap:6px;padding:14px 18px}.same span{font-size:13px;background:var(--ok-soft);color:var(--ink);border-radius:6px;padding:3px 10px}

/* 하단 승인 막대 */
.bar-approve{position:fixed;left:0;right:0;bottom:0;background:var(--surface);border-top:1px solid var(--line);
padding:12px clamp(16px,4vw,32px) calc(12px + env(safe-area-inset-bottom,0px));box-shadow:0 -6px 24px rgba(15,20,19,.06)}
.bar-approve .in{max-width:1040px;margin:0 auto;display:flex;flex-wrap:wrap;gap:10px 16px;align-items:center}
.bar-approve p{margin:0;font-size:14px;flex:1 1 280px}.bar-approve p b{color:var(--accent)}
.cmd{display:flex;align-items:center;gap:0;border:1px solid var(--line);border-radius:8px;overflow:hidden;max-width:100%}
.cmd code{padding:8px 12px;font-size:13px;background:var(--sunk);white-space:nowrap;overflow-x:auto;max-width:60vw}
.cmd button{border:0;background:var(--accent);color:#fff;font:600 13px var(--sans);padding:8px 14px;cursor:pointer}
.cmd button:hover{filter:brightness(1.08)}
@media (max-width:760px){
 .head{grid-template-columns:1fr}.stamp{grid-row:auto;grid-column:1;justify-self:start}
 ol.steps li{grid-template-columns:28px 1fr}ol.steps li>.shotcell{grid-column:2}ol.steps li>.txt{grid-column:2}
 .facts div{border-left:0;padding-left:0}
}
@media (prefers-reduced-motion:reduce){*{transition:none!important}}
"""

COPY_JS = """<script>
document.querySelectorAll('[data-copy]').forEach(function(b){b.addEventListener('click',function(){
 var t=b.getAttribute('data-copy');var done=function(){b.textContent='복사됨';setTimeout(function(){b.textContent='복사'},1500)};
 var sel=function(){var c=b.previousElementSibling;var r=document.createRange();r.selectNodeContents(c);var s=getSelection();s.removeAllRanges();s.addRange(r);b.textContent='선택됨 · ⌘C'};
 try{navigator.clipboard.writeText(t).then(done,sel)}catch(e){sel()}});});
</script>"""

CHECK_SVG = ("<svg width='14' height='14' viewBox='0 0 14 14' aria-hidden='true'><path d='M2.5 7.5l3 3 6-7' fill='none' "
             "stroke='currentColor' stroke-width='2' stroke-linecap='round' stroke-linejoin='round'/></svg>")

KIND = {"field": "칸", "dialog": "알림창", "text": "화면 문구", "snapshot": "화면 구조", "url_path": "주소", "url_contains": "주소에 포함",
        "title": "제목", "no_text": "사라진 문구", "text_matches": "문구 형식"}


def _e(s: Any) -> str:
    return h.escape("" if s is None else str(s))


def _page(title: str, body: str, *, script: str = "") -> str:
    return (f"<!doctype html><html lang='ko'><head><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1,viewport-fit=cover'>"
            f"<title>{_e(title)}</title>{FONTS}<style>{CSS}</style></head><body><main>{body}</main>{script}</body></html>")


def _val(a: dict[str, Any], cls: str = "", old: dict[str, Any] | None = None) -> str:
    """기대값 한 개: '부가세 120' / '알림창 저장하시겠습니까?'"""
    if a["kind"] == "field":
        k, v = a["target"], (a["value"] if a["value"] != "" else "(빈 값)")
    else:
        k, v = KIND.get(a["kind"], a["kind"]), a["target"]
    prev = ""
    if old is not None:
        ov = old["value"] if old["kind"] == "field" else old["target"]
        prev = f" <s class='mono'>{_e(ov)}</s>"
    return f"<span class='val {cls}'><span class='k'>{_e(k)}</span><span class='v'>{_e(v)}</span>{prev}</span>"


def docstrings(tests_dir: Path | None) -> dict[str, str]:
    """테스트 함수 이름 → docstring 첫 줄. 사람이 읽는 제목으로 쓴다."""
    out: dict[str, str] = {}
    if not tests_dir or not tests_dir.exists():
        return out
    for f in tests_dir.rglob("test_*.py"):
        try:
            tree = ast.parse(f.read_text(encoding="utf-8"))
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef) and node.name.startswith("test_"):
                doc = ast.get_docstring(node)
                if doc:
                    out[node.name] = doc.strip().splitlines()[0].split(". ")[0]
    return out


def _title(name: str, docs: dict[str, str]) -> str:
    base, _, param = name.partition("[")
    t = docs.get(base, base)
    return t + (f" · {param.rstrip(']')}" if param else "")


def open_in_browser(path: Path) -> None:
    uri = path.resolve().as_uri()
    try:
        if sys.platform == "darwin":
            subprocess.run(["open", uri], check=False)
        else:
            webbrowser.open(uri)
    except Exception:
        pass


# -- 승인 검토 ---------------------------------------------------------------------------
ACTION = re.compile(r'^(\w+) "(.+?)"(?: = (.*))?$')


def _action(kind: str, text: str) -> str:
    m = ACTION.match(text)
    if kind == "goto":
        return f"<span class='verb'>열기</span><code>{_e(text)}</code>"
    if kind == "press":
        return f"<span class='verb'>키</span><b>{_e(text)}</b>"
    if not m:
        return _e(text)
    role, name, value = m.groups()
    if kind == "fill":
        return f"<span class='verb'>입력</span><b>{_e(name)}</b> ← <code>{_e(value)}</code>"
    if kind == "select":
        return f"<span class='verb'>선택</span><b>{_e(name)}</b> ← {_e(value)}"
    if kind == "check":
        return f"<span class='verb'>체크</span><b>{_e(name)}</b>"
    return f"<span class='verb'>누르기</span><b>{_e(name)}</b>" + (" <span class='tid'>링크</span>" if role == "link" else "")


def _seen(line: str) -> str:
    m = CONTROL.match(line)
    if m:
        return f"{m.group(2)} {m.group(3)}" if m.group(3) else m.group(2)
    return line.removeprefix("text: ")


def _timeline(d: Path, name: str, rules: list[str], out_dir: Path) -> str:
    """골든에 기록된 as-is 동작: 단계마다 화면, 동작, 새로 나타난 내용, 뜬 알림창, 그 시점에 확인한 기대값."""
    from jev_e2e.observe import flatten, mask
    data = json.loads((d / f"{name}.json").read_text(encoding="utf-8"))
    by_step: dict[int, list[dict[str, Any]]] = {}
    for a in data.get("assertions", []):
        by_step.setdefault(a["step"] - 1, []).append(a)
    items, prev = [], Counter()
    for o in data.get("steps", []):
        cur = Counter(mask(flatten(o.get("snapshot", ""), keep_urls=False), rules))
        new = [l for l in (cur - prev).elements() if not l.startswith(("option ", "link "))]
        prev = cur
        n = o["index"] + 1
        if o.get("shot") and (d / o["shot"]).exists():
            rel = os.path.relpath(d / o["shot"], out_dir)
            shot = f"<a class='thumb' href='{_e(rel)}' target='_blank'><img src='{_e(rel)}' loading='lazy' alt='{n}단계 화면'><span>크게</span></a>"
        else:
            shot = "<div class='noshot'>화면 없음</div>"
        shown = list(dict.fromkeys(_clean(_seen(l)) for l in new))  # 라벨 글자와 입력칸 이름이 같은 줄은 한 번만
        seen = ("<div class='row'><span class='lab'>나타남</span>" + "".join(f"<span class='it'>{_e(x)}</span>" for x in shown[:6])
                + (f"<span class='lab'>외 {len(shown) - 6}</span>" if len(shown) > 6 else "") + "</div>") if shown else ""
        dlg = "".join(f"<div class='dlg'><span class='verb'>{'확인창' if x['type'] == 'confirm' else '알림창'}</span><b>{_e(x['message'])}</b>"
                      f"<span class='ans'>→ {'확인' if x['action'] == 'accept' else '취소'}</span></div>" for x in o.get("dialogs", []))
        checks = "".join(f"<div class='check'>{CHECK_SVG}<span><span class='k'>{_e(a['target'] if a['kind'] == 'field' else KIND.get(a['kind'], a['kind']))}</span> "
                         f"<span class='v'>{_e((a['value'] if a['value'] != '' else '(빈 값)') if a['kind'] == 'field' else a['target'])}</span></span></div>"
                         for a in by_step.get(o["index"], []))
        items.append(f"<li><div class='no'>{n}</div><div class='shotcell'>{shot}</div><div class='txt'><div class='act'>{_action(o['kind'], o['text'])}</div>"
                     f"{seen}{dlg}{checks}</div></li>")
    return "<ol class='steps'>" + "".join(items) + "</ol>"


def write_review(d: Path, *, tests_dir: Path | None = None, out: Path | None = None) -> Path:
    tests_dir = tests_dir or Path("e2e") / d.name
    docs = docstrings(tests_dir)
    st = oracle.status(d)
    prev = oracle.approved_assertions(d)
    ts = oracle.tests(d)
    cfg = oracle.load_config(d)
    maps = oracle.name_maps(d)
    masks = oracle.mask_hits(d)
    eqs = cfg.get("equivalent_mutants", [])
    out = out or Path("reports") / f"review-{d.name}.html"

    if st["ok"]:
        stamp = ("ok", "승인됨", f"{_e(st['approved_by'])} · {_e((st['approved_at'] or '')[:16])}")
    elif (d / oracle.MANIFEST).exists():
        stamp = ("warn", "재승인 필요", "승인 후 기록이 바뀜")
    else:
        stamp = ("warn", "승인 필요", "처음 승인")
    recorded = sorted({(t["base_url"], (t["recorded_at"] or "")[:16]) for t in ts})
    urls = ", ".join(sorted({u for u, _ in recorded}))
    last = max((r for _, r in recorded), default="")

    changed = new = 0
    blocks = []
    for t in ts:
        before = None if prev is None else prev.get(t["name"])
        vals = []
        for i, a in enumerate(t["assertions"]):
            old = before[i] if before is not None and i < len(before) else None
            if before is not None and old is None:
                vals.append(_val(a, "add"))
            elif old is not None and old != a:
                vals.append(_val(a, "chg", old))
            else:
                vals.append(_val(a))
        tag = ""
        if prev is not None and before is None:
            tag, new = "<span class='tag new'>새 테스트</span>", new + 1
        elif before is not None and before != t["assertions"]:
            tag, changed = "<span class='tag chg'>기대값 바뀜</span>", changed + 1
        blocks.append(f"<details class='test'><summary><div class='tt'>{_e(_title(t['name'], docs))}{tag}</div>"
                      f"<span class='tog'>{t['steps']}단계 보기</span><div class='tid'>{_e(t['name'])}</div>"
                      f"<div class='vals'>{''.join(vals) or '<span class=tid>명시한 확인 값 없음 · 화면 비교만</span>'}</div></summary>"
                      + _timeline(d, t["name"], cfg.get("ignore", []), out.parent) + "</details>")
    removed = [] if prev is None else sorted(set(prev) - {t["name"] for t in ts})

    B = [f"<header class='head'><div class='eyebrow'>기준 승인 검토</div><div class='stamp {stamp[0]}'>{stamp[1]}<small>{stamp[2]}</small></div>"
         f"<h1>{_e(d.name)}</h1><p class='lede'>as-is에서 기록한 동작이 to-be의 정답이 됩니다. 기록이 실제 업무와 맞는지 보고 승인하세요.</p>"
         f"<div class='prov'><span><b>기록한 곳</b> {_e(urls)}</span><span><b>마지막 기록</b> {_e(last)}</span><span><b>기준 폴더</b> {_e(d)}</span></div></header>"]
    facts = [("테스트", len(ts)), ("확인 값", sum(len(t["assertions"]) for t in ts)), ("가림 규칙", len(masks)),
             ("이름 변경", sum(len(m) for m in maps.values())), ("동등 결함", len(eqs))]
    B.append("<dl class='facts'>" + "".join(f"<div><dt>{k}</dt><dd>{v}</dd></div>" for k, v in facts) + "</dl>")
    if prev is not None and (changed or new or removed):
        B.append(f"<div class='notice'><b>지난 승인 이후</b> 기대값이 바뀐 테스트 {changed}개 · 새 테스트 {new}개 · 없어진 테스트 {len(removed)}개"
                 + (" (" + ", ".join(_e(r) for r in removed) + ")" if removed else "") + "</div>")
    B.append("<section><div class='sh'><h2>승인 전에 볼 것</h2></div><ol class='todo'>"
             "<li><span class='k'>1</span><a href='#tests'><b>테스트별 as-is 동작</b><br>단계별 화면과 확인 값이 실제 업무와 맞는지</a></li>"
             "<li><span class='k'>2</span><a href='#masks'><b>가리는 값</b><br>매번 바뀌는 값만 가리고 금액 같은 값은 안 가리는지</a></li>"
             "<li><span class='k'>3</span><a href='#rules'><b>이름 변경 · 동등 결함</b><br>바뀌어도 되는 라벨과 제외할 결함에 동의하는지</a></li></ol></section>")
    B.append("<section id='tests'><div class='sh'><h2>테스트별 as-is 동작</h2><p>펼치면 단계마다 기록된 화면이 나옵니다</p></div>"
             f"<div class='tests'>{''.join(blocks)}</div></section>")

    if masks:
        rows = "".join(f"<tr><td class='m'>{_e(m['rule'])}</td><td class='m'>{m['total']}곳</td><td>"
                       + ("".join(f"<span class='chip'>{_e(k)}</span>" for k, _ in m["samples"]) or "<span class='warnchip'>아무것도 가리지 않음</span>")
                       + "</td></tr>" for m in masks)
        mask_html = f"<div class='panel tbl'><table><tr><th>규칙</th><th>가린 곳</th><th>실제로 가린 값</th></tr>{rows}</table></div>"
    else:
        mask_html = "<div class='panel empty'>가리는 값 없음</div>"
    B.append(f"<section id='masks'><div class='sh'><h2>가리는 값</h2><p>비교에서 빼는 값입니다. 매번 바뀌는 값만 있어야 합니다</p></div>{mask_html}</section>")

    if maps:
        rows = "".join(f"<tr><td>{_e(t)}</td><td>{_e(a)}</td><td><b>{_e(b)}</b></td></tr>" for t, m in maps.items() for a, b in m.items())
        map_html = f"<div class='panel tbl'><table><tr><th>대상 to-be</th><th>as-is 이름</th><th>to-be 이름</th></tr>{rows}</table></div>"
    else:
        map_html = "<div class='panel empty'>이름 변경 없음</div>"
    if eqs:
        rows = "".join(f"<tr><td class='m'>{_e(e.get('path'))}</td><td class='m'>{_e(e.get('context'))}</td><td>{_e(e.get('reason', ''))}</td></tr>" for e in eqs)
        eq_html = f"<div class='panel tbl'><table><tr><th>화면</th><th>바꾼 곳</th><th>화면에 차이가 없는 이유</th></tr>{rows}</table></div>"
    else:
        eq_html = "<div class='panel empty'>동등 결함 없음</div>"
    B.append(f"<section id='rules'><div class='sh'><h2>이름 변경</h2><p>to-be에서 바뀌어도 되는 라벨</p></div>{map_html}"
             f"<div class='sh' style='margin-top:14px'><h2>동등 결함</h2><p>일부러 넣어도 화면이 같아서 탐지율에서 빼는 결함</p></div>{eq_html}</section>")

    cmd = f"uv run jev-e2e approve {d} --by <이름>"
    if st["ok"]:
        msg = f"<b>승인된 기준</b>입니다. 기록이 바뀌면 이 화면이 다시 만들어지고 재승인을 요청합니다."
        bar = f"<div class='bar-approve'><div class='in'><p>{msg}</p></div></div>"
    else:
        msg = "as-is 동작이 맞으면 <b>터미널</b>에서 승인하세요. 승인 중이면 <code>" + _e(d.name) + "</code>을 입력합니다. 틀린 게 있으면 승인하지 말고 담당자에게 알려 주세요."
        bar = (f"<div class='bar-approve'><div class='in'><p>{msg}</p><div class='cmd'><code>{_e(cmd)}</code>"
               f"<button type='button' data-copy='{_e(cmd)}'>복사</button></div></div></div>")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(_page(f"{d.name} 기준 승인", "".join(B) + bar, script=COPY_JS), encoding="utf-8")
    return out


# -- 검증 결과 ---------------------------------------------------------------------------
FIELD = re.compile(r"field \"(.+?)\" == '(.*?)': got '(.*?)'")
DIALOG_NE = re.compile(r"last dialog (None|'.*?') != '(.*?)'")
TEXT_MISSING = re.compile(r"text '(.+?)' visible")
NOT_FOUND = re.compile(r'(\w+) "(.+?)" not found in any frame')
DRIFT = re.compile(r"expectation (changed|added|removed) since as-is recording: (.+)")
DIALOGS = re.compile(r"dialogs: (\[.*?\]) → (\[.*?\])")
CONTROL = re.compile(r'^(\w+) "(.+?)"(?: \[[^\]]+\])*(?:: (.*))?$')


def _clean(v: str) -> str:
    return v.replace("<masked>", "(가림)")


def rows_for(messages: list[str]) -> tuple[list[tuple[str, str, str]], list[str]]:
    """실패 메시지 → (항목, as-is, to-be) 행과 기타 메모. 같은 행은 한 번만."""
    text = "\n".join(messages)
    rows: list[tuple[str, str, str]] = []
    notes: list[str] = []
    for m in DRIFT.finditer(text):
        notes.append("기대값이 as-is 기록 이후 바뀌었습니다: " + m.group(2))
    for m in FIELD.finditer(text):
        rows.append((m.group(1), m.group(2) or "(빈 값)", m.group(3) or "(빈 값)"))
    for m in DIALOG_NE.finditer(text):
        rows.append(("알림창", m.group(2), "(없음)" if m.group(1) == "None" else m.group(1).strip("'")))
    for m in DIALOGS.finditer(text):
        try:
            strip = lambda xs: [x.split(": ", 1)[-1] for x in xs]  # "alert: 문구" → "문구" (종류는 문구와 함께 보면 중복)
            g, a = strip(ast.literal_eval(m.group(1))), strip(ast.literal_eval(m.group(2)))
            rows.append(("알림창", " / ".join(g) or "(없음)", " / ".join(a) or "(없음)"))
        except Exception:
            pass
    for m in TEXT_MISSING.finditer(text):
        rows.append(("화면 문구", m.group(1), "(없음)"))
    for m in NOT_FOUND.finditer(text):
        rows.append((f"{m.group(2)} ({m.group(1)})", "있음", "찾을 수 없음"))
    removed, added = [], []
    for line in text.splitlines():
        s = line.strip()
        if s.startswith(("---", "+++", "@@")) or len(s) < 2:
            continue
        if s[0] == "-" and not s.startswith("- "):
            removed.append(s[1:])
        elif s[0] == "+":
            added.append(s[1:])
    key = lambda l: (CONTROL.match(l).group(2) if CONTROL.match(l) else None)
    val = lambda l: ((CONTROL.match(l).group(3) or "(값 없음)") if CONTROL.match(l) else l.removeprefix("text: "))
    used = set()
    for r in removed:
        k = key(r)
        match = next((i for i, a in enumerate(added) if i not in used and k is not None and key(a) == k), None)
        if match is not None:
            used.add(match)
            rows.append((k, _clean(val(r)), _clean(val(added[match]))))
        else:
            rows.append((k or "화면 문구", _clean(val(r)), "(없음)"))
    extra = [_clean(val(a)) for i, a in enumerate(added) if i not in used]
    if len(extra) > 2:  # to-be가 다른 화면에 머문 경우: 줄마다 행을 만들지 않고 한 줄로 요약
        rows.append(("to-be에만 있는 화면 내용", "(없음)", " · ".join(extra[:4]) + (f" 외 {len(extra) - 4}줄" if len(extra) > 4 else "")))
    else:
        rows += [((key(a) or "화면 문구"), "(없음)", _clean(val(a))) for i, a in enumerate(added) if i not in used]
    seen, out = set(), []
    for r in rows:
        if r not in seen and r[1] != r[2]:
            seen.add(r)
            out.append(r)
    return out, notes


def _shot(name: str, junit: Path) -> str:
    p = Path("reports") / f"{name}-fail.png"
    if not p.exists() or not (junit.stat().st_mtime - 900 <= p.stat().st_mtime <= junit.stat().st_mtime + 5):
        return ""
    return (f"<details class='more'><summary>실패 순간 화면</summary>"
            f"<img class='shot' src='data:image/png;base64,{base64.b64encode(p.read_bytes()).decode()}' alt='실패 순간 화면'></details>")


def write_report(*, oracle_dir: Path, checks: list[tuple[str, bool, str]], trusted: bool, runs: list[dict[str, Any]],
                 muts: list[dict[str, Any]], out: Path, tests_dir: Path | None = None) -> Path:
    docs = docstrings(tests_dir or Path("e2e") / oracle_dir.name)
    B = []
    for r in runs:
        cases = r["cases"]
        fails = [c for c in cases if c["status"] == "fail"]
        target = r["props"].get("base_url") or Path(r["path"]).stem.removeprefix("junit-")
        if not trusted:
            stamp = ("warn", "판정 보류", "믿을 수 없는 실행")
            lede = "아래 '믿을 수 있는가'에서 빨간 항목을 먼저 해결하세요."
        elif fails:
            stamp = ("bad", f"차이 {len(fails)}건", f"{len(cases)}개 중 {len(cases) - len(fails)}개 동일")
            lede = "to-be가 as-is와 다르게 동작한 곳입니다. 결함이면 수정 요청, 의도한 변경이면 기준 변경을 결정하세요."
        else:
            stamp = ("ok", "동일", f"{len(cases)}개 모두 같음")
            lede = "to-be가 as-is와 같게 동작합니다."
        B.append(f"<header class='head'><div class='eyebrow'>검증 결과</div><div class='stamp {stamp[0]}'>{stamp[1]}<small>{stamp[2]}</small></div>"
                 f"<h1>{_e(oracle_dir.name)}</h1><p class='lede'>{lede}</p>"
                 f"<div class='prov'><span><b>비교 대상</b> {_e(target)}</span><span><b>생성</b> {time.strftime('%Y-%m-%d %H:%M')}</span>"
                 f"<span><b>기준 폴더</b> {_e(oracle_dir)}</span></div></header>")
        if fails:
            cards = []
            for c in fails:
                rows, notes = rows_for(c["messages"])
                table = ("<div class='tbl'><table><tr><th>무엇이</th><th>as-is 기준</th><th>to-be</th></tr>"
                         + "".join(f"<tr><td>{_e(i)}</td><td class='m was'>{_e(a)}</td><td class='m now'>{_e(b)}</td></tr>" for i, a, b in rows[:10])
                         + "</table></div>") if rows else ""
                more = f"<div class='tid'>외 {len(rows) - 10}건은 원본 로그에</div>" if len(rows) > 10 else ""
                cards.append(f"<div class='panel diff'><div class='ttl'><span class='tt'>{_e(_title(c['name'], docs))}</span><span class='tid'>{_e(c['name'])}</span></div>"
                             + "".join(f"<div><span class='pill warn'>기대값 변경</span> {_e(n)}</div>" for n in notes) + table + more
                             + _shot(c["name"], Path(r["path"]))
                             + f"<details class='more'><summary>원본 로그</summary><pre>{_e(chr(10).join(c['messages']))}</pre></details></div>")
            B.append("<section><div class='sh'><h2>다른 점</h2><p>무엇이 · as-is 기준 · to-be</p></div>" + "".join(cards) + "</section>")
        same = [c for c in cases if c["status"] == "pass"]
        if same:
            B.append("<section><div class='sh'><h2>같은 동작</h2></div><div class='panel same'>"
                     + "".join(f"<span>{_e(_title(c['name'], docs))}</span>" for c in same) + "</div></section>")

    B.append("<section><div class='sh'><h2>믿을 수 있는가</h2><p>산출물에서 자동으로 확인한 항목</p></div><div class='panel'><ul class='checks'>"
             + "".join(f"<li><span class='dot {'ok' if ok else 'bad'}'></span><span>{_e(name)}</span><span class='d'>{_e(detail)}</span></li>"
                       for name, ok, detail in checks) + "</ul></div></section>")
    for m in muts:
        if m["mode"] != "expects+golden":
            continue
        surv = [x for x in m["mutants"] if not x["killed"]]
        B.append(f"<section><div class='sh'><h2>테스트가 결함을 잡는 능력</h2><p>일부러 넣은 결함을 몇 개나 잡았는지</p></div>"
                 f"<div class='panel meter'><span class='n'>{m['score']:.0%}</span> <span class='tid'>{m['killed']} / {m['total']}</span>"
                 f"<div class='bar'><i style='width:{m['score'] * 100:.0f}%'></i></div>"
                 + "".join(f"<div class='tid' style='margin-top:8px'>못 잡음 · {_e(x['path'])} · {_e(x['desc'][:90])}</div>" for x in surv[:5])
                 + "</div></section>")
    out = out.with_suffix(".html")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(_page(f"{oracle_dir.name} 검증 결과", "".join(B)), encoding="utf-8")
    return out
