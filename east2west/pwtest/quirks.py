"""as-is 이상 동작: 기록(quirks/<app>.json) → 통합 화면의 'as-is 이상 동작' 탭에서 고객 결정 → 판정 규칙과 소스 수정 작업 목록.

원칙: to-be 는 as-is 와 똑같이 동작한다 — as-is 동작이 이상해도(글자 없는 오류 창, 0 을 '조건 없음'으로 보는 조회, 빈 값에 서버 400 …) 따라 한다.
이상하다고 본 점은 면제하지 않고 여기에 적어 고객과 정한다: 현행 유지(to-be 도 그대로) · 수정(to-be 를 일부러 바꾼다) · 보류.
'수정'으로 정한 것만 판정 규칙(rules.py 의 고객 결정(수정) 갈래)이 되고, 그 기록에 이어진 테스트(+ match)의 차이만 덮는다.
현행 유지·보류·결정 전은 규칙을 만들지 않는다: to-be 가 as-is 와 다르면 그대로 결함이다.

파일 (프로젝트 루트의 quirks/, runs/ 옆. 골든이 아니라 승인 해시에 들지 않는다)
  quirks/<app>.json            기록 목록. 조사한 사람·에이전트가 쓴다
                               [{id, kind: quirk|env, title, severity: high|mid|low, strange, expected, impact, repro: [재현 단계], observed, cause,
                                 evidence: [근거], verified: run|source, tests: [골든 테스트 id], routes: [주소 경로], match: 다른 점 행 '무엇이' 정규식(선택),
                                 options: [고객 선택지], shot: {test, step, caption}}]
                               kind=env 는 as-is 동작이 아니라 로컬 환경(자료·설정) 때문에 다르게 보이는 것: 따로 보이고 결정·규칙 대상이 아니다
                               shot 은 as-is 캡처를 보일 골든 단계. step 은 골든 단계 번호(index, 0부터 — allowed_differences 의 step 과 같다)
  quirks/<app>.brief.json      화면에 보일 짧은 글 {id: {title, summary, expected, steps, see, impact, options, caption}} (선택, 없으면 기록 원문)
  quirks/<app>.decisions.json  고객 결정 {id: {decision: keep|change|hold, direction, confirmed_by, confirmed_at, memo, updated_at, history: [이전 값…]}}
                               통합 화면만 쓴다 (save_decision: 수정은 방향이 있어야 한다. 확인자·확인일은 입력받지 않고 예전 값만 지킨다. 고칠 때마다 이전 값은 history).
                               골든처럼 사람 것이다 — tools/guard_oracle.py 가 에이전트의 쓰기를 막는다

to-be 상태(state)는 적지 않고 실행 원장(runs/<app>/)에서 계산한다. 이어진 테스트마다 그 테스트를 돌린 마지막 실행을 보고,
하나라도 다르면 diff, 아니고 같은 것이 있으면 same, 모두 결함 아닌 갈래(자료·환경 등)로 승인됐거나 match 행을 못 찾으면 unknown, 실행이 없으면 none,
이어진 테스트가 없으면(소스로만 판단한 기록) untracked.
'수정'으로 정한 기록은 changed(고객 결정 규칙에 걸린 차이가 났다 = to-be 가 결정대로 바뀌었다) / pending(아직 as-is 와 같다).

화면 (hub 의 'as-is 이상 동작' 탭, fragment): 요약 칸 · 결정 거르기 · 목록 → 상세(as-is 캡처(골든) / to-be 캡처(마지막 실행의 같은 단계, 없으면 실패 순간) 전환,
크게 보기, 재현, 영향, 결정 입력), '판정에 반영'(결정에서 만든 규칙을 보이고 to-be 비교를 다시 돌린다 — 규칙은 다음 비교부터 쓰인다),
결정 내보내기(.md: 소스 수정 작업 목록), 고객용 HTML(standalone: 모든 단계 캡처를 품고(같은 그림은 한 번) 결정은 읽기만).
`east2west quirks golden/<app>` 은 같은 작업 목록과 규칙을 터미널에 찍는다 (에이전트가 소스를 고칠 때 읽는다).
"""
from __future__ import annotations

import base64
import json
import re
import threading
import time
from pathlib import Path
from typing import Any

from . import html, ledger, rules

ROOT = Path("quirks")
SEV = {"high": "업무 영향 큼", "mid": "업무 영향 중간", "low": "업무 영향 작음"}
TOBE = {"same": "to-be 맞춤", "diff": "to-be 아직 다름", "unknown": "비교 못 함", "none": "비교 실행 없음", "untracked": "재는 테스트 없음",
        "changed": "고객 결정대로 바꿈", "pending": "수정 전 (as-is 그대로)"}  # 아래 둘은 '수정'으로 정한 기록
VERIFIED = {"run": "실행으로 확인", "source": "소스로 판단"}
DECISION = {"": "결정 전", "keep": "현행 유지", "change": "수정", "hold": "보류"}
FIELDS = ("decision", "direction", "confirmed_by", "confirmed_at", "memo")
DATE = re.compile(r"\d{4}-\d{2}-\d{2}")
_lock = threading.Lock()


def path(app: str, side: str = "") -> Path:
    return ROOT / (f"{app}.{side}.json" if side else f"{app}.json")


def _read(p: Path, empty: Any) -> Any:
    if not p.exists():
        return empty
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except ValueError as e:
        raise ValueError(f"{p}: JSON 을 읽지 못했습니다 ({e})") from e
    if not isinstance(data, type(empty)):
        raise ValueError(f"{p}: {'목록(배열)' if isinstance(empty, list) else '객체'}이어야 합니다")
    return data


def load(app: str) -> list[dict[str, Any]]:
    """기록 목록 (id 가 있는 것만). 파일이 없으면 빈 목록, 깨졌으면 ValueError."""
    return [q for q in _read(path(app), []) if isinstance(q, dict) and q.get("id")]


def count(app: str) -> int:
    """통합 화면 탭의 숫자: 검토할 기록 수 (환경 차이 제외). 파일이 없거나 깨졌으면 0."""
    try:
        return sum(1 for q in load(app) if q.get("kind") != "env")
    except ValueError:
        return 0


def briefs(app: str) -> dict[str, Any]:
    return _read(path(app, "brief"), {})


def decisions(app: str) -> dict[str, Any]:
    return _read(path(app, "decisions"), {})


def problems(app: str, golden: Path | None = None) -> list[str]:
    """기록 파일의 문제: 화면 위 알림과 east2west quirks 가 보인다. golden 이 있으면 이어진 테스트·캡처 단계가 골든에 있는지도 본다."""
    out: list[str] = []
    try:
        raw, dec = _read(path(app), []), decisions(app)
        briefs(app)
    except ValueError as e:
        return [str(e)]
    seen: set[str] = set()
    for i, q in enumerate(raw):
        at = f"{path(app).name}[{i}]"
        if not isinstance(q, dict) or not q.get("id"):
            out.append(f"{at}: id 가 없습니다")
            continue
        at = f"{q['id']}"
        if q["id"] in seen:
            out.append(f"{at}: id 가 겹칩니다")
        seen.add(q["id"])
        if not q.get("title"):
            out.append(f"{at}: title 이 없습니다")
        if q.get("kind", "quirk") not in ("quirk", "env"):
            out.append(f"{at}: kind 는 quirk 또는 env")
        if q.get("severity", "mid") not in SEV:
            out.append(f"{at}: severity 는 high · mid · low")
        if q.get("match"):
            try:
                re.compile(q["match"])
            except (re.error, TypeError) as e:
                out.append(f"{at}: match 정규식이 틀렸습니다 ({e}) — 결정해도 규칙을 만들지 않습니다")
        tests = q.get("tests", [])
        if not isinstance(tests, list) or not all(isinstance(t, str) for t in tests):
            out.append(f"{at}: tests 는 테스트 id 목록")
            tests = []
        shot = q.get("shot") or {}
        if golden is not None:
            out += [f"{at}: 골든에 없는 테스트 {t}" for t in tests if not (golden / f"{t}.json").exists()]
            if shot.get("test") and not (golden / f"{shot['test']}.json").exists():
                out.append(f"{at}: shot.test {shot['test']} 이 골든에 없습니다")
        if shot and not isinstance(shot.get("step", 0), int):
            out.append(f"{at}: shot.step 은 골든 단계 번호(0부터)")
        if q.get("kind") != "env" and (dec.get(q["id"]) or {}).get("decision") == "change" and not tests and not q.get("match"):
            out.append(f"{at}: '수정'으로 정했지만 tests 도 match 도 없어 판정 규칙을 만들 수 없습니다")
    out += [f"결정만 있고 기록이 없는 id: {k}" for k in dec if k not in seen]
    return out


