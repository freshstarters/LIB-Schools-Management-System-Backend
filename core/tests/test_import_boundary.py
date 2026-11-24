import ast
from pathlib import Path

from django.conf import settings
from django.test import SimpleTestCase


class ImportBoundaryTests(SimpleTestCase):
    def test_core_does_not_import_demo(self):
        offenders = []
        for path in (Path(settings.BASE_DIR) / "core").rglob("*.py"):
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            for node in ast.walk(tree):
                modules = []
                if isinstance(node, ast.Import):
                    modules = [alias.name for alias in node.names]
                elif isinstance(node, ast.ImportFrom) and node.module:
                    modules = [node.module]
                for module in modules:
                    if module == "demo" or module.startswith("demo."):
                        offenders.append(f"{path}:{node.lineno} imports {module}")
        self.assertEqual(offenders, [])
