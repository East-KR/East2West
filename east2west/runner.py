"""시나리오 실행기.

step 종류
  goto: <url>                          결정론
  do: <자연어>  [fill: <값>]           Jev가 대상 요소를 고른다. fill이 있으면 입력 가능한 요소만 후보.
  press: <키>                          결정론 (예: Enter)
  expect: {url_contains, url_path, text, text_matches, no_text, title_contains, field+value, snapshot_contains, dialog}   결정론 assert (모든 프레임 대상)
  dialog: accept | dismiss | {accept: <prompt 입력값>}   다음에 뜨는 alert/confirm/prompt 하나의 처리 (기본 accept)
  save_storage_state: <path>

goto의 상대 경로는 --base-url (또는 EAST2WEST_BASE_URL) 기준. 문자열 값의 ${VAR}는 환경변수로 치환 (비밀번호 등).
프레임: 최상위 문서와 보이는 frame/iframe을 모두 후보로 본다 (레거시 frameset). 캐시된 프레임에 요소가 없으면
다른 프레임에서 같은 (role, name)을 찾는다 (as-is frameset 캐시를 to-be 단일 페이지에서 재생).
기록/비교: --record DIR 은 스텝별 관찰값을 골든으로 저장, --compare DIR 은 골든과 비교해 차이를 보고 (observe.py).

캐시: 첫 실행에서 Jev가 고른 (role, name, nth, scope)을 .east2west-cache/<시나리오>.json에 **스텝 문장을 키로** 저장하고,
다음 실행부터는 그 요소가 페이지에 있으면 Jev 없이 재생한다. 못 찾으면 Jev를 다시 불러 캐시를 고친다 (healed).
--replay-only 모드에서는 Jev를 부르지 않고 캐시 미스를 실패로 처리한다 (CI용, API 키 불필요).
"""
from __future__ import annotations

import json
import os
import re
import time
from dataclasses import asdict, dataclass, field, replace
from pathlib import Path
from typing import Any
from urllib.parse import urljoin, urlparse

import yaml
from playwright.sync_api import Error as PWError
from playwright.sync_api import Frame, Page, sync_playwright

from . import triage as _triage
from .jev import Decision, JevClient
from .observe import CompareOptions, compare, observation

UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/140.0.0.0 Safari/537.36"
from .snapshot import ACTIONABLE_ROLES, EDITABLE_ROLES, Element, _grams, parse_elements, rank_for_goal, relevance


def _origin(url: str) -> tuple[str, str, int | None]:
    parsed = urlparse(url)
    scheme = parsed.scheme.lower()
    return scheme, (parsed.hostname or "").lower(), parsed.port or {"http": 80, "https": 443}.get(scheme)


@dataclass
class StepResult:
    index: int
    kind: str
    text: str
    status: str  # pass | fail | skip
    ms: float
    source: str = ""  # jev | cache | -
    target: str = ""
    reason: str = ""
    jev: dict[str, Any] | None = None
    url_after: str = ""
    healed: bool = False  # 캐시가 있었지만 Jev로 다시 골랐다 (UI 변경 신호)
    dialogs: list[dict[str, Any]] = field(default_factory=list)  # 이 스텝에서 뜬 alert/confirm/prompt
    diff: list[str] = field(default_factory=list)  # --compare: 골든과 다른 점
    triage: dict[str, Any] | None = None  # --triage: Jev가 고른 실패 원인 (triage.py)


