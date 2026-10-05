from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path
from xml.sax.saxutils import escape

from .observe import CompareOptions
from .runner import RunResult, Runner


def load_dotenv(path: Path = Path(".env")) -> None:
    """KEY=VALUE 줄을 환경변수로. 이미 설정된 변수는 덮어쓰지 않는다 (셸 export가 우선)."""
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip().removeprefix("export ").strip(), value.strip().strip("'\""))


def build_parser() -> argparse.ArgumentParser:
    """명령 정의 전부. main 과 문서 대조 테스트(tests/test_docs.py)가 같이 쓴다."""
    ap = argparse.ArgumentParser(prog="east2west", description="East2West: Playwright as-is/to-be equivalence tests with an approved oracle, plus a natural-language YAML runner (Jev) and a crawler")
    sub = ap.add_subparsers(dest="cmd", required=True)
    run = sub.add_parser("run", help="run one or more scenario YAML files")
    run.add_argument("scenarios", nargs="+", type=Path)
    run.add_argument("--headed", action="store_true")
    run.add_argument("--no-cache", action="store_true", help="always ask Jev; do not read or write the replay cache")
    run.add_argument("--margin", type=float, default=0.1, help="minimum top1-top2 probability gap to act (default 0.1)")
    run.add_argument("--max-candidates", type=int, default=60)
    run.add_argument("--settle-ms", type=int, default=1500)
    run.add_argument("--storage-state", type=Path, default=None, help="Playwright storage_state JSON (로그인 상태 재사용). 시나리오의 storage_state 키가 우선")
    run.add_argument("--replay-only", action="store_true", help="CI 모드: Jev를 절대 호출하지 않고 캐시만 재생. 캐시 미스는 실패")
    run.add_argument("--base-url", default=os.environ.get("EAST2WEST_BASE_URL"), help="goto 상대 경로의 기준 URL (기본: EAST2WEST_BASE_URL)")
    run.add_argument("--cache-dir", type=Path, default=Path(".east2west-cache"), help="재생 캐시 디렉터리 (기본 .east2west-cache)")
    run.add_argument("--record", type=Path, default=None, metavar="DIR", help="스텝별 관찰값을 골든으로 저장 (as-is에서 실행)")
    run.add_argument("--compare", type=Path, default=None, metavar="DIR", help="골든과 스텝별 관찰값 비교 (to-be에서 실행). 차이가 있으면 DIFF")
    run.add_argument("--compare-ignore", action="append", default=[], metavar="REGEX", help="비교 전에 <masked>로 바꿀 정규식 (반복 가능)")
    run.add_argument("--compare-url", action="store_true", help="URL 경로·쿼리와 제목도 비교")
    run.add_argument("--compare-unordered", action="store_true", help="화면 내용 줄 순서를 무시하고 비교")
    run.add_argument("--junit", type=Path, default=None, metavar="PATH", help="JUnit XML 결과 파일 (CI 리포트용)")
    run.add_argument("--triage", action="store_true", help="실패·diff 스텝의 원인을 Jev로 분류 (ui_changed|real_defect|environment|timing|test_bug). API 키 필요, --replay-only와 같이 못 씀")
    run.add_argument("--workers", type=int, default=1, help="동시에 돌릴 브라우저 수 (기본 1). 시나리오·matrix 행은 서로 독립이라 나눠 돌려도 결과는 같다. 서버 상태를 바꾸는 시나리오나 로그인 세션 하나를 나눠 쓰는 경우는 1")
    tr = sub.add_parser("triage", help="이미 만들어진 리포트 JSON의 실패·diff 스텝 원인을 Jev로 분류 (브라우저 없이). <리포트>-triage.json에 저장")
    tr.add_argument("reports", nargs="+", type=Path, help="east2west run이 남긴 reports/<stem>-<시각>.json")
    ap_status = sub.add_parser("oracle-status", help="오라클 승인 상태와 마스킹 규칙 감사 (승인은 기록·비교·결함 탐지가 자동으로 한다)")
    ap_status.add_argument("oracle_dir", type=Path)
    mut = sub.add_parser("mutate", help="결함 주입으로 Playwright 테스트의 탐지력 측정")
    mut.add_argument("targets", nargs="+", help="pytest 대상 (e2e/<app>)")
    mut.add_argument("--base-url", default=os.environ.get("EAST2WEST_BASE_URL"), required=not os.environ.get("EAST2WEST_BASE_URL"))
    mut.add_argument("--compare", type=Path, default=None, help="오라클 디렉터리. 없으면 expect만으로 측정")
    mut.add_argument("--workers", type=int, default=4)
    mut.add_argument("--max-per-op", type=int, default=5, help="(응답 경로, 연산자)당 최대 결함 수")
    mut.add_argument("--max-mutants", type=int, default=0, help="전체 결함 수 상한 (경로·연산자에 걸쳐 고르게 남긴다). 0이면 제한 없음")
    mut.add_argument("--out", type=Path, default=None)
    rep = sub.add_parser("report", help="산출물만으로 검증 보고서(markdown) 생성. 사람이 보는 화면은 east2west ui 실행 탭의 판정")
    rep.add_argument("--oracle", type=Path, required=True)
    rep.add_argument("--junit", type=Path, action="append", default=[], help="pytest --junitxml 결과 (여러 개 가능)")
    rep.add_argument("--mutation", type=Path, action="append", default=[], help="east2west mutate 결과 JSON (여러 개 가능)")
    rep.add_argument("--out", type=Path, required=True, help="보고서 markdown 경로")
    rt = sub.add_parser("routes", help="소스에서 라우트(화면 주소) 목록을 뽑는다 (실행 없이 정규식). Screen Map이 '코드에는 있는데 탐색·시나리오가 못 간 화면'을 회색으로 표시하는 잣대")
    rt.add_argument("src", type=Path, help="as-is 또는 to-be 소스 폴더 (단일 파일 앱이면 파일)")
    rt.add_argument("--out", type=Path, required=True, help="crawl/<app>/routes.json (as-is) 또는 crawl/<app>-tobe/routes.json (to-be)")
    cr = sub.add_parser("crawl", help="시작 화면에서 동작을 모두 눌러 보고 흐름 그래프와 시나리오를 만든다 (Jev 호출 없음)")
    cr.add_argument("start", help="시작 URL (상대 경로면 --base-url 기준, 시나리오 goto에 그대로 쓴다)")
    cr.add_argument("--out", type=Path, required=True, help="graph.json, graph.md, 스크린샷, 시나리오 YAML을 둘 디렉터리")
    cr.add_argument("--fixtures", type=Path, default=None, help="inputs: {입력칸 이름: 값}, deny: 누르지 않을 이름 정규식")
    cr.add_argument("--cache-dir", type=Path, default=None, help="시나리오 재생 캐시 디렉터리 (기본 <out>/cache)")
    cr.add_argument("--depth", type=int, default=3, help="시작 화면에서 몇 번의 전이까지 탐색할지 (기본 3)")
    cr.add_argument("--max-states", type=int, default=30, help="전체 상태 수 상한 (기본 30)")
    cr.add_argument("--max-actions", type=int, default=40, help="상태당 누를 동작 수 상한")
    cr.add_argument("--route-states", type=int, default=0,
                    help="라우트(주소, 숫자 조각은 {id})마다 상태 수 상한. 한 화면의 변형에 빠지지 않고 다른 화면으로 넓게 간다 (기본 0 = 없음)")
    cr.add_argument("--seeds", type=Path, default=None,
                    help="east2west routes 의 routes.json. 시작점에서 더 갈 곳이 없으면 눌러서 못 간 선언 화면을 주소로 직접 열어 거기서 다시 탐색한다")
    cr.add_argument("--group-min", type=int, default=3, help="비슷한 요소가 이 개수 이상이면 대표 하나만 누른다")
    cr.add_argument("--reps", type=int, default=3, help="목록성 화면(표 행·목록 항목)에서 누를 대표 행 상한. 분기 열(상태·유형 …) 값 조합마다 하나 (기본 3)")
    cr.add_argument("--classify", choices=("auto", "rule", "jev"), default="auto",
                    help="분기 열 고르기: rule=값 종류 규칙만, jev=Jev 분류(캐시, margin 미달이면 규칙), auto=API 키가 있으면 jev (기본). 픽스처 pick 이 늘 우선")
    cr.add_argument("--min-margin", type=float, default=0.2, help="Jev 분류를 믿는 최소 margin (1위-2위 확률 차). 미달이면 규칙 + graph.md 검토 표시")
    cr.add_argument("--settle-ms", type=int, default=400)
    cr.add_argument("--base-url", default=os.environ.get("EAST2WEST_BASE_URL"))
    cr.add_argument("--storage-state", type=Path, default=None)
    cr.add_argument("--headed", action="store_true")
    cr.add_argument("--dry-run", action="store_true", help="아무것도 누르지 않고 시작 화면에서 누를 것·안 누를 것·채울 입력칸만 보여 준다")
    tg = sub.add_parser("targets", help="스모크 대상 정하기: 화면별 조회 버튼을 규칙으로 확정하고, 애매한 화면은 검토 대상으로 표시 (누르지 않음)")
    tg.add_argument("screens", type=Path, help="화면 목록 YAML ([{SCREEN, URL}, …])")
    tg.add_argument("--out", type=Path, required=True, help="대상이 적힌 화면 목록 (QUERY, by: rule|review)")
    tg.add_argument("--base-url", default=os.environ.get("EAST2WEST_BASE_URL"))
    tg.add_argument("--names", default=None, help="조회 버튼으로 인정할 이름, | 로 구분 (기본: 조회|검색|찾기|조회하기|검색하기|Search|Find)")
    tg.add_argument("--storage-state", type=Path, default=None)
    stt = sub.add_parser("status", help="수정 → 재실행 루프용: 마지막 to-be 비교의 남은 실패, 종류, 지난 실행 대비 변화 (runs/<app>/ 원장)")
    stt.add_argument("oracle_dir", type=Path)
    qk = sub.add_parser("quirks", help="as-is 이상 동작의 고객 결정 → 소스 수정 작업 목록(markdown)과 결정에서 만든 판정 규칙. 결정은 사람이 east2west ui 의 'as-is 이상 동작' 탭에서")
    qk.add_argument("target", type=Path, help="golden/<app> 또는 앱 이름 (기록은 quirks/<app>.json)")
    ui = sub.add_parser("ui", help="통합 화면: 프로젝트마다 Screen Map·시나리오·실행·as-is 이상 동작을 한 화면에서 (로컬 서버, 산출물만 읽음). 사람이 보는 화면은 전부 여기")
    ui.add_argument("--golden", type=Path, default=Path("golden"), help="오라클 루트 (기본 golden/)")
    ui.add_argument("--tests", type=Path, default=Path("e2e"), help="테스트 루트 (제목용, 기본 e2e/)")
    ui.add_argument("--port", type=int, default=8790)
    ui.add_argument("--no-open", action="store_true")
    return ap


