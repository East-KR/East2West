"""Playwright 테스트용 helper (`ui` fixture). pytest 플러그인 `parity.pwtest.plugin`이 만든다.

- 요소는 화면에 보이는 역할과 이름으로 찾는다 (getByRole). 프레임과 무관하다: as-is frameset에서 쓴 테스트가 to-be 단일 페이지에서 그대로 돈다.
- 테스트는 "무엇을"만 쓴다 (`ui.select("품목", "볼펜")`). 위젯마다 다른 "어떻게"는 이 파일이 맡는다 (네이티브 select / 커스텀 드롭다운).
- alert/confirm/prompt는 기본 accept, `ui.dialog("dismiss")`로 다음 하나를 바꾼다.
- 동작(goto/click/fill/select/check/press) 직후마다 관찰값을 골든으로 기록(--record)하거나 비교(--compare)한다. 정규화는 parity.observe.
- 이름 매핑(--name-map): as-is 이름 → to-be 이름. 테스트 코드는 as-is 이름 그대로 둔다.
- expect_* 호출은 (종류, 대상, 기대값)으로 기록된다. 골든에 함께 저장되고, 비교 때 테스트의 기대값이 기록 이후 바뀌었으면
  실패한다 (to-be 결과에 맞춰 기대값을 고치는 것을 막는다).
"""
from __future__ import annotations

import json
import re
import shutil
import tempfile
import time
from pathlib import Path
from typing import Any
from urllib.parse import urljoin

from playwright.sync_api import Frame, Locator, Page

from parity.observe import CompareOptions, compare, observation


