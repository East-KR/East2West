"""차이 규칙(rules.py): 결함 아닌 갈래·같은 뜻 규칙의 검사, 행 판정, cascade, 비교 때 승인(ui._after), 원장·보고서 표시와 옛 원장 호환."""
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from east2west import cli
from east2west.observe import CompareOptions, compare, observation
from east2west.pwtest import catalog, ledger, oracle, quirks, report, rules
from east2west.pwtest.ui import UI

ORA = 'ORA-12899: value too large for column "S"."T"."COL" (actual: 12, maximum: 10)'
SAME = {"class": "same", "reason": "DB 길이 초과: 같은 열", "what": "알림창",
        "asis": r'ORA-12899: .*\."(?P<col>\w+)" \(', "tobe": r"^(?P<col>\w+) 컬럼에 지정된 길이보다 큰 값이 입력되었습니다", "same_groups": ["col"]}


def obs(snapshot: str, dialogs=(), url="http://asis/orders", index=0, text="/orders"):
    return observation(index=index, kind="goto", text=text, url=url, title="", snapshot=snapshot,
                       dialogs=[{"type": "alert", "message": m, "action": "accept"} for m in dialogs], opts=CompareOptions())


def lines(a, b):
    return compare(a, b, CompareOptions())


def judge(rs):
    return rules.Judge(rules.compile_rules(rs))


def test_problems_name_every_bad_rule():
    assert rules.problems([SAME, {"class": "env", "reason": "자료", "tests": ["test_a"]}]) == []
    bad = rules.problems([{"class": "defect", "reason": "x", "what": "a"}, {"class": "env", "reason": " ", "what": "a"},
                          {"class": "env", "reason": "r", "what": "("}, {"class": "env", "reason": "r"},
                          {"class": "same", "reason": "r", "asis": "a", "tobe": "b", "same_groups": ["g"]},
                          {"class": "env", "reason": "r", "what": "a", "same_groups": ["g"]}, {"class": "env", "reason": "r", "tests": "test_a"},
                          {"class": "tool", "reason": "r", "routes": ["/a("]}, {"class": "env", "reason": "r", "what": "a", "why": "옛 키"}])
    text = "\n".join(bad)
    for part in ("[0]: 모르는 갈래", "[1]: reason", "[2].what: 정규식이 틀렸습니다", "[3]: 범위", "[4]: 묶음 (?P<g>…)", "[5]: same_groups 는",
                 "[6].tests: 문자열 목록", "[7].routes[0]: 정규식", "[8]: 모르는 키 why"):
        assert part in text, part
    with pytest.raises(ValueError):
        rules.compile_rules([{"class": "env", "reason": "r"}])


def test_oracle_refuses_bad_rules_but_still_reads_them(tmp_path, capsys):
    d = tmp_path / "golden" / "app"
    d.mkdir(parents=True)
    (d / "oracle.json").write_text(json.dumps({"difference_rules": [{"class": "env", "what": "("}]}), encoding="utf-8")
    pre = oracle.precheck(d)
    assert "모르는" not in pre["config_error"] and "reason" in pre["config_error"] and "정규식" in pre["config_error"]
    assert oracle.load_config(d)["difference_rules"]  # 승인 화면이 틀린 파일도 보여 줄 수 있게 읽기는 그대로
    assert cli.main(["oracle-status", str(d)]) == 1 and "invalid rules" in capsys.readouterr().out
    (d / "oracle.json").write_text(json.dumps({"difference_rules": [SAME]}), encoding="utf-8")
    assert oracle.precheck(d)["config_error"] == "" and oracle.config_problems(d) == []


def test_same_meaning_needs_equal_groups():
    a = obs("- text: 주문\n", dialogs=[ORA])
    j = judge([SAME])
    assert j.step(lines(a, obs("- text: 주문\n", dialogs=["COL 컬럼에 지정된 길이보다 큰 값이 입력되었습니다"])), test="t")["verdict"] == "same"
    other = j.step(lines(a, obs("- text: 주문\n", dialogs=["NAME 컬럼에 지정된 길이보다 큰 값이 입력되었습니다"])), test="t")
    assert other["verdict"] == "diff" and not other["notes"]


