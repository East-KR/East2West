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


def main(argv: list[str] | None = None) -> int:
    load_dotenv()
    ap = argparse.ArgumentParser(prog="jev-e2e", description="Natural-language E2E steps decided by Jev, executed by Playwright")
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
    run.add_argument("--base-url", default=os.environ.get("JEV_BASE_URL"), help="goto 상대 경로의 기준 URL (기본: JEV_BASE_URL)")
    run.add_argument("--cache-dir", type=Path, default=Path(".jev-cache"), help="재생 캐시 디렉터리 (기본 .jev-cache)")
    run.add_argument("--record", type=Path, default=None, metavar="DIR", help="스텝별 관찰값을 골든으로 저장 (as-is에서 실행)")
    run.add_argument("--compare", type=Path, default=None, metavar="DIR", help="골든과 스텝별 관찰값 비교 (to-be에서 실행). 차이가 있으면 DIFF")
    run.add_argument("--compare-ignore", action="append", default=[], metavar="REGEX", help="비교 전에 <masked>로 바꿀 정규식 (반복 가능)")
    run.add_argument("--compare-url", action="store_true", help="URL 경로·쿼리와 제목도 비교")
    run.add_argument("--compare-unordered", action="store_true", help="화면 내용 줄 순서를 무시하고 비교")
    run.add_argument("--junit", type=Path, default=None, metavar="PATH", help="JUnit XML 결과 파일 (CI 리포트용)")
    ap_approve = sub.add_parser("approve", help="(사람) 오라클 디렉터리를 검토하고 승인한다. 터미널에서만 동작")
    ap_approve.add_argument("oracle_dir", type=Path)
    ap_approve.add_argument("--by", required=True, help="승인자 이름")
    ap_approve.add_argument("--note", default="")
    ap_approve.add_argument("--tests", type=Path, default=None, help="테스트 디렉터리 (검토 화면 제목용, 기본 e2e/<오라클 이름>)")
    ap_review = sub.add_parser("review", help="승인 검토 화면(HTML)을 만들고 연다. 승인은 하지 않는다")
    ap_review.add_argument("oracle_dir", type=Path)
    ap_review.add_argument("--tests", type=Path, default=None)
    ap_review.add_argument("--no-open", action="store_true")
    ap_status = sub.add_parser("oracle-status", help="오라클 승인 상태와 마스킹 규칙 감사")
    ap_status.add_argument("oracle_dir", type=Path)
    mut = sub.add_parser("mutate", help="결함 주입으로 Playwright 테스트의 탐지력 측정")
    mut.add_argument("targets", nargs="+", help="pytest 대상 (e2e/<app>)")
    mut.add_argument("--base-url", default=os.environ.get("JEV_BASE_URL"), required=not os.environ.get("JEV_BASE_URL"))
    mut.add_argument("--compare", type=Path, default=None, help="오라클 디렉터리. 없으면 expect만으로 측정")
    mut.add_argument("--allow-unapproved", action="store_true")
    mut.add_argument("--workers", type=int, default=4)
    mut.add_argument("--max-per-op", type=int, default=5, help="(응답 경로, 연산자)당 최대 결함 수")
    mut.add_argument("--out", type=Path, default=None)
    rep = sub.add_parser("report", help="산출물만으로 검증 보고서 생성")
    rep.add_argument("--oracle", type=Path, required=True)
    rep.add_argument("--junit", type=Path, action="append", default=[], help="pytest --junitxml 결과 (여러 개 가능)")
    rep.add_argument("--mutation", type=Path, action="append", default=[], help="jev-e2e mutate 결과 JSON (여러 개 가능)")
    rep.add_argument("--out", type=Path, required=True)
    cr = sub.add_parser("crawl", help="시작 화면에서 동작을 모두 눌러 보고 흐름 그래프와 시나리오를 만든다 (Jev 호출 없음)")
    cr.add_argument("start", help="시작 URL (상대 경로면 --base-url 기준, 시나리오 goto에 그대로 쓴다)")
    cr.add_argument("--out", type=Path, required=True, help="graph.json, graph.md, 스크린샷, 시나리오 YAML을 둘 디렉터리")
    cr.add_argument("--fixtures", type=Path, default=None, help="inputs: {입력칸 이름: 값}, deny: 누르지 않을 이름 정규식")
    cr.add_argument("--cache-dir", type=Path, default=None, help="시나리오 재생 캐시 디렉터리 (기본 <out>/cache)")
    cr.add_argument("--depth", type=int, default=3, help="시작 화면에서 몇 번의 전이까지 탐색할지 (기본 3)")
    cr.add_argument("--max-states", type=int, default=30)
    cr.add_argument("--max-actions", type=int, default=40, help="상태당 누를 동작 수 상한")
    cr.add_argument("--group-min", type=int, default=3, help="비슷한 요소가 이 개수 이상이면 대표 하나만 누른다")
    cr.add_argument("--settle-ms", type=int, default=400)
    cr.add_argument("--base-url", default=os.environ.get("JEV_BASE_URL"))
    cr.add_argument("--storage-state", type=Path, default=None)
    cr.add_argument("--headed", action="store_true")
    cr.add_argument("--dry-run", action="store_true", help="아무것도 누르지 않고 시작 화면에서 누를 것·안 누를 것·채울 입력칸만 보여 준다")
    args = ap.parse_args(argv)
    if args.cmd == "crawl":
        from .crawl import Crawler, load_fixtures
        inputs, deny = load_fixtures(args.fixtures)
        crawler = Crawler(args.start, base_url=args.base_url, inputs=inputs, deny=deny, max_depth=args.depth, max_states=args.max_states,
                          max_actions=args.max_actions, group_min=args.group_min, settle_ms=args.settle_ms,
                          storage_state=args.storage_state, headed=args.headed)
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
        from .crawl import check_output_dir
        check_output_dir(args.out)
        crawler.run(shots_dir=args.out / "screens")
        cache_dir = args.cache_dir or args.out / "cache"
        written = crawler.write(args.out, cache_dir)
        counts = {k: sum(1 for e in crawler.edges if e.kind == k) for k in ("transition", "local", "external", "error", "denied")}
        print(f"\n== states {len(crawler.nodes)}, actions {counts} | {args.out / 'graph.md'} | {len(written)} scenarios")
        if written:
            print(f"   uv run jev-e2e run {args.out}/crawl_*.yaml --cache-dir {cache_dir} --replay-only"
                  + (f" --base-url {args.base_url}" if args.base_url else "") + (f" --storage-state {args.storage_state}" if args.storage_state else ""))
            print(f"   Playwright 테스트: {args.out / 'test_crawl.py'} (검토 후 e2e/<app>/로 옮기면 승인·결함 주입 흐름에 올라간다)")
        return 0
    if args.cmd == "approve":
        from .pwtest import oracle
        oracle.approve(args.oracle_dir, args.by, args.note, tests_dir=args.tests)
        return 0
    if args.cmd == "review":
        from .pwtest import html
        page = html.write_review(args.oracle_dir, tests_dir=args.tests)
        print(page)
        if not args.no_open:
            html.open_in_browser(page)
        return 0
    if args.cmd == "oracle-status":
        from .pwtest import oracle
        st = oracle.status(args.oracle_dir)
        print(("approved by %s at %s" % (st["approved_by"], st["approved_at"])) if st["ok"] else "NOT APPROVED:\n  " + "\n  ".join(st["problems"]))
        print("mask rules:\n" + ("\n".join(oracle.mask_audit(args.oracle_dir)) or "  (none)"))
        return 0 if st["ok"] else 1
    if args.cmd == "mutate":
        from .pwtest import mutation
        name = Path(args.targets[0]).name
        out = args.out or Path("reports") / f"mutation-{name}-{'golden' if args.compare else 'expects'}.json"
        mutation.run(args.targets, base_url=args.base_url, compare=args.compare, workers=args.workers,
                     max_per_op=args.max_per_op, allow_unapproved=args.allow_unapproved, out=out)
        return 0
    if args.cmd == "report":
        from .pwtest import report
        report.write(oracle_dir=args.oracle, junits=args.junit, mutations=args.mutation, out=args.out)
        return 0
    if args.record and args.compare:
        ap.error("--record and --compare are exclusive (record on as-is, compare on to-be)")

    runner = Runner(headed=args.headed, use_cache=not args.no_cache, min_margin=args.margin,
                    max_candidates=args.max_candidates, settle_ms=args.settle_ms,
                    storage_state=args.storage_state, replay_only=args.replay_only, cache_dir=args.cache_dir,
                    base_url=args.base_url, record_dir=args.record, compare_dir=args.compare,
                    compare_opts=CompareOptions(ignore=args.compare_ignore, url=args.compare_url, unordered=args.compare_unordered))
    results: list[tuple[Path, RunResult]] = []
    try:
        for path in args.scenarios:
            for stem, vars in Runner.variants(path):
                print(f"\n## {path}" + (f"  {vars}" if vars else ""))
                results.append((path, runner.run(path, vars=vars, stem=stem)))
    finally:
        runner.close()
    failed = sum(1 for _, r in results if r.status != "pass")
    diffs = sum(1 for _, r in results if r.status == "diff")
    print(f"\n{len(results) - failed}/{len(results)} scenarios passed" + (f" ({diffs} differ from golden)" if diffs else ""))
    if args.junit:
        write_junit(args.junit, results)
    return 1 if failed else 0


def write_junit(path: Path, results: list[tuple[Path, RunResult]]) -> None:
    cases = []
    for src, r in results:
        body = ""
        if r.status != "pass":
            bad = [s for s in r.steps if s.status == "fail" or s.diff]
            msg = "; ".join(f"step {s.index} {s.kind} {s.text}: {s.reason or 'differs from golden'}" for s in bad) or r.status
            detail = "\n".join(line for s in bad for line in s.diff)
            body = f'<failure type="{r.status}" message="{escape(msg, {chr(34): "&quot;"})}">{escape(detail)}</failure>'
        cases.append(f'  <testcase classname="{escape(str(src.parent))}" name="{escape(r.scenario, {chr(34): "&quot;"})}" time="{r.elapsed_ms / 1000:.2f}">{body}</testcase>')
    failures = sum(1 for _, r in results if r.status != "pass")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f'<?xml version="1.0" encoding="UTF-8"?>\n<testsuite name="jev-e2e" tests="{len(results)}" failures="{failures}">\n'
                    + "\n".join(cases) + "\n</testsuite>\n", encoding="utf-8")


if __name__ == "__main__":
    sys.exit(main())