@dataclass
class RunResult:
    scenario: str
    status: str
    steps: list[StepResult] = field(default_factory=list)
    jev_calls: int = 0
    jev_ms_total: float = 0.0
    elapsed_ms: float = 0.0
    healed_steps: int = 0
    diff_steps: int = 0
    triage: dict[str, int] = field(default_factory=dict)  # 분류별 건수

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class Runner:
    def __init__(self, *, headed: bool = False, use_cache: bool = True, min_margin: float = 0.1, max_candidates: int = 60,
                 settle_ms: int = 1500, expect_timeout_s: float = 10, report_dir: Path = Path("reports"), cache_dir: Path = Path(".east2west-cache"),
                 storage_state: Path | None = None, replay_only: bool = False, base_url: str | None = None,
                 record_dir: Path | None = None, compare_dir: Path | None = None, compare_opts: CompareOptions | None = None,
                 triage: bool = False):
        self.headed, self.use_cache, self.min_margin, self.max_candidates = headed, use_cache, min_margin, max_candidates
        self.settle_ms, self.expect_timeout_s = settle_ms, expect_timeout_s
        self.report_dir, self.cache_dir = report_dir, cache_dir
        self.storage_state, self.replay_only = storage_state, replay_only
        self.base_url = base_url
        self.record_dir, self.compare_dir = record_dir, compare_dir
        self.compare_opts = compare_opts or CompareOptions()
        self.triage = triage  # 실패·diff 스텝의 원인을 Jev로 분류 (API 키 필요)
        self._triage_env: dict[str, dict[str, Any]] = {}  # 환경 문제로 분류된 실패 사유 → 결과. 같은 사유가 화면마다 반복되면 다시 묻지 않는다
        self._jev: JevClient | None = None
        self._dialog_plan: list[dict[str, Any]] = []
        self._step_dialogs: list[dict[str, Any]] = []
        self._all_dialogs: list[dict[str, Any]] = []

    @property
    def jev(self) -> JevClient:
        if self._jev is None:
            self._jev = JevClient()  # replay-only 모드에서는 끝까지 만들어지지 않는다 (API 키 불필요)
        return self._jev

    def close(self) -> None:
        if self._jev is not None:
            self._jev.close()

    # -- 페이지 도우미 ---------------------------------------------------------------
    @staticmethod
    def _page_info(page: Page) -> dict[str, Any]:
        return {"url": page.url, "title": page.title()}

    # -- 프레임 -----------------------------------------------------------------------
    @staticmethod
    def _frame_key(frame: Frame) -> str:
        parts: list[str] = []
        f = frame
        while f.parent_frame is not None:
            parts.append(f.name or ("url:" + urlparse(f.url).path))
            f = f.parent_frame
        return "/".join(reversed(parts))

    @classmethod
    def _frames(cls, page: Page) -> list[tuple[str, Frame]]:
        """최상위 문서와 보이는 하위 frame/iframe (문서 순서)."""
        out: list[tuple[str, Frame]] = []
        for f in page.frames:
            if f.is_detached():
                continue
            if f.parent_frame is not None:
                try:
                    if not f.frame_element().is_visible():
                        continue
                except PWError:
                    continue
            out.append((cls._frame_key(f), f))
        return out

    @staticmethod
    def _frame_snapshot(frame: Frame) -> str:
        body = frame.locator("body")  # frameset 문서에는 body가 없다
        loc = body if body.count() else frame.locator(":root")
        return loc.aria_snapshot(timeout=5000)

    def _snapshots(self, page: Page) -> list[tuple[str, str]]:
        out = []
        for key, f in self._frames(page):
            try:
                out.append((key, self._frame_snapshot(f)))
            except PWError:
                continue
        return out

    def _snapshot_text(self, page: Page) -> str:
        return "\n".join(snap if not key else f"## frame {key}\n{snap}" for key, snap in self._snapshots(page))

    def _elements(self, page: Page, *, editable_only: bool) -> list[Element]:
        out: list[Element] = []
        for key, snap in self._snapshots(page):
            for el in parse_elements(snap, roles=EDITABLE_ROLES if editable_only else ACTIONABLE_ROLES):
                out.append(replace(el, frame=key, order=len(out)))
        return out

    def _root(self, page: Page, frame_key: str) -> Frame | None:
        if not frame_key:
            return page.main_frame
        return next((f for k, f in self._frames(page) if k == frame_key), None)

    def _locator(self, page: Page, el: Element):
        root = self._root(page, el.frame)
        if root is None:
            return None
        loc = root.get_by_role(el.role, name=el.name, exact=True, disabled=False)
        return loc.nth(el.nth) if el.dup > 1 else loc

    def _count(self, page: Page, el: Element) -> int:
        loc = self._locator(page, el)
        return loc.count() if loc is not None else 0

    def _relocate(self, page: Page, el: Element) -> Element | None:
        """캐시된 프레임에 없으면 다른 프레임에서 같은 (role, name)을 찾는다. 한 프레임에서만, 같은 개수로 나올 때만 인정."""
        hits = []
        for key, f in self._frames(page):
            n = f.get_by_role(el.role, name=el.name, exact=True, disabled=False).count()
            if n:
                hits.append((key, n))
        if len(hits) == 1 and (hits[0][1] == 1 or hits[0][1] == el.dup):
            key, n = hits[0]
            return replace(el, frame=key, dup=n, nth=el.nth if n == el.dup else 0)
        return None

    def _is_visible(self, page: Page, el: Element) -> bool:
        loc = self._locator(page, el)
        if loc is None:
            return False
        if el.role == "option":  # 닫힌 네이티브 select의 option은 보이지 않지만 select가 보이면 선택할 수 있다
            sel = loc.locator("xpath=ancestor::select")
            return sel.count() > 0 and sel.first.is_visible()
        return loc.is_visible()

    def _visible(self, page: Page, elements: list[Element], goal: str) -> list[Element]:
        """목표와 관련도가 높은 순으로 보이는 요소만 골라 후보 상한만큼 모은다."""
        goal_grams = _grams(goal)
        by_relevance = sorted(elements, key=lambda e: (-relevance(e, goal_grams), e.order))
        out: list[Element] = []
        for el in by_relevance:
            if len(out) >= self.max_candidates:
                break
            try:
                if self._is_visible(page, el):
                    out.append(el)
            except PWError:
                continue
        return sorted(out, key=lambda e: e.order)

    def _settle(self, page: Page) -> Page:
        ctx = page.context
        try:
            page.wait_for_load_state("load", timeout=15000)
        except PWError:
            pass
        if page.is_closed():  # 스스로 닫히는 팝업 (레거시 우편번호·코드 검색 창) → 남은 창으로
            page = ctx.pages[-1]
        page.wait_for_timeout(self.settle_ms)
        # 새 탭이 열렸으면 그 탭으로 옮긴다.
        if ctx.pages and ctx.pages[-1] is not page:
            newest = ctx.pages[-1]
            try:
                newest.wait_for_load_state("load", timeout=15000)
            except PWError:
                pass
            newest.bring_to_front()
            return newest
        return page

    # -- step 실행 ---------------------------------------------------------------------
    def _do_target(self, page: Page, idx: int, text: str, target: dict[str, Any], value: str | None) -> tuple[StepResult, Page]:
        """명시된 대상 {role, name[, nth]} 또는 {role, row: N}(N번째 데이터 행 안의 첫 요소)을 결정론적으로 실행한다.
        못 찾거나 여러 개면 실패한다. 다른 요소로 대신하지 않는다 (Jev 복구 없음)."""
        t0 = time.perf_counter()
        role, name = target.get("role", "button"), target.get("name")
        label = f"{role} " + (f'"{name}"' if name is not None else f"in data row {target.get('row')}")
        fail = lambda why: (StepResult(idx, "do", text, "fail", _ms(t0), "target", label, reason=why), page)
        if "row" in target:
            hits = []
            for _, f in self._frames(page):
                rows = f.get_by_role("row").filter(has=f.get_by_role("cell"))
                if rows.count() >= int(target["row"]):
                    loc = rows.nth(int(target["row"]) - 1).get_by_role(role)
                    if loc.count():
                        hits.append(loc.first)
            if not hits:
                return fail(f"no {role} in data row {target['row']}")
            loc = hits[0]
        else:
            if not name:
                return fail("target name is empty (a screen marked by: review needs its name filled in)")
            hits = [(f, f.get_by_role(role, name=name, exact=True, disabled=False)) for _, f in self._frames(page)]
            hits = [(f, l) for f, l in hits if l.count()]
            total = sum(l.count() for _, l in hits)
            if not total:
                return fail(f'target not found: {role} "{name}"')
            if total > 1 and "nth" not in target:
                return fail(f'target is ambiguous: {total} × {role} "{name}" (add nth)')
            loc = hits[0][1].nth(int(target.get("nth", 0)))
        try:
            if value is not None:
                loc.fill(value, timeout=10000)
            elif role == "option":
                loc.locator("xpath=ancestor::select").first.select_option(label=name, timeout=10000)
            elif role in ("checkbox", "radio"):
                loc.check(timeout=10000)
            else:
                loc.click(timeout=10000)
        except Exception as e:
            return fail(f"action failed: {str(e).splitlines()[0]}")
        page = self._settle(page)
        return StepResult(idx, "do", text, "pass", _ms(t0), "target", label, url_after=page.url), page

    def _act(self, page: Page, el: Element, action: str, value: str | None, *, timeout: float = 10000) -> None:
        loc = self._locator(page, el)
        if loc is None or loc.count() < 1:
            raise RuntimeError("target not found on page")
        if action == "fill":
            loc.fill(value or "", timeout=timeout)
        elif el.role == "option":
            loc.locator("xpath=ancestor::select").first.select_option(label=el.name, timeout=timeout)
        elif el.role in ("checkbox", "radio"):
            loc.check(timeout=timeout)
        else:
            loc.click(timeout=timeout)

    def _do(self, page: Page, idx: int, text: str, value: str | None, cache: dict[str, Any], history: list[str], key: str) -> tuple[StepResult, Page]:
        action = "fill" if value is not None else "click"
        t0 = time.perf_counter()
        # 1) 캐시 재생
        cached = cache.get(key) if self.use_cache else None
        if cached:
            el = Element(cached["role"], cached["name"], 0, nth=cached.get("nth", 0), dup=cached.get("dup", 1), scope=cached.get("scope", ""),
                          frame=cached.get("frame", ""))
            if self._count(page, el) < 1:
                el = self._relocate(page, el) or el
            if self._count(page, el) >= 1:
                try:
                    self._act(page, el, action, value)
                    page = self._settle(page)
                    history.append(f"{action} {el.role} {el.name}")
                    return StepResult(idx, "do", text, "pass", _ms(t0), "cache", el.label(), url_after=page.url), page
                except Exception as e:  # 캐시가 낡았으면 Jev로 복구
                    history.append(f"cache replay failed: {e}")
        if self.replay_only:
            why = "replay-only: cached target not found on page" if cached else "replay-only: no cache for this step"
            return StepResult(idx, "do", text, "fail", _ms(t0), "cache", reason=why + " (run locally with Jev to repair the cache)"), page
        # 2) Jev 결정. aria 스냅샷에는 화면에 없는 요소도 섞이므로 보이는 요소만 후보로 보낸다.
        snapshot_text = self._snapshot_text(page)
        all_elements = self._elements(page, editable_only=(action == "fill"))
        elements = self._visible(page, all_elements, text)
        excluded: set[tuple[str, str]] = set()
        jev_info: dict[str, Any] = {}
        last_error = "no actionable candidates on page"
        for attempt in range(2):  # 실행 실패 시 그 요소를 빼고 한 번 더 묻는다.
            pool = rank_for_goal([e for e in elements if e.key not in excluded], text, limit=self.max_candidates)
            if not pool:
                break
            d: Decision = self.jev.choose(goal=text, action=action, elements=pool, page=self._page_info(page), history=history)
            if os.environ.get("MEASURE_DIR"):
                mdir = Path(os.environ["MEASURE_DIR"]); mdir.mkdir(parents=True, exist_ok=True)
                (mdir / f"{self._scenario_stem}-step{idx}-snapshot.txt").write_text(snapshot_text, encoding="utf-8")
                (mdir / f"{self._scenario_stem}-step{idx}-prompt.json").write_text(json.dumps({"step": text, "action": action, "page": self._page_info(page), "history": history[-6:], "criteria": {f"c{i}": e.describe(action) for i, e in enumerate(pool)} | {"abstain": "No candidate matches this step on the current page; do not act."}}, ensure_ascii=False, indent=1), encoding="utf-8")
            jev_info = {"candidates": d.candidates, "of_elements": len(all_elements), "visible": len(elements), "confidence": d.confidence, "margin": d.margin,
                        "ms": d.ms + jev_info.get("ms", 0), "top3": d.top, "attempt": attempt + 1,
                        "pool": [e.describe(action) for e in pool],
                        "jev_input_tokens": d.input_tokens, "jev_output_tokens": d.output_tokens, "jev_prompt_chars": d.prompt_chars,
                        "snapshot_chars": len(snapshot_text), "snapshot_lines": snapshot_text.count("\n") + 1}
            if d.element is None:
                return StepResult(idx, "do", text, "fail", _ms(t0), "jev", reason="Jev abstained (no matching target)", jev=jev_info), page
            if d.margin < self.min_margin:
                return StepResult(idx, "do", text, "fail", _ms(t0), "jev", d.element.label(),
                                  reason=f"ambiguous: margin {d.margin} < {self.min_margin}", jev=jev_info), page
            try:
                self._act(page, d.element, action, value)
                break
            except Exception as e:
                last_error = f'action failed on {d.element.role} "{d.element.name}": {str(e).splitlines()[0]}'
                excluded.add(d.element.key)
                history.append(last_error)
        else:
            return StepResult(idx, "do", text, "fail", _ms(t0), "jev", reason=last_error, jev=jev_info), page
        if not jev_info or d.element is None or d.element.key in excluded:
            return StepResult(idx, "do", text, "fail", _ms(t0), "jev", reason=last_error, jev=jev_info or None), page
        page = self._settle(page)
        cache[key] = {"role": d.element.role, "name": d.element.name, "nth": d.element.nth, "dup": d.element.dup, "scope": d.element.scope, "step": text}
        if d.element.frame:
            cache[key]["frame"] = d.element.frame
        history.append(f"{action} {d.element.role} {d.element.name}")
        return StepResult(idx, "do", text, "pass", _ms(t0), "jev", d.element.label(), jev=jev_info, url_after=page.url, healed=bool(cached)), page

    def _visible_texts(self, page: Page) -> list[str]:
        out = []
        for _, f in self._frames(page):
            try:
                if f.locator("body").count():
                    out.append(f.locator("body").inner_text(timeout=2000))
            except PWError:
                continue
        return out

    def _expect(self, page: Page, idx: int, cond: dict[str, Any]) -> StepResult:
        t0 = time.perf_counter()
        deadline = t0 + self.expect_timeout_s
        failures: list[str] = []
        while True:
            failures = []
            if "url_contains" in cond and cond["url_contains"] not in page.url:
                failures.append(f"url {page.url!r} lacks {cond['url_contains']!r}")
            if "url_path" in cond and urlparse(page.url).path != cond["url_path"]:  # 정확히 이 경로 (접두어 우연 일치 방지)
                failures.append(f"url path {urlparse(page.url).path!r} != {cond['url_path']!r}")
            if "text_matches" in cond:  # 형식이 맞는 문구가 보인다 (총 \d+건: 건수는 몰라도 결과가 나왔다)
                if not any(re.search(cond["text_matches"], t) for t in self._visible_texts(page)):
                    failures.append(f"no visible text matches {cond['text_matches']!r}")
            if "no_text" in cond:  # 이전 화면의 문구가 사라졌다 (화면이 실제로 바뀌었다)
                if any(cond["no_text"] in t for t in self._visible_texts(page)):
                    failures.append(f"text {cond['no_text']!r} still visible")
            if "title_contains" in cond and cond["title_contains"] not in page.title():
                failures.append(f"title {page.title()!r} lacks {cond['title_contains']!r}")
            frames = [f for _, f in self._frames(page)]
            if "field" in cond:  # 입력 요소의 현재 값 (첫 번째로 찾은 프레임)
                actual = "<error: field not found in any frame>"
                for f in frames:
                    loc = f.get_by_role("textbox", name=cond["field"], exact=True).or_(
                        f.get_by_role("combobox", name=cond["field"], exact=True)).or_(
                        f.get_by_role("searchbox", name=cond["field"], exact=True)).or_(
                        f.get_by_role("spinbutton", name=cond["field"], exact=True))
                    try:
                        if loc.count():
                            actual = loc.first.input_value(timeout=1000)
                            break
                    except Exception as e:
                        actual = f"<error: {str(e).splitlines()[0]}>"
                if actual != cond.get("value", ""):
                    failures.append(f"field {cond['field']!r} value {actual!r} != {cond.get('value', '')!r}")
            if "snapshot_contains" in cond:
                if not any(cond["snapshot_contains"] in snap for _, snap in self._snapshots(page)):
                    failures.append(f"aria snapshot lacks {cond['snapshot_contains']!r}")
            if "text" in cond:
                found = False
                for f in frames:
                    try:
                        found = f.get_by_text(cond["text"]).first.is_visible(timeout=500) or cond["text"] in f.content()
                    except PWError:
                        found = False
                    if found:
                        break
                if not found:
                    failures.append(f"text {cond['text']!r} not found")
            # 스모크용: 마지막 동작(goto/do/press) 이후에 생긴 문제
            if cond.get("http_ok") and self._events["http_errors"]:
                failures.append(f"HTTP error: {self._events['http_errors']}")
            if cond.get("no_js_error") and self._events["js_errors"]:
                failures.append(f"JS error: {self._events['js_errors']}")
            if cond.get("no_dialog") and self._events["dialogs"]:
                failures.append(f"unexpected dialog: {[d['type'] + ': ' + d['message'] for d in self._events['dialogs']]}")
            if "rows_at_least" in cond:  # 데이터 행 (cell이 있는 row) 수, 모든 프레임 합
                n = 0
                for f in frames:
                    try:
                        n += f.get_by_role("row").filter(has=f.get_by_role("cell")).count()
                    except PWError:
                        pass
                if n < int(cond["rows_at_least"]):
                    failures.append(f"data rows {n} < {cond['rows_at_least']}")
            if "dialog" in cond:  # 가장 최근에 뜬 alert/confirm/prompt의 메시지
                last = self._all_dialogs[-1]["message"] if self._all_dialogs else None
                if last is None or cond["dialog"] not in last:
                    failures.append(f"last dialog {last!r} lacks {cond['dialog']!r}")
            if not failures or time.perf_counter() > deadline:
                break
            page.wait_for_timeout(500)
        status = "pass" if not failures else "fail"
        return StepResult(idx, "expect", json.dumps(cond, ensure_ascii=False), status, _ms(t0), "-", reason="; ".join(failures), url_after=page.url)

    # -- 시나리오 -----------------------------------------------------------------------
    def _on_dialog(self, dialog) -> None:
        plan = self._dialog_plan.pop(0) if self._dialog_plan else {"action": "accept"}
        rec = {"type": dialog.type, "message": dialog.message, "action": plan["action"]}
        self._step_dialogs.append(rec)
        self._events["dialogs"].append(rec)
        self._all_dialogs.append(rec)
        try:
            if plan["action"] == "dismiss":
                dialog.dismiss()
            elif plan.get("text") is not None:
                dialog.accept(plan["text"])
            else:
                dialog.accept()
        except PWError:
            pass

    def _triage_step(self, sr: StepResult, result: RunResult, page: Page, scenario: str) -> _triage.Triage:
        """실패·diff 스텝의 근거를 모아 Jev에 원인을 묻는다. 페이지 읽기가 실패해도 남은 근거로 진행한다."""
        page_info: dict[str, Any] | None = None
        snapshot = ""
        try:
            page_info = self._page_info(page)
            snapshot = self._snapshot_text(page)
        except PWError:
            pass
        state = _triage.evidence(asdict(sr), scenario=scenario, history=[asdict(s) for s in result.steps[:-1]],
                                 events=self._events, page=page_info, snapshot=snapshot,
                                 mode={"replay_only": self.replay_only, "compare": self.compare_dir is not None, "record": self.record_dir is not None})
        return _triage.classify(self.jev, state)

    def _url(self, target: str) -> str:
        if (self.record_dir or self.compare_dir) and not self.base_url:
            raise ValueError("record/compare needs --base-url to verify the target server")
        if urlparse(target).scheme:
            resolved = target
        elif not self.base_url:
            raise ValueError(f"relative goto {target!r} needs --base-url or EAST2WEST_BASE_URL")
        else:
            resolved = urljoin(self.base_url.rstrip("/") + "/", target.lstrip("/"))
        if (self.record_dir or self.compare_dir) and _origin(resolved) != _origin(self.base_url):
            raise ValueError(f"goto target is outside configured server: {resolved}")
        return resolved

    @staticmethod
    def variants(scenario_path: Path) -> list[tuple[str, dict[str, str]]]:
        """시나리오 하나를 여러 대상에 돌리는 matrix. 행마다 (stem, 변수). matrix가 없으면 [(stem, {})].

        matrix: [{SCREEN_URL: /emp, SCREEN_NAME: 사원 조회}, …]  또는  matrix_file: screens.yaml (시나리오 기준 상대 경로)
        행마다 캐시·골든·리포트가 따로 생긴다 (<stem>--<행 번호>).
        """
        spec = yaml.safe_load(scenario_path.read_text(encoding="utf-8"))
        rows = spec.get("matrix")
        if spec.get("matrix_file"):
            rows = yaml.safe_load((scenario_path.parent / spec["matrix_file"]).read_text(encoding="utf-8"))
        if not rows:
            return [(scenario_path.stem, {})]
        return [(f"{scenario_path.stem}--{i:02d}", {k: str(v) for k, v in row.items()}) for i, row in enumerate(rows)]

    def _on_page(self, pg: Page) -> None:
        pg.on("dialog", self._on_dialog)
        pg.on("pageerror", lambda err: self._events["js_errors"].append(str(err).splitlines()[0]))

        def on_response(r) -> None:
            try:
                if r.request.is_navigation_request() and r.status >= 400:
                    self._events["http_errors"].append(f"{r.status} {r.url}")
            except PWError:
                pass
        pg.on("response", on_response)

    def run(self, scenario_path: Path, *, vars: dict[str, str] | None = None, stem: str | None = None) -> RunResult:
        vars = vars or {}
        stem = stem or scenario_path.stem
        self._scenario_stem = stem
        self._vars = vars
        spec = yaml.safe_load(scenario_path.read_text(encoding="utf-8"))
        try:
            name = _expand(spec.get("name") or stem, vars)
        except ValueError:
            name = spec.get("name") or stem
        steps: list[dict[str, Any]] = spec["steps"]
        opts = self.compare_opts.merged(spec.get("compare"))
        cache_file = self.cache_dir / f"{stem}.json"
        cache: dict[str, Any] = json.loads(cache_file.read_text()) if (self.use_cache and cache_file.exists()) else {}
        result = RunResult(scenario=name, status="pass")
        golden: list[dict[str, Any]] | None = None
        if self.compare_dir:
            gfile = self.compare_dir / f"{stem}.json"
            if not gfile.exists():
                result.status = "fail"
                print(f"  ✘ no golden recording {gfile} (record it on as-is with --record)")
                return result
            recorded = json.loads(gfile.read_text(encoding="utf-8"))
            if _origin(self.base_url or "") == _origin(recorded.get("base_url") or ""):
                result.status = "fail"
                print("  ✘ compare target is the recorded as-is server")
                return result
            golden = recorded["steps"]
        observations: list[dict[str, Any]] = []
        history: list[str] = []
        seen_keys: dict[str, int] = {}
        self._dialog_plan, self._all_dialogs = [], []
        self._events: dict[str, list] = {"dialogs": [], "js_errors": [], "http_errors": []}  # 마지막 동작 스텝 이후
        # 시나리오가 세션을 정할 수 있다: storage_state: none (로그인 안 된 상태가 필요한 인증 시나리오) | <path>
        state = spec.get("storage_state", self.storage_state)
        state = None if state in (None, "none", False) else Path(state)
        t_run = time.perf_counter()
        self.report_dir.mkdir(exist_ok=True)
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=not self.headed, args=["--disable-blink-features=AutomationControlled"])
            ctx_kwargs: dict[str, Any] = {"viewport": {"width": 1280, "height": 900}, "locale": "ko-KR", "user_agent": UA}
            if state and state.exists():
                ctx_kwargs["storage_state"] = str(state)
            ctx = browser.new_context(**ctx_kwargs)
            ctx.on("page", self._on_page)  # 팝업 창 포함: 대화상자, JS 오류, HTTP 오류 수집
            page = ctx.new_page()
            try:
                for idx, raw_step in enumerate(steps):
                    kind = next(iter(raw_step))
                    if result.status == "fail":
                        result.steps.append(StepResult(idx, kind, str(raw_step[kind]), "skip", 0))
                        continue
                    self._step_dialogs = []
                    if kind in ("goto", "do", "press"):
                        self._events = {"dialogs": [], "js_errors": [], "http_errors": []}
                    try:
                        step = _expand(raw_step, vars)
                    except ValueError as e:
                        sr = StepResult(idx, kind, str(raw_step[kind]), "fail", 0, reason=str(e))
                        step = None
                    if step is None:
                        pass
                    elif "goto" in step:
                        t0 = time.perf_counter()
                        try:
                            page.goto(self._url(step["goto"]), wait_until="load")
                            page = self._settle(page)
                            sr = StepResult(idx, "goto", step["goto"], "pass", _ms(t0), "-", url_after=page.url)
                        except Exception as e:
                            sr = StepResult(idx, "goto", step["goto"], "fail", _ms(t0), "-", reason=str(e).splitlines()[0])
                    elif "do" in step and "target" in step:  # 대상이 명시된 스텝: Jev도 캐시도 쓰지 않는다
                        sr, page = self._do_target(page, idx, step["do"], step["target"], step.get("fill"))
                    elif "do" in step:
                        seen_keys[raw_step["do"]] = seen_keys.get(raw_step["do"], 0) + 1
                        key = raw_step["do"] if seen_keys[raw_step["do"]] == 1 else f'{raw_step["do"]} #{seen_keys[raw_step["do"]]}'
                        sr, page = self._do(page, idx, step["do"], step.get("fill"), cache, history, key)
                        if sr.jev:
                            result.jev_calls += 1
                            result.jev_ms_total += sr.jev["ms"]
                    elif "press" in step:
                        t0 = time.perf_counter()
                        page.keyboard.press(step["press"])
                        page = self._settle(page)
                        sr = StepResult(idx, "press", step["press"], "pass", _ms(t0), "-", url_after=page.url)
                    elif "expect" in step:
                        sr = self._expect(page, idx, step["expect"])
                    elif "dialog" in step:  # 다음 alert/confirm/prompt 하나의 처리 방법을 예약
                        d = step["dialog"]
                        plan = {"action": "accept", "text": d["accept"]} if isinstance(d, dict) and "accept" in d else {"action": str(d)}
                        if plan["action"] not in ("accept", "dismiss"):
                            sr = StepResult(idx, "dialog", json.dumps(d, ensure_ascii=False), "fail", 0, reason="dialog must be accept | dismiss | {accept: <text>}")
                        else:
                            self._dialog_plan.append(plan)
                            sr = StepResult(idx, "dialog", json.dumps(d, ensure_ascii=False), "pass", 0, "-")
                    elif "save_storage_state" in step:  # 로그인 후 쿠키/스토리지 저장 → 다음 시나리오는 --storage-state로 재사용
                        t0 = time.perf_counter()
                        out = Path(step["save_storage_state"]); out.parent.mkdir(parents=True, exist_ok=True)
                        ctx.storage_state(path=str(out))
                        sr = StepResult(idx, "save_storage_state", str(out), "pass", _ms(t0), "-")
                    else:
                        sr = StepResult(idx, "?", json.dumps(step, ensure_ascii=False), "fail", 0, reason="unknown step type")
                    sr.dialogs = list(self._step_dialogs)
                    if sr.status == "pass" and kind in ("goto", "do", "press") and (self.record_dir or self.compare_dir) and _origin(page.url) != _origin(self.base_url):
                        sr.status, sr.reason = "fail", f"page left the configured target: {page.url}"
                    # 기록/비교: 화면을 바꾸는 스텝 직후의 관찰값. 스텝에 compare: false 를 달면 건너뛴다 (시간 의존 화면 등).
                    if sr.status == "pass" and kind in ("goto", "do", "press") and raw_step.get("compare", True) and (self.record_dir or golden is not None):
                        obs = observation(index=idx, kind=kind, text=str(raw_step[kind]), url=page.url, title=page.title(),
                                          snapshot=self._snapshot_text(page), dialogs=sr.dialogs, opts=opts)
                        observations.append(obs)
                        if golden is not None:
                            g = next((o for o in golden if o["index"] == idx), None)
                            sr.diff = ["! no golden observation for this step (re-record)"] if g is None else compare(g, obs, opts)
                            if sr.diff:
                                result.diff_steps += 1
                                (self.report_dir / f"{stem}-step{idx}-diff.txt").write_text("\n".join(sr.diff), encoding="utf-8")
                    result.steps.append(sr)
                    if sr.healed:
                        result.healed_steps += 1
                    _print_step(sr)
                    if sr.status == "fail":
                        result.status = "fail"
                        shot = self.report_dir / f"{stem}-step{idx}-fail.png"
                        try:
                            page.screenshot(path=str(shot), full_page=False)
                        except PWError:
                            pass
                    if self.triage and _triage.needs_triage(asdict(sr)):
                        env_key = re.sub(r"https?://\S+", "<url>", sr.reason or "")  # 서버가 죽으면 화면 수백 개가 같은 사유로 실패한다
                        if sr.status == "fail" and env_key in self._triage_env:
                            sr.triage = {**self._triage_env[env_key], "reused": True}
                        else:
                            sr.triage = self._triage_step(sr, result, page, name).to_dict()
                            if sr.status == "fail" and sr.triage["category"] == "environment" and sr.triage.get("likely_widespread", 0) >= 0.7:
                                self._triage_env[env_key] = sr.triage
                        result.triage[sr.triage["category"]] = result.triage.get(sr.triage["category"], 0) + 1
                        print(f"     {_triage.format_line(sr.triage)}")
                try:
                    page.screenshot(path=str(self.report_dir / f"{stem}-final.png"), full_page=False)
                except PWError:
                    pass
            finally:
                browser.close()
        if golden is not None:
            observed_indexes = {o["index"] for o in observations}
            for expected in golden:
                if expected["index"] not in observed_indexes:
                    result.steps.append(StepResult(expected["index"], expected["kind"], expected["text"], "diff", 0,
                                                   reason="approved observation was not executed",
                                                   diff=["! approved observation was not executed"]))
                    result.diff_steps += 1
        result.elapsed_ms = _ms(t_run)
        if result.status == "pass" and result.diff_steps:
            result.status = "diff"
        if self.use_cache and any(s.source == "jev" and s.status == "pass" for s in result.steps):
            self.cache_dir.mkdir(parents=True, exist_ok=True)
            cache_file.write_text(json.dumps(cache, ensure_ascii=False, indent=1), encoding="utf-8")
        recorded = ""
        if self.record_dir:
            if result.status == "pass":
                self.record_dir.mkdir(parents=True, exist_ok=True)
                gfile = self.record_dir / f"{stem}.json"
                gfile.write_text(json.dumps({"scenario": name, "source": str(scenario_path), "recorded_at": time.strftime("%Y-%m-%d %H:%M:%S"),
                                             "base_url": self.base_url, "steps": observations}, ensure_ascii=False, indent=1), encoding="utf-8")
                recorded = f" | golden {gfile}"
            else:
                recorded = " | golden NOT written (scenario failed)"
        report = self.report_dir / f"{stem}-{time.strftime('%Y%m%d-%H%M%S')}.json"
        report.write_text(json.dumps(result.to_dict(), ensure_ascii=False, indent=1), encoding="utf-8")
        healed = f" | ⚠ healed steps {result.healed_steps} (cache changed, review the diff)" if result.healed_steps else ""
        diffs = f" | ≠ {result.diff_steps} steps differ from golden" if result.diff_steps else ""
        triaged = (" | ⚑ " + ", ".join(f"{k} {v}" for k, v in result.triage.items())) if result.triage else ""
        print(f"== {name}: {result.status.upper()} | {result.elapsed_ms / 1000:.1f}s | Jev calls {result.jev_calls} ({result.jev_ms_total:.0f}ms){healed}{diffs}{triaged}{recorded} | report {report}")
        return result


