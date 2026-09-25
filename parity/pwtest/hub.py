"""통합 화면 (parity ui): 사람이 보는 HTML 네 장(개요·승인 검토·화면 지도·검증 보고서)과 실행 이력을 한 화면에서 본다.

uv run parity ui [--golden golden] [--port 8790]      → http://127.0.0.1:8790/

서버는 산출물(golden/<app>/, runs/<app>/)만 읽고, 요청이 올 때 기존 생성기(catalog·review·map·report)로 화면을 만든다.
파일을 미리 만들어 둘 필요가 없고, 실행 원장이 실행마다 JUnit·스크린샷 사본을 남기므로 지난 실행의 지도·보고서도 다시 그릴 수 있다.
새로 판단하는 것은 없다. 승인은 여전히 터미널에서만 한다.

경로:
  /                                 통합 화면 (앱 목록, 탭, 실행 선택)
  /api/apps                         앱별 승인 상태·실행 수·마지막 결과
  /api/app/<app>                    시나리오, 실행 이력(지난 실행 대비 변화 포함), 결함 주입 결과
  POST /api/app/<app>/approve       웹 승인 {by, code, fingerprint}. 사람이 터미널에서 띄운 서버만 코드를 만들고 그 터미널에 찍는다 (에이전트 서버는 403)
  /page/<app>/catalog|review        개요(골든 관리), 승인 검토
  /page/<app>/map?run=<시각>         화면 지도 (실행을 고르면 그 실행의 다른 화면을 빨갛게)
  /page/<app>/report?run=<시각>      검증 보고서 (그 실행의 JUnit + 현재 승인본의 결함 주입 결과)
  /file?p=runs/…                    스크린샷 등 산출물 파일 (runs/ 와 골든 루트 아래만, 읽기 전용)
"""
from __future__ import annotations

import hmac
import json
import mimetypes
import secrets
import sys
import threading
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, quote, unquote, urlsplit

from . import catalog, html, ledger, oracle, report, review
from . import map as screen_map

PAGES = ("catalog", "review", "map", "report")


