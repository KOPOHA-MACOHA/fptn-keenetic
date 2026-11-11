import importlib.util
import pathlib
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("runtime", ROOT / "scripts/fix-static-libgcc.py")
runtime = importlib.util.module_from_spec(spec)
spec.loader.exec_module(runtime)


class StaticRuntimeTest(unittest.TestCase):
    def test_static_archives_follow_application_libraries(self):
        original = "*libgcc:\n%{static|static-libgcc:-lgcc}%{!static:%{!static-libgcc:-lgcc_s}}\n"
        result = runtime.patch_specs(original)
        self.assertIn("%{static|static-libgcc:-lgcc -lgcc_eh -latomic}", result)
        self.assertTrue(result.endswith("%{!static:%{!static-libgcc:-lgcc_s}}\n"))

    def test_unknown_or_already_patched_specs_are_rejected(self):
        for value in ["changed", "%{static|static-libgcc:-lgcc}" * 2,
                      "%{static|static-libgcc:-lgcc -lgcc_eh -latomic}"]:
            with self.assertRaises(ValueError):
                runtime.patch_specs(value)
