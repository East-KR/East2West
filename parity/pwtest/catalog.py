"""골든 관리 화면 (카탈로그): 한 앱의 골든 시나리오를 한눈에. 승인 상태, 마지막 to-be 결과, 실행 이력, 시나리오 상세, 화면 지도로 가는 링크.

parity catalog golden/<app> [--out reports/catalog-<app>.html]

읽는 것: golden/<app>/ (시나리오, 기대값, 캡처, 승인), runs/<app>/ (실행 원장). 새로 판단하는 것은 없다.
화면: 위에 승인·마지막 실행 요약, 그 아래 시나리오 표 (제목, 단계 수, 확인 값, 마지막 결과, 이력 점). 행을 누르면 시나리오 팝업
(필름스트립 + 단계 + 마지막 실행에서 다른 점). 오른쪽 위에 화면 지도·검증 보고서 링크.
"""
from __future__ import annotations

import base64
import json
from collections import defaultdict
from pathlib import Path
from typing import Any

from . import html, ledger, oracle
from .map import _plain

KIND_LABEL = {"same": "같음", "drift": "기대값 바뀜", "golden_diff": "as-is와 다름", "assert": "확인 값 실패", "error": "실행 못 함"}

CSS = """
main{max-width:1240px;gap:22px}
.head{gap:6px 24px}.head h1{font-size:26px}
.summary{display:grid;grid-template-columns:repeat(auto-fit,minmax(200px,1fr));gap:10px}
.card{background:var(--surface);border:1px solid var(--line);border-radius:12px;padding:14px 16px}
.card .k{font-size:12.5px;color:var(--muted)}.card .v{font:600 22px var(--mono);margin-top:2px}.card .v small{font:12.5px var(--sans);color:var(--muted);margin-left:6px}
.card.bad .v{color:var(--bad)}.card.ok .v{color:var(--ok)}.card.warn .v{color:var(--warn)}
.links{display:flex;gap:8px;flex-wrap:wrap}
.links a,.links button{font:600 13px var(--sans);padding:7px 12px;border:1px solid var(--line);border-radius:8px;background:var(--surface);color:var(--ink);text-decoration:none;cursor:pointer}
.links a:hover,.links button:hover{border-color:var(--accent);color:var(--accent)}
.tools{display:flex;gap:8px;flex-wrap:wrap;align-items:center}
.tools input,.tools select{font:inherit;font-size:14px;padding:7px 11px;border:1px solid var(--line);border-radius:8px;background:var(--surface);color:var(--ink)}
.tools input{min-width:260px}
.tbl table{width:100%;border-collapse:collapse;font-size:14px;background:var(--surface);border:1px solid var(--line);border-radius:12px;overflow:hidden}
.tbl th{font-size:12px;letter-spacing:.04em;color:var(--muted);text-align:left;padding:10px 12px;background:var(--sunk);border-bottom:1px solid var(--line)}
.tbl td{padding:10px 12px;border-bottom:1px solid var(--line);vertical-align:middle}
.tbl tr:last-child td{border-bottom:0}.tbl tbody tr{cursor:pointer}.tbl tbody tr:hover td{background:var(--accent-soft)}
.tbl tr.dim{display:none}
.tbl .t b{display:block;font-size:14.5px}.tbl .t small{font:11.5px var(--mono);color:var(--faint)}
.tbl .n{font-family:var(--mono);font-variant-numeric:tabular-nums;color:var(--muted);white-space:nowrap}
.hist{display:inline-flex;gap:3px;align-items:center}.hist i{width:10px;height:10px;border-radius:3px;background:var(--line);display:inline-block}
.hist i.p{background:var(--ok)}.hist i.f{background:var(--bad)}.hist i.cur{outline:2px solid var(--ink);outline-offset:1px}
.pill.none{background:var(--sunk);color:var(--muted)}
.chips{display:flex;flex-wrap:wrap;gap:4px}
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

JS = r"""<script>
const D = JSON.parse(document.getElementById('d').textContent);
const esc = s => String(s ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const modal = document.getElementById('modal'), lb = document.getElementById('lb');
const KIND = D.kind_label;
function openLb(src, cap){ if(!src) return; lb.querySelector('img').src = src; lb.querySelector('.cap').textContent = cap || ''; lb.classList.add('on'); }
lb.addEventListener('click', () => lb.classList.remove('on'));
function closeModal(){ modal.classList.remove('on'); }
modal.addEventListener('click', e => { if(e.target === modal) closeModal(); });
addEventListener('keydown', e => { if(e.key !== 'Escape') return; if(lb.classList.contains('on')) lb.classList.remove('on'); else closeModal(); });
function openTest(name){
  const t = D.tests[name]; if(!t) return;
  const last = t.last, pill = !last ? '<span class="pill none">비교 전</span>' : last.status === 'pass' ? '<span class="pill ok">as-is와 같음</span>' : `<span class="pill bad">${esc(KIND[last.kind] || last.kind)}</span>`;
  let h = `<div class="mh"><div><b>${esc(t.title)}</b><small>${esc(name)} · ${t.steps.length}단계 · ${esc(t.recorded_at || '')} 기록</small></div>${pill}<button class="ib" type="button" id="closeModal" aria-label="닫기">×</button></div><div class="mb">`;
  h += `<div class="film">` + t.steps.map(s => `<button type="button" class="${s.failed ? 'fail' : ''}" data-lb="${esc(s.shot)}" data-cap="${s.index + 1}단계 · ${esc(s.action_text)}" title="${esc(s.action_text)}">`
      + (s.shot ? `<img src="${D.shots[s.shot]}" alt="">` : '') + `<span class="k">${s.index + 1}</span><span class="cap">${esc(s.action_text)}</span></button>`).join('') + `</div>`;
  h += `<div class="steps">` + t.steps.map(s => `<div class="visit ${s.failed ? 'fail' : ''}"><span class="no">${s.index + 1}</span><div><div>${s.action}</div>`
      + s.dialogs.map(d => `<div class="dlg">${d.type === 'confirm' ? '확인창' : '알림창'} “${esc(d.message)}” → ${d.action === 'accept' ? '확인' : '취소'}</div>`).join('')
      + (s.checks.length ? `<div class="ck">${s.checks.join('')}</div>` : '') + `</div></div>`).join('') + `</div>`;
  if(last && last.rows && last.rows.length) h += `<div class="sec"><h4>마지막 비교에서 다른 점 · ${esc(last.target)}</h4><div class="dtab"><table><tr><th>무엇이</th><th>as-is</th><th>to-be</th></tr>`
      + last.rows.map(r => `<tr><td>${esc(r[0])}</td><td class="m was">${esc(r[1])}</td><td class="m now">${esc(r[2])}</td></tr>`).join('') + `</table></div></div>`;
  else if(last && last.status !== 'pass') h += `<div class="sec"><h4>마지막 비교</h4><div>${esc(last.summary)}</div></div>`;
  if(t.history.length) h += `<div class="sec"><h4>실행 이력</h4><div class="runs">` + t.history.slice().reverse().map(r =>
      `<div class="run"><span class="d">${esc(r.finished)}</span><span class="s">${esc(r.target)} · 승인본 ${esc(r.approved_at || '없음')}</span>${r.status === 'pass' ? '<span class="pill ok">같음</span>' : `<span class="pill bad">${esc(KIND[r.kind] || '실패')}</span>`}</div>`).join('') + `</div></div>`;
  h += `</div>`;
  modal.querySelector('.box').innerHTML = h;
  modal.classList.add('on');
  modal.querySelector('#closeModal').addEventListener('click', closeModal);
  modal.querySelectorAll('[data-lb]').forEach(b => b.addEventListener('click', () => openLb(D.shots[b.dataset.lb], b.dataset.cap)));
}
document.querySelectorAll('tbody tr[data-test]').forEach(tr => tr.addEventListener('click', () => openTest(tr.dataset.test)));
const q = document.getElementById('q'), f = document.getElementById('f');
function filter(){
  const s = q.value.trim().toLowerCase(), st = f.value;
  document.querySelectorAll('tbody tr[data-test]').forEach(tr => {
    const t = D.tests[tr.dataset.test];
    const hit = (!s || t.title.toLowerCase().includes(s) || tr.dataset.test.toLowerCase().includes(s)) && (!st || tr.dataset.state === st);
    tr.classList.toggle('dim', !hit);
  });
}
q.addEventListener('input', filter); f.addEventListener('change', filter);
</script>"""


def _img(p: Path) -> str:
    return "data:image/jpeg;base64," + base64.b64encode(p.read_bytes()).decode() if p.exists() else ""


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
        data = json.loads((d / f"{t['name']}.json").read_text(encoding="utf-8"))
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
                shots[sid] = _img(d / o["shot"])
            act = html._action(o["kind"], o["text"])
            steps.append({"index": o["index"], "action": act, "action_text": _plain(act),
                          "dialogs": o.get("dialogs", []), "checks": [html._val(a) for a in by_step.get(o["index"], [])],
                          "shot": sid, "failed": o["index"] in failed_steps})
        tests[t["name"]] = {"title": html._title(t["name"], docs), "steps": steps, "assertions": len(t["assertions"]),
                            "recorded_at": t["recorded_at"], "last": last, "history": hist.get(t["name"], [])}
    return {"app": app, "oracle": st, "runs": [{"file": r["_file"], "finished": r["finished"], "target": r["target"], "totals": r["totals"],
                                               "approved_at": r["oracle"].get("approved_at")} for r in runs],
            "tests": tests, "shots": shots, "kind_label": KIND_LABEL}


def render(g: dict[str, Any]) -> str:
    st, runs, tests = g["oracle"], g["runs"], g["tests"]
    latest = runs[-1] if runs else None
    n_fail = sum(1 for t in tests.values() if t["last"] and t["last"]["status"] != "pass")
    n_pass = sum(1 for t in tests.values() if t["last"] and t["last"]["status"] == "pass")
    n_never = len(tests) - n_fail - n_pass
    if not st["ok"]:
        stamp = ("warn", "승인 필요", "기준이 승인되지 않음")
    elif not latest:
        stamp = ("ok", "승인됨", "to-be 비교 전")
    elif n_fail:
        stamp = ("bad", f"남은 실패 {n_fail}", f"{len(tests)}개 중")
    else:
        stamp = ("ok", "모두 같음", f"{len(tests)}개 시나리오")
    stale = bool(latest and st.get("approved_at") and latest["approved_at"] != st.get("approved_at"))
    cards = [("승인", (st.get("approved_by") or "없음") + (f"<small>{html._e((st.get('approved_at') or '')[:16])}</small>" if st["ok"] else ""), "ok" if st["ok"] else "warn"),
             ("시나리오", f"{len(tests)}<small>확인 값 {sum(t['assertions'] for t in tests.values())}</small>", ""),
             ("마지막 비교", (f"{html._e(latest['finished'][:16])}<small>{html._e(latest['target'])}</small>" if latest else "없음"), "warn" if stale else ""),
             ("결과", (f"{n_pass} 같음 · {n_fail} 다름" + (f" · {n_never} 비교 전" if n_never else "")) if latest else "비교 전", "bad" if n_fail else ("ok" if latest else ""))]
    summary = "<div class='summary'>" + "".join(f"<div class='card {c}'><div class='k'>{k}</div><div class='v'>{v}</div></div>" for k, v, c in cards) + "</div>"
    if stale:
        summary += f"<div class='notice'><b>주의</b> 마지막 비교는 이전 승인본({html._e(latest['approved_at'] or '없음')})으로 실행됐습니다. 현재 승인본으로 다시 비교하세요.</div>"
    rows = []
    for name, t in tests.items():
        last = t["last"]
        state = "never" if not last else ("pass" if last["status"] == "pass" else "fail")
        pill = "<span class='pill none'>비교 전</span>" if not last else ("<span class='pill ok'>같음</span>" if last["status"] == "pass" else f"<span class='pill bad'>{html._e(KIND_LABEL.get(last['kind'], '실패'))}</span>")
        hist = "".join(f"<i class='{'p' if h['status'] == 'pass' else 'f'}{' cur' if i == len(t['history']) - 1 else ''}' title='{html._e(h['finished'])} · {html._e(h['target'])}'></i>"
                       for i, h in enumerate(t["history"][-12:]))
        rows.append(f"<tr data-test='{html._e(name)}' data-state='{state}'><td class='t'><b>{html._e(t['title'])}</b><small>{html._e(name)}</small></td>"
                    f"<td class='n'>{len(t['steps'])}</td><td class='n'>{t['assertions']}</td><td>{pill}</td>"
                    f"<td><span class='hist'>{hist or '<span class=tid>—</span>'}</span></td><td class='n'>{html._e((t['recorded_at'] or '')[:10])}</td></tr>")
    links = (f"<div class='links'><a href='map-{html._e(g['app'])}.html'>화면 지도</a><a href='verification-{html._e(g['app'])}.html'>검증 보고서</a>"
             f"<a href='review-{html._e(g['app'])}.html'>승인 검토</a></div>")
    body = (f"<header class='head'><div class='eyebrow'>골든 관리</div><div class='stamp {stamp[0]}'>{stamp[1]}<small>{stamp[2]}</small></div>"
            f"<h1>{html._e(g['app'])}</h1><p class='lede'>이 앱의 골든 시나리오와 to-be 비교 현황입니다. 행을 누르면 시나리오 단계와 마지막 결과가 나옵니다.</p>"
            f"<div class='prov'><span><b>기준 폴더</b> golden/{html._e(g['app'])}</span><span><b>실행 기록</b> runs/{html._e(g['app'])} · {len(runs)}회</span></div></header>"
            f"{summary}{links}"
            f"<div class='tools'><input id='q' type='search' placeholder='시나리오 이름·ID로 찾기' aria-label='찾기'>"
            f"<select id='f' aria-label='결과로 거르기'><option value=''>모든 결과</option><option value='fail'>다름만</option><option value='pass'>같음만</option><option value='never'>비교 전만</option></select></div>"
            f"<div class='tbl'><table><thead><tr><th>시나리오</th><th>단계</th><th>확인 값</th><th>마지막 결과</th><th>이력 (오래된 → 최근)</th><th>기록일</th></tr></thead><tbody>{''.join(rows)}</tbody></table></div>"
            f"<div class='modal' id='modal' role='dialog' aria-label='시나리오 상세'><div class='box'></div></div>"
            f"<div class='lb' id='lb' role='dialog' aria-label='화면 크게 보기'><div><img src='' alt=''><div class='cap'></div></div></div>")
    data = json.dumps(g, ensure_ascii=False).replace("</", "<\\/")
    page = html._page(f"{g['app']} 골든 관리", body, script=f"<script type='application/json' id='d'>{data}</script>{JS}")
    return page.replace("</style>", CSS + "</style>", 1)


def write(d: Path, out: Path, tests_dir: Path | None = None) -> Path:
    g = build(d, tests_dir)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(render(g), encoding="utf-8")
    n_fail = sum(1 for t in g["tests"].values() if t["last"] and t["last"]["status"] != "pass")
    print(f"{out} · 시나리오 {len(g['tests'])}, 실행 {len(g['runs'])}회, 남은 실패 {n_fail}")
    return out
