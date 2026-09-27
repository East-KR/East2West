"""검증 보고서: 산출물(오라클 승인 상태, JUnit XML, 결함 주입 결과)에서만 만든다. 서술은 넣지 않는다.

eastshift report --oracle golden/<app> --junit reports/junit-<target>.xml --mutation reports/mutation-<app>-golden.json --out reports/verification-<app>.md
  → markdown 한 장 (에이전트·CI 가 판정 줄을 읽는다). 사람이 보는 화면은 eastshift ui 의 검증 보고서 탭 (hub.py 가 render_html 로 만든다).
"""
from __future__ import annotations

import json
import time
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any

from . import oracle
from .evidence import source_hash

MIN_SCORE = 0.8  # 결함 주입 탐지율 기준 (expects+golden 모드)


def _junit(path: Path) -> dict[str, Any]:
    root = ET.parse(path).getroot()
    suites = [root] if root.tag == "testsuite" else list(root.iter("testsuite"))
    props, cases = {}, {}
    for s in suites:
        for p in s.iter("property"):
            props[p.get("name")] = p.get("value")
        for tc in s.iter("testcase"):
            identity = tc.find("./properties/property[@name='eastshift_id']")
            name = identity.get("value") if identity is not None else tc.get("name", "?")
            c = cases.setdefault(name, {"name": name, "status": "pass", "messages": []})
            accepted = tc.find("./properties/property[@name='eastshift_accepted_differences']")
            if accepted is not None:
                c["accepted_differences"] = json.loads(accepted.get("value", "[]"))
            setup = tc.find("./properties/property[@name='eastshift_setup']")
            if setup is not None:
                c["setup"] = json.loads(setup.get("value", "{}"))
            builds = tc.find("./properties/property[@name='eastshift_build_ids']")
            if builds is not None:
                c["build_ids"] = json.loads(builds.get("value", "[]"))
            for tag in ("failure", "error"):
                for el in tc.findall(tag):
                    c["status"] = "fail"
                    # 본문(el.text)이 전체 내용. message 속성은 같은 내용의 요약이고, teardown 실패는 'failed on teardown with "…"'로 감싸여 있다
                    msg = el.text or el.get("message") or ""
                    if msg.startswith('failed on teardown with "') and msg.endswith('"'):
                        msg = msg[len('failed on teardown with "'):-1]
                    c["messages"].append(msg)
            if tc.find("skipped") is not None and c["status"] == "pass":
                c["status"] = "skip"
    for c in cases.values():
        text = "\n".join(c["messages"])
        c["drift"] = "expectation changed since" in text or "expectation added since" in text or "expectation removed since" in text
        c["golden_diff"] = "differs from golden" in text
    return {"path": str(path), "props": props, "cases": list(cases.values())}


def _first_lines(text: str, n: int = 6) -> str:
    lines = [l.rstrip() for l in text.strip().splitlines() if l.strip()]
    return "\n".join(lines[:n]) + ("\n…" if len(lines) > n else "")


