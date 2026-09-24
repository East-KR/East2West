"""Claude Code PreToolUse hook: 에이전트가 오라클(golden/)을 직접 고치거나 승인하지 못하게 막는다.

막는 것
- Edit/Write/MultiEdit/NotebookEdit 로 golden/ 아래 파일을 쓰는 것
- Bash 로 `jev-e2e approve` 를 실행하거나 APPROVED.json 을 건드리는 것
- Bash 로 golden/ 아래를 지우거나 옮기거나 덮어쓰는 것 (rm, mv, cp, sed -i, tee, >, truncate …)
허용하는 것: 읽기, `pytest … --record/--compare golden/…`, `jev-e2e mutate/report/oracle-status`.
--record 로 기록하면 해시가 바뀌어 사람이 다시 승인해야 비교가 돈다 (APPROVED.json이 진짜 안전장치, 이 hook은 앞단 차단).
차단 시 exit 2 + stderr: Claude Code가 도구 호출을 막고 이유를 에이전트에게 보여 준다.
"""
import json
import re
import sys

REASON = ("golden/ is the approved oracle: agents do not edit it or approve it. "
          "Propose the change to the user (what and why); a person edits oracle.json / name maps and runs "
          "`uv run jev-e2e approve golden/<app> --by <name>` in a terminal.")
ALLOWED_FLAGS = re.compile(r"--(?:record|compare|oracle|name-map)[ =]\S*golden/\S*")
WRITE_VERBS = re.compile(r"(^|[\s;&|(])(rm|mv|cp|tee|truncate|ln|chmod|sed\s+-i|perl\s+-[pi]|dd)\b"  # golden/ 인자가 있는 쓰기 명령
                         r"|(?<![<\w-])>>?\s*['\"]?\S*golden/"                                      # golden/ 으로 향하는 리다이렉트
                         r"|\bopen\([^)]*golden/[^)]*['\"][wa]")                                     # 스크립트에서 golden/ 파일 쓰기


def block(why: str) -> None:
    print(f"BLOCKED by tools/guard_oracle.py: {why}\n{REASON}", file=sys.stderr)
    sys.exit(2)


def main() -> None:
    data = json.load(sys.stdin)
    tool, inp = data.get("tool_name", ""), data.get("tool_input", {}) or {}
    if tool in ("Edit", "Write", "MultiEdit", "NotebookEdit"):
        path = inp.get("file_path") or inp.get("notebook_path") or ""
        if "/golden/" in path or path.startswith("golden/") or path.endswith("APPROVED.json"):
            block(f"{tool} on {path}")
    elif tool == "Bash":
        cmd = inp.get("command", "")
        # 명령으로 실행하는 경우만 (명령 줄 맨 앞, 또는 ; && || | 뒤). 문서·코드 편집 안의 문구는 막지 않는다.
        # 파이썬에서 approve()를 직접 부르는 경로는 approve() 자신의 터미널(TTY) 검사가 막는다.
        if re.search(r"(^|[;&|]\s*|\n\s*)(uv\s+run\s+)?(jev-e2e|python3?\s+-m\s+jev_e2e\.cli)\s+approve\b", cmd) \
                or re.search(r"\S*APPROVED\.json", cmd):
            block("approving or touching APPROVED.json")
        rest = ALLOWED_FLAGS.sub("", cmd)
        if "golden/" in rest and WRITE_VERBS.search(rest):
            block("shell command that writes into golden/")


if __name__ == "__main__":
    main()
