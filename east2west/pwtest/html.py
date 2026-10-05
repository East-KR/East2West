"""검증 보고서 화면(render_report)과 다른 화면들이 같이 쓰는 조각: 페이지 틀(_page, CSS), 단계 요약(_action, _seen), 다른 점 표(rows_for), 캡처(_shot).
화면은 전부 통합 화면(east2west ui, hub.py)이 요청 때 조각(fragment: html·css·js)으로 만들어 한 문서 안에 끼운다 (iframe 없음). 골든 관리(시나리오)는 catalog.py, Screen Map은 map.py.

원칙: 결론 먼저, 문장은 짧게, "무엇이 · 기준 · 실제"만. 원본 로그는 접어 둔다.
디자인: 검수 서류. 차분한 청록 회색 바탕, 상태는 도장(stamp)과 점 표시, 값은 고정폭 숫자. 라이트/다크 모두.
"""
from __future__ import annotations

import ast
import html as h
import json
import os
import re
import time
from collections import Counter
from pathlib import Path
from typing import Any
from urllib.parse import quote

from . import oracle

FONTS = ("<link rel='preconnect' href='https://fonts.googleapis.com'><link rel='preconnect' href='https://fonts.gstatic.com' crossorigin>"
         "<link rel='stylesheet' href='https://fonts.googleapis.com/css2?family=IBM+Plex+Mono:wght@400;500&"
         "family=IBM+Plex+Sans+KR:wght@400;500;600;700&display=swap'>")

CSS = """
:root{--bg:#F4F6F5;--surface:#FFFFFF;--sunk:#EDF1EF;--ink:#18211F;--muted:#5C6864;--faint:#8A9590;--line:#DCE2DF;
--accent:#0E6B61;--accent-soft:#E0EFEB;--warn:#A85807;--warn-soft:#FBEEDB;--bad:#B3261E;--bad-soft:#FBE7E4;--ok:#1B7443;--ok-soft:#E1F1E7;
--accepted:#6B4FD6;--accepted-soft:#EEEAFB;--asis:#3F4B48;--tobe:#1F5FAD;
--sans:"IBM Plex Sans KR","Apple SD Gothic Neo","Malgun Gothic",system-ui,sans-serif;--mono:"IBM Plex Mono",ui-monospace,"SF Mono",Menlo,monospace;color-scheme:light}
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

/* 페이지 머리 + 칩: 통합 화면의 네 탭이 같은 언어를 쓴다. 칩 = 상태 한 줄 요약, 누를 수 있으면 거르기 */
.pgh{display:flex;align-items:center;gap:10px;flex-wrap:wrap}
.pgh .chips{display:flex;flex-wrap:wrap;gap:6px;align-items:center;flex:1;min-width:0}
/* (i) 설명: 제목·출처·긴 설명은 여기 숨겨 두고 호버·초점에 보인다. 제목은 통합 화면 위 막대 가운데에 있다 */
.ihelp{position:relative;display:inline-grid;place-items:center;width:24px;height:24px;border-radius:50%;border:1px solid var(--line);color:var(--muted);font:600 12px/1 var(--mono);cursor:help;background:var(--surface);flex:none}
.ihelp i{font-style:normal}.ihelp:hover,.ihelp:focus{border-color:var(--accent);color:var(--accent);outline:none}
.ihelp .tip{display:none;position:absolute;right:0;top:30px;z-index:60;width:min(560px,80vw);background:var(--ink);color:#fff;border-radius:10px;padding:12px 14px;font:13px/1.55 var(--sans);text-align:left;box-shadow:0 12px 32px rgba(0,0,0,.28);cursor:auto}
.ihelp:hover .tip,.ihelp:focus .tip,.ihelp:focus-within .tip{display:block}
.ihelp .tip p{margin:0 0 8px}.ihelp .tip p:last-child{margin-bottom:0}
.ihelp .tip .facts{display:flex;flex-wrap:wrap;gap:4px 14px;font-size:12.5px;color:rgba(255,255,255,.78)}.ihelp .tip .facts b{color:#fff;font-weight:600;margin-right:4px}
/* 범례 (지도가 (i) 말풍선 안에 넣는다) */
.legend{display:flex;flex-wrap:wrap;gap:6px 18px;font-size:13px;color:var(--muted);align-items:center}
.legend span{display:inline-flex;align-items:center;gap:6px}
.legend i{display:inline-block;width:14px;height:14px;border-radius:4px;background:var(--line)}
.legend i.ok{background:var(--ok)}.legend i.bad{background:var(--bad)}.legend i.st{background:var(--accent)}.legend i.undev{background:var(--warn)}.legend i.new{background:var(--new,#2F6FDE)}.legend i.accepted{background:var(--accepted)}.legend i.unreached{background:var(--faint)}
.legend .ln{width:26px;height:0;border-top:2px solid var(--accent)}.legend .ln.back{border-top:2px dashed var(--faint)}
.legend .kd{font:600 11px var(--sans);padding:1px 7px;border-radius:4px;background:var(--sunk);color:var(--muted)}
.ihelp .tip .legend{color:rgba(255,255,255,.88);margin:0 0 10px;padding-bottom:10px;border-bottom:1px solid rgba(255,255,255,.18)}
.ihelp .tip .legend i.st{background:#8fd1c6}.ihelp .tip .legend .ln{border-top-color:#8fd1c6}.ihelp .tip .legend .ln.back{border-top-color:rgba(255,255,255,.6)}.ihelp .tip .legend i.unreached{background:rgba(255,255,255,.55)}
.ihelp .tip .legend .kd{background:rgba(255,255,255,.16);color:#fff}
.fchip{font:600 12.5px var(--sans);padding:5px 11px;border-radius:999px;border:1px solid var(--line);background:var(--surface);color:var(--muted);display:inline-flex;align-items:center;gap:6px;line-height:1.2;white-space:nowrap}
button.fchip{cursor:pointer}button.fchip:hover{border-color:var(--accent);color:var(--accent)}
.fchip b{font:600 12.5px var(--mono);color:var(--ink)}.fchip i{width:9px;height:9px;border-radius:50%;background:var(--line);display:inline-block;flex:none}
.fchip small{font-weight:500;color:var(--faint);font-size:12px}
.fchip.ok i{background:var(--ok)}.fchip.bad i{background:var(--bad)}.fchip.warn i,.fchip.undev i{background:var(--warn)}.fchip.new i{background:var(--new,#2F5FB3)}.fchip.accepted i{background:var(--accepted)}.fchip.accent i{background:var(--accent)}.fchip.unreached i,.fchip.none i{background:var(--faint)}
.fchip.ok{color:var(--ok)}.fchip.bad{color:var(--bad)}.fchip.warn{color:var(--warn)}
button.fchip.on{background:var(--accent);border-color:var(--accent);color:#fff}button.fchip.on b,button.fchip.on small{color:#fff}button.fchip.on i{background:#fff}button.fchip.on::before{content:"✓";font-weight:700;margin-right:-2px}button.fchip.on:hover{color:#fff;filter:brightness(1.08)}
.fchip.info{background:var(--sunk);border-color:transparent}
.fchip.lead{font-size:14px;padding:7px 14px}.fchip.lead.ok{background:var(--ok-soft);border-color:transparent}.fchip.lead.bad{background:var(--bad-soft);border-color:transparent}
.fchip.lead.warn{background:var(--warn-soft);border-color:transparent}.fchip.lead.accepted{background:var(--accepted-soft);border-color:transparent;color:var(--accepted)}
/* 쪽 나누기 (표·목록을 10줄씩) */
.pager{display:flex;align-items:center;gap:4px;justify-content:flex-end;margin-top:8px;font:12.5px var(--mono);color:var(--muted)}
.pager .rng{margin-right:8px}.pager .gap{padding:0 4px;color:var(--faint)}
.pager button{font:600 13px var(--sans);min-width:30px;height:28px;padding:0 8px;border:1px solid var(--line);border-radius:6px;background:var(--surface);color:var(--ink);cursor:pointer}
.pager button:hover{border-color:var(--accent);color:var(--accent)}.pager button.on{background:var(--accent-soft);color:var(--accent);border-color:var(--accent)}.pager button:disabled{opacity:.35;cursor:default}
.kpis{display:grid;grid-template-columns:repeat(auto-fit,minmax(170px,1fr));gap:10px}
.kpi{background:var(--surface);border:1px solid var(--line);border-radius:10px;padding:10px 14px}
.kpi .k{font-size:12px;color:var(--muted)}.kpi .v{font:600 17px/1.3 var(--mono);margin-top:2px}.kpi .v small{display:block;font:12px/1.4 var(--sans);color:var(--muted);margin-top:1px}
.kpi.ok .v{color:var(--ok)}.kpi.bad .v{color:var(--bad)}.kpi.warn .v{color:var(--warn)}


/* 섹션 */
section{display:flex;flex-direction:column;gap:12px}
.sh{display:flex;align-items:baseline;gap:12px;flex-wrap:wrap}
.sh h2{font-size:18px;font-weight:600}.sh p{margin:0;color:var(--muted);font-size:13.5px}
.panel{background:var(--surface);border:1px solid var(--line);border-radius:10px}
.empty{padding:14px 18px;color:var(--faint);font-size:14px}


.notice{padding:12px 16px;border-radius:10px;background:var(--warn-soft);color:var(--ink);font-size:14px}
.notice b{color:var(--warn)}

/* 테스트 */
.tests{display:flex;flex-direction:column;gap:10px}
.tt{font-weight:600;font-size:15.5px}.tid{font:12px var(--mono);color:var(--faint)}
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
/* 비교 제외: 같음(초록)·미개발(주황)과 헷갈리지 않게 따로 보라 */
.pill.accepted{background:var(--accepted-soft);color:var(--accepted)}
/* as-is | to-be 딱지와 'to-be 화면 없음' 자리 (시나리오·지도·실행 판정이 같이 쓴다) */
.sd{display:inline-block;font:700 10px/16px var(--mono);padding:0 5px;border-radius:3px;color:#fff;letter-spacing:.03em;vertical-align:1px;white-space:nowrap}
.sd.asis{background:var(--asis)}.sd.tobe{background:var(--tobe)}
.tnone{display:grid;place-content:center;gap:2px;text-align:center;background:repeating-linear-gradient(135deg,var(--sunk) 0 8px,var(--bg) 8px 16px);color:var(--muted);font-size:11px;line-height:1.3;border:1px dashed var(--faint);border-radius:4px}
.tnone small{font-size:10px;color:var(--faint)}
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

@media (max-width:760px){
 .head{grid-template-columns:1fr}
 ol.steps li{grid-template-columns:28px 1fr}ol.steps li>.shotcell{grid-column:2}ol.steps li>.txt{grid-column:2}
 .facts div{border-left:0;padding-left:0}
}
@media (prefers-reduced-motion:reduce){*{transition:none!important}}
"""

