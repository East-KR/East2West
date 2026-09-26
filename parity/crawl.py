"""화면 탐색기: 시작 화면에서 할 수 있는 동작을 모두 눌러 보고 화면 상태 그래프를 만든 뒤, 그 그래프로 시나리오 YAML을 만든다.

- 동작 목록은 aria 스냅샷에서 뽑는다 (parse_elements). Jev도 텍스트 생성 LLM도 부르지 않는다.
- 화면 상태 = 스냅샷 구조 서명 (입력값, [checked] 같은 상태, 숫자를 뺀다) + "입력을 채운 뒤인지".
  동작 뒤 서명이 같고 대화상자도 없으면 화면 안 동작(local), 아니면 전이(transition).
- 동작마다 새 브라우저 컨텍스트에서 시작 URL부터 경로를 다시 재생한 뒤 실행한다 (뒤로가기에 의존하지 않는다).
- 입력칸은 누르지 않고 픽스처 값으로만 채운다. 입력칸이 있는 상태에서는 전이 동작을 "그대로" / "채워서" 두 번 누른다.
  "채워서" = 픽스처 값 + 다른 입력칸 값을 바꾸는 화면 안 버튼(캘린더 날짜 등) 하나.
- (프레임, 역할, 범위 라벨, 숫자를 가린 이름)이 같은 요소가 group_min개 이상이면 첫 요소만 누른다 (캘린더 날짜, 페이지 번호).
- 목록성 화면(표의 행, 목록의 항목)은 같은 열의 요소를 한 템플릿으로 보고 행 몇 개만 대표로 누른다 (parity.lists): 분기 열(상태·유형 …)의 값 조합마다 하나,
  상한 --reps. 분기 열은 픽스처 pick > Jev 분류(캐시, margin 게이트) > 규칙 순. 같은 층의 대표들이 다른 화면으로 가면 그 층을 더 누른다 (적응 확장).
- deny 정규식에 걸리는 이름(삭제, 로그아웃 …)은 누르지 않고 기록만 한다. 다른 origin으로 나가는 동작은 따라가지 않는다.

산출물: graph.json, graph.md (mermaid + 표), 화면 스크린샷, 시나리오 YAML과 재생 캐시 (첫 실행부터 Jev 호출 0).
시나리오는 현재 동작의 기록이다. 그 동작이 맞는지는 사람이 검토한다 (as-is 골든 기록에 그대로 쓸 수 있다).
"""
from __future__ import annotations

import hashlib
import json
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import urljoin, urlparse

import yaml
from playwright.sync_api import Page, sync_playwright

from . import lists as _lists
from .runner import UA, Runner, _expand
from .snapshot import EDITABLE_ROLES, LINE, Element, parse_elements

# 누르면 되돌릴 수 없거나 밖으로 나가는 동작. 테스트 DB에서도 메일·문자·결재·이체는 실제 사람과 외부 기관에 닿을 수 있다.
DEFAULT_DENY = (r"삭제|탈퇴|해지|로그아웃|결제|이체|송금|환불|전송|발송|메일|문자|결재|상신|초기화|다운로드|엑셀|인쇄"
                r"|(?i:log ?out|sign ?out|delete|remove|download|print|send|mail|sms|transfer|refund|reset)")
CLICK_ROLES = ("button", "link", "tab", "menuitem", "switch")
TOGGLE_ROLES = ("checkbox", "radio", "option")
STATE_ATTR = re.compile(r"\s*\[(?:checked|pressed|selected|expanded|active|focused)(?:=[^\]]*)?\]")
DIGITS = re.compile(r"\d+")
HEADING = re.compile(r'^\s*-\s+heading\s+"(?P<name>(?:\\.|[^"\\])*)"')
ALERT = re.compile(r'^\s*-\s+alert(?:\s+"(?P<name>(?:\\.|[^"\\])*)")?(?::\s*(?P<text>.+))?$')
MODAL = re.compile(r'^\s*-\s+(?:dialog|alertdialog)\s+"(?P<name>(?:\\.|[^"\\])*)"')
CONTENT = re.compile(r'^\s*-\s+(?:text|paragraph|cell|gridcell|definition|listitem)(?:\s+"(?P<name>(?:\\.|[^"\\])*)")?(?::\s*(?P<text>.+))?$')
CHILD_TEXT = re.compile(r"^\s*-\s+[a-z]+(?:\s+\"[^\"]*\")?:\s*(?P<text>.+)$")
ROLE_KO = {"button": "버튼", "link": "링크", "tab": "탭", "menuitem": "메뉴", "switch": "스위치", "checkbox": "체크박스", "radio": "라디오 버튼",
           "option": "옵션", "textbox": "입력칸", "searchbox": "검색칸", "combobox": "콤보박스", "spinbutton": "숫자 입력칸"}


@dataclass
class Step:
    el: Element
    action: str                 # click | fill (option은 부모 select 선택, checkbox/radio는 check. Runner._act와 같다)
    value: str | None = None    # 원문. ${VAR}는 시나리오에 그대로 남기고 탐색할 때만 치환한다

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {"action": self.action, **asdict(self.el)}
        if self.value is not None:
            d["value"] = self.value
        return d


@dataclass
class Node:
    id: int
    sig: str
    filled: bool                # 같은 URL 안에서 입력을 채운 뒤 도달했다
    url: str
    loc: str                    # 페이지와 보이는 프레임들의 경로 (frameset은 페이지 URL이 늘 같으므로 프레임 URL까지 본다)
    title: str
    path: list[int]             # 시작 화면에서 오는 전이 edge id
    headings: list[str] = field(default_factory=list)
    alerts: list[str] = field(default_factory=list)
    modal: str = ""
    texts: list[str] = field(default_factory=list)  # 짧은 본문 텍스트 (전이 뒤 새로 나타난 내용을 expect로)
    missing_inputs: list[str] = field(default_factory=list)  # 픽스처 값이 없어 채우지 못한 입력칸
    snapshot: str = ""          # 이 상태의 스냅샷 원문: 이전/다음 화면에만 있는 문구를 고를 때 쓴다
    explored: bool = False
    screenshot: str = ""
    lists: list[dict[str, Any]] = field(default_factory=list)  # 이 화면의 목록 템플릿과 표본 (parity.lists.ListInfo.to_dict)

    @property
    def label(self) -> str:
        base = self.modal or (self.alerts[0] if self.alerts else "") or (self.headings[0] if self.headings else "") or self.title or urlparse(self.url).path
        return base + (" (입력 후)" if self.filled else "")


