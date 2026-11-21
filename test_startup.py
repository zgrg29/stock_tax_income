"""Deployment regressions, including a long-lived process with old modules."""
import ast
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from check_startup import check_imports

ROOT = Path(__file__).resolve().parent


class StartupTests(unittest.TestCase):
    def test_internal_symbols_requirements_and_import_graph(self):
        versions = check_imports()
        self.assertIn('streamlit', versions)

    def test_legacy_api_is_the_same_implementation(self):
        import cgt
        import importers
        from stock_tax import cgt as packaged_cgt, importers as packaged_importers
        self.assertIs(cgt.Transaction, packaged_cgt.Transaction)
        self.assertIs(importers.BrokerImporter, packaged_importers.BrokerImporter)

    def test_old_or_foreign_top_level_modules_cannot_break_application(self):
        script = '''
import sys, types
from streamlit.testing.v1 import AppTest
for name in ('importers', 'cgt'):
    old = types.ModuleType(name)
    old.__file__ = '/previous-release/' + name + '.py'
    sys.modules[name] = old
from check_startup import check_imports
check_imports()
app = AppTest.from_file('app.py').run(timeout=30)
assert not app.exception, [e.message for e in app.exception]
assert len(app.sidebar.selectbox) == 2
assert len(app.sidebar.button) == 1
print('Isolated package initialized with conflicting old modules present')
'''
        result = subprocess.run([sys.executable, '-c', script], cwd=ROOT,
                                capture_output=True, text=True, timeout=45)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_package_importers_uses_relative_cgt_import(self):
        tree = ast.parse((ROOT / 'stock_tax' / 'importers.py').read_text(encoding='utf-8'))
        imports = [node for node in ast.walk(tree) if isinstance(node, ast.ImportFrom) and node.module == 'cgt']
        self.assertTrue(imports)
        self.assertTrue(all(node.level == 1 for node in imports))

    def test_missing_requirement_is_rejected(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / 'requirements.txt').write_text('streamlit\n', encoding='utf-8')
            with self.assertRaisesRegex(RuntimeError, 'missing dependencies'):
                check_imports(root)


if __name__ == '__main__':
    unittest.main()