def save_decision(app: str, qid: str, body: dict[str, Any], now: float | None = None) -> dict[str, Any]:
    """한 기록의 고객 결정을 저장한다 (통합 화면의 POST 만 부른다). 바뀌기 전 값은 history 에 남긴다 — 누가 언제 무엇으로 정했는지 나중에 본다.
    수정은 어떻게 바꿀지(direction)가 있어야 한다 (소스를 고칠 사람이 읽는다). 확인자·확인일(confirmed_by·at)은 화면에서 받지 않고
    (사용자 결정 2026-10-04: 입력 칸을 없앴다) 예전 값을 그대로 넘겨 지우지 않는다 — 값이 있으면 확인일 형식만 본다."""
    q = next((x for x in load(app) if x["id"] == qid), None)
    if q is None:
        raise ValueError(f"모르는 기록: {qid}")
    if q.get("kind") == "env":
        raise ValueError("환경 차이 기록은 결정 대상이 아닙니다 (to-be 가 따라 할 as-is 동작이 아니다)")
    new = {k: str(body.get(k, "") or "").strip()[:4000] for k in FIELDS}
    d = new["decision"]
    if d not in DECISION:
        raise ValueError("decision 은 keep · change · hold · 빈 값(결정 전) 중 하나입니다")
    if d == "change" and not new["direction"]:
        raise ValueError("수정으로 정하면 수정 방향을 적어야 합니다 (소스를 고칠 사람이 읽습니다)")
    if new["confirmed_at"] and not DATE.fullmatch(new["confirmed_at"]):
        raise ValueError("확인일은 YYYY-MM-DD 형식입니다")
    with _lock:
        allx = decisions(app)
        old = allx.get(qid) or {}
        if old and {k: old.get(k, "") for k in FIELDS} == new:
            return old
        hist = [*old.get("history", []), {k: old.get(k, "") for k in (*FIELDS, "updated_at")}] if old else []
        rec = {**new, "updated_at": time.strftime("%Y-%m-%d %H:%M", time.localtime(now)), "history": hist[-20:]}
        allx[qid] = rec
        p = path(app, "decisions")
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_suffix(".tmp")
        tmp.write_text(json.dumps(allx, ensure_ascii=False, indent=1), encoding="utf-8")
        tmp.replace(p)
    return rec


def decision_rules(app: str) -> list[dict[str, Any]]:
    """고객이 '수정'으로 정한 기록(환경 차이 제외) → 판정 규칙. to-be 를 일부러 as-is 와 다르게 바꾼 것이므로 그 기록에 이어진 테스트(tests)의
    차이를 '고객 결정(수정)'으로 센다. 범위는 그 테스트로만 좁히고 match 가 있으면 그 행으로 더 좁힌다 (같은 화면의 다른 결함까지 덮지 않게).
    tests 도 match 도 없으면 어디에 걸지 정할 수 없어 만들지 않는다. 파일이 깨졌으면 규칙 없이 비교한다 (덮는 것보다 결함으로 남는 쪽이 안전하다)."""
    try:
        recs, dec = {q["id"]: q for q in load(app)}, decisions(app)
    except ValueError:
        return []
    out = []
    for qid, d in dec.items():
        q = recs.get(qid)
        if not q or q.get("kind") == "env" or not isinstance(d, dict) or d.get("decision") != "change":
            continue
        tests = [t for t in q.get("tests") or [] if isinstance(t, str) and t]
        rule = {"class": "customer", "quirk": qid,
                "reason": f"고객 결정 {qid} ({d.get('confirmed_by') or '확인자 없음'} {d.get('confirmed_at') or ''}): {d.get('direction') or '수정'}".replace(" )", ")")}
        if tests:
            rule["tests"] = tests
        if q.get("match"):
            rule["what"] = q["match"]
        if (tests or q.get("match")) and not rules.problems([rule]):
            out.append(rule)
    return out


def _latest_cases(app: str) -> dict[str, tuple[dict[str, Any], dict[str, Any]]]:
    """테스트 → (그 테스트를 돌린 마지막 실행, 그 결과). 일부만 다시 돌린 실행(-k)이 다른 테스트의 상태를 지우지 않게 테스트마다 본다."""
    out: dict[str, tuple[dict[str, Any], dict[str, Any]]] = {}
    for r in ledger.load_runs(app):  # 오래된 것부터: 뒤의 실행이 덮는다
        for name, c in (r.get("cases") or {}).items():
            out[name] = (r, c)
    return out


def _outcome(q: dict[str, Any], case: dict[str, Any]) -> tuple[str, str]:
    """이어진 테스트 하나의 결과 → (same | diff | unknown | customer, 이유). customer = 이 기록의 고객 결정 규칙에 걸린 차이가 났다."""
    mine = lambda x: isinstance(x, dict) and x.get("class") == "customer" and (x.get("quirk") or (x.get("rule") or {}).get("quirk")) == q["id"]
    accepted = case.get("accepted") or []
    if case.get("status") == "pass":
        if case.get("kind") != "accepted_diff":
            return "same", ""
        return ("customer", "") if any(mine(a) for a in accepted) else ("unknown", case.get("summary", ""))
    rows, notes = case.get("rows") or [], case.get("row_classes") or []
    rx = q.get("match")
    try:
        picked = [(r, notes[i] if i < len(notes) else None) for i, r in enumerate(rows) if not rx or re.search(rx, str(r[0]))]
    except re.error:
        picked = []
    if any(n is None for _, n in picked):
        return "diff", next(str(r[0]) for r, n in picked if n is None)
    if any(mine(n) for _, n in picked) or any(mine(a) for a in accepted):
        return "customer", ""
    if picked or rx:
        return "unknown", ""  # 모두 결함 아닌 갈래이거나, 이 실행에서 그 행이 보이지 않았다
    return "diff", case.get("summary", "")[:80] or case.get("kind", "")


def state(app: str, items: list[dict[str, Any]], dec: dict[str, Any] | None = None) -> dict[str, dict[str, Any]]:
    """기록 id → {tobe, open: [아직 다른 테스트: 이유]}. 우선순위: diff > (하나라도 same 이면) same > unknown. 실행이 없으면 none.
    '수정'으로 정한 기록: 결정 규칙에 걸린 차이가 났으면 changed, 아직 같으면 pending."""
    latest, dec = _latest_cases(app), decisions(app) if dec is None else dec
    out = {}
    for q in items:
        outs, open_ = [], []
        for t in q.get("tests") or []:
            if t in latest:
                o, why = _outcome(q, latest[t][1])
                outs.append(o)
                if o == "diff":
                    open_.append(f"{t}: {why}" if why else t)
        change = (dec.get(q["id"]) or {}).get("decision") == "change"
        if not q.get("tests"):
            tobe = "untracked"  # 소스로만 판단한 기록: 이어진 테스트가 없어 실행으로는 알 수 없다
        elif not outs:
            tobe = "none"
        elif change:
            tobe = "changed" if "customer" in outs else "diff" if "diff" in outs else "pending" if "same" in outs else "unknown"
        else:
            tobe = "diff" if ("diff" in outs or "customer" in outs) else "same" if "same" in outs else "unknown"
        out[q["id"]] = {"tobe": tobe, "open": open_}
    return out


def _tobe_shot(app: str, run: dict[str, Any], case: dict[str, Any], test: str, step: int) -> Path | None:
    """마지막 실행의 같은 단계 to-be 캡처. 원장의 case['tobe_shots'](목록·사전 어느 모양이든)나 runs/<app>/<시각>/shots/<테스트>-tobe-NN.jpg."""
    shots, cand = case.get("tobe_shots"), None
    if isinstance(shots, dict):
        cand = shots.get(str(step), shots.get(step))
    elif isinstance(shots, list):
        cand = next((s for s in shots if isinstance(s, dict) and s.get("step") == step), None) or (shots[step] if 0 <= step < len(shots) else None)
    if isinstance(cand, dict):
        cand = cand.get("path") or cand.get("shot") or cand.get("file")
    folder = ledger.run_dir(app) / run.get("_stamp", "") / "shots"
    tries = [Path(cand), folder / Path(cand).name] if isinstance(cand, str) and cand else []
    tries += [folder / f"{n}-tobe-{step:02d}.jpg" for n in dict.fromkeys((test, re.sub(r"[^\w.-]+", "_", test)))]
    return next((p for p in tries if p.is_file()), None)


def _url(p: Path | None, embed: bool) -> str:
    if p is None or not p.is_file():
        return ""
    if embed:
        return f"data:{'image/png' if p.suffix == '.png' else 'image/jpeg'};base64," + base64.b64encode(p.read_bytes()).decode()
    return html.shot_url(p)


def _frames(app: str, golden: Path | None, q: dict[str, Any], latest: dict[str, Any], embed: bool) -> tuple[list[dict[str, Any]], str]:
    """기록의 캡처: 그 테스트의 골든 단계마다 as-is(골든) · to-be(마지막 실행의 같은 단계). 고객용 HTML(embed)도 모든 단계를 품는다 —
    크게 보기에서 이전·다음 단계로 넘기려면 필요하다 (사용자 결정 2026-10-05). 같은 그림은 standalone 이 한 번만 싣는다.
    둘째 값은 같은 단계 to-be 캡처가 없을 때 대신 보일 실패 순간 화면."""
    s = q.get("shot") or {}
    test, step = s.get("test"), s.get("step")
    if not test or golden is None or not (golden / f"{test}.json").exists():
        return [], ""
    try:
        steps = json.loads((golden / f"{test}.json").read_text(encoding="utf-8")).get("steps", [])
    except ValueError:
        return [], ""
    from .map import _plain
    run, case = latest.get(test, ({}, {}))
    frames = []
    for o in steps:
        b = _tobe_shot(app, run, case, test, o["index"]) if case else None
        frames.append({"step": o["index"], "asis": _url(golden / o["shot"] if o.get("shot") else None, embed), "tobe": _url(b, embed),
                       "action": _plain(html._action(o.get("kind", ""), o.get("text", "")))})
    fail = case.get("screenshot") if case else ""
    return frames, _url(Path(fail), embed) if fail else ""


