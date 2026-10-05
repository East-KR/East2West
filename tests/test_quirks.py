"""as-is 이상 동작(quirks.py): 기록·결정 저장 검사와 이력, to-be 상태 계산, 결정 → 판정 규칙의 범위, 통합 화면 경로(같은 출처만), 작업 목록, CLI."""
import json
import threading
from http.server import ThreadingHTTPServer
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import pytest

from east2west import cli
from east2west.observe import CompareOptions, observation
from east2west.pwtest import hub, ledger, quirks, rules

RECORDS = [
    {"id": "Q-01", "kind": "quirk", "title": "회차를 비우면 빈 '실패' 창", "severity": "high", "strange": "글자 없는 실패 창", "expected": "전체 회차 조회",
     "impact": "이력을 못 본다", "repro": ["화면을 연다", "조회"], "observed": "빈 창", "cause": "int 바인딩 실패", "evidence": ["Ctl.java:49"],
     "verified": "run", "tests": ["test_a", "test_b"], "routes": ["/regulations"], "match": "알림창",
     "options": ["현행 유지: 빈 창", "수정: 전체 회차로 조회"], "shot": {"test": "test_a", "step": 1, "caption": "빈 실패 창"}},
    {"id": "Q-02", "kind": "quirk", "title": "0 을 넣으면 조건이 빠진다", "severity": "mid", "tests": ["test_b"]},
    {"id": "Q-03", "kind": "quirk", "title": "기록만 있고 테스트가 없다", "severity": "low"},
    {"id": "E-01", "kind": "env", "title": "로컬은 처리상태 코드가 없다", "severity": "low", "tests": ["test_a"]},
]
KEEP = {"decision": "keep", "confirmed_by": "고객 김", "confirmed_at": "2026-10-04"}
CHANGE = {"decision": "change", "direction": "회차를 비우면 전체 회차로 조회한다", "confirmed_by": "고객 김", "confirmed_at": "2026-10-04", "memo": "운영 반영"}


@pytest.fixture
def proj(tmp_path, monkeypatch):
    """임시 프로젝트 루트: quirks/, runs/, golden/app (테스트 셋 · 단계 캡처)."""
    monkeypatch.setattr(quirks, "ROOT", tmp_path / "quirks")
    monkeypatch.setattr(ledger, "RUNS", tmp_path / "runs")
    (tmp_path / "quirks").mkdir()
    (tmp_path / "quirks" / "app.json").write_text(json.dumps(RECORDS, ensure_ascii=False), encoding="utf-8")
    g = tmp_path / "golden" / "app"
    for t in ("test_a", "test_b", "test_c"):
        steps = []
        for i in range(2):
            o = observation(index=i, kind="goto", text=f"/p{i}", url=f"http://asis/p{i}", title="", snapshot="- text: 화면\n", dialogs=[], opts=CompareOptions())
            o["shot"] = f"shots/{t}/{i:02d}.jpg"
            (g / "shots" / t).mkdir(parents=True, exist_ok=True)
            (g / "shots" / t / f"{i:02d}.jpg").write_bytes(b"\xff\xd8\xff\xe0asis")
            steps.append(o)
        (g / f"{t}.json").write_text(json.dumps({"test": t, "base_url": "http://asis", "setup": {}, "assertions": [], "steps": steps}), encoding="utf-8")
    return tmp_path


def test_save_decision_checks_and_keeps_history(proj):
    for body, why in (({"decision": "maybe"}, "decision"), ({"decision": "change", **{k: v for k, v in CHANGE.items() if k != "direction"}}, "수정 방향"),
                      ({**KEEP, "confirmed_at": "10/04"}, "YYYY-MM-DD")):  # 확인자·확인일은 받지 않는다(사용자 결정 2026-10-04) — 예전 값의 형식만 본다
        with pytest.raises(ValueError, match=why):
            quirks.save_decision("app", "Q-01", body)
    with pytest.raises(ValueError, match="모르는 기록"):
        quirks.save_decision("app", "Q-99", KEEP)
    with pytest.raises(ValueError, match="환경 차이"):
        quirks.save_decision("app", "E-01", KEEP)
    assert quirks.save_decision("app", "Q-02", {"decision": "hold"})["decision"] == "hold"
    assert quirks.save_decision("app", "Q-03", {"decision": "keep"})["decision"] == "keep"  # 확인자·확인일 없이도 저장된다
    first = quirks.save_decision("app", "Q-01", KEEP, now=0)
    assert first["history"] == [] and first["updated_at"]
    assert quirks.save_decision("app", "Q-01", KEEP, now=999) == first  # 같은 값이면 이력을 늘리지 않는다
    second = quirks.save_decision("app", "Q-01", CHANGE)
    assert second["decision"] == "change" and second["history"][-1]["decision"] == "keep" and second["history"][-1]["updated_at"] == first["updated_at"]
    assert json.loads((proj / "quirks" / "app.decisions.json").read_text(encoding="utf-8"))["Q-01"]["direction"] == CHANGE["direction"]


