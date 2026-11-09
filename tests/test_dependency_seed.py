"""An incompatible/corrupt shared package cache cannot become a Docker context."""
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import shutil
import sys
import tarfile
import tempfile
import unittest
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location('seed',ROOT/'scripts/seed-conan-cache.py')
seed=importlib.util.module_from_spec(spec);spec.loader.exec_module(seed)


class SharedCacheTest(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name)
        shutil.copytree(ROOT/'build-locks',self.root/'build-locks')
        (self.root/'scripts').mkdir()
        shutil.copyfile(ROOT/'scripts/dependency-image.py',self.root/'scripts/dependency-image.py')
        self.archive=self.root/'dependencies.tar'
        self.output=self.root/'output'

    def record(self):
        return json.loads((self.root/'build-locks/shared-build-cache.json').read_text())

    def save(self,record):
        (self.root/'build-locks/shared-build-cache.json').write_text(json.dumps(record))

    def invoke(self):
        return [patch.object(seed,'ROOT',self.root),patch.object(sys,'argv',['seed',str(self.archive)]),
                patch.dict(os.environ,{'GITHUB_OUTPUT':str(self.output)})]

    def test_changed_tools_do_not_import_an_old_cache(self):
        self.assertEqual(self.record()['locksSha256'],seed.lock_digest(self.root/'build-locks'))
        path=self.root/'build-locks/apt.txt';path.write_text(path.read_text()+'changed=1\n')
        a,b,c=self.invoke()
        with a,b,c,patch.object(seed.subprocess,'run') as run:
            seed.main();run.assert_not_called()
        self.assertFalse(self.output.exists())

    def test_corruption_is_a_hard_error_and_publishes_no_context(self):
        def restored(*args,**kwargs):
            with tarfile.open(self.archive,'w') as archive:
                content=b'not an OCI layout';info=tarfile.TarInfo('oci-layout');info.size=len(content)
                archive.addfile(info,io.BytesIO(content))
        a,b,c=self.invoke()
        with a,b,c,patch.object(seed.subprocess,'run',side_effect=restored), self.assertRaises(ValueError):
            seed.main()
        self.assertFalse(self.output.exists())
