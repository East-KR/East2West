"""통합 화면 (eastshift ui): 프로젝트 목록에서 시작해, 프로젝트마다 사람이 보는 화면 네 장(개요·시나리오 승인·화면 지도·검증 보고서)과 실행 이력을 한 화면에서 본다.
사람이 보는 화면은 전부 여기서만 만든다 (파일로 따로 떨구지 않는다). 승인도 여기서만 한다.

uv run eastshift ui [--golden golden] [--port 8790]      → http://127.0.0.1:8790/

첫 화면은 프로젝트 목록(eastshift.json)이다. "프로젝트 추가"를 누르면 as-is와 to-be 소스 위치를 파일 시스템에서 고르는 창이 뜨고,
저장하면 등록부에 적히고 e2e/<app>/ 자리가 생긴다. 등록부에 없어도 golden/<app> 이나 e2e/<app> 이 있으면 목록에 나온다(경로 미설정).
서버는 산출물(golden/<app>/, runs/<app>/)만 읽고, 요청이 올 때 기존 생성기(catalog·review·map·report)로 화면을 만든다.
실행 원장이 실행마다 JUnit·스크린샷 사본을 남기므로 지난 실행의 지도·보고서도 다시 그릴 수 있다. 새로 판단하는 것은 없다.

경로:
  /                                 통합 화면 (프로젝트 목록 → 프로젝트 화면: 탭, 실행 선택)
  /api/apps                         프로젝트별 등록 정보·승인 상태·실행 수·마지막 결과
  /api/app/<app>                    시나리오, 실행 이력(지난 실행 대비 변화 포함), 결함 주입 결과
  POST /api/app/<app>/approve       웹 승인 {by, fingerprint}. 통합 검토 화면에서 바로 승인한다
  POST /api/app/<app>/init          골든 시나리오 지도 초기화: 골든이 없는 프로젝트를 as-is에서 탐색(crawl) → 시나리오 초안 → 기록 {depth} (승인은 하지 않는다)
  POST /api/app/<app>/crawl         탐색 지도: as-is 또는 to-be를 eastshift crawl 로 훑는다 {side: asis|tobe, depth}. 소스 위치가 있으면 이어서 eastshift routes 로
                                    라우트 목록(crawl/<app>[-tobe]/routes.json)을 뽑는다 → 지도가 '코드에만 있는 화면'을 회색으로 표시
  POST /api/app/<app>/compare       to-be 비교 다시 실행 (pytest --compare, 보고서의 '바로 해결' 버튼)
  POST /api/app/<app>/mutate        결함 탐지 측정 실행 (eastshift mutate --compare, 보고서의 '바로 해결' 버튼)
  GET  /api/app/<app>/job           위 작업의 진행 (단계·로그). /init 도 같은 것. 앱마다 한 번에 하나
  POST /api/projects                프로젝트 추가 {name, asis:{src,url}, tobe:{src,url}, note}
  POST /api/projects/<app>          프로젝트 설정 변경 (같은 본문)
  POST /api/projects/<app>/delete   등록 해제 (산출물은 남긴다)
  /api/fs?path=                     폴더 고르기용 하위 폴더 목록 (작업 디렉터리·홈 아래만)
  /page/<app>/catalog|review        개요(골든 관리), 시나리오 승인
  /page/<app>/map?src=compare&run=<시각>   화면 지도 셋 중 하나: src=asis|tobe 는 탐색 결과(crawl/<app>, crawl/<app>-tobe), compare 는 as-is 기준 to-be 비교
                                    (골든 + 그 실행의 다름(빨강) + to-be 탐색으로 미개발(노랑)·새 화면(파랑), 캡처는 to-be 우선)
  /page/<app>/report?run=<시각>      검증 보고서 (그 실행의 JUnit + 현재 승인본의 결함 주입 결과)
  /file?p=runs/…                    스크린샷 등 산출물 파일 (runs/ 와 골든 루트 아래만, 읽기 전용)
"""
from __future__ import annotations