def test_decision_rules_only_for_change_and_only_in_linked_tests(proj):
    quirks.save_decision("app", "Q-01", CHANGE)
    quirks.save_decision("app", "Q-02", KEEP)
    (proj / "quirks" / "app.decisions.json").write_text(json.dumps({**quirks.decisions("app"), "Q-03": CHANGE, "E-01": CHANGE}, ensure_ascii=False), encoding="utf-8")
    made = quirks.decision_rules("app")
    assert [(r["quirk"], r["tests"], r["what"]) for r in made] == [("Q-01", ["test_a", "test_b"], "알림창")]  # 현행 유지·환경·범위 없는 기록은 규칙 없음
    assert made[0]["class"] == "customer" and "고객 김 2026-10-04" in made[0]["reason"] and CHANGE["direction"] in made[0]["reason"]
    assert any("Q-03" in p and "규칙을 만들 수 없습니다" for p in quirks.problems("app"))
    d = proj / "golden" / "app"
    (d / "oracle.json").write_text(json.dumps({"difference_rules": [{"class": "env", "reason": "자료", "what": "건수"}]}), encoding="utf-8")
    loaded = rules.load(d)
    assert [r["class"] for r in loaded] == ["env", "customer"]  # 승인된 oracle.json 규칙이 먼저
    j = lambda test: rules.Judge(loaded).step(["dialogs: ['alert: 실패'] → []"], test=test)["verdict"]
    assert j("test_a") == "accepted" and j("test_c") == "diff"  # 이어진 테스트 밖은 결함 그대로
    (proj / "quirks" / "app.decisions.json").write_text("{broken", encoding="utf-8")
    assert quirks.decision_rules("app") == []  # 깨진 결정 파일: 규칙 없이 비교 (덮는 것보다 결함으로 남는 쪽이 안전)


def _run(cases):
    ledger.write_run("app", target="http://tobe", oracle={"ok": True}, started=0, cases=cases)  # 같은 초면 -2, -3 … 이 뒤에 온다


def test_state_precedence_and_change_decisions(proj):
    items = quirks.load("app")
    assert {k: v["tobe"] for k, v in quirks.state("app", items).items()} == {"Q-01": "none", "Q-02": "none", "Q-03": "untracked", "E-01": "none"}  # Q-03: 이어진 테스트가 없다 (실행이 있어도 알 수 없다)
    _run({"test_a": {"status": "pass", "kind": "same", "rows": []},
          "test_b": {"status": "fail", "kind": "golden_diff", "summary": "step 1", "rows": [["합계", "1", "2"]]}})
    st = quirks.state("app", items)
    assert st["Q-01"]["tobe"] == "same"  # match(알림창) 행이 없는 실패는 이 동작과 무관 → unknown, 같은 것이 하나 있으니 same
    assert st["Q-02"]["tobe"] == "diff" and st["Q-02"]["open"] == ["test_b: 합계"]
    _run({"test_b": {"status": "pass", "kind": "accepted_diff", "summary": "자료", "rows": [],
                     "accepted": [{"step": 1, "class": "env", "reason": "자료", "rule": {"class": "env"}}]}})
    assert quirks.state("app", items)["Q-02"]["tobe"] == "unknown"  # 결함 아닌 갈래로만 승인 = 비교가 서지 않음
    quirks.save_decision("app", "Q-01", CHANGE)
    assert quirks.state("app", items)["Q-01"]["tobe"] == "pending"  # 수정으로 정했는데 to-be 가 아직 as-is 와 같다
    cust = {"step": 1, "class": "customer", "reason": "고객 결정", "rule": {"class": "customer", "quirk": "Q-01"}}
    _run({"test_a": {"status": "pass", "kind": "accepted_diff", "summary": "고객", "rows": [], "accepted": [cust]}})
    assert quirks.state("app", items)["Q-01"]["tobe"] == "changed"
    _run({"test_a": {"status": "fail", "kind": "golden_diff", "summary": "s", "rows": [["알림창", "실패", "(없음)"]]}})
    assert quirks.state("app", items)["Q-01"]["tobe"] == "diff"  # 규칙에 안 걸린 알림창 차이가 남았다
    quirks.save_decision("app", "Q-01", KEEP)
    _run({"test_a": {"status": "fail", "kind": "golden_diff", "summary": "s", "rows": [["알림창", "실패", "(없음)"], ["합계", "1", "2"]],
                     "row_classes": [{"class": "customer", "reason": "고객", "quirk": "Q-01"}, None]}})
    assert quirks.state("app", items)["Q-01"]["tobe"] == "diff"  # 현행 유지로 바꿨으면 고객 결정 차이도 다름이다


