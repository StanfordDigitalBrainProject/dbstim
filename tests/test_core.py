"""D1's public API against the actual server on disposable databases."""

import csv
from dataclasses import FrozenInstanceError
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import dbstim
from tests.http_fixture import server
from tests.real_server import ACCOUNTS, CHECKOUT, FaultProxy, real_server
from tests.test_dstim import response


class FilterTests(unittest.TestCase):
    def test_frozen_symbolic_values_and_server_encoding(self):
        condition = dbstim.Filter('duration_seconds', '>=', 10)
        self.assertEqual((condition.metric, condition.op, condition.value), ('duration_seconds', '>=', 10))
        self.assertEqual(condition.to_dict(), dict(metric_id='duration_seconds', operator='gte', value=10))
        self.assertEqual(dbstim.Filter('audio_track_present', '!=', False).to_dict()['operator'], 'is_not')
        with self.assertRaises(FrozenInstanceError):
            condition.value = 2

    def test_invalid_filters(self):
        for args in [('', '>', 1), ('x', 'gte', 1), ('x', '!=', 1), ('x', '>', True),
                     ('x', '==', None), ('x', '==', '1'), ('x', '>', float('nan')),
                     ('x', '<', float('inf')), ('x', [], True), ('\nx', '==', 1)]:
            with self.subTest(args=args), self.assertRaises(ValueError):
                dbstim.Filter(*args)


class PreparingMediaTests(unittest.TestCase):
    """The server answers 503 with Retry-After while it renders media such as foil halves."""

    movie = b'rendered movie bytes'

    def serve(self, preparing):
        document = dict(schema_version='dbp-experiment-media-v1', experiment_id='experiment-1', name='Demo',
                        revision=1, dataset_version='v1', seed='0', filters=[],
                        media=[dict(media_id='m1', catalog_id='clip-1', file_name='m1.mp4', segment=None,
                                    sha256=hashlib.sha256(self.movie).hexdigest(), size_bytes=len(self.movie),
                                    duration_seconds=1.5)])

        def respond(method, path, headers, body):
            if path == '/api/auth/login':
                return response(dict(csrf_token='csrf', user=dict(username='demo')))
            if path == '/api/v1/experiments/experiment-1':
                return response(dict(id='experiment-1', name='Demo', revision=1))
            for suffix in ('/media-manifest', '/media/m1'):
                if path.endswith(suffix) and preparing.get(suffix):
                    wait = preparing[suffix].pop(0) if isinstance(preparing[suffix], list) else preparing[suffix]
                    return response(dict(detail=dict(code='media_preparing', message='Still being prepared.')),
                                    503, **{'Retry-After': wait})
            if path.endswith('/media-manifest'):
                return response(document)
            if path.endswith('/media/m1'):
                return 200, {'Content-Type': 'video/mp4', 'Content-Length': len(self.movie)}, self.movie
            return response(dict(detail='not found'), 404)

        origin, requests = self.enterContext(server(respond))
        clock = [0.0]
        waits = []

        def sleep(seconds):
            waits.append(seconds)
            clock[0] += seconds

        self.enterContext(patch('dbstim._download.sleep', side_effect=sleep))
        self.enterContext(patch('dbstim._download.monotonic', side_effect=lambda: clock[0]))
        client = self.enterContext(dbstim.Client('demo', 'password', url=origin))
        folder = Path(self.enterContext(tempfile.TemporaryDirectory())) / 'bids'
        return client.open(id='experiment-1'), folder, requests, waits

    def test_download_waits_as_directed_then_succeeds(self):
        exp, folder, requests, waits = self.serve({'/media-manifest': [7, 7], '/media/m1': [3]})
        exp.download(folder)
        self.assertEqual(waits, [7, 7, 3])
        self.assertEqual((folder / 'stimuli/m1.mp4').read_bytes(), self.movie)
        self.assertEqual(exp.videos, ('clip-1',))
        self.assertEqual(sum(path.endswith('/media-manifest') for _, path, _, _ in requests), 3)

    def test_download_stops_within_bounds_and_reports_last_retry_after(self):
        for retry_after in (10, 3600):
            with self.subTest(retry_after=retry_after):
                exp, folder, requests, waits = self.serve({'/media-manifest': retry_after})
                with self.assertRaisesRegex(dbstim.ApiError, f'retry after {retry_after} seconds') as caught:
                    exp.download(folder)
                self.assertEqual((caught.exception.status, caught.exception.retry_after), (503, retry_after))
                self.assertLessEqual(sum(waits), 300)
                self.assertTrue(all(wait == retry_after for wait in waits))
                self.assertEqual(len(waits), 30 if retry_after == 10 else 0)
                self.assertFalse(folder.exists())
                self.assertFalse(any('/media/' in path for _, path, _, _ in requests))


