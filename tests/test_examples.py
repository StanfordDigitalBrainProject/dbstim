"""The PGL example is user code: it must load without PGL, and dbstim must never import PGL."""
import ast
from contextlib import redirect_stdout
import csv
import io
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
from types import ModuleType, SimpleNamespace
import unittest
from unittest.mock import patch

import dbstim

ROOT = Path(__file__).resolve().parents[1]
EXAMPLE = ROOT / 'examples' / 'pgl_tracked_movie_task.py'


def load_example(pgl_module):
    """Import the example file with sys.modules['pgl'] set to pgl_module (None hides PGL)."""
    spec = importlib.util.spec_from_file_location('pgl_tracked_movie_task', EXAMPLE)
    module = importlib.util.module_from_spec(spec)
    with patch.dict(sys.modules, {'pgl': pgl_module}):
        spec.loader.exec_module(module)
    return module


class FakeTask:
    """The parts of pglTask the example relies on."""

    def __init__(self, pgl):
        self.pgl = pgl
        self.settings = SimpleNamespace()
        self.state = SimpleNamespace(currentTrial=0, currentSegment=-1)
        self.jumps = 0

    def startSegment(self, startTime):
        self.state.currentSegment += 1

    def jumpSegment(self):
        self.jumps += 1


class FakeMovie:
    def __init__(self, loads, frames_shown):
        self.movieNum = 1 if loads else None
        self.frames_shown = frames_shown

    def play(self):
        frames = [i / 30 for i in range(135)]
        self.movieTimes = frames
        self.presentedTimes = [100 + t if i < self.frames_shown else 0 for i, t in enumerate(frames)]
        self.duration = 4.5


def fake_pgl():
    module = ModuleType('pgl')
    module.pgl, module.pglExperiment, module.pglTask = object, object, FakeTask
    return module


class PglExampleTests(unittest.TestCase):
    def test_example_parses_and_imports_without_pgl(self):
        ast.parse(EXAMPLE.read_text(), str(EXAMPLE))
        module = load_example(None)
        self.assertIsNone(module.PGL)
        with self.assertRaises(SystemExit):
            module.run('unused-folder', 1)
        with self.assertRaises(SystemExit), redirect_stdout(io.StringIO()):
            module.main(['--help'])

    def test_dbstim_never_imports_pgl(self):
        for path in (ROOT / 'src' / 'dbstim').glob('*.py'):
            for node in ast.walk(ast.parse(path.read_text())):
                names = ([alias.name for alias in node.names] if isinstance(node, ast.Import)
                         else [node.module or ''] if isinstance(node, ast.ImportFrom) else [])
                for name in names:
                    self.assertNotEqual(name.split('.')[0], 'pgl', f'{path.name} imports {name}')

    def test_task_hooks_record_completed_load_failure_and_partial_play(self):
        root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        state = root / 'stimuli' / '.dbstim'
        state.mkdir(parents=True)
        media = [dict(media_id=f'm{i}', catalog_id=f'v{i}', file_name=f'v{i}.mp4', duration_seconds=4.5)
                 for i in range(1, 4)]
        (root / 'stimuli/manifest.json').write_text(json.dumps(dict(media=media)))
        trials = [dict(trial_id=f't{i}', media_id=f'v{i}', segment=None, manifest_media_id=f'm{i}', role='parent')
                  for i in range(1, 4)]
        (state / 'session.json').write_text(json.dumps(dict(
            schema_version=1, origin='http://localhost:1', username='demo', experiment_id='a' * 32, subject=1,
            session=1, manifest_sha256='b' * 64, blocks=[dict(run_id='run-001', trials=trials)])))
        module = load_example(fake_pgl())
        movies = iter([FakeMovie(True, 135), FakeMovie(False, 0), FakeMovie(True, 60)])
        pgl = SimpleNamespace(movie=lambda path, displayWidth: next(movies))
        with dbstim.session(root).run(1) as run:
            task = module.TrackedMovieTask(pgl, run)
            self.assertEqual(task.settings.nTrials, 3)
            task.startTask(10.0)
            for number in range(3):
                task.state.currentTrial, task.state.currentSegment = number, -1
                task.startSegment(12.0 + 5 * number)
                task.startSegment(12.5 + 5 * number)
            task.endTask()
        self.assertEqual(task.jumps, 3)
        with next(root.glob('sub-01/ses-01/beh/*_run-01_events.tsv')).open() as stream:
            rows = list(csv.DictReader(stream, delimiter='\t'))
        self.assertEqual([row['outcome'] for row in rows], ['completed', 'abandoned', 'abandoned'])
        self.assertEqual([float(row['onset']) for row in rows], [2.5, 7.0, 12.5])
        self.assertAlmostEqual(float(rows[0]['duration']), 4.5)
        self.assertIs(json.loads(rows[0]['result'])['reached_end'], True)
        self.assertEqual(float(rows[1]['duration']), 0)
        self.assertAlmostEqual(float(rows[2]['duration']), 2.0)


if __name__ == '__main__':
    unittest.main()