@dataclass
class Edge:
    id: int
    src: int
    steps: list[Step]
    mode: str                   # as-is | filled | dismiss (confirm을 취소)
    kind: str                   # transition | local | external | error | denied
    dst: int | None = None
    group: int = 1              # 대표로 누른 비슷한 요소 수
    preset_len: int = 0         # steps 앞쪽 중 "채우기" 스텝 수
    preset_sets: dict[str, str] = field(default_factory=dict)  # 채우기 버튼이 바꾼 입력칸 값 (시나리오 expect로)
    sets: dict[str, str] = field(default_factory=dict)         # local: 이 동작이 바꾼 다른 입력칸 값
    url_after: str = ""
    dialogs: list[dict[str, str]] = field(default_factory=list)
    js_errors: list[str] = field(default_factory=list)
    http_errors: list[str] = field(default_factory=list)
    reason: str = ""
    template: str = ""        # 목록 템플릿 대표일 때: '<목록 제목>:<열>' (parity.lists)
    row: int = -1              # 그 목록의 몇 번째 행
    stratum: dict[str, str] = field(default_factory=dict)  # 분기 열 값 (층)

    @property
    def action(self) -> Step:
        return self.steps[-1]


# 이름이 자기 내용을 이어 붙인 것인 역할 (row "품목 노트북", cell "1,235"). 이름을 넣으면 고른 값마다 다른 화면이 된다.
CONTENT_NAMED = {"row", "cell", "gridcell", "listitem"}


def signature(snapshots: list[tuple[str, str]]) -> str:
    """입력값·상태·숫자·내용에서 온 이름을 뺀 스냅샷 구조의 해시. 같은 화면이면 같은 값."""
    lines: list[str] = []
    for key, snap in snapshots:
        lines.append(f"## {key}")
        rows: dict[int, int] = {}   # 들여쓰기 → 연속된 row 수. 표는 머리 행 + 첫 데이터 행까지만 (행 수가 늘어도 같은 화면, 빈 표는 다른 화면)
        skip_below: int | None = None
        for line in snap.splitlines():
            m = LINE.match(line)
            indent = len(m["indent"]) if m else len(line) - len(line.lstrip())
            if skip_below is not None:
                if indent > skip_below:
                    continue
                skip_below = None
            if m and m["role"] == "row":
                rows[indent] = rows.get(indent, 0) + 1
                if rows[indent] > 2:
                    skip_below = indent
                    continue
            elif m:
                rows.pop(indent, None)
            if m:
                rest = STATE_ATTR.sub("", m["rest"])
                if m["role"] in EDITABLE_ROLES:
                    rest = rest.split(":", 1)[0]
                name = None if m["role"] in CONTENT_NAMED else m["name"]
                line = f'{m["indent"]}- {m["role"]}' + (f' "{name}"' if name is not None else "") + rest
            lines.append(DIGITS.sub("#", line))
    return hashlib.sha1("\n".join(lines).encode()).hexdigest()[:12]


def landmarks(snapshot_text: str) -> tuple[list[str], list[str], str, list[str]]:
    """(제목들, alert 문구들, 열린 모달 이름, 짧은 본문 텍스트들)."""
    headings, alerts, modal, texts = [], [], "", []
    lines = snapshot_text.splitlines()
    for i, line in enumerate(lines):
        if m := HEADING.match(line):
            headings.append(m["name"])
        elif m := ALERT.match(line):
            text = m["name"] or m["text"] or ""
            if not text and i + 1 < len(lines) and (c := CHILD_TEXT.match(lines[i + 1])):
                text = c["text"]
            if text.strip():
                alerts.append(text.strip())
        elif (m := MODAL.match(line)) and not modal:
            modal = m["name"]
        elif (m := CONTENT.match(line)) and 1 < len(t := (m["name"] or m["text"] or "").strip()) <= 60:
            texts.append(t)
    return headings, alerts, modal, texts


def sentence(step: Step) -> str:
    """재생 캐시의 키이자 Jev가 다시 고를 때 읽는 문장. 요소 이름과 범위 라벨을 그대로 쓴다."""
    el = step.el
    scoped = el.dup > 1 and el.scope and el.scope != el.name  # 범위 라벨은 휴리스틱이라 중복 이름을 가를 때만 쓴다
    where = (f'프레임 "{el.frame}"의 ' if el.frame else "") + (f'"{el.scope}" 영역의 ' if scoped else "")
    what = f'{ROLE_KO.get(el.role, el.role)} "{el.name}"' + (f" ({el.nth + 1}번째)" if el.dup > 1 and not scoped else "")
    if step.action == "fill":
        return f"{where}{what}에 값 입력"
    return f'{where}{what} {({"checkbox": "체크", "radio": "선택", "option": "선택"}).get(el.role, "누르기")}'


def stable_path(url: str) -> str:
    """URL 경로에서 숫자가 든 첫 조각부터 뺀다 (/reservations/3 → /reservations/). 매 실행 바뀌는 id를 피한다."""
    out = []
    for seg in urlparse(url).path.split("/"):
        if DIGITS.search(seg):
            return "/".join(out) + "/"
        out.append(seg)
    return "/".join(out)


