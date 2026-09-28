"""골든 관리 화면 (eastshift ui 의 시나리오 탭): 한 앱의 골든 시나리오를 한눈에. 승인 상태, 마지막 to-be 결과, 실행 이력, 시나리오 상세.

hub.py 가 /page/<app>/catalog 요청에 render(build(golden/<app>)) 로 만든다.

읽는 것: golden/<app>/ (시나리오, 기대값, 캡처, 승인), runs/<app>/ (실행 원장). 새로 판단하는 것은 없다.
화면: 위에 승인·마지막 실행 요약, 그 아래 시나리오 표 (제목, 단계 수, 확인 값, 마지막 결과, 이력 점). 행을 누르면 시나리오 팝업
(필름스트립 + 단계 + 마지막 실행에서 다른 점). 다른 화면으로는 통합 화면의 탭으로 간다.
"""
from __future__ import annotations

import json
from collections import defaultdict
from pathlib import Path
from typing import Any

from . import fscache, html, ledger, oracle
from .map import _plain

KIND_LABEL = {"same": "같음", "accepted_diff": "승인된 차이", "drift": "기대값 바뀜", "golden_diff": "as-is와 다름", "assert": "확인 값 실패", "error": "실행 못 함"}

CSS = """
main{max-width:none;gap:12px;padding-block:14px 14px;height:100%;display:flex;flex-direction:column}
.tbl{flex:1;min-height:0;overflow:hidden}.pager{margin-top:0;flex:none}.tools .sp{flex:1}
.head{gap:6px 24px}.head h1{font-size:26px}
.tools{display:flex;gap:8px;flex-wrap:wrap;align-items:center}
.tools input,.tools select{font:inherit;font-size:14px;padding:7px 11px;border:1px solid var(--line);border-radius:8px;background:var(--surface);color:var(--ink)}
.tools input{min-width:260px}
.tbl table{width:100%;border-collapse:collapse;font-size:14px;background:var(--surface);border:1px solid var(--line);border-radius:12px;overflow:hidden}
.tbl th{font-size:12px;letter-spacing:.04em;color:var(--muted);text-align:left;padding:10px 12px;background:var(--sunk);border-bottom:1px solid var(--line)}
.tbl td{padding:8px 12px;border-bottom:1px solid var(--line);vertical-align:middle}
.tbl tr:last-child td{border-bottom:0}.tbl tbody tr{cursor:pointer}.tbl tbody tr:hover td{background:var(--accent-soft)}
.tbl tr.dim{display:none}
.tbl .t b{display:block;font-size:14.5px}.tbl .t small{font:11.5px var(--mono);color:var(--faint)}
.tbl .r{white-space:nowrap}.tbl .r small{display:block;font:11.5px var(--mono);color:var(--bad);white-space:normal;max-width:360px;margin-top:3px}
.tbl tr[data-state='fail'] td:first-child{box-shadow:inset 3px 0 var(--bad)}
.tbl .n{font-family:var(--mono);font-variant-numeric:tabular-nums;color:var(--muted);white-space:nowrap}
.hist{display:inline-flex;gap:3px;align-items:center}.hist i{width:10px;height:10px;border-radius:3px;background:var(--line);display:inline-block}
.hist i.p{background:var(--ok)}.hist i.f{background:var(--bad)}.hist i.cur{outline:2px solid var(--ink);outline-offset:1px}
.pill.none{background:var(--sunk);color:var(--muted)}
.modal{position:fixed;inset:0;background:rgba(15,20,19,.55);display:none;place-items:center;z-index:40;padding:24px}
.modal.on{display:grid}
.modal .box{background:var(--surface);color:var(--ink);border-radius:14px;width:min(980px,96vw);max-height:88vh;display:flex;flex-direction:column;overflow:hidden;box-shadow:0 30px 80px rgba(0,0,0,.4)}
.modal .mh{display:grid;grid-template-columns:1fr auto auto;gap:12px;align-items:center;padding:14px 18px;border-bottom:1px solid var(--line)}
.modal .mh b{font-size:16px}.modal .mh small{display:block;font:12px var(--mono);color:var(--faint)}
.modal .mb{overflow:auto;padding:0 18px 18px}
.ib{border:1px solid var(--line);background:var(--surface);color:var(--muted);border-radius:6px;width:26px;height:26px;cursor:pointer;font:600 14px var(--sans);display:grid;place-items:center}
.film{display:flex;gap:6px;overflow-x:auto;padding:14px 0 10px}
.film button{flex:none;width:110px;border:2px solid var(--line);border-radius:6px;padding:0;background:#fff;cursor:pointer;position:relative;overflow:hidden}
.film button img{width:100%;height:68px;object-fit:cover;object-position:top left;display:block}
.film button .k{position:absolute;left:3px;top:3px;font:600 10.5px var(--mono);background:rgba(15,20,19,.7);color:#fff;border-radius:3px;padding:0 4px}
.film button.fail{border-color:var(--bad)}.film button:hover{border-color:var(--ink)}
.film button .cap{display:block;font-size:10.5px;color:var(--muted);padding:2px 4px;text-align:left;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;background:var(--surface)}
.steps{border:1px solid var(--line);border-radius:10px;overflow:hidden}
.visit{display:grid;grid-template-columns:auto 1fr;gap:10px;padding:10px 12px;border-top:1px solid var(--line);font-size:13.5px;align-items:start}
.visit:first-child{border-top:0}.visit.fail{background:var(--bad-soft)}
.visit .no{width:24px;height:24px;border-radius:50%;border:1.5px solid var(--accent);color:var(--accent);font:600 12px/22px var(--mono);text-align:center}
.visit.fail .no{border-color:var(--bad);color:var(--bad)}
.visit .dlg{margin-top:4px;font-size:12.5px;color:var(--muted)}.visit .ck{margin-top:6px;display:flex;flex-wrap:wrap;gap:4px}
.sec{margin-top:16px}.sec h4{margin:0 0 8px;font-size:12.5px;font-weight:600;letter-spacing:.06em;color:var(--muted)}
.runs{display:flex;flex-direction:column;gap:4px}.run{display:grid;grid-template-columns:auto 1fr auto;gap:10px;font-size:13px;padding:6px 10px;border:1px solid var(--line);border-radius:8px}
.run .d{font-family:var(--mono);color:var(--muted)}.run .s{color:var(--muted)}
.dtab{margin-top:10px;overflow-x:auto}.dtab table{font-size:13px}
.lb{position:fixed;inset:0;background:rgba(15,20,19,.82);display:none;place-items:center;z-index:50;padding:24px;cursor:zoom-out}
.lb.on{display:grid}.lb img{max-width:min(96vw,1280px);max-height:80vh;border-radius:8px;background:#fff}
.lb .cap{color:#fff;margin-top:12px;font-size:14px;text-align:center}
"""

