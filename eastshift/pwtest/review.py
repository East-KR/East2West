"""시나리오 승인 화면 (eastshift ui 의 시나리오 승인 탭): 검토자가 골든 시나리오를 하나씩 보고 "as-is 동작이 맞다"고 확인한 뒤 이름을 입력해 승인한다.

hub.py 가 /page/<app>/review 요청에 render(build(golden/<app>)) 로 만든다.

화면: 왼쪽 시나리오 목록(확인 여부, 지난 승인 이후 바뀐 것 표시: 기대값 바뀜 · 화면 바뀜 · 새 시나리오) · 가운데 선택한 시나리오(단계별 큰 캡처, 동작·알림창·확인 값) · 오른쪽 규칙(가림·이름 변경·동등 결함).
"확인함" 표시는 보는 사람 브라우저에만 저장된다(localStorage). 목록 위 "전체 확인"은 모두 확인 ↔ 전부 해제 토글이다. 모든 시나리오가 확인되면 승인 폼이 나타나고, 승인은 POST /api/app/<app>/approve (oracle.approve_from_review).
재승인이면 단계마다 '지난 승인 대비' 차이(나타난·사라진 줄, 동작·대화상자·API 변화)를 보여 준다. 지난 승인본은 stepdiff 가 해시로 검증해 찾는다.
읽는 것: golden/<app>/ 와 지난 승인본(runs/<app>/approved/ 또는 git 이력). 새로 판단하는 것은 없다.
화면에는 시나리오 목록만 싣고, 고른 시나리오의 단계·캡처는 /api/app/<app>/review/<test> (hub.detail) 로 받는다.
머리의 'as-is에서 다시 기록' 버튼은 확인 창을 거쳐 서버가 pytest --record 를 돌리게 한다 (hub.start_record). 기록은 승인을 무효로 만들고, 승인은 여전히 사람이 여기서 한다.
"""
from __future__ import annotations

import json
from collections import defaultdict
from pathlib import Path
from typing import Any

from . import fscache, html, oracle, stepdiff
from .map import _plain