def test_rows_that_rules_cannot_see_keep_the_step_a_diff():
    """행에 드러나지 않는 차이(같은 이름 요소의 상태만, 주소·제목, 순서 무시 비교의 '- ')가 섞이면 규칙이 아무리 넓어도 판정하지 않는다."""
    wide = judge([{"class": "env", "reason": "넓은 규칙", "what": "."}])
    state_only = lines(obs('- textbox "수량": "3"\n- button "저장"\n'), obs('- textbox "수량": "4"\n- button "저장" [disabled]\n'))
    assert rules.rows_of(state_only) is None and wide.step(state_only, test="t")["verdict"] == "diff"
    for extra in (["url: '/a' → '/b'"], ["! step text changed since recording: 'a' → 'b'"], ["- text: 사라짐"], ["api responses: [] → [1]"]):
        assert rules.rows_of(["-text: 가", "+text: 나", *extra]) is None
    assert rules.rows_of(["--- as-is(golden)", "+++ actual", "@@ -1 +1 @@", " text: 주문", "-text: 가", "+text: 나"]) == [("화면 문구", "가", "(없음)"), ("화면 문구", "(없음)", "나")]


def test_whole_step_accepted_partial_step_keeps_notes():
    env = {"class": "env", "reason": "로컬 DB 에 공통코드가 없다", "tests": ["test_a"], "what": "처리상태"}
    a = obs('- combobox "처리상태": "접수"\n- text: 합계 300\n')
    only = lines(a, obs('- combobox "처리상태": ""\n- text: 합계 300\n'))
    got = judge([env]).step(only, test="test_a")
    assert got["verdict"] == "accepted" and got["accepted"][0]["class"] == "env" and got["accepted"][0]["rows"] == [["처리상태", "접수", "(값 없음)"]]
    assert judge([env]).step(only, test="test_b")["verdict"] == "diff"  # 다른 테스트는 범위 밖
    both = judge([env]).step(lines(a, obs('- combobox "처리상태": ""\n- text: 합계 310\n')), test="test_a")
    assert both["verdict"] == "diff" and both["notes"] == {("처리상태", "접수", "(값 없음)"): {"class": "env", "reason": env["reason"]}}


def test_cascade_carries_the_class_to_later_steps():
    blocked = {"class": "blocked", "reason": "인쇄 양식이 없어 저장이 막힌다", "what": "저장 결과", "cascade": True}
    j = judge([blocked])
    first = j.step(lines(obs('- textbox "저장 결과": "완료"\n'), obs('- textbox "저장 결과": "실패"\n')), test="t")
    later = j.step(lines(obs("- text: 새 행 ORD-9\n"), obs("- text: 행 없음\n")), test="t")
    assert first["verdict"] == later["verdict"] == "accepted"
    assert later["accepted"][0]["class"] == "blocked" and "앞 단계에서 이어짐" in later["accepted"][0]["reason"]
    assert judge([blocked]).step(lines(obs("- text: 새 행 ORD-9\n"), obs("- text: 행 없음\n")), test="t")["verdict"] == "diff"  # cascade 전에는 결함


def test_scope_by_test_id_regex_and_route():
    row = ("화면 문구", "가", "나")
    r = rules.compile_rules([{"class": "env", "reason": "r", "tests": ["test_f[\\uc644\\ub8cc-0\\uac74]", "test_g_.*"], "routes": ["/orders(/\\d+)?"]}])[0]
    assert rules.hit(r, row, "test_f[\\uc644\\ub8cc-0\\uac74]", ("/orders/3", "")) and rules.hit(r, row, "test_g_1", ("", "/orders"))
    assert not rules.hit(r, row, "test_h", ("/orders",)) and not rules.hit(r, row, "test_g_1", ("/orders/x", "/order"))


class FakePage:
    """ui._after 만 돌리는 가짜 페이지: 화면 안정화(_settle)는 테스트가 스냅샷을 준다."""
    def __init__(self, url):
        self.url, self.context = url, SimpleNamespace(on=lambda *a: None)

    def on(self, *a):
        pass

    def title(self):
        return ""

    def screenshot(self, **kw):
        pass


