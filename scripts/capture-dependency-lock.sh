#!/bin/sh
# Read only from an existing dependency image. Never installs packages.
set -eu
mkdir -p /evidence
set -- --profile:build=default --profile:host=/work/profile -s:b compiler.cppstd=20 -o with_gui_client=False -o openwrt=True --no-remote
conan lock create . "$@" --lockfile-out=/evidence/conan.lock
conan graph info . "$@" --lockfile=/evidence/conan.lock --format=json > /evidence/graph.json
conan profile show --profile:build=default --profile:host=/work/profile -s:b compiler.cppstd=20 --format=json > /evidence/profiles.json
python - <<'PY'
import json, subprocess, pathlib
run = lambda *args: subprocess.check_output(args, text=True).strip()
tools = {'pip': run('python', '-m', 'pip', 'freeze', '--all').splitlines(),
         'conan': run('conan', '--version'), 'cmake': run('cmake', '--version').splitlines()[0],
         'ninja': run('ninja', '--version'),
         'dpkg': run('dpkg-query', '-W', '-f=${Package}=${Version}\n').splitlines(),
         'buildProfile': pathlib.Path('/root/.conan2/profiles/default').read_text(),
         'hostProfile': pathlib.Path('/work/profile').read_text()}
pathlib.Path('/evidence/tools.json').write_text(json.dumps(tools, indent=2)+'\n')
PY
