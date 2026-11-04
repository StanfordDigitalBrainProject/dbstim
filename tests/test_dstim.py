import hashlib
import json
import unittest

from dbstim import Client, Experiment, MetricFilter
from tests.http_fixture import server


def response(document, status=200, **headers):
    return status, {'Content-Type': 'application/json', **headers}, json.dumps(document).encode()


def manifest(blocks=None):
    trials = [dict(trial_id=f'trial-{index}', media_id='same-video', media_type='video',
                   role=role, segment=dict(start_seconds=start, end_seconds=end))
              for index, (role, start, end) in enumerate([('parent', 0, 5), ('repeat', 0, 5), ('foil', 5, 10)], 1)]
    document = dict(schema_version='dbp-experiment-v1', experiment_id='experiment-1', subject_id='subject-001',
                    blocks=blocks or [dict(block_index=1, trials=trials)])
    digest = hashlib.sha256(json.dumps(document, sort_keys=True, separators=(',', ':'), ensure_ascii=False).encode()).hexdigest()
    return dict(document, manifest_sha256=digest)


class Fixture:
    def __init__(self, document=None):
        self.document = document or manifest()
        self.states = {}
        self.commands = {}
        self.results = []
        self.lost_reply = 0  # Replies to replace with a 503 after applying the command.
        self.abandoned = True  # False answers abandoned as servers before it did.
        self.writer = None
        self.status = 'not_started'

    def __call__(self, method, path, headers, body):
        document = json.loads(body) if body else {}
        if path == '/api/auth/login':
            return response(dict(csrf_token='csrf', user=dict(username='demo')), **{'Set-Cookie': 'dbp_session=secret; Path=/; HttpOnly'})
        if path == '/api/v1/metrics':
            return response(dict(metrics=[dict(id='duration_seconds')]))
        if path.startswith('/api/v1/experiments?'):
            return response(dict(experiments=[dict(id='experiment-1', name='Demo', latest_published_revision_id='experiment-1')]))
        if path == '/api/v1/media/query':
            return response(dict(version='catalog-version'))
        if path == '/api/v1/experiments' and method == 'POST':
            self.created = document
            return response(dict(id='experiment-1'), 201)
        if path.endswith('/publish'):
            return response(dict(subjects=[self.document]))
        if path == '/api/v1/experiments/experiment-1':
            return response(dict(id='experiment-1', settings=dict(name='Demo', subject_count=1),
                                 publication=dict(subjects=[self.document])))
        if path.endswith('/manifest'):
            return response(self.document)
        if path.endswith('/media'):
            payload = b'second-half' if '/trial-3/' in path else b'first-half'
            return 200, {'Content-Type': 'video/mp4', 'Content-Length': len(payload),
                         'X-Content-SHA256': hashlib.sha256(payload).hexdigest()}, payload
        if path.endswith('/media-access'):
            return response(dict(path='/api/v1/media-access/' + 'a' * 64, expires_at=9999999999, sha256='b' * 64, size_bytes=99))
        if '/sessions/session-001' in path:
            if method == 'GET':
                return response(dict(manifest_sha256=self.document['manifest_sha256'], session_id='session-001',
                                     runs=[dict(run_id=f"run-{block['block_index']:03d}", trials=block['trials'])
                                           for block in self.document['blocks']],
                                     latest_run_id='run-001', progress=self.states.copy(), results=list(self.results),
                                     status=self.status,
                                     unconfirmed=[key for key, value in self.states.items() if value['status'] == 'started'],
                                     abandoned=[key for key, value in self.states.items() if value['status'] == 'abandoned']))
            if document.get('status') == 'abandoned' and not self.abandoned:
                return response(dict(detail='Choose an assigned trial and started or completed.'), 400)
            previous = self.commands.get(document['request_id'])
            if previous is not None and previous != document:
                return response(dict(detail='Request ID belongs to a different command.'), 409)
            if previous is None:
                if path.endswith('/start'):
                    if self.writer not in (None, document['writer_id']) and not document['takeover']:
                        return response(dict(detail='Another presenter owns this session. '
                                                    'Review progress before explicit takeover.'), 409)
                    self.writer, self.status = document['writer_id'], 'started'
                elif path.endswith('/finish'):
                    self.writer, self.status = None, document['status']
                else:
                    self.states[document['trial_id']] = dict(status=document['status'])
                    if document.get('result') is not None:
                        self.results.append(dict(trial_id=document['trial_id'], status=document['status'],
                                                 result=document['result']))
                self.commands[document['request_id']] = document
            if self.lost_reply:
                self.lost_reply -= 1
                return response({}, 503)
            return response(dict(accepted=document['request_id']))
        raise AssertionError(path)


class WorkflowTests(unittest.TestCase):


    def test_create_is_draft_and_pins_query(self):
        fixture = Fixture()
        with server(fixture) as (url, requests):
            client = Client('demo', 'secret', url=url)
            experiment = client.create_experiment('Demo', subjects=5, trials_per_run=24, foils_per_run=4,
                sessions_per_subject=2, runs_per_session=3, shared_fraction=0.1, seed='fixed',
                filters=[MetricFilter('duration_seconds', 'gte', 10)])
            self.assertIsInstance(experiment, Experiment)
            self.assertEqual(fixture.created['version'], 'catalog-version')
            self.assertEqual(fixture.created['settings']['shared_fraction'], 0.1)
            self.assertFalse(any(path.endswith('/publish') for _, path, _, _ in requests))


if __name__ == '__main__':
    unittest.main()
