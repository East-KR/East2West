"""통합 화면 (parity ui): 프로젝트 목록에서 시작해, 프로젝트마다 사람이 보는 HTML 네 장(개요·승인 검토·화면 지도·검증 보고서)과 실행 이력을 한 화면에서 본다.

uv run parity ui [--golden golden] [--port 8790]      → http://127.0.0.1:8790/

첫 화면은 프로젝트 목록(parity.json)이다. "프로젝트 추가"를 누르면 as-is와 to-be 소스 위치를 파일 시스템에서 고르는 창이 뜨고,
저장하면 등록부에 적히고 e2e/<app>/ 자리가 생긴다. 등록부에 없어도 golden/<app> 이나 e2e/<app> 이 있으면 목록에 나온다(경로 미설정).
서버는 산출물(golden/<app>/, runs/<app>/)만 읽고, 요청이 올 때 기존 생성기(catalog·review·map·report)로 화면을 만든다.
실행 원장이 실행마다 JUnit·스크린샷 사본을 남기므로 지난 실행의 지도·보고서도 다시 그릴 수 있다. 새로 판단하는 것은 없다.

경로:
  /                                 통합 화면 (프로젝트 목록 → 프로젝트 화면: 탭, 실행 선택)
  /api/apps                         프로젝트별 등록 정보·승인 상태·실행 수·마지막 결과
  /api/app/<app>                    시나리오, 실행 이력(지난 실행 대비 변화 포함), 결함 주입 결과
  POST /api/app/<app>/approve       웹 승인 {by, code, fingerprint}. 사람이 터미널에서 띄운 서버만 코드를 만들고 그 터미널에 찍는다 (에이전트 서버는 403)
  GET|POST /api/app/<app>/init      화면 지도 초기화: 골든이 없는 프로젝트를 as-is에서 탐색(crawl) → 시나리오 초안 → 기록. POST {depth}로 시작, GET으로 진행 (승인은 하지 않는다)
  POST /api/projects                프로젝트 추가 {name, asis:{src,url}, tobe:{src,url}, note}
  POST /api/projects/<app>          프로젝트 설정 변경 (같은 본문)
  POST /api/projects/<app>/delete   등록 해제 (산출물은 남긴다)
  /api/fs?path=                     폴더 고르기용 하위 폴더 목록 (작업 디렉터리·홈 아래만)
  /page/<app>/catalog|review        개요(골든 관리), 승인 검토
  /page/<app>/map?run=<시각>         화면 지도 (실행을 고르면 그 실행의 다른 화면을 빨갛게)
  /page/<app>/report?run=<시각>      검증 보고서 (그 실행의 JUnit + 현재 승인본의 결함 주입 결과)
  /file?p=runs/…                    스크린샷 등 산출물 파일 (runs/ 와 골든 루트 아래만, 읽기 전용)
"""
from __future__ import annotations

import hmac
import json
import mimetypes
import os
import secrets
import shutil
import subprocess
import sys
import threading
import time
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, quote, unquote, urlsplit

from . import catalog, html, ledger, oracle, projects, report, review
from . import map as screen_map

PAGES = ("catalog", "review", "map", "report")


