"""스모크 대상 정하기: 화면마다 "조회 버튼"이 어느 버튼인지 작성 시점에 정해 화면 목록에 적는다 (실행 중 Jev 판단 없음).

east2west targets scenarios/<app>/screens.yaml --base-url <as-is> --out scenarios/<app>/screens.targets.yaml

1. 화면을 열기만 한다 (아무것도 누르지 않는다). 보이는 버튼 이름을 모은다.
2. 규칙: 이름이 흔한 조회 버튼 이름과 정확히 같은 버튼이 화면에 하나뿐이면 확정 (by: rule).
3. 나머지는 비워 두고 후보를 적는다 (by: review). Claude Code나 사람이 후보에서 골라 QUERY를 채우고 by: picked로 바꾼다.
결과 파일이 곧 스모크의 대상이다. 사람이 검토할 수 있고, 실행은 결정론적이다.
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any
from urllib.parse import urljoin

import yaml
from playwright.sync_api import sync_playwright

from .runner import UA, Runner

QUERY_NAMES = ["조회", "검색", "찾기", "조회하기", "검색하기", "Search", "Find"]


def build(rows: list[dict[str, Any]], *, base_url: str, names: list[str], storage_state: Path | None = None,
          url_key: str = "URL") -> list[dict[str, Any]]:
    rt = Runner(settle_ms=500)
    out = []
    with sync_playwright() as p:
        browser = p.chromium.launch(args=["--disable-blink-features=AutomationControlled"])
        try:
            for row in rows:
                url = urljoin(base_url.rstrip("/") + "/", str(row[url_key]).lstrip("/"))
                kwargs: dict[str, Any] = {"viewport": {"width": 1280, "height": 900}, "locale": "ko-KR", "user_agent": UA}
                if storage_state and storage_state.exists():
                    kwargs["storage_state"] = str(storage_state)
                ctx = browser.new_context(**kwargs)
                new = {k: v for k, v in row.items() if k not in ("QUERY", "by", "candidates", "note")}
                try:
                    page = ctx.new_page()
                    resp = page.goto(url, wait_until="load")
                    page = rt._settle(page)
                    buttons = []
                    for el in rt._elements(page, editable_only=False):
                        if el.role == "button" and el.name not in buttons:
                            try:
                                if rt._is_visible(page, el):
                                    buttons.append(el.name)
                            except Exception:
                                continue
                    exact = [b for b in buttons if b in names]
                    if resp is not None and resp.status >= 400:
                        new.update({"QUERY": "", "by": "review", "note": f"HTTP {resp.status} on the reference app"})
                    elif len(exact) == 1:
                        new.update({"QUERY": exact[0], "by": "rule"})
                    else:
                        new.update({"QUERY": "", "by": "review", "candidates": buttons,
                                    "note": "no exact query name" if not exact else f"several exact names: {exact}"})
                except Exception as e:
                    new.update({"QUERY": "", "by": "review", "note": f"could not open: {str(e).splitlines()[0][:120]}"})
                finally:
                    ctx.close()
                print(f"  {new['by']:6s} {row.get('SCREEN', url)}: {new['QUERY'] or new.get('candidates') or new.get('note')}", flush=True)
                out.append(new)
        finally:
            browser.close()
    return out


def write(rows: list[dict[str, Any]], out: Path) -> None:
    header = ("# east2west targets가 만든 스모크 대상 목록.\n"
              "#   by: rule   이름 규칙으로 확정\n"
              "#   by: review 사람이나 Claude Code가 candidates에서 조회 버튼을 골라 QUERY를 채우고 by: picked로 바꾼다\n"
              "# QUERY가 빈 행은 스모크에서 '대상 없음'으로 실패한다 (다른 버튼으로 대신 누르지 않는다).\n")
    lines = [yaml.safe_dump([r], allow_unicode=True, sort_keys=False, default_flow_style=None, width=400).strip() for r in rows]
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(header + "\n".join(lines) + "\n", encoding="utf-8")


def main(screens: Path, out: Path, base_url: str | None, names: list[str] | None, storage_state: Path | None) -> int:
    base_url = base_url or os.environ.get("EAST2WEST_BASE_URL")
    if not base_url:
        raise SystemExit("--base-url (or EAST2WEST_BASE_URL) is required")
    rows = yaml.safe_load(screens.read_text(encoding="utf-8")) or []
    result = build(rows, base_url=base_url, names=names or QUERY_NAMES, storage_state=storage_state)
    write(result, out)
    n_rule = sum(r["by"] == "rule" for r in result)
    print(f"\n{n_rule}/{len(result)} settled by rule, {len(result) - n_rule} need review → {out}")
    return 0
