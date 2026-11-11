#!/usr/bin/env python3
"""Import the exact adaptation tree and its history on a migration branch only."""
import json
import os
from pathlib import Path
import subprocess

UPSTREAM = "a389bee62da930807c3bc85284ee4976e9c7e08f"
LEGACY = "efbf0d550f9a03c437ff03714cbecc9d6f5b6f22"
BRANCH = "migration/keenetic"


def git(*args):
    return subprocess.check_output(["git", *args], text=True).strip()


def main():
    if git("branch", "--show-current") != BRANCH:
        raise SystemExit("Import is restricted to the migration branch")
    if git("status", "--porcelain"):
        raise SystemExit("Import requires a clean checkout")
    if Path("keenetic").exists():
        raise SystemExit("Adaptation already exists; refusing to overwrite it")
    git("merge-base", "--is-ancestor", UPSTREAM, "HEAD")
    legacy_tree = git("rev-parse", LEGACY + "^{tree}")
    lock = json.loads(git("show", LEGACY + ":sources.lock.json"))
    if lock["fptn"] != UPSTREAM:
        raise SystemExit("Adaptation and upstream source revisions do not match")
    parent = git("rev-parse", "HEAD")
    git("read-tree", "--prefix=keenetic/", "-u", LEGACY)
    tree = git("write-tree")
    imported = git("rev-parse", tree + ":keenetic")
    if imported != legacy_tree:
        raise SystemExit("Imported tree differs from the original adaptation")
    candidate = git("commit-tree", tree, "-p", parent, "-p", LEGACY, "-m",
                    "Import Keenetic adaptation with its complete commit history")
    git("update-ref", "refs/heads/" + BRANCH, candidate, parent)
    report = {"schema": 1, "upstreamRevision": UPSTREAM,
              "legacyRepository": "KOPOHA-MACOHA/fptn-keenetic",
              "legacyRevision": LEGACY, "legacyTree": legacy_tree,
              "importedTree": imported, "migrationRevision": candidate,
              "readyForInstallation": False}
    print(json.dumps(report, indent=2))
    output = os.environ.get("GITHUB_OUTPUT")
    if output:
        with open(output, "a") as stream:
            stream.write("candidate=" + candidate + "\n")


if __name__ == "__main__":
    main()
