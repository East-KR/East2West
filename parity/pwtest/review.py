"""승인 검토 화면: 개발자가 골든 시나리오를 하나씩 보고 "as-is 동작이 맞다"고 확인한 뒤 터미널에서 승인한다.

parity review golden/<app>            (기록 `--record`가 끝날 때, `parity approve`가 열 때도 자동으로 만든다)

화면: 왼쪽 시나리오 목록(확인 여부, 지난 승인 이후 바뀐 것 표시) · 가운데 선택한 시나리오(단계별 큰 캡처, 동작·알림창·확인 값) · 오른쪽 규칙(가림·이름 변경·동등 결함).
"확인함" 표시는 보는 사람 브라우저에만 저장된다(localStorage). 승인은 여전히 터미널에서만: 모든 시나리오가 확인되면 승인 명령이 나타난다.
읽는 것: golden/<app>/ 뿐. 새로 판단하는 것은 없다.
"""
from __future__ import annotations

import base64
import json
from collections import defaultdict
from pathlib import Path
from typing import Any

from . import html, oracle
from .map import _plain

CSS = """
main{max-width:none;padding-block:22px 40px;gap:18px}
.head{gap:6px 24px}.head h1{font-size:26px}
.stage{display:grid;grid-template-columns:300px minmax(0,1fr) 340px;gap:14px;height:calc(100vh - 210px);min-height:600px}
.pane{background:var(--surface);border:1px solid var(--line);border-radius:12px;min-height:0;display:flex;flex-direction:column;overflow:hidden}
.pane>h2{display:flex;align-items:center;justify-content:space-between;gap:8px;font-size:12.5px;font-weight:600;letter-spacing:.06em;color:var(--muted);padding:12px 14px 10px;border-bottom:1px solid var(--line);margin:0}
.pane>h2 span{font-weight:400;letter-spacing:0}
.scroll{overflow:auto;min-height:0}
.list{padding:6px}
.item{display:grid;grid-template-columns:22px 1fr;gap:10px;align-items:start;padding:9px 8px;border-radius:8px;cursor:pointer;border:0;background:none;text-align:left;font:inherit;color:inherit;width:100%}
.item:hover{background:var(--sunk)}.item.sel{background:var(--accent-soft)}
.item .box{width:18px;height:18px;border:1.5px solid var(--line);border-radius:5px;margin-top:2px;display:grid;place-items:center;color:#fff;font-size:12px}
.item.done .box{background:var(--ok);border-color:var(--ok)}.item.done .box::after{content:"✓"}
.item .t{font-size:14px;font-weight:600;line-height:1.3}.item .t small{display:block;font:11.5px var(--mono);color:var(--faint);font-weight:400;margin-top:2px}
.item .flag{display:inline-block;margin-left:6px;font-size:11px;font-weight:600;border-radius:4px;padding:0 6px;vertical-align:1px}
.item .flag.chg{background:var(--warn-soft);color:var(--warn)}.item .flag.new{background:var(--accent-soft);color:var(--accent)}
.item.dim{display:none}
.filters{display:flex;gap:6px;padding:8px 10px;border-bottom:1px solid var(--line)}
.filters button{font:600 12.5px var(--sans);border:1px solid var(--line);background:var(--surface);color:var(--muted);border-radius:99px;padding:3px 10px;cursor:pointer}
.filters button.on{background:var(--ink);color:var(--bg);border-color:var(--ink)}
/* 가운데 */
.center{padding:0 18px 18px}
.center .dh{position:sticky;top:0;background:var(--surface);padding:14px 0 10px;border-bottom:1px solid var(--line);z-index:1;display:grid;grid-template-columns:1fr auto;gap:8px 14px;align-items:center}
.center h3{font-size:19px;font-weight:700;line-height:1.3}
.center .sub{grid-column:1/-1;font:12.5px var(--mono);color:var(--muted);display:flex;gap:10px;flex-wrap:wrap}
.okbtn{font:600 14px var(--sans);border:1.5px solid var(--ok);color:var(--ok);background:var(--surface);border-radius:8px;padding:7px 14px;cursor:pointer;white-space:nowrap}
.okbtn.on{background:var(--ok);color:#fff}
.stepv{display:grid;grid-template-columns:minmax(0,1fr) 300px;gap:16px;padding:16px 0;border-bottom:1px dashed var(--line)}
.stepv:last-child{border-bottom:0}
.shot{border:1px solid var(--line);border-radius:8px;overflow:hidden;background:#fff;cursor:zoom-in}.shot img{width:100%;display:block}
.noshot{aspect-ratio:16/10;border:1px dashed var(--line);border-radius:8px;display:grid;place-items:center;color:var(--faint);font-size:13px}
.act{font-size:16px;display:flex;gap:10px;align-items:baseline}.act .no{flex:none;width:26px;height:26px;border-radius:50%;border:1.5px solid var(--accent);color:var(--accent);font:600 13px/24px var(--mono);text-align:center}
.act .verb{font-size:12px;font-weight:600;color:var(--muted);margin-right:6px}
.seen{margin-top:8px;font-size:13px;display:flex;flex-wrap:wrap;gap:4px 6px;align-items:baseline}.seen .lab{color:var(--faint);font-size:12px}
.seen .it{font-family:var(--mono);background:var(--sunk);border-radius:4px;padding:0 6px}
.dlg{margin-top:8px;display:inline-flex;gap:8px;align-items:center;font-size:13px;border:1px solid var(--line);border-radius:6px;padding:4px 10px;background:var(--sunk)}
.dlg .ans{font-size:12px;color:var(--accent);font-weight:600}
.checks{margin-top:10px;display:flex;flex-direction:column;gap:6px}
.check{display:flex;gap:8px;align-items:baseline;font-size:14px}.check .k{color:var(--muted)}.check .v{font-family:var(--mono);font-weight:600}
.check.chg{background:var(--warn-soft);border-radius:6px;padding:4px 8px}.check.chg s{color:var(--faint);margin-left:6px}
.check.add{background:var(--accent-soft);border-radius:6px;padding:4px 8px}
.empty{padding:60px 20px;text-align:center;color:var(--faint)}
/* 오른쪽 규칙 */
.rules{padding:12px 14px;display:flex;flex-direction:column;gap:14px}
.rules h4{margin:0 0 6px;font-size:12.5px;font-weight:600;letter-spacing:.06em;color:var(--muted)}
.rules table{width:100%;border-collapse:collapse;font-size:13px}.rules td{padding:5px 6px;border-bottom:1px solid var(--line);vertical-align:top}.rules tr:last-child td{border-bottom:0}
.rules .m{font-family:var(--mono);font-size:12px}
.rules .none{color:var(--faint);font-size:13px}
.chip{display:inline-block;font:12px var(--mono);background:var(--sunk);border-radius:4px;padding:1px 6px;margin:1px 3px 1px 0}
.warnchip{color:var(--warn);font-weight:600;font-size:12.5px}
.notice{padding:10px 14px;border-radius:10px;background:var(--warn-soft);font-size:14px}.notice b{color:var(--warn)}
/* 하단 승인 막대 */
.bar-approve{position:fixed;left:0;right:0;bottom:0;background:var(--surface);border-top:1px solid var(--line);padding:12px clamp(16px,4vw,32px) calc(12px + env(safe-area-inset-bottom,0px));box-shadow:0 -6px 24px rgba(15,20,19,.06);z-index:5}
.bar-approve .in{display:flex;flex-wrap:wrap;gap:10px 16px;align-items:center}
.bar-approve p{margin:0;font-size:14px;flex:1 1 280px}.bar-approve b{color:var(--accent)}
.prog{height:8px;border-radius:99px;background:var(--sunk);flex:1 1 160px;overflow:hidden;max-width:260px}.prog i{display:block;height:100%;background:var(--ok)}
.cmd{display:flex;align-items:center;border:1px solid var(--line);border-radius:8px;overflow:hidden;max-width:100%}
.cmd code{padding:8px 12px;font-size:13px;background:var(--sunk);white-space:nowrap;overflow-x:auto;max-width:60vw}
.cmd button{border:0;background:var(--accent);color:#fff;font:600 13px var(--sans);padding:8px 14px;cursor:pointer}
.lb{position:fixed;inset:0;background:rgba(15,20,19,.82);display:none;place-items:center;z-index:50;padding:24px;cursor:zoom-out}
.lb.on{display:grid}.lb img{max-width:min(96vw,1400px);max-height:84vh;border-radius:8px;background:#fff}
.lb .cap{color:#fff;margin-top:12px;font-size:14px;text-align:center}
@media (max-width:1100px){.stage{grid-template-columns:260px minmax(0,1fr);height:auto}.pane.right{grid-column:1/-1}.stepv{grid-template-columns:1fr}}
@media (max-width:720px){.stage{grid-template-columns:1fr}}
"""