def _ui(tmp_path, monkeypatch, rules_, steps_asis, test="test_a"):
    monkeypatch.setattr(quirks, "ROOT", tmp_path / "quirks")
    d = tmp_path / "golden" / "app"
    d.mkdir(parents=True, exist_ok=True)
    (d / "oracle.json").write_text(json.dumps({"difference_rules": rules_}), encoding="utf-8")
    steps = [obs(s, dialogs=dl, index=i, text=f"/p{i}", url=f"http://asis/p{i}") for i, (s, dl) in enumerate(steps_asis)]
    (d / f"{test}.json").write_text(json.dumps({"test": test, "base_url": "http://asis", "setup": {}, "assertions": [], "steps": steps}), encoding="utf-8")
    u = UI(FakePage("http://tobe/p0"), base_url="http://tobe", test_id=test, compare_dir=d, compare_opts=CompareOptions())
    if getattr(u, "_shots", None):  # 비교 때 단계 캡처용 임시 폴더 (가짜 페이지라 비어 있다)
        __import__("shutil").rmtree(u._shots, ignore_errors=True)
        u._shots = None
    return u


def _run(u, snapshots):
    for i, (snap, dialogs) in enumerate(snapshots):
        u.page.url = f"http://tobe/p{i}"
        u._settle = lambda snap=snap: snap
        u._step_dialogs = [{"type": "alert", "message": m, "action": "accept"} for m in dialogs]
        u._after("goto", f"/p{i}")


def test_compare_time_acceptance_in_ui_after(tmp_path, monkeypatch):
    env = {"class": "env", "reason": "로컬 자료가 다르다", "what": "건수"}
    u = _ui(tmp_path, monkeypatch, [SAME, env], [("- text: 주문\n", [ORA]), ('- textbox "건수": "3"\n', []), ('- textbox "건수": "3"\n- text: 합계 1\n', [])])
    _run(u, [("- text: 주문\n", ["COL 컬럼에 지정된 길이보다 큰 값이 입력되었습니다"]),  # 같은 뜻: 차이 아님
             ('- textbox "건수": "5"\n', []),  # 자료·환경 차이: 비교 제외
             ('- textbox "건수": "5"\n- text: 합계 2\n', [])])  # 결함 행이 남아 다른 점, 맞은 행은 갈래가 남는다
    assert [(a["step"], a["class"]) for a in u.accepted_diffs] == [(1, "env")] and len(u.accepted_diffs[0]["sha256"]) == 64
    assert [s for s, _, _ in u.diffs] == [2] and u.row_notes[("건수", "3", "5")]["class"] == "env"


def test_exact_sha256_still_wins(tmp_path, monkeypatch):
    env = {"class": "env", "reason": "규칙", "what": "건수"}
    u = _ui(tmp_path, monkeypatch, [env], [('- textbox "건수": "3"\n', [])])
    digest = __import__("hashlib").sha256("\n".join(lines(obs('- textbox "건수": "3"\n', url="http://asis/p0", text="/p0"),
                                                          obs('- textbox "건수": "5"\n', url="http://tobe/p0", text="/p0"))).encode()).hexdigest()
    u.allowed_differences = [{"test": "test_a", "step": 0, "sha256": digest, "reason": "합의된 변경"}]
    _run(u, [('- textbox "건수": "5"\n', [])])
    assert u.accepted_diffs == [{"step": 0, "sha256": digest, "reason": "합의된 변경"}]