def data(app: str, golden: Path | None = None, tests_dir: Path | None = None, embed: bool = False) -> dict[str, Any]:
    """화면에 넘길 자료. embed=True 면 캡처를 data: 주소로 넣는다 (고객용 HTML)."""
    try:
        every, brief, dec = load(app), briefs(app), decisions(app)
    except ValueError as e:
        every, brief, dec = [], {}, {}
        broken = str(e)
    else:
        broken = ""
    docs = html.docstrings(tests_dir) if tests_dir else {}
    st = state(app, [q for q in every if q.get("kind", "quirk") != "env"], dec)
    latest = _latest_cases(app)
    out = []
    for q in every:
        b = brief.get(q["id"]) if isinstance(brief.get(q["id"]), dict) else {}
        frames, fail = _frames(app, golden, q, latest, embed)
        step = (q.get("shot") or {}).get("step")
        here = next((f for f in frames if f["step"] == step), None) or {}
        tests = [t for t in q.get("tests") or [] if isinstance(t, str)]
        out.append({
            "id": q["id"], "env": q.get("kind") == "env", "severity": q.get("severity", "mid") if q.get("severity") in SEV else "mid",
            "where": list(q.get("routes") or []) or [html._title(t, docs) for t in tests[:2]] + ([f"외 {len(tests) - 2}개"] if len(tests) > 2 else []),
            "verified": VERIFIED.get(q.get("verified", ""), ""), "tobe": st.get(q["id"], {}).get("tobe", "none"), "open": st.get(q["id"], {}).get("open", []),
            "title": b.get("title") or q.get("title", ""), "summary": b.get("summary") or q.get("strange", ""), "expected": b.get("expected") or q.get("expected", ""),
            "steps": b.get("steps") or q.get("repro", []), "see": b.get("see") or q.get("observed", ""), "impact": b.get("impact") or q.get("impact", ""),
            "options": b.get("options") or q.get("options", []), "caption": b.get("caption") or (q.get("shot") or {}).get("caption", ""),
            "shots": {"asis": here.get("asis", ""), "tobe": here.get("tobe") or fail, "tobe_fail": bool(fail and not here.get("tobe"))},
            "captures": frames, "shot_step": step,
            "tests": [{"name": t, "title": html._title(t, docs)} for t in tests],
            "more": {k: q.get(k) for k in ("strange", "observed", "cause", "evidence", "repro", "options", "routes", "match", "found")},
            "decision": dec.get(q["id"]) if isinstance(dec.get(q["id"]), dict) else {},
        })
    runs = ledger.load_runs(app)
    last = runs[-1]["finished"] if runs else ""
    made = decision_rules(app)
    stale = sum(1 for k, d in dec.items() if isinstance(d, dict) and d.get("updated_at", "") > last[:16]) if last else 0
    d = {"app": app, "items": out, "standalone": embed, "problems": ([broken] if broken else problems(app, golden)),
         "labels": {"sev": SEV, "tobe": TOBE, "decision": DECISION, "rule": rules.LABEL},
         "rules": [{"quirk": r["quirk"], "tests": r.get("tests", []), "what": r.get("what", ""), "reason": r["reason"]} for r in made],
         "last_run": last, "stale": stale}
    if embed:
        d["md"] = decisions_md(app)
    return d


def decisions_md(app: str) -> str:
    """고객 결정 → 소스 수정 작업 목록 (수정 건은 무엇을 어떻게 바꿀지, 원인·근거·지금 to-be 상태까지)과 결정에서 만든 판정 규칙. 에이전트가 읽고 소스를 고친다."""
    every, dec = load(app), decisions(app)
    items = [q for q in every if q.get("kind") != "env"]
    st = state(app, items, dec)
    made = {r["quirk"]: r for r in decision_rules(app)}
    key = lambda q: (dec.get(q["id"]) or {}).get("decision") or ""
    group = lambda k: [q for q in items if key(q) == k]
    L = [f"# as-is 이상 동작 — 고객 결정과 소스 수정 작업 ({app})", "",
         f"전체 {len(items)}건 · 수정 {len(group('change'))} · 현행 유지 {len(group('keep'))} · 보류 {len(group('hold'))} · 결정 전 {len(group(''))}"
         + (f" · 환경 차이 {len(every) - len(items)}건 별도" if len(every) > len(items) else ""), "",
         "원칙: to-be 는 as-is 와 똑같이 동작한다. '수정'으로 정한 것만 to-be 를 as-is 와 다르게 바꾼다. 그 차이는 아래 판정 규칙이 다음 to-be 비교부터",
         "'고객 결정(수정)'으로 센다 (east2west ui → as-is 이상 동작 탭 → 판정에 반영). 현행 유지·보류·결정 전은 as-is 그대로 따른다.", ""]
    for k, head in (("change", "수정 — 소스를 바꿀 것"), ("hold", "보류"), ("keep", "현행 유지 — to-be 는 지금처럼 as-is 를 따른다"), ("", "결정 전")):
        xs = group(k)
        if not xs:
            continue
        L += [f"## {head} ({len(xs)})", ""]
        for q in xs:
            d = dec.get(q["id"]) or {}
            L += [f"### {q['id']} · {q.get('title', '')}", "",
                  f"- 이어진 테스트: {', '.join(q.get('tests') or []) or '없음'}" + (f" · 화면: {', '.join(q.get('routes') or [])}" if q.get("routes") else ""),
                  f"- 무엇이 이상한가: {q.get('strange', '') or '-'}"]
            if k:
                L += [f"- 결정: {DECISION[k]}" + (f" — {d['direction']}" if d.get("direction") else ""),
                      f"- 확인: {d.get('confirmed_by') or '-'} · {d.get('confirmed_at') or '-'} (저장 {d.get('updated_at', '-')})"]
            if d.get("memo"):
                L.append(f"- 결정 사항: {d['memo']}")
            s = st.get(q["id"], {})
            L.append(f"- 지금 to-be: {TOBE[s.get('tobe', 'none')]}" + (f" ({'; '.join(s['open'][:3])})" if s.get("open") else ""))
            if k == "change":
                L += [f"- 보통 기대하는 동작: {q.get('expected') or '-'}", f"- as-is 원인: {q.get('cause') or '-'}"]
                L += [f"  - 근거 `{e}`" for e in (q.get("evidence") or [])[:8]]
                r = made.get(q["id"])
                L.append(f"- 판정 규칙: {_rule_text(r)}" if r else "- 판정 규칙: 만들지 못함 — 기록에 tests 도 match 도 없다 (차이는 결함으로 남는다)")
            L.append("")
    L += ["## 판정 규칙 (결정에서 자동 · 다음 to-be 비교부터)", ""]
    L += [f"- {qid}: {_rule_text(r)}" for qid, r in made.items()] or ["- 없음 ('수정'으로 정한 기록이 없다)"]
    return "\n".join(L) + "\n"


def _rule_text(r: dict[str, Any]) -> str:
    scope = [f"테스트 {', '.join(r['tests'])}" if r.get("tests") else "", f"'무엇이' /{r['what']}/" if r.get("what") else ""]
    return f"{rules.LABEL['customer']} · {' · '.join(x for x in scope if x)} · 사유 \"{r['reason']}\""