def main(argv: list[str] | None = None) -> int:
    load_dotenv()
    ap = build_parser()
    args = ap.parse_args(argv)
    if args.cmd == "ui":
        from .pwtest import hub
        hub.serve(args.golden, port=args.port, tests_root=args.tests, open_browser=not args.no_open)
        return 0
    if args.cmd == "quirks":
        from .pwtest import quirks
        app = args.target.name
        if not quirks.path(app).exists():
            print(f"{quirks.path(app)} 이 없습니다 (as-is 이상 동작 기록. 형식은 east2west/pwtest/quirks.py 첫 docstring)")
            return 1
        golden = args.target if args.target.is_dir() else Path("golden") / app
        problems = quirks.problems(app, golden if golden.is_dir() else None)
        print("".join(f"! {p}\n" for p in problems) + ("\n" if problems else "") + quirks.decisions_md(app), end="")
        return 0
    if args.cmd == "status":
        from .pwtest import ledger, oracle
        print(ledger.status_text(args.oracle_dir.name, oracle.status(args.oracle_dir)))
        return 0
    if args.cmd == "routes":
        from . import routes as _routes
        if not args.src.exists():
            ap.error(f"소스 위치가 없습니다: {args.src}")
        data = _routes.write(args.src, args.out)
        print(_routes.summary(data))
        print(f"-> {args.out}  (east2west ui 의 Screen Map이 이 목록과 대조해 못 간 화면을 회색으로 표시)")
        return 0
    if args.cmd == "targets":
        from . import targets
        return targets.main(args.screens, args.out, args.base_url, args.names.split("|") if args.names else None, args.storage_state)
    if args.cmd == "crawl":
        from .crawl import Crawler, load_fixtures_full, load_seeds
        inputs, deny, pick = load_fixtures_full(args.fixtures)
        jev = None
        if args.classify == "jev" or (args.classify == "auto" and (os.environ.get("TYPESAFE_API_KEY") or os.environ.get("TYPESAFEAI_API_KEY"))):
            from .jev import JevClient
            jev = JevClient()
        cache_dir = args.cache_dir or args.out / "cache"
        crawler = Crawler(args.start, base_url=args.base_url, inputs=inputs, deny=deny, max_depth=args.depth, max_states=args.max_states,
                          max_actions=args.max_actions, group_min=args.group_min, settle_ms=args.settle_ms,
                          storage_state=args.storage_state, headed=args.headed,
                          reps=args.reps, pick=pick, jev=jev, list_cache=cache_dir / "lists.json", min_margin=args.min_margin,
                          max_route_states=args.route_states, seeds=load_seeds(args.seeds) if args.seeds and args.seeds.exists() else None)
        if args.dry_run:
            pv = crawler.preview()
            print(f"{pv['title']}  {pv['url']}\n\n누를 것 ({len(pv['click'])}):")
            print("\n".join(f"  - {s}" + (f"  (비슷한 {n}개 중 대표)" if n > 1 else "") for s, n in pv["click"]) or "  (없음)")
            print(f"\n누르지 않을 것, 금지 목록 ({len(pv['deny'])}):")
            print("\n".join(f"  - {s}" for s in pv["deny"]) or "  (없음)")
            print("\n채울 입력칸: " + (", ".join(f"{k}={v}" for k, v in pv["fill"]) or "(없음)"))
            print("값이 없는 입력칸: " + (", ".join(pv["missing"]) or "(없음)"))
            print("\n주의: 실제 탐색은 저장·확정 버튼도 누릅니다. 테스트 DB와 테스트 계정에서만 돌리세요. 금지 목록은 픽스처의 deny로 바꿉니다.")
            return 0
        from .clicks import sources_for
        from .crawl import CHECKPOINT, mark_output_dir
        from .pwtest import projects
        mark_output_dir(args.out)
        # 누른 결과 기억: 상한을 올려 다시 돌리면 새 동작만 누른다. 등록부(east2west.json)의 그 주소 소스가 바뀌면 버린다 (east2west.clicks)
        crawler.run(shots_dir=args.out / "screens", checkpoint=args.out / CHECKPOINT, clicks=cache_dir / "clicks.jsonl",
                    sources=sources_for(crawler.start_url, projects.FILE))
        written = crawler.write(args.out, cache_dir)
        sampled = [(n, L) for n in crawler.nodes for L in n.lists if L.get("reps")]
        if sampled:
            print("   목록 표본: " + "; ".join(f"n{n.id} '{L['heading'] or L['kind']}' {L['rows']}행 → 대표 {sum(len(v) for v in L['reps'].values())} ({L['source']})" for n, L in sampled))
        counts = {k: sum(1 for e in crawler.edges if e.kind == k) for k in ("transition", "local", "external", "error", "denied")}
        print(f"\n== states {len(crawler.nodes)}, actions {counts} | {args.out / 'graph.md'} | {len(written)} scenarios")
        if written:
            print(f"   uv run east2west run {args.out}/crawl_*.yaml --cache-dir {cache_dir} --replay-only --base-url {crawler.origin}"
                  + (f" --storage-state {args.storage_state}" if args.storage_state else "") + "   (goto는 상대 경로: to-be는 --base-url만 바꾼다)")
            print(f"   Playwright 테스트: {args.out / 'test_crawl.py'} (검토 후 e2e/<app>/로 옮기면 기록·비교·결함 주입 흐름에 올라간다)")
        return 0
    if args.cmd == "oracle-status":
        from .pwtest import oracle
        st = oracle.status(args.oracle_dir)
        print(("approved by %s at %s" % (st["approved_by"], st["approved_at"])) if st["ok"] else "NOT APPROVED:\n  " + "\n  ".join(st["problems"]))
        print("mask rules:\n" + ("\n".join(oracle.mask_audit(args.oracle_dir)) or "  (none)"))
        bad = oracle.config_problems(args.oracle_dir)  # 틀린 차이 규칙: 비교가 거부된다
        if bad:
            print("invalid rules (comparison refused):\n" + "\n".join(f"  {b}" for b in bad))
        return 0 if st["ok"] and not bad else 1
    if args.cmd == "mutate":
        from .pwtest import mutation
        name = Path(args.targets[0]).name
        out = args.out or Path("reports") / f"mutation-{name}-{'golden' if args.compare else 'expects'}.json"
        mutation.run(args.targets, base_url=args.base_url, compare=args.compare, workers=args.workers,
                     max_per_op=args.max_per_op, out=out, max_mutants=args.max_mutants)
        if args.compare:
            from .pwtest import ledger
            print(f"kept for history: {ledger.save_mutation(args.compare.name, out)}")
        return 0
    if args.cmd == "report":
        from .pwtest import report
        report.write(oracle_dir=args.oracle, junits=args.junit, mutations=args.mutation, out=args.out)
        return 0
    if args.cmd == "triage":
        return triage_reports(args.reports)
    if args.triage and args.replay_only:
        ap.error("--triage needs Jev (TYPESAFE_API_KEY); it cannot run with --replay-only")
    if args.record and args.compare:
        ap.error("--record and --compare are exclusive (record on as-is, compare on to-be)")

    def make_runner() -> Runner:
        return Runner(headed=args.headed, use_cache=not args.no_cache, min_margin=args.margin,
                      max_candidates=args.max_candidates, settle_ms=args.settle_ms,
                      storage_state=args.storage_state, replay_only=args.replay_only, cache_dir=args.cache_dir,
                      base_url=args.base_url, record_dir=args.record, compare_dir=args.compare,
                      compare_opts=CompareOptions(ignore=args.compare_ignore, url=args.compare_url, unordered=args.compare_unordered),
                      triage=args.triage)
    jobs = [(path, stem, vars) for path in args.scenarios for stem, vars in Runner.variants(path)]
    results = run_jobs(jobs, make_runner, workers=args.workers)
    failed = sum(1 for _, r in results if r.status != "pass")
    diffs = sum(1 for _, r in results if r.status == "diff")
    print(f"\n{len(results) - failed}/{len(results)} scenarios passed" + (f" ({diffs} differ from golden)" if diffs else ""))
    if args.triage:
        from . import triage as _triage
        print(_triage.format_summary(*_triage.summarize([r.to_dict() for _, r in results])))
    if args.junit:
        write_junit(args.junit, results)
    return 1 if failed else 0


