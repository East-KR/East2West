"""승인 상태의 git 증분 해시(gitblobs)와, 한 번 확인한 승인 상태를 워커·결함 주입 pytest에 넘기는 것(precheck).
git 경로의 결과는 전부 직접 해시한 것과 같아야 한다: 바뀐 파일·git에 없는 파일·변환 속성이 걸린 파일·status 뒤에 바뀐 파일까지."""
import hashlib
import json
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from eastshift.pwtest import gitblobs, oracle, plugin


def git(repo: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(repo), "-c", "user.name=t", "-c", "user.email=t@t", *args], check=True, capture_output=True)


def direct(d: Path) -> dict[str, str]:
    return {p.relative_to(d).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in sorted(d.rglob("*")) if p.is_file() and p.name not in (oracle.MANIFEST, ".DS_Store")}


@pytest.fixture
def repo(tmp_path, monkeypatch):
    monkeypatch.setattr(gitblobs, "GIT_MIN_FILES", 0)
    r = tmp_path / "repo"
    d = r / "golden" / "app"
    (d / "shots" / "t1").mkdir(parents=True)
    for i in range(5):
        (d / f"t{i}.json").write_text(json.dumps({"steps": [], "i": i}), encoding="utf-8")
        (d / "shots" / "t1" / f"{i:02d}.jpg").write_bytes(bytes([i]) * 100)
    git(r, "init", "-q")
    git(r, "add", "-A")
    git(r, "commit", "-qm", "golden")
    return r, d


def store(r: Path) -> dict[str, str]:
    return gitblobs._load(r / ".git" / gitblobs.STORE)


def test_walk_matches_sorted_rglob(repo):
    r, d = repo
    (d / "a-b").mkdir()
    (d / "a-b" / "x.json").write_text("{}", encoding="utf-8")
    (d / "shots" / "t1-2").mkdir()
    (d / "shots" / "t1-2" / "00.jpg").write_bytes(b"j")
    assert oracle._walk(d) == [p.relative_to(d).as_posix() for p in sorted(d.rglob("*")) if p.is_file()]


def test_git_path_equals_direct_hashing_and_learns_blobs(repo):
    r, d = repo
    assert oracle.oracle_files(d) == direct(d)
    assert len(store(r)) == 10                                   # 처음엔 읽어서 기억한다
    (d / "t1.json").write_text('{"steps": [], "i": 99}', encoding="utf-8")   # 바뀐 파일
    (d / "new.json").write_text("{}", encoding="utf-8")                       # git에 없는 파일
    (d / "shots" / "t1" / "00.jpg").unlink()                                  # 지운 파일
    assert oracle.oracle_files(d) == direct(d)
    assert len(store(r)) == 10                                   # 바뀐 것·새 것은 기억하지 않는다 (색인에 없는 내용)


def test_status_uses_git_path_and_still_refuses_changes(repo):
    r, d = repo
    oracle.stamp(d, "east", "", oracle.oracle_files(d))
    assert oracle.status(d)["ok"]
    (d / "shots" / "t1" / "03.jpg").write_bytes(b"x" * 100)       # 같은 크기로 캡처 바꿔치기
    st = oracle.status(d)
    assert not st["ok"] and st["problems"] == ["changed since approval: shots/t1/03.jpg"]


def test_file_changed_after_status_is_hashed_but_not_remembered(repo, monkeypatch):
    """git status가 '같다'고 한 뒤에 바뀐 파일: 지금 내용의 해시를 쓰고, 틀린 짝(blob → 새 내용 해시)을 남기지 않는다."""
    r, d = repo
    (d / "t2.json").write_text('{"changed": true}', encoding="utf-8")
    real = gitblobs._git

    def stale_status(cwd, *args, stdin=None):
        return b"" if args[0] == "status" else real(cwd, *args, stdin=stdin)

    monkeypatch.setattr(gitblobs, "_git", stale_status)
    assert oracle.oracle_files(d) == direct(d)
    assert hashlib.sha256(b'{"changed": true}').hexdigest() not in store(r).values()


def test_conversion_attributes_and_autocrlf_fall_back_to_direct(repo):
    r, d = repo
    (r / ".gitattributes").write_text("*.json text eol=crlf\n", encoding="utf-8")
    git(r, "add", "-A")
    git(r, "commit", "-qm", "attrs")
    known = gitblobs.clean_sha256(d, oracle._walk(d))
    assert known and not any(r.endswith(".json") for r in known)   # 변환이 걸린 파일은 직접
    assert oracle.oracle_files(d) == direct(d)
    git(r, "config", "core.autocrlf", "input")
    assert gitblobs.clean_sha256(d, oracle._walk(d)) == {}


