"""Small subprocess regressions for the release helper; no Docker or live state."""
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest


class ReleaseHelper(unittest.TestCase):
    def run_release(self, passes, existing_output=None):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / 'optionb').mkdir()
            script = root / 'optionb/make_release.sh'
            shutil.copyfile(Path(__file__).parent / 'optionb/make_release.sh', script)
            (root / 'blr_stop.py').write_text(
                "CONTAINER = '" + 'a' * 64 + "'\nACCEPTED_EXIT_CODE = 2 # prior version\n")
            for name in ('blr_sleep_now.py', 'blr_idle_monitor.py'):
                (root / name).write_text('# fixture\n')
            (root / 'test_blr_sleepwake.py').write_text(
                'import unittest\nclass Fixture(unittest.TestCase):\n'
                f'    def test_result(self): self.assertTrue({passes!r})\n')
            out = root / 'release'
            protected = None
            if existing_output:
                retained = root / 'retained-release'
                retained.mkdir()
                protected = retained / 'blr_stop.py'
                protected.write_text('retained rollback bytes\n')
                if existing_output == 'symlink':
                    out.symlink_to(retained, target_is_directory=True)
                else:
                    retained.rename(out)
                    protected = out / 'blr_stop.py'
            result = subprocess.run(
                ['sh', str(script), 'b' * 64, str(out)],
                capture_output=True, text=True, timeout=15)
            if protected:
                self.assertEqual(protected.read_text(), 'retained rollback bytes\n')
                self.assertEqual([p.name for p in protected.parent.iterdir()], ['blr_stop.py'])
            return result

    def test_failing_tests_reject_release_without_hashes(self):
        result = self.run_release(False)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('FAILED', result.stdout + result.stderr)
        self.assertNotRegex(result.stdout, r'(?m)^[0-9a-f]{64}  ')

    def test_passing_tests_allow_release_hashes(self):
        result = self.run_release(True)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn('OK', result.stdout + result.stderr)
        self.assertRegex(result.stdout, r'(?m)^[0-9a-f]{64}  blr_stop.py$')

    def test_existing_release_is_not_overwritten(self):
        self.assertNotEqual(self.run_release(True, 'directory').returncode, 0)

    def test_symlink_to_retained_release_is_not_followed(self):
        self.assertNotEqual(self.run_release(True, 'symlink').returncode, 0)


if __name__ == '__main__':
    unittest.main()
