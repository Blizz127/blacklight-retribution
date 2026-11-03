"""Exercise create-body output with synthetic inspect data, never Docker."""
import json
from pathlib import Path
import stat
import subprocess
import sys
import tempfile
import unittest

from optionb import recreate


class RecreateOutput(unittest.TestCase):
    def fixture(self, root):
        path = root / 'inspect.json'
        path.write_text(json.dumps([{
            'Id': recreate.OLD_ID,
            'Image': recreate.IMAGE + '0' * 52,
            'Config': {'Hostname': 'old', 'Labels': {}, 'Env': ['FIXTURE=value']},
            'HostConfig': {'Binds': []},
            'Mounts': [{'Type': 'volume', 'Destination': recreate.ASSETS, 'Name': 'fixture-assets'}],
            'NetworkSettings': {'Networks': {'blrevive_default': {}}},
        }]))
        return path

    def run_helper(self, source, output):
        return subprocess.run(
            [sys.executable, '-B', recreate.__file__, str(source), '--body', str(output)],
            capture_output=True, text=True, timeout=10)

    def test_existing_outputs_are_preserved(self):
        for kind in ('file', 'symlink', 'dangling_symlink', 'input_file'):
            with self.subTest(kind=kind), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                source = self.fixture(root)
                source_bytes = source.read_bytes()
                out = root / 'body.json'
                target = root / 'retained.json'
                if kind == 'input_file':
                    out = source
                elif kind == 'file':
                    out.write_bytes(b'retained rollback')
                else:
                    if kind == 'symlink':
                        target.write_bytes(b'retained rollback')
                    out.symlink_to(target)
                result = self.run_helper(source, out)
                self.assertNotEqual(result.returncode, 0)
                self.assertNotIn('wrote', result.stdout)
                self.assertEqual(source.read_bytes(), source_bytes)
                if kind == 'file':
                    self.assertEqual(out.read_bytes(), b'retained rollback')
                elif kind == 'symlink':
                    self.assertTrue(out.is_symlink())
                    self.assertEqual(target.read_bytes(), b'retained rollback')
                elif kind == 'dangling_symlink':
                    self.assertTrue(out.is_symlink())
                    self.assertFalse(target.exists())

    def test_new_output_is_private_and_complete(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = self.fixture(root)
            source_bytes = source.read_bytes()
            out = root / 'body.json'
            result = self.run_helper(source, out)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(stat.S_IMODE(out.stat().st_mode), 0o600)
            self.assertEqual(json.loads(out.read_text()), recreate.body(json.loads(source_bytes)[0]))
            self.assertEqual(source.read_bytes(), source_bytes)
            self.assertNotIn('FIXTURE=value', result.stdout + result.stderr)


if __name__ == '__main__':
    unittest.main()
