"""결함 주입(mutation)으로 테스트의 결함 탐지력을 잰다. 앱 코드는 건드리지 않고, 브라우저가 받는 응답(HTML/JS/JSON)을 가로채 바꾼다.

jev-e2e mutate e2e/<app> --base-url <as-is> --compare golden/<app>

1. 발견: 테스트를 한 번 돌리며 테스트별로 받은 응답을 모은다 (이 실행이 통과해야 한다).
2. 생성: 응답마다 결함 후보 자리를 찾는다 (연산자 OPS). (경로, 연산자)당 최대 N개를 고르게 뽑는다.
3. 실행: 결함 하나씩, 그 경로를 받은 테스트만 다시 돌린다. 하나라도 실패하면 탐지(killed), 모두 통과하면 생존(survived).
4. 보고: 탐지율, 연산자별 탐지율, 생존 결함 목록(사람이 볼 것), 아무 결함도 못 잡은 테스트.

생존 결함은 두 종류다: 테스트가 못 보는 진짜 빈틈(테스트 보강), 관찰 가능한 차이가 없는 결함(동등 변이, 무시). 구분은 사람이 한다.
"""
from __future__ import annotations

import json
import re
import subprocess
import sys
import tempfile
import time
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

# -- 결함 후보 자리 -------------------------------------------------------------------
OPS = {
    "num": "숫자 하나의 마지막 자리 +1 (가격, 계산 상수, 표시 값)",
    "math": "반올림 방식 교체 (floor↔ceil, round→floor)",
    "cond": "비교 연산자 뒤집기 (=== ↔ !==, <= → < …)",
    "dialog": "alert 제거, confirm을 묻지 않고 확인 처리",
    "msg": "alert/confirm 문구 변경",
    "label": "화면 텍스트 한 곳 변경 (라벨, 안내문. <title>은 비교 대상이 아니라 제외)",
    "http500": "문서 응답을 500으로",
}
NUM = re.compile(r"(?<![\w.#%&-])(\d{1,3}(?:,\d{3})+|\d+(?:\.\d+)?)(?![\w%])")
TAG = re.compile(r"<[^>]*>")
SCRIPT = re.compile(r"<script[^>]*>(.*?)</script>", re.S | re.I)
STYLE = re.compile(r"<style[^>]*>.*?</style>", re.S | re.I)
TITLE = re.compile(r"<title[^>]*>.*?</title>", re.S | re.I)
ATTR_BEFORE = re.compile(r"([\w-]+)\s*=\s*['\"]?[^'\"]*$")
DIALOG = re.compile(r"\b(alert|confirm)\(")
MSG = re.compile(r"\b(?:alert|confirm)\((['\"])(.*?)\1")
COND = re.compile(r"===|!==|==|!=|<=|>=|(?<=\s)<(?=\s)|(?<=\s)>(?=\s)")
COND_SWAP = {"===": "!==", "!==": "===", "==": "!=", "!=": "==", "<=": "<", ">=": ">", "<": "<=", ">": ">="}
MATH = re.compile(r"Math\.(floor|ceil|round)")
MATH_SWAP = {"floor": "ceil", "ceil": "floor", "round": "floor"}
HANGUL = re.compile(r"[가-힣][가-힣 ]*[가-힣]")

Site = tuple[int, int, str, str]  # start, end, replacement, 설명


def _spans(rx: re.Pattern, text: str, group: int = 0) -> list[tuple[int, int]]:
    return [(m.start(group), m.end(group)) for m in rx.finditer(text)]


def _inside(pos: int, spans: list[tuple[int, int]]) -> tuple[int, int] | None:
    return next((s for s in spans if s[0] <= pos < s[1]), None)


def _ctx(text: str, start: int, end: int, repl: str) -> str:
    a, b = max(0, start - 25), min(len(text), end + 25)
    return (text[a:start] + "⟦" + text[start:end] + "→" + repl + "⟧" + text[end:b]).replace("\n", " ")