class UI:
    def __init__(self, page: Page, *, base_url: str, test_id: str, record_dir: Path | None = None, compare_dir: Path | None = None,
                 compare_opts: CompareOptions | None = None, settle_ms: int = 500, timeout_ms: int = 5000,
                 name_map: dict[str, str] | None = None, self_compare: bool = False):
        self.page, self.base_url, self.test_id = page, base_url, test_id
        self.name_map = name_map or {}  # as-is 이름 → to-be 이름 (의도된 라벨 변경). 테스트 코드는 as-is 이름 그대로
        self.record_dir, self.compare_dir = record_dir, compare_dir
        self.opts = compare_opts or CompareOptions()
        self.settle_ms, self.timeout_ms = settle_ms, timeout_ms
        self.dialogs: list[dict[str, Any]] = []
        self._plan: list[dict[str, Any]] = []
        self._step_dialogs: list[dict[str, Any]] = []
        self._step = 0
        self.observations: list[dict[str, Any]] = []
        self._shots = Path(tempfile.mkdtemp(prefix="parity-shots-")) if record_dir else None  # 통과하면 골든의 shots/로 옮긴다
        self.shot_dir = re.sub(r"[^\w.-]+", "_", test_id)
        self.diffs: list[tuple[int, str, list[str]]] = []
        self.assertions: list[dict[str, Any]] = []
        self._golden: list[dict[str, Any]] | None = None
        self._golden_assertions: list[dict[str, Any]] = []
        if compare_dir:
            g = compare_dir / f"{test_id}.json"
            if not g.exists():
                raise AssertionError(f"no golden {g} (record on as-is first)")
            data = json.loads(g.read_text(encoding="utf-8"))
            if not self_compare and data.get("base_url", "").rstrip("/") == base_url.rstrip("/"):
                raise AssertionError(f"golden {g} was recorded on {base_url} itself; compare needs the as-is recording")
            self._golden = data["steps"]
            self._golden_assertions = data.get("assertions", [])
        page.context.on("page", lambda p: p.on("dialog", self._on_dialog))
        page.on("dialog", self._on_dialog)

    # -- 프레임 ----------------------------------------------------------------
    def frames(self) -> list[Frame]:
        out = []
        for f in self.page.frames:
            if f.is_detached():
                continue
            if f.parent_frame is not None:
                try:
                    if not f.frame_element().is_visible():
                        continue
                except Exception:
                    continue
            out.append(f)
        return out

    def locate(self, role: str, name: str, nth: int | None = None) -> Locator:
        """모든 프레임에서 (role, name)을 찾는다. 한 프레임에서만 나와야 한다. 같은 이름이 여럿이면 nth(0부터)로 고른다."""
        name = self.name_map.get(name, name)
        deadline = time.monotonic() + self.timeout_ms / 1000
        while True:
            hits = [f.get_by_role(role, name=name, exact=True) for f in self.frames()]
            hits = [l for l in hits if l.count()]
            if len(hits) == 1:
                return hits[0].nth(nth) if nth is not None else hits[0]
            if len(hits) > 1:
                raise AssertionError(f'{role} "{name}" found in {len(hits)} frames')
            if time.monotonic() > deadline:
                raise AssertionError(f'{role} "{name}" not found in any frame')
            self.page.wait_for_timeout(200)

    def snapshot_text(self) -> str:
        parts = []
        for f in self.frames():
            body = f.locator("body")
            try:
                parts.append((body if body.count() else f.locator(":root")).aria_snapshot(timeout=5000))
            except Exception:
                continue
        return "\n".join(parts)

    # -- 대화상자 ----------------------------------------------------------------
    def dialog(self, action: str = "accept", text: str | None = None) -> None:
        """다음 alert/confirm/prompt 하나의 처리 (기본 accept)."""
        self._plan.append({"action": action, "text": text})

    def _on_dialog(self, d) -> None:
        plan = self._plan.pop(0) if self._plan else {"action": "accept", "text": None}
        rec = {"type": d.type, "message": d.message, "action": plan["action"]}
        self.dialogs.append(rec)
        self._step_dialogs.append(rec)
        d.dismiss() if plan["action"] == "dismiss" else (d.accept(plan["text"]) if plan["text"] is not None else d.accept())

    # -- 동작 (직후 자동 관찰) ------------------------------------------------------
    def _after(self, kind: str, text: str) -> None:
        try:
            self.page.wait_for_load_state("load", timeout=15000)
            for f in self.frames():
                f.wait_for_load_state("load", timeout=15000)
        except Exception:
            pass
        self.page.wait_for_timeout(self.settle_ms)
        if self.record_dir or self._golden is not None:
            obs = observation(index=self._step, kind=kind, text=text, url=self.page.url, title=self.page.title(),
                              snapshot=self.snapshot_text(), dialogs=self._step_dialogs, opts=self.opts)
            if self._shots is not None:  # 승인하는 사람이 보는 단계별 화면
                name = f"{self._step:02d}.jpg"
                try:
                    self.page.screenshot(path=str(self._shots / name), type="jpeg", quality=60)
                    obs["shot"] = f"shots/{self.shot_dir}/{name}"
                except Exception:
                    pass
            self.observations.append(obs)
            if self._golden is not None:
                g = next((o for o in self._golden if o["index"] == self._step), None)
                diff = ["! no golden observation for this step"] if g is None else compare(g, obs, self.opts, self.name_map)
                if diff:
                    self.diffs.append((self._step, text, diff))
        self._step += 1
        self._step_dialogs = []

    def goto(self, path: str) -> None:
        self.page.goto(urljoin(self.base_url.rstrip("/") + "/", path.lstrip("/")))
        self._after("goto", path)

    def click(self, role: str, name: str) -> None:
        self.locate(role, name).click()
        self._after("click", f'{role} "{name}"')

    def fill(self, name: str, value: str, role: str = "textbox", nth: int | None = None) -> None:
        self.locate(role, name, nth).fill(value)
        self._after("fill", f'{role} "{name}" = {value}')

    def act(self, role: str, name: str, nth: int | None = None) -> None:
        """역할에 맞는 기본 동작: option은 부모 select에서 선택, checkbox/radio는 체크, 나머지는 클릭 (parity 러너와 같다)."""
        loc = self.locate(role, name, nth)
        if role == "option":
            sel = loc.locator("xpath=ancestor::select")
            if sel.count():
                sel.first.select_option(label=self.name_map.get(name, name))
            else:
                loc.click()
        elif role in ("checkbox", "radio"):
            loc.check()
        else:
            loc.click()
        self._after("act", f'{role} "{name}"' + (f" #{nth + 1}" if nth is not None else ""))

    def select(self, name: str, option: str) -> None:
        """드롭다운에서 고른다. 네이티브 <select>면 select_option, 아니면 열고(click) → option 클릭 (MUI, Element 등 커스텀 위젯)."""
        box = self.locate("combobox", name)
        if box.evaluate("e => e.tagName") == "SELECT":
            box.select_option(label=option)
        else:
            box.click()
            self.locate("option", option).click()
        self._after("select", f'combobox "{name}" = {option}')

    def check(self, name: str, role: str = "checkbox") -> None:
        self.locate(role, name).check()
        self._after("check", f'{role} "{name}"')

    def press(self, key: str) -> None:
        self.page.keyboard.press(key)
        self._after("press", key)

    # -- 검증 -----------------------------------------------------------------
    def _poll(self, check, what: str) -> None:
        deadline = time.monotonic() + self.timeout_ms / 1000
        last = None
        while time.monotonic() < deadline:
            ok, last = check()
            if ok:
                return
            self.page.wait_for_timeout(200)
        raise AssertionError(f"{what}: got {last!r}")

    def _assert(self, kind: str, target: str, value: str | None = None) -> None:
        self.assertions.append({"step": self._step, "kind": kind, "target": target, "value": value})

    def assertion_drift(self, passed: bool) -> list[str]:
        """비교 모드: 테스트의 기대값이 as-is 기록 이후 바뀌었는지. 실패한 테스트는 실행된 앞부분만 본다."""
        if self._golden is None:
            return []
        g, a = self._golden_assertions, self.assertions
        out = [f"expectation changed since as-is recording: {x['kind']} {x['target']!r} {y['value']!r} → {x['value']!r}"
               if (x["kind"], x["target"]) == (y["kind"], y["target"]) else
               f"expectation changed since as-is recording: {y['kind']} {y['target']!r} → {x['kind']} {x['target']!r}"
               for x, y in zip(a, g) if x != y]
        if len(a) > len(g):
            out += [f"expectation added since as-is recording: {x['kind']} {x['target']!r} = {x['value']!r}" for x in a[len(g):]]
        if passed and len(a) < len(g):
            out += [f"expectation removed since as-is recording: {y['kind']} {y['target']!r} = {y['value']!r}" for y in g[len(a):]]
        return out

    def expect_field(self, name: str, value: str) -> None:
        """입력 요소의 값. textbox·spinbutton은 입력값, combobox는 선택된 표시값 (네이티브·커스텀 모두)."""
        self._assert("field", name, value)
        def current() -> str:
            for role in ("textbox", "spinbutton", "combobox"):
                name_ = self.name_map.get(name, name)
                hits = [f.get_by_role(role, name=name_, exact=True) for f in self.frames()]
                hits = [l for l in hits if l.count()]
                if len(hits) > 1 or (hits and hits[0].count() > 1):
                    return f"<ambiguous: {role} {name_!r} matches more than one element>"
                if hits:
                    el = hits[0].first
                    tag = el.evaluate("e => e.tagName")
                    if tag == "SELECT":
                        return el.evaluate("e => e.options[e.selectedIndex]?.text ?? ''")
                    if tag in ("INPUT", "TEXTAREA"):
                        return el.input_value()
                    return el.inner_text().strip()
            return "<not found>"
        self._poll(lambda: (lambda v: (v == value, v))(current()), f'field "{name}" == {value!r}')

    def expect_text(self, text: str) -> None:
        """문구가 화면에 보인다. 숨겨진 요소(display:none)에만 있는 문구는 인정하지 않는다."""
        self._assert("text", text)
        self._poll(lambda: (any(f.get_by_text(text).locator("visible=true").count() for f in self.frames()), None), f"text {text!r} visible")

    def expect_dialog(self, message: str) -> None:
        """가장 최근 alert/confirm/prompt 문구와 정확히 같아야 한다 (부분 일치는 문구 변경을 놓친다). 평범한 assert는 -O에서 사라지므로 쓰지 않는다."""
        self._assert("dialog", message)
        last = self.dialogs[-1]["message"] if self.dialogs else None
        if last != message:
            raise AssertionError(f"last dialog {last!r} != {message!r}")

    def expect_url_path(self, path: str) -> None:
        """URL 경로가 정확히 이것 (접두어가 우연히 같은 다른 화면을 통과시키지 않는다)."""
        from urllib.parse import urlparse
        self._assert("url_path", path)
        self._poll(lambda: (lambda p: (p == path, p))(urlparse(self.page.url).path), f"url path == {path!r}")

    def expect_url_contains(self, part: str) -> None:
        self._assert("url_contains", part)
        self._poll(lambda: (part in self.page.url, self.page.url), f"url contains {part!r}")

    def expect_title(self, part: str) -> None:
        self._assert("title", part)
        self._poll(lambda: (part in self.page.title(), self.page.title()), f"title contains {part!r}")

    def _texts(self) -> list[str]:
        out = []
        for f in self.frames():
            try:
                if f.locator("body").count():
                    out.append(f.locator("body").inner_text(timeout=2000))
            except Exception:
                continue
        return out

    def expect_text_matches(self, pattern: str) -> None:
        """형식이 맞는 문구가 보인다. 매번 바뀌는 숫자는 \\d+ 로 (총 \\d+건, 주문번호 \\d+)."""
        import re as _re
        self._assert("text_matches", pattern)
        self._poll(lambda: (any(_re.search(pattern, t) for t in self._texts()), None), f"text matching {pattern!r} visible")

    def expect_no_text(self, text: str) -> None:
        """이전 화면의 문구가 사라졌다 (화면이 실제로 바뀌었다)."""
        self._assert("no_text", text)
        self._poll(lambda: (not any(text in t for t in self._texts()), None), f"text {text!r} gone")

    def expect_snapshot(self, fragment: str) -> None:
        self._assert("snapshot", fragment)
        self._poll(lambda: (fragment in self.snapshot_text(), None), f"snapshot contains {fragment!r}")

    # -- 마무리 ---------------------------------------------------------------
    def screenshot(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        try:
            self.page.screenshot(path=str(path))
        except Exception:
            pass

    def finish(self, passed: bool) -> None:
        if self.record_dir and passed:
            self.record_dir.mkdir(parents=True, exist_ok=True)
            dest = self.record_dir / "shots" / self.shot_dir
            shutil.rmtree(dest, ignore_errors=True)
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(self._shots), dest)
            (self.record_dir / f"{self.test_id}.json").write_text(
                json.dumps({"test": self.test_id, "base_url": self.base_url, "recorded_at": time.strftime("%Y-%m-%d %H:%M:%S"),
                            "assertions": self.assertions, "steps": self.observations}, ensure_ascii=False, indent=1), encoding="utf-8")