def test_capture_paths_from_golden_and_latest_run(proj):
    shots = proj / "runs" / "app" / "s"
    _run({"test_a": {"status": "fail", "kind": "golden_diff", "summary": "s", "rows": [], "screenshot": "",
                     "tobe_shots": {"1": str(shots / "test_a-tobe-01.jpg")}}})
    stamp = ledger.load_runs("app")[-1]["_stamp"]
    folder = proj / "runs" / "app" / stamp / "shots"
    folder.mkdir(parents=True)
    (folder / "test_a-tobe-01.jpg").write_bytes(b"\xff\xd8tobe")  # 원장 경로가 옮겨졌어도 실행 사본 폴더에서 찾는다
    d = quirks.data("app", proj / "golden" / "app")
    q1 = next(x for x in d["items"] if x["id"] == "Q-01")
    assert q1["shots"]["asis"].startswith("/file?p=") and "test_a-tobe-01.jpg" in q1["shots"]["tobe"] and not q1["shots"]["tobe_fail"]
    assert [(f["step"], bool(f["asis"]), bool(f["tobe"])) for f in q1["captures"]] == [(0, True, False), (1, True, True)]
    embedded = quirks.data("app", proj / "golden" / "app", embed=True)
    e1 = next(x for x in embedded["items"] if x["id"] == "Q-01")
    assert e1["shots"]["asis"].startswith("data:image/jpeg;base64,") and len(e1["captures"]) == 2 and "## " in embedded["md"]  # 고객용도 모든 단계
    page = quirks.standalone("app", proj / "golden" / "app")  # 같은 그림은 한 번만 싣고 열쇠로 가리킨다
    one = e1["captures"][0]["asis"]
    assert page.count(one) == 1 and '"#img0"' in page and "D.imgs" in page


def test_markdown_work_list_and_cli(proj, monkeypatch, capsys):
    quirks.save_decision("app", "Q-01", CHANGE)
    quirks.save_decision("app", "Q-02", KEEP)
    md = quirks.decisions_md("app")
    assert "전체 3건 · 수정 1 · 현행 유지 1 · 보류 0 · 결정 전 1 · 환경 차이 1건 별도" in md
    change = md.split("## 수정 — 소스를 바꿀 것 (1)")[1].split("\n## ")[0]
    for part in ("Q-01", CHANGE["direction"], "고객 김 · 2026-10-04", "운영 반영", "int 바인딩 실패", "`Ctl.java:49`", "테스트 test_a, test_b", "/알림창/"):
        assert part in change, part
    assert "## 판정 규칙 (결정에서 자동 · 다음 to-be 비교부터)\n\n- Q-01: 고객 결정(수정)" in md
    monkeypatch.chdir(proj)
    assert cli.main(["quirks", "golden/app"]) == 0
    out = capsys.readouterr().out
    assert "# as-is 이상 동작" in out and "- Q-01: 고객 결정(수정)" in out
    assert cli.main(["quirks", "nope"]) == 1


def _serve(h):
    srv = ThreadingHTTPServer(("127.0.0.1", 0), type("H", (hub.Handler,), {"hub": h}))
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, f"http://127.0.0.1:{srv.server_address[1]}"


def test_hub_tab_save_and_exports(proj, monkeypatch):
    monkeypatch.chdir(proj)
    h = hub.Hub(Path("golden"), Path("e2e"), projects_file=proj / "east2west.json")
    srv, base = _serve(h)
    try:
        frag = json.loads(urlopen(base + "/page/app/quirks?fragment=1").read())
        assert frag["kind"] == "quirks" and "판정에 반영" in frag["html"] and "qdata" in frag["html"] and frag["js"]
        assert json.loads(urlopen(base + "/api/app/app").read())["quirks"] == 3
        post = lambda body, origin=base: urlopen(Request(base + "/api/app/app/quirks/Q-01", data=json.dumps(body).encode(), method="POST",
                                                         headers={"Content-Type": "application/json", **({"Origin": origin} if origin else {})}))
        with pytest.raises(HTTPError) as ex:
            post(CHANGE, "http://evil.example")
        assert ex.value.code == 403 and not (proj / "quirks" / "app.decisions.json").exists()  # 다른 출처의 POST 는 결정을 못 쓴다
        with pytest.raises(HTTPError) as ex:
            post({"decision": "change"})
        assert ex.value.code == 409 and "수정 방향" in json.loads(ex.value.read())["error"]
        assert json.loads(post(CHANGE).read())["decision"] == "change"
        assert quirks.decisions("app")["Q-01"]["confirmed_by"] == "고객 김"
        assert "고객 결정(수정)" in json.loads(urlopen(base + "/page/app/quirks?fragment=1").read())["html"]  # 저장하면 화면(규칙 목록)이 새로 만들어진다
        r = urlopen(base + "/api/app/app/quirks.md")
        assert r.headers["Content-Disposition"] == 'attachment; filename="asis-quirks-app.md"' and "수정 — 소스를 바꿀 것" in r.read().decode()
        r = urlopen(base + "/api/app/app/quirks.html")
        page = r.read().decode()
        assert "attachment" in r.headers["Content-Disposition"] and "data:image/jpeg;base64," in page and "/file?p=" not in page
    finally:
        srv.shutdown()
        srv.server_close()


def test_tab_without_records_explains_the_file(proj, monkeypatch):
    monkeypatch.chdir(proj)
    (proj / "quirks" / "app.json").unlink()
    frag = hub.Hub(Path("golden"), Path("e2e"), projects_file=proj / "east2west.json").page("app", "quirks")
    assert "아직 기록이 없습니다" in frag["html"] and "quirks/app.json" in frag["html"] and frag["js"] == ""