CSS = """
main{max-width:none;padding-block:22px 40px;gap:18px}
.rerec{font:600 12px var(--sans);padding:5px 11px;border-radius:999px;border:1px solid var(--line);background:var(--surface);color:var(--muted);cursor:pointer;white-space:nowrap;margin-left:auto}
.rerec:hover{border-color:var(--accent);color:var(--accent)}.rerec:disabled{opacity:.55;cursor:default}
.recline{font-size:12.5px;color:var(--muted);white-space:nowrap;overflow:hidden;text-overflow:ellipsis;max-width:46ch}.recline.ok{color:var(--ok)}.recline.bad{color:var(--bad)}
.head{gap:6px 24px}.head h1{font-size:26px}
.stage{display:grid;grid-template-columns:300px minmax(0,1fr) 340px;gap:14px;height:calc(100vh - 210px);min-height:600px}
.stage.norules{grid-template-columns:300px minmax(0,1fr)}
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
.filters .allok{margin-left:auto;border-color:var(--ok);color:var(--ok)}.filters .allok.on{background:var(--ok);color:#fff;border-color:var(--ok)}
/* 가운데 */
.center{padding:0 18px 18px}
.center .dh{position:sticky;top:0;background:var(--surface);padding:14px 0 10px;border-bottom:1px solid var(--line);z-index:1;display:grid;grid-template-columns:1fr auto;gap:8px 14px;align-items:center}
.center h3{font-size:19px;font-weight:700;line-height:1.3}
.center .sub{grid-column:1/-1;font:12.5px var(--mono);color:var(--muted);display:flex;gap:10px;flex-wrap:wrap}
.okbtn{font:600 14px var(--sans);border:1.5px solid var(--ok);color:var(--ok);background:var(--surface);border-radius:8px;padding:7px 14px;cursor:pointer;white-space:nowrap}
.okbtn.on{background:var(--ok);color:#fff}
.stepv{display:grid;grid-template-columns:minmax(0,1fr) 360px;gap:18px;padding:16px 0}
/* 단계 넘기기: 좌우 버튼 + 필름스트립 + k/N */
.stepnav{display:flex;align-items:center;gap:10px;padding:10px 0;border-bottom:1px solid var(--line)}
.nv{flex:none;width:36px;height:36px;border-radius:50%;border:1px solid var(--line);background:var(--surface);color:var(--ink);font:600 20px/1 var(--sans);cursor:pointer;display:grid;place-items:center}
.nv:hover{border-color:var(--accent);color:var(--accent)}.nv:disabled{opacity:.3;cursor:default;border-color:var(--line);color:var(--ink)}
.stepnav .cnt{flex:none;font:600 12.5px var(--mono);color:var(--muted);min-width:44px;text-align:center}
.film{flex:1;display:flex;gap:6px;overflow-x:auto;min-width:0;scroll-snap-type:x proximity;padding:2px}
.film button{flex:none;width:84px;height:56px;border:2px solid var(--line);border-radius:6px;padding:0;background:#fff;cursor:pointer;position:relative;overflow:hidden;scroll-snap-align:start}
.film button img{width:100%;height:100%;object-fit:cover;object-position:top left;display:block}
.film button .k{position:absolute;left:3px;top:3px;font:600 10.5px var(--mono);background:rgba(15,20,19,.7);color:#fff;border-radius:3px;padding:0 4px}
.film button:hover{border-color:var(--ink)}.film button.on{border-color:var(--accent);box-shadow:0 0 0 2px var(--accent-soft)}
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
.delta{margin-top:10px;border-left:3px solid var(--warn);background:var(--warn-soft);border-radius:0 6px 6px 0;padding:6px 10px;font-size:13px;display:flex;flex-direction:column;gap:3px}
.delta .lab{font-size:12px;font-weight:600;color:var(--warn)}.delta .ln{font-family:var(--mono);overflow-wrap:anywhere}.delta .ln.add::before{content:"+ ";color:var(--ok)}.delta .ln.rem{color:var(--muted);text-decoration:line-through}.delta .ln.rem::before{content:"− "}
.item .flag.obs{background:var(--sunk);color:var(--muted)}
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
.abox:empty{display:none}.af{display:inline-flex;flex-wrap:wrap;gap:6px;align-items:center;margin-left:6px}
.af input{font:inherit;font-size:14px;padding:7px 10px;border:1px solid var(--line);border-radius:8px;background:var(--surface);color:var(--ink);width:150px}
.af button{border:0;background:var(--accent);color:#fff;font:600 14px var(--sans);padding:8px 16px;border-radius:8px;cursor:pointer}
.af .msg{font-size:13px;color:var(--muted)}.af .msg.bad{color:var(--bad);font-weight:600}
.lb{position:fixed;inset:0;background:rgba(15,20,19,.82);display:none;place-items:center;z-index:50;padding:24px;cursor:zoom-out}
.lb.on{display:grid}.lb img{max-width:min(96vw,1400px);max-height:84vh;border-radius:8px;background:#fff}
.lb .cap{color:#fff;margin-top:12px;font-size:14px;text-align:center}
@media (max-width:1100px){.stage{grid-template-columns:260px minmax(0,1fr);height:auto}.pane.right{grid-column:1/-1}.stepv{grid-template-columns:1fr}}
@media (max-width:720px){.stage{grid-template-columns:1fr}}
"""

