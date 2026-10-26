"""CLI help fixture shared by service, PHP and capability tests."""
from pathlib import Path
import shutil
import shlex
ROOT=Path(__file__).resolve().parents[1]
def help_text():return (ROOT/'tests/fixtures/client-help.txt').read_text()
def install(root):
    lib=root/'libexec/fptn';lib.mkdir(parents=True,exist_ok=True)
    shutil.copyfile(ROOT/'scripts/fptn-client-capabilities.sh',lib/'client-capabilities.sh')
    client=lib/'fptn-client-cli'
    client.write_text('#!/bin/sh\ncase "$1" in\n --help) cat '+shlex.quote(str(ROOT/'tests/fixtures/client-help.txt'))+' ;;\n --version) echo 0.4.0 ;;\n *) exit 1 ;;\nesac\n');client.chmod(0o700)
