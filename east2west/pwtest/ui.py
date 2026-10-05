"""Playwright 테스트용 helper (`ui` fixture). pytest 플러그인 `east2west.pwtest.plugin`이 만든다.

- 요소는 화면에 보이는 역할과 이름으로 찾는다 (getByRole). 프레임과 무관하다: as-is frameset에서 쓴 테스트가 to-be 단일 페이지에서 그대로 돈다.
- 테스트는 "무엇을"만 쓴다 (`ui.select("품목", "볼펜")`). 위젯마다 다른 "어떻게"는 이 파일이 맡는다 (네이티브 select / 커스텀 드롭다운).
- alert/confirm/prompt는 기본 accept, `ui.dialog("dismiss")`로 다음 하나를 바꾼다.
- 동작(goto/click/fill/type/select/check/press) 직후마다 관찰값을 골든으로 기록(--record)하거나 비교(--compare)한다. 정규화는 east2west.observe.
  관찰은 화면이 멈춘 뒤에 한다: load 뒤 요청이 다 끝나고 스냅샷이 settle_ms 동안 안 바뀌면 멈춘 것 (계속 움직이는 화면은 상한에서 자른다).
  프레임 하나라도 스냅샷을 못 뜨면 실패다: 내용이 빠진 골든이 기록·승인되거나 빠진 채로 비교되면 안 된다.
- 이름 매핑(--name-map): as-is 이름 → to-be 이름. 테스트 코드는 as-is 이름 그대로 둔다. 키가 '/'로 시작하는 항목은 주소 매핑이다:
  goto·expect_url_path 가 to-be 주소로 바꿔 연다/맞춘다 (path_pairs, tobe_path). 단계 글과 골든은 as-is 주소 그대로.
- 단계마다 fetch/XHR 요청("METHOD /경로")을 부른 만큼 남긴다. 양쪽이 모두 부른 주소의 횟수가 다르면 차이다 (observe.request_counts).
  as-is 주소는 위 주소 매핑으로 맞추고, oracle.json 의 request_ignore 정규식에 맞는 주소(폴링 등)는 세지 않는다.
- 비교(--compare) 때도 기록처럼 단계마다 캡처한다 (JPEG 60, 임시 폴더 → tobe_shots). plugin 이 원장 사본 폴더로 복사해 통합 화면이 as-is 캡처와 나란히 보인다.
  민감정보 규칙은 기록과 같다: 비밀번호 같은 값을 넣은 뒤나 redact_patterns 가 있으면 찍지 않는다. 결함 주입 실행은 찍지 않는다.
- expect_* 호출은 (종류, 대상, 기대값)으로 기록된다. 골든에 함께 저장되고, 비교 때 테스트의 기대값이 기록 이후 바뀌었으면
  실패한다 (to-be 결과에 맞춰 기대값을 고치는 것을 막는다).
- 다른 단계는 oracle.json 의 allowed_differences(sha256 정확히)가 먼저, 그다음 차이 규칙(rules.py: 같은 뜻은 차이 아님, 자료·환경·보류·도구 한계·
  비교 불가·고객 결정은 사유와 함께 비교 제외)로 나눈다. 규칙에 다 맞지 않으면 다른 점 그대로다.
"""
from __future__ import annotations

import dataclasses
import json
import hashlib
import re
import shutil
import tempfile
import time
from pathlib import Path
from typing import Any
from urllib.parse import urljoin, urlsplit, urlunsplit

from playwright.sync_api import Error as PWError
from playwright.sync_api import Frame, Locator, Page

from east2west.observe import CompareOptions, compare, observation


def _origin(url: str) -> tuple[str, str, int | None]:
    parsed = urlsplit(url)
    return parsed.scheme.lower(), (parsed.hostname or "").lower(), parsed.port or {"http": 80, "https": 443}.get(parsed.scheme.lower())


def path_pairs(name_map: dict[str, str]) -> list[tuple[str, str]]:
    """이름 매핑 중 주소 항목: 키가 '/'로 시작하면 as-is 주소 → to-be 주소다 (전환하며 주소 체계가 바뀐 화면, /sys/UserList/index.do → /sys/user-list).
    {이름} 조각은 주소 한 단계와 맞고 값은 to-be 쪽 같은 이름 자리로 옮긴다 (/orders/{id} → /order/{id}). 오라클 폴더 안의 파일이라 사람이 승인한다."""
    return [(a, b) for a, b in name_map.items() if isinstance(a, str) and a.startswith("/") and isinstance(b, str) and b.startswith("/")]