class Hub:
    def __init__(self, golden_root: Path, tests_root: Path = Path("e2e")):
        self.golden_root = golden_root
        self.tests_root = tests_root
        self._cache: dict[tuple, tuple[float, str]] = {}
        self._lock = threading.Lock()
        self.approval_code: str | None = None  # 웹 승인 일회용 코드. 사람이 터미널에서 띄웠을 때만 만들어지고 그 터미널에만 찍힌다
        self.attempts = 0

    # ---- 웹 승인 ----
    def new_code(self) -> str:
        self.approval_code = "-".join(secrets.token_hex(2).upper() for _ in range(2))  # 예: 3F9A-C21B
        self.attempts = 0
        return self.approval_code

    def approve(self, app: str, *, by: str, code: str, fingerprint: str, note: str = "") -> dict[str, Any]:
        """검토 화면의 승인 폼. 코드는 터미널에 찍힌 것과 같아야 하고(5번 틀리면 잠김), 지문은 검토 화면을 만들 때의 기준과 같아야 한다."""
        d = self._dir(app)
        if not self.approval_code:
            raise PermissionError("웹 승인이 꺼져 있습니다. 사람이 자기 터미널에서 `uv run parity ui`를 띄우면 터미널에 승인 코드가 찍힙니다. 또는 `uv run parity approve` (터미널)")
        if self.attempts >= 5:
            raise PermissionError("승인 코드를 5번 틀려 잠겼습니다. parity ui를 다시 띄우세요")
        if not hmac.compare_digest((code or "").strip().upper(), self.approval_code):
            self.attempts += 1
            raise PermissionError(f"승인 코드가 다릅니다 (남은 시도 {5 - self.attempts})")
        rec = oracle.approve_from_review(d, by, note, fingerprint)
        self.new_code()
        print(f"\n승인됨: {app} · {rec['approved_by']} · {rec['approved_at']}\n다음 승인 코드: {self.approval_code}")
        with self._lock:
            self._cache.clear()
        return {"ok": True, "approved_by": rec["approved_by"], "approved_at": rec["approved_at"]}

    # ---- 산출물 읽기 ----
    def app_dirs(self) -> list[Path]:
        if not self.golden_root.exists():
            return []
        return sorted(p for p in self.golden_root.iterdir() if p.is_dir() and any(p.glob("*.json")))

    def _dir(self, app: str) -> Path:
        d = self.golden_root / app
        if "/" in app or ".." in app or not d.is_dir():
            raise KeyError(app)
        return d

    def _sig(self, app: str) -> float:
        """골든이나 원장이 바뀌면 화면 캐시를 버린다."""
        latest = 0.0
        for root in (self.golden_root / app, ledger.run_dir(app)):
            if root.exists():
                latest = max([latest] + [p.stat().st_mtime for p in root.rglob("*") if p.is_file()])
        return latest

    def apps(self) -> list[dict[str, Any]]:
        out = []
        for d in self.app_dirs():
            st = oracle.status(d)
            runs = ledger.load_runs(d.name)
            last = runs[-1] if runs else None
            out.append({"app": d.name, "ok": st["ok"], "approved_by": st.get("approved_by"), "approved_at": st.get("approved_at"),
                        "tests": len(oracle.tests(d)), "runs": len(runs),
                        "last": {"finished": last["finished"], "target": last["target"], "totals": last["totals"]} if last else None})
        return out

    def app(self, app: str) -> dict[str, Any]:
        d = self._dir(app)
        st = oracle.status(d)
        docs = html.docstrings(self.tests_root / app)
        tests = [{"name": t["name"], "title": html._title(t["name"], docs), "assertions": len(t["assertions"]), "recorded_at": t["recorded_at"]}
                 for t in oracle.tests(d)]
        runs, previous = [], None
        for r in ledger.load_runs(app):
            cases = {}
            for name, c in r["cases"].items():
                shot = c.get("screenshot")
                cases[name] = {**c, "screenshot": f"/file?p={quote(shot)}" if shot and Path(shot).exists() else None}
            runs.append({"stamp": r["_stamp"], "started": r.get("started"), "finished": r["finished"], "target": r["target"],
                         "approved_at": r["oracle"].get("approved_at"), "approved_by": r["oracle"].get("approved_by"), "ok": r["oracle"].get("ok"),
                         "totals": r["totals"], "junit": bool(r.get("junit") and Path(r["junit"]).exists()),
                         "delta": ledger.compare_runs(r, previous), "cases": cases})
            previous = r
        current = ledger.mutation_for(app, st.get("approved_at"))
        muts = [{"file": m["_file"], "generated_at": m.get("generated_at"), "mode": m.get("mode"), "score": m.get("score"), "killed": m.get("killed"),
                 "total": m.get("total"), "errors": m.get("errors", 0), "approved_at": m.get("oracle_approved_at"),
                 "current": bool(current) and str(current) == m["_file"]} for m in ledger.load_mutations(app)]
        return {"app": app, "oracle": st, "tests": tests, "runs": runs, "mutations": muts,
                "kind_label": catalog.KIND_LABEL}

    # ---- 화면 만들기 ----
    def page(self, app: str, kind: str, run: str | None = None) -> str:
        d = self._dir(app)
        if kind not in PAGES:
            raise KeyError(kind)
        key, sig = (app, kind, run), self._sig(app)
        with self._lock:
            hit = self._cache.get(key)
            if hit and hit[0] == sig:
                return hit[1]
        tests_dir = self.tests_root / app
        junit = ledger.run_dir(app) / run / "junit.xml" if run else None
        if run and (junit is None or not junit.exists()):
            body = (f"<header class='head'><div class='eyebrow'>{'화면 지도' if kind == 'map' else '검증 보고서'}</div><h1>{html._e(app)}</h1>"
                    f"<p class='lede'>실행 {html._e(run)}의 JUnit 사본이 없습니다. 이 실행은 원장에 JUnit을 남기기 전 것이거나, "
                    f"<code>--junitxml</code> 없이 실행됐습니다. 다시 비교하면 지도와 보고서가 나옵니다.</p></header>")
            return html._page(f"{app} {kind}", body)
        if kind == "catalog":
            page = catalog.render(catalog.build(d, tests_dir), embed=True)
        elif kind == "review":
            page = review.render(review.build(d, tests_dir), approve=self.approval_code is not None)
        elif kind == "map":
            page = screen_map.render(screen_map.build(d, junit, tests_dir))
        else:
            st = oracle.status(d)
            mut = ledger.mutation_for(app, st.get("approved_at"))
            b = report.build(oracle_dir=d, junits=[junit] if junit else [], mutations=[mut] if mut else [])
            page = report.render_html(d, b, tests_dir)
        with self._lock:
            self._cache[key] = (sig, page)
        return page

    def file(self, path: str) -> Path | None:
        """runs/ 와 골든 루트 아래의 파일만 준다. 상대 경로는 작업 디렉터리 기준."""
        if not path:
            return None
        full = Path(path).resolve()
        allowed = (ledger.RUNS.resolve(), self.golden_root.resolve())
        if not any(full.is_relative_to(a) for a in allowed) or not full.is_file():
            return None
        return full


