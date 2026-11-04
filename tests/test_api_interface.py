import dataclasses
import json
from pathlib import Path
import re
import tempfile
import unittest

import dbstim
from tests.http_fixture import server
from tests.test_examples import checked_out_session


ROOT = Path(__file__).resolve().parents[1]
HTML = (ROOT / 'docs' / 'api-interface.html').read_text()
MARKDOWN = (ROOT / 'docs' / 'api-interface.md').read_text()


def public(obj) -> set[str]:
    """Public names of a live object: its methods, properties and instance attributes."""
    return {name for name in dir(obj) if not name.startswith('_')}


def sign_in(method, path, headers, body):
    payload = json.dumps(dict(csrf_token='csrf', user=dict(username='demo'))).encode()
    return 200, {'Content-Type': 'application/json'}, payload


class ApiInterfaceDocsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        origin, _ = cls.enterClassContext(server(sign_in))
        client = cls.enterClassContext(dbstim.Client('demo', 'password', url=origin))
        session = checked_out_session(Path(cls.enterClassContext(tempfile.TemporaryDirectory())))
        # The variable names the docs use for each workflow object.
        cls.members = dict(client=public(client), experiment=public(dbstim.Experiment(client, 'experiment-1')),
                           session=public(session), run=public(session.run(1)))

    def test_html_is_self_contained(self):
        self.assertNotRegex(HTML, r'''(?i)(src|href)\s*=\s*["']?(https?:)?//''')
        self.assertNotRegex(HTML, r'(?i)<link\b|@import|url\((?!#)|fetch\(|XMLHttpRequest|<iframe')
        self.assertIn('<svg', HTML)

    def test_public_members_are_shown(self):
        for name in dbstim.__all__:
            self.assertIn(name, HTML)
        for variable, names in self.members.items():
            for name in names:
                with self.subTest(member=f'{variable}.{name}'):
                    self.assertIn(f'{variable}.{name}', HTML)
        for cls in (dbstim.Trial, dbstim.Filter, dbstim.Segment, dbstim.Metric, dbstim.MetricFilter):
            for field in dataclasses.fields(cls):
                self.assertIn(f'<code>{field.name}</code>', HTML)
        for outcome in ('accepted', 'already synced', 'kept as flagged attempt', 'waiting'):
            self.assertIn(f'"{outcome}"', HTML)

    def test_docs_name_only_real_members(self):
        for path, text in (('api-interface.html', HTML), ('api-interface.md', MARKDOWN)):
            for variable, name in re.findall(r'\b(client|experiment|session|run|dbstim)\.(?!py\b)([a-z_]+)', text):
                with self.subTest(doc=path, reference=f'{variable}.{name}'):
                    known = set(dbstim.__all__) if variable == 'dbstim' else self.members[variable]
                    self.assertIn(name, known)

    def test_retired_0_5_calls_are_gone(self):
        for path, text in (('api-interface.html', HTML), ('api-interface.md', MARKDOWN)):
            for retired in ('next_run', 'next_trial', 'record(', 'unconfirmed', '.subjects', 'mode="remote"'):
                with self.subTest(doc=path, retired=retired):
                    self.assertNotIn(retired, text)

    def test_markdown_has_mermaid_and_both_files_link_each_other(self):
        self.assertEqual(MARKDOWN.count('```mermaid\n'), 2)
        self.assertIn('(api-interface.html)', MARKDOWN)
        self.assertIn('href="api-interface.md"', HTML)
        for text in (HTML, MARKDOWN):
            self.assertIn(dbstim.__version__, text)


if __name__ == '__main__':
    unittest.main()