JS = r"""<script>
const D = JSON.parse(document.getElementById('d').textContent);
const esc = s => String(s ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const KEY = 'parity-review:' + D.app + ':' + D.fingerprint;  // 기록이 바뀌면 확인 표시도 새로 한다
let done = new Set();
try { done = new Set(JSON.parse(localStorage.getItem(KEY) || '[]')); } catch(e) {}
function save(){ try { localStorage.setItem(KEY, JSON.stringify([...done])); } catch(e) {} }
const center = document.getElementById('center'), lb = document.getElementById('lb');
function openLb(src, cap){ if(!src) return; lb.querySelector('img').src = src; lb.querySelector('.cap').textContent = cap || ''; lb.classList.add('on'); }
lb.addEventListener('click', () => lb.classList.remove('on'));
addEventListener('keydown', e => { if(e.key === 'Escape') lb.classList.remove('on'); });
let current = null;
function refresh(){
  document.querySelectorAll('.item').forEach(el => { el.classList.toggle('done', done.has(el.dataset.test)); el.classList.toggle('sel', el.dataset.test === current); });
  const n = D.order.length, k = D.order.filter(t => done.has(t)).length;
  document.getElementById('cnt').textContent = `${k}/${n} 확인`;
  document.getElementById('bar').innerHTML = k === n && n
    ? `<p><b>모든 시나리오를 확인했습니다.</b> 터미널에서 승인하세요. 승인 중이면 <code>${esc(D.dirname)}</code>을 입력합니다.</p><div class="cmd"><code>${esc(D.cmd)}</code><button type="button" id="copy">복사</button></div>`
    : `<p>확인하지 않은 시나리오 <b>${n - k}개</b>. 각 시나리오를 보고 as-is 동작이 맞으면 "확인함"을 누르세요. 틀린 것이 있으면 승인하지 말고 담당자에게 알려 주세요.</p><div class="prog"><i style="width:${n ? k / n * 100 : 0}%"></i></div>`;
  const c = document.getElementById('copy'); if(c) c.onclick = () => { navigator.clipboard.writeText(D.cmd).then(() => c.textContent = '복사됨').catch(() => { const r = document.createRange(); r.selectNodeContents(c.previousElementSibling); getSelection().removeAllRanges(); getSelection().addRange(r); c.textContent = '선택됨 · ⌘C'; }); };
  filter();
}
function show(name){
  const t = D.tests[name]; if(!t) return;
  current = name;
  let h = `<div class="dh"><h3>${esc(t.title)}${t.flag ? ` <span class="flag ${t.flag}">${t.flag === 'chg' ? '기대값 바뀜' : '새 시나리오'}</span>` : ''}</h3>`
    + `<button type="button" class="okbtn ${done.has(name) ? 'on' : ''}" id="ok">${done.has(name) ? '✓ 확인함' : 'as-is 동작이 맞음 · 확인함'}</button>`
    + `<div class="sub"><span>${esc(name)}</span><span>${t.steps.length}단계</span><span>${esc(t.base_url)}에서 ${esc(t.recorded_at || '')} 기록</span></div></div>`;
  t.steps.forEach(s => {
    h += `<div class="stepv"><div>` + (s.shot ? `<div class="shot" data-lb="${s.shot}" data-cap="${s.index + 1}단계 · ${esc(s.action_text)}"><img src="${D.shots[s.shot]}" alt="${s.index + 1}단계 화면" loading="lazy"></div>` : `<div class="noshot">캡처 없음</div>`) + `</div><div>`;
    h += `<div class="act"><span class="no">${s.index + 1}</span><span>${s.action}</span></div>`;
    if(s.seen.length) h += `<div class="seen"><span class="lab">나타남</span>${s.seen.map(x => `<span class="it">${esc(x)}</span>`).join('')}</div>`;
    s.dialogs.forEach(d => { h += `<div class="dlg"><span class="verb">${d.type === 'confirm' ? '확인창' : '알림창'}</span><b>${esc(d.message)}</b><span class="ans">→ ${d.action === 'accept' ? '확인' : '취소'}</span></div>`; });
    if(s.checks.length) h += `<div class="checks">` + s.checks.map(c => `<div class="check ${c.cls}"><span>✓</span><span class="k">${esc(c.k)}</span><span class="v">${esc(c.v)}</span>${c.old ? `<s>${esc(c.old)}</s>` : ''}</div>`).join('') + `</div>`;
    h += `</div></div>`;
  });
  center.innerHTML = h; center.scrollTop = 0;
  center.querySelector('#ok').addEventListener('click', () => { done.has(name) ? done.delete(name) : done.add(name); save(); show(name); refresh(); });
  center.querySelectorAll('[data-lb]').forEach(el => el.addEventListener('click', () => openLb(D.shots[el.dataset.lb], el.dataset.cap)));
  refresh();
  try { history.replaceState(null, '', '#' + encodeURIComponent(name)); } catch(e) {}
}
let mode = 'all';
function filter(){
  document.querySelectorAll('.item').forEach(el => {
    const t = D.tests[el.dataset.test];
    const hit = mode === 'all' || (mode === 'todo' && !done.has(el.dataset.test)) || (mode === 'chg' && t.flag);
    el.classList.toggle('dim', !hit);
  });
}
document.querySelectorAll('.filters button').forEach(b => b.addEventListener('click', () => { mode = b.dataset.mode; document.querySelectorAll('.filters button').forEach(x => x.classList.toggle('on', x === b)); filter(); }));
document.querySelectorAll('.item').forEach(el => el.addEventListener('click', () => show(el.dataset.test)));
const first = decodeURIComponent((location.hash || '').slice(1));
show(D.tests[first] ? first : (D.order.find(t => D.tests[t].flag) || D.order.find(t => !done.has(t)) || D.order[0]));
</script>"""