def sites(text: str, op: str, kind: str) -> list[Site]:
    """응답 본문에서 연산자 op를 적용할 자리들 (문서 순서, 결정론적)."""
    is_js = kind == "script" or text.lstrip()[:1] in ("{", "[")
    scripts = [(0, len(text))] if is_js else _spans(SCRIPT, text, 1)
    styles = [] if is_js else _spans(STYLE, text) + _spans(TITLE, text)
    tags = [] if is_js else _spans(TAG, text)
    out: list[Site] = []
    if op == "num":
        for m in NUM.finditer(text):
            if _inside(m.start(), styles):
                continue
            tag = None if _inside(m.start(), scripts) else _inside(m.start(), tags)
            if tag:  # 태그 안의 숫자는 value=, data-*= 값만 (size, rows 같은 레이아웃 속성 제외)
                attr = ATTR_BEFORE.search(text[tag[0]:m.start()])
                if not attr or not (attr.group(1) == "value" or attr.group(1).startswith("data-")):
                    continue
            s = m.group(0)
            repl = s[:-1] + str((int(s[-1]) + 1) % 10)
            out.append((m.start(), m.end(), repl, _ctx(text, m.start(), m.end(), repl)))
    elif op == "math":
        for m in MATH.finditer(text):
            repl = f"Math.{MATH_SWAP[m.group(1)]}"
            out.append((m.start(), m.end(), repl, _ctx(text, m.start(), m.end(), repl)))
    elif op == "cond":
        for m in COND.finditer(text):
            if _inside(m.start(), scripts):
                repl = COND_SWAP[m.group(0)]
                out.append((m.start(), m.end(), repl, _ctx(text, m.start(), m.end(), repl)))
    elif op == "dialog":
        for m in DIALOG.finditer(text):
            repl = "void(" if m.group(1) == "alert" else "(function(){return true;})("
            out.append((m.start(), m.end(), repl, _ctx(text, m.start(), m.end(), repl)))
    elif op == "msg":
        for m in MSG.finditer(text):
            repl = m.group(2) + " (변경)"
            out.append((m.start(2), m.end(2), repl, _ctx(text, m.start(2), m.end(2), repl)))
    elif op == "label":
        for m in HANGUL.finditer(text):
            if _inside(m.start(), scripts) or _inside(m.start(), styles) or _inside(m.start(), tags):
                continue
            repl = "X" + m.group(0)[1:]
            out.append((m.start(), m.end(), repl, _ctx(text, m.start(), m.end(), repl)))
    return out


def apply(text: str, op: str, site: int, kind: str) -> str:
    ss = sites(text, op, kind)
    if site >= len(ss):
        return text
    start, end, repl, _ = ss[site]
    return text[:start] + repl + text[end:]


# -- 응답 가로채기 (plugin이 테스트마다 설치) --------------------------------------------------
TEXTUAL = ("html", "javascript", "json", "text/plain")


class Capture:
    """발견 실행: 경로별 첫 응답 본문과 테스트별 경로."""

    def __init__(self) -> None:
        self.bodies: dict[str, dict[str, str]] = {}
        self.tests: dict[str, list[str]] = {}

    def add(self, test: str, path: str, kind: str, text: str) -> None:
        self.bodies.setdefault(path, {"kind": kind, "body": text})
        paths = self.tests.setdefault(test, [])
        if path not in paths:
            paths.append(path)

    def dump(self, d: Path) -> None:
        d.mkdir(parents=True, exist_ok=True)
        (d / "bodies.json").write_text(json.dumps(self.bodies, ensure_ascii=False), encoding="utf-8")
        (d / "tests.json").write_text(json.dumps(self.tests, ensure_ascii=False, indent=1), encoding="utf-8")


def install(ctx, *, test: str, capture: Capture | None, mutant: dict[str, Any] | None) -> None:
    def handler(route) -> None:
        req = route.request
        if req.resource_type not in ("document", "script", "xhr", "fetch"):
            return route.continue_()
        try:
            # 리다이렉트는 여기서 따라간다: 브라우저가 따라가는 리다이렉트 뒤 요청은 route에 다시 걸리지 않는다 (저장 → 완료 화면)
            resp = route.fetch()
        except Exception:
            return route.continue_()
        path = urlparse(resp.url).path  # 최종 응답 경로
        target = mutant is not None and path == mutant["path"]
        if target and mutant["op"] == "http500" and req.resource_type == "document":
            return route.fulfill(status=500, content_type="text/html; charset=utf-8", body="<h1>500 Internal Server Error</h1>")
        ctype = resp.headers.get("content-type", "")
        if not any(t in ctype for t in TEXTUAL):
            return route.fulfill(response=resp)
        text = resp.text()
        if capture is not None:
            capture.add(test, path, req.resource_type, text)
        if target and mutant["op"] != "http500":
            headers = {k: v for k, v in resp.headers.items() if k.lower() not in ("content-length", "content-encoding")}
            return route.fulfill(status=resp.status, headers=headers, body=apply(text, mutant["op"], mutant["site"], req.resource_type))
        return route.fulfill(response=resp)

    ctx.route("**/*", handler)


# -- 실행 ---------------------------------------------------------------------------
def _excluded(path: str, op: str, text: str, site: Site, rules: dict[str, Any]) -> str | None:
    """승인된 오라클 규칙으로 제외되는 결함이면 그 이유. 마스킹된 값은 비교하지 않으므로 바꿔도 차이가 없다."""
    for rx in rules.get("ignore", []):
        if any(m.start() <= site[0] < m.end() for m in re.finditer(rx, text)):
            return f"masked by oracle ignore {rx!r}"
    for e in rules.get("equivalent_mutants", []):
        if e.get("path") == path and e.get("op") == op and e.get("context", "\0") in site[3]:
            return f"approved equivalent: {e.get('reason', '')}"
    return None