CSS = """
main{max-width:1500px;padding:20px 28px 48px;gap:0}
.qwrap{display:grid;grid-template-columns:minmax(0,1fr);gap:18px;min-width:0}
.qhead{display:flex;gap:16px;justify-content:space-between;align-items:center}
.qhead h1{font-size:26px;line-height:1.35;letter-spacing:-.035em;margin:3px 0 6px}
.qhead p{margin:0;color:var(--muted);font-size:13px;line-height:1.6;max-width:80ch}
.qact{display:flex;gap:8px;flex:none;flex-wrap:wrap;justify-content:flex-end}
.qact button,.qact a{font:600 13px var(--sans);padding:10px 14px;border:1px solid var(--line);border-radius:8px;background:var(--surface);color:var(--ink);cursor:pointer;text-decoration:none;white-space:nowrap}
.qact .primary{background:var(--accent);border-color:var(--accent);color:#fff}
.qnote{margin:0;padding:10px 14px;border-radius:8px;background:var(--warn-soft);color:var(--warn);font-size:12.5px;line-height:1.6}.qnote b{margin-right:6px}
.qapply{background:var(--surface);border:1px solid var(--line);border-radius:12px;padding:16px 18px;display:grid;gap:10px}.qapply[hidden]{display:none}
.qapply h3{margin:0;font-size:15px}.qapply p{margin:0;font-size:12.5px;color:var(--muted);line-height:1.65}
.qapply ul{margin:0;padding-left:18px;font-size:12.5px;line-height:1.7}.qapply li code{font:11.5px var(--mono);background:var(--sunk);padding:1px 5px;border-radius:4px}
.qapply .row{display:flex;gap:10px;align-items:center;flex-wrap:wrap}.qapply .stale{color:var(--warn);font-weight:600}
.qapply button{font:600 13px var(--sans);padding:9px 14px;border-radius:8px;border:0;background:var(--accent);color:#fff;cursor:pointer}.qapply button:disabled{opacity:.45;cursor:default}
.qapply .job{font:12px/1.5 var(--mono);color:var(--muted);white-space:pre-wrap;overflow-wrap:anywhere}.qapply .job.fail{color:var(--bad)}.qapply .job.ok{color:var(--ok)}
.qsum{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));background:var(--surface);border:1px solid var(--line);border-radius:12px;overflow:hidden}
.tile{padding:12px 18px;display:grid;grid-template-columns:auto 1fr;gap:2px 12px;align-items:center;border-right:1px solid var(--line)}.tile:last-child{border:0}
.tile b{font:600 25px/1.2 var(--mono);grid-row:1/3;letter-spacing:-.06em}.tile b span{font-size:14px;color:var(--muted)}.tile .k{font-size:13px;font-weight:600}.tile small{font-size:11px;color:var(--muted)}
.tile.priority b{color:var(--bad)}.tile.progress b{color:var(--accent)}
.qfil{display:flex;flex-wrap:wrap;gap:10px;align-items:center}
.seg{display:inline-flex;gap:3px;background:var(--sunk);border-radius:8px;padding:3px}
.seg button{font:500 12px var(--sans);padding:7px 10px;min-height:34px;border:0;border-radius:6px;background:transparent;color:var(--muted);cursor:pointer}
.seg button[aria-pressed=true]{background:var(--surface);color:var(--accent);box-shadow:0 1px 4px #18211f14;font-weight:700}.seg button small{margin-left:5px;font:11px var(--mono);opacity:.75}
.qfil select,.qfil input{font:13px var(--sans);height:40px;padding:8px 10px;border:1px solid var(--line);border-radius:8px;background:var(--surface);color:var(--ink);min-width:0}
.qfil select{max-width:280px}.qfil input{flex:1 1 180px}.qfil [data-reset]{font:12px var(--sans);border:0;background:transparent;color:var(--accent);cursor:pointer;padding:8px}
.qmain{display:grid;grid-template-columns:310px minmax(0,1fr);gap:18px;align-items:stretch;min-width:0}
.qlist{background:var(--surface);border:1px solid var(--line);border-radius:12px;overflow:auto;min-height:0;position:relative;contain:size;scrollbar-width:thin;scrollbar-color:var(--line) transparent}
.qlist h5{margin:0;padding:13px 16px;font-size:12px;font-weight:600;color:var(--muted);position:sticky;top:0;background:var(--surface);z-index:1;border-bottom:1px solid var(--line);display:flex;justify-content:space-between}
.qlist h5 span{font-weight:400}
.qrow{display:grid;grid-template-columns:minmax(0,1fr) auto;gap:8px;width:100%;text-align:left;font:inherit;background:transparent;border:0;border-bottom:1px solid var(--line);border-left:3px solid transparent;padding:14px 13px;cursor:pointer;color:var(--ink)}
.qrow:hover{background:#f7faf8}.qrow[aria-current=true]{background:var(--accent-soft);border-left-color:var(--accent)}
.qrow .t{grid-column:1/-1;font-size:13px;line-height:1.6;font-weight:500;overflow-wrap:anywhere}.qrow[aria-current=true] .t{font-weight:600;color:var(--accent)}
.qrow .m{font-size:10.5px;color:var(--muted);line-height:1.6;overflow-wrap:anywhere;display:-webkit-box;-webkit-line-clamp:2;-webkit-box-orient:vertical;overflow:hidden}.qrow .d{display:flex;gap:5px;align-items:center;font-size:10px;color:var(--muted)}
.dot{width:6px;height:6px;border-radius:50%;display:inline-block;flex:none}.dot.high{background:var(--bad)}.dot.mid{background:var(--warn)}.dot.low{background:var(--faint)}
.pill{display:inline-block;border-radius:5px;padding:3px 8px;font-size:11px;font-weight:600;line-height:1.45;white-space:nowrap}
.d-{background:var(--sunk);color:var(--muted)}.d-keep{background:var(--ok-soft);color:var(--ok)}.d-change{background:#EEEAFB;color:#6B4FD6}.d-hold{background:var(--warn-soft);color:var(--warn)}
.s-high{background:var(--bad-soft);color:var(--bad)}.s-mid{background:var(--warn-soft);color:var(--warn)}.s-low{background:var(--sunk);color:var(--muted)}
.t-changed{background:#EEEAFB;color:#6B4FD6}.t-pending{background:var(--warn-soft);color:var(--warn)}.t-same{background:var(--ok-soft);color:var(--ok)}.t-diff{background:var(--bad-soft);color:var(--bad)}.t-unknown,.t-none,.t-untracked{background:var(--sunk);color:var(--muted)}
.qdet{background:var(--surface);border:1px solid var(--line);border-radius:12px;padding:22px;display:grid;gap:16px;align-content:start;min-width:0;overflow-wrap:anywhere}
.qtop{display:flex;gap:6px 10px;flex-wrap:wrap;align-items:center;font-size:11px;color:var(--muted)}.qtop code{font:600 12px var(--mono);color:var(--accent)}
.qtitle{display:grid;gap:12px}.qdet h2{font-size:22px;line-height:1.5;letter-spacing:-.025em;font-weight:700;margin:0;max-width:42ch;text-wrap:pretty}
.facts{display:grid;grid-template-columns:1.15fr 1fr;gap:20px}.facts>div{padding:15px 16px;border-radius:8px;background:#f6f8f7}.facts .impact{background:#fff8ee;border-left:3px solid #d99a4b}
.facts h4,.decide h4,.evidence h4{margin:0 0 7px;font-size:12px;font-weight:600;color:var(--muted)}.facts .impact h4{color:var(--warn)}
.facts p,.evidence p{margin:0;font-size:13px;line-height:1.8}.evidence ol{margin:0;padding-left:22px;font-size:13px;line-height:1.8}.evidence li+li{margin-top:5px}.evidence .see{margin-top:10px;color:var(--muted)}
.decide{border-top:1px solid var(--line);padding-top:16px;display:grid;gap:12px;margin:0}.decide-head{display:flex;align-items:center;justify-content:space-between;gap:10px}.decide h3{font-size:16px;font-weight:700;margin:0}
.choice{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:8px;border:0;padding:0;margin:0}.choice legend{position:absolute;width:1px;height:1px;overflow:hidden;clip-path:inset(50%)}
.choice label{border:1px solid var(--line);border-radius:8px;padding:12px;cursor:pointer;display:grid;grid-template-columns:16px 1fr;gap:5px 8px;align-content:start;background:var(--surface)}
.choice input{width:15px;height:15px;margin:3px 0 0;accent-color:var(--accent)}.choice label b{font-size:13px}.choice label span{grid-column:2;font-size:11px;line-height:1.55;color:var(--muted)}
.choice label:has(input:checked){border-color:var(--accent);background:#f0f8f5;box-shadow:inset 0 0 0 1px var(--accent)}.choice label:has(input:focus-visible){outline:2px solid var(--accent);outline-offset:2px}
.decide .field{display:grid;gap:6px;font-size:12px;font-weight:600;color:var(--muted)}.decide .who{display:grid;grid-template-columns:minmax(0,1fr) 180px;gap:10px}
.decide textarea,.decide input[type=text],.decide input[type=date]{font:13px var(--sans);padding:9px 11px;border:1px solid var(--line);border-radius:7px;background:var(--surface);color:var(--ink);width:100%;min-width:0}.decide textarea{min-height:70px;resize:vertical}
.direction[hidden]{display:none}.sugg{display:flex;flex-wrap:wrap;gap:6px;margin-top:8px}.sugg button{font:11px var(--sans);padding:6px 9px;border-radius:6px;border:1px solid var(--line);background:var(--surface);color:var(--accent);cursor:pointer;text-align:left}
.field p{margin:2px 0 0;font-size:11px;font-weight:400;color:var(--muted);line-height:1.6}.decision-note textarea{min-height:88px}
.save{display:flex;align-items:center;gap:12px;flex-wrap:wrap;margin:0}.save button{font:600 13px var(--sans);padding:10px 18px;border-radius:7px;border:0;background:var(--accent);color:white;cursor:pointer}.save button:disabled{opacity:.45;cursor:default}.save span{font-size:11px;color:var(--muted)}.save span.err{color:var(--bad);font-weight:600}
.save .next{margin-left:auto;background:var(--surface);color:var(--accent);border:1px solid var(--line);font-size:12px;padding:9px 12px}
details.hist{font-size:11.5px;color:var(--muted)}details.hist summary{cursor:pointer}details.hist ol{margin:6px 0 0;padding-left:20px;line-height:1.7}
details.more{border-top:1px solid var(--line);padding-top:14px}.evidence summary,details.more summary{cursor:pointer;font-size:12px;color:var(--muted);font-weight:500}.evidence{border:1px solid var(--line);border-radius:8px;padding:14px 16px;background:#fafcfb}.evidence summary{color:var(--accent);font-weight:600}.evidence[open] summary{margin-bottom:14px}.evidence-body{display:grid;gap:18px}
.shot{margin:0;display:grid;gap:8px}.shot .sw{display:flex;gap:6px}.shot .sw button{font:12px var(--sans);padding:6px 10px;border-radius:6px;border:1px solid var(--line);background:var(--surface);color:var(--muted);cursor:pointer}.shot .sw button[aria-pressed=true]{background:var(--accent-soft);color:var(--accent);border-color:var(--accent)}
.shot a{display:block;width:fit-content;max-width:100%;justify-self:start;border:1px solid var(--line);border-radius:8px;overflow:hidden;background:var(--sunk);cursor:zoom-in}.shot img{display:block;width:auto;max-width:100%;height:auto;max-height:300px;object-fit:contain;object-position:top left}.shot figcaption,.noshot{font-size:11px;color:var(--muted);line-height:1.65}.noshot{display:block;padding:10px 12px;border-radius:6px;background:var(--sunk);margin:0}
.lb{position:fixed;inset:0;z-index:50;background:rgba(10,14,13,.9);display:grid;place-items:center;overflow:auto;padding:20px;cursor:default}.lb[hidden]{display:none}
.lb-panel{display:grid;gap:12px;width:min(1600px,100%);min-width:0}.lb-toolbar{display:flex;align-items:center;gap:12px;flex-wrap:wrap;position:sticky;top:0;z-index:1;padding:10px 12px;border:1px solid #ffffff30;border-radius:9px;background:#202b28;color:#fff}
.lb-switch,.lb-navigation{display:flex;gap:6px;align-items:center}.lb-navigation{margin-left:auto}.lb button{font:600 12px var(--sans);padding:8px 12px;border:1px solid #ffffff50;border-radius:6px;background:transparent;color:#fff;cursor:pointer}.lb button[aria-pressed=true]{background:#e0efeb;border-color:#e0efeb;color:#0e6b61}.lb button:disabled{opacity:.35;cursor:default}.lb-count{font:12px var(--mono);padding:0 5px;white-space:nowrap}
.lb img{display:block;max-width:100%;max-height:calc(100dvh - 175px);width:auto;height:auto;justify-self:center;border-radius:6px;background:#fff;object-fit:contain}.lb p{color:#fff;font-size:12px;line-height:1.7;margin:0;text-align:center}.lb .lb-help{color:#b9c5bf;font-size:11px}
.lb .lb-none{color:#b9c5bf;font-size:13px;padding:60px 20px;text-align:center;border:1px dashed #ffffff40;border-radius:8px}
details.more dl{display:grid;grid-template-columns:110px minmax(0,1fr);gap:8px 12px;margin:14px 0 0;font-size:12px;line-height:1.7}details.more dt{color:var(--muted)}details.more dd{margin:0}details.more code{font:11px var(--mono);word-break:break-all;white-space:normal}
.empty{padding:28px 20px;color:var(--muted);text-align:center;margin:0;font-size:13px}
.qempty{background:var(--surface);border:1px solid var(--line);border-radius:12px;padding:22px 24px;display:grid;gap:10px;font-size:13px;line-height:1.7}.qempty pre{margin:0;font:12px/1.55 var(--mono);background:var(--sunk);border-radius:8px;padding:12px 14px;overflow:auto;white-space:pre}
@media (max-width:1100px){main{padding:20px}.qmain{grid-template-columns:270px minmax(0,1fr);gap:14px}.tile{padding:12px;gap:2px 8px}.tile b{font-size:22px}.qdet{padding:20px}.facts{grid-template-columns:1fr;gap:10px}.qdet h2{font-size:20px}}
@media (max-width:820px){main{padding:18px 14px 32px}.qhead{align-items:start;flex-wrap:wrap}.qhead h1{font-size:22px}.qact button,.qact a{font-size:11px;padding:8px}.qsum{grid-template-columns:repeat(2,minmax(0,1fr))}.tile{border-bottom:1px solid var(--line)}.tile:nth-child(2){border-right:0}.qmain{grid-template-columns:1fr}.qlist{contain:none;max-height:230px}.qdet{padding:18px}.seg{width:100%}.seg button{flex:1}.qfil select{flex:1;max-width:none}.qfil input{flex-basis:100%}.decide .who{grid-template-columns:1fr}}
@media (max-width:480px){.choice{grid-template-columns:1fr}.save .next{margin-left:0}.qdet h2{font-size:19px}}
"""