def test_outside_git_or_few_files_hash_directly(tmp_path, monkeypatch):
    d = tmp_path / "app"
    d.mkdir()
    (d / "t.json").write_text("{}", encoding="utf-8")
    assert gitblobs.clean_sha256(d, ["t.json"]) == {}              # 파일이 적으면 git을 부르지 않는다
    monkeypatch.setattr(gitblobs, "GIT_MIN_FILES", 0)
    monkeypatch.setenv("GIT_CEILING_DIRECTORIES", str(tmp_path))
    assert gitblobs.clean_sha256(d, ["t.json"]) == {}              # 저장소 밖
    assert oracle.oracle_files(d) == direct(d)


def test_precheck_is_used_only_for_the_same_oracle_and_approval(repo, tmp_path, monkeypatch):
    r, d = repo
    oracle.stamp(d, "east", "", oracle.oracle_files(d))
    pre = oracle.precheck(d)
    assert pre["status"]["ok"] and pre["setup"] == {} and not pre["setup_error"]
    assert oracle.given_precheck(d, pre) is pre
    assert oracle.given_precheck(tmp_path, pre) is None                      # 다른 오라클 폴더
    oracle.stamp(d, "someone", "", oracle.oracle_files(d))                  # 다시 승인됨 → 넘겨받은 것은 옛 승인본의 것
    assert oracle.given_precheck(d, pre) is None

    worker = SimpleNamespace(workerinput={"eastshift_precheck": pre})
    assert plugin._handed_precheck(worker) is pre                            # xdist 워커
    f = tmp_path / "pre.json"
    f.write_text(json.dumps(pre), encoding="utf-8")
    monkeypatch.setenv(oracle.PRECHECK, str(f))
    assert plugin._handed_precheck(SimpleNamespace()) == pre                 # eastshift mutate가 띄운 pytest

    node = SimpleNamespace(config=SimpleNamespace(_eastshift_precheck=pre), workerinput={})
    plugin.pytest_configure_node(node)
    assert node.workerinput["eastshift_precheck"] is pre                     # 컨트롤러가 워커에 넘긴다


def test_recorded_setup_differences_are_reported(tmp_path):
    d = tmp_path / "app"
    d.mkdir()
    (d / "a.json").write_text(json.dumps({"setup": {"reset_path": "/r"}}), encoding="utf-8")
    assert oracle.recorded_setup(d) == {"reset_path": "/r"}
    (d / "b.json").write_text(json.dumps({"setup": {}}), encoding="utf-8")
    with pytest.raises(ValueError):
        oracle.recorded_setup(d)
    assert "different setup" in oracle.precheck(d)["setup_error"]


def test_xdist_workers_take_the_controllers_precheck(tmp_path):
    """실제 pytest -n 2: 승인 확인(골든 전체 해시)은 컨트롤러에서 한 번만 한다."""
    import os
    import sys
    import textwrap
    g = tmp_path / "oracle" / "app"
    g.mkdir(parents=True)
    (g / "t_a.json").write_text('{"steps": []}', encoding="utf-8")
    oracle.stamp(g, "east", "", oracle.oracle_files(g))
    (tmp_path / "conftest.py").write_text(textwrap.dedent("""
        import os
        from eastshift.pwtest import oracle
        real = oracle.precheck
        def counted(d):
            with open(os.environ["PRECHECK_LOG"], "a") as f:
                f.write(os.environ.get("PYTEST_XDIST_WORKER", "controller") + "\\n")
            return real(d)
        oracle.precheck = counted
    """), encoding="utf-8")
    (tmp_path / "test_x.py").write_text("def test_a():\n    pass\n\n\ndef test_b():\n    pass\n", encoding="utf-8")
    log = tmp_path / "precheck.log"
    r = subprocess.run([sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", "-n", "2", "test_x.py", "--compare", str(g), "--base-url", "http://tobe"],
                       cwd=tmp_path, env={**os.environ, "PRECHECK_LOG": str(log)}, capture_output=True, text=True, timeout=120)
    assert r.returncode == 0, r.stdout + r.stderr
    assert "2 passed" in r.stdout
    assert log.read_text(encoding="utf-8").split() == ["controller"]