def test_ledger_report_and_catalog_show_classes_and_read_old_runs(tmp_path, monkeypatch):
    monkeypatch.setattr(ledger, "RUNS", tmp_path / "runs")
    d = tmp_path / "golden" / "app"
    d.mkdir(parents=True)
    step = obs("- text: 주문\n")
    for t in ("test_old", "test_new", "test_part"):
        (d / f"{t}.json").write_text(json.dumps({"test": t, "base_url": "http://asis", "setup": {}, "assertions": [], "steps": [step]}), encoding="utf-8")
    acc = {"step": 0, "sha256": "x" * 64, "class": "env", "reason": "로컬 자료", "rule": {"class": "env"}, "rows": [["건수", "3", "5"]]}
    ledger.write_run("app", target="http://tobe", oracle={"ok": True}, started=0, cases={  # 옛 원장: 갈래·row_classes 가 없다
        "test_old": {"status": "pass", "kind": "accepted_diff", "summary": "합의된 변경", "rows": []}})
    ledger.write_run("app", target="http://tobe", oracle={"ok": True}, started=0, cases={
        "test_old": {"status": "pass", "kind": "accepted_diff", "summary": "합의된 변경", "rows": []},
        "test_new": {"status": "pass", "kind": "accepted_diff", "summary": rules.describe(acc), "rows": [], "accepted": [acc]},
        "test_part": {"status": "fail", "kind": "golden_diff", "summary": "step 0", "rows": [["건수", "3", "5"], ["합계", "1", "2"]],
                      "row_classes": [{"class": "env", "reason": "로컬 자료"}, None]}})
    g = catalog.build(d)
    assert g["tests"]["test_old"]["last"]["accepted"] == [] and g["tests"]["test_old"]["last"]["row_classes"] == []
    assert g["tests"]["test_new"]["last"]["accepted"] == [{"step": 0, "text": "자료·환경 차이: 로컬 자료"}]
    assert g["tests"]["test_part"]["last"]["row_classes"] == ["자료·환경 차이: 로컬 자료", ""]
    assert "자료·환경 차이: 로컬 자료" in catalog.fragment(g)["html"]  # 알약 말풍선 = 요약
    junit = tmp_path / "junit.xml"
    junit.write_text("<testsuite><testcase name='test_new'><properties><property name='east2west_id' value='test_new'/>"
                     f"<property name='east2west_accepted_differences' value='{json.dumps([acc], ensure_ascii=False)}'/></properties></testcase>"
                     "<testcase name='test_part'><properties><property name='east2west_id' value='test_part'/>"
                     "<property name='east2west_row_classes' value='[[\"건수\", \"3\", \"5\", \"env\", \"로컬 자료\"]]'/></properties>"
                     "<failure>step 0 /p0: differs from golden\n    -textbox \"건수\": 3\n    +textbox \"건수\": 5\n    -text: 합계 1</failure></testcase>"
                     "<testcase name='test_old'><properties><property name='east2west_id' value='test_old'/>"
                     "<property name='east2west_accepted_differences' value='[{\"step\": 0, \"sha256\": \"y\", \"reason\": \"합의된 변경\"}]'/></properties></testcase></testsuite>",
                     encoding="utf-8")
    out = tmp_path / "v.md"
    report.write(oracle_dir=d, junits=[junit], mutations=[], out=out)
    md = out.read_text(encoding="utf-8")
    assert "0단계: 비교 제외 — 자료·환경 차이: 로컬 자료" in md and "0단계: 비교 제외 — 합의된 변경" in md
    assert "규칙에 맞은 행 `건수`: 자료·환경 차이: 로컬 자료" in md
    frag = report.render_fragment(d, report.build(oracle_dir=d, junits=[junit], mutations=[]))
    assert "자료·환경 차이 · 로컬 자료" in frag["html"] and "자료·환경 차이: 로컬 자료" in frag["html"]


def test_request_count_line_becomes_a_row_rules_can_judge():
    a = observation(index=0, kind="click", text="조회", url="http://asis/orders", title="", snapshot="- text: x\n", dialogs=[],
                    opts=CompareOptions(), requests=["GET /orders", "GET /orders"])
    b = observation(index=0, kind="click", text="조회", url="http://tobe/orders", title="", snapshot="- text: x\n", dialogs=[],
                    opts=CompareOptions(), requests=["GET /orders"])
    diff = lines(a, b)
    assert rules.rows_of(diff) == [("서버 요청 횟수 · GET /orders", "2회", "1회")]
    customer = {"class": "customer", "reason": "두 번 읽기는 고객이 고치기로 했다", "what": "^서버 요청 횟수"}
    assert judge([customer]).step(diff, test="t")["verdict"] == "accepted"
    assert judge([]).step(diff, test="t")["verdict"] == "diff"
