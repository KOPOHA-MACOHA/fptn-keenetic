import importlib.util
import pathlib
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("prepare", ROOT / "scripts/prepare-source.py")
prepare = importlib.util.module_from_spec(spec)
spec.loader.exec_module(prepare)


class SourceGuardTest(unittest.TestCase):
    def test_upstream_change_is_rejected_without_writing(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            file = root / "source.cpp"
            for content in ["changed upstream", "needle needle"]:
                file.write_text(content)
                with self.assertRaises(ValueError):
                    prepare.replace_once(root, "source.cpp", "needle", "replacement")
                self.assertEqual(file.read_text(), content)

    def test_wrong_binary_is_rejected(self):
        spec = importlib.util.spec_from_file_location("elf", ROOT / "scripts/verify-elf.py")
        elf = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(elf)
        with tempfile.TemporaryDirectory() as tmp:
            path = pathlib.Path(tmp) / "binary"
            path.write_bytes(b"not an executable")
            with self.assertRaises(ValueError):
                elf.verify(path)