class Handler(BaseHTTPRequestHandler):
    hub: Hub

    def log_message(self, fmt, *args):  # 조용히
        pass

    def _send(self, body: bytes, ctype: str = "text/html; charset=utf-8", code: int = 200) -> None:
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _json(self, data: Any) -> None:
        self._send(json.dumps(data, ensure_ascii=False).encode("utf-8"), "application/json; charset=utf-8")

    def do_GET(self):  # noqa: N802
        u = urlsplit(self.path)
        parts = [unquote(x) for x in u.path.strip("/").split("/") if x]
        q = parse_qs(u.query)
        try:
            if not parts:
                return self._send(SHELL.encode("utf-8"))
            if parts == ["api", "apps"]:
                return self._json(self.hub.apps())
            if len(parts) == 3 and parts[:2] == ["api", "app"]:
                return self._json(self.hub.app(parts[2]))
            if len(parts) == 3 and parts[0] == "page":
                return self._send(self.hub.page(parts[1], parts[2], (q.get("run") or [None])[0]).encode("utf-8"))
            if parts == ["file"]:
                p = self.hub.file((q.get("p") or [""])[0])
                if p is None:
                    return self._send(b"not found", "text/plain", 404)
                return self._send(p.read_bytes(), mimetypes.guess_type(p.name)[0] or "application/octet-stream")
        except KeyError as e:
            return self._send(f"unknown: {e}".encode(), "text/plain; charset=utf-8", 404)
        return self._send(b"not found", "text/plain", 404)

    def do_POST(self):  # noqa: N802
        parts = [unquote(x) for x in urlsplit(self.path).path.strip("/").split("/") if x]
        if len(parts) != 4 or parts[:2] != ["api", "app"] or parts[3] != "approve":
            return self._send(b"not found", "text/plain", 404)
        try:
            body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0)) or 0) or b"{}")
            res = self.hub.approve(parts[2], by=str(body.get("by", "")), code=str(body.get("code", "")),
                                   fingerprint=str(body.get("fingerprint", "")), note=str(body.get("note", "")))
            return self._json(res)
        except PermissionError as e:
            return self._send(json.dumps({"error": str(e)}, ensure_ascii=False).encode("utf-8"), "application/json; charset=utf-8", 403)
        except (ValueError, KeyError) as e:
            return self._send(json.dumps({"error": str(e)}, ensure_ascii=False).encode("utf-8"), "application/json; charset=utf-8", 409)