JS = r"""
const $ = s => root.querySelector(s), $$ = s => root.querySelectorAll(s);
const D = JSON.parse($('#d').textContent);
const FLAG = {chg: '기대값 바뀜', obs: '화면 바뀜', new: '새 시나리오'};
const esc = s => String(s ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const KEY = 'eastshift-review:' + D.app + ':' + D.fingerprint;  // 기록이 바뀌면 확인 표시도 새로 한다
let done = new Set();
try { done = new Set(JSON.parse(localStorage.getItem(KEY) || '[]')); } catch(e) {}
function save(){ try { localStorage.setItem(KEY, JSON.stringify([...done])); } catch(e) {} }
const center = $('#center'), lb = $('#lb');
function openLb(src, cap){ if(!src) return; lb.querySelector('img').src = src; lb.querySelector('.cap').textContent = cap || ''; lb.classList.add('on'); }
lb.addEventListener('click', () => lb.classList.remove('on'));
ctx.listen(window, 'keydown', e => { if(e.key === 'Escape') lb.classList.remove('on'); });
let current = null;
function refresh(){
  $$('.item').forEach(el => { el.classList.toggle('done', done.has(el.dataset.test)); el.classList.toggle('sel', el.dataset.test === current); });
  const n = D.order.length, k = D.order.filter(t => done.has(t)).length;
  $('#cnt').textContent = `${k}/${n} 확인`;
  const all = $('#allok'); all.classList.toggle('on', n > 0 && k === n); all.textContent = n && k === n ? '✓ 전체 확인함' : '전체 확인';
  const chk = $('#chk'); if(chk){ chk.querySelector('b').textContent = `${k}/${n}`; chk.className = 'fchip ' + (n && k === n ? 'ok' : k ? 'warn' : 'none'); }
  // 승인 폼은 머리의 칩 줄 끝에: 모든 시나리오를 확인했고 아직 승인 전일 때만. 승인 상태와 진행은 칩(승인됨 / 확인 k/N)이 보여 준다
  const bar = $('#bar');
  if(!approved && k === n && n) bar.innerHTML = `<form class="af" id="af"><input name="by" placeholder="승인자 이름" required autocomplete="name"><button type="submit">승인</button><span class="msg" id="amsg"></span></form>`;
  else bar.innerHTML = '';
  const af = $('#af'); if(af) af.onsubmit = async e => {
    e.preventDefault(); const msg = $('#amsg'); msg.textContent = '승인 중…'; msg.className = 'msg';
    try {
      const r = await fetch(`/api/app/${encodeURIComponent(D.app)}/approve`, {method: 'POST', headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({by: af.by.value, fingerprint: D.fingerprint})});
      const j = await r.json();
      if(!r.ok || j.error){ msg.textContent = j.error || `실패 (${r.status})`; msg.className = 'msg bad'; return; }
      approved = j; refresh();
      const lead = $('.fchip.lead'); if(lead){ lead.className = 'fchip ok lead'; lead.innerHTML = `<i></i>승인됨<small>${esc(j.approved_by)} · ${esc(j.approved_at.slice(0, 16))}</small>`; }
      try { ctx.approved(); } catch(_) {}  // 통합 화면에 알려 프로젝트 목록·카드의 승인 상태를 갱신한다
    } catch(err){ msg.textContent = '서버에 연결할 수 없습니다: ' + err; msg.className = 'msg bad'; }
  };
  filter();
}
let approved = D.status && D.status.ok ? {approved_by: D.status.approved_by, approved_at: D.status.approved_at} : null;  // 이미 승인된 기준이면 폼 대신 승인 표시
let stepIdx = 0, stepOf = null, stepGo = null;  // 가운데 화면의 현재 단계
ctx.listen(window, 'keydown', e => {  // ← → 로 단계 넘기기 (입력칸에 있을 때는 제외)
  if(!stepGo || (e.key !== 'ArrowLeft' && e.key !== 'ArrowRight') || /^(INPUT|TEXTAREA|SELECT)$/.test((e.target && e.target.tagName) || '')) return;
  e.preventDefault(); stepGo(e.key === 'ArrowLeft' ? -1 : 1);
});
const detail = {};  // 시나리오 상세는 고를 때 받아 둔다 (목록 화면에는 없다)
async function load(name){
  if(detail[name]) return detail[name];
  const r = await fetch(`/api/app/${encodeURIComponent(D.app)}/review/${encodeURIComponent(name)}`);
  if(!r.ok) throw new Error(await r.text());
  return detail[name] = await r.json();
}
async function show(name){
  if(!D.tests[name]) return;
  current = name; refresh();
  if(!detail[name]) center.innerHTML = '<div class="empty">불러오는 중…</div>';
  let dt;
  try { dt = await load(name); } catch(e){ center.innerHTML = `<div class="empty">시나리오를 불러오지 못했습니다: ${esc(e.message)}</div>`; return; }
  if(current !== name) return;  // 기다리는 동안 다른 시나리오를 골랐다
  const t = dt.test, shots = dt.shots;
  let h = `<div class="dh"><h3>${esc(t.title)}${t.flag ? ` <span class="flag ${t.flag}">${FLAG[t.flag]}</span>` : ''}</h3>`
    + `<button type="button" class="okbtn ${done.has(name) ? 'on' : ''}" id="ok">${done.has(name) ? '✓ 확인함' : 'as-is 동작이 맞음 · 확인함'}</button>`
    + `<div class="sub"><span>${esc(name)}</span><span>${t.steps.length}단계</span>${t.gone.length ? `<span>지난 승인보다 ${t.gone.length}단계 줄어듦: ${esc(t.gone.join(' · '))}</span>` : ''}<span>${esc(t.base_url)}에서 ${esc(t.recorded_at || '')} 기록</span></div></div>`;
  // 단계는 한 번에 하나: 좌우 버튼(←/→ 키)으로 넘기고, 필름스트립으로 바로 간다
  if(name !== stepOf) { stepIdx = 0; stepOf = name; }
  h += `<div class="stepnav"><button type="button" class="nv" id="prev" aria-label="이전 단계">‹</button><div class="film" id="film">`
    + t.steps.map((s, i) => `<button type="button" data-i="${i}" title="${s.index + 1}단계 · ${esc(s.action_text)}">${s.shot ? `<img loading="lazy" src="${shots[s.shot]}" alt="">` : ''}<span class="k">${s.index + 1}</span></button>`).join('')
    + `</div><span class="cnt" id="scnt"></span><button type="button" class="nv" id="next" aria-label="다음 단계">›</button></div><div id="stepbox"></div>`;
  center.innerHTML = h; center.scrollTop = 0;
  center.querySelector('#ok').addEventListener('click', () => { done.has(name) ? done.delete(name) : done.add(name); save(); show(name); refresh(); });
  const box = center.querySelector('#stepbox'), film = center.querySelector('#film');
  function draw(){
    const s = t.steps[stepIdx];
    let b = `<div class="stepv"><div>` + (s.shot ? `<div class="shot" data-lb="${s.shot}" data-cap="${s.index + 1}단계 · ${esc(s.action_text)}"><img loading="lazy" src="${shots[s.shot]}" alt="${s.index + 1}단계 화면"></div>` : `<div class="noshot">캡처 없음</div>`) + `</div><div>`;
    b += `<div class="act"><span class="no">${s.index + 1}</span><span>${s.action}</span></div>`;
    if(s.seen.length) b += `<div class="seen"><span class="lab">나타남</span>${s.seen.map(x => `<span class="it">${esc(x)}</span>`).join('')}</div>`;
    s.dialogs.forEach(d => { b += `<div class="dlg"><span class="verb">${d.type === 'confirm' ? '확인창' : '알림창'}</span><b>${esc(d.message)}</b><span class="ans">→ ${d.action === 'accept' ? '확인' : '취소'}</span></div>`; });
    (s.api || []).forEach(a => { b += `<details class="dlg"><summary>API ${esc(a.method)} ${esc(a.path)} · ${a.status}</summary><pre style="white-space:pre-wrap;overflow-wrap:anywhere">${esc(a.body)}</pre></details>`; });
    if(s.delta){
      const x = s.delta;
      b += `<div class="delta"><span class="lab">지난 승인 대비${x.new ? ' · 새 단계' : ''}</span>`;
      if(x.action_old) b += `<span>동작: <s>${esc(x.action_old)}</s></span>`;
      b += x.added.map(l => `<span class="ln add">${esc(l)}</span>`).join('') + x.removed.map(l => `<span class="ln rem">${esc(l)}</span>`).join('');
      if(x.dialogs) b += `<span>대화상자: <s>${esc(x.dialogs[0].join(', ') || '없음')}</s> → ${esc(x.dialogs[1].join(', ') || '없음')}</span>`;
      if(x.api) b += `<span>API 응답 바뀜: ${esc(x.api.join(', '))}</span>`;
      b += `</div>`;
    }
    if(s.checks.length) b += `<div class="checks">` + s.checks.map(c => `<div class="check ${c.cls}"><span>✓</span><span class="k">${esc(c.k)}</span><span class="v">${esc(c.v)}</span>${c.old ? `<s>${esc(c.old)}</s>` : ''}</div>`).join('') + `</div>`;
    b += `</div></div>`;
    box.innerHTML = b;
    box.querySelectorAll('[data-lb]').forEach(el => el.addEventListener('click', () => openLb(shots[el.dataset.lb], el.dataset.cap)));
    film.querySelectorAll('button').forEach(f => f.classList.toggle('on', +f.dataset.i === stepIdx));
    const on = film.querySelector('button.on'); if(on) on.scrollIntoView({block: 'nearest', inline: 'nearest'});
    center.querySelector('#scnt').textContent = `${stepIdx + 1} / ${t.steps.length}`;
    center.querySelector('#prev').disabled = stepIdx === 0; center.querySelector('#next').disabled = stepIdx >= t.steps.length - 1;
  }
  stepGo = d => { const n = stepIdx + d; if(n < 0 || n >= t.steps.length) return; stepIdx = n; draw(); };
  center.querySelector('#prev').addEventListener('click', () => stepGo(-1));
  center.querySelector('#next').addEventListener('click', () => stepGo(1));
  film.querySelectorAll('button').forEach(f => f.addEventListener('click', () => { stepIdx = +f.dataset.i; draw(); }));
  draw();
  refresh();
  try { history.replaceState(null, '', '#' + encodeURIComponent(name)); } catch(e) {}
}
let mode = 'all';
function filter(){
  $$('.item').forEach(el => {
    const t = D.tests[el.dataset.test];
    const hit = mode === 'all' || (mode === 'todo' && !done.has(el.dataset.test)) || (mode === 'chg' && t.flag);
    el.classList.toggle('dim', !hit);
  });
}
$('#allok').addEventListener('click', () => {  // 전체 확인 토글: 모두 확인됐으면 전부 해제, 아니면 전부 확인 (승인은 여전히 이름을 넣어야 된다)
  done = D.order.every(t => done.has(t)) ? new Set() : new Set(D.order); save(); if(current) show(current); else refresh();
});
$$('.filters button[data-mode]').forEach(b => b.addEventListener('click', () => { mode = b.dataset.mode; $$('.filters button[data-mode]').forEach(x => x.classList.toggle('on', x === b)); filter(); }));
$$('.item').forEach(el => el.addEventListener('click', () => show(el.dataset.test)));
// as-is에서 다시 기록: 확인 창 → POST /api/app/<app>/record → 진행 한 줄 → 끝나면 화면을 새로 그린다 (승인은 그 뒤 사람이)
const rerec = $('#rerec'), recline = $('#recline');
let recTimer = null;
function recShow(j){
  recline.hidden = false;
  const last = (j.log || []).slice(-1)[0] || '';
  recline.className = 'recline' + (j.running ? '' : j.ok ? ' ok' : ' bad');
  recline.textContent = j.running ? `기록 중… ${last}` : j.ok ? '끝. 바뀐 곳을 보고 다시 승인하세요.' : `실패: ${j.error || last}`;
}
async function recPoll(first){
  let j; try { j = await (await fetch(`/api/app/${encodeURIComponent(D.app)}/job`)).json(); } catch(e) { return; }
  if(j.kind !== 'record'){ rerec.disabled = !!j.running; return; }
  if(j.running){ rerec.disabled = true; rerec.textContent = '기록 중…'; recShow(j); recTimer = setTimeout(() => recPoll(false), 2000); return; }
  if(first) return;
  recShow(j); rerec.disabled = false; rerec.textContent = 'as-is에서 다시 기록';
  if(j.ok) setTimeout(() => { if(ctx && ctx.refresh) ctx.refresh(); else location.reload(); }, 1200);
}
if(rerec){
  ctx.onDestroy && ctx.onDestroy(() => clearTimeout(recTimer));
  rerec.addEventListener('click', async () => {
    let asis = '';
    try { const a = await (await fetch(`/api/app/${encodeURIComponent(D.app)}`)).json(); asis = ((a.project || {}).asis || {}).url || ''; } catch(e) {}
    if(!asis){ recline.hidden = false; recline.className = 'recline bad'; recline.textContent = 'as-is 주소가 없습니다. 프로젝트 설정에서 적으세요.'; return; }
    if(!confirm(`골든 ${D.order.length}개를 as-is(${asis})에서 다시 기록해 덮어씁니다.\n지금 승인은 무효가 되고, 끝나면 바뀐 곳을 보고 다시 승인해야 합니다.\n\n계속할까요?`)) return;
    rerec.disabled = true; rerec.textContent = '기록 중…';
    try {
      const r = await fetch(`/api/app/${encodeURIComponent(D.app)}/record`, {method: 'POST', headers: {'Content-Type': 'application/json'}, body: '{}'});
      const j = await r.json(); if(!r.ok || j.error) throw new Error(j.error || r.statusText);
      recShow(j); recTimer = setTimeout(() => recPoll(false), 2000);
    } catch(e) { recline.hidden = false; recline.className = 'recline bad'; recline.textContent = '시작하지 못했습니다: ' + e.message; rerec.disabled = false; rerec.textContent = 'as-is에서 다시 기록'; }
  });
  recPoll(true);
}
const first = decodeURIComponent(ctx.getSub() || '');
show(D.tests[first] ? first : (D.order.find(t => D.tests[t].flag) || D.order.find(t => !done.has(t)) || D.order[0]));
"""


