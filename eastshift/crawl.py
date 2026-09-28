"""화면 탐색기: 시작 화면에서 할 수 있는 동작을 모두 눌러 보고 화면 상태 그래프를 만든 뒤, 그 그래프로 시나리오 YAML을 만든다.

- 동작 목록은 aria 스냅샷에서 뽑는다 (parse_elements). Jev도 텍스트 생성 LLM도 부르지 않는다.
- 화면 상태 = 스냅샷 구조 서명 (입력값, [checked] 같은 상태, 숫자를 뺀다) + "입력을 채운 뒤인지".
  동작 뒤 서명이 같고 대화상자도 없으면 화면 안 동작(local), 아니면 전이(transition).
- 동작마다 새 브라우저 컨텍스트에서 시작 URL부터 경로를 다시 재생한 뒤 실행한다 (뒤로가기에 의존하지 않는다).
- 한 상태의 동작들은 서로 독립이라 브라우저 여러 개(작업 스레드)가 나눠 누른다. 결과 반영(edge id, 새 상태 등록)은 한 줄로
  원래 순서대로 하므로 그래프 모양은 작업 수와 무관하다. 작업 수: EASTSHIFT_CRAWL_WORKERS (기본 4, --storage-state를 쓰면 1:
  컨텍스트들이 로그인 세션 하나를 나눠 쓰면 서버 세션 상태가 섞일 수 있다).
- 입력칸은 누르지 않고 픽스처 값으로만 채운다. 입력칸이 있는 상태에서는 전이 동작을 "그대로" / "채워서" 두 번 누른다.
  "채워서" = 픽스처 값 + 다른 입력칸 값을 바꾸는 화면 안 버튼(캘린더 날짜 등) 하나.
- (프레임, 역할, 범위 라벨, 숫자를 가린 이름)이 같은 요소가 group_min개 이상이면 첫 요소만 누른다 (캘린더 날짜, 페이지 번호).
- 목록성 화면(표의 행, 목록의 항목)은 같은 열의 요소를 한 템플릿으로 보고 행 몇 개만 대표로 누른다 (eastshift.lists): 분기 열(상태·유형 …)의 값 조합마다 하나,
  상한 --reps. 분기 열은 픽스처 pick > Jev 분류(캐시, margin 게이트) > 규칙 순. 같은 층의 대표들이 다른 화면으로 가면 그 층을 더 누른다 (적응 확장).
- deny 정규식에 걸리는 이름(삭제, 로그아웃 …)은 누르지 않고 기록만 한다. 다른 origin으로 나가는 동작은 따라가지 않는다.

산출물: graph.json, graph.md (mermaid + 표. 크면 주소 단위로 합치거나 뺀다), 화면 스크린샷, 시나리오 YAML과 재생 캐시 (첫 실행부터 Jev 호출 0).
탐색 중에는 --out 의 .crawl-partial.jsonl 에 상태마다 덧붙여, 끊겨도 같은 설정으로 다시 돌리면 이어 간다 (Crawler.run 의 checkpoint).
누른 결과는 cache/clicks.jsonl 에 기억해, 상한을 올려 다시 탐색하면 새 동작만 누른다 (Crawler.run 의 clicks, eastshift.clicks).
시나리오는 현재 동작의 기록이다. 그 동작이 맞는지는 사람이 검토한다 (as-is 골든 기록에 그대로 쓸 수 있다).
"""
from __future__ import annotations

import hashlib
import json
import os
import queue
import re
import threading
from concurrent.futures import Future
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Callable
from urllib.parse import urljoin, urlparse

import yaml
from playwright.sync_api import Error as PWError
from playwright.sync_api import Page, sync_playwright

from . import clicks as _clicks
from . import lists as _lists
from .pwtest.mutation import route_key
from .runner import UA, Runner, _expand
from .snapshot import EDITABLE_ROLES, LINE, Element, parse_elements

# 누르면 되돌릴 수 없거나 밖으로 나가는 동작. 테스트 DB에서도 메일·문자·결재·이체는 실제 사람과 외부 기관에 닿을 수 있다.
DEFAULT_DENY = (r"삭제|탈퇴|해지|로그아웃|결제|이체|송금|환불|전송|발송|메일|문자|결재|상신|초기화|다운로드|엑셀|인쇄"
                r"|(?i:log ?out|sign ?out|delete|remove|download|print|send|mail|sms|transfer|refund|reset)")
MERMAID_EDGES, MERMAID_TEXT = 500, 50000  # mermaid 기본 한도 (maxEdges, maxTextSize): 넘으면 그림 대신 오류가 나온다
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
    lists: list[dict[str, Any]] = field(default_factory=list)  # 이 화면의 목록 템플릿과 표본 (eastshift.lists.ListInfo.to_dict)
    root: int = 0               # 경로의 시작점: 0 = 시작 주소, 1… = 눌러서는 못 가 직접 연 화면 (씨앗, Crawler.roots)

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
    template: str = ""        # 목록 템플릿 대표일 때: '<목록 제목>:<열>' (eastshift.lists)
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


def _relative(url: str) -> str:
    """시나리오의 goto: 경로와 쿼리만 (to-be 비교 때 --base-url로 대상을 고른다)."""
    u = urlparse(url)
    return (u.path or "/") + (f"?{u.query}" if u.query else "")


def route_of(loc: str) -> str:
    """상태의 라우트: 페이지·프레임 경로마다 숫자·해시 조각을 {id}로 (Screen Map·결함 주입과 같은 규칙: 주문 4번과 9번 상세는 같은 라우트)."""
    return "|".join(route_key(part or "/") for part in loc.split("|"))


def load_seeds(routes_json: Path) -> list[str]:
    """eastshift routes 의 routes.json 에서 바로 열 수 있는 화면 주소 (종류 screen, 경로 매개변수 없음)."""
    data = json.loads(routes_json.read_text(encoding="utf-8"))
    return [r["path"] for r in data.get("routes", []) if r.get("kind", "screen") == "screen" and "{" not in r["path"]]


def maximal_paths(cands: list[list[int]]) -> list[list[int]]:
    """다른 경로의 앞부분(같은 것 포함)인 경로를 뺀다. 남긴 경로의 앞부분을 모두 집합에 넣어 두고 본다 (전이 수만 개여도 경로 수에 비례)."""
    kept: list[list[int]] = []
    prefixes: set[tuple[int, ...]] = set()
    for p in sorted(cands, key=len, reverse=True):
        if tuple(p) not in prefixes:
            kept.append(p)
            prefixes.update(tuple(p[:i]) for i in range(1, len(p) + 1))
    return sorted(kept)