def serve(golden_root: Path, *, port: int = 8790, tests_root: Path = Path("e2e"), open_browser: bool = True) -> None:
    hub = Hub(golden_root, tests_root)
    if not hub.app_dirs():
        raise SystemExit(f"{golden_root}/ 아래에 기록된 골든이 없습니다 (pytest e2e/<app> --base-url <as-is> --record {golden_root}/<app>)")
    handler = type("HubHandler", (Handler,), {"hub": hub})
    srv = ThreadingHTTPServer(("127.0.0.1", port), handler)
    url = f"http://127.0.0.1:{srv.server_address[1]}/"
    print(f"parity ui · {url}  (앱 {len(hub.app_dirs())}개: {', '.join(d.name for d in hub.app_dirs())}) — Ctrl+C로 종료")
    # 웹 승인은 사람이 터미널에서 띄웠을 때만. 에이전트 도구는 터미널이 없으므로 코드가 만들어지지 않고, 승인 요청은 403이다
    if sys.stdin.isatty() and sys.stdout.isatty():
        print(f"승인 코드: {hub.new_code()}   (검토 화면에서 모든 시나리오를 확인한 뒤 이름과 함께 입력. 이 터미널에만 보인다)")
    else:
        print("웹 승인 꺼짐: 터미널에서 띄운 것이 아닙니다. 승인은 사람이 자기 터미널에서 `parity ui` 또는 `parity approve`로.")
    if open_browser:
        threading.Timer(0.4, lambda: webbrowser.open(url)).start()
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        srv.server_close()