FLAGS = {"chg": "기대값 바뀜", "obs": "화면 바뀜", "new": "새 시나리오"}



def _check(a: dict[str, Any], cls: str = "", old: dict[str, Any] | None = None) -> dict[str, str]:
    kind = html.KIND.get(a["kind"], a["kind"])
    k, v = (a["target"], a["value"] if a["value"] != "" else "(빈 값)") if a["kind"] == "field" else (kind, a["target"])
    o = "" if old is None else (old["value"] if old["kind"] == "field" else old["target"])
    return {"k": k, "v": v, "cls": cls, "old": o}


def _delta(x: dict[str, Any] | None) -> dict[str, Any] | None:
    """stepdiff 한 단계 → 화면용. 동작이 바뀌었으면 예전 동작을 읽을 수 있는 문장으로."""
    if not x:
        return None
    old = x.get("action", [None])[0]
    return {"new": bool(x.get("new")), "action_old": _plain(html._action(*old)) if old and not x.get("new") else "",
            "added": x["added"][:12], "removed": x["removed"][:12], "dialogs": x.get("dialogs"), "api": x.get("api")}


def build(d: Path, tests_dir: Path | None = None) -> dict[str, Any]:
    import hashlib
    from collections import Counter

    from ..observe import flatten, mask

    docs = html.docstrings(tests_dir or Path("e2e") / d.name)
    st = oracle.status(d)
    prev = oracle.approved_assertions(d)
    # 승인 뒤 바뀐 기준이면 지난 승인본을 찾아 단계별로 비교한다 (승인 그대로면 볼 것이 없다)
    approved = stepdiff.approved_goldens(d) if not st["ok"] and st.get("approved_by") else {}
    cfg = oracle.load_config(d)
    rules = cfg.get("ignore", [])
    shots: dict[str, str] = {}
    tests: dict[str, dict[str, Any]] = {}
    order: list[str] = []
    for t in oracle.tests(d):
        data = fscache.json_load(d / f"{t['name']}.json")
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
        deltas = {x["index"]: x for x in stepdiff.diff(approved[t["name"]], data, rules)} if t["name"] in approved else {}
        if not flag and deltas:
            flag = "obs"
        gone = [_plain(html._action(*x["action"][0])) for x in deltas.values() if x.get("gone")]
        steps, prev_lines = [], Counter()
        for o in data.get("steps", []):
            cur = Counter(mask(flatten(o.get("snapshot", ""), keep_urls=False), rules))
            new = [l for l in (cur - prev_lines).elements() if not l.startswith(("option ", "link "))]
            prev_lines = cur
            seen = list(dict.fromkeys(html._clean(html._seen(l)) for l in new))[:8]
            sid = ""
            if o.get("shot"):
                sid = f"{t['name']}#{o['index']}"
                shots[sid] = html.shot_url(d / o["shot"])
            act = html._action(o["kind"], o["text"])
            steps.append({"index": o["index"], "action": act, "action_text": _plain(act), "seen": seen,
                          "dialogs": o.get("dialogs", []), "api": o.get("api", []),
                          "checks": by_step.get(o["index"], []), "shot": sid, "delta": _delta(deltas.get(o["index"]))})
        tests[t["name"]] = {"title": html._title(t["name"], docs), "steps": steps, "flag": flag, "gone": gone, "base_url": t["base_url"], "recorded_at": t["recorded_at"]}
        order.append(t["name"])
    removed = [] if prev is None else sorted(set(prev) - set(order))
    fp = oracle.oracle_files(d)
    return {"app": d.name, "status": st, "tests": tests, "order": order,
            "shots": shots, "removed": removed, "masks": oracle.mask_hits(d), "maps": oracle.name_maps(d),
            "eqs": cfg.get("equivalent_mutants", []), "api_paths": cfg.get("api_compare", []),
            "allowed": cfg.get("allowed_differences", []), "coverage": cfg.get("coverage", []),
            "redact_fields": cfg.get("redact_fields", []), "redact_patterns": cfg.get("redact_patterns", []),
            # 기록이 바뀌면 브라우저에 남긴 "확인함" 표시를 새로 시작하려고 파일 해시들의 해시를 쓴다
            "fingerprint": oracle.fingerprint(d)}