import json
import mimetypes
import os
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
        self.jobs: dict[str, dict[str, Any]] = {}  # 화면 지도 초기화 작업 (앱별 하나)

    # ---- 웹 승인 ----
    def approve(self, app: str, *, by: str, fingerprint: str, note: str = "") -> dict[str, Any]:
        """검토 화면에서 직접 승인한다. 검토 당시의 기준 지문을 확인한다."""
        d = self._golden(app)
        rec = oracle.approve_from_review(d, by, note, fingerprint)
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

    # ---- 화면 지도 세 가지: as-is 탐색 (crawl/<app>), to-be 탐색 (crawl/<app>-tobe), to-be 비교 (as-is 기준: 골든 + 실행 JUnit + 두 탐색으로 미개발·새 화면) ----
    def _has_golden(self, app: str) -> bool:
        d = self.golden_root / app
        return d.is_dir() and any(d.glob("*.json"))

    @staticmethod
    def crawl_dir(app: str, side: str) -> Path:
        return Path("crawl") / (app if side == "asis" else f"{app}-tobe")

    def maps(self, app: str) -> dict[str, bool]:
        return {"asis": (self.crawl_dir(app, "asis") / "graph.json").exists(), "tobe": (self.crawl_dir(app, "tobe") / "graph.json").exists(),
                "compare": self._has_golden(app)}

    def _side_url(self, app: str, side: str) -> str:
        spec = projects.load(self.projects_file).get(app) or {}
        url = (spec.get(side) or {}).get("url") or ""
        if not url.startswith(("http://", "https://")):
            raise ValueError(f"{'as-is' if side == 'asis' else 'to-be'} 실행 주소가 없습니다. 프로젝트 설정에서 적으세요")
        return url

    def crawl_plan(self, app: str, side: str, depth: int = 3) -> list[dict[str, Any]]:
        """탐색 지도 한 쪽: eastshift crawl 한 번. 이미 있으면 덮어쓴다 (탐색 산출물은 도구 것이라 다시 만들어도 된다)."""
        self._check(app)
        if side not in projects.SIDES:
            raise ValueError(f"side는 asis|tobe: {side}")
        url = self._side_url(app, side)
        depth = max(1, min(int(depth or 3), 5))
        out = self.crawl_dir(app, side)
        steps = [{"step": "crawl", "label": f"{'as-is' if side == 'asis' else 'to-be'}({url}) 화면 탐색 (깊이 {depth}) → {out}",
                  "cmd": [sys.executable, "-m", "eastshift.cli", "crawl", url, "--out", str(out), "--depth", str(depth)]}]
        src = ((projects.load(self.projects_file).get(app) or {}).get(side) or {}).get("src") or ""
        if src and Path(src).expanduser().exists():  # 소스가 있으면 라우트 목록도 뽑아 지도의 잣대로 (코드에만 있는 화면을 회색으로)
            steps.append({"step": "routes", "label": f"소스 {src} 에서 라우트 목록 → {out / 'routes.json'} (코드에는 있는데 탐색이 못 간 화면의 잣대)",
                          "cmd": [sys.executable, "-m", "eastshift.cli", "routes", src, "--out", str(out / "routes.json")]})
        return steps

    def init_plan(self, app: str, depth: int = 3) -> list[dict[str, Any]]:
        """골든 시나리오 비교 지도 초기화 단계 (실행하지 않는다). 시나리오가 없으면 탐색(이미 탐색했으면 건너뜀) → 초안 옮기기 → 기록, 있으면 기록만. 승인은 여기 없다 — 사람이 한다."""
        self._check(app)
        if self._has_golden(app):
            raise ValueError("골든이 이미 있어 지도를 그릴 수 있습니다. 다시 기록하려면 터미널에서 (골든은 사람 승인물이라 여기서 덮어쓰지 않습니다)")
        url = self._side_url(app, "asis")
        depth = max(1, min(int(depth or 3), 5))
        out = self.crawl_dir(app, "asis")
        steps: list[dict[str, Any]] = []
        if not self._scenarios(app):
            if (out / "test_crawl.py").exists():
                steps.append({"step": "crawl", "label": f"탐색 건너뜀 — {out}/ 에 as-is 탐색 결과가 이미 있음", "skip": True})
            else:
                steps.append({"step": "crawl", "label": f"as-is 화면 탐색 (깊이 {depth}) → {out}",
                              "cmd": [sys.executable, "-m", "eastshift.cli", "crawl", url, "--out", str(out), "--depth", str(depth)]})
            steps.append({"step": "tests", "label": f"시나리오 초안을 {self.tests_root / app}/ 로", "copy": [str(out / "test_crawl.py"), str(self.tests_root / app / "test_crawl.py")]})
        else:
            steps.append({"step": "crawl", "label": f"탐색 건너뜀 — {self.tests_root / app}/ 에 시나리오 {self._scenarios(app)}개", "skip": True})
        steps.append({"step": "record", "label": f"as-is({url})에서 골든 기록 → {self.golden_root / app}",
                      "cmd": [sys.executable, "-m", "pytest", str(self.tests_root / app), "--base-url", url, "--record", str(self.golden_root / app), "-q", "-p", "no:cacheprovider"]})
        return steps

    def init_status(self, app: str) -> dict[str, Any]:
        """앱의 현재/마지막 작업 (init | asis | tobe). 없으면 running=False."""
        job = self.jobs.get(app)
        if not job:
            return {"running": False}
        return {k: v for k, v in job.items() if k != "log"} | {"log": job["log"][-80:]}

    def init_project(self, app: str, depth: int = 3) -> dict[str, Any]:
        """골든 시나리오 비교 지도 초기화를 백그라운드로 시작한다."""
        return self._start_job(app, "init", self.init_plan(app, depth), check=lambda: self._has_golden(app),
                               done_msg="== 끝. Screen Map을 그립니다. 승인은 시나리오 승인 탭에서 (사람)",
                               missing_msg="기록이 끝났지만 골든 파일이 없습니다 (시나리오가 하나도 통과하지 못했는지 로그를 보세요)")

    def start_crawl(self, app: str, side: str, depth: int = 3) -> dict[str, Any]:
        """as-is 또는 to-be 탐색 지도를 백그라운드로 만든다."""
        return self._start_job(app, side, self.crawl_plan(app, side, depth), check=lambda: (self.crawl_dir(app, side) / "graph.json").exists(),
                               done_msg="== 끝. 탐색 지도를 그립니다.", missing_msg="탐색이 끝났지만 graph.json이 없습니다 (로그를 보세요)")

    # ---- 보고서의 '바로 해결' 버튼: to-be 비교 다시 실행, 결함 탐지 측정. 둘 다 승인된 골든이 있어야 돈다 (pytest·mutate 가 스스로 거부한다) ----
    def compare_plan(self, app: str) -> list[dict[str, Any]]:
        """to-be 비교 한 번: pytest --compare (원장·JUnit 사본은 플러그인이 남긴다). 다른 결과(exit 1)도 정상 종료다."""
        self._golden(app)
        url = self._side_url(app, "tobe")
        junit = Path("reports") / f"junit-{app}.xml"
        return [{"step": "compare", "label": f"to-be({url}) 비교 → runs/{app}/ 원장 + {junit}", "ok_exit": (0, 1),
                 "cmd": [sys.executable, "-m", "pytest", str(self.tests_root / app), "--base-url", url, "--compare", str(self.golden_root / app),
                         "--junitxml", str(junit), "-q", "-p", "no:cacheprovider"]}]

    def mutate_plan(self, app: str) -> list[dict[str, Any]]:
        """결함 탐지 측정: eastshift mutate --compare (결과는 runs/<app>/mutations/ 에도 복사된다). as-is 에서 돈다."""
        self._golden(app)
        url = self._side_url(app, "asis")
        return [{"step": "mutate", "label": f"as-is({url})에 결함을 하나씩 넣어 테스트가 잡는지 측정 (시나리오 수에 따라 몇 분~수십 분)",
                 "cmd": [sys.executable, "-m", "eastshift.cli", "mutate", str(self.tests_root / app), "--base-url", url, "--compare", str(self.golden_root / app), "--max-per-op", "100"]}]

    def start_compare(self, app: str) -> dict[str, Any]:
        before = len(ledger.load_runs(app))
        return self._start_job(app, "compare", self.compare_plan(app), check=lambda: len(ledger.load_runs(app)) > before,
                               done_msg="== 끝. 새 실행이 원장에 남았습니다. 보고서와 지도를 새로 그립니다.", missing_msg="비교가 끝났지만 원장에 새 실행이 없습니다 (로그를 보세요: 승인되지 않은 골든이면 거부됩니다)")

    def start_mutate(self, app: str) -> dict[str, Any]:
        st = oracle.status(self.golden_root / app)
        return self._start_job(app, "mutate", self.mutate_plan(app), check=lambda: ledger.mutation_for(app, st.get("approved_at"), st.get("approval_id")) is not None,
                               done_msg="== 끝. 현재 승인본의 결함 탐지 결과가 원장에 남았습니다. 보고서를 새로 그립니다.", missing_msg="측정이 끝났지만 현재 승인본의 결과가 없습니다 (로그를 보세요)")

    def _start_job(self, app: str, kind: str, steps: list[dict[str, Any]], *, check, done_msg: str, missing_msg: str) -> dict[str, Any]:
        job = self.jobs.get(app)
        if job and job["running"]:
            raise ValueError(f"이미 실행 중입니다 ({job['kind']})")
        job = {"kind": kind, "running": True, "ok": False, "error": "", "steps": [{"step": s["step"], "label": s["label"], "state": "skip" if s.get("skip") else "wait"} for s in steps],
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
                        if proc.wait() not in s.get("ok_exit", (0,)):
                            raise RuntimeError(f"{s['step']} 실패 (exit {proc.returncode})")
                    st["state"] = "done"
                if not check():
                    raise RuntimeError(missing_msg)
                job["ok"] = True
                log(done_msg)
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
        """골든·원장·탐색 결과가 바뀌면 화면 캐시를 버린다."""
        latest = 0.0
        for root in (self.golden_root / app, ledger.run_dir(app), self.crawl_dir(app, "asis"), self.crawl_dir(app, "tobe")):
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
                         "approved_at": r["oracle"].get("approved_at"), "approval_id": r["oracle"].get("approval_id"),
                         "approved_by": r["oracle"].get("approved_by"), "ok": r["oracle"].get("ok"),
                         "totals": r["totals"], "junit": bool(r.get("junit") and Path(r["junit"]).exists()),
                         "delta": ledger.compare_runs(r, previous), "cases": cases})
            previous = r
        current = ledger.mutation_for(app, st.get("approved_at"), st.get("approval_id"))
        muts = [{"file": m["_file"], "generated_at": m.get("generated_at"), "mode": m.get("mode"), "score": m.get("score"), "killed": m.get("killed"),
                 "total": m.get("total"), "errors": m.get("errors", 0), "approved_at": m.get("oracle_approved_at"),
                 "current": bool(current) and str(current) == m["_file"]} for m in ledger.load_mutations(app)]
        return {"app": app, "project": projects.load(self.projects_file).get(app), "golden": has_golden, "scenarios": self._scenarios(app),
                "maps": self.maps(app), "oracle": st, "tests": tests, "runs": runs, "mutations": muts, "kind_label": catalog.KIND_LABEL}

    # ---- 화면 만들기 ----
    def page(self, app: str, kind: str, run: str | None = None, src: str = "compare") -> dict[str, Any]:
        """화면 조각 (html.fragment 형식: kind·title·html·css·js). 통합 화면이 한 문서 안에 끼우고, /page/… 직접 접속은 html.assemble 로 문서를 만든다.
        src: 화면 지도의 출처 — compare(골든 시나리오 + 실행), asis/tobe(탐색 결과)."""
        self._check(app)
        if kind not in PAGES:
            raise KeyError(kind)
        if kind == "map" and src in ("asis", "tobe"):
            cd = self.crawl_dir(app, src)
            if not (cd / "graph.json").exists():
                who = "as-is" if src == "asis" else "to-be"
                body = (f"<header class='head'><div class='eyebrow'>Screen Map · {who} 탐색</div><h1>{html._e(app)}</h1>"
                        f"<p class='lede'>{who}를 아직 탐색하지 않았습니다. 통합 화면의 \"{who} 탐색\" 버튼이나 <code>uv run eastshift crawl &lt;{who} 주소&gt; --out {html._e(str(cd))}</code></p></header>")
                return html.fragment("map", f"{app} map", body)
            key, sig = (app, kind, src), self._sig(app)
            with self._lock:
                hit = self._cache.get(key)
                if hit and hit[0] == sig:
                    return hit[1]
            page = screen_map.fragment(screen_map.build_from_crawl(app, cd, src))
            with self._lock:
                self._cache[key] = (sig, page)
            return page
        d = self.golden_root / app
        if not (d.is_dir() and any(d.glob("*.json"))):
            spec = projects.load(self.projects_file).get(app) or {}
            asis = (spec.get("asis") or {}).get("url") or "<as-is 주소>"
            body = (f"<header class='head'><div class='eyebrow'>{html._e(app)}</div><h1>골든이 아직 없습니다</h1>"
                    f"<p class='lede'>시나리오를 as-is에서 기록해야 개요·시나리오 승인·지도·보고서가 생깁니다.</p></header>"
                    f"<section><pre><code>uv run eastshift crawl {html._e(asis)} --out crawl/{html._e(app)}   # 화면을 훑어 시나리오 초안\n"
                    f"uv run pytest e2e/{html._e(app)} --base-url {html._e(asis)} --record golden/{html._e(app)}   # as-is에서 기록</code></pre></section>")
            return html.fragment(kind, f"{app} {kind}", body)
        key, sig = (app, kind, run), self._sig(app)
        with self._lock:
            hit = self._cache.get(key)
            if hit and hit[0] == sig:
                return hit[1]
        tests_dir = self.tests_root / app
        junit = ledger.run_dir(app) / run / "junit.xml" if run else None
        if run and (junit is None or not junit.exists()):
            body = (f"<header class='head'><div class='eyebrow'>{'Screen Map' if kind == 'map' else '검증 보고서'}</div><h1>{html._e(app)}</h1>"
                    f"<p class='lede'>실행 {html._e(run)}의 JUnit 사본이 없습니다. 이 실행은 원장에 JUnit을 남기기 전 것이거나, "
                    f"<code>--junitxml</code> 없이 실행됐습니다. 다시 비교하면 지도와 보고서가 나옵니다.</p></header>")
            return html.fragment(kind, f"{app} {kind}", body)
        if kind == "catalog":
            page = catalog.fragment(catalog.build(d, tests_dir))
        elif kind == "review":
            page = review.fragment(review.build(d, tests_dir))
        elif kind == "map":
            ca, ct = self.crawl_dir(app, "asis"), self.crawl_dir(app, "tobe")
            page = screen_map.fragment(screen_map.build(d, junit, tests_dir, ca if (ca / "graph.json").exists() else None, ct if (ct / "graph.json").exists() else None))
        else:
            st = oracle.status(d)
            mut = ledger.mutation_for(app, st.get("approved_at"), st.get("approval_id"))
            b = report.build(oracle_dir=d, junits=[junit] if junit else [], mutations=[mut] if mut else [])
            page = report.render_fragment(d, b, tests_dir)
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
            if len(parts) == 4 and parts[:2] == ["api", "app"] and parts[3] in ("init", "job"):
                return self._json(self.hub.init_status(self.hub._check(parts[2])))
            if len(parts) == 3 and parts[:2] == ["api", "app"]:
                return self._json(self.hub.app(parts[2]))
            if len(parts) == 3 and parts[0] == "page":
                frag = self.hub.page(parts[1], parts[2], (q.get("run") or [None])[0], (q.get("src") or ["compare"])[0])
                if q.get("fragment"):  # 통합 화면이 iframe 없이 끼워 넣을 조각
                    return self._json(frag)
                return self._send(html.assemble(frag).encode("utf-8"))
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

    def _same_origin(self) -> bool:
        """브라우저가 보내는 Origin 이 이 서버 자신이어야 한다. 같은 브라우저에 열린 다른 사이트가 127.0.0.1 로 POST 를 쏘는 것(탐색 시작, 프로젝트 삭제)을 막는다.
        Origin 이 없는 요청(curl 등 비브라우저)은 그대로 둔다: 서버가 127.0.0.1 에만 열려 있어 로컬 사용자뿐이다."""
        origin = self.headers.get("Origin")
        if not origin:
            return True
        u = urlsplit(origin)
        return u.hostname in ("127.0.0.1", "localhost", "::1") and (u.port or 80) == self.server.server_address[1]

    def do_POST(self):  # noqa: N802
        parts = [unquote(x) for x in urlsplit(self.path).path.strip("/").split("/") if x]
        if not self._same_origin():
            return self._json({"error": "다른 출처(origin)에서 온 요청은 받지 않습니다"}, 403)
        try:
            body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0)) or 0) or b"{}")
            if len(parts) == 4 and parts[:2] == ["api", "app"] and parts[3] == "approve":
                return self._json(self.hub.approve(parts[2], by=str(body.get("by", "")),
                                                   fingerprint=str(body.get("fingerprint", "")), note=str(body.get("note", ""))))
            if len(parts) == 4 and parts[:2] == ["api", "app"] and parts[3] == "init":
                return self._json(self.hub.init_project(parts[2], depth=int(body.get("depth") or 3)))
            if len(parts) == 4 and parts[:2] == ["api", "app"] and parts[3] == "crawl":
                return self._json(self.hub.start_crawl(parts[2], str(body.get("side") or "asis"), depth=int(body.get("depth") or 3)))
            if len(parts) == 4 and parts[:2] == ["api", "app"] and parts[3] == "compare":
                return self._json(self.hub.start_compare(parts[2]))
            if len(parts) == 4 and parts[:2] == ["api", "app"] and parts[3] == "mutate":
                return self._json(self.hub.start_mutate(parts[2]))
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
    print(f"eastshift ui · {url}  (프로젝트 {len(names)}개{': ' + ', '.join(names) if names else ' — 첫 화면에서 추가'}) — Ctrl+C로 종료")
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
.top{position:relative}
.where{position:absolute;left:50%;top:50%;transform:translate(-50%,-50%);display:flex;align-items:center;gap:8px;font-size:14px;color:var(--muted);white-space:nowrap;pointer-events:none}
.where b{color:var(--ink);font-weight:700;font-size:15px}
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
#view > main{height:100%;overflow:auto;max-width:none;padding-block:22px 60px;gap:22px}
/* 화면 조각 컨테이너 (iframe 대신): 탭 하나가 이 안에 들어간다. 조각의 CSS 는 .pg-<kind> 로 가둬져 있다 */
.pg{height:100%;overflow:auto;position:relative;background:var(--bg)}
.pg.inline{height:auto;overflow:visible}.pg.inline main{padding:0;height:auto}.rslot{display:block}
.fold summary{cursor:pointer;font-weight:600;font-size:16px;padding:6px 0;list-style:none}.fold summary::-webkit-details-marker{display:none}.fold summary::before{content:'▸ ';color:var(--muted)}.fold[open] summary::before{content:'▾ '}.fold summary small{font-weight:400;color:var(--faint);margin-left:8px}.fold .tbl{margin-top:10px}.pg.loading{display:flex;align-items:center;justify-content:center;color:var(--muted)}
main.hist h2{font-size:16px;font-weight:600}
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
/* 화면 지도 출처 바 */
.mapwrap{display:flex;flex-direction:column;height:100%}
.srcbar{display:flex;align-items:center;gap:6px;padding:8px 14px;border-bottom:1px solid var(--line);background:var(--surface);flex:none}
.srcbar > button:not(.btn){font:inherit;font-size:13px;font-weight:600;padding:6px 12px;border:1px solid var(--line);border-radius:999px;background:var(--surface);color:var(--muted);cursor:pointer;display:inline-flex;align-items:center;gap:7px}
.srcbar > button:not(.btn):hover{border-color:var(--accent);color:var(--accent)}.srcbar > button.on{background:var(--accent-soft);color:var(--accent);border-color:transparent}
.srcbar > button small{font-size:11px;font-weight:500;color:var(--faint)}
.srcbar .sp{flex:1}
.srcbar .slot{display:flex;align-items:center;margin-left:6px}.srcbar .slot .pgh{flex-wrap:nowrap}.srcbar .slot .chips{flex-wrap:nowrap}
.mapbody{flex:1;min-height:0;position:relative}.mapbody .pg{height:100%}
.mapbody main.init{height:100%;overflow:auto;padding-block:28px 60px;box-sizing:border-box}
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
const TABS = [['map','Screen Map'],['overview','시나리오'],['review','시나리오 승인'],['runs','실행']];  // 실행 = 실행 목록 + 고른 실행의 판정(옛 이력·검증 보고서)  // 왼쪽 메뉴 순서. 첫 탭(Screen Map)이 프로젝트를 열 때의 기본
// 위 막대 가운데: 앱 이름 › 탭 (지도는 출처까지). 페이지 조각에는 제목이 없다
function setWhere(extra){
  const tab = (TABS.find(t => t[0] === state.tab) || ['', ''])[1];
  $('#where').innerHTML = state.app ? `<b>${esc(state.app)}</b><span class="sepc">›</span><span>${esc(tab)}</span>${extra ? `<span class="sepc">·</span><span>${esc(extra)}</span>` : ''}` : '';
}
// 쪽 나누기: items 를 per 개씩 보이고 after 뒤에 ‹ 1 2 3 › 를 붙인다. focus 요소가 있으면 그 요소가 있는 쪽부터. 스크롤이 길어지지 않게
function pager(items, per, after, focus){
  if (items.length <= per) return;
  const n = Math.ceil(items.length / per);
  let page = focus ? Math.floor(items.indexOf(focus) / per) : 0;
  const foot = document.createElement('div'); foot.className = 'pager'; after.after(foot);
  function draw(){
    items.forEach((el, i) => { el.hidden = Math.floor(i / per) !== page; });
    const nums = [...Array(n).keys()].filter(i => n <= 7 || i === 0 || i === n - 1 || Math.abs(i - page) <= 1);
    let last = -1, btns = '';
    for (const i of nums){ if (i - last > 1) btns += '<span class="gap">…</span>'; btns += `<button type="button" class="${i === page ? 'on' : ''}" data-p="${i}">${i + 1}</button>`; last = i; }
    foot.innerHTML = `<span class="rng">${page * per + 1}–${Math.min((page + 1) * per, items.length)} / ${items.length}</span>`
      + `<button type="button" data-d="-1" ${page === 0 ? 'disabled' : ''} aria-label="이전 쪽">‹</button>${btns}<button type="button" data-d="1" ${page === n - 1 ? 'disabled' : ''} aria-label="다음 쪽">›</button>`;
    foot.querySelectorAll('[data-p]').forEach(b => b.onclick = () => { page = +b.dataset.p; draw(); });
    foot.querySelectorAll('[data-d]').forEach(b => b.onclick = () => { page = Math.max(0, Math.min(n - 1, page + +b.dataset.d)); draw(); });
  }
  draw();
}
// 머리의 상태 칩 (화면 조각들과 같은 모양: html.chip)
const chip = (cls, label, n, sub, lead, title) => `<span class="fchip ${cls}${lead ? ' lead' : ''}"${title ? ` title="${esc(title)}"` : ''}>${cls === 'info' ? '' : '<i></i>'}${esc(label)}${n != null ? `<b>${esc(n)}</b>` : ''}${sub ? `<small>${esc(sub)}</small>` : ''}</span>`;
const KIND = {golden_diff:'as-is와 다름', assert:'확인 값 실패', drift:'기대값 변경', error:'실행 못 함', same:'같음', accepted_diff:'승인된 차이'};
const casePill = c => c.kind === 'accepted_diff' ? '<span class="pill warn">승인된 차이</span>' : c.status === 'pass' ? '<span class="pill ok">같음</span>' : `<span class="pill bad">${esc(KIND[c.kind]||c.kind)}</span>`;
let apps = [], state = {app:null, tab:'map', run:null, src:null, sub:''}, data = null, initTimer = null;

