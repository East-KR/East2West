r"""차이 규칙: to-be 비교의 다른 점을 행(무엇이 · as-is · to-be)으로 보고, 결함이 아닌 갈래로 나눌 수 있는 것만 나눈다.

원칙: to-be 는 as-is 와 똑같이 동작해야 한다 — 다른 점은 기본이 결함이다. 결함에서 빼는 것은 비교 자체가 성립하지 않는 경우
(자료·환경, 대조할 자산 없음, 도구 한계, 비교 불가)와 고객이 '수정'으로 정한 경우뿐이고, 사유(reason)를 반드시 적는다.
as-is 동작이 이상한 것은 여기서 빼지 않는다: to-be 는 따라 하고, 이상한 점은 'as-is 이상 동작'(quirks.py)에 적어 고객과 정한다.

갈래 (class)
  env       자료·환경 차이        양쪽 DB 자료·연결 설정이 달라서 생긴 차이 (코드 결함이 아니다)
  blocked   보류                 대조할 자산(공통코드·양식·인증 모듈)이 없어 비교할 수 없다
  tool      도구 한계             관찰기가 그 화면 구성을 읽지 못한다. 소스로 같음을 확인한 것만
  invalid   비교 불가             as-is 관찰이 실패했다
  customer  고객 결정(수정)        as-is 이상 동작을 고객이 '수정'으로 정해 일부러 바꾼 것. 보통은 'as-is 이상 동작' 탭의 결정에서 만든다
                                 (quirks.decision_rules: 그 기록에 이어진 테스트 + match 로만 좁힌다)
  same      같은 뜻 (차이 아님)    문구는 달라도 뜻이 같다. asis·tobe 정규식이 둘 다 맞고 same_groups 의 이름 붙은 묶음이 서로 같을 때만

golden/<app>/oracle.json 의 difference_rules (오라클 파일이라 승인 대상 — 바꾸면 다음 비교가 시작할 때 자동 승인한다):
  [{class, reason(필수), tests?: [테스트 id 또는 정규식], routes?: [주소 경로 정규식], what?/asis?/tobe?: 정규식, cascade?: bool, same_groups?: [묶음 이름]}]
  tests 는 id 가 같거나 정규식이 id 전체와 맞으면, routes 는 그 단계의 as-is 또는 to-be 주소 경로 전체와 맞으면, what·asis·tobe 는 그 칸 안에서 찾으면 맞는다.
  범위(tests·routes·what·asis·tobe)가 하나도 없는 규칙은 모든 다른 점을 덮으므로 받지 않는다. 앞에 있는 규칙이 이기고, 'same' 규칙을 먼저 본다.
  모르는 갈래·빈 사유·틀린 정규식이 있으면 비교를 거부한다 (oracle.precheck → pytest --compare, east2west oracle-status 가 이유를 보인다).

  예) DB 길이 초과: as-is 는 오라클 원문, to-be 는 우리말 안내 — 같은 열이면 같은 오류다
    {"class": "same", "reason": "DB 길이 초과 오류: 문구는 달라도 같은 열", "what": "알림창",
     "asis": "ORA-12899: .*\\.\"(?P<col>\\w+)\" \\(", "tobe": "^(?P<col>\\w+) 컬럼에 지정된 길이보다 큰 값이 입력되었습니다", "same_groups": ["col"]}
    → as-is 'ORA-12899: value too large for column "S"."T"."COL" (actual: 12, maximum: 10)' 와 to-be 'COL 컬럼에 지정된 길이보다 큰 값이 입력되었습니다' 는
      같은 뜻이고, to-be 가 'NAME 컬럼에 …' 이면 다른 열이라 결함이다.
  예) {"class": "env", "reason": "로컬 DB 에 처리상태 공통코드가 없다", "tests": ["test_regulation_.*"], "what": "처리상태"}
  예) {"class": "blocked", "reason": "인쇄 양식이 없어 저장이 막힌다", "routes": ["/print/.*"], "cascade": true}

비교 때 (ui.py _after, Judge.step): 단계의 diff 줄 → html.rows_for 행 → 'same' 규칙에 맞는 행은 뺀다 → 남은 행이 없으면 같은 단계,
모두 결함 아닌 규칙에 맞으면 비교 제외(규칙마다 {step, sha256, class, reason, rule, rows} — 원장·JUnit·보고서에 갈래와 사유가 보인다),
하나라도 안 맞으면 다른 점 그대로다 (맞은 행의 갈래·사유는 notes 로 원장의 row_classes 에 남아 화면에 보인다).
cascade=true 규칙이 맞은 뒤에는 그 테스트의 뒤 단계에서 규칙에 안 맞는 행도 같은 갈래로 본다 (앞에서 막힌 것의 결과: 저장이 막히면 뒤의 '새 행 보임').
행으로 드러나지 않는 차이(주소·제목·API 응답·단계 문구, 순서 무시 비교, 값은 같고 상태만 바뀐 요소)가 섞인 단계는 규칙으로 판정하지 않는다 — 놓치지 않으려고.
골든 단계의 sha256 을 정확히 적는 allowed_differences 가 먼저다.
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from . import html

CLASSES = {"env": "자료·환경 차이", "blocked": "보류(대조할 자산 없음)", "tool": "도구 한계", "invalid": "비교 불가", "customer": "고객 결정(수정)"}
LABEL = {**CLASSES, "same": "같은 뜻"}
KEYS = {"class", "reason", "tests", "routes", "what", "asis", "tobe", "cascade", "same_groups", "quirk"}
SCOPE = ("tests", "routes", "what", "asis", "tobe")
SUMMARY_ROW = "to-be에만 있는 화면 내용"  # html.rows_for 가 to-be 에만 있는 줄이 셋 이상이면 한 행으로 줄인다


def describe(item: dict[str, Any]) -> str:
    """비교 제외 한 건을 한 줄로: '자료·환경 차이: 사유'. 갈래가 없는 것(allowed_differences 의 sha256)은 사유만."""
    cls = item.get("class")
    return f"{LABEL.get(cls, cls)}: {item.get('reason', '')}" if cls else str(item.get("reason", ""))


def _compile(p: Any, at: str, out: list[str]) -> re.Pattern | None:
    if not isinstance(p, str) or not p:
        out.append(f"{at}: 빈 정규식이거나 문자열이 아닙니다")
        return None
    try:
        return re.compile(p)
    except re.error as e:
        out.append(f"{at}: 정규식이 틀렸습니다 ({p!r}: {e})")
        return None


def problems(rules: Any, where: str = "difference_rules") -> list[str]:
    """규칙 목록의 문제. 빈 목록이면 쓸 수 있다."""
    if rules is None:
        return []
    if not isinstance(rules, list):
        return [f"{where}: 목록이어야 합니다"]
    out: list[str] = []
    for i, r in enumerate(rules):
        at = f"{where}[{i}]"
        if not isinstance(r, dict):
            out.append(f"{at}: 객체여야 합니다")
            continue
        if r.get("class") not in LABEL:
            out.append(f"{at}: 모르는 갈래 class={r.get('class')!r} (쓸 수 있는 것: {', '.join(LABEL)})")
        if not str(r.get("reason") or "").strip():
            out.append(f"{at}: reason(사유)이 비어 있습니다 — 결함에서 빼는 이유를 적습니다")
        if set(r) - KEYS:
            out.append(f"{at}: 모르는 키 {', '.join(sorted(set(r) - KEYS))}")
        if not any(r.get(k) for k in SCOPE):
            out.append(f"{at}: 범위(tests·routes·what·asis·tobe)가 없습니다 — 모든 다른 점을 덮게 됩니다")
        found = {k: _compile(r[k], f"{at}.{k}", out) for k in ("what", "asis", "tobe") if k in r}
        for k in ("tests", "routes"):
            if k in r and (not isinstance(r[k], list) or not all(isinstance(x, str) and x for x in r[k])):
                out.append(f"{at}.{k}: 문자열 목록이어야 합니다")
        if isinstance(r.get("routes"), list):
            for j, p in enumerate(r["routes"]):
                _compile(p, f"{at}.routes[{j}]", out)
        if "cascade" in r and not isinstance(r["cascade"], bool):
            out.append(f"{at}.cascade: true 또는 false")
        groups = r.get("same_groups")
        if r.get("class") == "same":
            if not (found.get("asis") and found.get("tobe")):
                out.append(f"{at}: 같은 뜻(same) 규칙은 asis 와 tobe 정규식이 둘 다 있어야 합니다")
            if not isinstance(groups, list) or not groups:
                out.append(f"{at}: 같은 뜻(same) 규칙은 same_groups(양쪽에서 같아야 하는 묶음 이름)가 있어야 합니다")
            else:
                for g in groups:
                    missing = [k for k in ("asis", "tobe") if found.get(k) and g not in found[k].groupindex]
                    if missing:
                        out.append(f"{at}: 묶음 (?P<{g}>…) 이 {'·'.join(missing)} 정규식에 없습니다")
        elif groups is not None:
            out.append(f"{at}: same_groups 는 같은 뜻(same) 규칙에만 씁니다")
    return out


def compile_rules(rules: list[dict[str, Any]] | None) -> list[dict[str, Any]]:
    """검사를 통과한 규칙 → 맞춰 보기 쉬운 모양. 문제가 있으면 ValueError."""
    errs = problems(rules)
    if errs:
        raise ValueError("difference_rules: " + "; ".join(errs))
    return [{**r, "_re": {k: re.compile(r[k]) for k in ("what", "asis", "tobe") if k in r},
             "_routes": [re.compile(p) for p in r.get("routes") or []]} for r in rules or []]


def load(oracle_dir: Path) -> list[dict[str, Any]]:
    """비교에 쓰는 규칙: 승인된 oracle.json 의 difference_rules, 그 뒤에 고객이 '수정'으로 정한 결정(quirks/<app>.decisions.json → quirks.decision_rules).
    결정은 골든 밖에 있지만 사람만 쓴다 (통합 화면, tools/guard_oracle.py 가 에이전트의 쓰기를 막는다)."""
    from . import oracle, quirks
    return compile_rules([*(oracle.load_config(oracle_dir).get("difference_rules") or []), *quirks.decision_rules(oracle_dir.name)])


def public(r: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in r.items() if not k.startswith("_")}


def _test_ok(r: dict[str, Any], test: str) -> bool:
    if "tests" not in r:
        return True
    for t in r["tests"]:
        if t == test:
            return True
        try:
            if re.fullmatch(t, test):
                return True
        except re.error:  # 대괄호가 든 매개변수 테스트 id 는 정규식이 아니라 글자 그대로다
            pass
    return False


def hit(r: dict[str, Any], row: tuple[str, str, str], test: str, paths: tuple[str, ...] = ()) -> bool:
    """한 행이 규칙에 맞는가. 같은 뜻(same) 규칙은 양쪽 정규식이 맞고 same_groups 의 묶음이 같아야 한다."""
    if not _test_ok(r, test):
        return False
    if r["_routes"] and not any(p.fullmatch(x) for p in r["_routes"] for x in paths if x):
        return False
    found = {}
    for k, v in zip(("what", "asis", "tobe"), row):
        rx = r["_re"].get(k)
        if rx is not None:
            found[k] = rx.search(str(v))
            if not found[k]:
                return False
    if r["class"] == "same":
        return all(found["asis"].group(g) == found["tobe"].group(g) for g in r["same_groups"])
    return True


def _key_val(line: str) -> tuple[str, str, str | None]:
    """diff 줄 하나 → html.rows_for 가 만드는 행의 (무엇이, 값)과 머리(역할·이름·상태). 머리는 요소 줄만 있다."""
    m = html.CONTROL.match(line)
    if not m:
        return "화면 문구", html._clean(line.removeprefix("text: ")), None
    head = line[:m.start(3) - 2] if m.group(3) is not None else line
    return m.group(2), html._clean(m.group(3) or "(값 없음)"), head


def rows_of(lines: list[str]) -> list[tuple[str, str, str]] | None:
    """한 단계의 diff 줄 → 행. 규칙으로 판정하면 안 되는 단계는 None: 행이 못 담는 줄(주소·제목·API·단계 문구, 순서 무시 비교의 '- '),
    행에 드러나지 않는 변화(같은 이름 요소의 역할·상태만 바뀜 → rows_for 가 같은 값이라 버린다)가 섞였을 때."""
    rows, _ = html.rows_for(["\n".join(lines)])
    summary = any(r[0] == SUMMARY_ROW for r in rows)
    heads: dict[str, set[str]] = {}
    for raw in lines:
        line = raw.strip()  # rows_for 와 같이 다듬는다
        if len(line) < 2 or raw.startswith(" ") or line.startswith(("---", "+++", "@@")):
            continue
        if line.startswith("dialogs: "):
            if not any(r[0] == "알림창" for r in rows):
                return None  # 종류만 바뀐 알림창(alert → confirm)은 행이 없다
            continue
        if line.startswith("requests: "):  # 요청 횟수 줄은 rows_for 가 '서버 요청 횟수 · …' 행으로 만든다
            if not html.REQUESTS.match(line):
                return None
            continue
        removed = line.startswith("-") and not line.startswith("- ")
        if not removed and not line.startswith("+"):
            return None
        what, val, head = _key_val(line[1:])
        if head is not None:
            heads.setdefault(what, set()).add(head)
        side = 1 if removed else 2
        if not any(r[0] == what and r[side] == val for r in rows) and not (not removed and summary):
            return None
    if not rows or any(len(h) > 1 for h in heads.values()):
        return None
    return rows


class Judge:
    """한 테스트의 단계들을 차례로 판정한다. cascade 가 테스트 안에서 이어지므로 테스트마다 하나 (ui.UI 가 만든다)."""

    def __init__(self, rules: list[dict[str, Any]]):
        self.rules = rules
        self.cascade: dict[str, Any] | None = None

    def _note(self, r: dict[str, Any], reason: str | None = None) -> dict[str, Any]:
        note = {"class": r["class"], "reason": reason or r["reason"]}
        return {**note, "quirk": r["quirk"]} if r.get("quirk") else note

    def step(self, lines: list[str], *, test: str, paths: tuple[str, ...] = ()) -> dict[str, Any]:
        """{verdict: same|accepted|diff, accepted: [{class, reason, rule, rows}], notes: {행: {class, reason, quirk?}}}.
        same = 같은 뜻 규칙으로 모든 행이 빠졌다 (차이가 아니다), accepted = 남은 행이 모두 결함 아닌 규칙에 맞았다, diff = 그 밖."""
        rows = rows_of(lines) if self.rules or self.cascade else None
        if rows is None:
            return {"verdict": "diff", "accepted": [], "notes": {}}
        notes: dict[tuple[str, str, str], dict[str, Any]] = {}
        used: dict[int, dict[str, Any]] = {}
        cascade = self.cascade
        for row in rows:
            r = next((r for r in self.rules if r["class"] == "same" and hit(r, row, test, paths)), None) \
                or next((r for r in self.rules if r["class"] != "same" and hit(r, row, test, paths)), None)
            if r is not None:
                notes[row] = self._note(r)
                used.setdefault(id(r), {"rule": r, "rows": []})["rows"].append(row)
                if r.get("cascade") and self.cascade is None:
                    cascade = r
            elif self.cascade is not None:
                c = self.cascade
                notes[row] = self._note(c, f"{c['reason']} (앞 단계에서 이어짐)")
                used.setdefault(id(c), {"rule": c, "rows": [], "cascaded": True})["rows"].append(row)
        self.cascade = cascade
        rest = [row for row in rows if notes.get(row, {}).get("class") != "same"]
        if not rest:
            return {"verdict": "same", "accepted": [], "notes": notes}
        if all(row in notes for row in rest):
            accepted = [{"class": u["rule"]["class"], "reason": u["rule"]["reason"] + (" (앞 단계에서 이어짐)" if u.get("cascaded") else ""),
                         "rule": public(u["rule"]), "rows": [list(x) for x in u["rows"][:5]]}
                        for u in used.values() if u["rule"]["class"] != "same"]
            return {"verdict": "accepted", "accepted": accepted, "notes": notes}
        return {"verdict": "diff", "accepted": [], "notes": notes}
