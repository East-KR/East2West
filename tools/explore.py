"""URL을 열고 상호작용 요소를 상위 컨테이너와 함께 출력한다. 시나리오 설계용.

frame/iframe 안의 요소는 `@frame <이름>`으로 표시한다 (레거시 frameset). 클릭 체인은 모든 프레임에서 찾는다.
"""
import re, sys
from collections import Counter
from playwright.sync_api import sync_playwright

from parity.runner import Runner

import os
url = sys.argv[1]; clicks = [c for c in (sys.argv[2] if len(sys.argv) > 2 else "").split(";") if c]
HEADED = os.environ.get("HEADED") == "1"
UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/140.0.0.0 Safari/537.36"
ROLES = ("link","button","textbox","searchbox","combobox","checkbox","radio","tab","menuitem","option","switch","spinbutton","slider")
CONTAINERS = ("group","dialog","region","navigation","table","list","listbox","menu","tablist","form","complementary","banner","main","contentinfo","grid","rowgroup","row","gridcell","cell","article","tabpanel","toolbar")
LINE = re.compile(r'^(?P<ind>\s*)-\s+(?P<role>[a-z]+)(?:\s+"(?P<name>(?:\\.|[^"\\])*)")?(?P<rest>.*)$')
with sync_playwright() as p:
    b = p.chromium.launch(headless=not HEADED, args=["--disable-blink-features=AutomationControlled"])
    pg = b.new_page(viewport={"width":1280,"height":900}, locale="ko-KR", user_agent=UA)
    try:
        pg.goto(url, wait_until="load", timeout=30000)
    except Exception as e:
        print("goto error:", str(e)[:200])
    pg.wait_for_timeout(2500)
    for click in clicks:
        role, name = click.split(":",1)
        frames = [f for _, f in Runner._frames(pg)]
        if role == "fill":
            loc = next((l for f in frames for l in [f.get_by_role("combobox").or_(f.get_by_role("textbox")).or_(f.get_by_role("searchbox"))] if l.count()), None)
            if loc is None: sys.exit("fill: no input in any frame")
            loc.first.fill(name); pg.keyboard.press("Enter")
        else:
            loc = next((l for f in frames for l in [f.get_by_role(role, name=name, exact=True)] if l.count()), None)
            if loc is None: sys.exit(f"click: {role} {name!r} not found in any frame")
            loc.first.click(timeout=10000)
        pg.wait_for_timeout(2500)
    print("URL:", pg.url, "| TITLE:", pg.title())
    rows = []
    for fkey, snap in Runner()._snapshots(pg):
        stack = []  # (indent, role, name)
        for line in snap.splitlines():
            m = LINE.match(line)
            if not m: continue
            ind = len(m["ind"])
            while stack and stack[-1][0] >= ind: stack.pop()
            role, name = m["role"], (m["name"] or "")
            if role in ROLES and name and "[disabled]" not in m["rest"]:
                ctx = next(((r,n) for i,r,n in reversed(stack) if n), None)
                rows.append((role, name, ctx, fkey))
            if role in CONTAINERS or role in ROLES:
                stack.append((ind, role, name))
    c = Counter((r,n) for r,n,_,_ in rows)
    print("interactive:", len(rows), "| unique:", sum(1 for v in c.values() if v==1), "| frames:", len(Runner._frames(pg)))
    for r,n,ctx,fkey in rows[:int(sys.argv[3]) if len(sys.argv)>3 else 120]:
        dup = f" x{c[(r,n)]}" if c[(r,n)]>1 else ""
        print(f"{r:10s} {n[:60]!r}{dup}" + (f"   ⟵ {ctx[0]} {ctx[1][:40]!r}" if ctx else "") + (f"   @frame {fkey}" if fkey else ""))
    b.close()