def serial(i: int, total: int) -> str:
    """시나리오 번호. 자릿수를 전체 개수에 맞춰야 이름순이 번호순이다 (crawl_100 이 crawl_11 앞에 오지 않게). 99개까지는 두 자리 그대로."""
    return f"{i:0{max(2, len(str(total)))}d}"


def stable_path(url: str) -> str:
    """URL 경로에서 숫자가 든 첫 조각부터 뺀다 (/reservations/3 → /reservations/). 매 실행 바뀌는 id를 피한다."""
    out = []
    for seg in urlparse(url).path.split("/"):
        if DIGITS.search(seg):
            return "/".join(out) + "/"
        out.append(seg)
    return "/".join(out)


class _Session:
    """작업 스레드 하나의 브라우저와, 그 브라우저에서 지금 실행 중인 동작의 이벤트."""
    def __init__(self, browser: Any) -> None:
        self.browser = browser
        self.events: dict[str, list] = {"dialogs": [], "js_errors": [], "http_errors": []}
        self.dismiss_confirm = False

    def reset(self) -> None:
        self.events = {"dialogs": [], "js_errors": [], "http_errors": []}
        self.dismiss_confirm = False


class _Pool:
    """브라우저를 하나씩 가진 작업 스레드들. Playwright sync API는 스레드를 넘나들 수 없어서 스레드마다 따로 띄운다.
    map()은 작업을 나눠 돌리고 결과를 넣은 순서대로 돌려준다 (예외는 그 자리에서 다시 던진다)."""

    def __init__(self, n: int, launch: Callable[[Any], Any]) -> None:
        self.n, self.launch = max(1, n), launch
        self.q: queue.Queue = queue.Queue()
        self.threads: list[threading.Thread] = []
        self.errors: list[BaseException] = []

    def _loop(self, ready: threading.Event) -> None:
        try:
            with sync_playwright() as p:
                browser = self.launch(p)
                s = _Session(browser)
                ready.set()
                try:
                    while (job := self.q.get()) is not None:
                        fn, fut = job
                        if fut.set_running_or_notify_cancel():
                            try:
                                fut.set_result(fn(s))
                            except BaseException as e:
                                fut.set_exception(e)
                finally:
                    browser.close()
        except BaseException as e:
            self.errors.append(e)
            ready.set()

    def __enter__(self) -> "_Pool":
        for _ in range(self.n):
            ready = threading.Event()
            t = threading.Thread(target=self._loop, args=(ready,), daemon=True)
            t.start()
            ready.wait()
            self.threads.append(t)
            if self.errors:
                self.__exit__(None, None, None)
                raise self.errors[0]
        return self

    def __exit__(self, *exc: Any) -> None:
        if exc and exc[0] is not None:  # 멈춤(Ctrl+C, 통합 화면의 멈춤)·오류: 아직 시작하지 않은 누르기는 버리고, 누르는 중인 것만 끝내고 닫는다
            while True:
                try:
                    job = self.q.get_nowait()
                except queue.Empty:
                    break
                if job is not None:
                    job[1].cancel()
        for _ in self.threads:
            self.q.put(None)
        for t in self.threads:
            t.join(timeout=30)

    def map(self, jobs: list[Callable[[_Session], Any]]) -> list[Any]:
        futs: list[Future] = []
        for fn in jobs:
            fut: Future = Future()
            self.q.put((fn, fut))
            futs.append(fut)
        return [f.result() for f in futs]


def _workers(storage_state: Path | None) -> int:
    v = os.environ.get("EASTSHIFT_CRAWL_WORKERS", "").strip()
    if v:
        return max(1, int(v))
    return 1 if storage_state else 4