def triage_reports(paths: list[Path]) -> int:
    """리포트 JSON의 실패·diff 스텝을 사후 분류한다. 페이지 스냅샷·오류 이벤트는 없고 사유·diff·Jev 확률·이력만 근거로 쓴다."""
    import json

    from . import triage as _triage
    from .jev import JevClient

    client = JevClient()
    outs: list[dict] = []
    try:
        for path in paths:
            report = json.loads(path.read_text(encoding="utf-8"))
            steps = report.get("steps", [])
            counts: dict[str, int] = {}
            print(f"\n## {path}  ({report.get('scenario')}: {report.get('status')})")
            for i, s in enumerate(steps):
                if not _triage.needs_triage(s):
                    continue
                state = _triage.evidence(s, scenario=str(report.get("scenario")), history=steps[:i], mode={"post_hoc": True})
                s["triage"] = _triage.classify(client, state).to_dict()
                counts[s["triage"]["category"]] = counts.get(s["triage"]["category"], 0) + 1
                print(f"  ✘ {s['index']:2d} {s['kind']:6s} {s['text']}  !! {s.get('reason') or 'differs from golden'}\n     {_triage.format_line(s['triage'])}")
            report["triage"] = counts
            out = path.with_name(path.stem + "-triage.json")
            out.write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")
            outs.append(report)
            print(f"  -> {out}" if counts else "  (nothing to classify)")
    finally:
        client.close()
    print()
    print(_triage.format_summary(*_triage.summarize(outs)))
    return 0


