#!/usr/bin/env python3
"""Validate the CLI examples embedded in the docs.

Anti-rot guard (docs improvement plan, P3). Deterministic and offline — it does
NOT run live crawls. It checks the things that actually broke before:

  1. the `opc` console script resolves on PATH;
  2. every `opc <subcommand>` used in a fenced ```bash block is a real command;
  3. every long `--flag` used with that subcommand exists in its `--help`.

This would have caught the three real doc bugs from this session: `opc` not
being installed, the (mistaken) "no `--platforms` flag" claim, and any future
flag rename. Run it in CI or locally:

    python tools/check_doc_examples.py        # exits non-zero on any problem

Scope: scans README.md and docs/*.md (skips docs/superpowers/). Lines with
placeholder seeds like `<seed>` are fine — only the command + flags are checked.
"""
from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ANSI = re.compile(r"\x1b\[[0-9;]*m")
BASH_BLOCK = re.compile(r"```bash\n(.*?)```", re.DOTALL)
LONG_FLAG = re.compile(r"--[a-z][a-z0-9-]+")
CLI_NAMES = ("opc", "open-pulse-crawler")


def doc_files() -> list[Path]:
    files = [ROOT / "README.md"]
    files += [p for p in (ROOT / "docs").glob("*.md")]
    return [f for f in files if f.is_file()]


def logical_lines(block: str) -> list[str]:
    """Join shell line-continuations (`\\` at EOL) into single logical lines."""
    out, buf = [], ""
    for raw in block.splitlines():
        line = raw.rstrip()
        if line.endswith("\\"):
            buf += line[:-1] + " "
            continue
        buf += line
        out.append(buf.strip())
        buf = ""
    if buf.strip():
        out.append(buf.strip())
    return out


def parse_invocation(line: str):
    """Return (subcommand, [long_flags]) for an `opc …` line, or None."""
    toks = line.split()
    # Drop leading VAR=value env assignments.
    i = 0
    while i < len(toks) and re.fullmatch(r"[A-Z_][A-Z0-9_]*=.*", toks[i]):
        i += 1
    if i >= len(toks) or toks[i] not in CLI_NAMES:
        return None
    rest = toks[i + 1:]
    sub = next((t for t in rest if not t.startswith("-")), None)
    flags = [f.split("=")[0] for f in LONG_FLAG.findall(line)]
    return sub, flags


def help_flags(sub: str) -> set[str] | None:
    """Long flags accepted by `opc <sub> --help`, or None if the subcommand is unknown."""
    env = {**os.environ, "COLUMNS": "200", "TERM": "dumb", "NO_COLOR": "1"}
    r = subprocess.run([CLI_NAMES[0], sub, "--help"], capture_output=True, text=True, env=env)
    if r.returncode != 0:
        return None
    text = ANSI.sub("", r.stdout + r.stderr)
    return set(LONG_FLAG.findall(text))


def main() -> int:
    problems: list[str] = []

    if shutil.which(CLI_NAMES[0]) is None:
        print(f"FAIL: `{CLI_NAMES[0]}` is not on PATH — install with `pip install -e .`")
        return 1

    flag_cache: dict[str, set[str] | None] = {}
    checked = 0
    for f in doc_files():
        text = f.read_text()
        for block in BASH_BLOCK.findall(text):
            for line in logical_lines(block):
                inv = parse_invocation(line)
                if inv is None:
                    continue
                sub, flags = inv
                if sub is None:  # bare `opc` / `opc --help`
                    continue
                checked += 1
                if sub not in flag_cache:
                    flag_cache[sub] = help_flags(sub)
                valid = flag_cache[sub]
                if valid is None:
                    problems.append(f"{f.relative_to(ROOT)}: unknown subcommand `opc {sub}`")
                    continue
                for fl in flags:
                    if fl not in valid:
                        problems.append(
                            f"{f.relative_to(ROOT)}: `opc {sub}` has no flag `{fl}`"
                        )

    if problems:
        print(f"Doc example check FAILED ({len(problems)} problem(s)):")
        for p in sorted(set(problems)):
            print("  -", p)
        return 1
    print(f"Doc example check OK — validated {checked} `opc` invocation(s) across "
          f"{len(doc_files())} files.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
