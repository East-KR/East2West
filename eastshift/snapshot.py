"""Playwright aria snapshot → 후보 요소 표.

Aside 없이 순수 Playwright의 `aria_snapshot()` 출력을 파싱한다.

- (role, name)이 중복이면 (캘린더 날짜 "15", 인원 "+" 등) 바로 앞에 나온 라벨(heading, 짧은 text, 이름 있는 컨테이너)을
  `scope`로 붙이고, 같은 (role, name) 안에서의 순번 `nth`로 실행 대상을 찾는다.
- 비활성 요소는 후보에서 빼고, 실행 시에도 `disabled=False`로 찾으므로 스냅샷 순번과 locator 순번이 맞는다.
"""
from __future__ import annotations

import re
from collections import Counter, defaultdict
from dataclasses import dataclass

ACTIONABLE_ROLES = ("link", "button", "textbox", "searchbox", "combobox", "checkbox", "radio", "tab", "menuitem", "option", "switch", "spinbutton", "slider")
EDITABLE_ROLES = ("textbox", "searchbox", "combobox", "spinbutton")
# 이 역할의 이름은 범위 라벨로 쓰지 않는다 (셀 이름은 버튼 이름과 같아 정보가 없다).
NOT_LABEL_ROLES = {"row", "cell", "gridcell", "columnheader", "rowheader", "rowgroup", "table", "list", "listitem", "img", "generic"}
LINE = re.compile(r'^(?P<indent>\s*)-\s+(?P<role>[a-z]+)(?:\s+"(?P<name>(?:\\.|[^"\\])*)")?(?P<rest>.*)$')
TEXT_LINE = re.compile(r'^(?P<indent>\s*)-\s+(?:text|heading|strong|emphasis|paragraph|term)\b[^:]*:\s*(?P<text>.+?)\s*$')
MAX_LABEL = 60
SIBLING_TTL_LINES = 45  # 같은 깊이 형제에 적용되는 범위 (평면 달력: 제목 뒤 날짜 버튼 ≤ 31개)
LABEL_TTL_LINES = 150  # 라벨은 이 줄 수 안의 요소에만 적용한다 (달력 한 달 ≈ 100줄). 먼 곳의 텍스트가 엉뚱하게 붙는 것을 막는다.


@dataclass(frozen=True)
class Element:
    role: str
    name: str
    order: int
    nth: int = 0          # 같은 (role, name) 중 몇 번째 (비활성 제외)
    dup: int = 1          # 같은 (role, name) 총 개수
    scope: str = ""       # 바로 앞 라벨 (heading / 짧은 text / 이름 있는 컨테이너)
    frame: str = ""       # 요소가 있는 프레임 ("" = 최상위 문서, 그 외 frame name 경로. 레거시 frameset/iframe)

    @property
    def key(self) -> tuple[str, str, str, int]:
        return (self.frame, self.role, self.name, self.nth)

    def describe(self, action: str) -> str:
        base = f'{action} {self.role} "{self.name[:100]}"'
        # 짧은 이름(날짜 "20", "+", "검색")이나 중복 이름은 앞선 라벨이 있어야 구분된다. 긴 이름은 그 자체로 충분하다.
        if self.scope and (self.dup > 1 or len(self.name) <= 12):
            base += f' in "{self.scope[:MAX_LABEL]}"'
        elif self.dup > 1:
            base += f" (#{self.nth + 1} of {self.dup})"
        if self.frame:
            base += f' @frame "{self.frame}"'
        return base

    def label(self) -> str:
        return (f'{self.role} "{self.name}"' + (f' in "{self.scope}"' if self.scope else (f" #{self.nth + 1}" if self.dup > 1 else ""))
                + (f' @frame "{self.frame}"' if self.frame else ""))


def _unquote(name: str) -> str:
    return name.replace('\\"', '"').replace("\\\\", "\\").strip()


@dataclass
class _Label:
    text: str
    lineno: int
    kind: str          # "text" (text/heading 줄) | "container" (이름 있는 컨테이너)
    siblings: int = 0  # 라벨 이후 같은 깊이에 나타난 형제 수


