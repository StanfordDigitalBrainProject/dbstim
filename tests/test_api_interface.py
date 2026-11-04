from pathlib import Path
import unittest

import dbstim


ROOT = Path(__file__).resolve().parents[1]
HTML = (ROOT / 'docs' / 'api-interface.html').read_text()
MARKDOWN = (ROOT / 'docs' / 'api-interface.md').read_text()


class ApiInterfaceDocsTests(unittest.TestCase):
    def test_html_is_self_contained(self):
        self.assertNotRegex(HTML, r'''(?i)(src|href)\s*=\s*["']?(https?:)?//''')
        self.assertNotRegex(HTML, r'(?i)<link\b|@import|url\((?!#)|fetch\(|XMLHttpRequest|<iframe')
        self.assertIn('<svg', HTML)

    # The API visual and its surface coverage move to 1.0 in D3.

    def test_markdown_has_mermaid_and_both_files_link_each_other(self):
        self.assertEqual(MARKDOWN.count('```mermaid\n'), 2)
        self.assertIn('(api-interface.html)', MARKDOWN)
        self.assertIn('href="api-interface.md"', HTML)
        for text in (HTML, MARKDOWN):
            self.assertIn(dbstim.__version__, text)


if __name__ == '__main__':
    unittest.main()
