import unittest
from unittest.mock import patch

import dbstim
from dbstim import Client, Metric, MetricFilter
from tests.http_fixture import server
from tests.test_dstim import Fixture, manifest, response


def two_runs():
    def block(index):
        return dict(block_index=index, trials=[dict(trial_id=f'run{index}-trial-{position}', media_id='video',
                                                    media_type='video', role='parent', segment=None)
                                               for position in (1, 2)])
    return manifest([block(1), block(2)])


def finishes(fixture):
    return [command['status'] for command in fixture.commands.values() if 'trial_id' not in command and 'status' in command]


class UsabilityTests(unittest.TestCase):
    def test_symbolic_operators_map_to_server_codes(self):
        for symbol, code in (('>=', 'gte'), ('<=', 'lte'), ('>', 'gt'), ('<', 'lt'), ('==', 'eq'), ('gte', 'gte')):
            with self.subTest(symbol=symbol):
                self.assertEqual(MetricFilter('duration_seconds', symbol, 10).to_dict()['operator'], code)
        self.assertEqual(MetricFilter('duration_seconds', '>=', 10), MetricFilter('duration_seconds', 'gte', 10))
        for symbol, value, code in (('==', True, 'is'), ('!=', True, 'is_not'), ('==', False, 'is'),
                                    ('is', True, 'is'), ('==', None, 'eq'), ('==', 1, 'eq')):
            with self.subTest(symbol=symbol, value=value):
                self.assertEqual(MetricFilter('audio_track_present', symbol, value).operator, code)
        for symbol, value in (('=>', 10), ('!=', 10), ('!=', None), (['=='], True)):
            with self.subTest(symbol=symbol, value=value), self.assertRaises(ValueError):
                MetricFilter('duration_seconds', symbol, value)

    def test_integer_seed_is_sent_as_text(self):
        fixture = Fixture()
        with server(fixture) as (url, _):
            Client('demo', 'secret', url=url).create_experiment('Demo', subjects=1, trials_per_run=3, seed=7)
            self.assertEqual(fixture.created['settings']['seed'], '7')

    def test_metrics_returns_names(self):
        def respond(method, path, headers, body):
            if path == '/api/v1/metrics':
                return response(dict(metrics=[dict(id='duration_seconds', label='Duration', kind='number',
                                                   unit='seconds', operators=['gt', 'gte'])], methods=[]))
            return Fixture()(method, path, headers, body)

        with server(respond) as (url, _):
            [metric] = Client('demo', 'secret', url=url).metrics()
        self.assertEqual(metric, 'duration_seconds')
        self.assertEqual(repr(Metric('audio_track_present', 'Audio track', 'boolean', None, ('is', 'is_not'))),
                         '<Metric audio_track_present: Audio track (boolean); operators == !=>')

    def test_connect_prompts_without_echoing_the_password(self):
        with server(Fixture()) as (url, requests):
            with patch('builtins.input', return_value=' demo ') as asked, \
                    patch('dbstim.client.getpass', return_value='secret') as hidden:
                client = dbstim.connect(url=url)
            self.assertEqual(client.username, 'demo')
            asked.assert_called_once()
            hidden.assert_called_once()
            with patch('builtins.input') as asked, patch('dbstim.client.getpass', return_value='secret'):
                dbstim.connect('demo', url=url)
            asked.assert_not_called()
            self.assertIn(b'"password": "secret"', requests[0][3])


    def test_rounded_shared_fraction_is_reported(self):
        fixture = Fixture()

        def respond(method, path, headers, body):
            if path == '/api/v1/experiments' and method == 'POST':
                return response(dict(id='experiment-1', settings=dict(parents_per_subject=6, shared_per_subject=0)), 201)
            return fixture(method, path, headers, body)

        with server(respond) as (url, _):
            client = Client('demo', 'secret', url=url)
            with self.assertWarnsRegex(UserWarning, r'shared_fraction=0.1 rounds down to 0 of 6 parent videos per '
                                                    r'subject \(realized fraction 0\)'):
                client.create_experiment('Demo', subjects=1, trials_per_run=6, shared_fraction=0.1)
            with patch('dbstim.client.warnings.warn') as warned:
                client.create_experiment('Demo', subjects=1, trials_per_run=6)
            warned.assert_not_called()


if __name__ == '__main__':
    unittest.main()