def _img(p: Path) -> str:
    return "data:image/jpeg;base64," + base64.b64encode(p.read_bytes()).decode() if p.exists() else ""


def _check(a: dict[str, Any], cls: str = "", old: dict[str, Any] | None = None) -> dict[str, str]:
    kind = html.KIND.get(a["kind"], a["kind"])
    k, v = (a["target"], a["value"] if a["value"] != "" else "(빈 값)") if a["kind"] == "field" else (kind, a["target"])
    o = "" if old is None else (old["value"] if old["kind"] == "field" else old["target"])
    return {"k": k, "v": v, "cls": cls, "old": o}


def build(d: Path, tests_dir: Path | None = None) -> dict[str, Any]:
    import hashlib
    from collections import Counter

    from ..observe import flatten, mask

    docs = html.docstrings(tests_dir or Path("e2e") / d.name)
    st = oracle.status(d)
    prev = oracle.approved_assertions(d)
    cfg = oracle.load_config(d)
    rules = cfg.get("ignore", [])
    shots: dict[str, str] = {}
    tests: dict[str, dict[str, Any]] = {}
    order: list[str] = []
    for t in oracle.tests(d):
        data = json.loads((d / f"{t['name']}.json").read_text(encoding="utf-8"))
        before = None if prev is None else prev.get(t["name"])
        by_step: dict[int, list[dict[str, str]]] = defaultdict(list)
        for i, a in enumerate(t["assertions"]):
            old = before[i] if before is not None and i < len(before) else None
            if before is not None and old is None:
                by_step[a["step"] - 1].append(_check(a, "add"))
            elif old is not None and old != a:
                by_step[a["step"] - 1].append(_check(a, "chg", old))
            else:
                by_step[a["step"] - 1].append(_check(a))
        flag = ""
        if prev is not None and before is None:
            flag = "new"
        elif before is not None and before != t["assertions"]:
            flag = "chg"
        steps, prev_lines = [], Counter()
        for o in data.get("steps", []):
            cur = Counter(mask(flatten(o.get("snapshot", ""), keep_urls=False), rules))
            new = [l for l in (cur - prev_lines).elements() if not l.startswith(("option ", "link "))]
            prev_lines = cur
            seen = list(dict.fromkeys(html._clean(html._seen(l)) for l in new))[:8]
            sid = ""
            if o.get("shot"):
                sid = f"{t['name']}#{o['index']}"
                shots[sid] = _img(d / o["shot"])
            act = html._action(o["kind"], o["text"])
            steps.append({"index": o["index"], "action": act, "action_text": _plain(act), "seen": seen,
                          "dialogs": o.get("dialogs", []), "checks": by_step.get(o["index"], []), "shot": sid})
        tests[t["name"]] = {"title": html._title(t["name"], docs), "steps": steps, "flag": flag, "base_url": t["base_url"], "recorded_at": t["recorded_at"]}
        order.append(t["name"])
    removed = [] if prev is None else sorted(set(prev) - set(order))
    fp = oracle.oracle_files(d)
    return {"app": d.name, "dirname": d.name, "cmd": f"uv run parity approve {d} --by <이름>", "status": st, "tests": tests, "order": order,
            "shots": shots, "removed": removed, "masks": oracle.mask_hits(d), "maps": oracle.name_maps(d),
            "eqs": cfg.get("equivalent_mutants", []),
            # 기록이 바뀌면 브라우저에 남긴 "확인함" 표시를 새로 시작하려고 파일 해시들의 해시를 쓴다
            "fingerprint": hashlib.sha256(json.dumps(sorted(fp.items())).encode()).hexdigest()[:12]}