CHECK_SVG = ("<svg width='14' height='14' viewBox='0 0 14 14' aria-hidden='true'><path d='M2.5 7.5l3 3 6-7' fill='none' "
             "stroke='currentColor' stroke-width='2' stroke-linecap='round' stroke-linejoin='round'/></svg>")

KIND = {"field": "칸", "dialog": "알림창", "text": "화면 문구", "snapshot": "화면 구조", "url_path": "주소", "url_contains": "주소에 포함",
        "title": "제목", "no_text": "사라진 문구", "text_matches": "문구 형식"}


def _e(s: Any) -> str:
    return h.escape("" if s is None else str(s))


# -- 화면 조각: 통합 화면(hub)이 iframe 없이 한 문서 안에 끼워 넣는 단위 -------------------------------------
# 조각 = {kind, title, html, css, js}. css 는 .pg-<kind> 안으로 가둔 것, js 는 (root, ctx) 를 받는 함수 본문.
#   root: 조각의 컨테이너 요소 (독립 페이지에서는 body). 화면 안 요소는 root.querySelector 로만 찾는다.
#   ctx:  getSub()/setSub(s)/onSub(fn) — 화면 안 이동(지도의 상세, 시나리오 목록의 고른 것)을 주소 해시의 뒷부분으로 (통합 화면이 자기 해시 뒤에 붙여 준다)
#         listen(target, type, fn) — window/document 리스너. 통합 화면이 조각을 치울 때 함께 떼어 낸다
STANDALONE_CTX = ("const __ctx = {getSub: () => (location.hash || '').slice(1), setSub: s => { if (s) location.hash = s; else if (location.hash) location.hash = ''; },"
                  " onSub: fn => addEventListener('hashchange', fn), listen: (t, e, f, o) => t.addEventListener(e, f, o), onDestroy: () => {}};")


def scope_css(css: str, prefix: str) -> str:
    """페이지 CSS를 컨테이너 안으로 가둔다: 선택자마다 prefix 를 앞에 붙이고, html/body/:root 는 컨테이너 자신으로. @media 안은 재귀, @keyframes/@font-face 는 그대로."""
    css = re.sub(r"/\*.*?\*/", "", css, flags=re.S)
    out, i, n = [], 0, len(css)
    while i < n:
        j = css.find("{", i)
        if j < 0:
            out.append(css[i:])
            break
        depth, k = 1, j + 1
        while k < n and depth:
            depth += (css[k] == "{") - (css[k] == "}")
            k += 1
        sel, body = css[i:j], css[j + 1:k - 1]
        s = sel.strip()
        if s.startswith("@"):
            out.append(f"{sel}{{{scope_css(body, prefix) if s.startswith(('@media', '@supports', '@container', '@layer')) else body}}}")
        else:
            out.append(_scope_selectors(sel, prefix) + "{" + body + "}")
        i = k
    return "".join(out)


