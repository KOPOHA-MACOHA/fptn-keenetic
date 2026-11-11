#!/usr/bin/env python3
"""Fail before compiling if host system headers leak into cross-build flags."""
import json
import pathlib
import shlex
import sys


def host_includes(command):
    tokens = shlex.split(command) if isinstance(command, str) else command
    paths = []
    for index, token in enumerate(tokens):
        if token in ("-I", "-isystem", "-idirafter", "-iquote"):
            if index + 1 < len(tokens):
                paths.append(tokens[index + 1])
        else:
            for prefix in ("-isystem", "-idirafter", "-iquote", "-I"):
                if token.startswith(prefix) and len(token) > len(prefix):
                    paths.append(token[len(prefix):])
                    break
    roots = ("/usr/include", "/usr/local/include")
    return [p for p in paths if any(p.rstrip("/") == root or p.startswith(root + "/") for root in roots)]


if __name__ == "__main__":
    commands = json.loads(pathlib.Path(sys.argv[1]).read_text())
    if not commands:
        sys.exit("Empty compilation database")
    failures = [(item["file"], host_includes(item.get("arguments", item.get("command", ""))))
                for item in commands]
    failures = [(name, paths) for name, paths in failures if paths]
    if failures:
        for name, paths in failures:
            print(f"Host headers in {name}: {paths}", file=sys.stderr)
        sys.exit(1)
    print(f"Cross-build include isolation: PASS ({len(commands)} compilation units)")
