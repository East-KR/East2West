"""pytest 플러그인: `ui` fixture와 as-is/to-be 비교 옵션. jev-e2e를 설치하면 자동 등록된다 (pyproject의 pytest11 entry point).

uv run pytest e2e/<app> --base-url <as-is> --record golden/<app>     # as-is 골든 기록 → 사람이 `jev-e2e approve golden/<app>`
uv run pytest e2e/<app> --base-url <to-be> --compare golden/<app>    # to-be 비교 (승인된 오라클만)

비교 규칙(마스킹, 이름 매핑)은 오라클 디렉터리 안에만 둔다 (oracle.py). 테스트 코드나 명령행으로 바꿀 수 없다.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

import pytest
from playwright.sync_api import sync_playwright

from jev_e2e.observe import CompareOptions

from . import mutation, oracle
from .ui import UI

_capture: mutation.Capture | None = None


def pytest_addoption(parser):
    g = parser.getgroup("jev-e2e", "as-is/to-be E2E (jev_e2e.pwtest)")
    g.addoption("--base-url", default=os.environ.get("JEV_BASE_URL"), help="대상 앱 기준 URL (기본 JEV_BASE_URL)")
    g.addoption("--record", type=Path, default=None, help="골든 기록 디렉터리 (as-is에서). 기록 후 사람이 jev-e2e approve")
    g.addoption("--compare", type=Path, default=None, help="승인된 오라클 디렉터리와 비교 (to-be에서)")
    g.addoption("--name-map", type=Path, default=None, help="as-is 이름 → to-be 이름 JSON. --compare 디렉터리 안의 파일만")
    g.addoption("--allow-unapproved", action="store_true", help="승인 안 된 오라클로 비교 (결과에 UNAPPROVED로 남는다)")
    g.addoption("--settle-ms", type=int, default=500)
    g.addoption("--headed", action="store_true")
    g.addoption("--storage-state", type=Path, default=None, help="로그인 상태 JSON (Playwright storage_state)")
    g.addoption("--jev-capture", type=Path, default=None, help="(internal: jev-e2e mutate)")
    g.addoption("--jev-mutant", default=None, help="(internal: jev-e2e mutate)")


def pytest_configure(config):
    global _capture
    record, compare = config.getoption("--record"), config.getoption("--compare")
    if record and compare:
        raise pytest.UsageError("--record and --compare are exclusive (record on as-is, compare on to-be)")
    config._jev_oracle = None
    if compare:
        st = oracle.status(compare)
        if not st["ok"] and not config.getoption("--allow-unapproved"):
            raise pytest.UsageError("oracle not approved, comparison refused:\n  " + "\n  ".join(st["problems"])
                                    + f"\nA person reviews and runs `uv run jev-e2e approve {compare}` in a terminal.")
        config._jev_oracle = st
        nm = config.getoption("--name-map")
        if nm and nm.resolve().parent != compare.resolve():
            raise pytest.UsageError(f"--name-map must live in the oracle directory {compare} (it is part of what gets approved)")
    if config.getoption("--jev-capture"):
        _capture = mutation.Capture()


def pytest_sessionfinish(session):
    if _capture is not None:
        _capture.dump(session.config.getoption("--jev-capture"))


def pytest_terminal_summary(terminalreporter, exitstatus, config):
    """기록이 끝나면 승인 검토 화면을 만든다. 사람은 이 화면을 보고 승인한다."""
    record = config.getoption("--record")
    if not record or not record.exists():
        return
    shots = record / "shots"
    if shots.exists():  # 골든 JSON이 없는 테스트의 화면은 지운다 (삭제·이름 바뀐 테스트)
        from .ui import re as _re
        keep = {_re.sub(r"[^\w.-]+", "_", g.stem) for g in record.glob("*.json")}
        for d in shots.iterdir():
            if d.is_dir() and d.name not in keep:
                import shutil
                shutil.rmtree(d, ignore_errors=True)
    from . import html
    page = html.write_review(record)
    terminalreporter.write_line(f"\n승인 검토 화면: {page.resolve().as_uri()}")
    terminalreporter.write_line(f"확인 후 승인 (사람, 터미널): uv run jev-e2e approve {record} --by <이름>")


def pytest_report_header(config):
    st = config._jev_oracle
    if st is None:
        return None
    if st["ok"]:
        return f"oracle: approved by {st['approved_by']} at {st['approved_at']}"
    return "oracle: UNAPPROVED — " + "; ".join(st["problems"])


@pytest.fixture(scope="session", autouse=True)
def _oracle_properties(request, record_testsuite_property):
    st = request.config._jev_oracle
    if request.config.getoption("--base-url"):
        record_testsuite_property("base_url", request.config.getoption("--base-url"))
    if st is not None:
        record_testsuite_property("oracle_approved", str(st["ok"]).lower())
        record_testsuite_property("oracle_approved_by", st.get("approved_by") or "")
        record_testsuite_property("oracle_approved_at", st.get("approved_at") or "")
    mutant = request.config.getoption("--jev-mutant")
    if mutant:
        record_testsuite_property("mutant", mutant)


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_makereport(item, call):
    rep = (yield).get_result()
    if rep.when == "call":
        item.passed_call = rep.passed


@pytest.fixture(scope="session")
def browser(request):
    with sync_playwright() as p:
        b = p.chromium.launch(headless=not request.config.getoption("--headed"))
        yield b
        b.close()


@pytest.fixture
def ui(request, browser):
    cfg = request.config
    base_url = cfg.getoption("--base-url")
    if not base_url:
        pytest.fail("--base-url (or JEV_BASE_URL) is required", pytrace=False)
    if hasattr(request.module, "COMPARE_IGNORE"):
        pytest.fail("COMPARE_IGNORE in test code is not used: put mask rules in <oracle dir>/oracle.json so they are approved", pytrace=False)
    compare, name_map = cfg.getoption("--compare"), cfg.getoption("--name-map")
    ignore = oracle.load_config(compare).get("ignore", []) if compare else []
    state = cfg.getoption("--storage-state")
    ctx = browser.new_context(viewport={"width": 1280, "height": 900}, locale="ko-KR",
                              **({"storage_state": str(state)} if state and state.exists() else {}))
    mutant = cfg.getoption("--jev-mutant")
    if _capture is not None or mutant:
        # 절대 경로::이름: 프로젝트 밖의 테스트 파일도 결함 주입 실행에서 다시 고를 수 있게
        mutation.install(ctx, test=f"{request.node.path}::{request.node.name}", capture=_capture, mutant=json.loads(mutant) if mutant else None)
    u = UI(ctx.new_page(), base_url=base_url, test_id=request.node.name,
           record_dir=cfg.getoption("--record"), compare_dir=compare, compare_opts=CompareOptions(ignore=ignore),
           settle_ms=cfg.getoption("--settle-ms"),
           name_map=json.loads(name_map.read_text(encoding="utf-8")) if name_map else None,
           self_compare=bool(mutant) or _capture is not None)  # 결함 주입은 as-is 자신과 비교하는 것이 목적
    yield u
    passed = getattr(request.node, "passed_call", False)
    if not passed and not mutant:
        u.screenshot(Path("reports") / f"{request.node.name}-fail.png")
    u.finish(passed=passed)
    ctx.close()
    lines = [f"  {d}" for d in u.assertion_drift(passed)]
    for step, text, diff in u.diffs:
        lines.append(f"step {step} {text}: differs from golden")
        lines += [f"    {l}" for l in diff[:12]]
    if lines:
        pytest.fail("\n".join(lines), pytrace=False)