def _scope_selectors(sel: str, prefix: str) -> str:
    lead = sel[:len(sel) - len(sel.lstrip())]
    scoped = []
    for p in (x.strip() for x in sel.split(",")):
        if not p:
            continue
        if p in ("html", "body", ":root"):
            scoped.append(prefix)
        elif p.startswith(("body.", "body:", "body ", "body>")):
            scoped.append(prefix + p[4:])
        elif p.startswith("html "):
            scoped.append(prefix + " " + p[5:].lstrip())
        else:
            scoped.append(prefix + " " + p)
    return lead + ",".join(dict.fromkeys(scoped))


def help(*, info: str = "", facts: list[str] = (), extra: str = "") -> str:
    """(i) 설명 아이콘: 호버·초점에 말풍선. info 는 문장(이스케이프됨), facts 는 '<b>이름</b> 값' 조각(HTML), extra 는 말풍선 안에 넣을 HTML (지도의 범례 등)."""
    if not (info or facts or extra):
        return ""
    return ("<span class='ihelp' tabindex='0' role='note' aria-label='설명'><i>i</i><span class='tip'>" + (f"<p>{_e(info)}</p>" if info else "") + extra
            + (f"<div class='facts'>{''.join(f'<span>{f}</span>' for f in facts)}</div>" if facts else "") + "</span></span>")


def head(chips: list[str], *, info: str = "", facts: list[str] = (), chips_id: str = "", extra: str = "") -> str:
    """페이지 머리: 상태 칩 한 줄 + (i) 설명. 제목(앱 이름·탭)은 통합 화면의 위 막대 가운데가 보여 주므로 여기 없다."""
    return f"<header class='pgh'><div class='chips'{f' id={chips_id!r}' if chips_id else ''}>{''.join(chips)}</div>{help(info=info, facts=facts, extra=extra)}</header>"


def chip(cls: str, label: str, n: Any = None, *, sub: str = "", title: str = "", lead: bool = False) -> str:
    """머리의 상태 칩 (누르지 않는 것). cls: ok|bad|warn|accepted|new|none|info. n 은 숫자, sub 는 작은 보조 글."""
    dot = "" if cls in ("info", "") else "<i></i>"
    return (f"<span class='fchip {cls}{' lead' if lead else ''}'" + (f" title='{_e(title)}'" if title else "") + f">{dot}{_e(label)}"
            + (f"<b>{_e(n)}</b>" if n is not None else "") + (f"<small>{_e(sub)}</small>" if sub else "") + "</span>")


# 믿을 수 있는가 항목 → 실행으로 풀리는 것. compare = to-be 비교 다시 (pytest --compare, 승인본과 다르면 먼저 자동 승인), mutate = 결함 탐지 측정
FIX = {"기준이 승인됨": ("compare", "to-be 비교 실행 (자동 승인)"), "비교 실행 결과": ("compare", "to-be 비교 실행"),
       "결함 탐지 측정": ("mutate", "결함 탐지 측정 실행"), "결함 탐지 측정도 승인된 기준으로": ("mutate", "결함 탐지 측정 실행")}
COMPARE_FIX = {"승인된 기준으로 비교함", "비교 결과가 현재 승인본으로 만들어짐", "비교 결과의 승인 파일이 현재와 같음", "비교 때의 테스트·도구 코드가 현재와 같음",
               "결함 주입 실행이 아닌 실제 비교 결과", "기록된 테스트를 빠짐없이 실행"}

REPORT_JS = r"""
const $ = s => root.querySelector(s), $$ = s => root.querySelectorAll(s);
const esc = s => String(s ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const box = $('#fixlog'), APP = ctx.app || (box ? box.dataset.app : '');
let timer = null;
ctx.onDestroy(() => clearTimeout(timer));
const NAMES = {compare: 'to-be 비교', mutate: '결함 탐지 측정'};
function lock(running){ $$('.fix').forEach(b => { b.disabled = running; }); }
function show(j){
  if(!box) return;
  box.hidden = false;
  const steps = (j.steps || []).map((s, i) => `<div class="istep ${s.state}"><span class="no">${i + 1}</span><span>${esc(s.label)}</span><span class="st">${{wait:'대기', run:'실행 중…', done:'완료', fail:'실패', skip:'건너뜀'}[s.state] || ''}</span></div>`).join('');
  box.innerHTML = `<div class="fixhead"><b>${esc(NAMES[j.kind] || j.kind)}</b><span>${j.running ? '실행 중 · ' + esc(j.started || '') : (j.ok ? '끝 · 보고서를 새로 그립니다' : '실패')}</span></div>${steps}`
    + (j.error ? `<div class="err">${esc(j.error)}</div>` : '') + `<pre class="log">${esc((j.log || []).join('\n'))}</pre>`;
  const pre = box.querySelector('pre'); pre.scrollTop = pre.scrollHeight;
  lock(!!j.running);
}
async function poll(first){
  let j; try { j = await (await fetch(`/api/app/${encodeURIComponent(APP)}/job`)).json(); } catch(e) { return; }
  if(!NAMES[j.kind]) return;                       // 탐색 같은 다른 작업은 여기서 다루지 않는다
  if(j.running){ show(j); timer = setTimeout(() => poll(false), 2000); return; }
  if(first) return;                                // 예전에 끝난 작업은 다시 보여주지 않는다
  show(j);
  if(j.ok && ctx.refresh) setTimeout(() => ctx.refresh(), 900);
}
$$('.fix').forEach(b => b.addEventListener('click', async () => {
  const kind = b.dataset.fix;
  lock(true);
  try {
    const r = await fetch(`/api/app/${encodeURIComponent(APP)}/${kind}`, {method: 'POST', headers: {'Content-Type': 'application/json'}, body: '{}'});
    const j = await r.json(); if(!r.ok || j.error) throw new Error(j.error || r.statusText);
    show(j); timer = setTimeout(() => poll(false), 2000);
  } catch(e) { if(box){ box.hidden = false; box.innerHTML = `<div class="err">${esc(e.message)}</div>`; } lock(false); }
}));
poll(true);
"""

