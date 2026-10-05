"""Claude Code PreToolUse hook: 에이전트가 오라클(golden/)을 손으로 고치거나 승인 도장을 손으로 쓰지 못하게, 고객 결정(quirks/<app>.decisions.json)을 쓰지 못하게 막는다.

막는 것
- Edit/Write/MultiEdit/NotebookEdit 로 golden/ 아래 파일을 쓰는 것
- Bash 로 APPROVED.json 을 쓰거나 인라인 파이썬으로 oracle.stamp 를 부르는 것 (읽기는 된다). 승인은 기록·비교·결함 탐지가 자동으로 한다
  (oracle.auto_approve, 2026-10-05 사용자 결정). 옛 `east2west approve` 호출도 계속 막는다.
- Bash 로 golden/ 아래를 지우거나 옮기거나 덮어쓰는 것 (rm, mv, cp, sed -i, tee, >, truncate …)
- quirks/<app>.decisions.json 을 쓰는 것 (Edit/Write, 쓰기 명령·리다이렉트·인라인 파이썬). 고객 결정은 골든처럼 사람 것이다:
  사람이 east2west ui 의 'as-is 이상 동작' 탭에서 저장하고, '수정' 결정은 비교 때 판정 규칙이 된다 (quirks.py). 기록(quirks/<app>.json)은 에이전트가 써도 된다.
허용하는 것: 읽기, `pytest … --record/--compare golden/…`, `east2west mutate/report/oracle-status`.
골든은 기록(--record)으로만 바뀌고, 바뀐 골든은 기록·비교·결함 탐지가 자동으로 승인한다. 이 hook 은 그 경로 밖에서 손으로 고치는 것을 앞단에서 막는다.
차단 시 exit 2 + stderr: Claude Code가 도구 호출을 막고 이유를 에이전트에게 보여 준다.
"""
import json
import re
import sys

REASON = ("golden/ is the approved oracle: agents do not edit it or write its approval by hand. "
          "Re-record it from as-is (pytest --record) or propose rule changes (oracle.json / name maps) to the user; "
          "record, compare and mutate approve a changed golden automatically (oracle.auto_approve).")
DECISIONS_REASON = ("quirks/<app>.decisions.json holds customer decisions: agents read them (east2west quirks <app>) but do not write them. "
                    "A person records keep/change/hold in `uv run east2west ui` (as-is 이상 동작 tab).")
DECISIONS = re.compile(r"quirks/[^\s'\"]*\.decisions\.json")
DECISIONS_REDIRECT = re.compile(r"(?<![<\w-])>>?\s*['\"]?\S*\.decisions\.json")
SHELL_WRITE = re.compile(r"(^|[\s;&|(])(rm|mv|cp|tee|truncate|ln|chmod|sed\s+-i|perl\s+-[pi]|dd)\b")
ALLOWED_FLAGS = re.compile(r"--(?:record|compare|oracle|name-map)[ =]\S*golden/\S*")
PY_WRITE = re.compile(r"\b(?:write_text|write_bytes|open\(|json\.dump\b|shutil\.|os\.(?:remove|unlink|rename|replace|rmdir)|\.unlink\(|\.rename\(|\.replace\(|rmtree|\.touch\(|\.mkdir\()")
WRITE_VERBS = re.compile(r"(^|[\s;&|(])(rm|mv|cp|tee|truncate|ln|chmod|sed\s+-i|perl\s+-[pi]|dd)\b"  # golden/ 인자가 있는 쓰기 명령
                         r"|(?<![<\w-])>>?\s*['\"]?\S*golden/"                                      # golden/ 으로 향하는 리다이렉트
                         r"|\bopen\([^)]*golden/[^)]*['\"][wa]")                                     # 스크립트에서 golden/ 파일 쓰기


def block(why: str, reason: str = REASON) -> None:
    print(f"BLOCKED by tools/guard_oracle.py: {why}\n{reason}", file=sys.stderr)
    sys.exit(2)


def main() -> None:
    data = json.load(sys.stdin)
    tool, inp = data.get("tool_name", ""), data.get("tool_input", {}) or {}
    if tool in ("Edit", "Write", "MultiEdit", "NotebookEdit"):
        path = inp.get("file_path") or inp.get("notebook_path") or ""
        if "/golden/" in path or path.startswith("golden/") or path.endswith("APPROVED.json"):
            block(f"{tool} on {path}")
        if re.search(r"(^|/)quirks/[^/]+\.decisions\.json$", path):
            block(f"{tool} on {path}", DECISIONS_REASON)
    elif tool == "Bash":
        cmd = inp.get("command", "")
        # 명령으로 실행하는 경우만 (명령 줄 맨 앞, 또는 ; && || | 뒤). 문서·코드 편집 안의 문구는 막지 않는다.
        # 인라인 파이썬으로 승인 도장(oracle.stamp)을 직접 찍는 것도 막는다. 자동 승인(auto_approve)은 기록·비교가 부르는 경로라 막지 않는다
        if re.search(r"\bpython[0-9.]*\b", cmd) and re.search(r"\bstamp\(", cmd):
            block("stamping the oracle approval by hand")
        if re.search(r"(^|[;&|]\s*|\n\s*)(uv\s+run\s+)?(east2west|python3?\s+-m\s+east2west\.cli)\s+approve\b", cmd):
            block("approving with CLI")
        # APPROVED.json 은 읽기(cat, head, grep, jq …)는 되고, 쓰기 명령·리다이렉트·파이썬 쓰기 API와 함께 나오면 막는다
        if "APPROVED.json" in cmd and (WRITE_VERBS.search(cmd) or PY_WRITE.search(cmd)
                                       or re.search(r"(?<![<\w-])>>?\s*['\"]?\S*APPROVED\.json", cmd)):
            block("touching APPROVED.json")
        rest = ALLOWED_FLAGS.sub("", cmd)
        if "golden/" in rest and WRITE_VERBS.search(rest):
            block("shell command that writes into golden/")
        # 인라인 파이썬(heredoc, -c)이 golden/을 언급하면서 파일 쓰기 API를 쓰면 막는다. 변수로 경로를 돌려도 API 이름은 남는다.
        if "golden/" in rest and re.search(r"\bpython[0-9.]*\b", rest) and PY_WRITE.search(rest):
            block("inline python that names golden/ and writes files (read it with east2west oracle-status)")
        # 고객 결정 파일: 읽기(cat, jq, east2west quirks)는 되고, 쓰기 명령·리다이렉트·파이썬 쓰기와 함께 나오면 막는다.
        # 파이썬은 경로를 조각내 만들 수 있으므로(Path("quirks") / f"{app}.decisions.json") '.decisions.json' 이름만 보여도 막는다
        if (DECISIONS.search(cmd) and SHELL_WRITE.search(cmd)) or DECISIONS_REDIRECT.search(cmd):
            block("shell command that writes quirks/*.decisions.json", DECISIONS_REASON)
        if ".decisions.json" in cmd and re.search(r"\bpython[0-9.]*\b", cmd) and PY_WRITE.search(cmd):
            block("inline python that names *.decisions.json and writes files", DECISIONS_REASON)


if __name__ == "__main__":
    main()
