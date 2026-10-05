"""Claude Code hook(tools/guard_oracle.py): 에이전트의 golden/ 쓰기·손 승인(APPROVED.json·oracle.stamp)과 고객 결정(quirks/*.decisions.json) 쓰기는 막고, 읽기와 기록·비교(자동 승인) 실행은 그대로 둔다."""
import json
import subprocess
import sys
from pathlib import Path

import pytest

HOOK = Path(__file__).resolve().parents[1] / "tools" / "guard_oracle.py"


def hook(tool: str, **inp) -> int:
    r = subprocess.run([sys.executable, str(HOOK)], input=json.dumps({"tool_name": tool, "tool_input": inp}), capture_output=True, text=True)
    assert r.returncode in (0, 2), r.stderr
    return r.returncode


@pytest.mark.parametrize("cmd", [
    "cat golden/legacy/APPROVED.json",
    "head -c 600 golden/portal/APPROVED.json && jq .approved_by golden/portal/APPROVED.json",
    "grep -rn approved_by golden/",
    "uv run pytest e2e/legacy --base-url http://127.0.0.1:8801 --record golden/legacy",
    "uv run pytest e2e/legacy --base-url http://127.0.0.1:8802 --compare golden/legacy --junitxml reports/junit.xml",
    "uv run east2west mutate e2e/legacy --base-url http://x --compare golden/legacy",
    "uv run east2west oracle-status golden/legacy",
    "uv run east2west report --oracle golden/legacy --junit reports/j.xml --out reports/v.md",
    "cp reports/a.png docs/samples/b.png",
    "echo 'approve later' > notes.txt",
    "curl -X POST http://127.0.0.1:8790/api/app/legacy/record -d '{}'",
    "python3 -c \"from pathlib import Path; from east2west.pwtest import oracle; print(oracle.auto_approve(Path('golden/legacy')))\"",
])
def test_reads_and_record_compare_are_allowed(cmd):
    assert hook("Bash", command=cmd) == 0


@pytest.mark.parametrize("cmd", [
    "uv run east2west approve golden/legacy --by agent",
    "cd x && east2west approve golden/legacy --by me",
    "python3 -m east2west.cli approve golden/legacy --by me",
    "cp /tmp/x.json golden/legacy/APPROVED.json",
    "echo '{}' > golden/legacy/APPROVED.json",
    "echo '{}' > /tmp/g/APPROVED.json",
    "rm -rf golden/legacy/shots",
    "mv golden/legacy/test_a.json golden/legacy/test_b.json",
    "sed -i '' 's/120/124/' golden/legacy/test_order_save.json",
    "tee golden/legacy/oracle.json <<< '{}'",
    "python3 -c \"open('golden/legacy/oracle.json','w').write('{}')\"",
    "python3 - <<'EOF'\nfrom pathlib import Path\nPath('golden/legacy/oracle.json').write_text('{}')\nEOF",
    "python3 -c \"from east2west.pwtest import oracle; oracle.stamp(d, 'me', '', oracle.oracle_files(d))\"",
])
def test_writes_and_approval_are_blocked(cmd):
    assert hook("Bash", command=cmd) == 2


def test_editor_tools_cannot_touch_golden():
    assert hook("Edit", file_path="golden/legacy/oracle.json") == 2
    assert hook("Write", file_path="/abs/repo/golden/legacy/test_a.json") == 2
    assert hook("Write", file_path="/tmp/anything/APPROVED.json") == 2
    assert hook("Edit", file_path="east2west/pwtest/oracle.py") == 0
    assert hook("Write", file_path="docs/MIGRATION.md") == 0


@pytest.mark.parametrize("cmd", [
    "cat quirks/portal.decisions.json",
    "jq '.\"Q-01\".decision' quirks/portal.decisions.json > /tmp/decision.json",
    "uv run east2west quirks golden/portal",
    "echo '[]' > quirks/portal.json",  # 기록(조사 결과)은 에이전트가 써도 된다
])
def test_customer_decisions_can_be_read(cmd):
    assert hook("Bash", command=cmd) == 0


@pytest.mark.parametrize("cmd", [
    "echo '{}' > quirks/portal.decisions.json",
    "cp /tmp/d.json quirks/portal.decisions.json",
    "rm quirks/portal.decisions.json",
    "sed -i '' 's/keep/change/' quirks/portal.decisions.json",
    "python3 -c \"open('quirks/portal.decisions.json','w').write('{}')\"",
    "python3 - <<'EOF'\nfrom pathlib import Path\n(Path('quirks') / 'portal.decisions.json').write_text('{}')\nEOF",
])
def test_customer_decisions_cannot_be_written_by_agents(cmd):
    assert hook("Bash", command=cmd) == 2


def test_editor_tools_cannot_write_customer_decisions():
    assert hook("Write", file_path="/abs/repo/quirks/portal.decisions.json") == 2
    assert hook("Edit", file_path="quirks/portal.decisions.json") == 2
    assert hook("Write", file_path="/abs/repo/quirks/portal.json") == 0
