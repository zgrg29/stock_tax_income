"""Deployment checks for the restored single-file Streamlit app."""
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from check_startup import check_imports

ROOT = Path(__file__).resolve().parent


class StartupTests(unittest.TestCase):
    def test_actual_imports_and_dependencies(self):
        self.assertIn('numpy', check_imports())

    def test_old_business_modules_are_not_needed_to_initialize_app(self):
        script = '''
import sys, types
from streamlit.testing.v1 import AppTest
for name in ('importers', 'cgt', 'stock_tax'):
    sys.modules[name] = types.ModuleType(name)
from check_startup import check_imports
check_imports()
app = AppTest.from_file('app.py').run(timeout=30)
assert not app.exception, [e.message for e in app.exception]
assert len(app.sidebar.selectbox) == 1
assert len(app.get('file_uploader')) == 2
'''
        result = subprocess.run([sys.executable, '-c', script], cwd=ROOT,
                                capture_output=True, text=True, timeout=45)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_missing_requirement_is_rejected(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / 'requirements.txt').write_text('streamlit\n', encoding='utf-8')
            with self.assertRaisesRegex(RuntimeError, 'missing dependencies'):
                check_imports(root)


if __name__ == '__main__':
    unittest.main()