def run_jobs(jobs: list[tuple[Path, str, dict[str, str]]], make_runner, *, workers: int = 1) -> list[tuple[Path, RunResult]]:
    """(시나리오, stem, 변수) 목록을 돌려 넣은 순서대로 결과를 준다. workers>1이면 스레드마다 Runner(브라우저·Jev 클라이언트) 하나씩.
    행마다 캐시·골든·리포트 파일이 stem으로 따로 나뉘어 있어 동시에 써도 겹치지 않는다. 화면 수백 개 스모크가 대상."""
    import threading
    from concurrent.futures import ThreadPoolExecutor

    local = threading.local()
    runners: list[Runner] = []
    lock = threading.Lock()

    def runner() -> Runner:
        if not hasattr(local, "runner"):
            local.runner = make_runner()
            with lock:
                runners.append(local.runner)
        return local.runner

    def one(job: tuple[Path, str, dict[str, str]]) -> tuple[Path, RunResult]:
        path, stem, vars = job
        print(f"\n## {path}" + (f"  {vars}" if vars else ""), flush=True)
        return path, runner().run(path, vars=vars, stem=stem)

    try:
        if workers <= 1:
            return [one(j) for j in jobs]
        with ThreadPoolExecutor(max_workers=workers) as ex:
            return list(ex.map(one, jobs))
    finally:
        for r in runners:
            r.close()