HUB_CSS = """
html,body{height:100%}
body.hub{display:flex;flex-direction:column;overflow:hidden}
.top{display:flex;align-items:center;gap:18px;padding:0 20px;height:54px;border-bottom:1px solid var(--line);background:var(--surface);flex:none}
.brand{font-weight:700;font-size:16px;letter-spacing:-.01em}.brand span{font-weight:500;color:var(--faint);margin-left:6px;font-size:13px}
.apps{display:flex;gap:4px;flex:1;overflow:auto}
.apps button{border:1px solid transparent;background:none;font:inherit;font-size:13.5px;font-weight:500;padding:5px 11px;border-radius:999px;cursor:pointer;color:var(--muted);display:flex;gap:7px;align-items:center;white-space:nowrap}
.apps button.on{background:var(--accent-soft);color:var(--accent);border-color:transparent}
.apps button i{width:8px;height:8px;border-radius:50%;background:var(--faint)}.apps button i.ok{background:var(--ok)}.apps button i.bad{background:var(--bad)}.apps button i.warn{background:var(--warn)}
.runsel{display:flex;align-items:center;gap:8px;font-size:13px;color:var(--muted)}
.runsel select{font:inherit;font-size:13px;padding:5px 8px;border:1px solid var(--line);border-radius:6px;background:var(--surface);color:var(--ink);max-width:380px}
.frame{display:flex;flex:1;min-height:0}
.side{width:176px;flex:none;border-right:1px solid var(--line);background:var(--surface);padding:14px 10px;display:flex;flex-direction:column;gap:2px}
.side a{display:flex;align-items:center;gap:9px;padding:8px 12px;border-radius:8px;font-size:14px;color:var(--ink);text-decoration:none;cursor:pointer}
.side a:hover{background:var(--sunk)}.side a.on{background:var(--accent-soft);color:var(--accent);font-weight:600}
.side a small{margin-left:auto;font-size:11.5px;color:var(--faint);font-family:var(--mono)}
.side .sep{margin:10px 12px 6px;font-size:11px;font-weight:600;letter-spacing:.08em;color:var(--faint)}
#view{flex:1;min-width:0;position:relative;background:var(--bg)}
#view iframe{width:100%;height:100%;border:0;display:block;background:var(--bg)}
#view main{height:100%;overflow:auto;max-width:none;padding-block:28px 80px;gap:28px}
.hist h2{font-size:17px;font-weight:600}
.strip{display:grid;grid-template-columns:repeat(auto-fit,minmax(170px,1fr));gap:12px}
.strip .card{background:var(--surface);border:1px solid var(--line);border-radius:10px;padding:12px 16px}
.strip .k{font-size:12px;color:var(--muted)}.strip .v{font:600 22px/1.25 var(--mono);margin-top:4px}.strip .v small{display:block;font:12px/1.4 var(--sans);color:var(--muted);margin-top:2px}
.strip .card.ok .v{color:var(--ok)}.strip .card.bad .v{color:var(--bad)}.strip .card.warn .v{color:var(--warn)}
.tbl{background:var(--surface);border:1px solid var(--line);border-radius:10px;overflow:auto}
.tbl table{width:100%;border-collapse:collapse;font-size:13.5px}
.tbl th{text-align:left;font-weight:600;font-size:12px;color:var(--muted);padding:9px 12px;border-bottom:1px solid var(--line);background:var(--sunk);white-space:nowrap;letter-spacing:0}
.tbl td{padding:9px 12px;border-bottom:1px solid var(--line);vertical-align:middle}.tbl tr:last-child td{border-bottom:0}
.tbl tr.rrow{cursor:pointer;display:table-row}.tbl tr.rrow:hover td{background:var(--sunk)}.tbl tr.rrow.on td{background:var(--accent-soft)}
.mono{font-family:var(--mono);font-variant-numeric:tabular-nums}
.pill{display:inline-block;padding:2px 9px;border-radius:999px;font-size:12px;font-weight:600;white-space:nowrap}
.pill.ok{background:var(--ok-soft);color:var(--ok)}.pill.bad{background:var(--bad-soft);color:var(--bad)}.pill.warn{background:var(--warn-soft);color:var(--warn)}.pill.none{background:var(--sunk);color:var(--muted)}
.chip{display:inline-block;font-size:12px;padding:1px 7px;border-radius:5px;margin-right:4px;font-family:var(--mono)}
.chip.up{background:var(--ok-soft);color:var(--ok)}.chip.down{background:var(--bad-soft);color:var(--bad)}.chip.same{background:var(--sunk);color:var(--muted)}
.matrix td.c{text-align:center;padding:6px 4px}.matrix td.c i{display:inline-block;width:14px;height:14px;border-radius:4px;background:var(--sunk)}
.matrix td.c i.p{background:var(--ok)}.matrix td.c i.f{background:var(--bad)}.matrix th.run{cursor:pointer;text-align:center;font-family:var(--mono);font-weight:500}
.matrix th.run.on{color:var(--accent);background:var(--accent-soft)}
.matrix td.t b{display:block;font-weight:600}.matrix td.t small{color:var(--faint);font-family:var(--mono);font-size:11.5px}
.case{background:var(--surface);border:1px solid var(--line);border-radius:10px;padding:14px 16px;display:grid;grid-template-columns:1fr auto;gap:8px 16px}
.case.fail{border-left:4px solid var(--bad)}.case.pass{border-left:4px solid var(--ok)}
.case .ttl{font-weight:600}.case .ttl small{display:block;font-weight:400;color:var(--faint);font-family:var(--mono);font-size:11.5px}
.case .sum{grid-column:1/-1;font-family:var(--mono);font-size:12.5px;color:var(--muted);white-space:pre-wrap}
.case table{grid-column:1/-1;border-collapse:collapse;font-size:13px}.case td,.case th{padding:5px 10px;border-bottom:1px solid var(--line);text-align:left}
.case th{font-size:11.5px;color:var(--muted);font-weight:600}.case td.was{font-family:var(--mono)}.case td.now{font-family:var(--mono);color:var(--bad);font-weight:600}
.case img{grid-column:1/-1;max-width:420px;max-height:220px;object-fit:cover;object-position:top left;border:1px solid var(--line);border-radius:6px;cursor:zoom-in}
.actions{display:flex;gap:8px}.actions button{font:inherit;font-size:12.5px;padding:4px 10px;border:1px solid var(--line);border-radius:6px;background:var(--surface);color:var(--ink);cursor:pointer}
.actions button:hover{border-color:var(--accent);color:var(--accent)}
.notice{background:var(--warn-soft);color:var(--warn);border-radius:8px;padding:10px 14px;font-size:13.5px}
.empty{color:var(--muted);padding:40px;text-align:center}
@media (max-width:760px){.side{width:56px;padding:10px 6px}.side a span,.side a small,.side .sep{display:none}.runsel label{display:none}}
"""