def fragment(g: dict[str, Any]) -> dict[str, Any]:
    """시나리오 승인 조각 (html.fragment 형식). 왼쪽 시나리오 목록 · 가운데 단계 화면 · 오른쪽 규칙(있을 때만). 모든 시나리오를 확인하면 승인 폼이 나온다."""
    st = g["status"]
    n_all = len(g["tests"])
    n_chg = sum(1 for t in g["tests"].values() if t["flag"] == "chg")
    n_new = sum(1 for t in g["tests"].values() if t["flag"] == "new")
    n_obs = sum(1 for t in g["tests"].values() if t["flag"] == "obs")
    chips = []
    if st["ok"]:
        chips.append(html.chip("ok", "승인됨", sub=f"{st['approved_by']} · {(st['approved_at'] or '')[:16]}", lead=True))
    elif st.get("approved_by"):
        chips.append(html.chip("warn", "재승인 필요", sub="승인 후 기록이 바뀜", lead=True))
    else:
        chips.append(html.chip("warn", "승인 필요", sub="처음 승인", lead=True))
    chips.append(f"<span class='fchip info'>시나리오<b>{n_all}</b></span>")
    chips.append(f"<span class='fchip none' id='chk' title='이 브라우저에서 확인함 표시를 한 시나리오'><i></i>확인<b>0/{n_all}</b></span>")
    if n_chg:
        chips.append(html.chip("warn", "기대값 바뀜", n_chg, title="지난 승인 이후 기대값이 바뀐 시나리오"))
    if n_obs:
        chips.append(html.chip("none", "화면 바뀜", n_obs, title="기대값은 그대로지만 지난 승인 이후 기록된 화면·대화상자·API 응답이 바뀐 시나리오"))
    if n_new:
        chips.append(html.chip("accepted", "새 시나리오", n_new, title="지난 승인 이후 새로 기록된 시나리오"))
    if g["removed"]:
        chips.append(html.chip("none", "없어진 시나리오", len(g["removed"]), title=", ".join(g["removed"])))
    chips.append("<span class='abox' id='bar'></span>")  # 모두 확인하면 여기에 승인 폼 (이름 + 승인)
    # as-is 가 바뀌어 골든을 새로 떠야 할 때: 확인 창을 거쳐 서버가 pytest --record 를 돌린다. 끝나면 이 화면이 새로 그려져 '화면 바뀜'과 재승인 폼이 보인다
    chips.append("<button type='button' class='rerec' id='rerec' title='as-is에서 시나리오 전부를 다시 기록해 골든을 덮어씁니다. 지금 승인은 무효가 되고 다시 승인해야 합니다'>as-is에서 다시 기록</button>"
                 "<span class='recline' id='recline' hidden></span>")
    items = "".join(f"<button type='button' class='item' data-test='{html._e(n)}'><span class='box'></span><span class='t'>{html._e(t['title'])}"
                    + (f"<span class='flag {t['flag']}'>{FLAGS[t['flag']]}</span>" if t["flag"] else "")
                    + f"<small>{html._e(n)} · {len(t['steps'])}단계</small></span></button>" for n, t in g["tests"].items())
    notice = ""
    if st.get("problems") and st.get("approved_by"):
        notice = "<div class='notice'><b>승인 후 변경된 파일</b> " + ", ".join(html._e(p) for p in st["problems"]) + "</div>"

    # 오른쪽 규칙: 내용이 있는 묶음만. 하나도 없으면 오른쪽 칸 자체를 없앤다
    groups: list[tuple[str, str]] = []
    if g["masks"]:
        groups.append(("가리는 값 · 매번 바뀌는 값만", "<table>" + "".join(f"<tr><td class='m'>{html._e(m['rule'])}</td><td>" + ("".join(f"<span class='chip'>{html._e(k)}</span>" for k, _ in m["samples"]) or "<span class='warnchip'>아무것도 가리지 않음</span>") + f"<br><span class='tid'>{m['total']}곳</span></td></tr>" for m in g["masks"]) + "</table>"))
    if g["api_paths"]:
        groups.append(("비교하는 API 경로", "".join(f"<span class='chip'>{html._e(p)}</span>" for p in g["api_paths"])))
    if g["redact_fields"] or g["redact_patterns"]:
        groups.append(("민감정보 저장 제외", "".join(f"<span class='chip'>{html._e(p)}</span>" for p in g["redact_fields"] + g["redact_patterns"])))
    if g["coverage"]:
        groups.append(("필수 업무 경우", "<table>" + "".join(f"<tr><td>{html._e(e.get('case'))}</td><td>{html._e(', '.join(e.get('tests', [])))}</td></tr>" for e in g["coverage"]) + "</table>"))
    if g["allowed"]:
        groups.append(("승인된 차이", "<table>" + "".join(f"<tr><td class='m'>{html._e(e.get('test'))} · {html._e(e.get('step'))}단계</td><td>{html._e(e.get('reason'))}</td></tr>" for e in g["allowed"]) + "</table>"))
    if g["maps"]:
        groups.append(("이름 변경 · to-be에서 바뀌어도 되는 라벨", "<table>" + "".join(f"<tr><td>{html._e(a)}</td><td>→ <b>{html._e(b)}</b></td><td class='m'>{html._e(t)}</td></tr>" for t, m in g["maps"].items() for a, b in m.items()) + "</table>"))
    if g["eqs"]:
        groups.append(("동등 결함 · 탐지율에서 빼는 결함", "<table>" + "".join(f"<tr><td class='m'>{html._e(e.get('path'))}<br>{html._e(e.get('context'))}</td><td>{html._e(e.get('reason', ''))}</td></tr>" for e in g["eqs"]) + "</table>"))
    rules = (f"<div class='pane right'><h2>규칙 <span>{len(groups)}묶음</span></h2><div class='scroll rules'>"
             + "".join(f"<div><h4>{title}</h4>{content}</div>" for title, content in groups) + "</div></div>") if groups else ""
    body = (html.head(chips, info="as-is에서 기록한 동작이 to-be의 정답이 됩니다. 시나리오마다 단계 화면과 확인 값이 실제 업무와 맞는지 보고 '확인함'을 누르세요. 모두 확인하면 아래 막대에 승인 폼이 나옵니다.",
                      facts=[f"<b>기준 폴더</b> golden/{html._e(g['app'])}", f"<b>규칙</b> {len(groups)}묶음" if groups else "<b>규칙</b> 없음 (oracle.json 의 가림·이름 변경·동등 결함·승인된 차이가 오른쪽 칸에 보입니다)"])
            + notice +
            f"<div class='stage{' norules' if not groups else ''}'><div class='pane left'><h2><span>시나리오</span><span id='cnt'></span></h2>"
            f"<div class='filters'><button type='button' class='on' data-mode='all'>전체</button><button type='button' data-mode='todo'>미확인</button><button type='button' data-mode='chg'>바뀐 것</button><button type='button' class='allok' id='allok' title='모든 시나리오를 확인함으로 표시하거나 전부 해제'>전체 확인</button></div>"
            f"<div class='scroll list'>{items}</div></div>"
            f"<div class='pane center scroll' id='center'><div class='empty'>왼쪽에서 시나리오를 고르세요</div></div>{rules}</div>"
            f"<div class='lb' id='lb' role='dialog' aria-label='화면 크게 보기'><div><img src='' alt=''><div class='cap'></div></div></div>")
    # 화면에는 목록만 싣는다. 단계·캡처·다른 점은 시나리오를 고를 때 /api/app/<app>/review/<test> 로 받는다 (시나리오 수천 개여도 페이지는 가볍다)
    light = {"app": g["app"], "status": st, "order": g["order"], "removed": g["removed"], "fingerprint": g["fingerprint"],
             "tests": {n: {"title": t["title"], "flag": t["flag"], "steps": len(t["steps"])} for n, t in g["tests"].items()}}
    return html.fragment("review", f"{g['app']} 기준 승인", body, css=CSS, js=JS, data=("d", light))


def render(g: dict[str, Any]) -> str:
    """혼자 열리는 문서 (테스트, /page/… 직접 접속). 통합 화면은 fragment() 를 끼운다."""
    return html.assemble(fragment(g))
