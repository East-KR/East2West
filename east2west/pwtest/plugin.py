"""pytest 플러그인: `ui` fixture와 as-is/to-be 비교 옵션. East2West를 설치하면 자동 등록된다 (pyproject의 pytest11 entry point).

uv run pytest e2e/<app> --base-url <as-is> --record golden/<app>     # as-is 골든 기록. 끝나면 자동 승인 (oracle.auto_approve)
uv run pytest e2e/<app> --base-url <to-be> --compare golden/<app>    # to-be 비교 (승인본과 다르면 먼저 자동 승인). 끝나면 runs/<app>/ 원장에 결과를 남긴다 (ledger.py)
uv run pytest e2e/<app> --base-url <to-be> --compare golden/<app> -n 4   # 브라우저 4개 (pytest-xdist). 워커의 결과를 컨트롤러가 모아 원장 하나로 남긴다

비교는 단계마다 to-be 캡처를 남긴다: ui 가 임시 폴더에 찍고(tobe_shots), 케이스에 {단계: 경로}로 적고, 원장(ledger.write_run)이 사본 폴더로 복사한 뒤
이 플러그인이 임시 폴더를 지운다. xdist 워커의 임시 폴더도 같은 기계라 컨트롤러가 그대로 복사한다. 결함 주입 실행은 찍지 않는다.

비교 규칙(마스킹, 이름 매핑)은 오라클 디렉터리 안에만 둔다 (oracle.py). 테스트 코드나 명령행으로 바꿀 수 없다.
승인 확인(oracle.precheck: 골든 전체의 해시 대조, 기록 설정)은 한 번만 한다: xdist 컨트롤러가 워커에 넘기고(pytest_configure_node),
east2west mutate가 띄운 pytest는 그 결과 파일(oracle.PRECHECK 환경 변수)을 받는다. 같은 오라클 폴더·같은 승인본의 것일 때만 쓴다.
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
from _pytest.junitxml import xml_key
from playwright.sync_api import sync_playwright

from east2west.observe import CompareOptions

from . import ledger, mutation, oracle, rules
from .identity import canonical_ids
from .evidence import source_hash
from .ui import UI

_capture: mutation.Capture | None = None
_cases: dict[str, dict] = {}  # --compare 실행의 테스트별 결과 → 실행 원장 (runs/<app>/)
_started = 0.0


def pytest_collection_modifyitems(items):
    ids = canonical_ids([f"{item.path}::{item.name}" for item in items])
    for item in items:
        item._east2west_id = ids[f"{item.path}::{item.name}"]
        item.user_properties.append(("east2west_id", item._east2west_id))
    if items:
        source_dir = Path(os.path.commonpath([str(i.path.parent) for i in items])).resolve()
        items[0].config._east2west_source_dir = source_dir
        items[0].config._east2west_source_hash = source_hash(source_dir)


def pytest_addoption(parser):
    g = parser.getgroup("east2west", "as-is/to-be E2E (east2west.pwtest)")
    g.addoption("--base-url", default=os.environ.get("EAST2WEST_BASE_URL"), help="대상 앱 기준 URL (기본 EAST2WEST_BASE_URL)")
    g.addoption("--record", type=Path, default=None, help="골든 기록 디렉터리 (as-is에서). 기록이 끝나면 자동 승인")
    g.addoption("--compare", type=Path, default=None, help="오라클 디렉터리와 비교 (to-be에서). 승인본과 다르면 먼저 자동 승인")
    g.addoption("--name-map", type=Path, default=None, help="as-is 이름 → to-be 이름 JSON. --compare 디렉터리 안의 파일만")
    g.addoption("--settle-ms", type=int, default=500, help="동작 뒤 화면이 이만큼(ms) 안 바뀌고 남은 요청이 없으면 멈춘 것으로 보고 관찰한다 (기본 500)")
    g.addoption("--headed", action="store_true")
    g.addoption("--storage-state", type=Path, default=None, help="로그인 상태 JSON (Playwright storage_state)")
    g.addoption("--reset-path", default=None, help="각 테스트 전 POST할 대상 앱의 초기화 경로, 예: /test/reset")
    g.addoption("--fixed-time", default=None, help="브라우저 시간을 고정할 ISO-8601 시각")
    g.addoption("--jev-capture", type=Path, default=None, help="(internal: east2west mutate)")
    g.addoption("--jev-mutant", default=None, help="(internal: east2west mutate)")


def pytest_configure(config):
    global _capture
    record, compare = config.getoption("--record"), config.getoption("--compare")
    if record and compare:
        raise pytest.UsageError("--record and --compare are exclusive (record on as-is, compare on to-be)")
    config._jev_oracle = None
    config._east2west_setup = {}
    if compare:
        given = oracle.given_precheck(compare, _handed_precheck(config))
        if given is None and not hasattr(config, "workerinput"):  # 컨트롤러(또는 단독)만: 승인본과 다르면 먼저 도장 (워커는 넘겨받은 것을 쓴다)
            oracle.auto_approve(compare)
        pre = given or oracle.precheck(compare)
        config._east2west_precheck = pre
        st = pre["status"]
        if not st["ok"]:  # 골든 폴더가 없거나 도장을 찍지 못했다
            raise pytest.UsageError("oracle not approved, comparison refused:\n  " + "\n  ".join(st["problems"]))
        config._jev_oracle = st
        if pre["setup_error"]:
            raise pytest.UsageError(pre["setup_error"])
        if pre.get("config_error"):  # 틀린 차이 규칙으로 비교하면 결함을 덮을 수 있다 (rules.py)
            raise pytest.UsageError("oracle rules are invalid, comparison refused:\n  " + pre["config_error"].replace("\n", "\n  "))
        config._east2west_setup = pre["setup"]
        nm = config.getoption("--name-map")
        if nm and nm.resolve().parent != compare.resolve():
            raise pytest.UsageError(f"--name-map must live in the oracle directory {compare} (it is part of what gets approved)")
    if config.getoption("--jev-capture"):
        _capture = mutation.Capture()


def _handed_precheck(config) -> dict | None:
    """이미 확인한 승인 상태: xdist 워커는 컨트롤러가 넘긴 것, east2west mutate가 띄운 pytest는 그 파일 (oracle.PRECHECK)."""
    given = (getattr(config, "workerinput", None) or {}).get("east2west_precheck")
    if given is None and os.environ.get(oracle.PRECHECK):
        try:
            given = json.loads(Path(os.environ[oracle.PRECHECK]).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            given = None
    return given


@pytest.hookimpl(optionalhook=True)
def pytest_configure_node(node):
    """xdist 컨트롤러: 워커마다 골든 전체를 다시 해시하지 않게, 컨트롤러가 확인한 승인 상태를 넘긴다."""
    pre = getattr(node.config, "_east2west_precheck", None)
    if pre is not None:
        node.workerinput["east2west_precheck"] = pre


@pytest.hookimpl(trylast=True)  # junitxml 플러그인이 XML을 다 쓴 뒤에 원장이 그 사본을 챙긴다
def pytest_sessionfinish(session):
    if _capture is not None:
        _capture.dump(session.config.getoption("--jev-capture"))
    cfg = session.config
    if hasattr(cfg, "workerinput"):  # xdist 워커: 결과를 컨트롤러에 넘기고 원장은 컨트롤러가 쓴다 (pytest_testnodedown)
        cfg.workeroutput["east2west_cases"] = dict(_cases)
        cfg.workeroutput["east2west_source_hash"] = getattr(cfg, "_east2west_source_hash", "")
        cfg.workeroutput["east2west_source_dir"] = str(getattr(cfg, "_east2west_source_dir", ""))
        return
    compare = cfg.getoption("--compare")
    if compare and _cases and not cfg.getoption("--jev-mutant"):  # 결함 주입 실행은 원장에 남기지 않는다
        junit = cfg.getoption("xmlpath", default=None) or cfg.getoption("--junitxml", default=None)
        tmp = {Path(p).parent for c in _cases.values() for p in (c.get("tobe_shots") or {}).values()}  # 단계 캡처의 임시 폴더 (xdist 워커 것도 같은 기계)
        out = ledger.write_run(compare.name, target=cfg.getoption("--base-url"), oracle=cfg._jev_oracle or {}, cases=dict(_cases),
                               started=_started, junit=str(junit) if junit else None)
        session.config._jev_ledger = out
        for d in tmp:  # 원장 사본 폴더로 복사했으니 임시 폴더는 지운다 (ui 가 만든 것만)
            if d.name.startswith("east2west-shots-"):
                shutil.rmtree(d, ignore_errors=True)


def pytest_sessionstart(session):
    global _started
    _started = time.time()


@pytest.hookimpl(optionalhook=True)
def pytest_testnodedown(node, error):
    """xdist 컨트롤러: 워커가 끝날 때마다 그 워커의 비교 결과를 모은다 (원장 하나로). 소스 해시는 수집을 한 워커가 계산했으므로 여기서 받고,
    testsuite 속성도 여기서 컨트롤러의 JUnit에 붙인다 (워커의 record_testsuite_property는 컨트롤러 XML에 오지 않는다)."""
    out = getattr(node, "workeroutput", None) or {}
    _cases.update(out.get("east2west_cases", {}))
    cfg = node.config
    if "east2west_source_hash" in out and not getattr(cfg, "_east2west_props_done", False):
        cfg._east2west_source_hash, cfg._east2west_source_dir = out["east2west_source_hash"], Path(out["east2west_source_dir"])
        xml = cfg.stash.get(xml_key, None)
        if xml is not None:
            for k, v in _suite_properties(cfg):
                xml.add_global_property(k, v)
        cfg._east2west_props_done = True


def _suite_properties(config) -> list[tuple[str, str]]:
    """JUnit testsuite 속성: 어느 대상·어느 승인본·어느 코드로 돌렸는지 (보고서가 증거 유효성을 이걸로 판정한다)."""
    st = config._jev_oracle
    out = []
    if config.getoption("--base-url"):
        out.append(("base_url", config.getoption("--base-url")))
    if st is not None:
        out += [("oracle_approved", str(st["ok"]).lower()), ("oracle_approved_by", st.get("approved_by") or ""),
                ("oracle_approved_at", st.get("approved_at") or ""),
                ("oracle_approval_id", oracle.approval_id(config.getoption("--compare")) or ""),
                ("source_sha256", getattr(config, "_east2west_source_hash", "")),
                ("source_dir", str(getattr(config, "_east2west_source_dir", "")))]
    mutant = config.getoption("--jev-mutant")
    if mutant:
        out.append(("mutant", mutant))
    return out


def pytest_terminal_summary(terminalreporter, exitstatus, config):
    """기록이 끝나면 골든 폴더를 정리하고 자동 승인한다 (컨트롤러만: 이 훅은 xdist 워커에서 돌지 않는다)."""
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
    memo = oracle.auto_approve(record)
    terminalreporter.write_line(f"자동 승인: {memo}" if memo else "승인: 승인본과 같다 (다시 찍지 않음)")


def _ledger_summary_line(terminalreporter, config):
    out = getattr(config, "_jev_ledger", None)
    if out:
        terminalreporter.write_line(f"\n실행 원장: {out}  (east2west status {config.getoption('--compare')})")


def pytest_report_header(config):
    st = config._jev_oracle  # --compare 는 승인되지 않은 오라클이면 시작하지 않는다 (pytest_configure)
    return None if st is None else f"oracle: approved by {st['approved_by']} at {st['approved_at']}"


@pytest.fixture(scope="session", autouse=True)
def _oracle_properties(request, record_testsuite_property):
    if hasattr(request.config, "workerinput"):
        return  # xdist 워커의 testsuite 속성은 컨트롤러 XML에 오지 않는다. 컨트롤러가 pytest_testnodedown 에서 붙인다
    for k, v in _suite_properties(request.config):
        record_testsuite_property(k, v)


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
        pytest.fail("--base-url (or EAST2WEST_BASE_URL) is required", pytrace=False)
    if hasattr(request.module, "COMPARE_IGNORE"):
        pytest.fail("COMPARE_IGNORE in test code is not used: put mask rules in <oracle dir>/oracle.json so they are approved", pytrace=False)
    compare, name_map = cfg.getoption("--compare"), cfg.getoption("--name-map")
    ignore = oracle.load_config(compare).get("ignore", []) if compare else []
    state = cfg.getoption("--storage-state")
    setup = {**cfg._east2west_setup, **{k: v for k, v in {"reset_path": cfg.getoption("--reset-path"), "fixed_time": cfg.getoption("--fixed-time")}.items() if v}}
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
    if setup.get("reset_path") and (getattr(cfg, "workerinput", None) or {}).get("workercount", 1) > 1:
        pytest.fail("--reset-path resets the server before every test; with -n workers they would wipe each other's data. "
                    "Run without -n, or give each worker its own server", pytrace=False)
    if setup.get("reset_path"):
        path = setup["reset_path"]
        if not path.startswith("/") or urlsplit(path).netloc:
            pytest.fail("--reset-path must be a relative path starting with /", pytrace=False)
        response = ctx.request.post(urljoin(base_url.rstrip("/") + "/", path.lstrip("/")))
        if not response.ok:
            pytest.fail(f"reset failed: POST {path} returned {response.status}", pytrace=False)
        data_id = response.headers.get("x-east2west-data-id")
        if cfg.getoption("--compare") and setup.get("dataset_id") and data_id != setup["dataset_id"]:
            pytest.fail(f"reset dataset differs from recording: {data_id or 'header missing'} != {setup['dataset_id']}", pytrace=False)
        if data_id:
            setup["dataset_id"] = data_id
    mutant = cfg.getoption("--jev-mutant")
    if _capture is not None or mutant:
        # 절대 경로::이름: 프로젝트 밖의 테스트 파일도 결함 주입 실행에서 다시 고를 수 있게
        mutation.install(ctx, test=f"{request.node.path}::{request.node.name}", capture=_capture, mutant=json.loads(mutant) if mutant else None)
    test_id = getattr(request.node, "_east2west_id", request.node.name)
    if compare and not (compare / f"{test_id}.json").exists():
        # Mutant runs select one test at a time. Keep its collision-safe golden ID.
        from hashlib import sha256
        collision_id = f"{sha256(str(request.node.path.resolve()).encode()).hexdigest()[:10]}__{request.node.name}"
        if (compare / f"{collision_id}.json").exists():
            test_id = collision_id
            request.node.user_properties = [(k, test_id if k == "east2west_id" else v) for k, v in request.node.user_properties]
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
    request.node.user_properties.append(("east2west_setup", json.dumps(u.setup, ensure_ascii=False, sort_keys=True)))
    request.node.user_properties.append(("east2west_build_ids", json.dumps(sorted(u._target_build_ids), ensure_ascii=False)))
    if u.accepted_diffs:
        request.node.user_properties.append(("east2west_accepted_differences", json.dumps(u.accepted_diffs, ensure_ascii=False)))
    if u.row_notes:  # 다른 점으로 남은 단계에서 차이 규칙에 맞은 행: 보고서·실행 탭이 행 옆에 갈래를 보인다
        request.node.user_properties.append(("east2west_row_classes", json.dumps([[*row, n["class"], n["reason"]] for row, n in u.row_notes.items()], ensure_ascii=False)))
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
                               "summary": "; ".join(rules.describe(x) for x in u.accepted_diffs), "rows": [],
                               "build_ids": sorted(u._target_build_ids),
                               **({"accepted": u.accepted_diffs} if u.accepted_diffs else {})}  # 단계·갈래·사유·규칙 (옛 원장에는 없다)
        else:
            kind = "drift" if drift else ("golden_diff" if u.diffs else ("assert" if passed is False and "AssertionError" in fail_text else "error"))
            rows, _ = _html.rows_for(messages)
            first = next((l.strip() for l in fail_text.splitlines() if l.strip().startswith("E ")), "") or (lines[0].strip() if lines else fail_text.strip()[:200])
            _cases[test_id] = {"status": "fail", "kind": kind, "summary": first.removeprefix("E ").strip(), "rows": rows[:12],
                               "screenshot": f"reports/{test_id}-fail.png" if not passed and not (u._secret_values or u._redact_patterns) else "",
                               "build_ids": sorted(u._target_build_ids)}
            notes = [u.row_notes.get(tuple(r)) for r in rows[:12]]  # 차이 규칙에 맞은 행의 갈래·사유 (rows 와 나란히, 옛 원장에는 없다)
            if any(notes):
                _cases[test_id]["row_classes"] = notes
            if u.accepted_diffs:  # 다른 단계는 실패했어도 승인된 단계는 남긴다 (as-is 이상 동작의 '고객 결정대로 바꿈' 판단)
                _cases[test_id]["accepted"] = u.accepted_diffs
        # 단계별 to-be 캡처 (ui.tobe_shots, 임시 폴더): 원장이 사본 폴더로 복사한다. 비밀번호 같은 값을 넣은 테스트·가림 정규식이 있으면 남기지 않는다 (실패 순간 화면과 같은 규칙)
        if u._shots is not None:
            if u._secret_values or u._redact_patterns:
                shutil.rmtree(u._shots, ignore_errors=True)
            else:
                _cases[test_id]["tobe_shots"] = {str(k): v for k, v in sorted(u.tobe_shots.items())}
    if lines:
        pytest.fail("\n".join(lines), pytrace=False)