JS = r"""
const $ = s => root.querySelector(s), $$ = s => root.querySelectorAll(s);
const D = JSON.parse($('#d').textContent);
const esc = s => String(s ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const modal = $('#modal'), lb = $('#lb');
const KIND = D.kind_label;
const resultPill = r => r.kind === 'accepted_diff' ? '<span class="pill warn">승인된 차이</span>' : r.status === 'pass' ? '<span class="pill ok">같음</span>' : `<span class="pill bad">${esc(KIND[r.kind] || '실패')}</span>`;
function openLb(src, cap){ if(!src) return; lb.querySelector('img').src = src; lb.querySelector('.cap').textContent = cap || ''; lb.classList.add('on'); }
lb.addEventListener('click', () => lb.classList.remove('on'));
function closeModal(){ modal.classList.remove('on'); }
modal.addEventListener('click', e => { if(e.target === modal) closeModal(); });
ctx.listen(window, 'keydown', e => { if(e.key !== 'Escape') return; if(lb.classList.contains('on')) lb.classList.remove('on'); else closeModal(); });
const detail = {};  // 시나리오 상세는 행을 누를 때 받아 둔다
async function openTest(name){
  if(!D.tests[name]) return;
  let dt = detail[name];
  if(!dt){
    try { const r = await fetch(`/api/app/${encodeURIComponent(D.app)}/catalog/${encodeURIComponent(name)}`); if(!r.ok) throw new Error(await r.text()); dt = detail[name] = await r.json(); }
    catch(e){ modal.querySelector('.box').innerHTML = `<div class="mh"><div><b>불러오지 못했습니다</b><small>${esc(e.message)}</small></div><button class="ib" type="button" id="closeModal" aria-label="닫기">×</button></div>`; modal.classList.add('on'); modal.querySelector('#closeModal').addEventListener('click', closeModal); return; }
  }
  const t = dt.test, shots = dt.shots;
  const last = t.last, pill = !last ? '<span class="pill none">비교 전</span>' : resultPill(last);
  let h = `<div class="mh"><div><b>${esc(t.title)}</b><small>${esc(name)} · ${t.steps.length}단계 · ${esc(t.recorded_at || '')} 기록</small></div>${pill}<button class="ib" type="button" id="closeModal" aria-label="닫기">×</button></div><div class="mb">`;
  h += `<div class="film">` + t.steps.map(s => `<button type="button" class="${s.failed ? 'fail' : ''}" data-lb="${esc(s.shot)}" data-cap="${s.index + 1}단계 · ${esc(s.action_text)}" title="${esc(s.action_text)}">`
      + (s.shot ? `<img loading="lazy" src="${shots[s.shot]}" alt="">` : '') + `<span class="k">${s.index + 1}</span><span class="cap">${esc(s.action_text)}</span></button>`).join('') + `</div>`;
  h += `<div class="steps">` + t.steps.map(s => `<div class="visit ${s.failed ? 'fail' : ''}"><span class="no">${s.index + 1}</span><div><div>${s.action}</div>`
      + s.dialogs.map(d => `<div class="dlg">${d.type === 'confirm' ? '확인창' : '알림창'} “${esc(d.message)}” → ${d.action === 'accept' ? '확인' : '취소'}</div>`).join('')
      + (s.checks.length ? `<div class="ck">${s.checks.join('')}</div>` : '') + `</div></div>`).join('') + `</div>`;
  if(last && last.rows && last.rows.length) h += `<div class="sec"><h4>마지막 비교에서 다른 점 · ${esc(last.target)}</h4><div class="dtab"><table><tr><th>무엇이</th><th>as-is</th><th>to-be</th></tr>`
      + last.rows.map(r => `<tr><td>${esc(r[0])}</td><td class="m was">${esc(r[1])}</td><td class="m now">${esc(r[2])}</td></tr>`).join('') + `</table></div></div>`;
  else if(last && last.status !== 'pass') h += `<div class="sec"><h4>마지막 비교</h4><div>${esc(last.summary)}</div></div>`;
  if(t.history.length) h += `<div class="sec"><h4>실행 이력</h4><div class="runs">` + t.history.slice().reverse().map(r =>
      `<div class="run"><span class="d">${esc(r.finished)}</span><span class="s">${esc(r.target)} · 승인본 ${esc(r.approved_at || '없음')}</span>${resultPill(r)}</div>`).join('') + `</div></div>`;
  h += `</div>`;
  modal.querySelector('.box').innerHTML = h;
  modal.classList.add('on');
  modal.querySelector('#closeModal').addEventListener('click', closeModal);
  modal.querySelectorAll('[data-lb]').forEach(b => b.addEventListener('click', () => openLb(shots[b.dataset.lb], b.dataset.cap)));
}
$$('tbody tr[data-test]').forEach(tr => tr.addEventListener('click', () => openTest(tr.dataset.test)));
const q = $('#q');
let st = '';  // 머리 칩으로 고른 결과 ('' = 전부)
// 쪽 나누기: 걸러진 행을 10줄씩. 검색·칩이 바뀌면 1쪽으로
let PER = 10, page = 0;
const tbl = $('.tbl');
function fitRows(){  // 표에 보이는 줄 수 = (표 높이 − 머리줄) / 줄 높이. 스크롤이 생기지 않게 딱 맞춘다
  const thead = tbl.querySelector('thead'), row = tbl.querySelector('tbody tr:not(.dim)') || tbl.querySelector('tbody tr');
  const rowH = row ? Math.max(40, row.getBoundingClientRect().height) : 58;
  const avail = tbl.clientHeight - (thead ? thead.offsetHeight : 0) - 2;
  const per = Math.max(1, Math.floor(avail / rowH));
  if(per !== PER){ PER = per; return true; } return false;
}
ctx.listen(window, 'resize', () => { if(fitRows()) filter(); });
const foot = document.createElement('div'); foot.className = 'pager'; $('.tbl').after(foot);
function filter(resetPage){
  if(resetPage) page = 0;
  const s = q.value.trim().toLowerCase();
  const rows = [...$$('tbody tr[data-test]')], hits = [];
  rows.forEach(tr => {
    const t = D.tests[tr.dataset.test];
    const hit = (!s || t.title.toLowerCase().includes(s) || tr.dataset.test.toLowerCase().includes(s)) && (!st || tr.dataset.state === st);
    tr.classList.toggle('dim', !hit); if(hit) hits.push(tr);
  });
  const n = Math.max(1, Math.ceil(hits.length / PER)); page = Math.min(page, n - 1);
  hits.forEach((tr, i) => tr.classList.toggle('dim', Math.floor(i / PER) !== page));
  if(hits.length <= PER){ foot.innerHTML = hits.length ? '' : '<span class="rng">맞는 시나리오가 없습니다</span>'; return; }
  const nums = [...Array(n).keys()].filter(i => n <= 7 || i === 0 || i === n - 1 || Math.abs(i - page) <= 1);
  let last = -1, btns = '';
  for(const i of nums){ if(i - last > 1) btns += '<span class="gap">…</span>'; btns += `<button type="button" class="${i === page ? 'on' : ''}" data-p="${i}">${i + 1}</button>`; last = i; }
  foot.innerHTML = `<span class="rng">${page * PER + 1}–${Math.min((page + 1) * PER, hits.length)} / ${hits.length}</span><button type="button" data-d="-1" ${page === 0 ? 'disabled' : ''} aria-label="이전 쪽">‹</button>${btns}<button type="button" data-d="1" ${page === n - 1 ? 'disabled' : ''} aria-label="다음 쪽">›</button>`;
  foot.querySelectorAll('[data-p]').forEach(b => b.onclick = () => { page = +b.dataset.p; filter(); });
  foot.querySelectorAll('[data-d]').forEach(b => b.onclick = () => { page += +b.dataset.d; filter(); });
}
fitRows(); filter(); requestAnimationFrame(() => { if(fitRows()) filter(); });
q.addEventListener('input', () => filter(true));
$$('#chips .fchip[data-st]').forEach(b => b.addEventListener('click', () => {
  st = (b.dataset.st && st !== b.dataset.st) ? b.dataset.st : '';
  $$('#chips .fchip[data-st]').forEach(x => x.classList.toggle('on', x.dataset.st === st));
  filter(true);
}));
"""