class Crawler:
    def __init__(self, start: str, *, base_url: str | None = None, inputs: dict[str, Any] | None = None, deny: str = DEFAULT_DENY,
                 max_depth: int = 3, max_states: int = 30, max_actions: int = 40, group_min: int = 3, settle_ms: int = 400,
                 action_timeout_ms: int = 3000, storage_state: Path | None = None, headed: bool = False,
                 reps: int = 3, pick: dict[str, list[str]] | None = None, jev: Any | None = None, list_cache: Path | None = None, min_margin: float = 0.2):
        self.start_url = start if urlparse(start).scheme else urljoin((base_url or "").rstrip("/") + "/", start.lstrip("/"))
        # 시나리오·테스트의 goto는 항상 상대 경로: 절대 주소를 박아 두면 to-be 비교가 조용히 as-is를 치게 된다. 실행 때 --base-url로 as-is/to-be를 고른다
        u = urlparse(self.start_url)
        self.start = (u.path or "/") + (f"?{u.query}" if u.query else "")
        self.origin = f"{u.scheme}://{u.netloc}"
        if not urlparse(self.start_url).scheme:
            raise ValueError(f"relative start {start!r} needs --base-url or PARITY_BASE_URL")
        self.inputs = inputs or {}
        self.deny = re.compile(deny)
        self.max_depth, self.max_states, self.max_actions, self.group_min = max_depth, max_states, max_actions, group_min
        self.action_timeout_ms = action_timeout_ms
        self.storage_state, self.headed = storage_state, headed
        # 목록 표본화: 대표 행 상한, 픽스처 pick(목록 제목 → 분기 열), Jev 클라이언트(없으면 규칙), 분류 캐시
        self.reps, self.pick, self.jev, self.min_margin = reps, pick or {}, jev, min_margin
        self.list_cache_path = list_cache
        self.list_cache = _lists.load_cache(list_cache)
        self.rt = Runner(settle_ms=settle_ms)  # 프레임·스냅샷·요소 찾기·실행 도우미를 그대로 쓴다
        self.nodes: list[Node] = []
        self.edges: list[Edge] = []
        self._by_key: dict[tuple[str, bool], int] = {}
        self._events: dict[str, list] = {}
        self._reset_events()
        self.shots_dir: Path | None = None

    # -- 브라우저 ------------------------------------------------------------------------
    def _reset_events(self) -> None:
        self._events = {"dialogs": [], "js_errors": [], "http_errors": []}
        self._dismiss_confirm = False

    def _hook(self, pg: Page) -> None:
        def on_dialog(d) -> None:
            self._events["dialogs"].append({"type": d.type, "message": d.message})
            try:
                if d.type == "confirm" and self._dismiss_confirm:
                    d.dismiss()
                    return
                d.accept()
            except Exception:
                pass

        def on_response(r) -> None:
            try:
                if r.request.is_navigation_request() and r.status >= 400:
                    self._events["http_errors"].append(f"{r.status} {r.url}")
            except Exception:
                pass
        pg.on("dialog", on_dialog)
        pg.on("pageerror", lambda err: self._events["js_errors"].append(str(err).splitlines()[0]))
        pg.on("response", on_response)

    def _open(self, path: list[int]) -> Page:
        """새 컨텍스트에서 시작 URL을 열고 경로를 재생한다."""
        kwargs: dict[str, Any] = {"viewport": {"width": 1280, "height": 900}, "locale": "ko-KR", "user_agent": UA}
        if self.storage_state and self.storage_state.exists():
            kwargs["storage_state"] = str(self.storage_state)
        ctx = self.browser.new_context(**kwargs)
        ctx.on("page", self._hook)
        page = ctx.new_page()
        page.goto(self.start_url, wait_until="load")
        page = self.rt._settle(page)
        for eid in path:
            page = self._run(page, self.edges[eid].steps)
        return page

    def _run(self, page: Page, steps: list[Step]) -> Page:
        for s in steps:
            self.rt._act(page, s.el, s.action, _expand(s.value) if s.value is not None else None, timeout=self.action_timeout_ms)
            page = self.rt._settle(page)
        return page

    def _observe(self, page: Page) -> dict[str, Any]:
        snaps = self.rt._snapshots(page)
        text = "\n".join(snap if not key else f"## frame {key}\n{snap}" for key, snap in snaps)
        headings, alerts, modal, texts = landmarks(text)
        lists_, slots, offset = [], {}, 0  # 프레임마다 목록을 읽고 요소 번호를 전체 순서로 맞춘다 (Runner._elements 와 같은 순서)
        for key, snap in snaps:
            ls, sl = _lists.parse_lists(snap)
            for info in ls:
                info.id += len(lists_)
            slots.update({offset + i: _lists.Slot(sl[i].list_id + len(lists_), sl[i].row, sl[i].col) for i in sl})
            lists_ += ls
            offset += len(parse_elements(snap))
        elements = []
        for el in self.rt._elements(page, editable_only=False):
            try:
                if self.rt._is_visible(page, el):
                    elements.append(el)
            except Exception:
                continue
        if modal:  # 모달이 열려 있으면 배경(inert) 요소도 스냅샷에 남는다. 모달 안의 요소만 동작 대상으로.
            inside = []
            for el in elements:
                try:
                    if self.rt._locator(page, el).first.evaluate("e => !!e.closest('dialog[open], [aria-modal=true]')"):
                        inside.append(el)
                except Exception:
                    continue
            elements = inside or elements
        return {"url": page.url, "title": page.title(), "sig": signature(snaps), "elements": elements, "lists": lists_, "slots": slots,
                "headings": headings, "alerts": alerts, "modal": modal, "texts": texts, "snapshot": text,
                "loc": "|".join(urlparse(f.url).path for _, f in self.rt._frames(page))}

    def _values(self, page: Page) -> dict[str, str]:
        out = {}
        for el in self.rt._elements(page, editable_only=True):
            try:
                out[el.name] = self.rt._locator(page, el).first.input_value(timeout=500)
            except Exception:
                continue
        return out

    @staticmethod
    def _close(page: Page | None) -> None:
        if page is not None:
            try:
                page.context.close()
            except Exception:
                pass

    # -- 탐색 ----------------------------------------------------------------------------
    def _node_for(self, obs: dict[str, Any], filled: bool, path: list[int], page: Page) -> int | None:
        key = (obs["sig"], filled)
        if key in self._by_key:
            return self._by_key[key]
        if len(self.nodes) >= self.max_states:
            return None
        node = Node(len(self.nodes), obs["sig"], filled, obs["url"], obs["loc"], obs["title"], path,
                    headings=obs["headings"], alerts=obs["alerts"], modal=obs["modal"], texts=obs["texts"], snapshot=obs["snapshot"])
        if self.shots_dir:
            node.screenshot = str(self.shots_dir / f"n{node.id}.png")
            try:
                page.screenshot(path=node.screenshot)
            except Exception:
                node.screenshot = ""
        self.nodes.append(node)
        self._by_key[key] = node.id
        print(f"  + n{node.id} {node.label}  [{urlparse(node.url).path}]", flush=True)
        return node.id

    def _try(self, node: Node, steps: list[Step], mode: str, *, group: int = 1, preset_len: int = 0,
             preset_sets: dict[str, str] | None = None, dismiss: bool = False) -> Edge:
        """경로를 재생하고 steps를 실행해 결과를 edge로. edge id는 호출자가 곧바로 추가한다는 전제."""
        edge = Edge(len(self.edges), node.id, steps, mode, "error", group=group, preset_len=preset_len, preset_sets=preset_sets or {})
        page = None
        try:
            page = self._open(node.path)
            before = self._values(page)
            self._reset_events()
            self._dismiss_confirm = dismiss
            if preset_len:  # 채우기 스텝을 다 실행한 뒤의 실제 값을 기대값으로 (버튼 하나만 눌렀을 때 값과 다를 수 있다: 계산 버튼)
                page = self._run(page, steps[:preset_len])
                if edge.preset_sets:
                    now = self._values(page)
                    edge.preset_sets = {k: now.get(k, v) for k, v in edge.preset_sets.items()}
            page = self._run(page, steps[preset_len:])
            obs = self._observe(page)
        except Exception as e:
            edge.reason = str(e).splitlines()[0][:200]
            self._close(page)
            return edge
        edge.url_after = page.url
        edge.dialogs, edge.js_errors, edge.http_errors = (list(self._events[k]) for k in ("dialogs", "js_errors", "http_errors"))
        if urlparse(page.url).netloc != urlparse(self.start_url).netloc:
            edge.kind = "external"
        elif obs["sig"] == node.sig and not edge.dialogs:
            edge.kind = "local"
            if steps[-1].el.role not in TOGGLE_ROLES:  # 체크박스·옵션은 자기 값만 바꾼다
                edge.sets = {k: v for k, v in self._values(page).items() if before.get(k) != v and k != steps[-1].el.name}
        else:
            edge.kind = "transition"
            filled = (mode == "filled" or node.filled) and obs["loc"] == node.loc
            edge.dst = self._node_for(obs, filled, node.path + [edge.id], page)
            if edge.dst is None:
                edge.reason = f"state budget {self.max_states} reached"
        self._close(page)
        return edge

    def _candidates(self, elements: list[Element], obs: dict[str, Any] | None = None
                    ) -> tuple[list[tuple[Element, int, dict[str, Any] | None]], list[Element]]:
        """누를 후보 (요소, 비슷한 요소 수, 템플릿 메타). 목록 템플릿(같은 표의 같은 열)은 대표 행만, 나머지는 이름 그룹 규칙."""
        lists_: list[_lists.ListInfo] = (obs or {}).get("lists", [])
        slots: dict[int, _lists.Slot] = (obs or {}).get("slots", {})
        groups: dict[tuple[str, str, str, str], list[Element]] = {}
        tpl: dict[tuple[int, int, str], dict[int, Element]] = {}   # (목록, 열, 역할) → 행 → 요소
        for el in elements:
            if el.role not in CLICK_ROLES and el.role not in TOGGLE_ROLES:
                continue
            slot = slots.get(el.order)
            if slot is not None and slot.list_id < len(lists_) and lists_[slot.list_id].rows:
                tpl.setdefault((slot.list_id, slot.col, el.role), {}).setdefault(slot.row, el)
            else:
                groups.setdefault((el.frame, el.role, el.scope, DIGITS.sub("#", el.name)), []).append(el)
        out: list[tuple[Element, int, dict[str, Any] | None]] = []
        denied = []
        for els in groups.values():
            n = len(els) if len(els) >= self.group_min else 1
            for el in (els[:1] if n > 1 else els):
                if self.deny.search(el.name):
                    denied.append(el)
                else:
                    out.append((el, n, None))
        self._tpl = tpl
        decided: set[int] = set()
        for (lid, col, role), by_row in tpl.items():
            info = lists_[lid]
            if lid not in decided:
                _lists.decide(info, pick=self.pick, jev=self.jev, page_title=(obs or {}).get("title", ""), cache=self.list_cache, min_margin=self.min_margin)
                decided.add(lid)
            if len(by_row) < self.group_min:  # 행이 몇 개 안 되면 그냥 다 누른다
                rows = sorted(by_row)
            else:
                rows = [r for r in _lists.choose_reps(info, self.reps) if r in by_row] or sorted(by_row)[:1]
            key = f"{info.heading or info.kind}:{info.headers[col] if col < len(info.headers) else col}"
            info.reps[f"{key}:{role}"] = rows
            for r in rows:
                el = by_row[r]
                meta = {"template": key, "list": lid, "col": col, "role": role, "row": r, "stratum": _lists.stratum(info, r)}
                if self.deny.search(el.name):
                    denied.append(el)
                else:
                    out.append((el, len(by_row), meta))
        out.sort(key=lambda t: t[0].order)
        return out[:self.max_actions], denied

    def _preset(self, elements: list[Element], local: list[Edge]) -> tuple[list[Step], dict[str, str], list[str]]:
        """입력 채우기 스텝, 그 스텝이 바꾸는 입력칸 값, 픽스처가 없어 못 채운 입력칸."""
        setters: list[Edge] = []
        covered: dict[str, str] = {}
        for e in local:  # 다른 입력칸 값을 바꾸는 버튼 (캘린더 날짜 → 체크인 입력칸). 입력칸마다 하나.
            if e.action.el.role not in TOGGLE_ROLES and e.sets and not set(e.sets) <= set(covered):
                setters.append(e)
                covered.update(e.sets)
        steps = [e.action for e in setters]
        missing = []
        for el in elements:
            if el.role in EDITABLE_ROLES:
                if el.name in covered:
                    continue
                if el.name not in self.inputs:
                    missing.append(el.name)
                    continue
                v = str(self.inputs[el.name])
                opt = next((o for o in elements if o.role == "option" and o.name == v), None) if el.role == "combobox" else None
                steps.append(Step(opt, "click") if opt else Step(el, "fill", v))
            elif el.role in ("checkbox", "radio") and self.inputs.get(el.name) is True:
                steps.append(Step(el, "click"))
        steps.sort(key=lambda s: s.el.order)
        return steps, covered, missing

    def _explore(self, node: Node) -> None:
        page = None
        try:
            page = self._open(node.path)
            obs = self._observe(page)
        except Exception as e:
            print(f"  ! n{node.id} replay failed: {str(e).splitlines()[0][:160]}")
            self._close(page)
            return
        self._close(page)
        cands, denied = self._candidates(obs["elements"], obs)
        node.lists = [info.to_dict() for info in obs.get("lists", []) if info.rows]
        for el in denied:
            self.edges.append(Edge(len(self.edges), node.id, [Step(el, "click")], "as-is", "denied", reason="deny pattern"))
        sampled = [(info, sum(len(v) for v in info.reps.values())) for info in obs.get("lists", []) if info.reps]
        print(f"n{node.id} {node.label}: {len(cands)} actions" + (f", {len(denied)} denied" if denied else "")
              + "".join(f" · 목록 '{i.heading or i.kind}' {len(i.rows)}행 중 대표 {n} ({i.source}{'' if not i.branch_cols else ': ' + ', '.join(i.headers[c] for c in i.branch_cols)})" for i, n in sampled), flush=True)
        as_is = []
        for el, n, meta in cands:
            e = self._try(node, [Step(el, "click")], "as-is", group=n)
            if meta:
                e.template, e.row, e.stratum = meta["template"], meta["row"], meta["stratum"]
            self.edges.append(e)
            as_is.append(e)
        as_is += self._expand_strata(node, obs, as_is)
        for e in list(as_is):
            self._dismissed(node, e)
        if node.filled:
            return
        preset, sets, node.missing_inputs = self._preset(obs["elements"], [e for e in as_is if e.kind == "local"])
        if not preset:
            return
        in_preset = {s.el.key for s in preset}
        for e in as_is:
            el = e.action.el
            if el.role in TOGGLE_ROLES or el.key in in_preset:
                continue
            f = self._try(node, preset + [e.action], "filled", group=e.group, preset_len=len(preset), preset_sets=sets)
            same = (f.kind, f.dst, [d["message"] for d in f.dialogs]) == (e.kind, e.dst, [d["message"] for d in e.dialogs])
            if not same:
                self.edges.append(f)
                self._dismissed(node, f)

    def _expand_strata(self, node: Node, obs: dict[str, Any], as_is: list[Edge]) -> list[Edge]:
        """적응 확장: 같은 목록·같은 열·같은 층의 대표들이 서로 다른 화면으로 갔으면 분기 열이 틀린 것이다. 그 층의 다른 행을 둘 더 누른다."""
        lists_: list[_lists.ListInfo] = obs.get("lists", [])
        extra: list[Edge] = []
        by_key: dict[tuple[str, tuple], list[Edge]] = {}
        for e in as_is:
            if e.template:
                by_key.setdefault((e.template, tuple(e.stratum.items())), []).append(e)
        for (template, _), edges in by_key.items():
            results = {(e.kind, e.dst) for e in edges}
            if len(edges) < 2 or len(results) < 2:
                continue
            e0 = edges[0]
            tkey = next((k for k, by_row in self._tpl.items() if any(by_row.get(e.row) is e.action.el for e in edges)), None)
            if tkey is None:
                continue
            lid, col, role = tkey
            info = lists_[lid]
            tried = {e.row for e in edges}
            for r in _lists.more_rows(info, e0.row, exclude=tried, limit=2):
                el = self._tpl[tkey].get(r)
                if el is None or self.deny.search(el.name):
                    continue
                e = self._try(node, [Step(el, "click")], "as-is", group=e0.group)
                e.template, e.row, e.stratum = template, r, _lists.stratum(info, r)
                e.reason = (e.reason + "; " if e.reason else "") + "적응 확장: 같은 층의 대표들이 다른 화면으로 감"
                self.edges.append(e)
                extra.append(e)
            rk = f"{template}:{role}"
            info.reps[rk] = sorted(set(info.reps.get(rk, [])) | tried | {e.row for e in extra if e.template == template})
            node.lists = [i.to_dict() for i in lists_ if i.rows]
        return extra

    def _dismissed(self, node: Node, e: Edge) -> None:
        """confirm이 뜬 동작은 '취소'한 경로도 한 번 누른다 (취소하면 입력이 남는지, 저장이 안 되는지)."""
        if e.kind != "transition" or not any(d["type"] == "confirm" for d in e.dialogs):
            return
        d = self._try(node, e.steps, "dismiss", group=e.group, preset_len=e.preset_len, preset_sets=e.preset_sets, dismiss=True)
        if d.kind == "local":  # 취소하면 화면이 그대로인 것이 정상: 대화상자는 떴으므로 자기 자신으로 가는 전이로 기록
            d.kind, d.dst = "transition", node.id
        self.edges.append(d)

    def run(self, shots_dir: Path | None = None) -> None:
        self.shots_dir = shots_dir
        if shots_dir:
            shots_dir.mkdir(parents=True, exist_ok=True)
        with sync_playwright() as p:
            self.browser = p.chromium.launch(headless=not self.headed, args=["--disable-blink-features=AutomationControlled"])
            try:
                page = self._open([])
                self._node_for(self._observe(page), False, [], page)
                self._close(page)
                i = 0
                while i < len(self.nodes):
                    node = self.nodes[i]
                    i += 1
                    if len(node.path) < self.max_depth:
                        self._explore(node)
                        node.explored = True
            finally:
                self.browser.close()

    # -- 산출물 --------------------------------------------------------------------------
    def to_dict(self) -> dict[str, Any]:
        return {"start": self.start, "start_url": self.start_url, "inputs": sorted(self.inputs), "deny": self.deny.pattern,
                "nodes": [asdict(n) | {"label": n.label} for n in self.nodes],
                "edges": [asdict(e) | {"steps": [s.to_dict() for s in e.steps], "sentence": sentence(e.action)} for e in self.edges]}

    def paths(self) -> list[list[int]]:
        """모든 전이 edge를 한 번 이상 지나는 경로. 다른 경로의 앞부분인 경로는 뺀다."""
        cands = [self.nodes[e.src].path + [e.id] for e in self.edges if e.kind == "transition" and e.dst is not None]
        cands.sort(key=len, reverse=True)
        kept: list[list[int]] = []
        for p in cands:
            if not any(k[:len(p)] == p for k in kept):
                kept.append(p)
        return sorted(kept, key=lambda p: p)

    def scenario(self, path: list[int]) -> tuple[dict[str, Any], dict[str, Any]]:
        """(시나리오, 재생 캐시). 캐시 키 규칙은 Runner.run과 같다 (같은 문장이 반복되면 ' #n')."""
        root = self.nodes[0]
        steps: list[dict[str, Any]] = [{"goto": self.start}]
        if root.title:
            steps.append({"expect": {"title_contains": root.title}})
        cache: dict[str, Any] = {}
        seen: dict[str, int] = {}
        labels = [root.label]
        for eid in path:
            e = self.edges[eid]
            src = self.nodes[e.src]
            for i, s in enumerate(e.steps):
                text = sentence(s)
                seen[text] = seen.get(text, 0) + 1
                key = text if seen[text] == 1 else f"{text} #{seen[text]}"
                if e.mode == "dismiss" and i == len(e.steps) - 1:
                    steps.append({"dialog": "dismiss"})
                step: dict[str, Any] = {"do": text}
                if s.action == "fill":
                    step["fill"] = s.value
                steps.append(step)
                cache[key] = {"role": s.el.role, "name": s.el.name, "nth": s.el.nth, "dup": s.el.dup, "scope": s.el.scope, "step": text}
                if s.el.frame:
                    cache[key]["frame"] = s.el.frame
                if i == e.preset_len - 1:
                    steps += [{"expect": {"field": k, "value": v}} for k, v in e.preset_sets.items()]
            if e.dialogs:
                steps.append({"expect": {"dialog": e.dialogs[-1]["message"]}})
            dst = self.nodes[e.dst]
            if dst.id != src.id:
                steps += self._expects(src, dst)
                labels.append(dst.label)
            else:
                labels.append(f"{dst.label} ({'취소' if e.mode == 'dismiss' else '대화상자'})")
        return {"name": "[탐색] " + " → ".join(labels), "steps": steps}, cache

    @staticmethod
    def _expects(src: Node, dst: Node) -> list[dict[str, Any]]:
        """화면이 실제로 바뀌었는지 가려내는 검증. 이전 화면에도 맞는 검증(접두어가 같은 URL, 이전 제목에 포함된 문구)은 쓰지 않는다.

        - URL: 경로가 바뀌었으면 정확한 경로(url_path). 숫자 id가 든 경로는 id 앞까지를 url_contains로, 이전 경로에도 맞으면 생략.
        - 새 화면에만 있는 문구 하나 (모달 > alert > 제목 > 본문 순). 이전 화면 스냅샷에 들어 있지 않아야 한다.
          숫자가 든 문구는 숫자 자리만 \\d+ 인 패턴(text_matches): 건수·번호가 바뀌어도 통과, 문구가 바뀌면 실패.
        - 이전 화면에만 있던 문구 하나가 사라졌는지 (no_text). 새 화면 스냅샷에 들어 있지 않아야 한다.
        """
        out: list[dict[str, Any]] = []
        sp_, dp = urlparse(src.url).path, urlparse(dst.url).path
        if dp != sp_:
            if not DIGITS.search(dp):
                out.append({"expect": {"url_path": dp}})
            elif (stable := stable_path(dst.url)) not in ("", "/") and not sp_.startswith(stable):
                out.append({"expect": {"url_contains": stable}})

        def check(c: str) -> tuple[str, str] | None:
            """문구 → (검증 종류, 값). 숫자가 든 문구(총 2건, 주문번호 420)는 실행마다 바뀌므로 숫자 자리만 \\d+ 인 패턴으로.
            숫자를 빼면 글자가 2자도 안 남는 문구(2026-08, 1,235)는 형식이 없어서 쓰지 않는다."""
            if not DIGITS.search(c):
                return ("text", c)
            if len(re.findall(r"[^\W\d_]", c)) < 2:
                return None
            pattern = r"\d+".join(re.escape(part) for part in DIGITS.split(c))
            return ("text_matches", pattern.replace(r"\ ", r"\s+"))

        def found(chk: tuple[str, str], snapshot: str) -> bool:
            return chk[1] in snapshot if chk[0] == "text" else re.search(chk[1], snapshot) is not None

        def cands(n: Node) -> list[tuple[str, str]]:
            raw = ([n.modal] if n.modal else []) + n.alerts + n.headings + n.texts
            return [chk for c in raw if c and c.strip() and (chk := check(c))]
        new = next((c for c in cands(dst) if not found(c, src.snapshot)), None)
        gone = next((c for c in cands(src) if c[0] == "text" and not found(c, dst.snapshot)), None)  # 사라진 문구는 숫자 없는 것만
        if new:
            out.append({"expect": {new[0]: new[1]}})
        if gone:
            out.append({"expect": {"no_text": gone[1]}})
        return out

    def preview(self) -> dict[str, Any]:
        """--dry-run: 아무것도 누르지 않고 시작 화면에서 누를 것, 누르지 않을 것, 채울 입력칸을 보여 준다."""
        with sync_playwright() as p:
            self.browser = p.chromium.launch(headless=not self.headed, args=["--disable-blink-features=AutomationControlled"])
            try:
                page = self._open([])
                obs = self._observe(page)
                self._close(page)
            finally:
                self.browser.close()
        cands, denied = self._candidates(obs["elements"], obs)
        fills = [(el.name, self.inputs[el.name]) for el in obs["elements"] if el.role in EDITABLE_ROLES and el.name in self.inputs]
        missing = [el.name for el in obs["elements"] if el.role in EDITABLE_ROLES and el.name not in self.inputs]
        return {"url": obs["url"], "title": obs["title"],
                "click": [(sentence(Step(el, "click")) + (f" [목록 {m['template']} {m['row'] + 1}행]" if m else ""), n) for el, n, m in cands],
                "deny": [sentence(Step(el, "click")) for el in denied], "fill": fills, "missing": missing}

    def pytest_module(self) -> str:
        """같은 경로를 Playwright 테스트(ui fixture)로. 승인·결함 주입·검증 보고서 흐름에 그대로 올라간다."""
        L = ['"""parity crawl이 만든 테스트. 현재 동작의 기록이다. 검토하고 업무상 중요한 값(금액 등) 확인을 더한 뒤 e2e/<app>/로 옮겨 쓴다.',
             "", f"시작: {self.start}", '"""', "from parity.runner import _expand", ""]
        for i, path in enumerate(self.paths(), 1):
            spec, cache = self.scenario(path)
            L += ["", f"def test_crawl_{i:02d}(ui):", f'    """{spec["name"].removeprefix("[탐색] ")}"""']
            seen: dict[str, int] = {}
            for st in spec["steps"]:
                if "goto" in st:
                    L.append(f"    ui.goto({st['goto']!r})")
                elif "dialog" in st:
                    L.append(f"    ui.dialog({st['dialog']!r})")
                elif "do" in st:
                    seen[st["do"]] = seen.get(st["do"], 0) + 1
                    c = cache[st["do"] if seen[st["do"]] == 1 else f"{st['do']} #{seen[st['do']]}"]
                    nth = f", nth={c['nth']}" if c.get("dup", 1) > 1 else ""
                    if "fill" in st:
                        v = st["fill"]
                        val = f"_expand({v!r})" if "${" in str(v) else repr(v)
                        L.append(f"    ui.fill({c['name']!r}, {val}, role={c['role']!r}{nth})")
                    else:
                        L.append(f"    ui.act({c['role']!r}, {c['name']!r}{nth})")
                else:
                    exp = st["expect"]
                    if "field" in exp:
                        L.append(f"    ui.expect_field({exp['field']!r}, {exp['value']!r})")
                        continue
                    k, v = next(iter(exp.items()))
                    call = {"title_contains": "expect_title", "text": "expect_text", "text_matches": "expect_text_matches", "no_text": "expect_no_text",
                            "url_path": "expect_url_path", "url_contains": "expect_url_contains", "dialog": "expect_dialog"}[k]
                    L.append(f"    ui.{call}({v!r})")
        return "\n".join(L) + "\n"

    def write(self, out_dir: Path, cache_dir: Path) -> list[Path]:
        marker = check_output_dir(out_dir)
        out_dir.mkdir(parents=True, exist_ok=True)
        cache_dir.mkdir(parents=True, exist_ok=True)
        marker.write_text("parity crawl output: regenerated on every crawl. Move reviewed files out before editing them.\n", encoding="utf-8")
        if self.list_cache:  # 분기 열 분류 캐시: 표식이 생긴 뒤에 쓴다 (먼저 쓰면 out 이 '탐색 산출물 폴더가 아닌 것'으로 보인다)
            _lists.save_cache(self.list_cache_path or cache_dir / "lists.json", self.list_cache)
        (out_dir / "graph.json").write_text(json.dumps(self.to_dict(), ensure_ascii=False, indent=1), encoding="utf-8")
        (out_dir / "graph.md").write_text(self.markdown(), encoding="utf-8")
        for old in out_dir.glob("crawl_*.yaml"):  # 이전 탐색이 만든 시나리오 (이 도구의 산출물)
            old.unlink()
            (cache_dir / f"{old.stem}.json").unlink(missing_ok=True)
        written = []
        for i, path in enumerate(self.paths(), 1):
            spec, cache = self.scenario(path)
            f = out_dir / f"crawl_{i:02d}.yaml"
            header = ("# parity crawl이 만든 시나리오. 현재 동작의 기록이므로 맞는 동작인지 검토한 뒤 쓴다.\n"
                      "# 이 폴더는 crawl을 다시 돌리면 덮어쓴다. 검토한 파일은 scenarios/<app>/로 옮겨서 고친다.\n")
            f.write_text(header + yaml.safe_dump(spec, allow_unicode=True, sort_keys=False, width=200), encoding="utf-8")
            (cache_dir / f"{f.stem}.json").write_text(json.dumps(cache, ensure_ascii=False, indent=1), encoding="utf-8")
            written.append(f)
        (out_dir / "test_crawl.py").write_text(self.pytest_module(), encoding="utf-8")
        return written

    def markdown(self) -> str:
        def q(s: str) -> str:
            return s.replace('"', "'").replace("\n", " ")[:60]
        out = ["# 화면 탐색 결과", "", f"시작: `{self.start}`, 상태 {len(self.nodes)}개, 동작 {len(self.edges)}개", "", "```mermaid", "flowchart TD"]
        for n in self.nodes:
            out.append(f'  n{n.id}["n{n.id} {q(n.label)}<br/>{q(urlparse(n.url).path)}"]')
        for e in self.edges:
            if e.kind == "transition" and e.dst is not None:
                lab = {"filled": "채워서: ", "dismiss": "취소: "}.get(e.mode, "") + sentence(e.action) + (f" / {e.dialogs[-1]['type']}: {e.dialogs[-1]['message']}" if e.dialogs else "")
                out.append(f'  n{e.src} -->|"{q(lab)}"| n{e.dst}')
        out += ["```", "", "## 동작", "", "| 상태 | 방식 | 동작 | 결과 | 비고 |", "| :--- | :--- | :--- | :--- | :--- |"]
        result = {"local": "화면 안", "external": "외부 이동 (따라가지 않음)", "error": "실행 실패", "denied": "누르지 않음 (deny)"}
        for e in self.edges:
            res = f"→ n{e.dst}" if e.kind == "transition" and e.dst is not None else result.get(e.kind, e.kind)
            notes = [e.reason] if e.reason else []
            if e.template:
                notes.append(f"목록 대표: {e.template} {e.row + 1}행" + (f" ({', '.join(f'{k}={v}' for k, v in e.stratum.items())})" if e.stratum else "") + (f" / {e.group}행 중" if e.group > 1 else ""))
            elif e.group > 1:
                notes.append(f"비슷한 요소 {e.group}개 중 대표")
            notes += [f"{k} = {v}" for k, v in e.sets.items()]
            notes += [f"{d['type']}: {d['message']}" for d in e.dialogs] + [f"JS 오류: {x}" for x in e.js_errors] + [f"HTTP {x}" for x in e.http_errors]
            out.append(f"| n{e.src} | {e.mode} | {sentence(e.action)} | {res} | {'; '.join(notes)} |")
        missing = [(n, n.missing_inputs) for n in self.nodes if n.missing_inputs]
        if missing:
            out += ["", "## 픽스처 값이 없는 입력칸", "", "`inputs:`에 값을 주면 \"채워서\" 경로가 이 칸도 채운다.", ""]
            out += [f"- n{n.id} {n.label}: {', '.join(m)}" for n, m in missing]
        sampled = [(n, L) for n in self.nodes for L in n.lists if L.get("reps")]
        if sampled:
            out += ["", "## 목록 표본", "", "목록성 화면은 행을 전부 누르지 않고 분기 열의 값 조합마다 대표 하나(상한 --reps)만 누른다. "
                    "분기 열은 픽스처 `pick` > Jev 분류 > 규칙. `검토`가 붙은 목록은 Jev가 확신하지 못해 규칙으로 정했으니 사람이 `pick:`으로 확정한다.", "",
                    "| 상태 | 목록 | 행 | 분기 열 (근거) | 대표 행 |", "| :--- | :--- | :--- | :--- | :--- |"]
            for n, L in sampled:
                branch = ", ".join(L["branch"]) or "(없음 → 첫·끝 행)"
                src = L["source"] + (f" margin {L['margin']}" if L.get("margin") is not None else "") + (" **검토**" if "abstain" in L["source"] else "")
                reps = "; ".join(f"{k.rsplit(':', 1)[0].split(':', 1)[-1]}: {', '.join(str(r + 1) for r in v)}" for k, v in L["reps"].items())
                out.append(f"| n{n.id} | {q(L['heading'] or L['kind'])} | {L['rows']} | {q(branch)} ({q(src)}) | {q(reps)} |")
        unexplored = [n for n in self.nodes if not n.explored]
        if unexplored:
            out += ["", f"## 탐색하지 않은 상태 (깊이 {self.max_depth} 도달)", ""] + [f"- n{n.id} {n.label}" for n in unexplored]
        return "\n".join(out) + "\n"


