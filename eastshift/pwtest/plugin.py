"""pytest 플러그인: `ui` fixture와 as-is/to-be 비교 옵션. EastShift를 설치하면 자동 등록된다 (pyproject의 pytest11 entry point).

uv run pytest e2e/<app> --base-url <as-is> --record golden/<app>     # as-is 골든 기록 → `eastshift ui`의 시나리오 승인 탭
uv run pytest e2e/<app> --base-url <to-be> --compare golden/<app>    # to-be 비교 (승인된 오라클만). 끝나면 runs/<app>/ 원장에 결과를 남긴다 (ledger.py)

비교 규칙(마스킹, 이름 매핑)은 오라클 디렉터리 안에만 둔다 (oracle.py). 테스트 코드나 명령행으로 바꿀 수 없다.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import time
from pathlib import Path
from urllib.parse import urljoin, urlsplit

import pytest
from playwright.sync_api import sync_playwright

from eastshift.observe import CompareOptions

from . import ledger, mutation, oracle
from .identity import canonical_ids
from .evidence import source_hash
from .ui import UI

_capture: mutation.Capture | None = None
_cases: dict[str, dict] = {}  # --compare 실행의 테스트별 결과 → 실행 원장 (runs/<app>/)
_started = 0.0


def pytest_collection_modifyitems(items):
    ids = canonical_ids([f"{item.path}::{item.name}" for item in items])
    for item in items:
        item._eastshift_id = ids[f"{item.path}::{item.name}"]
        item.user_properties.append(("eastshift_id", item._eastshift_id))
    if items:
        source_dir = Path(os.path.commonpath([str(i.path.parent) for i in items])).resolve()
        items[0].config._eastshift_source_dir = source_dir
        items[0].config._eastshift_source_hash = source_hash(source_dir)


def pytest_addoption(parser):
    g = parser.getgroup("eastshift", "as-is/to-be E2E (eastshift.pwtest)")
    g.addoption("--base-url", default=os.environ.get("EASTSHIFT_BASE_URL"), help="대상 앱 기준 URL (기본 EASTSHIFT_BASE_URL)")
    g.addoption("--record", type=Path, default=None, help="골든 기록 디렉터리 (as-is에서). 기록 후 사람이 eastshift ui 시나리오 승인 탭에서 승인")
    g.addoption("--compare", type=Path, default=None, help="승인된 오라클 디렉터리와 비교 (to-be에서)")
    g.addoption("--name-map", type=Path, default=None, help="as-is 이름 → to-be 이름 JSON. --compare 디렉터리 안의 파일만")
    g.addoption("--allow-unapproved", action="store_true", help="승인 안 된 오라클로 비교 (결과에 UNAPPROVED로 남는다)")
    g.addoption("--settle-ms", type=int, default=500, help="동작 뒤 화면이 이만큼(ms) 안 바뀌고 남은 요청이 없으면 멈춘 것으로 보고 관찰한다 (기본 500)")
    g.addoption("--headed", action="store_true")
    g.addoption("--storage-state", type=Path, default=None, help="로그인 상태 JSON (Playwright storage_state)")
    g.addoption("--reset-path", default=None, help="각 테스트 전 POST할 대상 앱의 초기화 경로, 예: /test/reset")
    g.addoption("--fixed-time", default=None, help="브라우저 시간을 고정할 ISO-8601 시각")
    g.addoption("--jev-capture", type=Path, default=None, help="(internal: eastshift mutate)")
    g.addoption("--jev-mutant", default=None, help="(internal: eastshift mutate)")


def pytest_configure(config):
    global _capture
    record, compare = config.getoption("--record"), config.getoption("--compare")
    if record and compare:
        raise pytest.UsageError("--record and --compare are exclusive (record on as-is, compare on to-be)")
    config._jev_oracle = None
    config._eastshift_setup = {}
    if compare:
        st = oracle.status(compare)
        if not st["ok"] and not config.getoption("--allow-unapproved"):
            raise pytest.UsageError("oracle not approved, comparison refused:\n  " + "\n  ".join(st["problems"])
                                    + "\nA person reviews and approves in `uv run eastshift ui` (시나리오 승인 tab).")
        config._jev_oracle = st
        recorded_setups = {json.dumps(json.loads(p.read_text(encoding="utf-8")).get("setup", {}), sort_keys=True)
                           for p in oracle._golden_files(compare)}
        if len(recorded_setups) > 1:
            raise pytest.UsageError("golden tests have different setup settings; record them with one common reset path and fixed time")
        if recorded_setups:
            config._eastshift_setup = json.loads(next(iter(recorded_setups)))
        nm = config.getoption("--name-map")
        if nm and nm.resolve().parent != compare.resolve():
            raise pytest.UsageError(f"--name-map must live in the oracle directory {compare} (it is part of what gets approved)")
    if config.getoption("--jev-capture"):
        _capture = mutation.Capture()


@pytest.hookimpl(trylast=True)  # junitxml 플러그인이 XML을 다 쓴 뒤에 원장이 그 사본을 챙긴다
def pytest_sessionfinish(session):
    if _capture is not None:
        _capture.dump(session.config.getoption("--jev-capture"))
    cfg = session.config
    compare = cfg.getoption("--compare")
    if compare and _cases and not cfg.getoption("--jev-mutant"):  # 결함 주입 실행은 원장에 남기지 않는다
        junit = cfg.getoption("xmlpath", default=None) or cfg.getoption("--junitxml", default=None)
        out = ledger.write_run(compare.name, target=cfg.getoption("--base-url"), oracle=cfg._jev_oracle or {}, cases=dict(_cases),
                               started=_started, junit=str(junit) if junit else None)
        session.config._jev_ledger = out


def pytest_sessionstart(session):
    global _started
    _started = time.time()


def pytest_terminal_summary(terminalreporter, exitstatus, config):
    """기록이 끝나면 골든 폴더를 정리하고 승인 위치를 알린다. 사람은 통합 화면의 시나리오 승인 탭에서 보고 승인한다."""
    _ledger_summary_line(terminalreporter, config)
    record = config.getoption("--record")
    if not record or not record.exists():
        return
    shots = record / "shots"
    if shots.exists():  # 골든 JSON이 없는 테스트의 화면은 지운다 (삭제·이름 바뀐 테스트)
        keep = {re.sub(r"[^\w.-]+", "_", g.stem) for g in record.glob("*.json")}
        for d in shots.iterdir():
            if d.is_dir() and d.name not in keep:
                shutil.rmtree(d, ignore_errors=True)
    terminalreporter.write_line(f"\n골든 기록: {record}  (시나리오 {len(list(record.glob('*.json')))}개)")
    terminalreporter.write_line("확인 후 승인: uv run eastshift ui → 시나리오 승인 탭 (사람)")


def _ledger_summary_line(terminalreporter, config):
    out = getattr(config, "_jev_ledger", None)
    if out:
        terminalreporter.write_line(f"\n실행 원장: {out}  (eastshift status {config.getoption('--compare')})")


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
        record_testsuite_property("oracle_approval_id", oracle.approval_id(request.config.getoption("--compare")) or "")
        record_testsuite_property("source_sha256", getattr(request.config, "_eastshift_source_hash", ""))
        record_testsuite_property("source_dir", str(getattr(request.config, "_eastshift_source_dir", "")))
    mutant = request.config.getoption("--jev-mutant")
    if mutant:
        record_testsuite_property("mutant", mutant)


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_makereport(item, call):
    rep = (yield).get_result()
    if rep.when == "call":
        item.passed_call = rep.passed
        ui = item.funcargs.get("ui") if hasattr(item, "funcargs") else None
        item.fail_text = "" if rep.passed else (ui._redact(rep.longreprtext or "") if ui else rep.longreprtext or "")
        if not rep.passed and ui:
            rep.longrepr = item.fail_text


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
        pytest.fail("--base-url (or EASTSHIFT_BASE_URL) is required", pytrace=False)
    if hasattr(request.module, "COMPARE_IGNORE"):
        pytest.fail("COMPARE_IGNORE in test code is not used: put mask rules in <oracle dir>/oracle.json so they are approved", pytrace=False)
    compare, name_map = cfg.getoption("--compare"), cfg.getoption("--name-map")
    ignore = oracle.load_config(compare).get("ignore", []) if compare else []
    state = cfg.getoption("--storage-state")
    setup = {**cfg._eastshift_setup, **{k: v for k, v in {"reset_path": cfg.getoption("--reset-path"), "fixed_time": cfg.getoption("--fixed-time")}.items() if v}}
    ctx = browser.new_context(viewport={"width": 1280, "height": 900}, locale="ko-KR",
                              **({"storage_state": str(state)} if state and state.exists() else {}))
    if setup.get("fixed_time"):
        from datetime import datetime
        fixed = setup["fixed_time"]
        parsed = datetime.fromisoformat(fixed.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            pytest.fail("--fixed-time needs an explicit timezone, e.g. 2026-09-27T09:00:00+09:00", pytrace=False)
        ctx.add_init_script(f"""(() => {{ const Original = Date; const instant = new Original({json.dumps(fixed)}).valueOf();
            globalThis.Date = class extends Original {{ constructor(...args) {{ if (args.length) super(...args); else super(instant); }}
            static now() {{ return instant; }} }}; }})()""")
    if setup.get("reset_path"):
        path = setup["reset_path"]
        if not path.startswith("/") or urlsplit(path).netloc:
            pytest.fail("--reset-path must be a relative path starting with /", pytrace=False)
        response = ctx.request.post(urljoin(base_url.rstrip("/") + "/", path.lstrip("/")))
        if not response.ok:
            pytest.fail(f"reset failed: POST {path} returned {response.status}", pytrace=False)
        data_id = response.headers.get("x-eastshift-data-id")
        if cfg.getoption("--compare") and setup.get("dataset_id") and data_id != setup["dataset_id"]:
            pytest.fail(f"reset dataset differs from recording: {data_id or 'header missing'} != {setup['dataset_id']}", pytrace=False)
        if data_id:
            setup["dataset_id"] = data_id
    mutant = cfg.getoption("--jev-mutant")
    if _capture is not None or mutant:
        # 절대 경로::이름: 프로젝트 밖의 테스트 파일도 결함 주입 실행에서 다시 고를 수 있게
        mutation.install(ctx, test=f"{request.node.path}::{request.node.name}", capture=_capture, mutant=json.loads(mutant) if mutant else None)
    test_id = getattr(request.node, "_eastshift_id", request.node.name)
    if compare and not (compare / f"{test_id}.json").exists():
        # Mutant runs select one test at a time. Keep its collision-safe golden ID.
        from hashlib import sha256
        collision_id = f"{sha256(str(request.node.path.resolve()).encode()).hexdigest()[:10]}__{request.node.name}"
        if (compare / f"{collision_id}.json").exists():
            test_id = collision_id
            request.node.user_properties = [(k, test_id if k == "eastshift_id" else v) for k, v in request.node.user_properties]
    u = UI(ctx.new_page(), base_url=base_url, test_id=test_id,
           record_dir=cfg.getoption("--record"), compare_dir=compare, compare_opts=CompareOptions(ignore=ignore),
           settle_ms=cfg.getoption("--settle-ms"),
           name_map=json.loads(name_map.read_text(encoding="utf-8")) if name_map else None,
           self_compare=bool(mutant) or _capture is not None, setup=setup)  # 결함 주입은 as-is 자신과 비교하는 것이 목적
    yield u
    passed = getattr(request.node, "passed_call", False)
    if not passed and not mutant and not (u._secret_values or u._redact_patterns):
        u.screenshot(Path("reports") / f"{test_id}-fail.png")
    u.finish(passed=passed)
    ctx.close()
    drift = u.assertion_drift(passed)
    request.node.user_properties.append(("eastshift_setup", json.dumps(u.setup, ensure_ascii=False, sort_keys=True)))
    request.node.user_properties.append(("eastshift_build_ids", json.dumps(sorted(u._target_build_ids), ensure_ascii=False)))
    if u.accepted_diffs:
        request.node.user_properties.append(("eastshift_accepted_differences", json.dumps(u.accepted_diffs, ensure_ascii=False)))
    lines = [f"  {d}" for d in drift]
    for step, text, diff in u.diffs:
        lines.append(f"step {step} {text}: differs from golden")
        lines += [f"    {l}" for l in diff[:12]]
    if compare and not mutant and _capture is None:
        from . import html as _html
        fail_text = getattr(request.node, "fail_text", "")
        messages = [m for m in (fail_text, "\n".join(lines)) if m]
        if passed and not lines:
            _cases[test_id] = {"status": "pass", "kind": "accepted_diff" if u.accepted_diffs else "same",
                               "summary": "; ".join(x["reason"] for x in u.accepted_diffs), "rows": [],
                               "build_ids": sorted(u._target_build_ids)}
        else:
            kind = "drift" if drift else ("golden_diff" if u.diffs else ("assert" if passed is False and "AssertionError" in fail_text else "error"))
            rows, _ = _html.rows_for(messages)
            first = next((l.strip() for l in fail_text.splitlines() if l.strip().startswith("E ")), "") or (lines[0].strip() if lines else fail_text.strip()[:200])
            _cases[test_id] = {"status": "fail", "kind": kind, "summary": first.removeprefix("E ").strip(), "rows": rows[:12],
                               "screenshot": f"reports/{test_id}-fail.png" if not passed and not (u._secret_values or u._redact_patterns) else "",
                               "build_ids": sorted(u._target_build_ids)}
    if lines:
        pytest.fail("\n".join(lines), pytrace=False)