class Crawler:
    def __init__(self, start: str, *, base_url: str | None = None, inputs: dict[str, Any] | None = None, deny: str = DEFAULT_DENY,
                 max_depth: int = 3, max_states: int = 30, max_actions: int = 40, group_min: int = 3, settle_ms: int = 400,
                 action_timeout_ms: int = 3000, storage_state: Path | None = None, headed: bool = False,
                 reps: int = 3, pick: dict[str, list[str]] | None = None, jev: Any | None = None, list_cache: Path | None = None, min_margin: float = 0.2,
                 workers: int | None = None, max_route_states: int = 0, seeds: list[str] | None = None):
        """max_route_states: 한 라우트(route_of)가 가질 상태 수 상한 (0 = 없음). 탐색이 한 화면의 변형(필터·입력 조합)에 빠져 다른 화면에 못 가는 것을 막는다.
        seeds: 눌러서 못 간 화면을 직접 열어 볼 주소 (load_seeds). 시작점에서 더 갈 곳이 없을 때 아직 못 간 라우트만 하나씩 연다."""
        self.start_url = start if urlparse(start).scheme else urljoin((base_url or "").rstrip("/") + "/", start.lstrip("/"))
        # 시나리오·테스트의 goto는 항상 상대 경로: 절대 주소를 박아 두면 to-be 비교가 조용히 as-is를 치게 된다. 실행 때 --base-url로 as-is/to-be를 고른다
        u = urlparse(self.start_url)
        self.start = (u.path or "/") + (f"?{u.query}" if u.query else "")
        self.origin = f"{u.scheme}://{u.netloc}"
        if not urlparse(self.start_url).scheme:
            raise ValueError(f"relative start {start!r} needs --base-url or EASTSHIFT_BASE_URL")
        self.inputs = inputs or {}
        self.deny = re.compile(deny)
        self.max_depth, self.max_states, self.max_actions, self.group_min = max_depth, max_states, max_actions, group_min
        self.max_route_states = max_route_states
        full = [urljoin(self.origin + "/", x.lstrip("/")) if not urlparse(x).scheme else x for x in (seeds or [])]
        self.seeds = [x for x in dict.fromkeys(full) if urlparse(x).netloc == u.netloc]  # 다른 origin은 따라가지 않는다
        self.roots = [self.start_url]  # 경로가 시작하는 주소들 (Node.root). 씨앗은 연 순서대로 뒤에 붙는다
        self._seed_i = 0
        self._route_count: dict[str, int] = {}
        self._reached: set[str] = set()  # 도달한 상태들의 페이지·프레임 라우트 (씨앗이 이미 닿은 화면인지)
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
        self.workers = workers or _workers(storage_state)
        self.pool: _Pool | None = None
        self.shots_dir: Path | None = None
        self.sources: dict[str, str] = {}
        self.memo: _clicks.ClickMemo | None = None

    # -- 브라우저 ------------------------------------------------------------------------
    def _launch(self, p: Any) -> Any:
        return p.chromium.launch(headless=not self.headed, args=["--disable-blink-features=AutomationControlled"])

    def _hook(self, s: _Session, pg: Page) -> None:
        def on_dialog(d) -> None:
            s.events["dialogs"].append({"type": d.type, "message": d.message})
            try:
                if d.type == "confirm" and s.dismiss_confirm:
                    d.dismiss()
                    return
                d.accept()
            except PWError:
                pass

        def on_response(r) -> None:
            try:
                if r.request.is_navigation_request() and r.status >= 400:
                    s.events["http_errors"].append(f"{r.status} {r.url}")
            except PWError:
                pass
        pg.on("dialog", on_dialog)
        pg.on("pageerror", lambda err: s.events["js_errors"].append(str(err).splitlines()[0]))
        pg.on("response", on_response)

    def _open(self, s: _Session, path: list[int], root: int = 0) -> Page:
        """새 컨텍스트에서 시작점(self.roots[root])을 열고 경로를 재생한다. 작업 스레드에서 돈다: self.edges는 읽기만 한다."""
        kwargs: dict[str, Any] = {"viewport": {"width": 1280, "height": 900}, "locale": "ko-KR", "user_agent": UA}
        if self.storage_state and self.storage_state.exists():
            kwargs["storage_state"] = str(self.storage_state)
        ctx = s.browser.new_context(**kwargs)
        ctx.on("page", lambda pg: self._hook(s, pg))
        page = ctx.new_page()
        page.goto(self.roots[root], wait_until="load")
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
            except PWError:
                continue
        if modal:  # 모달이 열려 있으면 배경(inert) 요소도 스냅샷에 남는다. 모달 안의 요소만 동작 대상으로.
            inside = []
            for el in elements:
                try:
                    if self.rt._locator(page, el).first.evaluate("e => !!e.closest('dialog[open], [aria-modal=true]')"):
                        inside.append(el)
                except PWError:
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
            except PWError:
                continue
        return out

    @staticmethod
    def _close(page: Page | None) -> None:
        if page is not None:
            try:
                page.context.close()
            except PWError:
                pass

    # -- 탐색 ----------------------------------------------------------------------------
    # 누르기(_attempt, _look)는 작업 스레드에서, 반영(_commit, _node_for)은 메인 스레드에서 원래 순서대로 한다.
    # 한 묶음(map)이 도는 동안 메인 스레드는 기다리기만 하므로 작업 스레드가 읽는 self.edges·self._by_key는 바뀌지 않는다.
    def _shot(self, page: Page) -> bytes | None:
        if not self.shots_dir:
            return None
        try:
            return page.screenshot()
        except PWError:
            return None

    def _look(self, s: _Session, path: list[int], shot: bool = False, root: int = 0) -> tuple[dict[str, Any], bytes | None]:
        """경로를 재생한 화면의 관찰 (+ 스크린샷)."""
        page = None
        try:
            page = self._open(s, path, root)
            return self._observe(page), (self._shot(page) if shot else None)
        finally:
            self._close(page)

    def _has_room(self, loc: str, planned: int = 0, planned_routes: dict[str, int] | None = None) -> bool:
        """새 상태를 더 만들 수 있는가: 전체 상한과 그 라우트의 상한. planned*: 아직 만들지 않았지만 만들기로 한 것 (채워서 경로의 미리 계산)."""
        if len(self.nodes) + planned >= self.max_states:
            return False
        if self.max_route_states:
            r = route_of(loc)
            return self._route_count.get(r, 0) + (planned_routes or {}).get(r, 0) < self.max_route_states
        return True

    def _budget_reason(self, loc: str) -> str:
        if len(self.nodes) >= self.max_states:
            return f"state budget {self.max_states} reached"
        return f"route state budget {self.max_route_states} reached ({route_of(loc)})"

    def _count(self, node: Node) -> None:
        self._route_count[route_of(node.loc)] = self._route_count.get(route_of(node.loc), 0) + 1
        self._reached.update(route_key(part or "/") for part in node.loc.split("|"))

    def _look_seed(self, s: _Session, root: int) -> tuple[dict[str, Any], bytes | None, list[str]]:
        """씨앗 주소를 바로 연 관찰 + 그 이동의 HTTP 오류."""
        s.reset()
        obs, shot = self._look(s, [], shot=True, root=root)
        return obs, shot, list(s.events["http_errors"])

    def _node_for(self, obs: dict[str, Any], filled: bool, path: list[int], shot: bytes | None, root: int = 0) -> int | None:
        key = (obs["sig"], filled)
        if key in self._by_key:
            return self._by_key[key]
        if not self._has_room(obs["loc"]):
            return None
        node = Node(len(self.nodes), obs["sig"], filled, obs["url"], obs["loc"], obs["title"], path,
                    headings=obs["headings"], alerts=obs["alerts"], modal=obs["modal"], texts=obs["texts"], snapshot=obs["snapshot"], root=root)
        if self.shots_dir and shot:
            node.screenshot = str(self.shots_dir / f"n{node.id}.png")
            Path(node.screenshot).write_bytes(shot)
        self.nodes.append(node)
        self._by_key[key] = node.id
        self._count(node)
        print(f"  + n{node.id} {node.label}  [{urlparse(node.url).path}]", flush=True)
        return node.id

    def _attempt(self, s: _Session, node: Node, steps: list[Step], mode: str, *, preset_len: int = 0,
                 preset_sets: dict[str, str] | None = None, dismiss: bool = False) -> dict[str, Any]:
        """경로를 재생하고 steps를 실행한 결과 (edge id·도착 상태는 아직 없다: _commit이 정한다)."""
        r: dict[str, Any] = {"steps": steps, "mode": mode, "kind": "error", "preset_len": preset_len, "preset_sets": dict(preset_sets or {}),
                             "reason": "", "url_after": "", "dialogs": [], "js_errors": [], "http_errors": [], "sets": {}, "obs": None,
                             "filled": False, "shot": None}
        page = None
        try:
            page = self._open(s, node.path, node.root)
            before = self._values(page)
            s.reset()
            s.dismiss_confirm = dismiss
            if preset_len:  # 채우기 스텝을 다 실행한 뒤의 실제 값을 기대값으로 (버튼 하나만 눌렀을 때 값과 다를 수 있다: 계산 버튼)
                page = self._run(page, steps[:preset_len])
                if r["preset_sets"]:
                    now = self._values(page)
                    r["preset_sets"] = {k: now.get(k, v) for k, v in r["preset_sets"].items()}
            page = self._run(page, steps[preset_len:])
            obs = self._observe(page)
        except Exception as e:
            r["reason"] = str(e).splitlines()[0][:200]
            self._close(page)
            return r
        r["url_after"] = page.url
        r["dialogs"], r["js_errors"], r["http_errors"] = (list(s.events[k]) for k in ("dialogs", "js_errors", "http_errors"))
        if urlparse(page.url).netloc != urlparse(self.start_url).netloc:
            r["kind"] = "external"
        elif obs["sig"] == node.sig and not r["dialogs"]:
            r["kind"] = "local"
            if steps[-1].el.role not in TOGGLE_ROLES:  # 체크박스·옵션은 자기 값만 바꾼다
                r["sets"] = {k: v for k, v in self._values(page).items() if before.get(k) != v and k != steps[-1].el.name}
        else:
            r["kind"] = "transition"
            r["filled"] = (mode == "filled" or node.filled) and obs["loc"] == node.loc
            r["obs"] = {k: obs[k] for k in ("sig", "url", "loc", "title", "headings", "alerts", "modal", "texts", "snapshot")}
            if (obs["sig"], r["filled"]) not in self._by_key:  # 새 상태일 수 있을 때만 찍는다
                r["shot"] = self._shot(page)
        self._close(page)
        return r

    def _commit(self, node: Node, r: dict[str, Any], group: int = 1) -> Edge:
        """_attempt 결과를 edge로. edge id는 호출자가 곧바로 추가한다는 전제."""
        edge = Edge(len(self.edges), node.id, r["steps"], r["mode"], r["kind"], group=group, preset_len=r["preset_len"],
                    preset_sets=r["preset_sets"], sets=r["sets"], url_after=r["url_after"], dialogs=r["dialogs"],
                    js_errors=r["js_errors"], http_errors=r["http_errors"], reason=r["reason"])
        if edge.kind == "transition":
            edge.dst = self._node_for(r["obs"], r["filled"], node.path + [edge.id], r["shot"], node.root)
            if edge.dst is None:
                edge.reason = self._budget_reason(r["obs"]["loc"])
        return edge

    def _attempts(self, jobs: list[tuple[Node, list[Step], str, dict[str, Any]]]) -> list[dict[str, Any]]:
        """(상태, 스텝, 방식, 옵션) 여러 개를 작업 스레드들에 나눠 누른다. 결과는 넣은 순서대로.
        기억(self.memo)에 있는 동작은 누르지 않고 기억한 결과를 쓴다. 기억 확인과 적기는 메인 스레드에서 한다."""
        assert self.pool is not None
        memo = self.memo
        keys = [self._attempt_key(*j) for j in jobs] if memo else []
        prior = [memo.get(k) for k in keys] if memo else [None] * len(jobs)
        # 오류(시간 초과 등)는 한 번 더 눌러 보고, 두 번 연속이면 그대로 쓴다: 일시적인 느림은 다시 누르고, 늘 가려진 버튼에 매번 제한 시간을 쓰지 않게
        tries = [r.pop("tries", 1) if r is not None and r["kind"] == "error" else 0 for r in prior]
        got: list[dict[str, Any] | None] = [None if 0 < t < 2 else self._recalled(r) for r, t in zip(prior, tries)]
        miss = [i for i, r in enumerate(got) if r is None]
        fresh = self.pool.map([lambda s, j=jobs[i]: self._attempt(s, j[0], j[1], j[2], **j[3]) for i in miss])
        for i, r in zip(miss, fresh):
            got[i] = r
            if memo:
                memo.put(keys[i], {**{k: v for k, v in r.items() if k != "steps"}, **({"tries": tries[i] + 1} if r["kind"] == "error" else {})})
        if memo:
            memo.hits += len(jobs) - len(miss)
            memo.misses += len(miss)
        out = []
        for j, r in zip(jobs, got):
            assert r is not None
            r["steps"] = j[1]  # 목록 적응 확장은 누른 요소를 객체 동일성(is)으로 찾는다: 기억의 사본이 아니라 이번 탐색의 요소를 둔다
            out.append(r)
        return out

    def _step_key(self, s: Step) -> dict[str, Any]:
        d: dict[str, Any] = {"action": s.action, **asdict(s.el)}
        if s.value is not None:
            d["value"] = _expand(s.value)  # ${VAR}는 이번에 채울 값으로
        return d

    def _replay_key(self, path: list[int]) -> list[list[dict[str, Any]]]:
        """경로를 edge id가 아니라 스텝 내용으로 (id는 탐색마다 달라진다)."""
        return [[self._step_key(s) for s in self.edges[eid].steps] for eid in path]

    def _attempt_key(self, node: Node, steps: list[Step], mode: str, opts: dict[str, Any]) -> list[Any]:
        return ["attempt", self.roots[node.root], node.sig, node.filled, node.loc, self._replay_key(node.path), [self._step_key(s) for s in steps], mode,
                opts.get("preset_len", 0), sorted((opts.get("preset_sets") or {}).items()), bool(opts.get("dismiss"))]

    def _recalled(self, r: dict[str, Any] | None) -> dict[str, Any] | None:
        """기억한 결과를 이번 탐색에 맞춘다. 캡처는 새 상태가 될 때만 (_attempt와 같다). 새 상태가 될 결과인데 캡처가 없으면 None (다시 누른다)."""
        if r is None:
            return None
        new = r["kind"] == "transition" and (r["obs"]["sig"], r["filled"]) not in self._by_key
        if not (new and self.shots_dir):
            r["shot"] = None
            return r
        r["shot"] = self.memo.shot(r["shot"]) if self.memo and r.get("shot") else None
        return r if r["shot"] is not None else None

    def _look_at(self, node: Node) -> dict[str, Any]:
        """상태를 탐색하기 전 그 화면의 관찰. 기억에 있으면 브라우저를 열지 않는다."""
        assert self.pool is not None
        key = ["look", self.roots[node.root], self._replay_key(node.path)]
        if self.memo and (hit := self.memo.get(key)) is not None:
            self.memo.hits += 1
            return _clicks.decode_obs(hit)
        obs, _ = self.pool.map([lambda s: self._look(s, node.path, root=node.root)])[0]
        if self.memo:
            self.memo.misses += 1
            self.memo.put(key, _clicks.encode_obs(obs))  # 목록 표본화가 obs의 목록을 고치기 전에 적는다
        return obs

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
        assert self.pool is not None
        try:
            obs = self._look_at(node)
        except Exception as e:
            print(f"  ! n{node.id} replay failed: {str(e).splitlines()[0][:160]}")
            return
        cands, denied = self._candidates(obs["elements"], obs)
        node.lists = [info.to_dict() for info in obs.get("lists", []) if info.rows]
        for el in denied:
            self.edges.append(Edge(len(self.edges), node.id, [Step(el, "click")], "as-is", "denied", reason="deny pattern"))
        sampled = [(info, sum(len(v) for v in info.reps.values())) for info in obs.get("lists", []) if info.reps]
        print(f"n{node.id} {node.label}: {len(cands)} actions" + (f", {len(denied)} denied" if denied else "")
              + "".join(f" · 목록 '{i.heading or i.kind}' {len(i.rows)}행 중 대표 {n} ({i.source}{'' if not i.branch_cols else ': ' + ', '.join(i.headers[c] for c in i.branch_cols)})" for i, n in sampled), flush=True)
        as_is = []
        raws = self._attempts([(node, [Step(el, "click")], "as-is", {}) for el, _, _ in cands])
        for (el, n, meta), r in zip(cands, raws):
            e = self._commit(node, r, group=n)
            if meta:
                e.template, e.row, e.stratum = meta["template"], meta["row"], meta["stratum"]
            self.edges.append(e)
            as_is.append(e)
        as_is += self._expand_strata(node, obs, as_is)
        self._dismissed(node, list(as_is))
        if node.filled:
            return
        preset, sets, node.missing_inputs = self._preset(obs["elements"], [e for e in as_is if e.kind == "local"])
        if not preset:
            return
        in_preset = {s.el.key for s in preset}
        base = [e for e in as_is if e.action.el.role not in TOGGLE_ROLES and e.action.el.key not in in_preset]
        raws = self._attempts([(node, preset + [e.action], "filled", {"preset_len": len(preset), "preset_sets": sets}) for e in base])
        # "그대로"와 결과가 같으면 버린다. 도착 상태는 반영 전에 미리 계산한다 (새 상태는 기존 상태와 같을 수 없고, 상태 예산만 앞에서부터 센다):
        # 남길 것을 먼저 알아야 그 취소 경로를 한 묶음으로 누르고, 반영은 원래 순서(채워서 → 그 취소 → 다음 채워서)로 할 수 있다.
        kept: list[tuple[Edge, dict[str, Any]]] = []
        planned: dict[tuple[str, bool], object] = {}
        planned_routes: dict[str, int] = {}
        for e, r in zip(base, raws):
            dst: object = None
            if r["kind"] == "transition":
                key = (r["obs"]["sig"], r["filled"])
                if key in self._by_key:
                    dst = self._by_key[key]
                elif key in planned:
                    dst = planned[key]
                elif self._has_room(r["obs"]["loc"], len(planned), planned_routes):
                    dst = ("new", key)
            if (r["kind"], dst, [d["message"] for d in r["dialogs"]]) == (e.kind, e.dst, [d["message"] for d in e.dialogs]):
                continue
            if isinstance(dst, tuple) and r["obs"] and (r["obs"]["sig"], r["filled"]) not in planned:
                planned[(r["obs"]["sig"], r["filled"])] = dst
                planned_routes[route_of(r["obs"]["loc"])] = planned_routes.get(route_of(r["obs"]["loc"]), 0) + 1
            kept.append((e, r))
        for (e, r), d in zip(kept, self._dismiss_attempts(node, [(r, e.group) for e, r in kept])):
            f = self._commit(node, r, group=e.group)
            self.edges.append(f)
            if d is not None:
                self.edges.append(self._commit_dismissed(node, d, e.group))

    def _expand_strata(self, node: Node, obs: dict[str, Any], as_is: list[Edge]) -> list[Edge]:
        """적응 확장: 같은 목록·같은 열·같은 층의 대표들이 서로 다른 화면으로 갔으면 분기 열이 틀린 것이다. 그 층의 다른 행을 둘 더 누른다."""
        lists_: list[_lists.ListInfo] = obs.get("lists", [])
        extra: list[Edge] = []
        by_key: dict[tuple[str, tuple], list[Edge]] = {}
        plans: list[tuple[str, _lists.ListInfo, str, set[int], int, list[tuple[int, Element]]]] = []
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
            rows = [r for r in _lists.more_rows(info, e0.row, exclude=tried, limit=2)
                    if (el := self._tpl[tkey].get(r)) is not None and not self.deny.search(el.name)]
            plans.append((template, info, role, tried, e0.group, [(r, self._tpl[tkey][r]) for r in rows]))
        jobs = [(node, [Step(el, "click")], "as-is", {}) for *_, rows in plans for _, el in rows]
        raws = iter(self._attempts(jobs))
        for template, info, role, tried, group, rows in plans:  # 반영은 층 순서, 행 순서대로 (한 줄로 누를 때와 같은 edge id)
            for r, _ in rows:
                e = self._commit(node, next(raws), group=group)
                e.template, e.row, e.stratum = template, r, _lists.stratum(info, r)
                e.reason = (e.reason + "; " if e.reason else "") + "적응 확장: 같은 층의 대표들이 다른 화면으로 감"
                self.edges.append(e)
                extra.append(e)
            rk = f"{template}:{role}"
            info.reps[rk] = sorted(set(info.reps.get(rk, [])) | tried | {e.row for e in extra if e.template == template})
            node.lists = [i.to_dict() for i in lists_ if i.rows]
        return extra

    def _dismissed(self, node: Node, edges: list[Edge]) -> None:
        """confirm이 뜬 동작은 '취소'한 경로도 한 번 누른다 (취소하면 입력이 남는지, 저장이 안 되는지)."""
        items = [({"kind": e.kind, "dialogs": e.dialogs, "steps": e.steps, "preset_len": e.preset_len, "preset_sets": e.preset_sets}, e.group) for e in edges]
        for (_, group), d in zip(items, self._dismiss_attempts(node, items)):
            if d is not None:
                self.edges.append(self._commit_dismissed(node, d, group))

    def _dismiss_attempts(self, node: Node, items: list[tuple[dict[str, Any], int]]) -> list[dict[str, Any] | None]:
        """(결과, group)마다 취소 경로를 누른 결과. confirm이 없던 것은 None."""
        need = [i for i, (r, _) in enumerate(items) if r["kind"] == "transition" and any(d["type"] == "confirm" for d in r["dialogs"])]
        raws = self._attempts([(node, items[i][0]["steps"], "dismiss",
                                {"preset_len": items[i][0]["preset_len"], "preset_sets": items[i][0]["preset_sets"], "dismiss": True}) for i in need])
        out: list[dict[str, Any] | None] = [None] * len(items)
        for i, r in zip(need, raws):
            out[i] = r
        return out

    def _commit_dismissed(self, node: Node, r: dict[str, Any], group: int) -> Edge:
        d = self._commit(node, r, group=group)
        if d.kind == "local":  # 취소하면 화면이 그대로인 것이 정상: 대화상자는 떴으므로 자기 자신으로 가는 전이로 기록
            d.kind, d.dst = "transition", node.id
        return d

    def run(self, shots_dir: Path | None = None, checkpoint: Path | None = None, clicks: Path | None = None,
            sources: dict[str, str] | None = None) -> None:
        """checkpoint: 상태 하나를 다 누를 때마다 바뀐 것(새 상태·새 동작)을 이 파일에 한 줄씩 덧붙인다. 같은 설정으로 다시 돌리면
        거기서 이어 간다 (상태 수천 개짜리 탐색이 중간에 끊겨도 처음부터 다시 누르지 않게). 다 끝나면 지운다.
        clicks: 누른 결과의 기억 파일 (eastshift.clicks). 탐색이 끝나도 남아 다음 탐색이 새 동작만 누른다.
        sources: 그 기억의 기준 = 소스 폴더 → 버전 (clicks.sources_for). 버전이 바뀌면 기억을 비운다."""
        self.shots_dir = shots_dir
        self.sources = sources or {}
        if shots_dir:
            shots_dir.mkdir(parents=True, exist_ok=True)
        i = self._resume(checkpoint) if checkpoint else 0
        log = None
        if clicks:
            self.memo = _clicks.ClickMemo(clicks, {"v": _clicks.VERSION, "sources": self.sources, "settle_ms": self.rt.settle_ms,
                                                             "action_timeout_ms": self.action_timeout_ms, "storage_state": str(self.storage_state or "")})
            print(f"  누른 결과 기억 {clicks}: " + (f"비우고 시작 ({self.memo.reason})" if self.memo.reason else f"{len(self.memo.index)}개")
                  + ("" if self.sources else " — 소스 위치를 몰라(eastshift.json) 코드가 바뀌어도 알 수 없다. 앱이 바뀌었으면 이 파일을 지운다"), flush=True)
        with _Pool(self.workers, self._launch) as self.pool:
            try:
                fresh = not self.nodes
                if fresh or self.memo:  # 시작 화면은 늘 실제로 연다: 기억이 있으면 앱이 그대로인지 여기서도 본다
                    obs, shot = self.pool.map([lambda s: self._look(s, [], shot=fresh)])[0]
                    if self.memo:
                        self._check_root(obs["sig"])
                    if fresh:
                        self._node_for(obs, False, [], shot)
                if checkpoint:
                    log = checkpoint.open("a", encoding="utf-8")
                    if fresh:
                        self._checkpoint(log, 0, 0, 0)
                while True:
                    while i < len(self.nodes):
                        node = self.nodes[i]
                        i += 1
                        n0, e0 = len(self.nodes), len(self.edges)
                        if len(node.path) < self.max_depth:
                            self._explore(node)
                            node.explored = True
                        if log:
                            self._checkpoint(log, i, n0, e0, node)
                        self._progress(i)
                    if not self._next_seed():  # 시작점에서 더 갈 곳이 없다: 아직 못 간 선언 화면을 하나 직접 열어 거기서 다시
                        break
            finally:
                self.pool = None
                if log:
                    log.close()
                if self.memo:
                    self.memo.close()
                    print(f"  누른 결과 기억: 새로 누름 {self.memo.misses}회, 기억에서 {self.memo.hits}회", flush=True)
        if checkpoint:
            checkpoint.unlink(missing_ok=True)

    def _progress(self, done: int) -> None:
        """한 줄 진행 상황 (통합 화면이 작업 로그 대신 이 줄을 보여 준다)."""
        memo = f" · 새로 누름 {self.memo.misses} · 기억 {self.memo.hits}" if self.memo else ""
        seeds = f" · 씨앗 {self._seed_i}/{len(self.seeds)}" if self.seeds else ""
        print(f"[진행] 상태 {done}/{len(self.nodes)} 탐색 · 라우트 {len(self._route_count)} · 동작 {len(self.edges)}{memo}{seeds}", flush=True)

    def _next_seed(self) -> bool:
        """눌러서 못 간 선언 화면(씨앗)을 하나 직접 열어 새 시작점으로 둔다. 더 열 것이 없으면 False.
        이미 도달한 라우트, 열면 다른 origin으로 가거나 이미 있는 상태(로그인 화면으로 돌려보냄 등)가 되는 주소는 건너뛴다."""
        assert self.pool is not None
        while self._seed_i < len(self.seeds):
            url = self.seeds[self._seed_i]
            self._seed_i += 1
            if route_key(urlparse(url).path or "/") in self._reached:
                continue
            root = len(self.roots)
            self.roots.append(url)
            try:
                obs, shot, http = self.pool.map([lambda s: self._look_seed(s, root)])[0]
            except Exception as e:  # noqa: BLE001
                print(f"  ! 씨앗 {url} 열기 실패: {str(e).splitlines()[0][:160]}", flush=True)
                self.roots.pop()
                continue
            if http:  # 선언은 있는데 바로 열면 404·500: 상태로 두지 않고 알리기만 한다
                print(f"  씨앗 {url}: {http[0]}", flush=True)
                self.roots.pop()
                continue
            nid = None if urlparse(obs["url"]).netloc != urlparse(url).netloc else self._node_for(obs, False, [], shot, root)
            if nid is None or self.nodes[nid].root != root:
                print(f"  씨앗 {url}: 새 화면 아님 (" + ("다른 곳으로 감" if nid is None else f"n{nid}와 같음") + ")", flush=True)
                self.roots.pop()
                continue
            print(f"  씨앗 {url} → n{nid} (눌러서는 못 간 화면을 직접 엶)", flush=True)
            return True
        return False

    def _check_root(self, sig: str) -> None:
        assert self.memo is not None
        key = ["root", self.start_url]
        hit = self.memo.get(key)
        if hit is not None and hit["sig"] != sig:
            self.memo.clear("시작 화면 구조가 지난번과 다름")
            print("  누른 결과 기억: 시작 화면 구조가 지난번과 달라 비웠다", flush=True)
            hit = None
        if hit is None:
            self.memo.put(key, {"sig": sig})

    # -- 이어 하기 ------------------------------------------------------------------------
    def _resume_key(self) -> str:
        """탐색 결과를 바꾸는 설정. 이것이 같아야 지난 기록에서 이어 간다."""
        blob = [self.start_url, self.inputs, self.deny.pattern, self.max_depth, self.max_states, self.max_actions, self.group_min,
                self.reps, self.pick, self.min_margin, self.rt.settle_ms, self.sources, self.max_route_states, self.seeds]
        return hashlib.sha256(json.dumps(blob, ensure_ascii=False, sort_keys=True, default=str).encode()).hexdigest()[:16]

    def _checkpoint(self, log: Any, nxt: int, n0: int, e0: int, node: Node | None = None) -> None:
        """한 줄 = 다음에 누를 상태 번호 + 새로 생긴 상태·동작 + 방금 누른 상태(목록 표본·explored가 바뀐다) + 시작점·씨앗 진행."""
        upd = ([node] if node is not None and node.id < n0 else []) + self.nodes[n0:]
        log.write(json.dumps({"next": nxt, "nodes": [asdict(n) for n in upd], "edges": [asdict(e) for e in self.edges[e0:]],
                              "roots": self.roots, "seed": self._seed_i}, ensure_ascii=False) + "\n")
        log.flush()

    def _resume(self, checkpoint: Path) -> int:
        """지난 기록을 읽어 상태·동작을 되살리고 다음에 누를 상태 번호를 준다. 설정이 다르거나 기록이 없으면 새로 시작한다 (0)."""
        lines = checkpoint.read_text(encoding="utf-8").splitlines() if checkpoint.exists() else []
        try:
            same = bool(lines) and json.loads(lines[0]).get("key") == self._resume_key()
        except ValueError:
            same = False
        if not same:
            checkpoint.write_text(json.dumps({"key": self._resume_key()}) + "\n", encoding="utf-8")
            return 0
        nodes: dict[int, Node] = {}
        edges: list[Edge] = []
        nxt, good = 0, lines[:1]
        for line in lines[1:]:
            try:
                rec = json.loads(line)
            except ValueError:  # 쓰다 끊긴 마지막 줄
                break
            good.append(line)
            for d in rec["nodes"]:
                nodes[d["id"]] = Node(**d)
            edges += [Edge(**{**d, "steps": [Step(Element(**st["el"]), st["action"], st["value"]) for st in d["steps"]]}) for d in rec["edges"]]
            nxt = rec["next"]
            self.roots, self._seed_i = rec.get("roots", self.roots), rec.get("seed", 0)
        checkpoint.write_text("".join(x + "\n" for x in good), encoding="utf-8")  # 끊긴 줄을 걷어 내야 다음 줄을 덧붙일 수 있다
        self.nodes = [nodes[k] for k in sorted(nodes)]
        self.edges = edges
        self._by_key = {(n.sig, n.filled): n.id for n in self.nodes}
        for n in self.nodes:
            self._count(n)
        if self.nodes:
            print(f"  이어 하기: 상태 {len(self.nodes)}개 중 {nxt}개 탐색됨, 동작 {len(self.edges)}개 ({checkpoint})", flush=True)
        return nxt

    # -- 산출물 --------------------------------------------------------------------------
    def to_dict(self) -> dict[str, Any]:
        return {"start": self.start, "start_url": self.start_url, "inputs": sorted(self.inputs), "deny": self.deny.pattern,
                "roots": [_relative(u) for u in self.roots], "max_route_states": self.max_route_states,
                "nodes": [asdict(n) | {"label": n.label} for n in self.nodes],
                "edges": [asdict(e) | {"steps": [s.to_dict() for s in e.steps], "sentence": sentence(e.action)} for e in self.edges]}

    def paths(self) -> list[list[int]]:
        """모든 전이 edge를 한 번 이상 지나는 경로. 다른 경로의 앞부분인 경로는 뺀다."""
        return maximal_paths([self.nodes[e.src].path + [e.id] for e in self.edges if e.kind == "transition" and e.dst is not None])

    def scenario_paths(self) -> list[tuple[int, list[int]]]:
        """시나리오가 될 (시작점, 경로): 전이 경로들, 그다음 씨앗으로 연 화면 중 더 갈 곳이 없는 것 (그 화면이 열리는지만 본다).
        씨앗이 없으면 paths()와 같은 순서라 테스트 이름(test_crawl_NN)이 전과 같다."""
        out = [(self.nodes[self.edges[p[0]].src].root, p) for p in self.paths()]
        covered = {r for r, _ in out}
        return out + [(k, []) for k in range(1, len(self.roots)) if k not in covered]

    def scenario(self, path: list[int], root: int = 0) -> tuple[dict[str, Any], dict[str, Any]]:
        """(시나리오, 재생 캐시). 캐시 키 규칙은 Runner.run과 같다 (같은 문장이 반복되면 ' #n')."""
        start = self.nodes[self.edges[path[0]].src] if path else next(n for n in self.nodes if n.root == root and not n.path)
        steps: list[dict[str, Any]] = [{"goto": _relative(self.roots[start.root])}]
        if start.title:
            steps.append({"expect": {"title_contains": start.title}})
        cache: dict[str, Any] = {}
        seen: dict[str, int] = {}
        labels = [start.label]
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
        with _Pool(1, self._launch) as pool:
            obs, _ = pool.map([lambda s: self._look(s, [])])[0]
        cands, denied = self._candidates(obs["elements"], obs)
        fills = [(el.name, self.inputs[el.name]) for el in obs["elements"] if el.role in EDITABLE_ROLES and el.name in self.inputs]
        missing = [el.name for el in obs["elements"] if el.role in EDITABLE_ROLES and el.name not in self.inputs]
        return {"url": obs["url"], "title": obs["title"],
                "click": [(sentence(Step(el, "click")) + (f" [목록 {m['template']} {m['row'] + 1}행]" if m else ""), n) for el, n, m in cands],
                "deny": [sentence(Step(el, "click")) for el in denied], "fill": fills, "missing": missing}

    def pytest_module(self) -> str:
        """같은 경로를 Playwright 테스트(ui fixture)로. 승인·결함 주입·검증 보고서 흐름에 그대로 올라간다."""
        L = ['"""eastshift crawl이 만든 테스트. 현재 동작의 기록이다. 검토하고 업무상 중요한 값(금액 등) 확인을 더한 뒤 e2e/<app>/로 옮겨 쓴다.',
             "", f"시작: {self.start}", '"""', "from eastshift.runner import _expand", ""]
        paths = self.scenario_paths()
        for i, (root, path) in enumerate(paths, 1):
            spec, cache = self.scenario(path, root)
            L += ["", f"def test_crawl_{serial(i, len(paths))}(ui):", f'    """{spec["name"].removeprefix("[탐색] ")}"""']
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
        mark_output_dir(out_dir)
        cache_dir.mkdir(parents=True, exist_ok=True)
        if self.list_cache:  # 분기 열 분류 캐시: 표식이 생긴 뒤에 쓴다 (먼저 쓰면 out 이 '탐색 산출물 폴더가 아닌 것'으로 보인다)
            _lists.save_cache(self.list_cache_path or cache_dir / "lists.json", self.list_cache)
        (out_dir / "graph.json").write_text(json.dumps(self.to_dict(), ensure_ascii=False, indent=1), encoding="utf-8")
        (out_dir / "graph.md").write_text(self.markdown(), encoding="utf-8")
        for old in out_dir.glob("crawl_*.yaml"):  # 이전 탐색이 만든 시나리오 (이 도구의 산출물)
            old.unlink()
            (cache_dir / f"{old.stem}.json").unlink(missing_ok=True)
        written = []
        paths = self.scenario_paths()
        for i, (root, path) in enumerate(paths, 1):
            spec, cache = self.scenario(path, root)
            f = out_dir / f"crawl_{serial(i, len(paths))}.yaml"
            header = ("# eastshift crawl이 만든 시나리오. 현재 동작의 기록이므로 맞는 동작인지 검토한 뒤 쓴다.\n"
                      "# 이 폴더는 crawl을 다시 돌리면 덮어쓴다. 검토한 파일은 scenarios/<app>/로 옮겨서 고친다.\n")
            f.write_text(header + yaml.safe_dump(spec, allow_unicode=True, sort_keys=False, width=200), encoding="utf-8")
            (cache_dir / f"{f.stem}.json").write_text(json.dumps(cache, ensure_ascii=False, indent=1), encoding="utf-8")
            written.append(f)
        (out_dir / "test_crawl.py").write_text(self.pytest_module(), encoding="utf-8")
        return written

    def _diagram(self, q: Callable[[str], str]) -> list[str]:
        """mermaid 흐름도. mermaid는 선 500개·글자 5만 자를 넘으면 그리지 않으므로, 크면 주소(라우트) 단위로 합쳐 그리고,
        그래도 크면 그림을 빼고 통합 화면의 Screen Map(라우트 단위, 캡처·검색)을 가리킨다."""
        moves = [e for e in self.edges if e.kind == "transition" and e.dst is not None]
        body = [f'  n{n.id}["n{n.id} {q(n.label)}<br/>{q(urlparse(n.url).path)}"]' for n in self.nodes]
        for e in moves:
            lab = {"filled": "채워서: ", "dismiss": "취소: "}.get(e.mode, "") + sentence(e.action) + (f" / {e.dialogs[-1]['type']}: {e.dialogs[-1]['message']}" if e.dialogs else "")
            body.append(f'  n{e.src} -->|"{q(lab)}"| n{e.dst}')
        if len(moves) <= MERMAID_EDGES and sum(len(x) + 1 for x in body) <= MERMAID_TEXT:
            return ["```mermaid", "flowchart TD", *body, "```"]
        route = {n.id: stable_path(n.url) or "/" for n in self.nodes}
        rids = {r: i for i, r in enumerate(dict.fromkeys(route.values()))}
        count: dict[tuple[str, str], int] = {}
        for e in moves:
            k = (route[e.src], route[e.dst])
            if k[0] != k[1]:
                count[k] = count.get(k, 0) + 1
        states = {r: sum(1 for v in route.values() if v == r) for r in rids}
        body = [f'  r{i}["{q(r)}<br/>상태 {states[r]}개"]' for r, i in rids.items()]
        body += [f'  r{rids[a]} -->|"{c}"| r{rids[b]}' for (a, b), c in count.items()]
        where = "통합 화면(`uv run eastshift ui`)의 Screen Map · as-is 탐색에서 라우트별로 캡처와 함께 본다"
        if len(count) <= MERMAID_EDGES and sum(len(x) + 1 for x in body) <= MERMAID_TEXT:
            return [f"상태 {len(self.nodes)}개·전이 {len(moves)}개라 mermaid 한도를 넘어 주소 단위로 합쳤다 (선의 숫자 = 전이 수). 상태 단위 흐름은 아래 동작 표, 그림은 {where}.",
                    "", "```mermaid", "flowchart LR", *body, "```"]
        return [f"흐름도 생략: 상태 {len(self.nodes)}개·전이 {len(moves)}개, 주소 {len(rids)}개로 합쳐도 mermaid 한도(선 {MERMAID_EDGES}개, 글자 {MERMAID_TEXT}자)를 넘는다. "
                f"흐름은 아래 동작 표, 그림은 {where}."]

    def markdown(self) -> str:
        def q(s: str) -> str:
            return s.replace('"', "'").replace("\n", " ")[:60]
        out = ["# 화면 탐색 결과", "", f"시작: `{self.start}`, 상태 {len(self.nodes)}개, 동작 {len(self.edges)}개", ""] + self._diagram(q)
        out += ["", "## 동작", "", "| 상태 | 방식 | 동작 | 결과 | 비고 |", "| :--- | :--- | :--- | :--- | :--- |"]
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
        if len(self.roots) > 1:
            out += ["", "## 직접 연 화면 (씨앗)", "", "시작점에서 눌러서는 못 갔지만 소스의 라우트 목록(--seeds)에 있어 주소로 바로 열었다. "
                    "메뉴가 호버로만 열리거나, 링크가 스크립트로만 이동하거나, 권한·데이터가 있어야 보이는 화면일 수 있다.", ""]
            out += [f"- `{_relative(u)}` → " + ", ".join(f"n{n.id} {n.label}" for n in self.nodes if n.root == k and not n.path) for k, u in enumerate(self.roots) if k]
        capped: dict[str, int] = {}
        for e in self.edges:
            if e.reason.startswith("route state budget"):
                r = e.reason.rsplit("(", 1)[-1].rstrip(")")
                capped[r] = capped.get(r, 0) + 1
        if capped:
            out += ["", f"## 라우트 상한({self.max_route_states})에 걸린 화면", "",
                    "같은 화면의 변형(필터·입력 조합·팝업)이 상한보다 많아 더 만들지 않았다. 다른 화면으로 가는 탐색은 계속했다.", ""]
            out += [f"- `{r}`: 상태 {self._route_count.get(r, 0)}개, 더 만들지 않은 전이 {c}개" for r, c in sorted(capped.items())]
        unexplored = [n for n in self.nodes if not n.explored]
        if unexplored:
            out += ["", f"## 탐색하지 않은 상태 (깊이 {self.max_depth} 도달)", ""] + [f"- n{n.id} {n.label}" for n in unexplored]
        return "\n".join(out) + "\n"


def check_output_dir(out_dir: Path) -> Path:
    """crawl 산출물 폴더만 덮어쓴다. 사람이 쓰거나 고친 파일이 있는 폴더(표식 없음)면 멈춘다."""
    marker = out_dir / ".crawl-output"
    if out_dir.exists() and not marker.exists() and any(p for p in out_dir.iterdir() if p.name not in ("screens", "routes.json")):  # routes.json: 탐색 전에 eastshift routes 가 둔다
        raise SystemExit(f"{out_dir} is not a crawl output directory (no {marker.name}). Crawl overwrites its output; "
                         "use a fresh --out and keep reviewed scenarios/tests in scenarios/<app>/ or e2e/<app>/.")
    return marker


def mark_output_dir(out_dir: Path) -> None:
    """check_output_dir 을 통과한 폴더에 표식을 남긴다. 탐색 전에 남겨야 끊긴 탐색의 이어 하기 기록이 있는 폴더도 다시 쓸 수 있다."""
    marker = check_output_dir(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    marker.write_text("eastshift crawl output: regenerated on every crawl. Move reviewed files out before editing them.\n", encoding="utf-8")


CHECKPOINT = ".crawl-partial.jsonl"


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