class Hub:
    def __init__(self, golden_root: Path, tests_root: Path = Path("e2e"), projects_file: Path = projects.FILE):
        self.golden_root = golden_root
        self.tests_root = tests_root
        self.projects_file = projects_file
        self._cache: dict[tuple, tuple[float, str]] = {}
        self._lock = threading.Lock()
        self.approval_code: str | None = None  # 웹 승인 일회용 코드. 사람이 터미널에서 띄웠을 때만 만들어지고 그 터미널에만 찍힌다
        self.attempts = 0
        self.jobs: dict[str, dict[str, Any]] = {}  # 화면 지도 초기화 작업 (앱별 하나)

    # ---- 웹 승인 ----
    def new_code(self) -> str:
        self.approval_code = "-".join(secrets.token_hex(2).upper() for _ in range(2))  # 예: 3F9A-C21B
        self.attempts = 0
        return self.approval_code

    def approve(self, app: str, *, by: str, code: str, fingerprint: str, note: str = "") -> dict[str, Any]:
        """검토 화면의 승인 폼. 코드는 터미널에 찍힌 것과 같아야 하고(5번 틀리면 잠김), 지문은 검토 화면을 만들 때의 기준과 같아야 한다."""
        d = self._golden(app)
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

    # ---- 프로젝트 ----
    def names(self) -> list[str]:
        return projects.names(path=self.projects_file, golden_root=self.golden_root, tests_root=self.tests_root)

    def app_dirs(self) -> list[Path]:
        """골든이 기록된 앱 폴더들."""
        if not self.golden_root.exists():
            return []
        return sorted(p for p in self.golden_root.iterdir() if p.is_dir() and any(p.glob("*.json")))

    def _check(self, app: str) -> str:
        if not projects.NAME_RE.match(app or "") or app not in self.names():
            raise KeyError(app)
        return app

    def _golden(self, app: str) -> Path:
        """골든 폴더. 없으면 KeyError (아직 기록 전)."""
        d = self.golden_root / self._check(app)
        if not d.is_dir():
            raise KeyError(app)
        return d

    def _scenarios(self, app: str) -> int:
        d = self.tests_root / app
        return len(list(d.glob("test_*.py"))) if d.is_dir() else 0

    def add_project(self, name: str, spec: dict[str, Any]) -> dict[str, Any]:
        rec = projects.add(name, spec, path=self.projects_file, tests_root=self.tests_root)
        return {"ok": True, "app": name, **rec}

    def update_project(self, name: str, spec: dict[str, Any]) -> dict[str, Any]:
        self._check(name)
        rec = projects.update(name, spec, path=self.projects_file)
        return {"ok": True, "app": name, **rec}

    def remove_project(self, name: str) -> dict[str, Any]:
        self._check(name)
        projects.remove(name, path=self.projects_file)
        return {"ok": True, "app": name, "kept": [str(p) for p in (self.tests_root / name, self.golden_root / name, ledger.run_dir(name)) if p.exists()]}

    # ---- 화면 지도 초기화: 골든이 없는 프로젝트를 as-is에서 탐색·기록해 지도가 그려지는 상태로 ----
    def _has_golden(self, app: str) -> bool:
        d = self.golden_root / app
        return d.is_dir() and any(d.glob("*.json"))

    def init_plan(self, app: str, depth: int = 3) -> list[dict[str, Any]]:
        """초기화 단계 목록 (실행하지 않는다). 시나리오가 없으면 탐색 → 초안 옮기기 → 기록, 있으면 기록만. 승인은 여기 없다 — 사람이 한다."""
        self._check(app)
        if self._has_golden(app):
            raise ValueError("골든이 이미 있어 지도를 그릴 수 있습니다. 다시 기록하려면 터미널에서 (골든은 사람 승인물이라 여기서 덮어쓰지 않습니다)")
        spec = projects.load(self.projects_file).get(app) or {}
        url = (spec.get("asis") or {}).get("url") or ""
        if not url.startswith(("http://", "https://")):
            raise ValueError("as-is 실행 주소가 없습니다. 프로젝트 설정에서 적으세요")
        depth = max(1, min(int(depth or 3), 5))
        out = Path("crawl") / app
        steps: list[dict[str, Any]] = []
        if not self._scenarios(app):
            steps.append({"step": "crawl", "label": f"as-is 화면 탐색 (깊이 {depth}) → {out}",
                          "cmd": [sys.executable, "-m", "parity.cli", "crawl", url, "--out", str(out), "--depth", str(depth)]})
            steps.append({"step": "tests", "label": f"시나리오 초안을 {self.tests_root / app}/ 로", "copy": [str(out / "test_crawl.py"), str(self.tests_root / app / "test_crawl.py")]})
        else:
            steps.append({"step": "crawl", "label": f"탐색 건너뜀 — {self.tests_root / app}/ 에 시나리오 {self._scenarios(app)}개", "skip": True})
        steps.append({"step": "record", "label": f"as-is({url})에서 골든 기록 → {self.golden_root / app}",
                      "cmd": [sys.executable, "-m", "pytest", str(self.tests_root / app), "--base-url", url, "--record", str(self.golden_root / app), "-q", "-p", "no:cacheprovider"]})
        return steps

    def init_status(self, app: str) -> dict[str, Any]:
        job = self.jobs.get(app)
        if not job:
            return {"running": False}
        return {k: v for k, v in job.items() if k != "log"} | {"log": job["log"][-80:]}

    def init_project(self, app: str, depth: int = 3) -> dict[str, Any]:
        """초기화를 백그라운드로 시작한다. 진행은 init_status로 본다."""
        job = self.jobs.get(app)
        if job and job["running"]:
            raise ValueError("이미 실행 중입니다")
        steps = self.init_plan(app, depth)
        job = {"running": True, "ok": False, "error": "", "steps": [{"step": s["step"], "label": s["label"], "state": "skip" if s.get("skip") else "wait"} for s in steps],
               "log": [], "started": time.strftime("%Y-%m-%d %H:%M:%S"), "finished": None}
        self.jobs[app] = job

        def log(line: str) -> None:
            job["log"].append(line.rstrip("\n"))
            del job["log"][:-400]

        def run() -> None:
            try:
                for i, s in enumerate(steps):
                    st = job["steps"][i]
                    if s.get("skip"):
                        continue
                    st["state"] = "run"
                    log(f"== {s['label']}")
                    if "copy" in s:
                        src, dst = (Path(p) for p in s["copy"])
                        if not src.exists():
                            raise RuntimeError(f"탐색이 시나리오 초안을 만들지 못했습니다: {src}")
                        dst.parent.mkdir(parents=True, exist_ok=True)
                        shutil.copy(src, dst)
                        log(f"{src} → {dst}")
                    else:
                        log("$ " + " ".join(s["cmd"]))
                        proc = subprocess.Popen(s["cmd"], stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, env={**os.environ, "PYTHONUNBUFFERED": "1"})
                        for line in proc.stdout or []:
                            log(line)
                        if proc.wait() != 0:
                            raise RuntimeError(f"{s['step']} 실패 (exit {proc.returncode})")
                    st["state"] = "done"
                if not self._has_golden(app):
                    raise RuntimeError("기록이 끝났지만 골든 파일이 없습니다 (시나리오가 하나도 통과하지 못했는지 로그를 보세요)")
                job["ok"] = True
                log("== 끝. 화면 지도를 그립니다. 승인은 승인 검토 탭에서 (사람)")
            except Exception as e:  # noqa: BLE001
                job["error"] = str(e)
                for st in job["steps"]:
                    if st["state"] == "run":
                        st["state"] = "fail"
                log(f"!! {e}")
            finally:
                job["running"] = False
                job["finished"] = time.strftime("%Y-%m-%d %H:%M:%S")
                with self._lock:
                    self._cache.clear()

        threading.Thread(target=run, daemon=True).start()
        return self.init_status(app)

    # ---- 산출물 읽기 ----
    def _sig(self, app: str) -> float:
        """골든이나 원장이 바뀌면 화면 캐시를 버린다."""
        latest = 0.0
        for root in (self.golden_root / app, ledger.run_dir(app)):
            if root.exists():
                latest = max([latest] + [p.stat().st_mtime for p in root.rglob("*") if p.is_file()])
        return latest

    def apps(self) -> list[dict[str, Any]]:
        reg = projects.load(self.projects_file)
        out = []
        for name in self.names():
            d = self.golden_root / name
            has_golden = d.is_dir() and any(d.glob("*.json"))
            st = oracle.status(d) if has_golden else {"ok": False}
            runs = ledger.load_runs(name)
            last = runs[-1] if runs else None
            spec = reg.get(name)
            out.append({"app": name, "registered": spec is not None,
                        "asis": (spec or {}).get("asis"), "tobe": (spec or {}).get("tobe"), "note": (spec or {}).get("note", ""),
                        "created_at": (spec or {}).get("created_at"),
                        "scenarios": self._scenarios(name), "golden": has_golden,
                        "ok": st["ok"], "approved_by": st.get("approved_by"), "approved_at": st.get("approved_at"),
                        "tests": len(oracle.tests(d)) if has_golden else 0, "runs": len(runs),
                        "last": {"finished": last["finished"], "target": last["target"], "totals": last["totals"]} if last else None})
        return out

    def app(self, app: str) -> dict[str, Any]:
        self._check(app)
        d = self.golden_root / app
        has_golden = d.is_dir() and any(d.glob("*.json"))
        st = oracle.status(d) if has_golden else {"ok": False, "problems": ["골든이 아직 없습니다"]}
        docs = html.docstrings(self.tests_root / app)
        tests = [{"name": t["name"], "title": html._title(t["name"], docs), "assertions": len(t["assertions"]), "recorded_at": t["recorded_at"]}
                 for t in (oracle.tests(d) if has_golden else [])]
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
        return {"app": app, "project": projects.load(self.projects_file).get(app), "golden": has_golden, "scenarios": self._scenarios(app),
                "oracle": st, "tests": tests, "runs": runs, "mutations": muts, "kind_label": catalog.KIND_LABEL}

    # ---- 화면 만들기 ----
    def page(self, app: str, kind: str, run: str | None = None) -> str:
        self._check(app)
        if kind not in PAGES:
            raise KeyError(kind)
        d = self.golden_root / app
        if not (d.is_dir() and any(d.glob("*.json"))):
            spec = projects.load(self.projects_file).get(app) or {}
            asis = (spec.get("asis") or {}).get("url") or "<as-is 주소>"
            body = (f"<header class='head'><div class='eyebrow'>{html._e(app)}</div><h1>골든이 아직 없습니다</h1>"
                    f"<p class='lede'>시나리오를 as-is에서 기록해야 개요·승인 검토·지도·보고서가 생깁니다.</p></header>"
                    f"<section><pre><code>uv run parity crawl {html._e(asis)} --out crawl/{html._e(app)}   # 화면을 훑어 시나리오 초안\n"
                    f"uv run pytest e2e/{html._e(app)} --base-url {html._e(asis)} --record golden/{html._e(app)}   # as-is에서 기록</code></pre></section>")
            return html._page(f"{app} {kind}", body)
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

    def _json(self, data: Any, code: int = 200) -> None:
        self._send(json.dumps(data, ensure_ascii=False).encode("utf-8"), "application/json; charset=utf-8", code)

    def do_GET(self):  # noqa: N802
        u = urlsplit(self.path)
        parts = [unquote(x) for x in u.path.strip("/").split("/") if x]
        q = parse_qs(u.query)
        try:
            if not parts:
                return self._send(SHELL.encode("utf-8"))
            if parts in (["api", "apps"], ["api", "projects"]):
                return self._json(self.hub.apps())
            if parts == ["api", "fs"]:
                return self._json(projects.listdir((q.get("path") or [None])[0]))
            if len(parts) == 4 and parts[:2] == ["api", "app"] and parts[3] == "init":
                return self._json(self.hub.init_status(self.hub._check(parts[2])))
            if len(parts) == 3 and parts[:2] == ["api", "app"]:
                return self._json(self.hub.app(parts[2]))
            if len(parts) == 3 and parts[0] == "page":
                return self._send(self.hub.page(parts[1], parts[2], (q.get("run") or [None])[0]).encode("utf-8"))
            if parts == ["file"]:
                p = self.hub.file((q.get("p") or [""])[0])
                if p is None:
                    return self._send(b"not found", "text/plain", 404)
                return self._send(p.read_bytes(), mimetypes.guess_type(p.name)[0] or "application/octet-stream")
        except PermissionError as e:
            return self._json({"error": str(e)}, 403)
        except (KeyError, FileNotFoundError) as e:
            return self._send(f"unknown: {e}".encode(), "text/plain; charset=utf-8", 404)
        return self._send(b"not found", "text/plain", 404)

    def do_POST(self):  # noqa: N802
        parts = [unquote(x) for x in urlsplit(self.path).path.strip("/").split("/") if x]
        try:
            body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0)) or 0) or b"{}")
            if len(parts) == 4 and parts[:2] == ["api", "app"] and parts[3] == "approve":
                return self._json(self.hub.approve(parts[2], by=str(body.get("by", "")), code=str(body.get("code", "")),
                                                   fingerprint=str(body.get("fingerprint", "")), note=str(body.get("note", ""))))
            if len(parts) == 4 and parts[:2] == ["api", "app"] and parts[3] == "init":
                return self._json(self.hub.init_project(parts[2], depth=int(body.get("depth") or 3)))
            if parts == ["api", "projects"]:
                return self._json(self.hub.add_project(str(body.get("name", "")).strip(), body))
            if len(parts) == 3 and parts[:2] == ["api", "projects"]:
                return self._json(self.hub.update_project(parts[2], body))
            if len(parts) == 4 and parts[:2] == ["api", "projects"] and parts[3] == "delete":
                return self._json(self.hub.remove_project(parts[2]))
            return self._send(b"not found", "text/plain", 404)
        except PermissionError as e:
            return self._json({"error": str(e)}, 403)
        except (ValueError, KeyError) as e:
            return self._json({"error": str(e) if isinstance(e, ValueError) else f"모르는 프로젝트: {e}"}, 409)


