"""결함 주입(mutation)으로 테스트의 결함 탐지력을 잰다. 앱 코드는 건드리지 않고, 브라우저가 받는 응답(HTML/JS/JSON)을 가로채 바꾼다.

eastshift mutate e2e/<app> --base-url <as-is> --compare golden/<app>

1. 발견: 테스트를 한 번 돌리며 테스트별로 받은 응답을 모은다 (이 실행이 통과해야 한다).
2. 생성: 응답마다 결함 후보 자리를 찾는다 (연산자 OPS). (경로, 연산자)당 최대 N개를 고르게 뽑는다.
   여러 화면에 똑같이 나오는 자리(메뉴·머리글·공통 스크립트)는 처음 나온 경로에서만 뽑는다.
3. 실행: 결함 하나씩, 그 경로를 받은 테스트만 다시 돌린다. 하나라도 실패하면 탐지(killed)이고 거기서 멈춘다(--maxfail=1). 모두 통과하면 생존(survived).
   끝난 결과는 out 옆 <out>.partial.jsonl 에 바로 쌓인다. 끊긴 측정을 같은 기준으로 다시 돌리면 남은 결함만 돈다.
   승인 확인은 시작할 때 한 번 해서 결함마다 띄우는 pytest에 넘기고(oracle.PRECHECK), 끝날 때 다시 확인한다 (보고서의 승인 상태는 끝 시점).
4. 보고: 탐지율, 연산자별 탐지율, 생존 결함 목록(사람이 볼 것), 테스트별 먼저 잡은 결함 수(하한: 먼저 잡은 테스트 뒤는 돌지 않았다).

생존 결함은 두 종류다: 테스트가 못 보는 진짜 빈틈(테스트 보강), 관찰 가능한 차이가 없는 결함(동등 변이, 무시). 구분은 사람이 한다.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import xml.etree.ElementTree as ET
from bisect import bisect_right
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any
from urllib.parse import urljoin, urlparse
from .identity import canonical_ids
from .evidence import source_hash

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
# 한글 단위가 바로 붙은 값(120원, 3개, 2박)도 자리다. 식별자·CSS 단위·색상(#fff, x1, 12px)은 아스키 문자로 가려낸다
NUM = re.compile(r"(?<![A-Za-z0-9_.#%&-])(\d{1,3}(?:,\d{3})+|\d+(?:\.\d+)?)(?![A-Za-z0-9_%])")
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


def _merged(spans: list[tuple[int, int]]) -> list[tuple[int, int]]:
    """겹치지 않게 합치고 시작 순으로 정렬한다 (_inside의 이진 탐색 조건)."""
    out: list[tuple[int, int]] = []
    for a, b in sorted(spans):
        if out and a < out[-1][1]:
            out[-1] = (out[-1][0], max(out[-1][1], b))
        else:
            out.append((a, b))
    return out


def _inside(pos: int, spans: list[tuple[int, int]]) -> tuple[int, int] | None:
    """spans는 시작 순이고 서로 겹치지 않는다. 큰 응답(목록 화면, 번들 JS)에서 자리마다 전체를 훑지 않게 이진 탐색."""
    i = bisect_right(spans, (pos, sys.maxsize)) - 1
    return spans[i] if i >= 0 and spans[i][0] <= pos < spans[i][1] else None


def _ctx(text: str, start: int, end: int, repl: str) -> str:
    a, b = max(0, start - 25), min(len(text), end + 25)
    return (text[a:start] + "⟦" + text[start:end] + "→" + repl + "⟧" + text[end:b]).replace("\n", " ")


def sites(text: str, op: str, kind: str) -> list[Site]:
    """응답 본문에서 연산자 op를 적용할 자리들 (문서 순서, 결정론적)."""
    is_js = kind == "script" or text.lstrip()[:1] in ("{", "[")
    scripts = [(0, len(text))] if is_js else _spans(SCRIPT, text, 1)
    styles = [] if is_js else _merged(_spans(STYLE, text) + _spans(TITLE, text))
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


ID_SEG = re.compile(r"^(?:\d+|[0-9a-f]{8,}|[0-9a-fA-F-]{20,})$")


def route_key(path: str) -> str:
    """응답 경로 → 결함 자리의 열쇠. 숫자·해시 조각은 {id}: 주문 4번과 9번의 상세는 같은 화면이라 같은 결함이 들어가야 한다."""
    return "/" + "/".join("{id}" if ID_SEG.match(seg) else seg for seg in path.split("/") if seg)


class Capture:
    """발견 실행: 경로(route_key)별 첫 응답 본문과 테스트별 경로."""

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
            # 리다이렉트는 브라우저가 따라가게 둔다 (max_redirects=0). 여기서 따라가 버리면 저장 → 완료 화면의 주소가 바뀌지 않아
            # url 확인이 깨지고, 완료 화면 응답이 원래 주소로 잡힌다. 리다이렉트 뒤 요청은 새 내비게이션이라 route에 다시 걸린다
            resp = route.fetch(max_redirects=0)
        except Exception:
            return route.continue_()
        if 300 <= resp.status < 400:
            loc = resp.headers.get("location")
            if req.resource_type == "document" and loc:
                # 브라우저가 따라가는 리다이렉트는 route에 다시 걸리지 않아 완료 화면을 잡거나 결함을 넣을 수 없다.
                # 대신 location.replace로 이동시키면 새 내비게이션이라 route를 다시 지난다 (주소·이력은 리다이렉트와 같다)
                target = json.dumps(urljoin(resp.url, loc))
                return route.fulfill(status=200, content_type="text/html; charset=utf-8", body=f"<script>location.replace({target})</script>")
            return route.fulfill(response=resp)
        path = route_key(urlparse(resp.url).path)
        target = mutant is not None and path == mutant["path"]
        if target and mutant["op"] == "http500" and req.resource_type == "document":
            _mark(mutant)
            return route.fulfill(status=500, content_type="text/html; charset=utf-8", body="<h1>500 Internal Server Error</h1>")
        ctype = resp.headers.get("content-type", "")
        if not any(t in ctype for t in TEXTUAL):
            return route.fulfill(response=resp)
        text = resp.text()
        if capture is not None:
            capture.add(test, path, req.resource_type, text)
        if target and mutant["op"] != "http500":
            headers = {k: v for k, v in resp.headers.items() if k.lower() not in ("content-length", "content-encoding")}
            mutated = apply(text, mutant["op"], mutant["site"], req.resource_type)
            if mutated != text:  # 자리가 없으면(응답 본문이 발견 때와 달라짐) 결함이 안 들어간 것: 생존으로 세면 안 된다
                _mark(mutant)
            return route.fulfill(status=resp.status, headers=headers, body=mutated)
        return route.fulfill(response=resp)

    ctx.route("**/*", handler)


def _mark(mutant: dict[str, Any]) -> None:
    """결함이 실제로 응답에 들어갔다는 표식 (mutate 쪽이 이 파일로 '적용 안 됨'을 가려낸다)."""
    if mutant.get("marker"):
        try:
            Path(mutant["marker"]).touch()
        except OSError:
            pass


# -- 실행 ---------------------------------------------------------------------------
def _masked(text: str, rules: dict[str, Any]) -> list[tuple[str, list[tuple[int, int]]]]:
    """오라클 ignore 정규식마다 응답에서 가려지는 구간 (응답당 한 번만 찾는다)."""
    return [(rx, _merged(_spans(re.compile(rx), text))) for rx in rules.get("ignore", [])]


def _excluded(path: str, op: str, text: str, site: Site, rules: dict[str, Any],
              masked: list[tuple[str, list[tuple[int, int]]]] | None = None) -> str | None:
    """승인된 오라클 규칙으로 제외되는 결함이면 그 이유. 마스킹된 값은 비교하지 않으므로 바꿔도 차이가 없다."""
    for rx, spans in (masked if masked is not None else _masked(text, rules)):
        if _inside(site[0], spans):
            return f"masked by oracle ignore {rx!r}"
    for e in rules.get("equivalent_mutants", []):
        if e.get("path") == path and e.get("op") == op and e.get("context", "\0") in site[3]:
            return f"approved equivalent: {e.get('reason', '')}"
    return None


def generate(bodies: dict[str, dict[str, str]], max_per_op: int, rules: dict[str, Any] | None = None,
             stats: dict[str, int] | None = None) -> list[dict[str, Any]]:
    """결함 목록. 여러 화면에 똑같이 나오는 자리(메뉴·머리글·공통 스크립트: 연산자와 앞뒤 문맥이 같은 자리)는 경로 순으로
    처음 나온 경로에서만 후보가 된다. 공통 틀 하나를 화면 수만큼 되풀이해 재지 않게 하고, 뒤 화면의 몫은 그 화면에만 있는 내용으로 간다.
    stats가 있으면 이렇게 빠진 자리 수를 stats["shared"]에 센다."""
    rules = rules or {}
    mutants = []
    seen: dict[str, set[str]] = {op: set() for op in OPS}
    shared = 0
    for path, b in sorted(bodies.items()):
        masked = _masked(b["body"], rules)
        for op in OPS:
            if op == "http500":
                if b["kind"] == "document":
                    mutants.append({"path": path, "op": op, "site": 0, "desc": f"{path} → 500"})
                continue
            ss = sites(b["body"], op, b["kind"])
            eligible = [i for i, site in enumerate(ss) if not _excluded(path, op, b["body"], site, rules, masked)]
            keep = [i for i in eligible if ss[i][3] not in seen[op]]
            shared += len(eligible) - len(keep)
            seen[op].update(ss[i][3] for i in eligible)
            n = len(keep)
            if not n:
                continue
            pick = _spread(keep, max_per_op)
            mutants += [{"path": path, "op": op, "site": i, "desc": ss[i][3]} for i in pick]
    for i, m in enumerate(mutants):
        m["id"] = f"m{i:03d}"
    if stats is not None:
        stats["shared"] = shared
    return mutants


def _pytest(args: list[str], timeout: float | None, env: dict[str, str] | None = None) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", *args],
                          capture_output=True, text=True, timeout=timeout, env={**os.environ, **env} if env else None)


def _timeout(n_tests: int) -> int:
    """결함 하나의 실행 제한: 고른 테스트 수에 비례. 공통 자원(여러 화면이 받는 JS)의 결함은 테스트 수백 개를 돌린다."""
    return 300 + 60 * n_tests


def _resume_key(mutants: list[dict[str, Any]], tests: dict[str, list[str]], common: list[str], source_sha256: str) -> str:
    """이어 하기 기준: 결함 목록·테스트별 경로·실행 옵션·테스트 소스가 모두 같아야 지난 결과를 다시 쓴다."""
    blob = json.dumps([mutants, tests, common, source_sha256], ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(blob.encode()).hexdigest()


def _load_partial(partial: Path, key: str) -> dict[str, dict[str, Any]]:
    """중단된 측정의 끝난 결과 {id: result}. 기준이 다르면 버린다."""
    if not partial.exists():
        return {}
    lines = partial.read_text(encoding="utf-8").splitlines()
    try:
        if not lines or json.loads(lines[0]).get("key") != key:
            return {}
    except ValueError:
        return {}
    done = {}
    for line in lines[1:]:
        try:
            r = json.loads(line)
        except ValueError:  # 쓰다 끊긴 마지막 줄
            continue
        done[r["id"]] = r
    return done


def _failed(junit: Path) -> list[str]:
    if not junit.exists():
        return []
    out = []
    for tc in ET.parse(junit).getroot().iter("testcase"):
        if tc.find("failure") is not None or tc.find("error") is not None:
            ident = tc.find("./properties/property[@name='eastshift_id']")
            out.append(ident.get("value") if ident is not None else tc.get("name", "?"))
    return sorted(set(out))


def _spread(items: list[Any], n: int) -> list[Any]:
    """n개를 고르게 (처음과 끝 포함). n이 충분하면 전부."""
    if n <= 0 or len(items) <= n:
        return items
    return [items[j] for j in sorted({round(i * (len(items) - 1) / max(1, n - 1)) for i in range(n)})]


def run(targets: list[str], *, base_url: str, compare: Path | None, workers: int, max_per_op: int,
        allow_unapproved: bool, out: Path, max_mutants: int = 0) -> dict[str, Any]:
    common = ["--base-url", base_url] + (["--compare", str(compare)] if compare else []) + (["--allow-unapproved"] if allow_unapproved else [])
    work = Path(tempfile.mkdtemp(prefix="eastshift-mutate-"))
    from . import oracle
    # 승인 확인은 여기서 한 번: 결함마다 띄우는 pytest가 골든 전체(캡처 포함)를 다시 해시하지 않고 이 결과를 받는다. 끝날 때 다시 확인한다
    start = oracle.precheck(compare) if compare else None
    env: dict[str, str] = {}
    if start is not None:
        (work / "precheck.json").write_text(json.dumps(start, ensure_ascii=False), encoding="utf-8")
        env[oracle.PRECHECK] = str(work / "precheck.json")
    t0 = time.time()
    print(f"[1/3] discovery run on {base_url} ({'expects + golden' if compare else 'expects only'})")
    r = _pytest([*targets, *common, "--jev-capture", str(work / "cap")], timeout=None, env=env)  # 전체 테스트 한 바퀴: 규모에 따라 몇 시간도 걸린다
    if r.returncode != 0:
        shutil.rmtree(work, ignore_errors=True)
        sys.exit("discovery run must pass on the unmutated app before mutating:\n" + r.stdout[-3000:])
    bodies = json.loads((work / "cap/bodies.json").read_text(encoding="utf-8"))
    tests = json.loads((work / "cap/tests.json").read_text(encoding="utf-8"))
    identities = canonical_ids(list(tests))
    source_dir = Path(os.path.commonpath([str(Path(t.rsplit("::", 1)[0]).parent) for t in tests])).resolve() if tests else None
    source_sha256 = source_hash(source_dir) if source_dir else ""
    rules = oracle.load_config(compare) if compare else {}
    gen: dict[str, int] = {}
    mutants = generate(bodies, max_per_op, rules, gen)
    generated = len(mutants)
    mutants = _spread(mutants, max_mutants)  # 전체 상한: 경로·연산자 순으로 고르게 남긴다 (화면 수천 개면 결함 수십만 개가 된다)
    print(f"[2/3] {len(mutants)} mutants from {len(bodies)} responses, {len(tests)} tests"
          + (f" ({gen['shared']} sites shared with an earlier route skipped)" if gen["shared"] else "")
          + (f" (capped from {generated} by --max-mutants {max_mutants})" if len(mutants) < generated else ""))

    by_path: dict[str, list[str]] = defaultdict(list)  # 경로 → 그 응답을 받는 테스트 (결함마다 전체 테스트를 훑지 않게)
    for t, paths in tests.items():
        for pth in dict.fromkeys(paths):
            by_path[pth].append(t)

    def one(m: dict[str, Any]) -> dict[str, Any]:
        sel = by_path.get(m["path"], [])
        junit = work / f"{m['id']}.xml"
        # 고른 테스트는 인자 파일로 (@file). 공통 자원이면 수천 개라 명령줄 길이 한도를 넘는다
        argsfile = work / f"{m['id']}.args"
        argsfile.write_text("\n".join(sel) + "\n", encoding="utf-8")
        # 탐지 = 테스트가 실제로 실패했다. pytest가 테스트를 못 찾거나 설정 오류로 끝난 것(종료 코드 2 이상)은 탐지가 아니라 실행 오류다.
        marker = work / f"{m['id']}.applied"
        try:
            # 한 테스트가 잡으면 탐지가 확정되므로 나머지는 돌리지 않는다 (--maxfail=1). 공통 자원의 결함이 테스트 수백 개를 다 돌지 않게.
            # 그래서 killed_by는 먼저 잡은 테스트 하나다
            res = _pytest([f"@{argsfile}", *common, "--maxfail=1", "--jev-mutant", json.dumps({**{k: m[k] for k in ("path", "op", "site")}, "marker": str(marker)}),
                           "--junitxml", str(junit)], timeout=_timeout(len(sel)), env=env)
            by = _failed(junit)
            status = "killed" if res.returncode == 1 and by else ("survived" if res.returncode == 0 else "error")
            err = "" if status != "error" else (res.stdout.strip().splitlines() or ["?"])[-1][:200]
            if status != "error" and not marker.exists():
                status, err = "error", "mutant was never applied (response body differs from discovery run)"
        except subprocess.TimeoutExpired:
            by, status, err = [], "error", "timeout"
        for f in (junit, marker, argsfile):  # 결과는 partial.jsonl 에 남는다. 결함 수만 개의 사본을 임시 폴더에 쌓지 않는다
            f.unlink(missing_ok=True)
        return {**m, "tests": [identities[t] for t in sel], "status": status, "killed": status == "killed", "killed_by": by, "error": err}

    # 끝난 결과는 바로 out 옆 .partial.jsonl 에 쌓는다. 몇 시간짜리 측정이 중간에 끊겨도 같은 기준으로 다시 돌리면 남은 것만 돈다
    partial = out.with_name(out.name + ".partial.jsonl")
    key = _resume_key(mutants, tests, common, source_sha256)
    done = _load_partial(partial, key)
    if done:
        print(f"  resuming: {len(done)}/{len(mutants)} mutants already measured ({partial})")
    else:
        partial.parent.mkdir(parents=True, exist_ok=True)
        partial.write_text(json.dumps({"key": key}) + "\n", encoding="utf-8")
    todo = [m for m in mutants if m["id"] not in done]
    step = max(1, len(mutants) // 100)
    with ThreadPoolExecutor(max_workers=workers) as ex, partial.open("a", encoding="utf-8") as log:
        futures = [ex.submit(one, m) for m in todo]
        for fut in as_completed(futures):
            r = fut.result()
            done[r["id"]] = r
            log.write(json.dumps(r, ensure_ascii=False) + "\n")
            log.flush()
            if len(done) % step == 0 or len(done) == len(mutants):
                el = time.time() - t0
                print(f"  {len(done)}/{len(mutants)} mutants, {sum(x['killed'] for x in done.values())} killed, {el:.0f}s", flush=True)
    results = [done[m["id"]] for m in mutants]
    print(f"[3/3] done in {time.time() - t0:.0f}s")

    killed = sum(r["killed"] for r in results)
    errors = [r for r in results if r["status"] == "error"]
    by_op = {op: {"total": sum(r["op"] == op for r in results), "killed": sum(r["op"] == op and r["killed"] for r in results)} for op in OPS}
    test_names = sorted(identities.values())
    kills = {t: sum(t in r["killed_by"] for r in results) for t in test_names}
    end = oracle.status(compare) if compare else {}
    if start is not None and (end["ok"], end.get("approval_id")) != (start["status"]["ok"], start["status"].get("approval_id")):
        print(f"  ! oracle changed during the run (approved: {start['status']['ok']} at start, {end['ok']} at end); the report records the end state")
    report = {"generated_at": time.strftime("%Y-%m-%d %H:%M:%S"), "targets": targets, "base_url": base_url,
              "oracle_approved": bool(compare) and end["ok"],
              "oracle_approved_at": end.get("approved_at") if compare else None,
              "oracle_approval_id": oracle.approval_id(compare) if compare else None,
              "source_sha256": source_sha256,
              "source_dir": str(source_dir) if source_dir else "",
              "mode": "expects+golden" if compare else "expects-only", "compare": str(compare) if compare else None,
              "total": len(results), "killed": killed, "errors": len(errors), "score": round(killed / len(results), 3) if results else None,
              "by_op": by_op, "test_kills": kills, "stop_at_first_kill": True, "shared_sites_skipped": gen["shared"],
              "generated": generated, "max_mutants": max_mutants, "mutants": results}
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")
    partial.unlink(missing_ok=True)
    shutil.rmtree(work, ignore_errors=True)
    print(f"\nmutation score {killed}/{len(results)} = {(report['score'] or 0):.0%} ({report['mode']})")
    for op, v in by_op.items():
        if v["total"]:
            print(f"  {op:8s} {v['killed']}/{v['total']}  {OPS[op]}")
    print("  tests that were never first to kill: " + (", ".join(t for t, k in kills.items() if not k) or "none")
          + " (a mutant stops at its first failing test, so this is not 'killed nothing')")
    if errors:
        print(f"  ! {len(errors)} mutant runs errored (not counted as detection): {errors[0]['error']}")
    survivors = [r for r in results if r["status"] == "survived"]
    if survivors:
        print(f"  survivors ({len(survivors)}) — each is a test gap or an equivalent mutant:")
        for r in survivors:
            print(f"    {r['id']} {r['op']:7s} {r['path']}: {r['desc'][:110]}")
    print(f"report: {out}")
    return report