def build(*, oracle_dir: Path, junits: list[Path], mutations: list[Path]) -> dict[str, Any]:
    """산출물 → 신뢰 확인 목록과 판정. write()와 통합 화면(hub)이 같이 쓴다."""
    st = oracle.status(oracle_dir)
    approval_id = oracle.approval_id(oracle_dir)
    default_source_dir = Path("e2e") / oracle_dir.name
    runs = [_junit(j) for j in junits]
    muts = [json.loads(m.read_text(encoding="utf-8")) for m in mutations]
    checks: list[tuple[str, bool, str]] = []

    checks.append(("기준이 승인됨", st["ok"], f"{st.get('approved_by')} · {st.get('approved_at')}" if st["ok"] else "; ".join(st["problems"])))
    recorded = {g.stem for g in oracle_dir.glob("*.json")
                if g.name not in (oracle.MANIFEST, oracle.CONFIG) and not g.name.startswith("name_map")}
    for r in runs:
        approved_run = r["props"].get("oracle_approved") == "true"
        checks.append(("승인된 기준으로 비교함", approved_run, "" if approved_run else "승인 없이 실행 (--allow-unapproved)"))
        same = st["ok"] and r["props"].get("oracle_approved_at") == st.get("approved_at")
        checks.append(("비교 결과가 현재 승인본으로 만들어짐", same,
                       "" if same else f"실행 당시 승인 {r['props'].get('oracle_approved_at') or '없음'} ≠ 현재 {st.get('approved_at')}"))
        checks.append(("비교 결과의 승인 파일이 현재와 같음", bool(approval_id and r["props"].get("oracle_approval_id") == approval_id), "승인 파일 SHA-256"))
        current_source = source_hash(Path(r["props"].get("source_dir") or default_source_dir))
        checks.append(("비교 때의 테스트·도구 코드가 현재와 같음", r["props"].get("source_sha256") == current_source, "소스 SHA-256"))
        build_ids = {bid for c in r["cases"] for bid in c.get("build_ids", [])}
        if build_ids:
            checks.append(("한 실행에서 대상 앱 배포 ID가 일정함", len(build_ids) == 1, ", ".join(sorted(build_ids))))
        checks.append(("결함 주입 실행이 아닌 실제 비교 결과", not r["props"].get("mutant"), r["props"].get("mutant") or ""))
        ran = {c["name"] for c in r["cases"] if c["status"] != "skip"}
        missing = sorted(recorded - ran)
        checks.append(("기록된 테스트를 빠짐없이 실행", not missing, ("빠짐: " + ", ".join(missing)) if missing else f"{len(recorded)}개"))
        drift = [c["name"] for c in r["cases"] if c["drift"]]
        checks.append(("기대값을 임의로 바꾸지 않음", not drift, ", ".join(drift)))
    gold = [m for m in muts if m["mode"] == "expects+golden"]
    for m in gold:
        checks.append(("결함 탐지 측정도 승인된 기준으로", bool(m.get("oracle_approved")), m["generated_at"]))
        same = st["ok"] and m.get("oracle_approved_at") == st.get("approved_at")
        checks.append(("결함 탐지 측정이 현재 승인본으로 만들어짐", same,
                       "" if same else f"측정 당시 승인 {m.get('oracle_approved_at') or '없음'} ≠ 현재 {st.get('approved_at')}"))
        checks.append(("결함 탐지 측정의 승인 파일이 현재와 같음", bool(approval_id and m.get("oracle_approval_id") == approval_id), "승인 파일 SHA-256"))
        current_source = source_hash(Path(m.get("source_dir") or default_source_dir))
        checks.append(("결함 탐지 측정의 테스트·도구 코드가 현재와 같음", m.get("source_sha256") == current_source, "소스 SHA-256"))
        checks.append(("결함 탐지 측정이 오류 없이 실행됨", m.get("errors", 0) == 0, f"실행 오류 {m.get('errors', 0)}건" if m.get("errors") else ""))
        untested = sorted(recorded - set(m.get("test_kills", {})))
        checks.append(("기록된 테스트 전부가 결함 탐지 측정에 참여", not untested, ("빠짐: " + ", ".join(untested)) if untested else f"{len(recorded)}개"))
        score = m["score"] or 0
        checks.append((f"테스트가 결함을 {MIN_SCORE:.0%} 이상 잡음", score >= MIN_SCORE, f"{m['killed']}/{m['total']} = {score:.0%}"))
        core_total = sum(v["total"] for op, v in m.get("by_op", {}).items() if op != "label")
        core_killed = sum(v["killed"] for op, v in m.get("by_op", {}).items() if op != "label")
        checks.append((f"라벨 외 결함도 {MIN_SCORE:.0%} 이상 잡음", core_total > 0 and core_killed / core_total >= MIN_SCORE,
                       f"{core_killed}/{core_total}" if core_total else "라벨 외 결함 후보 없음"))
    trusted = all(ok for _, ok, _ in checks) and bool(runs) and bool(gold)
    accepted_count = sum(len(c.get("accepted_differences", [])) for r in runs for c in r["cases"])
    equivalent = bool(runs) and not accepted_count and all(c["status"] == "pass" for r in runs for c in r["cases"])
    declared = oracle.load_config(oracle_dir).get("coverage", [])
    coverage = []
    for entry in declared:
        names = entry.get("tests", [])
        ok = bool(names) and all(any(c["name"] == name and c["status"] == "pass" for c in r["cases"])
                                     for name in names for r in runs)
        coverage.append({"case": entry.get("case", "?"), "tests": names, "ok": ok})
    coverage_complete = bool(coverage) and all(c["ok"] for c in coverage)
    return {"status": st, "runs": runs, "muts": muts, "gold": gold, "checks": checks, "trusted": trusted,
            "equivalent": equivalent, "accepted_count": accepted_count, "coverage": coverage, "coverage_complete": coverage_complete}