REPORT_CSS = """
main{max-width:none;gap:22px;padding-block:22px 60px}
.checks li{grid-template-columns:18px 1fr auto auto}
.fix{font:600 12px var(--sans);padding:4px 11px;border-radius:999px;border:1px solid var(--accent);background:var(--surface);color:var(--accent);cursor:pointer;white-space:nowrap}
.fix:hover{background:var(--accent);color:#fff}.fix:disabled{opacity:.5;cursor:default;border-color:var(--line);color:var(--muted);background:var(--surface)}
.fixlog{margin-top:10px;background:var(--surface);border:1px solid var(--line);border-radius:10px;padding:12px 14px;display:flex;flex-direction:column;gap:8px}
.fixhead{display:flex;justify-content:space-between;gap:10px;font-size:13.5px}.fixhead span{color:var(--muted)}
.fixlog .istep{display:grid;grid-template-columns:24px 1fr auto;gap:10px;align-items:center;font-size:13px;padding:6px 10px;border:1px solid var(--line);border-radius:8px}
.fixlog .istep .no{width:22px;height:22px;border-radius:50%;background:var(--sunk);color:var(--muted);font:600 11px/22px var(--mono);text-align:center}
.fixlog .istep.run{border-color:var(--accent);background:var(--accent-soft)}.fixlog .istep.done{border-color:var(--ok)}.fixlog .istep.fail{border-color:var(--bad)}.fixlog .st{font-size:12px;font-weight:600;color:var(--muted)}
.fixlog .err{color:var(--bad);background:var(--bad-soft);border-radius:8px;padding:8px 12px;font-size:13px}
.fixlog pre.log{margin:0;max-height:260px;overflow:auto;font:12px/1.5 var(--mono);background:var(--sunk);border-radius:8px;padding:10px 12px;white-space:pre-wrap;word-break:break-all}
.checks{padding:4px 8px}.checks li{padding:10px 10px;border-radius:6px}.checks li.bad{background:var(--bad-soft)}.checks li.bad .d{color:var(--bad)}
.sh h2 .tid{font-size:14px;margin-left:4px}
details.same-wrap>summary{cursor:pointer;padding:12px 18px;font-weight:600;font-size:14px;list-style:none;color:var(--muted)}
details.same-wrap>summary::-webkit-details-marker{display:none}details.same-wrap>summary::before{content:"▸ ";color:var(--faint)}details.same-wrap[open]>summary::before{content:"▾ "}
details.same-wrap[open]>summary{border-bottom:1px solid var(--line)}
.diff .ttl .tt{font-weight:600;font-size:15px}
.fpair{display:grid;grid-template-columns:1fr 1fr;gap:12px;margin-top:8px}
.fpair figure{margin:0;display:flex;flex-direction:column;gap:5px;min-width:0}.fpair figcaption{font-size:12.5px;color:var(--muted)}
.fpair img{width:100%;max-height:340px;object-fit:cover;object-position:top left;border:1px solid var(--line);border-radius:6px;background:#fff}
.fpair .tnone{aspect-ratio:16/10;font-size:13px}
"""

# as-is | to-be 나란히: 시나리오(catalog)·Screen Map(map) 조각이 자기 CSS 뒤에 붙인다 (조각 안으로 가둬져 각 조각의 .film 보다 앞선다).
# 필름은 as-is 줄과 to-be 줄로 나뉘어 같은 단계가 위아래로 맞고, 단계 목록은 왼쪽 as-is · 오른쪽 to-be, 크게 보기는 두 장을 나란히.
PAIR_CSS = """
.film.pair{display:grid;grid-auto-flow:column;grid-template-rows:auto auto;grid-auto-columns:max-content;gap:6px;justify-content:start;align-items:stretch;scroll-snap-type:none}
.film.pair .side{position:sticky;left:0;z-index:2;display:grid;place-items:center;width:54px;border-radius:6px;font:700 10.5px/1.2 var(--mono);letter-spacing:.02em;color:#fff;white-space:nowrap}
.film.pair .side.asis{background:var(--asis)}.film.pair .side.tobe{background:var(--tobe)}
.film.pair button .sd{position:absolute;right:3px;top:3px;font-size:9px;line-height:14px;padding:0 4px}
.film.pair button.tb{border-color:color-mix(in srgb,var(--tobe) 35%,var(--line))}
.film.pair button.fail{border-color:var(--bad)}.film.pair button:hover{border-color:var(--ink)}
.film button .tnone{height:60px;border:0;border-radius:0;padding-top:16px;font-size:10.5px}.film button .tnone small{display:none}
.steps.pair{display:grid;grid-template-columns:minmax(0,1fr) minmax(0,1fr)}
.steps.pair>.ph{padding:5px 12px;font:700 11px/1.4 var(--mono);letter-spacing:.05em;color:#fff}
.steps.pair>.ph.asis{background:var(--asis)}.steps.pair>.ph.tobe{background:var(--tobe)}
.steps.pair>.visit{min-width:0;overflow-wrap:anywhere}.steps.pair>.ph+.ph+.visit,.steps.pair>.ph+.ph+.visit+.visit{border-top:0}
.steps.pair>.visit.tb{border-left:3px solid var(--tobe);background:color-mix(in srgb,var(--tobe) 5%,transparent)}
.steps.pair>.visit.tb .no{border-color:var(--tobe);color:var(--tobe)}.steps.pair>.visit.tb.fail{background:var(--bad-soft)}
.steps.pair>.visit.tb .miss{margin-top:4px;font-size:12.5px;color:var(--muted)}
.tbnote{margin-top:12px;font-size:12.5px;color:var(--muted);display:flex;gap:8px;align-items:center;flex-wrap:wrap}
.lb{padding:16px}
.lb .lbgallery{width:100%;max-height:calc(100dvh - 32px);display:flex;flex-direction:column;gap:12px;min-width:0;cursor:default;color:#fff}
.lbbar,.lbnav,.lbsides{display:flex;align-items:center;gap:8px}.lbbar{justify-content:space-between}.lbnav{justify-content:center;flex-wrap:wrap}
.lb .lbgallery button{font:600 13px var(--sans);padding:8px 14px;border:1px solid rgba(255,255,255,.35);border-radius:7px;background:#26332f;color:#fff;cursor:pointer;min-height:40px}
.lb .lbgallery button[aria-pressed=true]{background:#fff;color:#18211f;border-color:#fff}.lb .lbgallery button:disabled{opacity:.4;cursor:default}
.lb .lbgallery button:focus-visible{outline-color:#fff}
.lb .lbview{display:flex;align-items:center;justify-content:center;height:calc(100dvh - 172px);min-height:0;flex:1 1 auto;overflow:hidden}
.lb .lbview img{display:block;width:auto;height:auto;max-width:100%;max-height:100%;object-fit:contain;border-radius:8px}
.lb .lbview .tnone{color:#fff;background:transparent;padding:30px}
.lb .lbgallery .cap{margin:0;font-size:14px;overflow-wrap:anywhere}.lbcount{min-width:72px;text-align:center;font:13px var(--mono)}
@media(max-width:600px){.lb{padding:12px}.lb .lbgallery{max-height:calc(100dvh - 24px)}.lb .lbgallery button{padding:8px 10px}.lb .lbview{height:calc(100dvh - 164px)}}
.reg{margin-top:16px}.reg h4{margin:0 0 4px;font-size:12.5px;font-weight:600;letter-spacing:.06em;color:var(--muted)}
.reg p{margin:0 0 8px;font-size:12.5px;color:var(--muted)}
.reg table{width:100%;border-collapse:collapse;font-size:12.5px}
.reg th,.reg td{padding:6px 10px;border-top:1px solid var(--line);text-align:left;vertical-align:top;overflow-wrap:anywhere}
.reg th{background:var(--sunk);border-top:0;font-weight:600}
.reg tr.mine td{background:var(--bad-soft)}.reg tr.mine td:first-child{box-shadow:inset 3px 0 var(--bad)}
.reg .who button{border:0;background:none;padding:0;font:inherit;color:var(--accent);cursor:pointer;text-decoration:underline}
"""