def build(d: Path, tests_dir: Path | None = None) -> dict[str, Any]:
    app = d.name
    docs = html.docstrings(tests_dir or Path("e2e") / app)
    st = oracle.status(d)
    runs = ledger.load_runs(app)
    hist = ledger.history(app)
    latest = runs[-1] if runs else None
    shots: dict[str, str] = {}
    tests: dict[str, dict[str, Any]] = {}
    for t in oracle.tests(d):
        data = fscache.json_load(d / f"{t['name']}.json")
        by_step = defaultdict(list)
        for a in data.get("assertions", []):
            by_step[a["step"] - 1].append(a)
        last = None
        if latest and t["name"] in latest["cases"]:
            c = latest["cases"][t["name"]]
            last = {"status": c["status"], "kind": c.get("kind", ""), "summary": c.get("summary", ""), "rows": c.get("rows", []), "target": latest["target"]}
        failed_steps: set[int] = set()
        if last and last["status"] != "pass":
            import re
            failed_steps = {int(m.group(1)) for m in re.finditer(r"step (\d+) ", last.get("summary", ""))}
        steps = []
        for o in data.get("steps", []):
            sid = ""
            if o.get("shot"):
                sid = f"{t['name']}#{o['index']}"
                shots[sid] = html.shot_url(d / o["shot"])
            act = html._action(o["kind"], o["text"])
            steps.append({"index": o["index"], "action": act, "action_text": _plain(act),
                          "dialogs": o.get("dialogs", []), "checks": [html._val(a) for a in by_step.get(o["index"], [])],
                          "shot": sid, "failed": o["index"] in failed_steps})
        tests[t["name"]] = {"title": html._title(t["name"], docs), "steps": steps, "assertions": len(t["assertions"]),
                            "recorded_at": t["recorded_at"], "last": last, "history": hist.get(t["name"], [])}
    return {"app": app, "oracle": st, "runs": [{"file": r["_file"], "finished": r["finished"], "target": r["target"], "totals": r["totals"],
                                               "approved_at": r["oracle"].get("approved_at"),
                                               "approval_id": r["oracle"].get("approval_id")} for r in runs],
            "tests": tests, "shots": shots, "kind_label": KIND_LABEL}