const SRC = [['asis','as-is 탐색'],['tobe','to-be 탐색'],['compare','to-be 비교 (as-is 기준)']];
// 해시 = #app/tab/run/src[/sub…]. sub 는 끼워 넣은 화면 조각의 안쪽 이동(지도의 상세 라우트, 검토의 시나리오)으로, 조각이 ctx.setSub 로 쓰고 ctx.getSub 로 읽는다 (인코딩된 그대로)
function parseHash(){ const parts = location.hash.replace(/^#\/?/, '').split('/'); const [app, tab, run, src] = parts.slice(0, 4).map(decodeURIComponent); return {app: app||null, tab: ({history:'runs', report:'runs'})[tab] || tab || 'map', run: run && run !== '-' ? run : null, src: src && src !== '-' ? src : null, sub: parts.slice(4).join('/')}; }
function setHash(){ const parts = [state.app, state.tab, state.run || '-', state.src || (state.sub ? '-' : null)]; while (parts.length && !parts[parts.length-1]) parts.pop(); const h = parts.map(encodeURIComponent).join('/') + (state.sub ? '/' + state.sub : ''); if (location.hash.replace(/^#\/?/, '') !== h) location.hash = h; }

// ---- 화면 조각 끼우기 (iframe 대신): 서버의 /page/<app>/<kind>?fragment=1 이 {html, css, js} 를 주고, css 는 .pg-<kind> 로 가둬져 있고 js 는 (root, ctx) 함수 본문이다 ----
let mounted = null, mountSeq = 0;
function destroyPage(){ if (mounted){ for (const f of mounted.cleanups) { try { f(); } catch (_) {} } mounted = null; } }
async function mountPage(container, kind, q, inline){
  destroyPage();
  const seq = ++mountSeq;
  container.innerHTML = '<div class="pg loading">불러오는 중…</div>';
  let frag;
  try { const r = await fetch(`/page/${encodeURIComponent(state.app)}/${kind}${q}${q ? '&' : '?'}fragment=1`); if (!r.ok) throw new Error(await r.text()); frag = await r.json(); }
  catch (e) { if (seq === mountSeq) container.innerHTML = `<div class="empty">화면을 불러오지 못했습니다: ${esc(e.message)}</div>`; return; }
  if (seq !== mountSeq) return;  // 기다리는 동안 다른 탭으로 갔다
  let st = document.getElementById('css-' + kind); if (!st){ st = document.createElement('style'); st.id = 'css-' + kind; document.head.appendChild(st); } st.textContent = frag.css || '';
  const root = document.createElement('div'); root.className = 'pg pg-' + kind + (inline ? ' inline' : ''); root.innerHTML = frag.html;
  container.innerHTML = ''; container.appendChild(root);
  const cleanups = [], subs = [];
  const ctx = {getSub: () => state.sub || '', setSub: s => { state.sub = s || ''; setHash(); }, onSub: fn => subs.push(fn),
    listen: (t, ev, fn, o) => { t.addEventListener(ev, fn, o); cleanups.push(() => t.removeEventListener(ev, fn, o)); },
    onDestroy: fn => cleanups.push(fn), approved: () => loadApps().then(route),
    go: tab => { state.tab = tab; state.sub = ''; setHash(); },  // 다른 탭으로
    refresh: () => loadApps().then(() => { state.run = null; return loadApp(); }).then(render),  // 새 실행이 생겼을 때: 최신 실행으로 다시 그린다
    app: state.app,
    slot: container.closest('.mapwrap') ? container.closest('.mapwrap').querySelector('#mapslot') : null};  // 조각이 머리 칩을 붙일 자리 (출처 바 오른쪽)
  mounted = {kind, root, subs, cleanups, at: {app: state.app, tab: state.tab, run: state.run || null, src: state.src || null}};  // 어느 상태로 끼웠는지 (route 가 다시 만들지 판단)
  if (frag.js){ try { new Function('root', 'ctx', frag.js)(root, ctx); } catch (e) { console.error(e); } }
}
function mapSrc(){ return state.src && data.maps && (state.src in data.maps) ? state.src : (data.golden ? 'compare' : (data.maps && data.maps.asis ? 'asis' : (data.maps && data.maps.tobe ? 'tobe' : 'asis'))); }
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
  if (!state.app){ c.innerHTML = '<span class="sepc">›</span><b>프로젝트</b>'; $('#where').innerHTML = ''; $('#runsel').hidden = true; return; }
  c.innerHTML = `<span class="sepc">›</span><select id="switch" aria-label="프로젝트 바꾸기">${apps.map(a => `<option value="${esc(a.app)}" ${a.app === state.app ? 'selected' : ''}>${esc(a.app)}</option>`).join('')}</select>`;
  $('#switch').onchange = e => { state.app = e.target.value; state.run = null; state.src = null; state.sub = ''; setHash(); };
  setWhere();
  // 실행 선택의 보이기/숨기기는 loadApp·renderMap 이 정한다 (여기서 먼저 보이게 하면 자료를 받는 동안 깜빡인다)
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
  if (!a.golden) next = `<div class="next"><div class="nx"><span>다음: Screen Map 만들기 — as-is를 ${a.scenarios ? '기록' : '탐색해 시나리오 초안을 만들고 기록'}합니다</span><button class="btn primary sm" data-init>Screen Map 만들기</button></div>`
    + `<code>${a.scenarios ? '' : `uv run eastshift crawl ${esc(asis)} --out crawl/${esc(a.app)}\n`}uv run pytest e2e/${esc(a.app)} --base-url ${esc(asis)} --record golden/${esc(a.app)}</code></div>`;
  else if (!a.ok) next = `<div class="next">다음: 시나리오 승인 탭에서 시나리오를 확인하고 이름을 입력해 승인 (사람)</div>`;
  else if (!a.runs) next = `<div class="next">다음: to-be 비교<code>uv run pytest e2e/${esc(a.app)} --base-url ${esc(tobe)} --compare golden/${esc(a.app)} --junitxml reports/junit-${esc(a.app)}.xml</code></div>`;
  return `<div class="proj" data-app="${esc(a.app)}"><div class="hd"><b data-open>${esc(a.app)}</b>${pill}<span class="sp"></span>${a.registered ? '' : '<span class="pill warn" title="eastshift.json에 없음. golden/ 또는 e2e/ 에서 발견">미등록</span>'}</div>
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
    for (const el of card.querySelectorAll('[data-open], [data-init]')) el.onclick = () => { state.app = name; state.tab = 'map'; state.run = null; state.sub = ''; setHash(); };
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
  sel.value = state.run || ''; sel.disabled = !data.runs.length; $('#runsel').hidden = !data.runs.length || (state.tab === 'map' && mapSrc() !== 'compare');
  $('#tabs').hidden = false;
  $('#tabs').innerHTML = `<a data-back><span>← 프로젝트</span></a><div class="sep">${esc(state.app)}</div>` + TABS.map(([k, l]) => {
    const n = k === 'runs' ? data.runs.length : (k === 'overview' ? data.tests.length : '');
    return `<a data-tab="${k}" class="${k === state.tab ? 'on' : ''}"><span>${l}</span>${n !== '' ? `<small>${n}</small>` : ''}</a>`;
  }).join('');
  for (const a of $('#tabs').querySelectorAll('a[data-tab]')) a.onclick = () => { state.tab = a.dataset.tab; state.sub = ''; setHash(); };
  $('#tabs a[data-back]').onclick = () => { state = {app:null, tab:'map', run:null, src:null, sub:''}; location.hash = ''; };
}

function render(){
  const v = $('#view');
  clearTimeout(initTimer);
  if (!state.app){ renderList(v); return; }
  if (state.tab === 'map'){ renderMap(v); return; }
  const q = state.run ? '?run=' + encodeURIComponent(state.run) : '';
  const page = {overview:'catalog', review:'review'}[state.tab];
  if (page){ mountPage(v, page, q); return; }
  renderRuns(v);
}

// ---- 화면 지도: 출처 셋. as-is 탐색 / to-be 탐색 은 crawl 결과, to-be 비교 는 골든 + 선택한 실행 + 두 탐색(미개발·새 화면). 없으면 만드는 화면 ----
function renderMap(v){
  const src = mapSrc(), m = data.maps || {};
  const bar = `<div class="srcbar">${SRC.map(([k, l]) => `<button class="${k === src ? 'on' : ''}" data-src="${k}">${l}${m[k] ? '' : `<small>${k === 'compare' ? '골든 전' : '탐색 전'}</small>`}</button>`).join('')}
    <span class="sp"></span>${src !== 'compare' && m[src] ? `<button class="btn sm" data-recrawl>다시 탐색</button>` : ''}<div class="slot" id="mapslot"></div></div>`;  // slot: 지도 조각이 자기 상태 칩·(i) 를 여기로 옮겨 놓는다
  v.innerHTML = `<div class="mapwrap">${bar}<div class="mapbody" id="mapbody"></div></div>`;
  for (const b of v.querySelectorAll('[data-src]')) b.onclick = () => { state.src = b.dataset.src; state.sub = ''; setHash(); };
  const rc = v.querySelector('[data-recrawl]'); if (rc) rc.onclick = () => renderJob($('#mapbody'), src, true);
  $('#runsel').hidden = !data.runs.length || src !== 'compare';
  const body = $('#mapbody');
  if (src === 'compare' ? !data.golden : !m[src]){ renderJob(body, src === 'compare' ? 'init' : src, false); return; }
  const q = '?src=' + src + (src === 'compare' && state.run ? '&run=' + encodeURIComponent(state.run) : '');
  setWhere(SRC.find(x => x[0] === src)[1]);
  mountPage(body, 'map', q);
}

// 작업 화면: kind = init (골든 시나리오 지도: 탐색 → 초안 → 기록) | asis | tobe (탐색만). 서버가 순서대로 돌리고 단계·로그를 2초마다 보여준다
async function renderJob(v, kind, force){
  const p = data.project || {}, side = kind === 'tobe' ? 'tobe' : 'asis', url = (p[side]||{}).url || '', who = side === 'asis' ? 'as-is' : 'to-be';
  let job = await api('/api/app/' + encodeURIComponent(state.app) + '/job');
  const mine = job.kind === kind;  // 다른 종류의 작업이 돌고 있으면 기다린다
  const stepsHtml = steps => `<div class="isteps">${steps.map((s, i) => `<div class="istep ${s.state}"><span class="no">${i+1}</span><span>${esc(s.label)}</span><span class="st">${{wait:'대기', run:'실행 중…', done:'완료', fail:'실패', skip:'건너뜀'}[s.state]}</span></div>`).join('')}</div>`;
  const crawlDir = side === 'asis' ? `crawl/${state.app}/` : `crawl/${state.app}-tobe/`;
  const plan = kind === 'init' ? [
      {state: data.scenarios || (data.maps||{}).asis ? 'skip' : 'wait', label: data.scenarios ? `탐색 건너뜀 — e2e/${state.app}/ 에 시나리오 ${data.scenarios}개` : (data.maps||{}).asis ? `탐색 건너뜀 — ${crawlDir} 에 as-is 탐색 결과가 이미 있음` : `as-is 화면 탐색 (crawl) → ${crawlDir}`},
      {state: data.scenarios ? 'skip' : 'wait', label: `시나리오 초안을 e2e/${state.app}/ 로`},
      {state: 'wait', label: `as-is(${url || '주소 없음'})에서 골든 기록 → golden/${state.app}/`}]
    : [{state: 'wait', label: `${who}(${url || '주소 없음'}) 화면 탐색 (crawl) → ${crawlDir}`}];
  const title = kind === 'init' ? '아직 to-be 비교 지도가 없습니다' : (force ? `${who}를 다시 탐색합니다` : `아직 ${who} 탐색 지도가 없습니다`);
  const lede = kind === 'init'
    ? '이 지도는 as-is에서 기록한 골든 시나리오를 뼈대로, 비교 실행에서 다르게 동작한 화면(빨강)·to-be 탐색에 없는 미개발 화면(노랑)·to-be에만 있는 새 화면(파랑)을 표시합니다. 아래 순서를 서버가 대신 돌립니다. 기록이 끝나면 지도가 바로 보이고, <b>승인</b>은 그 뒤 사람이 시나리오 승인 탭에서 합니다.'
    : `이 지도는 ${who}를 탐색(eastshift crawl)해 찾은 화면을 그대로 잇습니다. 시나리오나 비교와 무관하게 ${who}에 어떤 화면·팝업·드로워가 있는지 봅니다.`;
  const needCrawl = kind !== 'init' || !(data.scenarios || (data.maps||{}).asis);
  const running = job.running, busyOther = running && !mine;
  v.innerHTML = `<main class="init"><div class="card"><div class="eyebrow">Screen Map · ${esc(SRC.find(x => x[0] === (kind === 'init' ? 'compare' : kind))[1])}</div><h1>${title}</h1>
    <p class="lede">${lede}</p>
    <div id="isteps">${stepsHtml(mine && job.steps ? job.steps : plan)}</div>
    <div class="irow"><label>${who} 주소</label>${url ? `<b class="mono">${esc(url)}</b>` : `<span class="pill warn">없음 — 프로젝트 설정에서 적으세요</span> <button class="btn sm" id="goset">설정</button>`}
      <label>탐색 깊이</label><select id="depth" ${needCrawl ? '' : 'disabled'}><option value="2">2 (빠름)</option><option value="3" selected>3 (기본)</option><option value="4">4 (넓게)</option></select></div>
    ${needCrawl ? `<div class="notice"><b>주의</b> 탐색은 저장·확정 버튼도 실제로 누릅니다. 테스트 DB·테스트 계정의 ${who}에서만 돌리세요. 삭제·결제·발송 같은 버튼은 기본 금지 목록으로 누르지 않습니다.</div>` : ''}
    ${busyOther ? `<div class="notice">다른 작업(${esc(job.kind)})이 실행 중입니다. 끝나면 다시 누르세요.</div>` : ''}
    <div class="irow"><button class="btn primary" id="doinit" ${!url || running ? 'disabled' : ''}>${running && mine ? '실행 중…' : (kind === 'init' ? 'Screen Map 만들기' : `${who} 탐색`)}</button></div>
    <div id="ierr" class="err">${esc(mine && job.error || '')}</div>
    <pre class="log" id="ilog" ${mine && (job.log||[]).length ? '' : 'hidden'}>${esc(mine ? (job.log||[]).join('\n') : '')}</pre></div></main>`;
  const goset = $('#goset'); if (goset) goset.onclick = () => openEditor(apps.find(a => a.app === state.app));
  const label = $('#doinit').textContent;
  $('#doinit').onclick = async () => {
    $('#ierr').textContent = ''; $('#doinit').disabled = true; $('#doinit').textContent = '실행 중…';
    try {
      if (kind === 'init') await api('/api/app/' + encodeURIComponent(state.app) + '/init', {depth: +$('#depth').value});
      else await api('/api/app/' + encodeURIComponent(state.app) + '/crawl', {side, depth: +$('#depth').value});
      poll();
    } catch (e) { $('#ierr').textContent = e.message; $('#doinit').disabled = false; $('#doinit').textContent = label; }
  };
  async function poll(){
    const j = await api('/api/app/' + encodeURIComponent(state.app) + '/job');
    if (j.steps){ $('#isteps').innerHTML = stepsHtml(j.steps); }
    const log = $('#ilog'); log.hidden = !(j.log||[]).length; log.textContent = (j.log||[]).join('\n'); log.scrollTop = log.scrollHeight;
    $('#ierr').textContent = j.error || '';
    if (j.running){ initTimer = setTimeout(poll, 2000); return; }
    if (j.ok){ await loadApps(); await loadApp(); render(); return; }  // 산출물이 생겼으니 지도를 그린다
    $('#doinit').disabled = false; $('#doinit').textContent = '다시 시도';
  }
  if (running && mine) poll();
}

function renderRuns(v){
  const d = data, st = d.oracle, runs = d.runs;
  const last = runs[runs.length-1];
  const stale = last && st.approval_id && last.approval_id !== st.approval_id;
  const chips = [
    st.ok ? chip('ok', '승인됨', null, `${st.approved_by} · ${(st.approved_at||'').slice(0,16)}`, true) : chip('warn', d.golden ? '승인 필요' : '골든 없음', null, d.golden ? '시나리오 승인 탭에서' : '', true),
    chip('info', '비교 실행', runs.length + '회', last ? '마지막 ' + last.finished.slice(5,16) : '아직 없음'),
  ];
  let h = `<main class="hist"><header class="pgh"><div class="chips">${chips.join('')}</div><span class="ihelp" tabindex="0" role="note" aria-label="설명"><i>i</i><span class="tip"><p>to-be 비교 실행마다 원장에 결과가 남습니다. 표에서 실행을 고르면 그 실행의 판정(믿을 수 있는가 → 다른 점 → 결함 탐지 → 업무 범위)이 아래에 나오고, Screen Map 도 그 실행 기준으로 그려집니다.</p><div class="facts"><span><b>원장</b> runs/${esc(d.app)}</span><span><b>파일</b> 같은 판정을 markdown 으로: eastshift report (에이전트·CI용)</span></div></span></span></header>`;
  if (stale) h += `<div class="notice"><b>주의</b> 마지막 비교는 이전 승인본(${esc(last.approved_at||'없음')})으로 실행됐습니다. 현재 승인본으로 다시 비교하세요.</div>`;
  if (!runs.length){
    const tobe = ((d.project||{}).tobe||{}).url || '<to-be>';
    h += `<div class="empty">to-be 비교 실행 기록이 없습니다.<br><code>uv run pytest e2e/${esc(d.app)} --base-url ${esc(tobe)} --compare golden/${esc(d.app)} --junitxml reports/junit-${esc(d.app)}.xml</code></div></main>`; v.innerHTML = h; return; }

  h += `<section><h2>실행 <small class="mono" style="color:var(--faint);font-weight:400">최근 → 오래된 · 번호는 실행 순서 · 행을 누르면 아래에 그 실행의 판정</small></h2><div class="tbl"><table><thead><tr><th>#</th><th>끝난 시각</th><th>대상</th><th>승인본</th><th>결과</th><th>지난 실행 대비</th><th></th></tr></thead><tbody>`;
  const rrows = [];  // 최근 실행이 맨 위 (지난 실행 대비·번호는 시간순 그대로)
  runs.forEach((r, i) => {
    const dl = r.delta, chips = [];
    if (i > 0){
      if (dl.newly_passing.length) chips.push(`<span class="chip up">+${dl.newly_passing.length} 통과로</span>`);
      if (dl.newly_failing.length) chips.push(`<span class="chip down">−${dl.newly_failing.length} 새로 실패</span>`);
      if (dl.still_failing.length) chips.push(`<span class="chip same">${dl.still_failing.length} 계속 실패</span>`);
      if (dl.new_tests.length) chips.push(`<span class="chip same">새 테스트 ${dl.new_tests.length}</span>`);
      if (!chips.length) chips.push('<span class="chip same">변화 없음</span>');
    } else chips.push('<span class="chip same">첫 실행</span>');
    const ap = r.approval_id && r.approval_id === st.approval_id ? `<span class="pill ok">현재</span>` : `<span class="pill warn" title="${esc(r.approved_at||'승인 없음')}">이전 승인본</span>`;
    rrows.push(`<tr class="rrow ${r.stamp === state.run ? 'on' : ''}" data-run="${r.stamp}"><td class="mono">${i+1}</td><td class="mono">${esc(r.finished)}</td><td class="mono">${esc(r.target)}</td><td>${ap}</td>`
       + `<td>${r.totals.fail ? `<span class="pill bad">${r.totals.fail} 다름</span>` : Object.values(r.cases).some(c => c.kind === 'accepted_diff') ? '<span class="pill warn">승인된 차이 포함</span>' : '<span class="pill ok">모두 같음</span>'} <span class="mono" style="color:var(--faint);font-size:12px">/ ${r.totals.pass + r.totals.fail}</span></td>`
       + `<td>${chips.join('')}</td><td><div class="actions">${r.junit ? `<button data-go="map" data-run="${r.stamp}">Screen Map</button>` : '<span style="color:var(--faint);font-size:12px">JUnit 없음</span>'}</div></td></tr>`);
  });
  h += rrows.reverse().join('');
  h += `</tbody></table></div></section>`;

  // 고른 실행의 판정: 검증 보고서 조각을 여기에 끼운다 (믿을 수 있는가 · 다른 점 · 승인된 차이 · 결함 탐지 · 업무 범위)
  h += `<section id="runreport" class="rslot"></section>`;

  if (d.mutations.length){
    h += `<section><h2>결함 탐지 측정 <small class="mono" style="color:var(--faint);font-weight:400">승인본마다 한 번 · 현재 승인본 결과가 위 판정에 쓰인다</small></h2><div class="tbl"><table><thead><tr><th>측정 시각</th><th>탐지율</th><th>오류</th><th>승인본</th></tr></thead><tbody>`
       + d.mutations.map(m => `<tr><td class="mono">${esc(m.generated_at)}</td><td><b class="mono">${m.score == null ? '—' : Math.round(m.score*100) + '%'}</b> <span class="mono" style="color:var(--faint);font-size:12px">${m.killed}/${m.total}</span></td><td class="mono">${m.errors}</td><td>${m.current ? '<span class="pill ok">현재</span>' : `<span class="pill warn" title="${esc(m.approved_at||'')}">이전 승인본</span>`}</td></tr>`).join('')
       + `</tbody></table></div></section>`;
  }

  h += `<details class="fold"><summary>시나리오 × 실행 격자 <small class="mono">${d.tests.length} × ${runs.length} · 열 번호를 누르면 그 실행 선택</small></summary><div class="tbl matrix"><table><thead><tr><th>시나리오</th>${runs.map((r,i) => `<th class="run ${r.stamp === state.run ? 'on' : ''}" data-run="${r.stamp}" title="${esc(r.finished)} · ${esc(r.target)}">${i+1}</th>`).join('')}<th>마지막</th></tr></thead><tbody>`;
  for (const t of d.tests){
    const cells = runs.map(r => { const c = r.cases[t.name]; return `<td class="c" title="${esc(r.finished)}${c ? ' · ' + esc(KIND[c.kind]||c.kind) : ''}"><i class="${c ? (c.status === 'pass' ? 'p' : 'f') : ''}"></i></td>`; }).join('');
    const lc = last.cases[t.name];
    h += `<tr><td class="t"><b>${esc(t.title)}</b><small>${esc(t.name)}</small></td>${cells}<td>${lc ? casePill(lc) : '<span class="pill none">비교 전</span>'}</td></tr>`;
  }
  h += `</tbody></table></div></details>`;
  h += `</main>`;
  v.innerHTML = h;
  for (const el of v.querySelectorAll('[data-run]')){
    el.addEventListener('click', e => { const go = e.target.dataset.go; state.run = el.dataset.run; if (go) state.tab = go; state.sub = ''; setHash(); });
  }
  for (const t of v.querySelectorAll('.tbl')) pager([...t.querySelectorAll('tbody tr')], 10, t, t.querySelector('tbody tr.on'));  // 표는 10줄씩
  if (state.run) mountPage($('#runreport'), 'report', '?run=' + encodeURIComponent(state.run), true);
}

async function route(){
  const h = parseHash();
  if (h.app && !apps.some(a => a.app === h.app)) h.app = null;  // 모르는 이름이면 목록으로
  // 끼운 조각의 안쪽 이동(sub)만 바뀐 것이면 조각을 다시 만들지 않고 조각에게만 알린다. 비교 기준은 조각을 끼울 때의 상태 (탭 버튼 등은 setHash 전에 state 를 먼저 바꾼다)
  const same = mounted && mounted.at.app === h.app && mounted.at.tab === h.tab && mounted.at.run === (h.run || null) && mounted.at.src === (h.src || null);
  state = {app: h.app, tab: h.tab, run: h.run, src: h.src, sub: h.sub || ''};
  renderTop();
  if (same){ for (const f of mounted.subs) f(); return; }
  destroyPage();
  if (state.app) await loadApp();
  render();
  if (!apps.length && !document.querySelector('.modal')) openEditor(null);  // 첫 방문: 바로 추가 창
}
$('#run').addEventListener('change', e => { state.run = e.target.value || null; state.sub = ''; setHash(); });
$('#brand').addEventListener('click', e => { e.preventDefault(); location.hash = ''; });
window.addEventListener('message', e => { if (e.data && e.data.eastshift === 'approved') loadApps().then(route); });  // 검토 화면에서 승인되면 상태 갱신
window.addEventListener('hashchange', route);
loadApps().then(route);
"""

SHELL = (f"<!doctype html><html lang='ko'><head><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1,viewport-fit=cover'>"
         f"<title>EastShift</title>{html.FONTS}<style>{html.CSS}{HUB_CSS}</style></head><body class='hub'>"
         "<header class='top'><a class='brand' id='brand' href='#'>EastShift</a><div class='crumb' id='crumb'></div><div class='where' id='where'></div>"
         "<div class='runsel' id='runsel' hidden><label for='run'>실행</label><select id='run' aria-label='실행 선택'></select></div></header>"
         "<div class='frame'><nav class='side' id='tabs' aria-label='화면' hidden></nav><div id='view'></div></div>"
         f"<script>{HUB_JS}</script></body></html>")
