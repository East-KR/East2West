"""스텝 관찰값 기록(골든)과 비교. as-is → to-be 전환에서 "버그까지 동일하게 동작하는지"를 본다.

관찰값 = 스텝 직후 화면에 보이는 내용 + 입력 컨트롤 상태 + 그 스텝에서 뜬 alert/confirm/prompt + 그 스텝의 fetch/XHR 요청 (+ 선택적으로 URL, 제목).
- 요청 횟수도 동작이다: 양쪽이 모두 부른 같은 주소를 as-is 와 다른 횟수로 부르면 차이다 (한 번 누름에 두 번 읽는 as-is 면 to-be 도 두 번).
  한쪽만 부르는 주소는 보지 않는다 (전환하며 API 가 바뀌는 것이 보통이다). 주소는 name_map 의 '/' 항목으로 맞춘다. 요청을 기록하지 않은 옛 골든은 보지 않는다.
- 레이아웃이 바뀌어도 (frameset → 단일 페이지, table → div) 같은 내용이면 같다고 보도록, aria 스냅샷을 평탄화한다.
  비대화형 역할(paragraph, cell, heading, listitem …)은 텍스트만 남기고, 대화형 역할은 역할·이름·값·상태([checked] 등)를 남긴다.
- 매 실행마다 바뀌는 값(주문번호, 오늘 날짜, 시각)은 `ignore` 정규식으로 <masked> 처리한다.
"""
from __future__ import annotations

import difflib
import re
from collections import Counter
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlsplit

from .snapshot import ACTIONABLE_ROLES

LINE = re.compile(r'^-\s+(?P<role>[a-z]+)(?:\s+"(?P<name>(?:\\.|[^"\\])*)")?(?P<attrs>(?:\s+\[[^\]]*\])*)(?::\s*(?P<text>.*))?$')
STATE_ATTR = re.compile(r"\[(checked|selected|pressed|expanded|disabled)(?:=[^\]]*)?\]")
# 이름이 자손 내용을 이어 붙인 것이거나 (row "품목 노트북") 보이지 않는 aria-label인 (navigation "업무 메뉴") 컨테이너.
# 자식이 있으면 이름을 버리고 자식만 본다. 자식 없는 잎(cell "노트북")의 이름은 내용이므로 남긴다.
CONTAINER_ROLES = {"row", "cell", "gridcell", "rowgroup", "table", "grid", "treegrid", "list", "listitem", "navigation", "main", "region",
                   "group", "form", "search", "banner", "contentinfo", "complementary", "article", "document", "generic", "menu", "menubar",
                   "toolbar", "tablist", "tabpanel", "listbox", "tree", "iframe", "dialog", "alertdialog", "figure", "section", "none"}
HAS_WORD = re.compile(r"\w")


@dataclass
class CompareOptions:
    ignore: list[str] = field(default_factory=list)  # 정규식. 일치 부분을 <masked>로 바꾼 뒤 비교
    url: bool = False        # URL 경로+쿼리와 제목도 비교 (전환 시 URL 체계가 바뀌는 경우가 많아 기본은 끔)
    unordered: bool = False  # 줄 순서 무시 (레이아웃 재배치가 큰 경우)
    request_ignore: list[str] = field(default_factory=list)  # 정규식. 맞는 요청 주소는 횟수를 비교하지 않는다 (폴링·알림처럼 시각에 따라 횟수가 바뀌는 것)

    def merged(self, spec: dict[str, Any] | None) -> "CompareOptions":
        spec = spec or {}
        return CompareOptions(ignore=self.ignore + list(spec.get("ignore", [])),
                              url=bool(spec.get("url", self.url)), unordered=bool(spec.get("unordered", self.unordered)),
                              request_ignore=self.request_ignore + list(spec.get("request_ignore", [])))


def _unquote(s: str) -> str:
    return s.replace('\\"', '"').replace("\\\\", "\\")