JS = r"""
const D = JSON.parse(root.querySelector('#qdata').textContent);
if (D.imgs) {  // 고객용 HTML: 한 번만 실은 그림을 열쇠 자리에 되돌린다 (_dedupe)
  const img = v => D.imgs[v] || v;
  for (const x of D.items) { for (const f of x.captures || []) { f.asis = img(f.asis); f.tobe = img(f.tobe); } x.shots.asis = img(x.shots.asis); x.shots.tobe = img(x.shots.tobe); }
}
const L = D.labels, items = D.items, standalone = !!D.standalone, APP = encodeURIComponent(D.app);
const esc = s => String(s ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const dec = x => (x.decision && x.decision.decision) || '';
const RANK = {high: 0, mid: 1, low: 2};
let f = {d: 'all', sev: '', where: '', q: ''}, cur = null, timer = null;
let lbItem = null, lbStep = 0, lbSide = 'asis', lbReturn = null;
if (ctx.onDestroy) ctx.onDestroy(() => clearTimeout(timer));
const remembered = () => { try { return sessionStorage.getItem('quirks-cur-' + D.app); } catch (_) { return null; } };
const remember = id => { try { sessionStorage.setItem('quirks-cur-' + D.app, id); } catch (_) {} };

function summary(){
  const qs = items.filter(x => !x.env), c = k => qs.filter(x => dec(x) === k).length;
  const met = qs.filter(x => x.tobe === 'same' || x.tobe === 'changed').length, high = qs.filter(x => x.severity === 'high').length;
  root.querySelector('.qsum').innerHTML = `
    <div class="tile"><b>${qs.length}</b><span class="k">검토할 동작</span><small>환경 차이 ${items.length - qs.length}건 별도</small></div>
    <div class="tile priority"><b>${high}</b><span class="k">업무 영향 큼</span><small>목록에서 먼저 표시</small></div>
    <div class="tile progress"><b>${qs.length - c('')}<span>/${qs.length}</span></b><span class="k">고객 결정 기록</span><small>보류 ${c('hold')}건 포함 · 결정 전 ${c('')}건</small></div>
    <div class="tile"><b>${met}<span>/${qs.length}</span></b><span class="k">to-be 기준 충족</span><small>as-is 그대로 · 고객 결정대로</small></div>`;
  for (const b of root.querySelectorAll('[data-d]')) b.innerHTML = `${b.dataset.d === 'all' ? '전체' : L.decision[b.dataset.d]}<small>${b.dataset.d === 'all' ? qs.length : c(b.dataset.d)}</small>`;
}
function visible(x){
  if (f.d !== 'all' && (x.env || dec(x) !== f.d)) return false;
  if (f.sev && x.severity !== f.sev) return false;
  if (f.where && !x.where.includes(f.where)) return false;
  if (f.q && !(x.id + ' ' + x.where.join(' ') + ' ' + x.tests.map(t => t.name).join(' ') + ' ' + x.title + ' ' + x.summary).toLowerCase().includes(f.q)) return false;
  return true;
}
function list(){
  const xs = items.filter(visible).sort((a, b) => (a.env - b.env) || (RANK[a.severity] - RANK[b.severity]) || a.id.localeCompare(b.id));
  if (!xs.some(x => x.id === cur)) cur = xs[0]?.id || null;
  const row = x => `<button type="button" class="qrow" data-id="${esc(x.id)}" aria-current="${x.id === cur}">
    <span class="t">${esc(x.title)}</span><span class="m">${esc(x.id)}${x.where.length ? ' · ' + esc(x.where.join(', ')) : ''}</span>
    <span class="d"><i class="dot ${esc(x.severity)}" title="${esc(L.sev[x.severity])}"></i><span class="pill d-${dec(x)}">${x.env ? '환경' : L.decision[dec(x)]}</span></span></button>`;
  const q = xs.filter(x => !x.env), e = xs.filter(x => x.env), box = root.querySelector('.qlist'), pos = box.scrollTop;
  box.innerHTML = ((q.length ? `<h5>검토 목록 · ${q.length}건<span>영향 큰 순</span></h5>` + q.map(row).join('') : '')
    + (e.length ? `<h5>로컬 환경 차이 · ${e.length}건</h5>` + e.map(row).join('') : '')) || '<p class="empty">조건에 맞는 기록이 없습니다.<br>검색어나 거르기를 바꿔 보세요.</p>';
  box.scrollTop = pos;
  for (const b of box.querySelectorAll('.qrow')) b.onclick = () => {
    cur = b.dataset.id; remember(cur); list(); detail();
    if (matchMedia('(max-width:820px)').matches) root.querySelector('.qdet').scrollIntoView({block: 'start', behavior: 'smooth'});
  };
}
function refresh(force = false){ const before = cur; list(); if (force || before !== cur) detail(); }

function shot(x){
  const s = x.shots || {};
  if (!s.asis) return `<p class="noshot">캡처 없음 — ${x.verified === '소스로 판단' ? '소스로 판단한 기록이라 실행 화면이 없습니다' : '기록의 shot(골든 테스트·단계)이 없거나 골든에 그 캡처가 없습니다'}.</p>`;
  const tb = s.tobe ? `<button type="button" data-s="tobe" aria-pressed="false">to-be ${s.tobe_fail ? '(실패 순간)' : '(같은 단계)'}</button>` : '';
  return `<figure class="shot"><div class="sw"><button type="button" data-s="asis" aria-pressed="true">as-is (이상한 동작)</button>${tb}</div>
    <a data-side="asis" href="${esc(s.asis)}" target="_blank" rel="noopener"><img src="${esc(s.asis)}" alt="${esc(x.id)} as-is 캡처" loading="lazy"></a>
    <figcaption>${esc(x.caption || 'as-is 화면')}${x.shot_step != null ? ` · 단계 ${x.shot_step + 1}` : ''}${s.tobe ? '' : ' · to-be 캡처는 비교 실행이 있으면 보입니다'} · 누르면 크게 보기 (Esc 닫기 · ← → 이전·다음 단계)</figcaption></figure>`;
}
function history(d){
  const h = (d.history || []).filter(o => o.decision || o.direction || o.memo).slice().reverse();
  return h.length ? `<details class="hist"><summary>이전 결정 ${h.length}개</summary><ol>${h.map(o => `<li>${esc(o.updated_at || '-')} · ${esc(L.decision[o.decision || ''])}${o.direction ? ' — ' + esc(o.direction) : ''} · ${esc(o.confirmed_by || '-')} ${esc(o.confirmed_at || '')}${o.memo ? ' · ' + esc(o.memo) : ''}</li>`).join('')}</ol></details>` : '';
}
function detail(){
  const x = items.find(i => i.id === cur), box = root.querySelector('.qdet');
  if (!x){ box.innerHTML = '<p class="empty">표시할 기록이 없습니다.<br>검색어나 거르기를 바꿔 보세요.</p>'; return; }
  const d = x.decision || {}, m = x.more || {}, ro = standalone ? 'readonly' : '', dis = standalone ? 'disabled' : '';
  const opts = (x.options || []).map(o => `<button type="button" data-o="${esc(o)}">${esc(o)}</button>`).join('');
  box.innerHTML = `
   <div class="qtitle"><div class="qtop"><code>${esc(x.id)}</code><span>${esc(x.where.join(' · '))}</span>
     <span class="pill s-${esc(x.severity)}">${esc(L.sev[x.severity] || '')}</span>${x.verified ? `<span>${esc(x.verified)}</span>` : ''}
     ${x.env ? '<span class="pill d-">로컬 환경 차이</span>' : `<span class="pill t-${esc(x.tobe)}" title="실행 원장의 마지막 비교에서 계산">${esc(L.tobe[x.tobe])}</span>`}</div>
   <h2>${esc(x.title)}</h2></div>
   <details class="evidence" open><summary>캡처와 재현 방법 · ${x.shots && x.shots.asis ? '캡처 있음' : '캡처 없음'}</summary><div class="evidence-body">
     <div><h4>보통 기대하는 동작 · 고객 결정 전 제안</h4><p>${esc(x.expected || '-')}</p></div>
     <div><h4>as-is에서 재현하는 방법</h4><ol>${(x.steps || []).map(s => `<li>${esc(s)}</li>`).join('')}</ol>${x.see ? `<p class="see">→ ${esc(x.see)}</p>` : ''}</div>
     ${shot(x)}</div></details>
   <div class="facts"><div><h4>현재 이렇게 동작합니다</h4><p>${esc(x.summary)}</p></div><div class="impact"><h4>업무에 미치는 영향</h4><p>${esc(x.impact || '-')}</p></div></div>
   ${x.env ? '' : `<form class="decide" novalidate>
     <div class="decide-head"><h3>이 동작을 어떻게 할까요?</h3><span class="pill d-${dec(x)}" data-status>${L.decision[dec(x)]}</span></div>
     <fieldset class="choice"><legend>고객 결정</legend>
       ${[['keep','현행 유지','to-be 도 지금 as-is 동작 그대로'],['change','수정','to-be 를 바꾼다 · 방향을 적는다'],['hold','보류','추가 확인 후 결정']].map(([k, t, s]) =>
         `<label><input type="radio" name="decision" value="${k}" ${d.decision === k ? 'checked' : ''} ${dis}><b>${t}</b><span>${s}</span></label>`).join('')}
     </fieldset>
     <div class="direction" ${d.decision === 'change' || d.direction ? '' : 'hidden'}><div class="field"><label for="q-direction">수정 방향</label><textarea id="q-direction" name="direction" placeholder="예: 끝 날짜만 넣어도 그날까지의 자료를 보여 준다" ${ro}>${esc(d.direction)}</textarea></div>
       ${standalone ? '' : `<div class="sugg" aria-label="선택지에서 고르기">${opts}</div>`}</div>
     <div class="decision-note field"><label for="q-memo">결정 사항</label><textarea id="q-memo" name="memo" aria-describedby="q-memo-help" placeholder="결정 이유, 변경 범위, 예외 조건 등" ${ro}>${esc(d.memo)}</textarea><p id="q-memo-help">현행 유지·수정·보류를 정한 이유와 구현 때 참고할 내용을 적습니다. 고칠 때마다 이전 값은 기록으로 남습니다.</p></div>
     ${standalone ? (d.updated_at ? `<p class="save"><span>저장 ${esc(d.updated_at)}</span></p>` : '') : `<div class="save"><button type="submit">결정 저장</button><span role="status">${d.updated_at ? '저장 ' + esc(d.updated_at) : '선택 후 저장해 주세요'}</span><button type="button" class="next" data-next>다음 결정 전 항목 →</button></div>`}
     ${history(d)}
   </form>`}
   <details class="more"><summary>원인 · 근거 · 원문</summary><dl>
     <dt>원인</dt><dd>${esc(m.cause || '-')}</dd>
     <dt>근거</dt><dd>${(m.evidence || []).map(e => `<code>${esc(e)}</code>`).join('<br>') || '-'}</dd>
     ${x.open && x.open.length ? `<dt>아직 다른 곳</dt><dd>${x.open.map(esc).join('<br>')}</dd>` : ''}
     <dt>이어진 테스트</dt><dd>${x.tests.map(t => `${esc(t.title)} <code>${esc(t.name)}</code>`).join('<br>') || '-'}</dd>
     ${m.routes && m.routes.length ? `<dt>화면</dt><dd>${m.routes.map(esc).join(', ')}</dd>` : ''}${m.match ? `<dt>규칙 범위 (match)</dt><dd><code>${esc(m.match)}</code></dd>` : ''}
     <dt>무엇이 이상한가 (원문)</dt><dd>${esc(m.strange || '-')}</dd><dt>재현하면 (원문)</dt><dd>${esc(m.observed || '-')}</dd>
     <dt>재현 (원문)</dt><dd>${(m.repro || []).map(esc).join('<br>') || '-'}</dd><dt>선택지 (원문)</dt><dd>${(m.options || []).map(esc).join('<br>') || '-'}</dd>
     ${m.found ? `<dt>찾은 날</dt><dd>${esc(m.found)}</dd>` : ''}</dl></details>`;
  const link = box.querySelector('.shot a');
  if (link) link.onclick = ev => { ev.preventDefault(); openLb(x, link.dataset.side || 'asis'); };
  for (const b of box.querySelectorAll('.shot .sw button')) b.onclick = () => {
    const s = x.shots[b.dataset.s], img = box.querySelector('.shot img'), a = box.querySelector('.shot a');
    img.src = s; a.href = s; a.dataset.side = b.dataset.s; img.alt = `${x.id} ${b.dataset.s === 'asis' ? 'as-is' : 'to-be'} 캡처`;
    for (const o of box.querySelectorAll('.shot .sw button')) o.setAttribute('aria-pressed', String(o === b));
  };
  const form = box.querySelector('form.decide');
  if (!form || standalone) return;
  const sync = () => { form.querySelector('.direction').hidden = form.querySelector('input[name=decision]:checked')?.value !== 'change' && !form.direction.value; };
  for (const r of form.querySelectorAll('input[name=decision]')) r.onchange = sync;
  const next = form.querySelector('[data-next]');
  const pending = () => items.filter(i => !i.env && !dec(i) && visible(i)).sort((a, b) => RANK[a.severity] - RANK[b.severity] || a.id.localeCompare(b.id));
  next.disabled = !pending().some(i => i.id !== cur);
  next.onclick = () => {
    const p = pending(), idx = p.findIndex(i => i.id === cur), target = p.slice(idx + 1).find(i => i.id !== cur) || p.find(i => i.id !== cur);
    if (target){ cur = target.id; remember(cur); refresh(true); root.querySelector('.qrow[aria-current=true]')?.scrollIntoView({block: 'nearest'}); }
  };
  for (const b of form.querySelectorAll('.sugg button')) b.onclick = () => {
    form.direction.value = b.dataset.o.replace(/^(현행 유지|수정)\s*:\s*/, '');
    form.querySelector(/^현행 유지/.test(b.dataset.o) ? 'input[value=keep]' : 'input[value=change]').checked = true; sync();
  };
  form.onsubmit = async e => {
    e.preventDefault();
    const btn = form.querySelector('.save button'), msg = form.querySelector('.save span');
    const body = {decision: (form.querySelector('input[name=decision]:checked') || {}).value || '', direction: form.direction.value,
                  confirmed_by: d.confirmed_by || '', confirmed_at: d.confirmed_at || '', memo: form.memo.value};  // 확인자·확인일은 입력받지 않고 예전 값을 지키기만 한다
    btn.disabled = true; msg.className = ''; msg.textContent = '저장 중…';
    try {
      const r = await fetch(`/api/app/${APP}/quirks/${encodeURIComponent(x.id)}`, {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(body)});
      const j = await r.json().catch(() => ({error: r.statusText})); if (!r.ok) throw new Error(j.error || r.status);
      x.decision = j; summary(); list();
      if (cur !== x.id){ detail(); return; }
      const badge = form.querySelector('[data-status]'); badge.className = 'pill d-' + dec(x); badge.textContent = L.decision[dec(x)];
      next.disabled = !pending().some(i => i.id !== cur);
      msg.textContent = '저장 ' + j.updated_at + ' · 판정 규칙은 다음 to-be 비교부터 (위의 판정에 반영)';
      const hist = form.querySelector('details.hist'), nh = history(j); if (hist) hist.outerHTML = nh || ''; else if (nh) form.insertAdjacentHTML('beforeend', nh);
      D.stale += 1; applyPanel();
    } catch (err) { msg.className = 'err'; msg.textContent = '저장하지 못했습니다: ' + err.message; }
    finally { btn.disabled = false; }
  };
}

// ---- 판정에 반영: 결정에서 만든 규칙을 보이고, 통합 화면의 to-be 비교(POST /compare, 실행 탭의 '바로 해결'과 같은 작업)를 다시 돌린다 ----
const panel = root.querySelector('.qapply');
function applyPanel(){
  if (!panel) return;
  const rs = D.rules;
  panel.querySelector('[data-rules]').innerHTML = rs.length
    ? '<ul>' + rs.map(r => `<li><b>${esc(r.quirk)}</b> · ${esc(L.rule.customer)} · ${r.tests.length ? '테스트 ' + r.tests.map(t => `<code>${esc(t)}</code>`).join(' ') : ''}${r.what ? ` · 무엇이 <code>/${esc(r.what)}/</code>` : ''}<br><span style="color:var(--muted)">${esc(r.reason)}</span></li>`).join('') + '</ul>'
    : '<p>아직 규칙이 없습니다. \'수정\'으로 정한 기록만 규칙이 됩니다 (현행 유지·보류는 to-be 가 as-is 를 따르므로 규칙이 없다).</p>';
  panel.querySelector('[data-stale]').textContent = D.stale ? `마지막 비교(${D.last_run || '없음'}) 뒤에 바뀐 결정 ${D.stale}개 — 다시 비교해야 반영됩니다.` : (D.last_run ? `마지막 비교 ${D.last_run}` : '아직 to-be 비교 실행이 없습니다.');
}
const runBtn = panel && panel.querySelector('[data-run]'), jobLine = panel && panel.querySelector('.job');
function showJob(j){
  const step = (j.steps || []).find(s => s.state === 'run') || (j.steps || []).slice(-1)[0], last = (j.log || []).slice(-1)[0] || '';
  jobLine.hidden = false; jobLine.className = 'job' + (j.running ? '' : j.ok ? ' ok' : ' fail');
  jobLine.textContent = j.running ? `to-be 비교 실행 중 · ${step ? step.label : ''}\n${last}` : j.ok ? '끝. 새 실행으로 to-be 상태를 다시 계산합니다.' : `실패: ${j.error || last}`;
}
async function poll(first){
  let j; try { j = await (await fetch(`/api/app/${APP}/job`)).json(); } catch (_) { return; }
  if (j.kind !== 'compare'){ if (j.running){ runBtn.disabled = true; runBtn.title = `다른 작업(${j.kind})이 실행 중입니다`; timer = setTimeout(() => poll(true), 2000); } else { runBtn.disabled = false; runBtn.title = ''; } return; }
  if (j.running){ runBtn.disabled = true; showJob(j); timer = setTimeout(() => poll(false), 2000); return; }
  runBtn.disabled = false;
  if (first) return;  // 예전에 끝난 비교는 다시 보이지 않는다
  showJob(j);
  if (j.ok && ctx.refresh) timer = setTimeout(() => ctx.refresh(), 900);
}
if (panel && !standalone){
  root.querySelector('[data-apply]').onclick = () => { panel.hidden = !panel.hidden; if (!panel.hidden) panel.scrollIntoView({block: 'nearest'}); };
  runBtn.onclick = async () => {
    if (!confirm(`to-be 비교를 다시 돌립니다 (pytest --compare, 승인된 골든 기준).\n결정에서 만든 판정 규칙 ${D.rules.length}개가 이번 비교부터 쓰입니다. 시나리오 수에 따라 몇 분 걸릴 수 있습니다.\n\n계속할까요?`)) return;
    runBtn.disabled = true;
    try {
      const r = await fetch(`/api/app/${APP}/compare`, {method: 'POST', headers: {'Content-Type': 'application/json'}, body: '{}'});
      const j = await r.json().catch(() => ({error: r.statusText})); if (!r.ok || j.error) throw new Error(j.error || r.statusText);
      showJob(j); timer = setTimeout(() => poll(false), 2000);
    } catch (e) { jobLine.hidden = false; jobLine.className = 'job fail'; jobLine.textContent = '시작하지 못했습니다: ' + e.message; runBtn.disabled = false; }
  };
  applyPanel(); poll(true);
}
const md = root.querySelector('[data-md]');
if (md) md.onclick = () => {  // 고객용 HTML 에서는 품고 온 작업 목록을 내려받는다
  const a = document.createElement('a'); a.href = URL.createObjectURL(new Blob([D.md], {type: 'text/markdown'})); a.download = `asis-quirks-${D.app}.md`; a.click(); setTimeout(() => URL.revokeObjectURL(a.href), 1000);
};

// ---- 크게 보기: 그 테스트의 단계별 캡처, as-is / to-be 전환 ----
const lb = root.querySelector('.lb');
const frames = () => lbItem?.captures?.length ? lbItem.captures : [{step: lbItem?.shot_step ?? 0, asis: lbItem?.shots?.asis, tobe: lbItem?.shots?.tobe, action: ''}];
function renderLb(){
  const fs = frames(), fr = fs[lbStep]; if (!fr) return;
  const img = lb.querySelector('img'), none = lb.querySelector('.lb-none'), src = fr[lbSide];
  img.hidden = !src; none.hidden = !!src; if (src) img.src = src;
  none.textContent = lbSide === 'tobe' ? '이 단계의 to-be 캡처가 없습니다 (비교 실행이 그 단계까지 가지 못했거나 캡처를 남기지 않았습니다)' : '이 단계의 as-is 캡처가 없습니다';
  img.alt = `${lbItem.id} ${lbSide === 'asis' ? 'as-is' : 'to-be'} · 단계 ${fr.step + 1}`;
  lb.querySelector('.lb-caption').textContent = `${lbItem.id} · 단계 ${fr.step + 1}${fr.action ? ' · ' + fr.action : ''}${fr.step === lbItem.shot_step ? ' · ' + (lbItem.caption || lbItem.title) : ''}`;
  lb.querySelector('.lb-count').textContent = `${lbStep + 1} / ${fs.length}`;
  for (const b of lb.querySelectorAll('[data-lb-side]')) b.setAttribute('aria-pressed', String(b.dataset.lbSide === lbSide));
  lb.querySelector('[data-lb-prev]').disabled = lbStep === 0;
  lb.querySelector('[data-lb-next]').disabled = lbStep === fs.length - 1;
}
function openLb(item, side){
  lbItem = item; lbSide = side; lbReturn = document.activeElement;
  lbStep = Math.max(0, frames().findIndex(fr => fr.step === item.shot_step)); renderLb(); lb.hidden = false;
  lb.querySelector('[data-lb-close]').focus();
}
function closeLb(){ lb.hidden = true; if (lbReturn?.isConnected) lbReturn.focus({preventScroll: true}); }
function moveLb(delta){ const n = lbStep + delta; if (n >= 0 && n < frames().length){ lbStep = n; renderLb(); } }
for (const b of lb.querySelectorAll('[data-lb-side]')) b.onclick = () => { lbSide = b.dataset.lbSide; renderLb(); };
lb.querySelector('[data-lb-prev]').onclick = () => moveLb(-1);
lb.querySelector('[data-lb-next]').onclick = () => moveLb(1);
lb.querySelector('[data-lb-close]').onclick = closeLb;
lb.onclick = ev => { if (ev.target === lb) closeLb(); };
ctx.listen(document, 'keydown', ev => {
  if (lb.hidden) return;
  if (ev.key === 'Escape'){ ev.preventDefault(); closeLb(); return; }
  if (ev.key === 'ArrowLeft' || ev.key === 'ArrowRight'){ ev.preventDefault(); moveLb(ev.key === 'ArrowLeft' ? -1 : 1); }
  if (ev.key === 'Tab'){
    const fo = [...lb.querySelectorAll('button:not(:disabled)')], first = fo[0], last = fo[fo.length - 1];
    if (ev.shiftKey && document.activeElement === first){ ev.preventDefault(); last.focus(); }
    else if (!ev.shiftKey && document.activeElement === last){ ev.preventDefault(); first.focus(); }
  }
});

// ---- 거르기 ----
const wsel = root.querySelector('[data-f=where]'), places = [...new Set(items.flatMap(x => x.where))].sort();
wsel.innerHTML = '<option value="">모든 화면·테스트</option>' + places.map(s => `<option>${esc(s)}</option>`).join('');
wsel.hidden = !places.length;
for (const b of root.querySelectorAll('[data-d]')) b.onclick = () => { f.d = b.dataset.d; for (const o of root.querySelectorAll('[data-d]')) o.setAttribute('aria-pressed', String(o === b)); refresh(); };
root.querySelector('[data-f=sev]').onchange = e => { f.sev = e.target.value; refresh(); };
wsel.onchange = e => { f.where = e.target.value; refresh(); };
root.querySelector('[data-f=q]').oninput = e => { f.q = e.target.value.trim().toLowerCase(); refresh(); };
root.querySelector('[data-reset]').onclick = () => {
  f = {d: 'all', sev: '', where: '', q: ''};
  for (const b of root.querySelectorAll('[data-d]')) b.setAttribute('aria-pressed', String(b.dataset.d === 'all'));
  for (const i of root.querySelectorAll('[data-f]')) i.value = '';
  refresh();
};
const first = items.filter(x => !x.env).sort((a, b) => RANK[a.severity] - RANK[b.severity] || a.id.localeCompare(b.id))[0] || items[0];
cur = remembered() && items.some(i => i.id === remembered()) ? remembered() : first && first.id;
summary(); list(); detail();
"""