PAIR_JS = r"""
// as-is | to-be 나란히 (html.PAIR_JS — 시나리오·Screen Map 조각이 자기 JS 앞에 붙인다. esc 는 각 조각의 것을 부를 때 쓴다)
const NONE_TOBE = '<span class="tnone"><b>to-be 화면 없음</b><small>to-be가 이 단계까지 가지 못했습니다</small></span>';
// 필름 두 줄: items = [{k: 번호, cap: as-is 동작, tcap: to-be 동작, a: as-is 캡처 주소, b: to-be 캡처 주소('' = 화면 없음), fail, here}]. 버튼의 data-i = items 의 순서
function pairFilm(items){
  return `<div class="film pair"><span class="side asis">AS-IS</span><span class="side tobe">TO-BE</span>` + items.map((x, i) => {
    const cls = `${x.fail ? 'fail' : ''} ${x.here ? 'here' : ''}`;
    return `<button type="button" class="${cls}" data-i="${i}" title="${esc(x.k + '단계 · as-is · ' + x.cap)}">${x.a ? `<img loading="lazy" src="${x.a}" alt="">` : ''}<span class="k">${x.k}</span><span class="sd asis">AS-IS</span><span class="cap">${esc(x.cap)}</span></button>`
      + `<button type="button" class="tb ${cls}" data-i="${i}" title="${esc(x.k + '단계 · to-be · ' + x.tcap)}">${x.b ? `<img loading="lazy" src="${x.b}" alt="">` : NONE_TOBE}<span class="k">${x.k}</span><span class="sd tobe">TO-BE</span><span class="cap">${esc(x.tcap)}</span></button>`;
  }).join('') + `</div>`;
}
// 크게 보기: 선택한 쪽 한 장, 같은 시나리오의 단계별 캡처를 앞뒤로 탐색한다.
function closePairLb(lb){
  lb.classList.remove('on');
  const opener = lb._gallery && lb._gallery.opener;
  if(opener && opener.isConnected) opener.focus({preventScroll:true});
}
function bindPairLb(lb, ctx){
  lb.setAttribute('aria-modal', 'true');
  lb.addEventListener('click', e => {
    if(e.target === lb || e.target.closest('[data-lbclose]')){ closePairLb(lb); return; }
    const s = lb._gallery; if(!s) return;
    const button = e.target.closest('button'); if(!button || button.disabled) return;
    if(button.dataset.side) s.side = button.dataset.side;
    else if(button.hasAttribute('data-prev')) s.index = Math.max(0, s.index - 1);
    else if(button.hasAttribute('data-next')) s.index = Math.min(s.items.length - 1, s.index + 1);
    else return;
    drawPairLb(lb);
    if(button.disabled) lb.querySelector('[data-lbclose]').focus({preventScroll:true});
  });
  ctx.listen(window, 'keydown', e => {
    if(!lb.classList.contains('on')) return;
    if(e.key === 'Escape'){ e.preventDefault(); e.stopImmediatePropagation(); closePairLb(lb); }
    else if(e.key === 'ArrowLeft' || e.key === 'ArrowRight'){
      e.preventDefault(); e.stopImmediatePropagation();
      const s = lb._gallery; s.index = Math.max(0, Math.min(s.items.length - 1, s.index + (e.key === 'ArrowLeft' ? -1 : 1))); drawPairLb(lb);
    } else if(e.key === 'Tab'){
      const buttons = [...lb.querySelectorAll('button:not(:disabled)')], i = buttons.indexOf(document.activeElement);
      e.preventDefault(); buttons[(i + (e.shiftKey ? -1 : 1) + buttons.length) % buttons.length].focus({preventScroll:true});
    }
  }, true);
}
function drawPairLb(lb){
  const s = lb._gallery, x = s.items[s.index], box = lb.firstElementChild;
  box.querySelectorAll('[data-side]').forEach(b => { b.disabled = !x[b.dataset.side]; b.setAttribute('aria-pressed', String(b.dataset.side === s.side)); });
  const src = x[s.side], cap = s.side === 'b' ? (x.tcap || x.cap) : x.cap;
  const view = box.querySelector('.lbview'); view.replaceChildren();
  if(src){ const img = document.createElement('img'); img.src = src; img.alt = `${s.side === 'b' ? 'TO-BE' : 'AS-IS'} · ${x.k ? x.k + '단계 · ' : ''}${cap || ''}`; view.appendChild(img); }
  else view.innerHTML = s.side === 'b' ? NONE_TOBE : '<span class="tnone"><b>as-is 캡처 없음</b></span>';
  box.querySelector('.cap').textContent = `${x.k ? x.k + '단계 · ' : ''}${cap || ''}`;
  box.querySelector('.lbcount').textContent = `${s.index + 1} / ${s.items.length}`;
  box.querySelector('[data-prev]').disabled = s.index === 0;
  box.querySelector('[data-next]').disabled = s.index === s.items.length - 1;
}
function pairGallery(lb, items, index = 0, side = 'a'){
  if(!items.length) return;
  const opener = lb.contains(document.activeElement) && lb._gallery ? lb._gallery.opener : document.activeElement;
  index = Math.max(0, Math.min(items.length - 1, index));
  const x = items[index]; if(!x[side] && x[side === 'a' ? 'b' : 'a']) side = side === 'a' ? 'b' : 'a';
  lb._gallery = {items, index, side, opener};
  lb.firstElementChild.className = 'lbgallery';
  lb.firstElementChild.innerHTML = '<div class="lbbar"><div class="lbsides" role="group" aria-label="캡처 선택"><button type="button" data-side="a">AS-IS</button><button type="button" data-side="b">TO-BE</button></div><button type="button" data-lbclose aria-label="확대 보기 닫기">닫기 ×</button></div><div class="lbview"></div><div class="cap" aria-live="polite"></div><div class="lbnav"><button type="button" data-prev>← 이전 이미지</button><span class="lbcount"></span><button type="button" data-next>다음 이미지 →</button></div>';
  drawPairLb(lb); lb.classList.add('on');
  lb.querySelector(`[data-side="${side}"]:not(:disabled)`)?.focus({preventScroll:true});
}
function pairLb(lb, cap, a, b){
  const tobeOnly = b === undefined && /^to-be\b/i.test(cap || '');
  pairGallery(lb, [{cap, a: tobeOnly ? '' : a, b: tobeOnly ? a : b}], 0, tobeOnly ? 'b' : 'a');
}
// 이 화면의 다른 점 모음 (hub.detail 의 register): 같은 화면을 지나는 시나리오들이 그 실행에서 잡은 차이. 이 시나리오도 잡은 줄은 붉게
function regTable(reg, me){
  const who = d => d.tests.slice(0, 3).map(x => `<button type="button" data-reg="${esc(x.name)}" title="${esc(x.name)}">${esc(x.name === me ? '이 시나리오' : x.title)}</button>`).join(', ') + (d.tests.length > 3 ? ` 외 ${d.tests.length - 3}개` : '');
  return `<div class="reg"><h4>이 화면의 다른 점 모음 · ${esc(reg.route)}</h4><p>이 화면을 지나는 시나리오 ${reg.tests}개가 ${esc(reg.finished)} 실행에서 잡은 차이를 하나씩 묶었습니다. 붉은 줄은 이 시나리오도 잡은 것.</p>`
    + (reg.rows.length ? `<table><tr><th>무엇이</th><th>as-is</th><th>to-be</th><th>잡은 시나리오</th></tr>` + reg.rows.map(d =>
        `<tr class="${d.mine ? 'mine' : ''}"><td>${esc(d.what)}</td><td class="m">${esc(d.asis)}</td><td class="m">${esc(d.tobe)}</td><td class="who">${who(d)}</td></tr>`).join('') + `</table>`
      : '<p>이 화면을 지나는 시나리오에서 다른 점이 없습니다.</p>') + `</div>`;
}
"""


