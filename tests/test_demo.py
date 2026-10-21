import ast
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]


class DemoTests(unittest.TestCase):
    def test_one_marimo_demo_with_explicit_actions(self):
        notebooks = list((ROOT / 'notebooks').glob('*.py'))
        self.assertEqual([path.name for path in notebooks], ['api.py'])
        source = notebooks[0].read_text()
        ast.parse(source)
        self.assertIn('mo.stop(not open_experiment.value)', source)
        self.assertIn('if publish.value:', source)
        self.assertIn('mo.stop(not download.value)', source)
        self.assertIn("kind='password'", source)
        self.assertNotIn('password123', source)
        self.assertFalse(list((ROOT / 'examples').glob('*.ipynb')))

    def test_markdown_limit(self):
        paths = [ROOT / 'README.md', *(ROOT / 'docs').rglob('*.md')]
        for path in paths:
            self.assertLessEqual(len(path.read_text().split()), 500, str(path))


if __name__ == '__main__':
    unittest.main()
