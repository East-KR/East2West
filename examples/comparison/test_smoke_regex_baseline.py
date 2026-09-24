"""scenarios/smoke/screen_smoke.yaml 과 같은 스모크를 Playwright로. 화면별 정보 없이 일반 규칙으로 쓴 버전 (LLM이 흔히 쓰는 방식).

조회 버튼 = 이름이 조회/검색/찾기/search 를 포함하는 첫 번째 버튼. 첫 항목 = 데이터 행 첫 줄의 첫 링크.
"""
import re
from pathlib import Path

import pytest
import yaml

SCREENS = yaml.safe_load((Path(__file__).parents[2] / "scenarios/smoke/screens.yaml").read_text(encoding="utf-8"))
QUERY_BUTTON = re.compile(r"조회|검색|찾기|search", re.I)


@pytest.mark.parametrize("screen", SCREENS, ids=[s["URL"].strip("/") for s in SCREENS])
def test_screen_smoke(browser, request, screen):
    base = request.config.getoption("--base-url").rstrip("/")
    ctx = browser.new_context(locale="ko-KR")
    page = ctx.new_page()
    events = {"http": [], "js": [], "dialogs": []}
    page.on("response", lambda r: r.request.is_navigation_request() and r.status >= 400 and events["http"].append(f"{r.status} {r.url}"))
    page.on("pageerror", lambda e: events["js"].append(str(e)))
    page.on("dialog", lambda d: (events["dialogs"].append(d.message), d.accept()))
    try:
        page.goto(base + screen["URL"])
        assert not events["http"] and not events["js"], events

        page.get_by_role("button", name=QUERY_BUTTON).first.click(timeout=5000)
        page.wait_for_timeout(500)
        assert not events["dialogs"] and not events["js"], events
        rows = page.get_by_role("row").filter(has=page.get_by_role("cell"))
        assert rows.count() >= 1, "no data rows after query"

        rows.first.get_by_role("link").first.click(timeout=5000)
        page.wait_for_load_state()
        assert not events["http"], events
        assert "상세" in page.title()
    finally:
        ctx.close()