def write(*, oracle_dir: Path, junits: list[Path], mutations: list[Path], out: Path) -> None:
    b = build(oracle_dir=oracle_dir, junits=junits, mutations=mutations)
    runs, muts, gold, checks, trusted = b["runs"], b["muts"], b["gold"], b["checks"], b["trusted"]

    L = [f"# 검증 보고서: {oracle_dir.name}", "",
         f"생성 {time.strftime('%Y-%m-%d %H:%M:%S')} · `eastshift report`가 아래 산출물에서만 만들었다.", ""]
    L += ["## 결과를 믿을 수 있는가", "", "| 확인 | 결과 | 근거 |", "| :--- | :--- | :--- |"]
    L += [f"| {name} | {'✅' if ok else '❌'} | {detail} |" for name, ok, detail in checks]
    if not runs:
        L.append("| 비교 실행 | ❌ | JUnit 결과 없음 |")
    if not gold:
        L.append("| 결함 탐지율 | ❌ | expects+golden 모드 결함 주입 결과 없음 |")
    L += ["", "## 판정", "", f"- 증거 유효성: {'유효' if trusted else '확인 필요'}",
          f"- 실행한 시나리오 동등성: {'모두 같음' if b['equivalent'] else '승인된 차이 포함' if b['accepted_count'] and all(c['status'] == 'pass' for r in runs for c in r['cases']) else '차이 또는 실패 있음'}",
          f"- 등록된 업무 범위: {'모두 통과' if b['coverage_complete'] else '미등록 또는 미완료'}", ""]
    L += ["| 업무 경우 | 연결된 테스트 | 결과 |", "| :--- | :--- | :--- |"]
    L += [f"| {x['case']} | {', '.join(x['tests'])} | {'통과' if x['ok'] else '미완료'} |" for x in b["coverage"]]
    if not b["coverage"]:
        L.append("| (등록되지 않음) | — | 범위 판단 불가 |")
    L.append("")

    for r in runs:
        cases = r["cases"]
        fails = [c for c in cases if c["status"] == "fail"]
        L += [f"## 비교 결과: `{r['path']}`", "",
              f"{len(cases)}개 중 통과 {sum(c['status'] == 'pass' for c in cases)}, 실패 {len(fails)}", "",
              "실행 조건: " + json.dumps(cases[0].get("setup", {}), ensure_ascii=False, sort_keys=True) if cases else "실행 조건: 없음", "",
              "대상 앱 배포 ID: " + (", ".join(sorted({bid for c in cases for bid in c.get("build_ids", [])})) or "헤더 없음"), ""]
        if r["props"].get("mutant"):
            L += [f"(결함 주입 실행: {r['props']['mutant']})", ""]
        for c in fails:
            kind = "기대값 변경" if c["drift"] else ("as-is와 다름" if c["golden_diff"] else "검증 실패")
            L += [f"- **{c['name']}** — {kind}", "", "```", _first_lines("\n".join(c["messages"]), 12), "```", ""]
        for c in cases:
            for accepted in c.get("accepted_differences", []):
                L += [f"- **{c['name']}** {accepted['step']}단계: 승인된 차이 — {accepted['reason']}", ""]

    for m in muts:
        L += [f"## 결함 주입: {m['mode']}", "",
              f"`{m['base_url']}`에 결함 {m['total']}개를 하나씩 주입, 탐지 {m['killed']}개 = **{(m.get('score') or 0):.0%}**", "",
              "이 비율은 생성해 넣은 결함에 대한 탐지율이며 전체 업무 검증 범위는 아닙니다.", "",
              "| 연산자 | 탐지 |", "| :--- | :--- |"]
        L += [f"| {op} | {v['killed']}/{v['total']} |" for op, v in m["by_op"].items() if v["total"]]
        idle = [t for t, k in m["test_kills"].items() if not k]
        L += ["", "아무 결함도 못 잡은 테스트: " + (", ".join(idle) if idle else "없음"), ""]
        surv = [x for x in m["mutants"] if not x["killed"]]
        if surv:
            L += [f"생존 결함 {len(surv)}개 (테스트 빈틈인지, 관찰할 차이가 없는 결함인지 사람이 판정):", ""]
            L += [f"- `{x['id']}` {x['op']} `{x['path']}`: `{x['desc'][:140]}`" for x in surv]
            L.append("")

    L += ["## 오라클", "", f"`{oracle_dir}`", "", "```", *[l.strip() for l in oracle.summary(oracle_dir)], "```", "",
          "마스킹 규칙과 실제로 가린 값:", "", "```", *([l.strip() for l in oracle.mask_audit(oracle_dir)] or ["(없음)"]), "```", ""]
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("\n".join(L), encoding="utf-8")
    print(f"{'TRUSTED' if trusted else 'NOT TRUSTED'}: {out}  (화면: uv run eastshift ui → 검증 보고서 탭)")


def render_fragment(oracle_dir: Path, b: dict[str, Any], tests_dir: Path | None = None) -> dict[str, Any]:
    """통합 화면의 검증 보고서 탭 조각 (html.fragment 형식)."""
    from . import html
    checks = list(b["checks"])
    if not b["runs"]:
        checks.append(("비교 실행 결과", False, "JUnit 결과 없음"))
    if not b["gold"]:
        checks.append(("결함 탐지 측정", False, "결과 없음"))
    return html.report_fragment(oracle_dir=oracle_dir, checks=checks, trusted=b["trusted"], runs=b["runs"], muts=b["muts"],
                                tests_dir=tests_dir, equivalent=b["equivalent"], accepted_count=b["accepted_count"], coverage=b["coverage"])


def render_html(oracle_dir: Path, b: dict[str, Any], tests_dir: Path | None = None) -> str:
    from . import html
    return html.assemble(render_fragment(oracle_dir, b, tests_dir))
