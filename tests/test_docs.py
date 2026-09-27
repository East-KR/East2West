"""문서 대조: README·CLAUDE.md·docs·스킬에 적힌 `eastshift <명령> --옵션`과 `pytest --옵션`이 실제로 있는지 본다.
이름을 바꾸거나 옵션을 없앴는데 문서가 옛 명령을 가르치면 여기서 실패한다 (사람도 에이전트도 문서를 보고 명령을 친다)."""
import argparse
import re
from pathlib import Path

import pytest

from eastshift import cli
from eastshift.pwtest import plugin

DOCS = sorted([Path("README.md"), Path("CLAUDE.md"), *Path("docs").glob("*.md"), *Path(".claude/skills").glob("*/SKILL.md")])
PYTEST_OWN = {"--junitxml", "--help", "-k", "-q", "-x", "-v", "-s"}  # pytest 자체 옵션 (플러그인 것이 아님)
CMD = re.compile(r"\b(?:uv run )?(eastshift|pytest)\b([^`\n]*)")


def _commands(text: str):
    """fenced 코드 블록 안의 줄과 본문 인라인 코드에서 명령을 뽑는다. 주석(#) 뒤와 <자리표시>는 버린다."""
    spans = re.findall(r"```[a-z]*\n(.*?)```", text, re.S)
    spans += re.findall(r"`([^`\n]*\b(?:eastshift|pytest)\b[^`\n]*)`", re.sub(r"```.*?```", "", text, flags=re.S))
    for span in spans:
        for line in span.splitlines():
            line = line.split(" #")[0]
            for m in CMD.finditer(line):
                yield m[1], m[2].split()


def _subcommands() -> dict[str, set[str]]:
    ap = cli.build_parser()
    sub = next(a for a in ap._actions if isinstance(a, argparse._SubParsersAction))
    return {name: {o for a in p._actions for o in a.option_strings} for name, p in sub.choices.items()}


def _plugin_options() -> set[str]:
    seen: set[str] = set()

    class Group:
        def addoption(self, *names, **kw):
            seen.update(names)

    class Parser:
        def getgroup(self, *a, **kw):
            return Group()

        addoption = Group.addoption

    plugin.pytest_addoption(Parser())
    return seen


@pytest.mark.parametrize("doc", DOCS, ids=str)
def test_documented_commands_exist(doc):
    subs, pyopts = _subcommands(), _plugin_options() | PYTEST_OWN
    bad = []
    for tool, args in _commands(doc.read_text(encoding="utf-8")):
        flags = [a.split("=")[0] for a in args if a.startswith("-") and not a.startswith("---")]
        if tool == "eastshift":
            if not args or args[0].startswith(("-", "<", "(")) or not re.fullmatch(r"[a-z][a-z-]*", args[0]):
                continue  # 문장 속 "eastshift" (도구 이름)
            if args[0] not in subs:
                bad.append(f"eastshift {args[0]}: 없는 명령")
                continue
            bad += [f"eastshift {args[0]} {f}: 없는 옵션" for f in flags if f not in subs[args[0]] | {"-h", "--help"}]
        else:
            bad += [f"pytest {f}: 플러그인에도 pytest 에도 없는 옵션" for f in flags if f not in pyopts]
    assert not bad, f"{doc}:\n" + "\n".join(sorted(set(bad)))


def test_scanner_sees_the_readme_commands():
    """대조가 헛돌지 않는지: README 에서 실제로 여러 명령을 뽑아야 한다."""
    found = {(t, a[0]) for t, a in _commands(Path("README.md").read_text(encoding="utf-8")) if a}
    assert {("eastshift", "ui"), ("eastshift", "mutate"), ("eastshift", "report"), ("eastshift", "crawl")} <= found