def fragment(g: dict[str, Any]) -> dict[str, Any]:
    """골든 관리(시나리오 탭) 조각 (html.fragment 형식). 머리의 칩이 상태 요약이자 거르기, 표는 다른 것부터."""
    st, runs, tests = g["oracle"], g["runs"], g["tests"]
    latest = runs[-1] if runs else None

    def state_of(t: dict[str, Any]) -> str:
        last = t["last"]
        if not last:
            return "never"
        if last.get("kind") == "accepted_diff":
            return "accepted"
        return "pass" if last["status"] == "pass" else "fail"

    states = {n: state_of(t) for n, t in tests.items()}
    n = {k: sum(1 for s in states.values() if s == k) for k in ("pass", "fail", "accepted", "never")}
    stale = bool(latest and st.get("approval_id") and latest.get("approval_id") != st.get("approval_id"))
    notice = (f"<div class='notice'><b>주의</b> 마지막 비교는 이전 승인본({html._e(latest['approved_at'] or '없음')})으로 실행됐습니다. 현재 승인본으로 다시 비교하세요.</div>" if stale else "")

    order = {"fail": 0, "accepted": 1, "never": 2, "pass": 3}
    rows = []
    for name in sorted(tests, key=lambda x: (order[states[x]], x)):
        t, state, last = tests[name], states[name], tests[name]["last"]
        pill = ("<span class='pill none'>비교 전</span>" if state == "never" else f"<span class='pill warn'>{html._e(KIND_LABEL['accepted_diff'])}</span>" if state == "accepted"
                else "<span class='pill ok'>같음</span>" if state == "pass" else f"<span class='pill bad'>{html._e(KIND_LABEL.get(last['kind'], '실패'))}</span>")
        summary = html._e((last or {}).get("summary") or "")[:90] if state == "fail" else ""
        hist = "".join(f"<i class='{'p' if h['status'] == 'pass' else 'f'}{' cur' if i == len(t['history']) - 1 else ''}' title='{html._e(h['finished'])} · {html._e(h['target'])}'></i>"
                       for i, h in enumerate(t["history"][-12:]))
        rows.append(f"<tr data-test='{html._e(name)}' data-state='{state}'><td class='t'><b>{html._e(t['title'])}</b><small>{html._e(name)} · {len(t['steps'])}단계 · 확인 값 {t['assertions']}</small></td>"
                    f"<td class='r'>{pill}{('<small>' + summary + '</small>') if summary else ''}</td>"
                    f"<td><span class='hist'>{hist or '<span class=tid>—</span>'}</span></td><td class='n'>{html._e((t['recorded_at'] or '')[:10])}</td></tr>")
    help_ = html.help(info="이 앱의 골든 시나리오와 to-be 비교 현황입니다. 행을 누르면 단계·캡처·다른 점·실행 이력이 나옵니다. 표는 창 높이에 맞춰 쪽을 나눕니다.",
                      facts=[f"<b>기준 폴더</b> golden/{html._e(g['app'])}", f"<b>실행 기록</b> runs/{html._e(g['app'])} · {len(runs)}회", f"<b>확인 값</b> {sum(t['assertions'] for t in tests.values())}개"])
    body = (notice +
            f"<div class='tools'><input id='q' type='search' placeholder='시나리오 제목·ID로 찾기' aria-label='찾기'><span class='sp'></span>{help_}</div>"
            f"<div class='tbl'><table><thead><tr><th>시나리오</th><th>마지막 결과</th><th>이력 (오래된 → 최근)</th><th>기록일</th></tr></thead><tbody>{''.join(rows)}</tbody></table></div>"
            f"<div class='modal' id='modal' role='dialog' aria-label='시나리오 상세'><div class='box'></div></div>"
            f"<div class='lb' id='lb' role='dialog' aria-label='화면 크게 보기'><div><img src='' alt=''><div class='cap'></div></div></div>")
    light = {"app": g["app"], "kind_label": KIND_LABEL, "tests": {n: {"title": t["title"]} for n, t in tests.items()}}  # 상세는 행을 누를 때 API로
    return html.fragment("catalog", f"{g['app']} 골든 관리", body, css=CSS, js=JS, data=("d", light))


def render(g: dict[str, Any]) -> str:
    """혼자 열리는 문서 (테스트, /page/… 직접 접속). 통합 화면은 fragment() 를 끼운다."""
    return html.assemble(fragment(g))
