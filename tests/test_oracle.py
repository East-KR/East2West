"""오라클 디렉터리(oracle.py): 승인 해시가 골든·규칙·이름 매핑·캡처 전부를 덮고, 승인 뒤 어떤 변경도 잡는다. 승인은 자동(auto_approve). 임시 디렉터리에서만 쓴다."""
import json
from pathlib import Path

import pytest

from east2west.pwtest import oracle


def _golden(d: Path, name: str, snapshot: str, assertions=()):
    (d / f"{name}.json").write_text(json.dumps({"test": name, "base_url": "http://asis", "recorded_at": "2026-01-01 00:00:00",
                                                "assertions": list(assertions), "steps": [{"index": 0, "kind": "goto", "text": "/", "url": "/",
                                                                                          "title": "t", "content": [], "snapshot": snapshot, "dialogs": []}]},
                                               ensure_ascii=False), encoding="utf-8")


@pytest.fixture
def oracle_dir(tmp_path):
    d = tmp_path / "app"
    d.mkdir()
    _golden(d, "test_a", "- text: 주문번호 12 완료\n- text: 합계 1,200원\n", [{"step": 1, "kind": "text", "target": "합계 1,200원", "value": None}])
    (d / oracle.CONFIG).write_text(json.dumps({"ignore": [r"주문번호 \d+"]}), encoding="utf-8")
    (d / "name_map.tobe.json").write_text(json.dumps({"수량": "주문 수량"}, ensure_ascii=False), encoding="utf-8")
    (d / "shots" / "test_a").mkdir(parents=True)
    (d / "shots" / "test_a" / "00.jpg").write_bytes(b"jpg")
    return d


def test_unapproved_status_says_approval_is_automatic(oracle_dir):
    st = oracle.status(oracle_dir)
    assert not st["ok"] and "never been approved" in st["problems"][0] and "automatically" in st["problems"][0]


def test_stamp_covers_every_file_and_any_change_is_detected(oracle_dir):
    files = oracle.oracle_files(oracle_dir)
    assert set(files) == {"test_a.json", "oracle.json", "name_map.tobe.json", "shots/test_a/00.jpg"}  # APPROVED.json 자신은 제외
    oracle.stamp(oracle_dir, "east", "", files)
    st = oracle.status(oracle_dir)
    assert st["ok"] and st["approved_by"] == "east"
    assert json.loads((oracle_dir / oracle.MANIFEST).read_text(encoding="utf-8"))["assertions"] == {"test_a": [{"step": 1, "kind": "text", "target": "합계 1,200원", "value": None}]}
    # 규칙 한 글자, 캡처 한 바이트, 파일 추가·삭제 모두 거부
    (oracle_dir / oracle.CONFIG).write_text(json.dumps({"ignore": [r"주문번호 \d*"]}), encoding="utf-8")
    assert oracle.status(oracle_dir)["problems"] == ["changed since approval: oracle.json"]
    (oracle_dir / oracle.CONFIG).write_text(json.dumps({"ignore": [r"주문번호 \d+"]}), encoding="utf-8")
    assert oracle.status(oracle_dir)["ok"]
    (oracle_dir / "shots" / "test_a" / "00.jpg").write_bytes(b"jpG")
    assert oracle.status(oracle_dir)["problems"] == ["changed since approval: shots/test_a/00.jpg"]
    (oracle_dir / "shots" / "test_a" / "00.jpg").write_bytes(b"jpg")
    _golden(oracle_dir, "test_b", "- text: x\n")
    assert oracle.status(oracle_dir)["problems"] == ["added since approval: test_b.json"]
    (oracle_dir / "test_b.json").unlink()
    (oracle_dir / "name_map.tobe.json").unlink()
    assert oracle.status(oracle_dir)["problems"] == ["removed since approval: name_map.tobe.json"]


def test_fingerprint_changes_with_content_and_ds_store_is_ignored(oracle_dir):
    fp = oracle.fingerprint(oracle_dir)
    (oracle_dir / ".DS_Store").write_bytes(b"\0")
    assert oracle.fingerprint(oracle_dir) == fp
    (oracle_dir / "test_a.json").write_text((oracle_dir / "test_a.json").read_text(encoding="utf-8").replace("1,200", "1,201"), encoding="utf-8")
    assert oracle.fingerprint(oracle_dir) != fp


def test_auto_approve_stamps_only_a_changed_oracle_and_says_what_changed(oracle_dir):
    """승인은 자동(auto_approve)뿐이다. 사람 이름이 아니라 AUTO_BY 로 남고, 같으면 다시 찍지 않는다 (앞선 원장이 '다른 승인본'이 되지 않게)."""
    assert not hasattr(oracle, "approve") and not hasattr(oracle, "approve_from_review")
    assert oracle.auto_approve(oracle_dir, "외부 도구") == "처음 승인 · 외부 도구"
    st = oracle.status(oracle_dir)
    assert st["ok"] and st["approved_by"] == oracle.AUTO_BY and st["note"] == "처음 승인 · 외부 도구"
    first = oracle.approval_id(oracle_dir)
    assert oracle.auto_approve(oracle_dir) == "" and oracle.approval_id(oracle_dir) == first
    _golden(oracle_dir, "test_b", "- text: x\n")
    (oracle_dir / oracle.CONFIG).write_text(json.dumps({"ignore": []}), encoding="utf-8")
    (oracle_dir / "name_map.tobe.json").unlink()
    assert oracle.auto_approve(oracle_dir) == "추가 1 · 바뀜 1 · 지움 1 (파일)" and oracle.status(oracle_dir)["ok"]
    assert oracle.auto_approve(oracle_dir.parent / "none") == ""  # 골든 폴더가 없으면 찍지 않는다


def test_mask_hits_show_what_each_rule_actually_hides(oracle_dir):
    hits = oracle.mask_hits(oracle_dir)
    assert hits == [{"rule": r"주문번호 \d+", "total": 1, "samples": [("주문번호 12", 1)]}]
    assert "masks 1 occurrences" in oracle.mask_audit(oracle_dir)[0]
    (oracle_dir / oracle.CONFIG).write_text(json.dumps({"ignore": [r"\d+"]}), encoding="utf-8")  # 넓은 규칙은 금액까지 가린다: 사람이 봐야 한다
    assert {s for s, _ in oracle.mask_hits(oracle_dir)[0]["samples"]} == {"12", "1", "200"}


def test_tests_and_name_maps_listing(oracle_dir):
    t = oracle.tests(oracle_dir)
    assert [x["name"] for x in t] == ["test_a"] and t[0]["steps"] == 1 and t[0]["base_url"] == "http://asis"
    assert oracle.name_maps(oracle_dir) == {"tobe": {"수량": "주문 수량"}}
    assert any("expects text '합계 1,200원'" in l for l in oracle.summary(oracle_dir))