def parse_elements(snapshot: str, *, roles: tuple[str, ...] = ACTIONABLE_ROLES) -> list[Element]:
    """활성 요소를 페이지 순서대로 반환한다. 이름 없는 요소는 제외.

    라벨 적용 규칙
    - text/heading 라벨: 같은 깊이의 형제는 SIBLING_TTL_LINES 이내까지, 더 깊은 요소는 **바로 다음 형제 컨테이너**의 자손만 (LABEL_TTL_LINES 이내).
      → 달력 "2026.10." → 바로 다음 table의 날짜 버튼, "성인 …" → 옆의 -/+ 버튼. 멀리 떨어진 푸터·모달에는 붙지 않는다.
    - 이름 있는 컨테이너 라벨(navigation "여행 서비스" 등): 자손에만 적용.
    """
    labels: dict[int, _Label] = {}
    found: list[tuple[str, str, str]] = []  # role, name, scope
    for lineno, line in enumerate(snapshot.splitlines()):
        m = LINE.match(line)
        if not m:
            continue
        indent = len(m["indent"])
        role, raw_name, rest = m["role"], m["name"], m["rest"]
        for k in [k for k in labels if k > indent]:  # 부모를 벗어나면 깊은 라벨은 무효
            del labels[k]
        same = labels.get(indent)
        if same is not None and same.kind == "container":
            del labels[indent]  # 컨테이너의 형제가 나오면 그 컨테이너 라벨은 끝
            same = None
        text_m = TEXT_LINE.match(line)
        if text_m and len(text_m["text"]) <= MAX_LABEL:
            labels[indent] = _Label(text_m["text"], lineno, "text")
            continue
        if role == "heading" and raw_name and len(raw_name) <= MAX_LABEL:  # heading "2026.10." [level=3]
            labels[indent] = _Label(_unquote(raw_name), lineno, "text")
            continue
        if role in roles and raw_name and "[disabled]" not in rest:
            name = _unquote(raw_name)
            if name:
                scope = ""
                for k in sorted(labels, reverse=True):
                    lab = labels[k]
                    if k > indent:
                        continue
                    if lab.kind == "container" and k < indent:
                        scope = lab.text; break
                    if lab.kind == "text":
                        if k == indent and lineno - lab.lineno <= SIBLING_TTL_LINES:
                            scope = lab.text; break
                        if k < indent and lab.siblings <= 1 and lineno - lab.lineno <= LABEL_TTL_LINES:
                            scope = lab.text; break
                found.append((role, name, scope))
            if same is not None:
                same.siblings += 1
            continue
        if raw_name and role not in NOT_LABEL_ROLES and role not in roles and len(raw_name) <= MAX_LABEL:
            labels[indent] = _Label(_unquote(raw_name), lineno, "container")
            continue
        if same is not None:
            same.siblings += 1
    counts = Counter((r, n) for r, n, _ in found)
    seen: dict[tuple[str, str], int] = defaultdict(int)
    out: list[Element] = []
    for role, name, scope in found:
        nth = seen[(role, name)]
        seen[(role, name)] += 1
        out.append(Element(role, name, len(out), nth=nth, dup=counts[(role, name)], scope=scope))
    return out


def _grams(text: str) -> set[str]:
    text = text.casefold()
    tokens = set(re.findall(r"[\w가-힣]+", text))
    bigrams = {text[i:i + 2] for i in range(len(text) - 1) if not text[i:i + 2].isspace()}
    return tokens | bigrams


def relevance(element: Element, goal_grams: set[str]) -> int:
    score = len(goal_grams & _grams(element.name))
    if element.scope:
        score += len(goal_grams & _grams(element.scope))
    return score


def rank_for_goal(elements: list[Element], goal: str, *, limit: int) -> list[Element]:
    """후보 상한을 넘을 때 목표 문장과 겹치는 문자/단어가 많은 요소를 우선한다. 동점은 페이지 순서."""
    if len(elements) <= limit:
        return elements
    goal_grams = _grams(goal)
    scored = sorted(elements, key=lambda e: (-relevance(e, goal_grams), e.order))
    chosen = {e.key for e in scored[:limit]}
    return [e for e in elements if e.key in chosen]
