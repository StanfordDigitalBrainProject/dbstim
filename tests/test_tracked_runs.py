"""Offline records and the S3 run-sync wire contract."""
import csv
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

import dbstim


class LocalRuns(unittest.TestCase):
    def setUp(self):
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        state = self.root / 'stimuli' / '.dbstim'
        state.mkdir(parents=True)
        media = [dict(media_id=f'm{i}', catalog_id=f'v{i}', file_name=f'v{i}.mp4', duration_seconds=4.5)
                 for i in range(1, 4)]
        for row in media:
            (self.root / 'stimuli' / row['file_name']).write_bytes(b'movie')
        (self.root / 'stimuli/manifest.json').write_text(json.dumps(dict(media=media)))
        metadata = dict(schema_version=1, origin='http://localhost:12345', username='admin',
                        experiment_id='a'*32, subject=1, session=1, manifest_sha256='b'*64,
                        blocks=[dict(run_id='run-001', trials=[dict(trial_id=f't{i}', media_id=f'm{i}', role='parent')
                                                            for i in range(1, 4)])])
        (state / 'session.json').write_text(json.dumps(metadata))
        self.session = dbstim.session(self.root)

    def rows(self):
        with next(self.root.glob('sub-*/ses-*/beh/*events.tsv')).open() as stream:
            return list(csv.DictReader(stream, delimiter='\t'))

    def test_events_resume_and_outcomes(self):
        with self.session.run(1) as run:
            trial = next(iter(run))
            run.started(trial, onset=0.5)
            run.completed(trial, duration=4.4, result={'response': 'a\tb\nc'})
        self.assertEqual(len(self.rows()), 1)
        with dbstim.session(self.root).run(1) as run:
            self.assertEqual([t.number for t in run], [2, 3])
            for trial in run:
                run.started(trial, onset=trial.number * 5)
                run.abandoned(trial, duration=1.2)
        rows = self.rows()
        self.assertEqual(list(rows[0])[:2], ['onset', 'duration'])
        self.assertEqual([r['outcome'] for r in rows], ['completed', 'abandoned', 'abandoned'])
        self.assertEqual(rows[0]['planned_duration'], '4.5')
        self.assertEqual(rows[0]['stim_file'], 'v1.mp4')
        self.assertEqual(json.loads(rows[0]['result']), {'response': 'a\tb\nc'})
        sidecar = json.loads(next(self.root.glob('sub-*/ses-*/beh/*events.json')).read_text())
        self.assertEqual(set(sidecar['outcome']['Levels']), {'completed', 'abandoned'})

    def test_crash_torn_tail_and_recovery(self):
        code = '''import os, sys, dbstim
with dbstim.session(sys.argv[1]).run(1) as run:
    run.started(next(iter(run)), onset=2)
    os._exit(0)
'''
        subprocess.run([sys.executable, '-c', code, str(self.root)], check=True)
        journal = next(self.root.glob('stimuli/.dbstim/*.jsonl'))
        with journal.open('ab') as stream:
            stream.write(b'{"torn":')
        with dbstim.session(self.root).run(1) as run:
            self.assertEqual([t.number for t in run], [2, 3])
        self.assertEqual(self.rows()[0]['outcome'], 'abandoned')
        self.assertEqual(self.rows()[0]['duration'], 'n/a')
        with dbstim.session(self.root).run(1) as run:
            self.assertEqual([t.number for t in run], [2, 3])
        records = [json.loads(line) for line in journal.read_text().splitlines()]
        self.assertEqual(sum(r.get('status') == 'abandoned' for r in records), 1)

    def test_fsync_and_order_validation(self):
        with self.session.run(1) as run:
            trials = list(run)
            with self.assertRaises(ValueError):
                run.started(trials[1], onset=0)
            with patch('os.fsync', wraps=os.fsync) as fsync:
                run.started(trials[0], onset=0)
                self.assertTrue(fsync.called)
            with self.assertRaises(ValueError):
                run.completed(trials[0], duration=float('nan'))
            with self.assertRaises(ValueError):
                run.completed(trials[0], duration=1, result={'bad': object()})
            run.abandoned(trials[0])
            with self.assertRaises(ValueError):
                run.started(trials[0], onset=0)

    def test_concurrent_run_is_refused(self):
        with self.session.run(1):
            with self.assertRaisesRegex(RuntimeError, 'open'):
                with dbstim.session(self.root).run(1):
                    pass

    def test_complete_malformed_record_is_not_discarded(self):
        with self.session.run(1):
            pass
        journal = next(self.root.glob('stimuli/.dbstim/*.jsonl'))
        with journal.open('ab') as stream:
            stream.write(b'{broken}\n')
        original = journal.read_bytes()
        with self.assertRaises(ValueError):
            with self.session.run(1):
                pass
        self.assertEqual(journal.read_bytes(), original)

    def test_write_failure_requires_reopen_before_further_events(self):
        with self.session.run(1) as run:
            trial = next(iter(run))
            with patch('os.fsync', side_effect=OSError('disk failure')):
                with self.assertRaises(OSError):
                    run.started(trial, onset=0)
            with self.assertRaises(RuntimeError):
                run.started(trial, onset=0)
        with self.session.run(1) as run:
            self.assertEqual([t.number for t in run], [2, 3])
        self.assertEqual(self.rows()[0]['outcome'], 'abandoned')



