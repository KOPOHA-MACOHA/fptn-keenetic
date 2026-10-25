import importlib.util
import pathlib
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("includes", ROOT / "scripts/check-cross-includes.py")
includes = importlib.util.module_from_spec(spec)
spec.loader.exec_module(includes)


class CrossIncludesTest(unittest.TestCase):
    def test_rejects_libtuntap_host_paths_and_system_variants(self):
        command = 'gcc -I/usr/include/ -I /usr/local/include -isystem /usr/include/x86_64-linux-gnu -c tuntap.c'
        self.assertEqual(len(includes.host_includes(command)), 3)

    def test_preserves_sdk_and_dependency_paths(self):
        command = 'gcc -I/builder/staging_dir/toolchain/include -I/build/_deps/libtuntap-src -I/root/.conan2/p/include -c tuntap.c'
        self.assertEqual(includes.host_includes(command), [])

    def test_understands_argument_arrays_and_quoted_paths(self):
        self.assertEqual(includes.host_includes(['gcc', '-isystem/usr/include']), ['/usr/include'])
        self.assertEqual(includes.host_includes('gcc -I"/build/path with spaces"'), [])