def check_output_dir(out_dir: Path) -> Path:
    """crawl 산출물 폴더만 덮어쓴다. 사람이 쓰거나 고친 파일이 있는 폴더(표식 없음)면 멈춘다."""
    marker = out_dir / ".crawl-output"
    if out_dir.exists() and not marker.exists() and any(p for p in out_dir.iterdir() if p.name != "screens"):
        raise SystemExit(f"{out_dir} is not a crawl output directory (no {marker.name}). Crawl overwrites its output; "
                         "use a fresh --out and keep reviewed scenarios/tests in scenarios/<app>/ or e2e/<app>/.")
    return marker


def load_fixtures(path: Path | None) -> tuple[dict[str, Any], str]:
    """inputs: {입력칸 이름: 값 | 체크박스 이름: true | 콤보박스 이름: 옵션 이름}, deny: 정규식 (생략 시 기본값)."""
    inputs, deny, _ = load_fixtures_full(path)
    return inputs, deny


def load_fixtures_full(path: Path | None) -> tuple[dict[str, Any], str, dict[str, list[str]]]:
    """… + pick: {목록 제목 | "*": [분기 열 이름…]} (목록 표본화의 분기 열을 사람이 정할 때)."""
    if path is None:
        return {}, DEFAULT_DENY, {}
    spec = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    pick = {str(k): [str(c) for c in (v or [])] for k, v in (spec.get("pick") or {}).items()}
    return dict(spec.get("inputs") or {}), spec.get("deny") or DEFAULT_DENY, pick