def render(g: dict[str, Any]) -> str:
    st = g["status"]
    if st["ok"]:
        stamp = ("ok", "승인됨", f"{html._e(st['approved_by'])} · {html._e((st['approved_at'] or '')[:16])}")
    elif st.get("approved_by"):
        stamp = ("warn", "재승인 필요", "승인 후 기록이 바뀜")
    else:
        stamp = ("warn", "승인 필요", "처음 승인")
    n_chg = sum(1 for t in g["tests"].values() if t["flag"] == "chg")
    n_new = sum(1 for t in g["tests"].values() if t["flag"] == "new")
    items = "".join(f"<button type='button' class='item' data-test='{html._e(n)}'><span class='box'></span><span class='t'>{html._e(t['title'])}"
                    + (f"<span class='flag {t['flag']}'>{'기대값 바뀜' if t['flag'] == 'chg' else '새 시나리오'}</span>" if t["flag"] else "")
                    + f"<small>{html._e(n)} · {len(t['steps'])}단계</small></span></button>" for n, t in g["tests"].items())
    notice = ""
    if n_chg or n_new or g["removed"]:
        notice = (f"<div class='notice'><b>지난 승인 이후</b> 기대값 바뀐 시나리오 {n_chg}개 · 새 시나리오 {n_new}개 · 없어진 시나리오 {len(g['removed'])}개"
                  + (" (" + ", ".join(html._e(r) for r in g["removed"]) + ")" if g["removed"] else "") + ". 바뀐 것을 먼저 보세요.</div>")
    masks = ("<table>" + "".join(f"<tr><td class='m'>{html._e(m['rule'])}</td><td>" + ("".join(f"<span class='chip'>{html._e(k)}</span>" for k, _ in m["samples"]) or "<span class='warnchip'>아무것도 가리지 않음</span>") + f"<br><span class='tid'>{m['total']}곳</span></td></tr>" for m in g["masks"]) + "</table>") if g["masks"] else "<div class='none'>없음</div>"
    maps = ("<table>" + "".join(f"<tr><td>{html._e(a)}</td><td>→ <b>{html._e(b)}</b></td><td class='m'>{html._e(t)}</td></tr>" for t, m in g["maps"].items() for a, b in m.items()) + "</table>") if g["maps"] else "<div class='none'>없음</div>"
    eqs = ("<table>" + "".join(f"<tr><td class='m'>{html._e(e.get('path'))}<br>{html._e(e.get('context'))}</td><td>{html._e(e.get('reason', ''))}</td></tr>" for e in g["eqs"]) + "</table>") if g["eqs"] else "<div class='none'>없음</div>"
    body = (f"<header class='head'><div class='eyebrow'>승인 검토</div><div class='stamp {stamp[0]}'>{stamp[1]}<small>{stamp[2]}</small></div>"
            f"<h1>{html._e(g['app'])}</h1><p class='lede'>as-is에서 기록한 동작이 to-be의 정답이 됩니다. 시나리오마다 단계 화면과 확인 값이 실제 업무와 맞는지 보고 확인하세요.</p>"
            f"<div class='prov'><span><b>기준 폴더</b> golden/{html._e(g['app'])}</span><span><b>시나리오</b> {len(g['tests'])}개</span></div></header>{notice}"
            f"<div class='stage'><div class='pane left'><h2><span>시나리오</span><span id='cnt'></span></h2>"
            f"<div class='filters'><button type='button' class='on' data-mode='all'>전체</button><button type='button' data-mode='todo'>미확인</button><button type='button' data-mode='chg'>바뀐 것</button></div>"
            f"<div class='scroll list'>{items}</div></div>"
            f"<div class='pane center scroll' id='center'><div class='empty'>왼쪽에서 시나리오를 고르세요</div></div>"
            f"<div class='pane right'><h2>규칙</h2><div class='scroll rules'><div><h4>가리는 값 · 매번 바뀌는 값만</h4>{masks}</div>"
            f"<div><h4>이름 변경 · to-be에서 바뀌어도 되는 라벨</h4>{maps}</div><div><h4>동등 결함 · 탐지율에서 빼는 결함</h4>{eqs}</div></div></div></div>"
            f"<div class='bar-approve'><div class='in' id='bar'></div></div>"
            f"<div class='lb' id='lb' role='dialog' aria-label='화면 크게 보기'><div><img src='' alt=''><div class='cap'></div></div></div>")
    data = json.dumps(g, ensure_ascii=False).replace("</", "<\\/")
    page = html._page(f"{g['app']} 기준 승인", body, script=f"<script type='application/json' id='d'>{data}</script>{JS}")
    return page.replace("</style>", CSS + "</style>", 1)


def write_review(d: Path, *, tests_dir: Path | None = None, out: Path | None = None) -> Path:
    out = out or Path("reports") / f"review-{d.name}.html"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(render(build(d, tests_dir)), encoding="utf-8")
    return out