def fragment(kind: str, title: str, body: str, *, css: str = "", js: str = "", data: tuple[str, Any] | None = None) -> dict[str, Any]:
    ds = f"<script type='application/json' id='{data[0]}'>{json.dumps(data[1], ensure_ascii=False).replace('</', '<\\/')}</script>" if data else ""
    return {"kind": kind, "title": title, "html": f"<main>{body}</main>{ds}", "css": scope_css(css, f".pg-{kind}") if css else "", "js": js}


def assemble(frag: dict[str, Any]) -> str:
    """조각 → 혼자 열리는 문서 (테스트, /page/… 직접 접속). body 가 조각의 컨테이너 역할을 한다."""
    script = f"<script>{STANDALONE_CTX}(function(root, ctx){{\n{frag['js']}\n}})(document.body, __ctx);</script>" if frag.get("js") else ""
    return (f"<!doctype html><html lang='ko'><head><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1,viewport-fit=cover'>"
            f"<title>{_e(frag['title'])}</title>{FONTS}<style>{CSS}{frag.get('css', '')}</style></head><body class='pg pg-{frag['kind']}'>{frag['html']}{script}</body></html>")


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
    # pytest 가 매개변수 id 의 한글을 \uXXXX 로 이스케이프한다 (골든 파일 이름도 그대로). 제목에서는 글자로 되돌린다
    param = re.sub(r"\\u([0-9a-fA-F]{4})", lambda m: chr(int(m.group(1), 16)), param.rstrip("]"))
    return t + (f" · {param}" if param else "")


# -- 단계 요약 (시나리오·지도·이상 동작이 같이 쓴다) -----------------------------------------
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


# -- 검증 결과 ---------------------------------------------------------------------------
FIELD = re.compile(r"field \"(.+?)\" == '(.*?)': got '(.*?)'")
DIALOG_NE = re.compile(r"last dialog (None|'.*?') != '(.*?)'")
TEXT_MISSING = re.compile(r"text '(.+?)' visible")
NOT_FOUND = re.compile(r'(\w+) "(.+?)" not found in any frame')
DRIFT = re.compile(r"expectation (changed|added|removed) since as-is recording: (.+)")
DIALOGS = re.compile(r"dialogs: (\[.*?\]) → (\[.*?\])")
REQUESTS = re.compile(r"^requests: (\S+) (\S+) ×(\d+) → ×(\d+)$", re.M)  # observe.request_counts
REQUESTS_ROW = "서버 요청 횟수"
CONTROL =re.compile(r'^(\w+) "(.+?)"(?: \[[^\]]+\])*(?:: (.*))?$')


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
    for m in REQUESTS.finditer(text):
        rows.append((f"{REQUESTS_ROW} · {m.group(1)} {m.group(2)}", f"{m.group(3)}회", f"{m.group(4)}회"))
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


def _rule_tag(found: list[str] | None) -> str:
    """다른 점 행이 차이 규칙(rules.py)에 맞았으면 그 갈래와 사유를 작은 글씨로. 단계에 결함 행이 남아 다른 점으로 남은 경우다."""
    if not found:
        return ""
    from .rules import LABEL
    return f"<div class='tid' title='{_e(found[1])}'>{_e(LABEL.get(found[0], found[0]))} · {_e(found[1])}</div>"


def shot_url(p: Path) -> str:
    """캡처 주소. 화면 HTML에 이미지를 넣지 않고 통합 화면의 /file 로 받게 한다: 시나리오·화면이 수천 개여도 페이지는 가볍고,
    브라우저는 보이는 캡처만 받는다. 파일이 없으면 빈 문자열."""
    return f"/file?p={quote(str(p))}" if p.exists() else ""


def _fail_png(name: str, junit: Path) -> Path | None:
    p = junit.parent / "shots" / f"{name}-fail.png"  # 원장 사본 (runs/<app>/<시각>/shots/)
    if not p.exists():
        p = Path("reports") / f"{name}-fail.png"
        if not p.exists() or not (junit.stat().st_mtime - 900 <= p.stat().st_mtime <= junit.stat().st_mtime + 5):
            return None
    return p


def _shot(name: str, junit: Path, oracle_dir: Path | None = None, messages: list[str] = ()) -> str:
    """실패 순간 화면. 그 실행이 단계별 to-be 캡처를 남겼으면 다른 단계의 as-is 골든 캡처와 to-be 캡처를 나란히 (_fail_pair),
    아니면 (옛 실행, 민감정보로 캡처를 안 남긴 실행) 실패 순간 한 장."""
    fail = _fail_png(name, junit)
    pair = _fail_pair(name, junit, oracle_dir, messages, fail) if oracle_dir else ""
    if pair:
        return pair
    if fail is None:
        return ""
    return (f"<details class='more'><summary>실패 순간 화면</summary>"
            f"<img class='shot' src='{shot_url(fail)}' alt='실패 순간 화면' loading='lazy'></details>")


