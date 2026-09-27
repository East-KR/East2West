"""Failures that must never be reported as a successful comparison."""
import json
from pathlib import Path

import pytest

from eastshift.observe import CompareOptions, observation
from eastshift.pwtest import oracle, report
from eastshift.pwtest.identity import canonical_ids
from eastshift.pwtest.ui import UI
from eastshift.pwtest.evidence import source_hash
from eastshift.runner import Runner


def _ui(snapshot: str, *, url: str = "http://tobe.test/orders") -> UI:
    class Page:
        def __init__(self):
            self.url = url

        def title(self):
            return "Orders"

    u = UI.__new__(UI)
    u.page = Page()
    u.base_url = "http://tobe.test"
    u.record_dir = None
    u.compare_dir = Path("golden/example")
    u._settle = lambda: snapshot
    u._step = 0
    u._step_dialogs = []
    u._shots = None
    u.opts = CompareOptions()
    u.name_map = {}
    u._secret_values = set()
    u._redact_patterns = []
    u._api_pending = []
    u.observations = []
    u.diffs = []
    u.accepted_diffs = []
    u.allowed_differences = []
    u.test_id = "test_order"
    u._golden = [observation(index=0, kind="goto", text="/orders", url="http://asis.test/orders", title="Orders",
                             snapshot='- heading "주문"', dialogs=[], opts=u.opts)]
    u._golden_assertions = []
    u.assertions = []
    return u


def test_missing_final_action_is_drift():
    u = _ui('- heading "주문"')
    u._golden.append({"index": 1})
    u.observations = [{"index": 0}]
    assert "action count changed" in " ".join(u.assertion_drift(True))


def test_navigation_to_wrong_server_is_rejected():
    u = _ui('- heading "주문"', url="http://asis.test/orders")
    with pytest.raises(AssertionError, match="configured target"):
        u._after("goto", "/orders")
    with pytest.raises(AssertionError, match="outside configured server"):
        u.goto("http://asis.test/orders")
    yaml_runner = Runner(base_url="http://tobe.test", compare_dir=Path("golden/example"))
    with pytest.raises(ValueError, match="outside configured server"):
        yaml_runner._url("http://asis.test/orders")


def test_exact_approved_difference_is_separate_from_failure():
    u = _ui('- heading "주문 등록"')
    u._after("goto", "/orders")
    assert u.diffs and not u.accepted_diffs
    digest = u.diffs[0][2][0].removeprefix("diff sha256: ")
    u = _ui('- heading "주문 등록"')
    u.allowed_differences = [{"test": "test_order", "step": 0, "sha256": digest, "reason": "승인된 이름 변경"}]
    u._after("goto", "/orders")
    assert not u.diffs and u.accepted_diffs[0]["reason"] == "승인된 이름 변경"


def test_secret_is_redacted_before_observation():
    u = _ui('- textbox "비밀번호": abc123')
    u._secret_values.add("abc123")
    u._after("fill", 'textbox "비밀번호" = abc123')
    assert "abc123" not in json.dumps(u.observations, ensure_ascii=False)
    assert "<redacted>" in json.dumps(u.observations, ensure_ascii=False)


def test_api_response_difference_is_reported():
    before = observation(index=0, kind="click", text="save", url="http://asis.test/", title="Orders",
                         snapshot='- heading "주문"', dialogs=[], opts=CompareOptions(),
                         api=[{"path": "/api/orders", "method": "POST", "status": 200, "body": '{"total": 100}'}])
    after = {**before, "api": [{"path": "/api/orders", "method": "POST", "status": 200, "body": '{"total": 101}'}]}
    from eastshift.observe import compare
    assert any("api responses" in x for x in compare(before, after, CompareOptions()))


def test_collision_ids_and_junit_cases_remain_distinct(tmp_path):
    a, b = tmp_path / "a.py", tmp_path / "b.py"
    ids = canonical_ids([f"{a}::test_save", f"{b}::test_save"])
    assert len(set(ids.values())) == 2
    junit = tmp_path / "junit.xml"
    junit.write_text("<testsuite>" + "".join(
        f'<testcase name="test_save"><properties><property name="eastshift_id" value="{ident}"/></properties></testcase>'
        for ident in ids.values()) + "</testsuite>")
    assert {x["name"] for x in report._junit(junit)["cases"]} == set(ids.values())


def test_report_separates_evidence_equivalence_and_coverage(tmp_path):
    golden = tmp_path / "sample"
    golden.mkdir()
    (golden / "test_order.json").write_text(json.dumps({"test": "test_order", "steps": [], "assertions": []}))
    (golden / "oracle.json").write_text(json.dumps({"coverage": [{"case": "주문 저장", "tests": ["test_order"]}]}))
    oracle.stamp(golden, "reviewer", "", oracle.oracle_files(golden))
    approval_id = oracle.approval_id(golden)
    source = source_hash(Path("e2e") / golden.name)
    junit = tmp_path / "run.xml"
    junit.write_text(f'''<testsuite><properties>
      <property name="oracle_approved" value="true"/>
      <property name="oracle_approved_at" value="{oracle.status(golden)['approved_at']}"/>
      <property name="oracle_approval_id" value="{approval_id}"/>
      <property name="source_sha256" value="{source}"/>
      </properties><testcase name="test_order"><failure>different</failure></testcase></testsuite>''')
    mutation = tmp_path / "mutation.json"
    mutation.write_text(json.dumps({"mode": "expects+golden", "oracle_approved": True,
                                    "oracle_approved_at": oracle.status(golden)["approved_at"],
                                    "oracle_approval_id": approval_id, "source_sha256": source,
                                    "generated_at": "now", "base_url": "http://asis.test", "errors": 0, "test_kills": {"test_order": 1},
                                    "score": .9, "killed": 9, "total": 10, "by_op": {"num": {"total": 10, "killed": 9}}, "mutants": []}))
    result = report.build(oracle_dir=golden, junits=[junit], mutations=[mutation])
    assert result["trusted"] and not result["equivalent"] and not result["coverage_complete"]
    assert result["coverage"][0]["case"] == "주문 저장"
    out = tmp_path / "verification.md"
    report.write(oracle_dir=golden, junits=[junit], mutations=[mutation], out=out)
    assert "증거 유효성: 유효" in out.read_text()
    assert "실행한 시나리오 동등성: 차이 또는 실패 있음" in out.read_text()
    assert not out.with_suffix(".html").exists()  # 화면은 eastshift ui 가 만든다. 파일은 markdown 하나뿐
    assert "주문 저장" in report.render_html(golden, result)
    stale = json.loads(mutation.read_text())
    stale["source_sha256"] = "older-code"
    mutation.write_text(json.dumps(stale))
    assert not report.build(oracle_dir=golden, junits=[junit], mutations=[mutation])["trusted"]