EXAMPLE = """[{"id": "Q-01", "kind": "quirk", "title": "회차를 비우고 조회하면 글자 없는 '실패' 창만 뜬다", "severity": "high",
  "strange": "…", "expected": "…", "impact": "…", "repro": ["…"], "observed": "…", "cause": "…", "evidence": ["파일:줄 — …"],
  "verified": "run", "tests": ["test_regulation_query"], "routes": ["/regulations"], "match": "알림창",
  "options": ["현행 유지: …", "수정: …"], "shot": {"test": "test_regulation_query", "step": 1, "caption": "가운데 빈 '실패' 창"}}]"""


def _e(s: Any) -> str:
    return html._e(s)


def body(app: str, d: dict[str, Any]) -> str:
    link = not d["standalone"]
    notes = "".join(f"<p class='qnote'><b>기록 확인</b>{_e(p)}</p>" for p in d["problems"][:8])
    if not d["items"]:
        return (f"<div class='qwrap'><header class='qhead'><div><div class='eyebrow'>as-is 이상 동작 · {_e(app)}</div><h1>아직 기록이 없습니다</h1>"
                f"<p>to-be 는 as-is 와 똑같이 동작해야 합니다 — as-is 동작이 이상해도 따라 합니다. 이상하다고 본 동작은 면제하지 않고 "
                f"<code>{_e(path(app))}</code> 에 적어 두면 여기에서 고객과 현행 유지·수정·보류를 정합니다.</p></div></header>{notes}"
                f"<div class='qempty'><b>기록 파일 형식</b> (프로젝트 루트의 quirks/, runs/ 옆 · 골든이 아니다)<pre>{_e(EXAMPLE)}</pre>"
                f"<span>tests 는 골든 테스트 id, shot.step 은 골든 단계 번호(0부터). 짧은 글은 <code>{_e(path(app, 'brief'))}</code>(선택), "
                f"결정은 이 탭이 <code>{_e(path(app, 'decisions'))}</code> 에 쓴다 (사람만).</span></div></div>")
    seg = "".join(f"<button type='button' data-d='{k}' aria-pressed='{str(k == 'all').lower()}'>{t}</button>"
                  for k, t in (("all", "전체"), ("", "결정 전"), ("keep", "현행 유지"), ("change", "수정"), ("hold", "보류")))
    acts = (f"<button type='button' class='primary' data-apply title='결정에서 만든 판정 규칙을 보고 to-be 비교를 다시 돌립니다'>판정에 반영</button>"
            f"<a href='/api/app/{_e(app)}/quirks.md' download title='고객 결정 → 소스 수정 작업 목록 (markdown)'>결정 내보내기 (.md)</a>"
            f"<a href='/api/app/{_e(app)}/quirks.html' download title='캡처를 품은 혼자 열리는 문서 (결정은 읽기만) — 고객 협의 자료'>고객용 HTML</a>") if link else \
        "<button type='button' data-md>결정 내보내기 (.md)</button>"
    apply = ("<section class='qapply' hidden aria-label='판정에 반영'><h3>판정에 반영 — 고객 결정(수정)에서 만든 규칙</h3>"
             "<p>'수정'으로 정한 기록마다 규칙 하나: 그 기록에 이어진 테스트(+ match)의 차이를 결함 대신 '고객 결정(수정)'으로 셉니다. "
             "규칙은 다음 to-be 비교부터 쓰입니다. 현행 유지·보류는 규칙이 없어 to-be 가 as-is 와 다르면 그대로 결함입니다.</p>"
             "<div data-rules></div><div class='row'><button type='button' data-run>to-be 비교 다시 실행</button><span class='stale' data-stale></span></div>"
             "<div class='job' role='status' hidden></div></section>") if link else ""
    return f"""<div class='qwrap'>
<header class='qhead'><div><div class='eyebrow'>as-is 이상 동작 · {_e(app)}</div><h1>as-is 동작 검토</h1>
<p>고객 결정 전까지 to-be 는 as-is 와 똑같이 동작합니다. 이상해 보이는 as-is 동작을 보고 현행 유지·수정·보류를 기록하세요.{'' if link else ' (읽기 전용 · 결정은 east2west ui 에서 저장합니다)'}</p></div>
<div class='qact'>{acts}</div></header>{notes}{apply}
<div class='qsum' aria-label='검토 현황'></div>
<div class='qfil' role='toolbar' aria-label='거르기'><div class='seg' aria-label='고객 결정'>{seg}</div>
<select data-f='sev' aria-label='업무 영향'><option value=''>모든 영향</option>{''.join(f"<option value='{k}'>{v}</option>" for k, v in SEV.items())}</select>
<select data-f='where' aria-label='화면·테스트'></select><input data-f='q' type='search' placeholder='제목·내용·기록 번호·테스트 검색' aria-label='찾기'><button type='button' data-reset>초기화</button></div>
<div class='qmain'><nav class='qlist' aria-label='기록 목록'></nav><article class='qdet' aria-live='polite'></article></div>
<div class='lb' hidden role='dialog' aria-modal='true' aria-label='캡처 크게 보기'><div class='lb-panel'>
<div class='lb-toolbar'><div class='lb-switch' aria-label='캡처 비교'><button type='button' data-lb-side='asis' aria-pressed='true'>as-is</button><button type='button' data-lb-side='tobe' aria-pressed='false'>to-be</button></div>
<div class='lb-navigation'><button type='button' data-lb-prev>← 이전 단계</button><span class='lb-count' role='status'></span><button type='button' data-lb-next>다음 단계 →</button></div><button type='button' data-lb-close aria-label='크게 보기 닫기'>닫기 ×</button></div>
<img alt=''><div class='lb-none' hidden></div><p class='lb-caption' aria-live='polite'></p><p class='lb-help'>그 테스트의 단계별 캡처 · ← → 로 이동 · Esc 로 닫기</p></div></div>
</div>"""