def write_junit(path: Path, results: list[tuple[Path, RunResult]]) -> None:
    cases = []
    for src, r in results:
        body = ""
        if r.status != "pass":
            bad = [s for s in r.steps if s.status == "fail" or s.diff]
            msg = "; ".join((f"[triage {s.triage['category']} p={s.triage['probabilities'].get(s.triage['category'], 0):.2f}] " if s.triage and not s.triage.get("error") else "")
                            + f"step {s.index} {s.kind} {s.text}: {s.reason or 'differs from golden'}" for s in bad) or r.status
            detail = "\n".join(line for s in bad for line in s.diff)
            body = f'<failure type="{r.status}" message="{escape(msg, {chr(34): "&quot;"})}">{escape(detail)}</failure>'
        cases.append(f'  <testcase classname="{escape(str(src.parent))}" name="{escape(r.scenario, {chr(34): "&quot;"})}" time="{r.elapsed_ms / 1000:.2f}">{body}</testcase>')
    failures = sum(1 for _, r in results if r.status != "pass")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f'<?xml version="1.0" encoding="UTF-8"?>\n<testsuite name="east2west" tests="{len(results)}" failures="{failures}">\n'
                    + "\n".join(cases) + "\n</testsuite>\n", encoding="utf-8")


if __name__ == "__main__":
    sys.exit(main())
