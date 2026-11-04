import copy
import hashlib
import json
import unittest

from dbstim import ApiError, Client, MetricFilter, Segment
from dbstim.workflow import _manifest
from tests.http_fixture import server
from tests.test_dstim import Fixture, manifest, response


class ValidationTests(unittest.TestCase):
    def test_segment_rejects_boolean_nonfinite_and_reversed_bounds(self):
        for bounds in ((True, 2), (0, float('inf')), (float('nan'), 2), (2, 1), (-1, 0)):
            with self.subTest(bounds=bounds), self.assertRaises(ValueError):
                Segment(*bounds)

    def test_filters_are_finite_and_serializable(self):
        self.assertEqual(MetricFilter('has_audio', 'is', True).to_dict()['value'], True)
        for values in (('', 'gt', 1), ('duration_seconds', '?', 1), ('duration_seconds', 'gt', float('nan'))):
            with self.subTest(values=values), self.assertRaises(ValueError):
                MetricFilter(*values)

    def test_bad_manifest_identity_hash_duplicate_and_bounds(self):
        original = manifest()
        _manifest(original, 'experiment-1', 'subject-001')
        for mutation in ('hash', 'duplicate', 'segment'):
            document = copy.deepcopy(original)
            if mutation == 'hash':
                document['blocks'][0]['trials'][0]['media_id'] = 'different'
            else:
                if mutation == 'duplicate':
                    document['blocks'][0]['trials'][1]['trial_id'] = 'trial-1'
                else:
                    document['blocks'][0]['trials'][1]['segment']['end_seconds'] = -1
                payload = {key: value for key, value in document.items() if key != 'manifest_sha256'}
                document['manifest_sha256'] = hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(',', ':'), ensure_ascii=False).encode()).hexdigest()
            with self.subTest(mutation=mutation), self.assertRaises(ApiError):
                _manifest(document, 'experiment-1', 'subject-001')

    def test_invalid_counts_fail_before_query(self):
        fixture = Fixture()
        with server(fixture) as (url, requests):
            client = Client('demo', 'secret', url=url)
            for values in (dict(subjects=True), dict(shared_fraction=-1), dict(shared_fraction=True),
                           dict(foils_per_run=10), dict(repeats_per_run=10), dict(seed=True), dict(seed=1.5),
                           dict(sessions_per_subject=0), dict(trials_per_run=0)):
                with self.subTest(values=values), self.assertRaises(ValueError):
                    client.create_experiment('Demo', **(dict(subjects=1, trials_per_run=5) | values))
            self.assertEqual(len(requests), 1)

    def test_manifest_rejects_malformed_or_duplicate_runs(self):
        for mutation in ('not-object', 'missing-trials', 'duplicate-run'):
            document = manifest()
            if mutation == 'not-object':
                document['blocks'][0] = None
            elif mutation == 'missing-trials':
                del document['blocks'][0]['trials']
            else:
                document['blocks'][0]['run_id'] = 'run-001'
                document['blocks'].append(dict(block_index=2, run_id='run-001', trials=[]))
            payload = {key: value for key, value in document.items() if key != 'manifest_sha256'}
            document['manifest_sha256'] = hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(',', ':'), ensure_ascii=False).encode()).hexdigest()
            with self.subTest(mutation=mutation), self.assertRaises(ApiError):
                _manifest(document, 'experiment-1', 'subject-001')

    def test_ambiguous_name_and_exact_revision(self):
        fixture = Fixture()

        def respond(method, path, headers, body):
            if path.startswith('/api/v1/experiments?'):
                return response(dict(experiments=[dict(id='one', name='Demo'), dict(id='two', name='Demo')]))
            return fixture(method, path, headers, body)

        with server(respond) as (url, _):
            client = Client('demo', 'secret', url=url)
            with self.assertRaisesRegex(ValueError, "2 experiments are named 'Demo': one \\(draft, created at an "
                                                    "unknown time\\); two .*client.experiment\\(id=...\\)"):
                client.experiment('Demo')
            with self.assertRaisesRegex(ValueError, "No experiment is named 'Other'; client.experiments"):
                client.experiment('Other')
            self.assertEqual(client.experiment(id='experiment-1').id, 'experiment-1')


if __name__ == '__main__':
    unittest.main()
