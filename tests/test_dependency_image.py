import hashlib
import importlib.util
import io
import json
from pathlib import Path
import shutil
import tarfile
import tempfile
import unittest

ROOT=Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location('dependency_image',ROOT/'scripts/dependency-image.py')
image=importlib.util.module_from_spec(spec); spec.loader.exec_module(image)


class DependencyImageTest(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name)

    def test_key_ignores_client_changes_but_tracks_sdk_and_dependency_inputs(self):
        files=['Dockerfile',*image.INPUTS]
        for name in files:
            dest=self.root/name; dest.parent.mkdir(parents=True,exist_ok=True)
            shutil.copyfile(ROOT/name,dest)
        before=image.cache_key(self.root)
        docker=self.root/'Dockerfile'
        docker.write_text(docker.read_text()+'\n# changed native client implementation\n')
        (self.root/'src').mkdir(); (self.root/'src/native.cpp').write_text('new app code')
        self.assertEqual(image.cache_key(self.root),before)
        lock=self.root/'sources.lock.json'; lock.write_text(lock.read_text()+'\n')
        self.assertNotEqual(image.cache_key(self.root),before)
        shutil.copyfile(ROOT/'sources.lock.json',lock)
        docker.write_text(docker.read_text().replace('ramips-mt7621','ramips-mt7622',1))
        self.assertNotEqual(image.cache_key(self.root),before)

    def make_layout(self):
        layout=self.root/'layout'; (layout/'blobs/sha256').mkdir(parents=True)
        (layout/'oci-layout').write_text('{"imageLayoutVersion":"1.0.0"}')
        def blob(data):
            digest=hashlib.sha256(data).hexdigest(); (layout/'blobs/sha256'/digest).write_bytes(data)
            return {'digest':'sha256:'+digest,'size':len(data)}
        config=blob(json.dumps({'os':'linux','architecture':'amd64'}).encode())
        layer=blob(b'example layer')
        manifest=blob(json.dumps({'schemaVersion':2,'config':config,'layers':[layer]}).encode())
        (layout/'index.json').write_text(json.dumps({'schemaVersion':2,'manifests':[manifest]}))
        return layout,manifest,layer

    def test_unpacks_and_selects_integrity_checked_image(self):
        layout,manifest,_=self.make_layout()
        archive=self.root/'image.tar'
        with tarfile.open(archive,'w') as tar:
            for file in layout.rglob('*'): tar.add(file,arcname=str(file.relative_to(layout)),recursive=False)
        restored=self.root/'restored'; image.unpack(archive,restored)
        self.assertEqual(image.image_digest(restored),manifest['digest'])

    def test_corrupt_layer_is_rejected_before_build(self):
        layout,_,layer=self.make_layout()
        blob=layout/'blobs/sha256'/layer['digest'].split(':')[1]
        blob.write_bytes(b'x'*layer['size'])
        with self.assertRaisesRegex(ValueError,'Corrupt blob'): image.image_digest(layout)

    def test_wrong_blob_size_is_rejected(self):
        layout,_,layer=self.make_layout()
        (layout/'blobs/sha256'/layer['digest'].split(':')[1]).write_bytes(b'truncated')
        with self.assertRaisesRegex(ValueError,'Wrong blob size'): image.image_digest(layout)

    def test_unsafe_archive_members_are_rejected(self):
        for name,symlink in [('../escape',False),('/etc/escape',False),('index.json',True)]:
            with self.subTest(name=name,symlink=symlink):
                archive=self.root/'unsafe.tar'
                with tarfile.open(archive,'w') as tar:
                    member=tarfile.TarInfo(name)
                    if symlink: member.type=tarfile.SYMTYPE; member.linkname='/etc/passwd'; tar.addfile(member)
                    else: member.size=1; tar.addfile(member,io.BytesIO(b'x'))
                with self.assertRaises(ValueError): image.unpack(archive,self.root/'bad')