class FrameSummary(unittest.TestCase):
    def test_representative_pgl_arrays(self):
        self.assertEqual(dbstim.frame_summary([10, 10.1, 0, 10.3], [0, .1, .2, .3], .4),
                         dict(frames_shown=3, frames_dropped=1, seconds_shown=.3, reached_end=True))
        result = dbstim.frame_summary([10, 10.1, 0, 0], [0, .1, .2, .3], .4)
        self.assertEqual(result['frames_dropped'], 2)
        self.assertFalse(result['reached_end'])
        self.assertEqual(dbstim.frame_summary([], [], 4)['seconds_shown'], 0)
        evidence = dbstim.frame_summary([-1, 0, float('nan'), 100, 100.1], [0, .1, .2, .3, .4], 1)
        self.assertEqual(evidence['frames_shown'], 2)
        self.assertEqual(evidence['frames_dropped'], 3)
        self.assertFalse(evidence['reached_end'])
        with self.assertRaises(ValueError):
            dbstim.frame_summary([1], [], 4)

from tests.real_server import ACCOUNTS, CHECKOUT, FaultProxy, count, real_server


@unittest.skipUnless(CHECKOUT, 'Set DBSTIM_SERVER to test against the merged server')
class TrackedServer(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.origin, cls.database = cls.enterClassContext(real_server(real_media=True))

    def setUp(self):
        self.proxy = FaultProxy(self.origin)
        self.addCleanup(self.proxy.close)
        self.client = self.enterContext(dbstim.Client('admin', ACCOUNTS['admin'], url=self.proxy.origin))
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.exp = self.client.create(self.id(), count=4).schedule(subjects=1, sessions=1, runs=2, trials_per_run=2)

    def checkout(self, name='first', **kwargs):
        return self.exp.checkout(subject=1, session=1, folder=self.root / name, **kwargs)

    def finish(self, session, number=1):
        with session.run(number) as run:
            for trial in run:
                self.assertTrue(trial.file.is_file())
                run.started(trial, onset=trial.number * 2)
                run.completed(trial, duration=1, result={'frames_shown': 20})

    def test_checkout_scopes_media_and_holds_session(self):
        checked = self.checkout()
        self.assertEqual((checked.subject, checked.session, checked.runs), (1, 1, (1, 2)))
        status = self.client._http._json('GET', checked._path)
        self.assertTrue(status['holder']['writer_id'])
        from dbstim._session import _computer_id
        self.assertEqual(status['holder']['writer_id'], _computer_id())
        self.assertEqual(status['progress'], {})
        assigned = {row['media_id'] for block in checked._metadata['blocks'] for row in block['trials']}
        self.assertEqual(set(checked._media), assigned)
        self.assertFalse(any(path.endswith('/trials') for _, path, _, _ in self.proxy.seen))
        # Re-checkout by the same computer reuses verified media.
        self.proxy.seen.clear()
        self.checkout()
        self.assertFalse(any('/media/' in path for _, path, _, _ in self.proxy.seen))

    def test_atomic_sync_retry_and_changed_resend_refused(self):
        checked = self.checkout()
        before = count(self.database, 'experiment_run_records')
        with checked.run(1) as run:
            trial = next(iter(run))
            run.started(trial, onset=0)
            run.completed(trial, duration=.5)
        self.assertEqual(checked.sync(), [])
        self.finish(checked)
        self.assertEqual(checked.sync(), ['accepted'])
        self.assertEqual(checked.sync(), ['already synced'])
        self.assertEqual(count(self.database, 'experiment_run_records'), before + 1)
        run = checked.run(1)
        run._read()
        body = run._payload()
        body['events'][1]['duration'] = 999
        with self.assertRaises(dbstim.ApiError) as caught:
            self.client._http._json('POST', checked._path + '/runs/run-001/sync', body)
        self.assertEqual(caught.exception.status, 409)
        self.assertEqual(count(self.database, 'experiment_run_records'), before + 1)
        self.assertFalse(any(path.endswith('/trials') for _, path, _, _ in self.proxy.seen))

    def test_lost_sync_reply_retries_identical_full_record(self):
        checked = self.checkout()
        self.finish(checked)
        self.proxy.faults.append(('POST', '/runs/run-001/sync', 'response'))
        before = count(self.database, 'experiment_run_records')
        with self.assertRaises(dbstim.ApiError):
            checked.sync()
        # A newly opened Session can send the exact same request after a process crash.
        self.assertEqual(dbstim.session(checked.folder).sync(client=self.client), ['already synced'])
        requests = [json.loads(body) for method, path, _, body in self.proxy.seen if path.endswith('/runs/run-001/sync')]
        self.assertEqual(requests[0], requests[1])
        self.assertEqual(count(self.database, 'experiment_run_records'), before + 1)

    def test_other_computer_takeover_flags_original_and_preserves_holder(self):
        first = self.checkout()
        self.finish(first)
        with patch('dbstim._session._computer_id', return_value='c'*32):
            with self.assertRaises(dbstim.ApiError) as caught:
                self.checkout('second')
            self.assertEqual(caught.exception.status, 409)
            second = self.checkout('second', takeover=True)
            self.finish(second)
            self.assertEqual(second.sync(), ['accepted'])
        before = count(self.database, 'experiment_session_attempts')
        self.assertEqual(first.sync(), ['kept as flagged attempt'])
        self.assertEqual(first.sync(), ['kept as flagged attempt'])
        self.assertEqual(count(self.database, 'experiment_session_attempts'), before + 1)
        self.assertFalse(list(first.folder.glob('sub-*/ses-*/beh/*events.*')))
        flagged = list(first.folder.glob('sourcedata/attempt-*/sub-*/ses-*/beh/*events.tsv'))
        self.assertEqual(len(flagged), 1)
        # Reopening cannot recreate the raw events file after the flag is known.
        with first.run(1) as run:
            self.assertEqual(list(run), [])
        self.assertFalse(list(first.folder.glob('sub-*/ses-*/beh/*events.tsv')))
        status = self.client._http._json('GET', first._path)
        self.assertEqual(status['synced_runs'][0]['writer_id'], 'c'*32)
        self.assertEqual(len(status['flagged_attempts']), 1)
        run = first.run(1)
        run._read()
        changed = run._payload()
        changed['events'][1]['result'] = {'changed': True}
        with self.assertRaises(dbstim.ApiError) as caught:
            self.client._http._json('POST', first._path + '/runs/run-001/sync', changed)
        self.assertEqual(caught.exception.status, 409)

    def test_sync_during_open_run_waits(self):
        checked = self.checkout()
        self.finish(checked)
        with checked.run(2) as run:
            trial = next(iter(run))
            run.started(trial, onset=0)
            self.assertEqual(checked.sync(), [])
            run.abandoned(trial)
        self.assertEqual(checked.sync(), ['accepted'])

    def test_session_two_downloads_only_its_assignments(self):
        self.exp = self.client.create(self.id() + '-two', count=4).schedule(
            subjects=1, sessions=2, runs=1, trials_per_run=2)
        checked = self.exp.checkout(subject=1, session=2, folder=self.root / 'session-two')
        self.assertEqual(checked.session, 2)
        self.assertEqual(checked.runs, (1,))
        self.assertEqual(len(checked._media), 2)
        self.assertEqual(len(list((checked.folder / 'stimuli').glob('*.mp4'))), 2)
        self.finish(checked)
        self.assertEqual(checked.sync(), ['accepted'])

    def test_crashed_run_recovers_and_syncs_single_record(self):
        checked = self.checkout()
        code = """import os, sys, dbstim
with dbstim.session(sys.argv[1]).run(1) as run:
    run.started(next(iter(run)), onset=0)
    os._exit(0)
"""
        subprocess.run([sys.executable, '-c', code, str(checked.folder)], check=True)
        reopened = dbstim.session(checked.folder)
        self.finish(reopened)
        self.assertEqual(reopened.sync(client=self.client), ['accepted'])
        run = reopened.run(1)
        run._read()
        stored = self.client._http._json('GET', reopened._path + '/run-records/' + run._payload()['request_id'])
        self.assertEqual([e['status'] for e in stored['record']['events']],
                         ['started', 'abandoned', 'started', 'completed'])
        self.assertEqual(len(list(reopened.folder.glob('sub-*/ses-*/beh/*events.tsv'))), 1)