def generate(bodies: dict[str, dict[str, str]], max_per_op: int, rules: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    rules = rules or {}
    mutants = []
    for path, b in sorted(bodies.items()):
        for op in OPS:
            if op == "http500":
                if b["kind"] == "document":
                    mutants.append({"path": path, "op": op, "site": 0, "desc": f"{path} → 500"})
                continue
            ss = sites(b["body"], op, b["kind"])
            keep = [i for i, site in enumerate(ss) if not _excluded(path, op, b["body"], site, rules)]
            n = len(keep)
            if not n:
                continue
            pick = keep if n <= max_per_op else [keep[j] for j in sorted({round(i * (n - 1) / (max_per_op - 1)) for i in range(max_per_op)})]
            mutants += [{"path": path, "op": op, "site": i, "desc": ss[i][3]} for i in pick]
    for i, m in enumerate(mutants):
        m["id"] = f"m{i:03d}"
    return mutants


def _pytest(args: list[str], timeout: int = 300) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", *args],
                          capture_output=True, text=True, timeout=timeout)


def _failed(junit: Path) -> list[str]:
    if not junit.exists():
        return []
    out = []
    for tc in ET.parse(junit).getroot().iter("testcase"):
        if tc.find("failure") is not None or tc.find("error") is not None:
            out.append(tc.get("name", "?"))
    return sorted(set(out))


def run(targets: list[str], *, base_url: str, compare: Path | None, workers: int, max_per_op: int,
        allow_unapproved: bool, out: Path) -> dict[str, Any]:
    common = ["--base-url", base_url] + (["--compare", str(compare)] if compare else []) + (["--allow-unapproved"] if allow_unapproved else [])
    work = Path(tempfile.mkdtemp(prefix="jev-mutate-"))
    t0 = time.time()
    print(f"[1/3] discovery run on {base_url} ({'expects + golden' if compare else 'expects only'})")
    r = _pytest([*targets, *common, "--jev-capture", str(work / "cap")])
    if r.returncode != 0:
        sys.exit("discovery run must pass on the unmutated app before mutating:\n" + r.stdout[-3000:])
    bodies = json.loads((work / "cap/bodies.json").read_text(encoding="utf-8"))
    tests = json.loads((work / "cap/tests.json").read_text(encoding="utf-8"))
    from . import oracle
    rules = oracle.load_config(compare) if compare else {}
    mutants = generate(bodies, max_per_op, rules)
    print(f"[2/3] {len(mutants)} mutants from {len(bodies)} responses, {len(tests)} tests")

    def one(m: dict[str, Any]) -> dict[str, Any]:
        sel = [t for t, paths in tests.items() if m["path"] in paths]
        junit = work / f"{m['id']}.xml"
        # 탐지 = 테스트가 실제로 실패했다. pytest가 테스트를 못 찾거나 설정 오류로 끝난 것(종료 코드 2 이상)은 탐지가 아니라 실행 오류다.
        try:
            res = _pytest([*sel, *common, "--jev-mutant", json.dumps({k: m[k] for k in ("path", "op", "site")}), "--junitxml", str(junit)])
            by = _failed(junit)
            status = "killed" if res.returncode == 1 and by else ("survived" if res.returncode == 0 else "error")
            err = "" if status != "error" else (res.stdout.strip().splitlines() or ["?"])[-1][:200]
        except subprocess.TimeoutExpired:
            by, status, err = [], "error", "timeout"
        return {**m, "tests": [t.split("::")[-1] for t in sel], "status": status, "killed": status == "killed", "killed_by": by, "error": err}

    with ThreadPoolExecutor(max_workers=workers) as ex:
        results = list(ex.map(one, mutants))
    print(f"[3/3] done in {time.time() - t0:.0f}s")

    killed = sum(r["killed"] for r in results)
    errors = [r for r in results if r["status"] == "error"]
    by_op = {op: {"total": sum(r["op"] == op for r in results), "killed": sum(r["op"] == op and r["killed"] for r in results)} for op in OPS}
    test_names = sorted({t.split("::")[-1] for t in tests})
    kills = {t: sum(t in r["killed_by"] for r in results) for t in test_names}
    report = {"generated_at": time.strftime("%Y-%m-%d %H:%M:%S"), "targets": targets, "base_url": base_url,
              "oracle_approved": bool(compare) and oracle.status(compare)["ok"],
              "mode": "expects+golden" if compare else "expects-only", "compare": str(compare) if compare else None,
              "total": len(results), "killed": killed, "errors": len(errors), "score": round(killed / len(results), 3) if results else None,
              "by_op": by_op, "test_kills": kills, "mutants": results}
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"\nmutation score {killed}/{len(results)} = {report['score']:.0%} ({report['mode']})")
    for op, v in by_op.items():
        if v["total"]:
            print(f"  {op:8s} {v['killed']}/{v['total']}  {OPS[op]}")
    print("  tests that killed nothing: " + (", ".join(t for t, k in kills.items() if not k) or "none"))
    if errors:
        print(f"  ! {len(errors)} mutant runs errored (not counted as detection): {errors[0]['error']}")
    survivors = [r for r in results if r["status"] == "survived"]
    if survivors:
        print(f"  survivors ({len(survivors)}) — each is a test gap or an equivalent mutant:")
        for r in survivors:
            print(f"    {r['id']} {r['op']:7s} {r['path']}: {r['desc'][:110]}")
    print(f"report: {out}")
    return report