def _value(s: str) -> str:
    """스냅샷의 YAML 값 표기 차이("1" vs 1)를 없앤다."""
    s = s.strip()
    if len(s) >= 2 and s[0] == s[-1] and s[0] in "\"'":
        s = _unquote(s[1:-1])
    return re.sub(r"\s+", " ", s).strip()


def flatten(snapshot: str, *, keep_urls: bool) -> list[str]:
    """aria 스냅샷 → 레이아웃에 덜 민감한 내용 줄 목록.

    드롭다운은 위젯 종류와 무관하게 `combobox "이름": 선택값` 한 줄로 만든다
    (네이티브 select는 option 자식을 모두 나열하고, 커스텀 드롭다운은 선택값만 보여 준다).
    """
    out: list[str] = []
    combo: tuple[int, int, str] | None = None  # (들여쓰기, out 위치, 이름): 자식 option을 모으는 중인 네이티브 select
    for raw in snapshot.splitlines():
        s = raw.strip()
        indent = len(raw) - len(raw.lstrip())
        if combo is not None and indent <= combo[0]:
            combo = None
        if combo is not None:  # select 안의 option: 선택된 것만 값으로 올린다
            m = LINE.match(s)
            if m and m["role"] == "option" and "[selected]" in (m["attrs"] or ""):
                out[combo[1]] = f'combobox "{combo[2]}": {_unquote(m["name"] or "")}'
            continue
        if s.startswith("- /url:"):
            if keep_urls:
                out.append("url: " + s[len("- /url:"):].strip())
            continue
        if s.startswith("## frame "):  # 프레임 경계는 레이아웃이다
            continue
        m = LINE.match(s)
        if not m:
            t = _value(s.lstrip("- "))
            if HAS_WORD.search(t) and not s.startswith("- /"):
                out.append("text: " + t)
            continue
        role, name, attrs, text = m["role"], _unquote(m["name"] or ""), m["attrs"] or "", _value(m["text"] or "")
        has_children = s.endswith(":") and not text
        if role == "combobox" and has_children:
            combo = (indent, len(out), name)
            out.append(f'combobox "{name}":')
            continue
        if role in ACTIONABLE_ROLES:
            states = " ".join(f"[{a}]" for a in STATE_ATTR.findall(attrs))
            line = f'{role} "{name}"' + (f" {states}" if states else "") + (f": {text}" if text else "")
            out.append(line)
            continue
        # 비대화형: 이름·텍스트만 (역할과 heading level은 레이아웃 표현이라 버린다)
        if role in CONTAINER_ROLES and has_children:
            name = ""
        for t in (name, text):
            t = re.sub(r"\s+", " ", t).strip()
            if HAS_WORD.search(t):  # "|", "·" 같은 구분자만 있는 줄은 레이아웃
                out.append("text: " + t)
    return out


def mask(lines: list[str], patterns: list[str]) -> list[str]:
    regs = [re.compile(p) for p in patterns]
    out = []
    for line in lines:
        for r in regs:
            line = r.sub("<masked>", line)
        out.append(line)
    return out


def relative_url(url: str) -> str:
    u = urlsplit(url)
    return u.path + (f"?{u.query}" if u.query else "")


def observation(*, index: int, kind: str, text: str, url: str, title: str, snapshot: str, dialogs: list[dict[str, Any]],
                opts: CompareOptions, api: list[dict[str, Any]] | None = None, requests: list[str] | None = None) -> dict[str, Any]:
    # snapshot 원문도 저장한다: 정규화 규칙이나 ignore를 바꿔도 as-is를 다시 기록하지 않고 비교할 수 있게.
    # requests: 그 스텝의 fetch/XHR 요청 "METHOD /경로" (쿼리 없이, 부른 만큼). 기록하는 쪽(pwtest)만 준다
    return {"index": index, "kind": kind, "text": text,
            "url": relative_url(url), "title": title,
            "content": mask(flatten(snapshot, keep_urls=opts.url), opts.ignore), "snapshot": snapshot,
            "dialogs": [{"type": d["type"], "message": mask([d["message"]], opts.ignore)[0], "action": d["action"]} for d in dialogs],
            "api": api or [], **({"requests": requests} if requests is not None else {})}


