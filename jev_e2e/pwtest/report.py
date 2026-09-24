"""검증 보고서: 산출물(오라클 승인 상태, JUnit XML, 결함 주입 결과)에서만 만든다. 서술은 넣지 않는다.

jev-e2e report --oracle golden/<app> --junit reports/junit-<target>.xml --mutation reports/mutation-<app>-golden.json --out reports/verification-<app>.md
"""
from __future__ import annotations

import json
import time
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any

from . import oracle

MIN_SCORE = 0.8  # 결함 주입 탐지율 기준 (expects+golden 모드)


def _junit(path: Path) -> dict[str, Any]:
    root = ET.parse(path).getroot()
    suites = [root] if root.tag == "testsuite" else list(root.iter("testsuite"))
    props, cases = {}, {}
    for s in suites:
        for p in s.iter("property"):
            props[p.get("name")] = p.get("value")
        for tc in s.iter("testcase"):
            name = tc.get("name", "?")
            c = cases.setdefault(name, {"name": name, "status": "pass", "messages": []})
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


def write(*, oracle_dir: Path, junits: list[Path], mutations: list[Path], out: Path) -> None:
    st = oracle.status(oracle_dir)
    runs = [_junit(j) for j in junits]
    muts = [json.loads(m.read_text(encoding="utf-8")) for m in mutations]
    checks: list[tuple[str, bool, str]] = []

    checks.append(("기준이 승인됨", st["ok"], f"{st.get('approved_by')} · {st.get('approved_at')}" if st["ok"] else "; ".join(st["problems"])))
    for r in runs:
        approved_run = r["props"].get("oracle_approved") == "true"
        checks.append(("승인된 기준으로 비교함", approved_run, "" if approved_run else "승인 없이 실행 (--allow-unapproved)"))
        recorded = {g.stem for g in oracle_dir.glob("*.json")
                    if g.name not in (oracle.MANIFEST, oracle.CONFIG) and not g.name.startswith("name_map")}
        ran = {c["name"] for c in r["cases"] if c["status"] != "skip"}
        missing = sorted(recorded - ran)
        checks.append(("기록된 테스트를 빠짐없이 실행", not missing, ("빠짐: " + ", ".join(missing)) if missing else f"{len(recorded)}개"))
        drift = [c["name"] for c in r["cases"] if c["drift"]]
        checks.append(("기대값을 임의로 바꾸지 않음", not drift, ", ".join(drift)))
    gold = [m for m in muts if m["mode"] == "expects+golden"]
    for m in gold:
        checks.append(("결함 탐지 측정도 승인된 기준으로", bool(m.get("oracle_approved")), m["generated_at"]))
        checks.append(("결함 탐지 측정이 오류 없이 실행됨", m.get("errors", 0) == 0, f"실행 오류 {m.get('errors', 0)}건" if m.get("errors") else ""))
        checks.append((f"테스트가 결함을 {MIN_SCORE:.0%} 이상 잡음", m["score"] >= MIN_SCORE, f"{m['killed']}/{m['total']} = {m['score']:.0%}"))
    trusted = all(ok for _, ok, _ in checks) and bool(runs) and bool(gold)

    L = [f"# 검증 보고서: {oracle_dir.name}", "",
         f"생성 {time.strftime('%Y-%m-%d %H:%M:%S')} · `jev-e2e report`가 아래 산출물에서만 만들었다.", ""]
    L += ["## 결과를 믿을 수 있는가", "", "| 확인 | 결과 | 근거 |", "| :--- | :--- | :--- |"]
    L += [f"| {name} | {'✅' if ok else '❌'} | {detail} |" for name, ok, detail in checks]
    if not runs:
        L.append("| 비교 실행 | ❌ | JUnit 결과 없음 |")
    if not gold:
        L.append("| 결함 탐지율 | ❌ | expects+golden 모드 결함 주입 결과 없음 |")
    L += ["", f"**판정: {'신뢰 가능' if trusted else '신뢰 불가 — ❌ 항목을 먼저 해결'}**", ""]

    for r in runs:
        cases = r["cases"]
        fails = [c for c in cases if c["status"] == "fail"]
        L += [f"## 비교 결과: `{r['path']}`", "",
              f"{len(cases)}개 중 통과 {sum(c['status'] == 'pass' for c in cases)}, 실패 {len(fails)}", ""]
        if r["props"].get("mutant"):
            L += [f"(결함 주입 실행: {r['props']['mutant']})", ""]
        for c in fails:
            kind = "기대값 변경" if c["drift"] else ("as-is와 다름" if c["golden_diff"] else "검증 실패")
            L += [f"- **{c['name']}** — {kind}", "", "```", _first_lines("\n".join(c["messages"]), 12), "```", ""]

    for m in muts:
        L += [f"## 결함 주입: {m['mode']}", "",
              f"`{m['base_url']}`에 결함 {m['total']}개를 하나씩 주입, 탐지 {m['killed']}개 = **{m['score']:.0%}**", "",
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
    if not runs:
        checks.append(("비교 실행 결과", False, "JUnit 결과 없음"))
    if not gold:
        checks.append(("결함 탐지 측정", False, "결과 없음"))
    from . import html
    page = html.write_report(oracle_dir=oracle_dir, checks=checks, trusted=trusted, runs=runs, muts=muts, out=out)
    print(f"{'TRUSTED' if trusted else 'NOT TRUSTED'}: {out} · {page}")