def _fail_pair(name: str, junit: Path, oracle_dir: Path, messages: list[str], fail: Path | None) -> str:
    """실패 순간 = to-be 가 골든의 마지막 단계까지 못 갔으면 멈춘 단계(마지막 to-be 캡처의 다음), 끝까지 갔으면 처음 다른 단계
    (메시지의 "step N" 이나 실패한 확인 값의 단계, map._fail_steps). to-be 쪽은 그 단계의 to-be 캡처, 그 단계까지 못 갔으면 실패 순간 화면(fail.png).
    원장 JSON(runs/<app>/<시각>.json)은 JUnit 사본 폴더 옆에 있다."""
    from . import fscache
    from .map import _fail_steps
    run, g = junit.parent.parent / f"{junit.parent.name}.json", oracle_dir / f"{name}.json"
    if not run.exists() or not g.exists():
        return ""
    try:
        tobe = (fscache.json_load(run).get("cases", {}).get(name) or {}).get("tobe_shots")
        golden = fscache.json_load(g)
    except (OSError, ValueError):
        return ""
    steps = {o["index"]: o for o in golden.get("steps", [])}
    if tobe is None or not steps:
        return ""
    reached = max(map(int, tobe), default=-1)  # to-be 가 캡처한 마지막 단계. 그보다 뒤에 골든 단계가 있으면 거기서 멈춘 것이 실패 순간이다
    failed = _fail_steps(list(messages), golden.get("assertions", []))
    at = reached + 1 if reached < max(steps) else min(min(failed) if failed else max(steps), max(steps))
    o = steps.get(at) or {}
    a = oracle_dir / o["shot"] if o.get("shot") else None
    if a is None or not a.exists():
        return ""
    b = Path(tobe[str(at)]) if str(at) in tobe else fail
    act = h.unescape(re.sub(r"\s+", " ", re.sub("<[^>]+>", " ", _action(o["kind"], o["text"]))).strip())
    right = (f"<img src='{shot_url(b)}' alt='to-be {at + 1}단계' loading='lazy'>" if b and b.exists()
             else "<span class='tnone'><b>to-be 화면 없음</b><small>캡처가 남지 않았습니다</small></span>")
    return (f"<details class='more'><summary>실패 순간 화면 · {at + 1}단계 {_e(act)} · as-is | to-be</summary><div class='fpair'>"
            f"<figure><figcaption><span class='sd asis'>AS-IS</span> 골든 캡처</figcaption><img src='{shot_url(a)}' alt='as-is {at + 1}단계' loading='lazy'></figure>"
            f"<figure><figcaption><span class='sd tobe'>TO-BE</span> {'그 단계의 to-be 캡처' if str(at) in tobe else '실패 순간 화면'}</figcaption>{right}</figure></div></details>")