def rename(lines: list[str], name_map: dict[str, str]) -> list[str]:
    """승인된 이름 매핑을 골든에 적용: 요소 이름("수량")과 그 이름만 있는 텍스트 줄(text: 수량)만 바꾼다. 문장 속 단어는 그대로."""
    if not name_map:
        return lines
    out = []
    labels = {a: b for a, b in name_map.items() if not a.startswith("/")}  # '/'로 시작하는 키는 주소 매핑 (pwtest.ui.path_pairs): 화면 글이 아니다
    for line in lines:
        for a, b in labels.items():
            if line == f"text: {a}":
                line = f"text: {b}"
            line = line.replace(f'"{a}"', f'"{b}"')
        out.append(line)
    return out


def request_counts(golden: dict[str, Any], actual: dict[str, Any], opts: CompareOptions, name_map: dict[str, str] | None = None) -> list[str]:
    """양쪽이 모두 부른 주소를 다른 횟수로 불렀으면 "requests: GET /경로 ×as-is → ×to-be" 줄. as-is 주소는 name_map 의 '/' 항목으로 to-be 주소로 바꿔 맞춘다."""
    if "requests" not in golden or "requests" not in actual:
        return []
    from .pwtest.ui import tobe_path  # 순환 import 를 피해 여기서
    skip = [re.compile(p) for p in opts.request_ignore]

    def count(reqs: list[str], to: Any) -> Counter:
        c: Counter = Counter()
        for r in reqs:
            method, _, path = r.partition(" ")
            path = to(path)
            if not any(p.search(path) for p in skip):
                c[f"{method} {path}"] += 1
        return c
    a, b = count(golden["requests"], lambda p: tobe_path(p, name_map or {})), count(actual["requests"], lambda p: p)
    return [f"requests: {k} ×{a[k]} → ×{b[k]}" for k in sorted(set(a) & set(b)) if a[k] != b[k]]


def compare(golden: dict[str, Any], actual: dict[str, Any], opts: CompareOptions, name_map: dict[str, str] | None = None) -> list[str]:
    """차이가 없으면 빈 목록. 있으면 사람이 읽을 diff 줄."""
    out: list[str] = []
    if golden.get("text") != actual.get("text"):
        out.append(f"! step text changed since recording: {golden.get('text')!r} → {actual.get('text')!r}")
    if opts.url:
        if golden["url"] != actual["url"]:
            out.append(f"url: {golden['url']!r} → {actual['url']!r}")
        if golden["title"] != actual["title"]:
            out.append(f"title: {golden['title']!r} → {actual['title']!r}")
    gd = [f"{d['type']}: {mask([d['message']], opts.ignore)[0]}" for d in golden["dialogs"]]
    ad = [f"{d['type']}: {d['message']}" for d in actual["dialogs"]]
    if gd != ad:
        out.append(f"dialogs: {gd} → {ad}")
    if golden.get("api", []) != actual.get("api", []):
        out.append(f"api responses: {golden.get('api', [])!r} → {actual.get('api', [])!r}")
    out += request_counts(golden, actual, opts, name_map)
    # 양쪽 모두 원문에서 현재 규칙으로 다시 정규화한다 (원문이 없는 옛 골든은 저장된 content 사용)
    gc = rename(mask(flatten(golden["snapshot"], keep_urls=opts.url) if "snapshot" in golden else golden["content"], opts.ignore), name_map or {})
    ac = mask(flatten(actual["snapshot"], keep_urls=opts.url), opts.ignore)
    if opts.unordered:
        missing, extra = Counter(gc) - Counter(ac), Counter(ac) - Counter(gc)
        out += [f"- {l}" for l in missing.elements()] + [f"+ {l}" for l in extra.elements()]
    elif gc != ac:
        out += [l for l in difflib.unified_diff(gc, ac, "as-is(golden)", "actual", n=1, lineterm="")]
    return out