@unittest.skipUnless(CHECKOUT, 'Set DBSTIM_SERVER to run real-server D1 tests')
class CoreTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.origin, cls.root = cls.enterClassContext(real_server(real_media=True))

    def setUp(self):
        self.proxy = FaultProxy(self.origin)
        self.addCleanup(self.proxy.close)
        self.client = self.enterContext(dbstim.Client('admin', ACCOUNTS['admin'], url=self.proxy.origin))
        self.folder = Path(self.enterContext(tempfile.TemporaryDirectory())) / 'bids'

    def create(self, **kwargs):
        return self.client.create(self.id(), count=2, **kwargs)

    def test_connect_metrics_create_open_and_unique_names(self):
        with patch('dbstim.client.getpass', return_value=ACCOUNTS['admin']):
            client = self.enterContext(dbstim.connect('admin', url=self.origin))
        self.assertIn('duration_seconds', client.metrics())
        self.assertTrue(all(type(metric) is str for metric in client.metrics()))
        exp = self.create(filters=[dbstim.Filter('duration_seconds', '>=', 1)], seed=7)
        self.assertEqual(exp.name, self.id())
        self.assertEqual(exp.revision, 1)
        exp.download(self.folder)
        manifest = json.loads((self.folder / 'stimuli/manifest.json').read_text())
        self.assertEqual(manifest['filters'], [dict(metric_id='duration_seconds', operator='gte', value=1)])
        self.assertEqual(manifest['seed'], '7')
        self.assertEqual(len(exp.videos), 2)
        self.assertIsInstance(exp.videos, tuple)
        self.assertEqual(self.client.open(exp.name).id, exp.id)
        self.assertEqual(self.client.open(id=exp.id).videos, exp.videos)
        with self.assertRaisesRegex(dbstim.ApiError, 'open'):
            self.client.create(exp.name, count=2)
        other = self.enterContext(dbstim.Client('other', ACCOUNTS['other'], url=self.origin))
        with self.assertRaises(dbstim.ApiError):
            other.open(id=exp.id)

    def test_replace_makes_revision_and_keeps_original_download_reproducible(self):
        exp = self.create()
        exp.download(self.folder)
        original = (self.folder / 'stimuli/manifest.json').read_bytes()
        revised = exp.replace([exp.videos[0]], reason='unusable clip')
        self.assertEqual(revised.revision, 2)
        self.assertNotEqual(exp.id, revised.id)
        self.assertNotIn(exp.videos[0], revised.videos)
        self.assertEqual(len(revised.videos), len(exp.videos))
        self.assertEqual(self.client.open(exp.name).id, revised.id)
        self.assertEqual(self.client.open(exp.name, revision=1).id, exp.id)
        self.client.open(id=exp.id).download(self.folder)
        self.assertEqual((self.folder / 'stimuli/manifest.json').read_bytes(), original)
        with self.assertRaises(dbstim.ApiError):
            revised.download(self.folder)

    def test_replace_leaves_pool_membership_to_the_server_without_fetching_media(self):
        exp = self.create()
        video = exp.videos[0]
        reopened = self.client.open(exp.name)
        self.proxy.seen.clear()
        with self.assertRaisesRegex(dbstim.ApiError, 'belonging to this experiment') as caught:
            reopened.replace(['not-in-this-experiment'])
        self.assertEqual(caught.exception.status, 400)
        revised = reopened.replace([video])
        self.assertEqual(revised.revision, 2)
        self.assertFalse(any('/media' in path for _, path, _, _ in self.proxy.seen))

    def test_schedule_is_optional_and_creates_revision(self):
        exp = self.create()
        scheduled = exp.schedule(subjects=1, trials_per_run=3, repeats_per_run=1)
        self.assertEqual(scheduled.revision, 2)
        self.assertEqual(set(scheduled.videos), set(exp.videos))
        self.assertEqual(exp.revision, 1)
        scheduled.download(self.folder)
        self.assertEqual(len(list((self.folder / 'stimuli').glob('*.mp4'))), 2)

    def test_download_layout_manifest_csv_checksums_and_rerun(self):
        exp = self.create(seed='stable')
        self.assertEqual(exp.download(self.folder), self.folder)
        stimuli = self.folder / 'stimuli'
        description = json.loads((self.folder / 'dataset_description.json').read_text())
        self.assertEqual(description['Name'], exp.name)
        self.assertIn('BIDSVersion', description)
        self.assertTrue((self.folder / 'README').is_file())
        manifest = json.loads((stimuli / 'manifest.json').read_text())
        self.assertEqual(manifest['experiment_id'], exp.id)
        self.assertEqual(manifest['name'], exp.name)
        self.assertEqual(manifest['revision'], 1)
        self.assertEqual(manifest['seed'], 'stable')
        self.assertEqual(manifest['filters'], [])
        self.assertTrue(manifest['dataset_version'])
        rows = manifest['media']
        self.assertEqual([row['catalog_id'] for row in rows], list(exp.videos))
        self.assertEqual([row['media_id'] for row in rows], sorted(row['media_id'] for row in rows))
        with (stimuli / 'manifest.csv').open(newline='') as stream:
            csv_rows = list(csv.DictReader(stream))
        self.assertEqual(len(csv_rows), 2)
        self.assertEqual([int(row['index']) for row in csv_rows], [1, 2])
        self.assertEqual({path.name for path in stimuli.glob('*.mp4')}, {row['file_name'] for row in rows})
        for row, csv_row in zip(rows, csv_rows):
            file = stimuli / row['file_name']
            self.assertEqual(hashlib.sha256(file.read_bytes()).hexdigest(), row['sha256'])
            self.assertEqual(file.stat().st_size, row['size_bytes'])
            self.assertGreater(row['duration_seconds'], 0)
            self.assertEqual(csv_row['filename'], row['file_name'])
        before = [file.stat().st_mtime_ns for file in stimuli.glob('*.mp4')]
        self.proxy.seen.clear()
        exp.download(self.folder)
        self.assertEqual([file.stat().st_mtime_ns for file in stimuli.glob('*.mp4')], before)
        self.assertFalse(any('/media/' in path for _, path, _, _ in self.proxy.seen))
        (stimuli / rows[0]['file_name']).unlink()
        exp.download(self.folder)
        self.assertEqual(len([path for _, path, _, _ in self.proxy.seen if '/media/' in path]), 1)

    def test_corrupt_download_is_refused_and_retry_skips_completed_files(self):
        exp = self.create()
        missing = exp.videos[-1]
        self.proxy.faults.append(('GET', '/media/' + missing, '503'))
        with self.assertRaises(dbstim.ApiError):
            exp.download(self.folder)
        self.assertEqual(len(list((self.folder / 'stimuli').glob('*.mp4'))), 1)
        self.proxy.seen.clear()
        exp.download(self.folder)
        self.assertEqual(len([path for _, path, _, _ in self.proxy.seen if '/media/' in path]), 1)
        manifest = json.loads((self.folder / 'stimuli/manifest.json').read_text())
        row = manifest['media'][0]
        (self.folder / 'stimuli' / row['file_name']).write_bytes(b'bad')
        exp.download(self.folder)
        self.assertEqual(hashlib.sha256((self.folder / 'stimuli' / row['file_name']).read_bytes()).hexdigest(), row['sha256'])
        # Change source bytes after creation: the real server refuses pinned checksum drift.
        source = self.root / 'media' / (missing + '.mp4')
        original_source = source.read_bytes()
        source.write_bytes(b'changed source')
        (self.folder / 'stimuli' / (missing + '.mp4')).unlink()
        try:
            with self.assertRaises(dbstim.ApiError):
                exp.download(self.folder)
            self.assertFalse((self.folder / 'stimuli' / (missing + '.mp4')).exists())
        finally:
            source.write_bytes(original_source)

    def test_rendered_foil_halves_are_distinct_files(self):
        exp = self.create()
        scheduled = exp.schedule(subjects=1, trials_per_run=3, foils_per_run=1)
        scheduled.download(self.folder)
        stimuli = self.folder / 'stimuli'
        manifest = json.loads((stimuli / 'manifest.json').read_text())
        self.assertEqual(len(manifest['media']), 3)
        segments = [row for row in manifest['media'] if row['segment'] is not None]
        self.assertEqual(len(segments), 2)
        self.assertEqual(segments[0]['catalog_id'], segments[1]['catalog_id'])
        self.assertNotEqual(segments[0]['file_name'], segments[1]['file_name'])
        for row in segments:
            self.assertEqual(hashlib.sha256((stimuli / row['file_name']).read_bytes()).hexdigest(), row['sha256'])

    def test_client_refuses_bytes_that_do_not_match_manifest(self):
        exp = self.create()
        video = exp.videos[0]
        self.proxy.faults.append(('GET', '/media/' + video, 'corrupt'))
        with self.assertRaisesRegex(dbstim.ApiError, 'SHA-256'):
            exp.download(self.folder)
        self.assertFalse((self.folder / 'stimuli' / (video + '.mp4')).exists())
        self.assertFalse(list((self.folder / 'stimuli').glob('.*')))
        exp.download(self.folder)
        self.assertEqual(len(list((self.folder / 'stimuli').glob('*.mp4'))), 2)

    def test_retry_after_lost_create_and_replace_replies_is_idempotent(self):
        self.proxy.faults.append(('POST', '/api/v1/experiments', 'response'))
        with self.assertRaises(dbstim.ApiError):
            self.create()
        exp = self.create()
        self.assertEqual(exp.revision, 1)
        created = [json.loads(body) for method, path, _, body in self.proxy.seen
                   if method == 'POST' and path == '/api/v1/experiments']
        self.assertEqual(created[0], created[1])
        videos = [exp.videos[0]]
        self.proxy.faults.append(('POST', '/replacements', 'response'))
        with self.assertRaises(dbstim.ApiError):
            exp.replace(videos)
        revised = exp.replace(videos)
        self.assertEqual(revised.revision, 2)
        self.assertEqual(self.client.open(exp.name).id, revised.id)

    def test_invalid_create_and_open_inputs_make_no_requests(self):
        before = len(self.proxy.seen)
        for kwargs in (dict(name='', count=2), dict(name='bad', count=True), dict(name='bad', count=0),
                       dict(name='bad', count=2, seed=False), dict(name='bad', count=2, seed=''),
                       dict(name='bad', count=2, filters=['duration_seconds']),
                       dict(name='bad', count=2, filters=None)):
            with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):
                self.client.create(**kwargs)
        for kwargs in (dict(), dict(name='x', id='x'), dict(name='x', revision=0),
                       dict(name='x', revision=True), dict(id='x', revision=1)):
            with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):
                self.client.open(**kwargs)
        self.assertEqual(len(self.proxy.seen), before)

    def test_encoded_experiment_name_reopens_exactly(self):
        exp = self.client.create('Memory & movies? café', count=1)
        self.assertEqual(self.client.open(exp.name).id, exp.id)

    def test_unlisted_movie_or_csv_cannot_silently_break_pgl_loading(self):
        exp = self.create()
        exp.download(self.folder)
        for name in ('unlisted.mp4', 'other.csv'):
            extra = self.folder / 'stimuli' / name
            extra.write_text('user file')
            with self.assertRaisesRegex(dbstim.ApiError, 'unlisted'):
                exp.download(self.folder)
            self.assertEqual(extra.read_text(), 'user file')
            extra.unlink()
        exp.download(self.folder)