HUB_JS = r"""
const $ = (s, el=document) => el.querySelector(s);
const esc = s => String(s ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const TABS = [['overview','개요'],['history','이력'],['review','승인 검토'],['map','화면 지도'],['report','검증 보고서']];
const KIND = {golden_diff:'as-is와 다름', assert:'확인 값 실패', drift:'기대값 변경', error:'실행 못 함', same:'같음'};
let apps = [], state = {app:null, tab:'overview', run:null}, data = null;

function parseHash(){ const [app, tab, run] = location.hash.replace(/^#/, '').split('/').map(decodeURIComponent); return {app: app||null, tab: tab||'overview', run: run||null}; }
function setHash(){ location.hash = [state.app, state.tab, state.run].filter(x => x).map(encodeURIComponent).join('/'); }
function runLabel(r){ return `${r.finished.slice(5,16)} · ${r.target.replace(/^https?:\/\//,'')} · ${r.totals.fail ? r.totals.fail + ' 다름' : '모두 같음'}`; }

async function loadApps(){
  apps = await (await fetch('/api/apps')).json();
  const nav = $('#apps'); nav.innerHTML = '';
  for (const a of apps){
    const b = document.createElement('button');
    const dot = !a.ok ? 'warn' : (a.last ? (a.last.totals.fail ? 'bad' : 'ok') : '');
    b.innerHTML = `<i class="${dot}"></i>${esc(a.app)}<small>${a.runs}회</small>`;
    b.onclick = () => { state.app = a.app; state.run = null; setHash(); };
    b.className = a.app === state.app ? 'on' : '';
    nav.appendChild(b);
  }
}

async function loadApp(){
  data = await (await fetch('/api/app/' + encodeURIComponent(state.app))).json();
  if (!state.run || !data.runs.some(r => r.stamp === state.run)) state.run = data.runs.length ? data.runs[data.runs.length-1].stamp : null;
  const sel = $('#run'); sel.innerHTML = '';
  for (const r of [...data.runs].reverse()){ const o = document.createElement('option'); o.value = r.stamp; o.textContent = runLabel(r); sel.appendChild(o); }
  sel.value = state.run || ''; sel.disabled = !data.runs.length;
  $('#tabs').innerHTML = `<div class="sep">${esc(state.app)}</div>` + TABS.map(([k, l]) => {
    const n = k === 'history' ? data.runs.length : (k === 'overview' ? data.tests.length : '');
    return `<a data-tab="${k}" class="${k === state.tab ? 'on' : ''}"><span>${l}</span>${n !== '' ? `<small>${n}</small>` : ''}</a>`;
  }).join('');
  for (const a of $('#tabs').querySelectorAll('a')) a.onclick = () => { state.tab = a.dataset.tab; setHash(); };
}

function render(){
  const v = $('#view');
  if (!state.app){ v.innerHTML = '<div class="empty">왼쪽 위에서 앱을 고르세요.</div>'; return; }
  const q = state.run ? '?run=' + encodeURIComponent(state.run) : '';
  const page = {overview:'catalog', review:'review', map:'map', report:'report'}[state.tab];
  if (page){ v.innerHTML = `<iframe title="${state.tab}" src="/page/${encodeURIComponent(state.app)}/${page}${q}"></iframe>`; return; }
  renderHistory(v);
}

function renderHistory(v){
  const d = data, st = d.oracle, runs = d.runs, cur = runs.find(r => r.stamp === state.run);
  const mut = d.mutations.find(m => m.current);
  const last = runs[runs.length-1];
  const stale = last && st.approved_at && last.approved_at !== st.approved_at;
  const cards = [
    ['승인', st.ok ? `${esc(st.approved_by)}<small>${esc((st.approved_at||'').slice(0,16))}</small>` : '없음<small>승인 필요</small>', st.ok ? 'ok' : 'warn'],
    ['비교 실행', `${runs.length}회<small>${last ? esc(last.finished.slice(0,16)) : '아직 없음'}</small>`, ''],
    ['마지막 결과', last ? `${last.totals.pass} 같음 · ${last.totals.fail} 다름` : '—', last ? (last.totals.fail ? 'bad' : 'ok') : ''],
    ['결함 탐지', mut ? `${Math.round(mut.score*100)}%<small>${mut.killed}/${mut.total} · ${esc((mut.generated_at||'').slice(0,16))}</small>` : '없음<small>현재 승인본으로 측정한 결과 없음</small>', mut ? (mut.score >= .8 ? 'ok' : 'bad') : 'warn'],
  ];
  let h = `<main class="hist"><div class="strip">${cards.map(([k,val,c]) => `<div class="card ${c}"><div class="k">${k}</div><div class="v">${val}</div></div>`).join('')}</div>`;
  if (stale) h += `<div class="notice"><b>주의</b> 마지막 비교는 이전 승인본(${esc(last.approved_at||'없음')})으로 실행됐습니다. 현재 승인본으로 다시 비교하세요.</div>`;
  if (!runs.length){ h += `<div class="empty">to-be 비교 실행 기록이 없습니다.<br><code>uv run pytest e2e/${esc(d.app)} --base-url &lt;to-be&gt; --compare golden/${esc(d.app)} --junitxml reports/junit-${esc(d.app)}.xml</code></div></main>`; v.innerHTML = h; return; }

  h += `<section><h2>실행 이력 <small class="mono" style="color:var(--faint);font-weight:400">오래된 → 최근</small></h2><div class="tbl"><table><thead><tr><th>#</th><th>끝난 시각</th><th>대상</th><th>승인본</th><th>결과</th><th>지난 실행 대비</th><th></th></tr></thead><tbody>`;
  runs.forEach((r, i) => {
    const dl = r.delta, chips = [];
    if (i > 0){
      if (dl.newly_passing.length) chips.push(`<span class="chip up">+${dl.newly_passing.length} 통과로</span>`);
      if (dl.newly_failing.length) chips.push(`<span class="chip down">−${dl.newly_failing.length} 새로 실패</span>`);
      if (dl.still_failing.length) chips.push(`<span class="chip same">${dl.still_failing.length} 계속 실패</span>`);
      if (dl.new_tests.length) chips.push(`<span class="chip same">새 테스트 ${dl.new_tests.length}</span>`);
      if (!chips.length) chips.push('<span class="chip same">변화 없음</span>');
    } else chips.push('<span class="chip same">첫 실행</span>');
    const ap = r.approved_at === st.approved_at ? `<span class="pill ok">현재</span>` : `<span class="pill warn" title="${esc(r.approved_at||'승인 없음')}">이전 승인본</span>`;
    h += `<tr class="rrow ${r.stamp === state.run ? 'on' : ''}" data-run="${r.stamp}"><td class="mono">${i+1}</td><td class="mono">${esc(r.finished)}</td><td class="mono">${esc(r.target)}</td><td>${ap}</td>`
       + `<td>${r.totals.fail ? `<span class="pill bad">${r.totals.fail} 다름</span>` : '<span class="pill ok">모두 같음</span>'} <span class="mono" style="color:var(--faint);font-size:12px">/ ${r.totals.pass + r.totals.fail}</span></td>`
       + `<td>${chips.join('')}</td><td><div class="actions">${r.junit ? `<button data-go="map" data-run="${r.stamp}">지도</button><button data-go="report" data-run="${r.stamp}">보고서</button>` : '<span style="color:var(--faint);font-size:12px">JUnit 없음</span>'}</div></td></tr>`;
  });
  h += `</tbody></table></div></section>`;

  h += `<section><h2>시나리오 × 실행</h2><div class="tbl matrix"><table><thead><tr><th>시나리오</th>${runs.map((r,i) => `<th class="run ${r.stamp === state.run ? 'on' : ''}" data-run="${r.stamp}" title="${esc(r.finished)} · ${esc(r.target)}">${i+1}</th>`).join('')}<th>마지막</th></tr></thead><tbody>`;
  for (const t of d.tests){
    const cells = runs.map(r => { const c = r.cases[t.name]; return `<td class="c" title="${esc(r.finished)}${c ? ' · ' + esc(KIND[c.kind]||c.kind) : ''}"><i class="${c ? (c.status === 'pass' ? 'p' : 'f') : ''}"></i></td>`; }).join('');
    const lc = last.cases[t.name];
    h += `<tr><td class="t"><b>${esc(t.title)}</b><small>${esc(t.name)}</small></td>${cells}<td>${lc ? (lc.status === 'pass' ? '<span class="pill ok">같음</span>' : `<span class="pill bad">${esc(KIND[lc.kind]||'실패')}</span>`) : '<span class="pill none">비교 전</span>'}</td></tr>`;
  }
  h += `</tbody></table></div></section>`;

  if (cur){
    const names = Object.keys(cur.cases).sort((a,b) => (cur.cases[a].status === 'pass') - (cur.cases[b].status === 'pass') || a.localeCompare(b));
    h += `<section><h2>실행 ${runs.indexOf(cur)+1} 상세 <small class="mono" style="color:var(--faint);font-weight:400">${esc(cur.finished)} · ${esc(cur.target)}</small></h2><div style="display:flex;flex-direction:column;gap:10px">`;
    for (const n of names){
      const c = cur.cases[n], t = d.tests.find(x => x.name === n) || {title: n};
      const rows = (c.rows||[]).map(([w,a,b]) => `<tr><td>${esc(w)}</td><td class="was">${esc(a)}</td><td class="now">${esc(b)}</td></tr>`).join('');
      h += `<div class="case ${c.status}"><div class="ttl">${esc(t.title)}<small>${esc(n)}</small></div><div>${c.status === 'pass' ? '<span class="pill ok">같음</span>' : `<span class="pill bad">${esc(KIND[c.kind]||c.kind)}</span>`}</div>`
         + (c.summary ? `<div class="sum">${esc(c.summary)}</div>` : '')
         + (rows ? `<table><tr><th>무엇이</th><th>as-is 기준</th><th>to-be</th></tr>${rows}</table>` : '')
         + (c.screenshot ? `<img src="${c.screenshot}" alt="실패 순간 화면" onclick="window.open(this.src)">` : '') + `</div>`;
    }
    h += `</div></section>`;
  }

  if (d.mutations.length){
    h += `<section><h2>결함 탐지 측정</h2><div class="tbl"><table><thead><tr><th>측정 시각</th><th>탐지율</th><th>오류</th><th>승인본</th></tr></thead><tbody>`
       + d.mutations.map(m => `<tr><td class="mono">${esc(m.generated_at)}</td><td><b class="mono">${m.score == null ? '—' : Math.round(m.score*100) + '%'}</b> <span class="mono" style="color:var(--faint);font-size:12px">${m.killed}/${m.total}</span></td><td class="mono">${m.errors}</td><td>${m.current ? '<span class="pill ok">현재</span>' : `<span class="pill warn" title="${esc(m.approved_at||'')}">이전 승인본</span>`}</td></tr>`).join('')
       + `</tbody></table></div></section>`;
  }
  h += `</main>`;
  v.innerHTML = h;
  for (const el of v.querySelectorAll('[data-run]')){
    el.addEventListener('click', e => { const go = e.target.dataset.go; state.run = el.dataset.run; if (go) state.tab = go; setHash(); });
  }
}

async function route(){
  const h = parseHash();
  const changedApp = h.app !== state.app;
  state = {app: h.app || (apps[0] && apps[0].app) || null, tab: h.tab, run: h.run};
  for (const b of $('#apps').children) b.className = b.textContent.startsWith(state.app) ? 'on' : '';
  if (state.app && (changedApp || !data)) await loadApp(); else if (state.app) await loadApp();
  for (const a of $('#tabs').querySelectorAll('a')) a.className = a.dataset.tab === state.tab ? 'on' : '';
  render();
  if (!location.hash && state.app) setHash();
}
$('#run').addEventListener('change', e => { state.run = e.target.value || null; setHash(); });
window.addEventListener('message', e => { if (e.data && e.data.parity === 'approved') loadApps().then(route); });  // 검토 화면에서 승인되면 상태 점·카드 갱신
window.addEventListener('hashchange', route);
loadApps().then(route);
"""

SHELL = (f"<!doctype html><html lang='ko'><head><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1,viewport-fit=cover'>"
         f"<title>parity</title>{html.FONTS}<style>{html.CSS}{HUB_CSS}</style></head><body class='hub'>"
         "<header class='top'><div class='brand'>parity<span>통합 화면</span></div><nav class='apps' id='apps' aria-label='앱'></nav>"
         "<div class='runsel'><label for='run'>실행</label><select id='run' aria-label='실행 선택'></select></div></header>"
         "<div class='frame'><nav class='side' id='tabs' aria-label='화면'></nav><div id='view'></div></div>"
         f"<script>{HUB_JS}</script></body></html>")