_VAR = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}")


def _expand(value: Any, vars: dict[str, str] | None = None) -> Any:
    """문자열 안의 ${VAR}를 matrix 변수, 없으면 환경변수로 치환. 둘 다 없으면 오류 (빈 값으로 조용히 진행하지 않는다)."""
    env = {**os.environ, **(vars or {})}
    if isinstance(value, str):
        missing = [v for v in _VAR.findall(value) if v not in env]
        if missing:
            raise ValueError(f"variable not set: {', '.join(missing)}")
        return _VAR.sub(lambda m: env[m.group(1)], value)
    if isinstance(value, dict):
        return {k: _expand(v, vars) for k, v in value.items()}
    if isinstance(value, list):
        return [_expand(v, vars) for v in value]
    return value


def _ms(t0: float) -> float:
    return round((time.perf_counter() - t0) * 1000, 1)


def _print_step(s: StepResult) -> None:
    mark = {"pass": "✔", "fail": "✘", "skip": "·"}[s.status]
    extra = ""
    if s.jev:
        extra = f" [jev {s.jev['candidates']}/{s.jev['of_elements']} cands, conf {s.jev['confidence']}, margin {s.jev['margin']}, {s.jev['ms']:.0f}ms]" + (" ⚠ HEALED" if s.healed else "")
    elif s.source == "cache":
        extra = " [cache]"
    elif s.source == "target":
        extra = " [target]"
    tgt = f" -> {s.target}" if s.target else ""
    why = f"  !! {s.reason}" if s.reason else ""
    dlg = "".join(f'  [{d["type"]} "{d["message"]}" → {d["action"]}]' for d in s.dialogs)
    print(f"  {mark} {s.index:2d} {s.kind:6s} {s.text}{tgt}{extra}{dlg}{why}")
    if s.diff:
        print(f"     ≠ differs from golden ({len(s.diff)} lines):")
        for line in s.diff[:12]:
            print(f"       {line}")
        if len(s.diff) > 12:
            print(f"       … ({len(s.diff) - 12} more, see reports/*-step{s.index}-diff.txt)")