def _path_rx(p: str) -> re.Pattern[str]:
    return re.compile("^" + re.sub(r"\\\{(\w+)\\\}", r"(?P<\1>[^/]+)", re.escape(p)) + "$")


def tobe_path(path: str, name_map: dict[str, str]) -> str:
    """as-is 주소 → to-be 주소 (path_pairs 의 첫 맞는 항목). 쿼리·해시·origin 은 그대로, 맞는 항목이 없으면 그대로."""
    parts = urlsplit(path)
    for a, b in path_pairs(name_map):
        m = _path_rx(a).match(parts.path)
        if m:
            try:
                new = b.format(**m.groupdict()) if "{" in b else b
            except (KeyError, IndexError, ValueError):
                new = b
            return urlunsplit(parts._replace(path=new))
    return path


# 입력칸의 오류 상태: 표준(aria-invalid, aria-errormessage → 없으면 aria-describedby 의 글)과 ExtJS 4(getActiveError, x-form-invalid-field).
# 레거시는 ExtJS 가 흔하고 오류를 aria 로 알리지 않는다. 문구의 줄바꿈(<br>, <li>)은 공백으로 — 위젯마다 다르게 쪼개도 같은 문구로 본다.
FIELD_STATE = """e => {
  const text = ids => (ids || '').split(/\\s+/).map(i => document.getElementById(i)).filter(Boolean).map(x => x.innerText || x.textContent || '').join(' ').trim();
  let invalid = e.getAttribute('aria-invalid') === 'true', error = '';
  if (invalid) error = text(e.getAttribute('aria-errormessage')) || text(e.getAttribute('aria-describedby'));
  const Ext = window.Ext, c = Ext && Ext.getCmp && e.id ? Ext.getCmp(e.id.replace(/-inputEl$/, '')) : null;
  if (c && c.getActiveError) {
    const html = c.getActiveError() || '', div = document.createElement('div');
    div.innerHTML = html.replace(/<br\\s*\\/?>|<\\/li>/gi, ' ');
    if (html || e.classList.contains('x-form-invalid-field')) { invalid = true; error = error || (div.textContent || '').trim(); }
  }
  return {invalid, error};
}"""