def fragment(app: str, golden: Path | None = None, tests_dir: Path | None = None) -> dict[str, Any]:
    """통합 화면 탭 조각 (html.fragment 형식). 캡처는 /file 주소로 (골든·runs/ 아래)."""
    d = data(app, golden, tests_dir)
    return html.fragment("quirks", f"{app} as-is 이상 동작", body(app, d), css=CSS, js=JS if d["items"] else "", data=("qdata", d))


def _dedupe(d: dict[str, Any]) -> None:
    """품은 그림(data: 주소)을 d['imgs'] 에 한 번만 두고 자리에는 열쇠('#img3')를 둔다. 여러 기록이 같은 테스트의 캡처를 쓰면 파일이 그만큼 준다.
    화면(JS)이 읽을 때 열쇠를 그림으로 되돌린다 (같은 문자열을 가리키므로 메모리도 늘지 않는다)."""
    imgs: dict[str, str] = {}
    keys: dict[str, str] = {}

    def key(v: str) -> str:
        if not v.startswith("data:"):
            return v
        if v not in keys:
            keys[v] = f"#img{len(keys)}"
            imgs[keys[v]] = v
        return keys[v]
    for x in d["items"]:
        for f in x.get("captures") or []:
            f["asis"], f["tobe"] = key(f.get("asis", "")), key(f.get("tobe", ""))
        x["shots"]["asis"], x["shots"]["tobe"] = key(x["shots"].get("asis", "")), key(x["shots"].get("tobe", ""))
    d["imgs"] = imgs


def standalone(app: str, golden: Path | None = None, tests_dir: Path | None = None) -> str:
    """고객 협의 자료로 보낼 혼자 열리는 문서: 기록마다 그 테스트의 모든 단계 캡처(as-is·to-be)를 품고(같은 그림은 한 번), 결정 칸은 읽기만 한다
    (저장은 east2west ui 에서)."""
    d = data(app, golden, tests_dir, embed=True)
    _dedupe(d)
    return html.assemble(html.fragment("quirks", f"{app} as-is 이상 동작", body(app, d), css=CSS, js=JS if d["items"] else "", data=("qdata", d)))