def report_fragment(*, oracle_dir: Path, checks: list[tuple[str, bool, str]], trusted: bool, runs: list[dict[str, Any]],
                    muts: list[dict[str, Any]], tests_dir: Path | None = None, equivalent: bool = False, accepted_count: int = 0,
                    coverage: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    """검증 보고서 조각. 순서 = 읽는 순서: 판정 칩 → 믿을 수 있는가 → 다른 점 → 비교 제외 → 결함 탐지 → 업무 범위 → 같은 동작(접힘)."""
    docs = docstrings(tests_dir or Path("e2e") / oracle_dir.name)
    coverage = coverage or []
    cases = [c for r in runs for c in r["cases"]]
    fails = [c for c in cases if c["status"] == "fail"]
    accepted = [(c["name"], item) for c in cases for item in c.get("accepted_differences", [])]
    same = [c for c in cases if c["status"] == "pass" and not c.get("accepted_differences")]
    bad_checks = [c for c in checks if not c[1]]
    gold = [m for m in muts if m["mode"] == "expects+golden"]
    cov_done = sum(1 for x in coverage if x["ok"])

    # -- 머리의 칩: 한 줄로 판정 --
    if not runs:
        verdict = ("warn", "비교 실행 없음", "to-be 비교를 돌린 뒤 여기서 봅니다")
    elif not trusted:
        verdict = ("warn", "판정 보류", f"믿을 수 없는 항목 {len(bad_checks)}개를 먼저 해결")
    elif fails:
        verdict = ("bad", "차이 있음", f"{len(cases)}개 중 {len(fails)}개가 as-is와 다름")
    elif accepted:
        verdict = ("accepted", "비교 제외 포함", f"{len(cases)}개 시나리오")
    else:
        verdict = ("ok", "동일", f"{len(cases)}개 모두 as-is와 같음")
    chips = [chip(verdict[0], verdict[1], sub=verdict[2], lead=True)]
    if runs:
        chips.append(chip("ok", "같음", len(same)))
        if fails:
            chips.append(chip("bad", "다름", len(fails)))
        if accepted:
            chips.append(chip("accepted", "비교 제외", len(accepted)))
    chips.append(chip("ok" if not bad_checks else "bad", "신뢰 확인", f"{len(checks) - len(bad_checks)}/{len(checks)}", title="산출물에서 자동으로 확인한 항목"))
    chips.append(chip("ok" if coverage and cov_done == len(coverage) else ("warn" if coverage else "none"), "업무 범위",
                      f"{cov_done}/{len(coverage)}" if coverage else "미등록", title="oracle.json 의 coverage"))
    for m in gold:
        score = m.get("score") or 0
        chips.append(chip("ok" if score >= 0.8 else "bad", "결함 탐지", f"{score:.0%}", sub=f"{m['killed']}/{m['total']}"))
    if not gold:
        chips.append(chip("none", "결함 탐지", "없음", title="east2west mutate --compare 결과 없음"))

    prov = []
    for r in runs:
        cs = r["cases"]
        setup = cs[0].get("setup", {}) if cs else {}
        build_ids = sorted({bid for c in cs for bid in c.get("build_ids", [])})
        target = r["props"].get("base_url") or Path(r["path"]).stem.removeprefix("junit-")
        prov += [f"<span><b>비교 대상</b> {_e(target)}</span>", f"<span><b>실행 조건</b> {_e(json.dumps(setup, ensure_ascii=False, sort_keys=True))}</span>",
                 f"<span><b>배포 ID</b> {_e(', '.join(build_ids) or '헤더 없음')}</span>"]
    prov += [f"<span><b>기준 폴더</b> {_e(oracle_dir)}</span>", f"<span><b>생성</b> {time.strftime('%Y-%m-%d %H:%M')}</span>"]
    B = [head(chips, info="산출물(승인 상태, JUnit, 결함 주입 결과)에서만 만든 보고서입니다. 서술은 넣지 않습니다. 같은 내용의 markdown 은 east2west report 로 만듭니다.", facts=prov)]

    # -- 믿을 수 있는가: 실패 항목을 먼저 --
    ordered = sorted(checks, key=lambda c: c[1])

    def fix_btn(name: str, ok: bool) -> str:
        """빨간 항목 옆의 '바로 해결' 버튼: 실행으로 풀리는 것만 (비교 다시 실행, 결함 탐지 측정. 둘 다 승인본과 다르면 먼저 자동 승인)."""
        if ok:
            return "<span></span>"
        kind, label = FIX.get(name, ("compare", "to-be 비교 다시 실행") if name in COMPARE_FIX else (None, None))
        if not kind:
            return "<span class='d'>사람이 판단</span>"
        return f"<button type='button' class='fix' data-fix='{kind}'>{label}</button>"
    B.append("<section><div class='sh'><h2>믿을 수 있는가</h2><p>산출물에서 자동으로 확인한 항목" + (f" · <b style='color:var(--bad)'>{len(bad_checks)}개 확인 필요</b>" if bad_checks else " · 모두 통과") + "</p></div><div class='panel'><ul class='checks'>"
             + "".join(f"<li class='{'' if ok else 'bad'}'><span class='dot {'ok' if ok else 'bad'}'></span><span>{_e(name)}</span><span class='d'>{_e(detail)}</span>{fix_btn(name, ok)}</li>"
                       for name, ok, detail in ordered) + f"</ul></div><div class='fixlog' id='fixlog' data-app='{_e(oracle_dir.name)}' hidden></div></section>")

    # -- 다른 점 --
    if fails:
        cards = []
        for r in runs:
            for c in r["cases"]:
                if c["status"] != "fail":
                    continue
                rows, notes = rows_for(c["messages"])
                classed = {tuple(x[:3]): x[3:] for x in c.get("row_classes", [])}  # 차이 규칙에 맞은 행 → (갈래, 사유) (rules.py)
                table = ("<div class='tbl'><table><tr><th>무엇이</th><th>as-is 기준</th><th>to-be</th></tr>"
                         + "".join(f"<tr><td>{_e(i)}{_rule_tag(classed.get((i, a, b)))}</td><td class='m was'>{_e(a)}</td><td class='m now'>{_e(b)}</td></tr>" for i, a, b in rows[:10])
                         + "</table></div>") if rows else ""
                more = f"<div class='tid'>외 {len(rows) - 10}건은 원본 로그에</div>" if len(rows) > 10 else ""
                cards.append(f"<div class='panel diff'><div class='ttl'><span class='tt'>{_e(_title(c['name'], docs))}</span><span class='tid'>{_e(c['name'])}</span></div>"
                             + "".join(f"<div><span class='pill warn'>기대값 변경</span> {_e(n)}</div>" for n in notes) + table + more
                             + _shot(c["name"], Path(r["path"]), oracle_dir, c["messages"])
                             + f"<details class='more'><summary>원본 로그</summary><pre>{_e(chr(10).join(c['messages']))}</pre></details></div>")
        B.append(f"<section><div class='sh'><h2>다른 점 <span class='tid'>{len(fails)}</span></h2><p>결함이면 수정 요청, 의도한 변경이면 기준 변경을 결정</p></div>" + "".join(cards) + "</section>")
    if accepted:
        from .rules import describe
        B.append("<section><div class='sh'><h2>비교 제외</h2><p>oracle.json 의 allowed_differences · difference_rules, 고객 결정(as-is 이상 동작)</p></div><div class='panel same'>"
                 + "".join(f"<div>{_e(_title(name, docs))} · {item['step']}단계 · {_e(describe(item))}</div>" for name, item in accepted) + "</div></section>")

    # -- 결함 탐지 --
    for m in gold:
        surv = [x for x in m["mutants"] if not x["killed"]]
        core_total = sum(v["total"] for op, v in m.get("by_op", {}).items() if op != "label")
        core_killed = sum(v["killed"] for op, v in m.get("by_op", {}).items() if op != "label")
        score = m.get("score") or 0
        B.append(f"<section><div class='sh'><h2>테스트가 결함을 잡는 능력</h2><p>일부러 넣은 결함 {m['total']}개 중 몇 개를 잡았는지 · {_e((m.get('generated_at') or '')[:16])}</p></div>"
                 f"<div class='panel meter'><span class='n'>{score:.0%}</span> <span class='tid'>{m['killed']} / {m['total']}</span>"
                 f"<div class='bar'><i style='width:{score * 100:.0f}%'></i></div>"
                 f"<div class='tid'>라벨 변경 제외: {core_killed}/{core_total} 탐지 · 생성한 결함에 대한 비율</div>"
                 + (f"<div class='tid' style='margin-top:10px'><b>못 잡은 결함 {len(surv)}개</b> (테스트 빈틈인지, 화면에 안 나타나는 결함인지 사람이 판정)</div>" if surv else "")
                 + "".join(f"<div class='tid'>· {_e(x['path'])} · {_e(x['desc'][:90])}</div>" for x in surv[:5])
                 + (f"<div class='tid'>외 {len(surv) - 5}개</div>" if len(surv) > 5 else "") + "</div></section>")

    # -- 업무 범위 --
    B.append("<section><div class='sh'><h2>업무 검증 범위</h2><p>oracle.json 에 등록한 업무 경우와 연결된 테스트</p></div><div class='panel'>"
             + (("<ul class='checks'>" + "".join(f"<li class='{'' if x['ok'] else 'bad'}'><span class='dot {'ok' if x['ok'] else 'bad'}'></span><span>{_e(x['case'])}</span><span class='d'>{_e(', '.join(x['tests']))}</span></li>" for x in coverage) + "</ul>")
                if coverage else "<div class='empty'>등록된 업무 경우가 없습니다. golden/&lt;app&gt;/oracle.json 의 coverage 에 업무 경우와 테스트를 적으면 여기서 통과 여부를 봅니다.</div>")
             + "</div></section>")

    # -- 같은 동작: 접어 둔다 --
    if same:
        B.append(f"<section><details class='panel same-wrap'><summary>같은 동작 {len(same)}개 · 펼쳐 보기</summary><div class='same'>"
                 + "".join(f"<span>{_e(_title(c['name'], docs))}</span>" for c in same) + "</div></details></section>")
    return fragment("report", f"{oracle_dir.name} 검증 보고서", "".join(B), css=REPORT_CSS, js=REPORT_JS)


def render_report(**kw: Any) -> str:
    """검증 보고서를 혼자 열리는 문서로 (report_fragment 와 같은 인자)."""
    return assemble(report_fragment(**kw))
