import ast
import dataclasses
import inspect
from pathlib import Path
import re
import textwrap
import unittest

import dbstim


ROOT = Path(__file__).resolve().parents[1]
HTML = (ROOT / 'docs' / 'api-interface.html').read_text()
MARKDOWN = (ROOT / 'docs' / 'api-interface.md').read_text()
# The variable names the docs use for each workflow object.
OBJECTS = {'client': dbstim.Client, 'experiment': dbstim.Experiment, 'subject': dbstim.Subject,
           'session': dbstim.Session, 'run': dbstim.Run}


def members(cls) -> set[str]:
    """Public methods and properties, plus public attributes assigned in __init__."""
    names = {name for name in vars(cls) if not name.startswith('_')}
    tree = ast.parse(textwrap.dedent(inspect.getsource(cls.__init__)))
    names.update(node.attr for node in ast.walk(tree) if isinstance(node, ast.Attribute)
                 and isinstance(node.value, ast.Name) and node.value.id == 'self' and not node.attr.startswith('_'))
    return names


class ApiInterfaceDocsTests(unittest.TestCase):
    def test_html_is_self_contained(self):
        self.assertNotRegex(HTML, r'''(?i)(src|href)\s*=\s*["']?(https?:)?//''')
        self.assertNotRegex(HTML, r'(?i)<link\b|@import|url\((?!#)|fetch\(|XMLHttpRequest|<iframe')
        self.assertIn('<svg', HTML)

    def test_every_public_member_is_shown(self):
        for name in dbstim.__all__:
            self.assertIn(name, HTML)
        for variable, cls in OBJECTS.items():
            for name in members(cls):
                with self.subTest(member=f'{variable}.{name}'):
                    self.assertIn(f'{variable}.{name}', HTML)
        for cls in (dbstim.Trial, dbstim.Segment, dbstim.Metric, dbstim.MetricFilter):
            for field in dataclasses.fields(cls):
                self.assertIn(f'<code>{field.name}</code>', HTML)

    def test_docs_name_only_real_members(self):
        for path, text in (('api-interface.html', HTML), ('api-interface.md', MARKDOWN)):
            for variable, name in re.findall(r'\b(client|experiment|subject|session|run|dbstim)\.(?!py\b)([a-z_]+)', text):
                with self.subTest(doc=path, reference=f'{variable}.{name}'):
                    known = set(dbstim.__all__) if variable == 'dbstim' else members(OBJECTS[variable])
                    self.assertIn(name, known)

    def test_markdown_has_mermaid_and_both_files_link_each_other(self):
        self.assertEqual(MARKDOWN.count('```mermaid\n'), 2)
        self.assertIn('(api-interface.html)', MARKDOWN)
        self.assertIn('href="api-interface.md"', HTML)
        for text in (HTML, MARKDOWN):
            self.assertIn(dbstim.__version__, text)


if __name__ == '__main__':
    unittest.main()