class UI:
    def __init__(self, page: Page, *, base_url: str, test_id: str, record_dir: Path | None = None, compare_dir: Path | None = None,
                 compare_opts: CompareOptions | None = None, settle_ms: int = 500, timeout_ms: int = 5000,
                 name_map: dict[str, str] | None = None, self_compare: bool = False,
                 setup: dict[str, str] | None = None):
        self.page, self.base_url, self.test_id = page, base_url, test_id
        self.name_map = name_map or {}  # as-is 이름 → to-be 이름 (의도된 라벨 변경). 테스트 코드는 as-is 이름 그대로
        self.setup = setup or {}
        self.record_dir, self.compare_dir = record_dir, compare_dir
        self.opts = compare_opts or CompareOptions()
        self.settle_ms, self.timeout_ms = settle_ms, timeout_ms
        self.dialogs: list[dict[str, Any]] = []
        self._plan: list[dict[str, Any]] = []
        self._step_dialogs: list[dict[str, Any]] = []
        self._step = 0
        self._secret_values: set[str] = set()
        from . import oracle
        privacy = oracle.load_config(compare_dir or record_dir) if (compare_dir or record_dir) else {}
        self._secret_fields = set(privacy.get("redact_fields", []))
        self._redact_patterns = [re.compile(p) for p in privacy.get("redact_patterns", [])]
        self._api_paths = [re.compile(p) for p in privacy.get("api_compare", [])]
        self._api_pending: list[Any] = []
        self._requests: list[str] = []  # 이번 단계의 fetch/XHR 요청 "METHOD /경로" (횟수 비교: observe.request_counts)
        if privacy.get("request_ignore"):
            self.opts = dataclasses.replace(self.opts, request_ignore=[*self.opts.request_ignore, *privacy["request_ignore"]])
        self._target_build_ids: set[str] = set()
        self.observations: list[dict[str, Any]] = []
        # 단계 캡처: 기록이면 통과할 때 골든의 shots/로 옮기고, 비교면 plugin 이 원장 사본 폴더(runs/<app>/<시각>/shots/)로 복사한다 (tobe_shots).
        # 결함 주입(as-is 자신과 비교)은 찍지 않는다. 가림 정규식(redact_patterns)이 있으면 비교 때는 아예 찍지 않는다 (화면에 가릴 값이 있을 수 있다)
        self._shots = (Path(tempfile.mkdtemp(prefix="east2west-shots-"))
                       if record_dir or (compare_dir and not self_compare and not self._redact_patterns) else None)
        self.tobe_shots: dict[int, str] = {}  # 비교 모드: 단계 번호 → 그 단계 직후 to-be 캡처 (임시 폴더)
        self.shot_dir = re.sub(r"[^\w.-]+", "_", test_id)
        self.diffs: list[tuple[int, str, list[str]]] = []
        self.accepted_diffs: list[dict[str, Any]] = []
        self.allowed_differences: list[dict[str, Any]] = []
        self.row_notes: dict[tuple[str, str, str], dict[str, Any]] = {}  # 다른 점으로 남은 단계에서 차이 규칙에 맞은 행 → 갈래·사유 (plugin 이 원장에 남긴다)
        self._judge = None  # 차이 규칙 (rules.py): 비교 때만
        self.assertions: list[dict[str, Any]] = []
        self._golden: list[dict[str, Any]] | None = None
        self._golden_assertions: list[dict[str, Any]] = []
        if compare_dir:
            from . import oracle
            self.allowed_differences = oracle.load_config(compare_dir).get("allowed_differences", [])
            from . import rules
            self._judge = rules.Judge(rules.load(compare_dir))  # oracle.json 의 difference_rules + 고객이 '수정'으로 정한 as-is 이상 동작
            g = compare_dir / f"{test_id}.json"
            if not g.exists():
                raise AssertionError(f"no golden {g} (record on as-is first)")
            data = json.loads(g.read_text(encoding="utf-8"))
            if data.get("setup", {}) != self.setup:
                raise AssertionError(f"test setup differs from recording: {data.get('setup', {})} != {self.setup}")
            if not self_compare and data.get("base_url", "").rstrip("/") == base_url.rstrip("/"):
                raise AssertionError(f"golden {g} was recorded on {base_url} itself; compare needs the as-is recording")
            self._golden = data["steps"]
            self._golden_assertions = data.get("assertions", [])
        page.context.on("page", lambda p: p.on("dialog", self._on_dialog))
        page.on("dialog", self._on_dialog)
        self._inflight: set[str] = set()  # 끝나지 않은 요청 (안정화 대기용). websocket·eventsource 는 끝나지 않으므로 세지 않는다
        page.on("request", lambda r: self._track(r, True))
        page.on("requestfinished", lambda r: self._track(r, False))
        page.on("requestfailed", lambda r: self._track(r, False))
        page.on("response", self._on_api_response)

    def _on_api_response(self, response) -> None:
        if response.request.resource_type == "document":
            build_id = response.headers.get("x-east2west-build-id")
            if build_id:
                self._target_build_ids.add(build_id)
        path = urlsplit(response.url).path
        if response.request.resource_type in ("xhr", "fetch") and any(p.fullmatch(path) for p in self._api_paths):
            self._api_pending.append(response)

    def _api_observations(self) -> list[dict[str, Any]]:
        responses, self._api_pending = self._api_pending, []
        out = []
        for response in responses:
            try:
                body = response.json() if "json" in response.headers.get("content-type", "").lower() else response.text()
                serialized = json.dumps(body, ensure_ascii=False, sort_keys=True) if not isinstance(body, str) else body
            except (PWError, ValueError) as exc:
                raise AssertionError(f"configured API response could not be read: {response.url}") from exc
            out.append({"path": self._redact(urlsplit(response.url).path), "method": response.request.method,
                        "status": response.status, "body": self._redact(serialized)})
        return sorted(out, key=lambda item: (item["path"], item["method"], item["status"], item["body"]))

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
                except PWError:  # 그 사이 떨어져 나간 프레임
                    continue
            out.append(f)
        return out

    def _track(self, r, started: bool) -> None:
        if r.resource_type in ("websocket", "eventsource"):
            return
        key = f"{id(r)}"
        (self._inflight.add if started else self._inflight.discard)(key)
        if started and r.resource_type in ("xhr", "fetch"):
            self._requests.append(f"{r.method} {urlsplit(r.url).path}")

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
                fb = self._text_fallback(role, name) if nth is None else None
                if fb is not None:
                    return fb
                raise AssertionError(f'{role} "{name}" not found in any frame')
            self.page.wait_for_timeout(200)

    CLICK_ROLES = ("button", "link", "tab", "menuitem")
    FALLBACK_TAGS = ("DIV", "SPAN", "A", "TD", "TH", "LI", "I", "B", "U", "INPUT", "BUTTON")

    def _text_fallback(self, role: str, name: str) -> Locator | None:
        """역할로 못 찾은 누르는 요소를 글자로 한 번 더 찾는다: 옛 화면의 `div onclick`, `<a href="#">` 안의 `<span>` 같은 역할 없는 버튼.
        보이는 것 중 글자가 정확히 같은 요소가 프레임을 통틀어 하나이고 태그가 누를 만한 것(제목·문단은 제외)일 때만 쓴다."""
        if role not in self.CLICK_ROLES:
            return None
        hits = []
        for f in self.frames():
            loc = f.get_by_text(name, exact=True).locator("visible=true")
            try:
                n = loc.count()
            except PWError:
                continue
            hits += [loc.nth(i) for i in range(n)]
        if len(hits) != 1:
            return None
        try:
            tag = hits[0].evaluate("e => e.tagName")
        except PWError:
            return None
        return hits[0] if tag in self.FALLBACK_TAGS else None

    def snapshot_text(self) -> str:
        """보이는 모든 프레임의 aria 스냅샷. 프레임 하나라도 못 읽으면 실패한다 (내용이 빠진 관찰값을 골든이나 비교에 쓰지 않는다)."""
        parts = []
        for f in self.frames():
            try:
                body = f.locator("body")
                parts.append((body if body.count() else f.locator(":root")).aria_snapshot(timeout=5000))
            except PWError as e:
                raise RuntimeError(f"frame snapshot failed ({f.url or 'about:blank'}): {str(e).splitlines()[0]}") from e
        return "\n".join(parts)

    def _try_snapshot(self) -> str | None:
        """안정화 대기 중에는 프레임이 갈리는 순간이 있어 실패를 '아직 안 멈춤'으로 본다."""
        try:
            return self.snapshot_text()
        except RuntimeError:
            return None

    def _settle(self) -> str:
        """동작 뒤 화면이 멈출 때까지 기다려 그 스냅샷을 돌려준다.
        멈춤 = 끝나지 않은 요청이 없고 스냅샷이 settle_ms 동안 그대로. 느린 조회(요청이 몇 초 걸림)는 끝날 때까지 기다린다 (상한 10×settle_ms, 최소 10초).
        요청 없이 계속 바뀌는 화면(시계, 애니메이션)은 6×settle_ms(최소 3초)에서, 요청이 끝나지 않는 화면(폴링)은 위 상한에서 그 시점 것을 쓴다."""
        try:
            self.page.wait_for_load_state("load", timeout=15000)
            for f in self.frames():
                f.wait_for_load_state("load", timeout=15000)
        except PWError:
            pass
        quiet = max(self.settle_ms, 100) / 1000
        t0 = time.monotonic()
        cap_changing, cap_requests = t0 + max(quiet * 6, 3.0), t0 + max(quiet * 10, 10.0)
        last, since = self._try_snapshot(), t0
        while True:
            self.page.wait_for_timeout(min(150, quiet * 1000))
            snap = self._try_snapshot()
            now = time.monotonic()
            if snap != last or snap is None:
                last, since = snap, now
            elif now - since >= quiet and not self._inflight:
                return snap
            if now > cap_requests or (now > cap_changing and not self._inflight):
                return snap if snap is not None else self.snapshot_text()  # 끝까지 못 읽었으면 여기서 오류가 난다

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
    def _after(self, kind: str, text: str, extra=None) -> None:
        snapshot = self._settle()
        if extra is not None:  # 스냅샷에 안 나오는 상태(칸의 오류 표시 · 문구)를 화면이 멈춘 뒤 읽어 붙인다 (type)
            snapshot = snapshot.rstrip("\n") + "".join(f"\n{line}" for line in extra())
        if (self.record_dir or self.compare_dir) and _origin(self.page.url) != _origin(self.base_url):
            raise AssertionError(f"page left the configured target: {self.page.url}")
        if self.record_dir or self._golden is not None:
            snapshot = self._redact(snapshot)
            text = self._redact(text)
            safe_dialogs = [{**d, "message": self._redact(d["message"])} for d in self._step_dialogs]
            obs = observation(index=self._step, kind=kind, text=text, url=self._redact(self.page.url), title=self._redact(self.page.title()),
                              snapshot=snapshot, dialogs=safe_dialogs, opts=self.opts, api=self._api_observations(),
                              requests=sorted(self._redact(r) for r in getattr(self, "_requests", [])))
            if self._shots is not None and not (self._secret_values or self._redact_patterns):
                name = f"{self._step:02d}.jpg"
                try:
                    self.page.screenshot(path=str(self._shots / name), type="jpeg", quality=60)
                    if self.record_dir:
                        obs["shot"] = f"shots/{self.shot_dir}/{name}"
                    else:  # 비교: 골든 관찰값에는 넣지 않는다 (비교 대상이 아님). 같은 번호의 as-is 캡처와 나란히 본다
                        self.tobe_shots[self._step] = str(self._shots / name)
                except PWError:
                    pass
            self.observations.append(obs)
            if self._golden is not None:
                g = next((o for o in self._golden if o["index"] == self._step), None)
                diff = ["! no golden observation for this step"] if g is None else compare(g, obs, self.opts, self.name_map)
                if diff:
                    digest = hashlib.sha256("\n".join(diff).encode()).hexdigest()
                    allowed = next((rule for rule in self.allowed_differences
                                    if rule.get("test") == self.test_id and rule.get("step") == self._step
                                    and rule.get("sha256") == digest and str(rule.get("reason", "")).strip()), None)
                    judge = getattr(self, "_judge", None)  # sha256 으로 허용한 차이(allowed_differences)가 먼저, 그다음 차이 규칙 (rules.Judge)
                    judged = {"verdict": "diff", "notes": {}} if allowed or judge is None else judge.step(
                        diff, test=self.test_id, paths=(urlsplit(g.get("url") or "").path if g else "", urlsplit(obs["url"]).path))
                    if allowed:
                        self.accepted_diffs.append({"step": self._step, "sha256": digest, "reason": allowed["reason"]})
                    elif judged["verdict"] == "accepted":
                        self.accepted_diffs += [{"step": self._step, "sha256": digest, **a} for a in judged["accepted"]]
                    elif judged["verdict"] == "diff":
                        if judged["notes"]:
                            self.row_notes.update(judged["notes"])
                        self.diffs.append((self._step, text, [f"diff sha256: {digest}", *diff]))
        self._step += 1
        self._step_dialogs = []
        self._requests = []

    def goto(self, path: str) -> None:
        if urlsplit(path).netloc and _origin(path) != _origin(self.base_url):
            raise AssertionError(f"goto target is outside configured server: {path}")
        # 주소가 바뀐 화면은 승인된 이름 매핑(path_pairs)으로 to-be 주소를 연다. 단계 글은 as-is 주소 그대로 (테스트 코드·골든과 같게)
        self.page.goto(urljoin(self.base_url.rstrip("/") + "/", tobe_path(path, self.name_map).lstrip("/")))
        self._after("goto", path)

    def click(self, role: str, name: str) -> None:
        self.locate(role, name).click()
        self._after("click", f'{role} "{name}"')

    def fill(self, name: str, value: str, role: str = "textbox", nth: int | None = None) -> None:
        loc = self.locate(role, name, nth)
        if value and (name in self._secret_fields or re.search(r"password|passcode|secret|token|비밀번호|암호|인증번호", name, re.I)
                      or loc.get_attribute("type") == "password"):
            self._secret_values.add(value)
        loc.fill(value)
        self._after("fill", f'{role} "{name}" = {value}')

    def _redact(self, value: str) -> str:
        for secret in self._secret_values:
            value = value.replace(secret, "<redacted>")
        for pattern in self._redact_patterns:
            value = pattern.sub("<redacted>", value)
        return value

    def act(self, role: str, name: str, nth: int | None = None) -> None:
        """역할에 맞는 기본 동작: option은 부모 select에서 선택, checkbox/radio는 체크, 나머지는 클릭 (east2west 러너와 같다)."""
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

    def type(self, name: str, value: str, role: str = "textbox", nth: int | None = None, leave: str | None = "Tab") -> None:
        """사람처럼 친다: 칸을 누르고 비운 뒤 한 글자씩 치고 leave 키(기본 Tab)로 벗어난다. 입력 중 동작을 본다 —
        키를 칠 때 뜨는 창(길이 초과 알림), 입력 마스크·대문자·날짜 정리로 칸에 남은 값, 벗어날 때의 검사(빨간 표시와 그 문구).
        fill 은 값을 한 번에 넣어 키 이벤트·벗어남 검사를 건너뛰므로 이 차이를 못 본다.
        칸의 오류 상태는 aria 스냅샷에 나오지 않아 관찰 줄로 붙인다: `text: 입력 오류 · <이름>: <문구>` (오류가 없으면 줄 없음, FIELD_STATE)."""
        loc = self.locate(role, name, nth)
        if value and (name in self._secret_fields or re.search(r"password|passcode|secret|token|비밀번호|암호|인증번호", name, re.I)
                      or loc.get_attribute("type") == "password"):
            self._secret_values.add(value)
        loc.click()
        loc.fill("")
        self.page.keyboard.type(value, delay=20)
        if leave:
            self.page.keyboard.press(leave)
        self._after("type", f'{role} "{name}" ⌨ {value}' + (f" + {leave}" if leave else ""),
                    extra=lambda: [f"- text: 입력 오류 · {name}: {err or '(문구 없음)'}" for invalid, err in [self._field_state(loc)] if invalid])

    def _field_state(self, loc: Locator) -> tuple[bool, str]:
        """(오류 표시 여부, 오류 문구). 못 읽으면 (False, '')."""
        try:
            got = loc.evaluate(FIELD_STATE)
        except PWError:
            return False, ""
        return bool(got.get("invalid")), self._redact(re.sub(r"\s+", " ", got.get("error") or "").strip())

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
        raise AssertionError(self._redact(f"{what}: got {last!r}"))

    def _assert(self, kind: str, target: str, value: str | None = None) -> None:
        self.assertions.append({"step": self._step, "kind": kind, "target": self._redact(target),
                                "value": self._redact(value) if value is not None else None})

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
        if passed and len(self.observations) != len(self._golden):
            out.append(f"action count changed since as-is recording: {len(self._golden)} → {len(self.observations)}")
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

    def expect_field_error(self, name: str, message: str = "", role: str = "textbox") -> None:
        """칸의 오류 표시와 문구 (벗어날 때의 검사 결과). message="" 는 오류 표시가 없어야 한다는 뜻이다.
        읽는 곳: aria-invalid · aria-errormessage · aria-describedby (표준), ExtJS 4 의 getActiveError · x-form-invalid-field (FIELD_STATE)."""
        self._assert("field_error", name, message)
        def current() -> str:
            invalid, err = self._field_state(self.locate(role, name))
            return (err or "(문구 없음)") if invalid else ""
        self._poll(lambda: (lambda v: (v == message, v))(current()), f'field "{name}" error == {message!r}')

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
        self._assert("url_path", path)  # 기록되는 기대값은 as-is 주소. 비교 때는 이름 매핑의 to-be 주소와 맞춘다
        want = tobe_path(path, self.name_map)
        self._poll(lambda: (lambda p: (p == want, p))(urlparse(self.page.url).path), f"url path == {want!r}")

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
            except PWError:
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
        self._poll(lambda: (fragment in (self._try_snapshot() or ""), None), f"snapshot contains {fragment!r}")

    # -- 마무리 ---------------------------------------------------------------
    def screenshot(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        try:
            self.page.screenshot(path=str(path))
        except PWError:
            pass

    def finish(self, passed: bool) -> None:
        if self.record_dir and passed:
            self.record_dir.mkdir(parents=True, exist_ok=True)
            dest = self.record_dir / "shots" / self.shot_dir
            shutil.rmtree(dest, ignore_errors=True)
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(self._shots), dest)
            (self.record_dir / f"{self.test_id}.json").write_text(
                json.dumps({"test": self.test_id, "base_url": self.base_url, "recorded_at": time.strftime("%Y-%m-%d %H:%M:%S"), "setup": self.setup,
                            "assertions": self.assertions, "steps": self.observations}, ensure_ascii=False, indent=1), encoding="utf-8")