def serve(golden_root: Path, *, port: int = 8790, tests_root: Path = Path("e2e"), open_browser: bool = True) -> None:
    hub = Hub(golden_root, tests_root)
    handler = type("HubHandler", (Handler,), {"hub": hub})
    srv = ThreadingHTTPServer(("127.0.0.1", port), handler)
    url = f"http://127.0.0.1:{srv.server_address[1]}/"
    names = hub.names()
    print(f"parity ui · {url}  (프로젝트 {len(names)}개{': ' + ', '.join(names) if names else ' — 첫 화면에서 추가'}) — Ctrl+C로 종료")
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
[hidden]{display:none!important}
body.hub{display:flex;flex-direction:column;overflow:hidden}
.top{display:flex;align-items:center;gap:14px;padding:0 20px;height:54px;border-bottom:1px solid var(--line);background:var(--surface);flex:none}
.brand{font-weight:700;font-size:16px;letter-spacing:-.01em;color:var(--ink);text-decoration:none;cursor:pointer}
.crumb{display:flex;align-items:center;gap:10px;flex:1;min-width:0;font-size:14px;color:var(--muted)}
.crumb .sepc{color:var(--faint)}.crumb b{color:var(--ink);font-weight:600;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.crumb select{font:inherit;font-size:13.5px;font-weight:600;padding:4px 8px;border:1px solid var(--line);border-radius:6px;background:var(--surface);color:var(--ink)}
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
.actions button:hover{border-color:var(--accent);color:var(--accent)}.actions button.danger:hover{border-color:var(--bad);color:var(--bad)}
.notice{background:var(--warn-soft);color:var(--warn);border-radius:8px;padding:10px 14px;font-size:13.5px}
.empty{color:var(--muted);padding:40px;text-align:center}
.btn{font:inherit;font-size:13.5px;font-weight:600;padding:7px 14px;border-radius:8px;border:1px solid var(--line);background:var(--surface);color:var(--ink);cursor:pointer}
.btn.primary{background:var(--accent);border-color:var(--accent);color:#fff}.btn.primary:hover{filter:brightness(1.08)}.btn:disabled{opacity:.5;cursor:default}.btn.sm{font-size:12.5px;padding:4px 10px}
/* 화면 지도 초기화 */
.init{max-width:820px;margin:0 auto;padding:0 24px;width:100%;box-sizing:border-box}
.init .card{background:var(--surface);border:1px solid var(--line);border-radius:14px;padding:26px 28px;display:flex;flex-direction:column;gap:16px}
.init h1{font-size:22px;font-weight:700;margin:0}.init .lede{margin:0;color:var(--muted);font-size:14px}
.isteps{display:flex;flex-direction:column;gap:8px}
.istep{display:grid;grid-template-columns:28px 1fr auto;gap:12px;align-items:center;padding:10px 12px;border:1px solid var(--line);border-radius:10px;font-size:13.5px}
.istep .no{width:24px;height:24px;border-radius:50%;background:var(--sunk);color:var(--muted);font:600 12px/24px var(--mono);text-align:center}
.istep .st{font-size:12px;font-weight:600;color:var(--muted)}
.istep.run{border-color:var(--accent);background:var(--accent-soft)}.istep.run .no{background:var(--accent);color:#fff}.istep.run .st{color:var(--accent)}
.istep.done{border-color:var(--ok)}.istep.done .no{background:var(--ok);color:#fff}.istep.done .st{color:var(--ok)}
.istep.fail{border-color:var(--bad)}.istep.fail .no{background:var(--bad);color:#fff}.istep.fail .st{color:var(--bad)}
.istep.skip{opacity:.55}
.irow{display:flex;align-items:center;gap:10px;flex-wrap:wrap;font-size:13.5px}.irow label{font-weight:600;color:var(--muted);font-size:12.5px}
.irow select{font:inherit;font-size:13px;padding:5px 8px;border:1px solid var(--line);border-radius:6px;background:var(--surface);color:var(--ink)}
.init .err{color:var(--bad);font-size:13px;background:var(--bad-soft);border-radius:8px;padding:8px 12px}.init .err:empty{display:none}
pre.log{margin:0;background:var(--sunk);border-radius:10px;padding:12px 14px;font:12px/1.5 var(--mono);max-height:340px;overflow:auto;white-space:pre-wrap;word-break:break-all}
.proj .next .nx{display:flex;align-items:center;justify-content:space-between;gap:10px;flex-wrap:wrap}
/* 프로젝트 목록 */
.plist{display:flex;flex-direction:column;gap:22px;max-width:1180px;margin:0 auto;padding:0 24px}
.plist .bar{display:flex;align-items:flex-end;justify-content:space-between;gap:16px;flex-wrap:wrap}
.plist h1{font-size:22px;font-weight:700;margin:0}.plist .lede{color:var(--muted);font-size:14px;margin:4px 0 0}
.pgrid{display:grid;grid-template-columns:repeat(auto-fill,minmax(340px,1fr));gap:14px}
.proj{background:var(--surface);border:1px solid var(--line);border-radius:12px;padding:16px 18px;display:flex;flex-direction:column;gap:12px}
.proj .hd{display:flex;align-items:center;gap:10px}.proj .hd b{font-size:16px;font-weight:700;cursor:pointer}.proj .hd b:hover{color:var(--accent)}
.proj .hd .sp{flex:1}
.proj .sides{display:grid;grid-template-columns:auto 1fr;gap:5px 12px;font-size:13px;align-items:baseline}
.proj .sides .k{font-size:11.5px;font-weight:600;letter-spacing:.06em;color:var(--faint)}
.proj .sides .v{min-width:0}.proj .sides .v code{font-family:var(--mono);font-size:12.5px;background:var(--sunk);padding:1px 6px;border-radius:4px;word-break:break-all}
.proj .sides .v a{color:var(--muted);font-family:var(--mono);font-size:12px;margin-left:6px;text-decoration:none}.proj .sides .v a:hover{color:var(--accent)}
.proj .sides .v.none{color:var(--warn);font-size:12.5px}
.proj .stats{display:flex;gap:14px;flex-wrap:wrap;font-size:12.5px;color:var(--muted)}.proj .stats b{font-family:var(--mono);color:var(--ink);font-weight:600}
.proj .next{font-size:12.5px;color:var(--muted);background:var(--sunk);border-radius:8px;padding:8px 12px}.proj .next code{font-family:var(--mono);font-size:12px;display:block;margin-top:4px;color:var(--ink);white-space:pre-wrap}
.proj .ft{display:flex;align-items:center;gap:8px;margin-top:auto}.proj .ft .sp{flex:1}
.proj .confirm{background:var(--bad-soft);color:var(--bad);border-radius:8px;padding:8px 12px;font-size:12.5px;display:flex;align-items:center;gap:8px;flex-wrap:wrap}
.proj .confirm .actions{margin-left:auto}
/* 창 */
.modal{position:fixed;inset:0;background:rgba(10,14,13,.55);display:flex;align-items:center;justify-content:center;z-index:50;padding:16px}
.pdlg{background:var(--surface);color:var(--ink);border:1px solid var(--line);border-radius:14px;width:min(680px,100%);max-height:92vh;overflow:auto;padding:22px 24px;display:flex;flex-direction:column;gap:16px;box-shadow:0 24px 60px rgba(0,0,0,.35)}
.pdlg,.pdlg *{text-align:left}.pdlg h2{font-size:18px;font-weight:700;margin:0}.pdlg .lede{color:var(--muted);font-size:13.5px;margin:-8px 0 0}
.pdlg .prow{display:grid;grid-template-columns:110px minmax(0,1fr);gap:10px;align-items:center;justify-items:stretch}.pdlg .prow>*{max-width:none}
.pdlg .prow label{font-size:13px;font-weight:600;color:var(--muted)}
.pdlg input,.pdlg textarea{font:inherit;font-size:13.5px;padding:7px 10px;border:1px solid var(--line);border-radius:7px;background:var(--bg);color:var(--ink);width:100%;box-sizing:border-box}.pdlg .ft,.pdlg .fsb,.pdlg .fscur{text-align:left}
.pdlg input:disabled{color:var(--muted);background:var(--sunk)}
.pdlg .pick{display:flex;gap:6px}.pdlg .pick input{flex:1;font-family:var(--mono);font-size:12.5px}
.pdlg fieldset{border:1px solid var(--line);border-radius:10px;padding:12px 14px 14px;margin:0;display:flex;flex-direction:column;gap:10px}
.pdlg legend{font-size:12px;font-weight:700;letter-spacing:.06em;color:var(--accent);padding:0 6px}
.pdlg .err{color:var(--bad);font-size:13px;background:var(--bad-soft);border-radius:7px;padding:8px 12px}.pdlg .err:empty{display:none}
.pdlg .ft{display:flex;gap:8px;justify-content:flex-end}
/* 폴더 고르기 */
.fsb{display:flex;gap:6px;flex-wrap:wrap;align-items:center;font-size:12.5px}
.fsb button{font:inherit;font-size:12.5px;padding:3px 9px;border:1px solid var(--line);border-radius:999px;background:var(--surface);color:var(--muted);cursor:pointer}.fsb button:hover{color:var(--accent);border-color:var(--accent)}
.fscur{font-family:var(--mono);font-size:12.5px;background:var(--sunk);padding:7px 10px;border-radius:7px;word-break:break-all;display:flex;gap:8px;align-items:center}.fscur .sp{flex:1}
.fslist{border:1px solid var(--line);border-radius:8px;max-height:44vh;overflow:auto;background:var(--bg)}
.fslist div{display:flex;gap:10px;align-items:baseline;padding:7px 12px;border-bottom:1px solid var(--line);cursor:pointer;font-size:13.5px}
.fslist div:last-child{border-bottom:0}.fslist div:hover{background:var(--accent-soft)}.fslist div small{color:var(--faint);font-family:var(--mono);font-size:11.5px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.fslist .none{color:var(--muted);cursor:default}
@media (max-width:760px){.side{width:56px;padding:10px 6px}.side a span,.side a small,.side .sep{display:none}.runsel label{display:none}.pdlg .prow{grid-template-columns:1fr}}
"""

HUB_JS = r"""
const $ = (s, el=document) => el.querySelector(s);
const esc = s => String(s ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const TABS = [['map','화면 지도'],['overview','개요'],['history','이력'],['review','승인 검토'],['report','검증 보고서']];
const KIND = {golden_diff:'as-is와 다름', assert:'확인 값 실패', drift:'기대값 변경', error:'실행 못 함', same:'같음'};
let apps = [], state = {app:null, tab:'map', run:null}, data = null, initTimer = null;

function parseHash(){ const [app, tab, run] = location.hash.replace(/^#\/?/, '').split('/').map(decodeURIComponent); return {app: app||null, tab: tab||'map', run: run||null}; }
function setHash(){ location.hash = [state.app, state.tab, state.run].filter(x => x).map(encodeURIComponent).join('/'); }
function runLabel(r){ return `${r.finished.slice(5,16)} · ${r.target.replace(/^https?:\/\//,'')} · ${r.totals.fail ? r.totals.fail + ' 다름' : '모두 같음'}`; }
async function api(path, body){
  const r = await fetch(path, body ? {method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify(body)} : undefined);
  const j = await r.json().catch(() => ({error: r.statusText}));
  if (!r.ok) throw new Error(j.error || r.statusText);
  return j;
}

async function loadApps(){ apps = await api('/api/apps'); }

// ---- 머리: 프로젝트 목록에서는 이름만, 프로젝트 안에서는 빵부스러기 + 실행 선택 ----
function renderTop(){
  const c = $('#crumb');
  if (!state.app){ c.innerHTML = '<span class="sepc">›</span><b>프로젝트</b>'; $('#runsel').hidden = true; return; }
  c.innerHTML = `<span class="sepc">›</span><select id="switch" aria-label="프로젝트 바꾸기">${apps.map(a => `<option value="${esc(a.app)}" ${a.app === state.app ? 'selected' : ''}>${esc(a.app)}</option>`).join('')}</select>`;
  $('#switch').onchange = e => { state.app = e.target.value; state.run = null; setHash(); };
  $('#runsel').hidden = false;
}

// ---- 프로젝트 목록 ----
function projCard(a){
  const side = (k, s) => `<div class="k">${k}</div>` + (s && s.src
    ? `<div class="v"><code>${esc(s.src)}</code>${s.url ? `<a href="${esc(s.url)}" target="_blank" rel="noopener">${esc(s.url.replace(/^https?:\/\//,''))}</a>` : ''}</div>`
    : `<div class="v none">경로 미설정 — 설정에서 고르세요</div>`);
  const pill = a.ok ? `<span class="pill ok" title="${esc(a.approved_at||'')}">승인됨 · ${esc(a.approved_by)}</span>`
             : a.golden ? '<span class="pill warn">승인 필요</span>' : '<span class="pill none">골든 없음</span>';
  const last = a.last ? `${a.last.totals.fail ? `<b style="color:var(--bad)">${a.last.totals.fail} 다름</b>` : '<b style="color:var(--ok)">모두 같음</b>'} <span>${esc(a.last.finished.slice(5,16))}</span>` : '<b>—</b>';
  let next = '';
  const asis = (a.asis||{}).url || '<as-is 주소>', tobe = (a.tobe||{}).url || '<to-be 주소>';
  if (!a.golden) next = `<div class="next"><div class="nx"><span>다음: 화면 지도 만들기 — as-is를 ${a.scenarios ? '기록' : '탐색해 시나리오 초안을 만들고 기록'}합니다</span><button class="btn primary sm" data-init>화면 지도 만들기</button></div>`
    + `<code>${a.scenarios ? '' : `uv run parity crawl ${esc(asis)} --out crawl/${esc(a.app)}\n`}uv run pytest e2e/${esc(a.app)} --base-url ${esc(asis)} --record golden/${esc(a.app)}</code></div>`;
  else if (!a.ok) next = `<div class="next">다음: 승인 검토 탭에서 확인 후 승인 (터미널 코드 필요)</div>`;
  else if (!a.runs) next = `<div class="next">다음: to-be 비교<code>uv run pytest e2e/${esc(a.app)} --base-url ${esc(tobe)} --compare golden/${esc(a.app)} --junitxml reports/junit-${esc(a.app)}.xml</code></div>`;
  return `<div class="proj" data-app="${esc(a.app)}"><div class="hd"><b data-open>${esc(a.app)}</b>${pill}<span class="sp"></span>${a.registered ? '' : '<span class="pill warn" title="parity.json에 없음. golden/ 또는 e2e/ 에서 발견">미등록</span>'}</div>
    <div class="sides">${side('AS-IS', a.asis)}${side('TO-BE', a.tobe)}</div>
    ${a.note ? `<div style="font-size:13px;color:var(--muted)">${esc(a.note)}</div>` : ''}
    <div class="stats"><span>시나리오 <b>${a.scenarios}</b></span><span>골든 <b>${a.tests}</b></span><span>비교 <b>${a.runs}</b>회</span><span>마지막 ${last}</span></div>
    ${next}<div class="confirm" hidden>등록만 지웁니다. e2e/·golden/·runs/ 의 산출물은 남습니다.<div class="actions"><button data-del-yes class="danger">지우기</button><button data-del-no>취소</button></div></div>
    <div class="ft"><span class="sp"></span><div class="actions"><button data-open>열기</button><button data-edit>설정</button>${a.registered ? '<button data-del class="danger">삭제</button>' : ''}</div></div></div>`;
}

function renderList(v){
  $('#tabs').hidden = true;
  v.innerHTML = `<main class="plist"><div class="bar"><div><h1>프로젝트</h1><p class="lede">as-is와 to-be 한 쌍이 프로젝트 하나. 소스 위치와 실행 주소를 적어 두면 시나리오·골든·비교 이력이 이 이름으로 묶입니다.</p></div><button class="btn primary" id="add">+ 프로젝트 추가</button></div>
    ${apps.length ? `<div class="pgrid">${apps.map(projCard).join('')}</div>` : '<div class="empty">아직 프로젝트가 없습니다. 위의 "프로젝트 추가"로 as-is·to-be 소스 위치를 고르세요.</div>'}</main>`;
  $('#add').onclick = () => openEditor(null);
  for (const card of v.querySelectorAll('.proj')){
    const name = card.dataset.app, a = apps.find(x => x.app === name);
    for (const el of card.querySelectorAll('[data-open], [data-init]')) el.onclick = () => { state.app = name; state.tab = 'map'; state.run = null; setHash(); };
    card.querySelector('[data-edit]').onclick = () => openEditor(a);
    const del = card.querySelector('[data-del]');
    if (del) del.onclick = () => { card.querySelector('.confirm').hidden = false; };
    card.querySelector('[data-del-no]').onclick = () => { card.querySelector('.confirm').hidden = true; };
    card.querySelector('[data-del-yes]').onclick = async () => { try { await api('/api/projects/' + encodeURIComponent(name) + '/delete', {}); await loadApps(); route(); } catch (e) { alert(e.message); } };
  }
}

// ---- 프로젝트 추가/설정 창 ----
function openEditor(a){
  const isNew = !a;
  const side = (k, label, s) => `<fieldset><legend>${label}</legend>
    <div class="prow"><label>소스 위치</label><div class="pick"><input name="${k}_src" value="${esc((s||{}).src||'')}" placeholder="폴더를 고르세요" autocomplete="off"><button type="button" class="btn" data-pick="${k}">찾아보기</button></div></div>
    <div class="prow"><label>실행 주소</label><input name="${k}_url" value="${esc((s||{}).url||'')}" placeholder="http://127.0.0.1:8820 (선택)" autocomplete="off"></div></fieldset>`;
  const m = document.createElement('div'); m.className = 'modal';
  m.innerHTML = `<form class="pdlg" id="pf"><h2>${isNew ? '프로젝트 추가' : `프로젝트 설정 · ${esc(a.app)}`}</h2>
    <p class="lede">as-is(지금 쓰는 앱)와 to-be(새로 만든 앱)의 소스 위치를 고르세요. 실행 주소는 탐색·기록·비교 명령에 그대로 들어갑니다.</p>
    <div class="prow"><label>이름</label><input name="name" value="${esc(isNew ? '' : a.app)}" ${isNew ? '' : 'disabled'} placeholder="소문자·숫자·-·_ (예: portal)" autocomplete="off" required></div>
    ${side('asis', 'AS-IS', a && a.asis)}${side('tobe', 'TO-BE', a && a.tobe)}
    <div class="prow"><label>메모</label><input name="note" value="${esc((a||{}).note||'')}" placeholder="선택" autocomplete="off"></div>
    <div class="err" id="perr"></div>
    <div class="ft"><button type="button" class="btn" data-cancel>취소</button><button type="submit" class="btn primary">${isNew ? '추가' : '저장'}</button></div></form>`;
  document.body.appendChild(m);
  const f = $('#pf', m);
  m.querySelector('[data-cancel]').onclick = () => m.remove();
  m.addEventListener('click', e => { if (e.target === m) m.remove(); });
  for (const b of m.querySelectorAll('[data-pick]')) b.onclick = () => { const inp = f.elements[b.dataset.pick + '_src']; openPicker(inp.value, p => { inp.value = p; }); };
  f.onsubmit = async e => {
    e.preventDefault(); $('#perr', m).textContent = '';
    const name = isNew ? f.elements.name.value.trim() : a.app;
    const body = {name, asis: {src: f.elements.asis_src.value.trim(), url: f.elements.asis_url.value.trim()}, tobe: {src: f.elements.tobe_src.value.trim(), url: f.elements.tobe_url.value.trim()}, note: f.elements.note.value.trim()};
    try { await api(isNew ? '/api/projects' : '/api/projects/' + encodeURIComponent(name), body); m.remove(); await loadApps(); route(); }
    catch (err) { $('#perr', m).textContent = err.message; }
  };
  (isNew ? f.elements.name : f.elements.asis_src).focus();
}

// ---- 폴더 고르기 (서버가 작업 디렉터리·홈 아래만 보여준다) ----
function openPicker(start, onPick){
  const m = document.createElement('div'); m.className = 'modal';
  m.innerHTML = `<div class="pdlg" role="dialog" aria-label="폴더 고르기"><h2>폴더 고르기</h2><div class="fsb" id="fsroots"></div>
    <div class="fscur"><button type="button" class="btn" id="fsup" title="상위 폴더">↑</button><span id="fspath" class="sp"></span><button type="button" class="btn primary" id="fsok">이 폴더 선택</button></div>
    <div class="fslist" id="fslist"></div><div class="err" id="fserr"></div>
    <div class="ft"><button type="button" class="btn" data-cancel>취소</button></div></div>`;
  document.body.appendChild(m);
  m.querySelector('[data-cancel]').onclick = () => m.remove();
  m.addEventListener('click', e => { if (e.target === m) m.remove(); });
  let cur = null;  // 선택값: 작업 디렉터리 안이면 상대 경로
  async function go(p){
    $('#fserr', m).textContent = '';
    try {
      const d = await api('/api/fs' + (p ? '?path=' + encodeURIComponent(p) : ''));
      cur = d.display === '.' ? '.' : d.display;
      $('#fsroots', m).innerHTML = d.roots.map(r => `<button type="button" data-p="${esc(r.path)}">${esc(r.name)}</button>`).join('');
      for (const b of $('#fsroots', m).children) b.onclick = () => go(b.dataset.p);
      $('#fspath', m).textContent = d.display === '.' ? d.path : d.display;
      $('#fspath', m).title = d.path;
      $('#fsup', m).disabled = !d.parent; $('#fsup', m).onclick = () => d.parent && go(d.parent);
      $('#fslist', m).innerHTML = d.dirs.length ? d.dirs.map(x => `<div data-p="${esc(x.path)}">📁 ${esc(x.name)}<small>${esc(x.hint)}</small></div>`).join('') : '<div class="none">하위 폴더 없음</div>';
      for (const el of $('#fslist', m).querySelectorAll('[data-p]')) el.onclick = () => go(el.dataset.p);
    } catch (e) { $('#fserr', m).textContent = e.message; }
  }
  $('#fsok', m).onclick = () => { if (cur){ onPick(cur); m.remove(); } };
  go(start && start.trim() ? start.trim() : null);
}

// ---- 프로젝트 화면 ----
async function loadApp(){
  data = await api('/api/app/' + encodeURIComponent(state.app));
  if (!state.run || !data.runs.some(r => r.stamp === state.run)) state.run = data.runs.length ? data.runs[data.runs.length-1].stamp : null;
  const sel = $('#run'); sel.innerHTML = '';
  for (const r of [...data.runs].reverse()){ const o = document.createElement('option'); o.value = r.stamp; o.textContent = runLabel(r); sel.appendChild(o); }
  sel.value = state.run || ''; sel.disabled = !data.runs.length; $('#runsel').hidden = !data.runs.length;
  $('#tabs').hidden = false;
  $('#tabs').innerHTML = `<a data-back><span>← 프로젝트</span></a><div class="sep">${esc(state.app)}</div>` + TABS.map(([k, l]) => {
    const n = k === 'history' ? data.runs.length : (k === 'overview' ? data.tests.length : '');
    return `<a data-tab="${k}" class="${k === state.tab ? 'on' : ''}"><span>${l}</span>${n !== '' ? `<small>${n}</small>` : ''}</a>`;
  }).join('');
  for (const a of $('#tabs').querySelectorAll('a[data-tab]')) a.onclick = () => { state.tab = a.dataset.tab; setHash(); };
  $('#tabs a[data-back]').onclick = () => { state = {app:null, tab:'overview', run:null}; location.hash = ''; };
}

function render(){
  const v = $('#view');
  clearTimeout(initTimer);
  if (!state.app){ renderList(v); return; }
  if (state.tab === 'map' && !data.golden){ renderInit(v); return; }
  const q = state.run ? '?run=' + encodeURIComponent(state.run) : '';
  const page = {overview:'catalog', review:'review', map:'map', report:'report'}[state.tab];
  if (page){ v.innerHTML = `<iframe title="${state.tab}" src="/page/${encodeURIComponent(state.app)}/${page}${q}"></iframe>`; return; }
  renderHistory(v);
}

// ---- 화면 지도 초기화: 골든이 없으면 지도 대신 이 화면. 탐색 → 초안 → 기록을 서버가 순서대로 돌리고 로그를 보여준다 ----
async function renderInit(v){
  const p = data.project || {}, asis = (p.asis||{}).url || '';
  let job = await api('/api/app/' + encodeURIComponent(state.app) + '/init');
  const stepsHtml = steps => `<div class="isteps">${steps.map((s, i) => `<div class="istep ${s.state}"><span class="no">${i+1}</span><span>${esc(s.label)}</span><span class="st">${{wait:'대기', run:'실행 중…', done:'완료', fail:'실패', skip:'건너뜀'}[s.state]}</span></div>`).join('')}</div>`;
  const plan = [
    {state: data.scenarios ? 'skip' : 'wait', label: data.scenarios ? `탐색 건너뜀 — e2e/${state.app}/ 에 시나리오 ${data.scenarios}개` : `as-is 화면 탐색 (crawl) → crawl/${state.app}/`},
    {state: data.scenarios ? 'skip' : 'wait', label: `시나리오 초안을 e2e/${state.app}/ 로`},
    {state: 'wait', label: `as-is(${asis || '주소 없음'})에서 골든 기록 → golden/${state.app}/`}];
  v.innerHTML = `<main class="init"><div class="card"><div class="eyebrow">화면 지도</div><h1>아직 화면 지도가 없습니다</h1>
    <p class="lede">지도는 as-is에서 기록한 골든으로 그립니다. 아래 순서를 서버가 대신 돌립니다. 기록이 끝나면 지도가 바로 보이고, <b>승인</b>은 그 뒤 사람이 승인 검토 탭에서 합니다.</p>
    <div id="isteps">${stepsHtml(job.steps || plan)}</div>
    <div class="irow"><label>as-is 주소</label>${asis ? `<b class="mono">${esc(asis)}</b>` : `<span class="pill warn">없음 — 프로젝트 설정에서 적으세요</span> <button class="btn sm" id="goset">설정</button>`}
      <label>탐색 깊이</label><select id="depth" ${data.scenarios ? 'disabled' : ''}><option value="2">2 (빠름)</option><option value="3" selected>3 (기본)</option><option value="4">4 (넓게)</option></select></div>
    ${data.scenarios ? '' : '<div class="notice"><b>주의</b> 탐색은 저장·확정 버튼도 실제로 누릅니다. 테스트 DB·테스트 계정의 as-is에서만 돌리세요. 삭제·결제·발송 같은 버튼은 기본 금지 목록으로 누르지 않습니다.</div>'}
    <div class="irow"><button class="btn primary" id="doinit" ${!asis || job.running ? 'disabled' : ''}>${job.running ? '실행 중…' : '화면 지도 만들기'}</button><span id="imsg" class="tid">${job.error ? '' : ''}</span></div>
    <div id="ierr" class="err">${esc(job.error || '')}</div>
    <pre class="log" id="ilog" ${(job.log||[]).length ? '' : 'hidden'}>${esc((job.log||[]).join('\n'))}</pre></div></main>`;
  const goset = $('#goset'); if (goset) goset.onclick = () => openEditor(apps.find(a => a.app === state.app));
  $('#doinit').onclick = async () => {
    $('#ierr').textContent = ''; $('#doinit').disabled = true; $('#doinit').textContent = '실행 중…';
    try { await api('/api/app/' + encodeURIComponent(state.app) + '/init', {depth: +$('#depth').value}); poll(); }
    catch (e) { $('#ierr').textContent = e.message; $('#doinit').disabled = false; $('#doinit').textContent = '화면 지도 만들기'; }
  };
  async function poll(){
    const j = await api('/api/app/' + encodeURIComponent(state.app) + '/init');
    if (j.steps){ $('#isteps').innerHTML = stepsHtml(j.steps); }
    const log = $('#ilog'); log.hidden = !(j.log||[]).length; log.textContent = (j.log||[]).join('\n'); log.scrollTop = log.scrollHeight;
    $('#ierr').textContent = j.error || '';
    if (j.running){ initTimer = setTimeout(poll, 2000); return; }
    if (j.ok){ await loadApps(); await loadApp(); render(); return; }  // 골든이 생겼으니 지도를 그린다
    $('#doinit').disabled = false; $('#doinit').textContent = '다시 시도';
  }
  if (job.running) poll();
}

function renderHistory(v){
  const d = data, st = d.oracle, runs = d.runs, cur = runs.find(r => r.stamp === state.run);
  const mut = d.mutations.find(m => m.current);
  const last = runs[runs.length-1];
  const stale = last && st.approved_at && last.approved_at !== st.approved_at;
  const cards = [
    ['승인', st.ok ? `${esc(st.approved_by)}<small>${esc((st.approved_at||'').slice(0,16))}</small>` : (d.golden ? '없음<small>승인 필요</small>' : '없음<small>골든 없음</small>'), st.ok ? 'ok' : 'warn'],
    ['비교 실행', `${runs.length}회<small>${last ? esc(last.finished.slice(0,16)) : '아직 없음'}</small>`, ''],
    ['마지막 결과', last ? `${last.totals.pass} 같음 · ${last.totals.fail} 다름` : '—', last ? (last.totals.fail ? 'bad' : 'ok') : ''],
    ['결함 탐지', mut ? `${Math.round(mut.score*100)}%<small>${mut.killed}/${mut.total} · ${esc((mut.generated_at||'').slice(0,16))}</small>` : '없음<small>현재 승인본으로 측정한 결과 없음</small>', mut ? (mut.score >= .8 ? 'ok' : 'bad') : 'warn'],
  ];
  let h = `<main class="hist"><div class="strip">${cards.map(([k,val,c]) => `<div class="card ${c}"><div class="k">${k}</div><div class="v">${val}</div></div>`).join('')}</div>`;
  if (stale) h += `<div class="notice"><b>주의</b> 마지막 비교는 이전 승인본(${esc(last.approved_at||'없음')})으로 실행됐습니다. 현재 승인본으로 다시 비교하세요.</div>`;
  if (!runs.length){
    const tobe = ((d.project||{}).tobe||{}).url || '<to-be>';
    h += `<div class="empty">to-be 비교 실행 기록이 없습니다.<br><code>uv run pytest e2e/${esc(d.app)} --base-url ${esc(tobe)} --compare golden/${esc(d.app)} --junitxml reports/junit-${esc(d.app)}.xml</code></div></main>`; v.innerHTML = h; return; }

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
  if (h.app && !apps.some(a => a.app === h.app)) h.app = null;  // 모르는 이름이면 목록으로
  state = {app: h.app, tab: h.tab, run: h.run};
  renderTop();
  if (state.app) await loadApp();
  render();
  if (!apps.length && !document.querySelector('.modal')) openEditor(null);  // 첫 방문: 바로 추가 창
}
$('#run').addEventListener('change', e => { state.run = e.target.value || null; setHash(); });
$('#brand').addEventListener('click', e => { e.preventDefault(); location.hash = ''; });
window.addEventListener('message', e => { if (e.data && e.data.parity === 'approved') loadApps().then(route); });  // 검토 화면에서 승인되면 상태 갱신
window.addEventListener('hashchange', route);
loadApps().then(route);
"""

SHELL = (f"<!doctype html><html lang='ko'><head><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1,viewport-fit=cover'>"
         f"<title>parity</title>{html.FONTS}<style>{html.CSS}{HUB_CSS}</style></head><body class='hub'>"
         "<header class='top'><a class='brand' id='brand' href='#'>parity</a><div class='crumb' id='crumb'></div>"
         "<div class='runsel' id='runsel' hidden><label for='run'>실행</label><select id='run' aria-label='실행 선택'></select></div></header>"
         "<div class='frame'><nav class='side' id='tabs' aria-label='화면' hidden></nav><div id='view'></div></div>"
         f"<script>{HUB_JS}</script></body></html>")
