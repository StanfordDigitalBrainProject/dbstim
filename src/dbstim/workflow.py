"""Typed views of immutable assignments and server-owned presentation state."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
import tempfile
from typing import TYPE_CHECKING
import uuid

from ._transport import ApiError
from .models import JSONObject, MediaMode, Segment, SessionStatus, Trial, TrialStatus, identifier

if TYPE_CHECKING:
    from .client import Client


def _hash_file(path: Path) -> str:
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def _manifest(document: JSONObject, experiment_id: str, subject_id: str) -> None:
    payload = {key: value for key, value in document.items() if key != 'manifest_sha256'}
    digest = hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(',', ':'),
                                      ensure_ascii=False, allow_nan=False).encode()).hexdigest()
    if (document.get('schema_version') != 'dbp-experiment-v1' or document.get('experiment_id') != experiment_id
            or document.get('subject_id') != subject_id or document.get('manifest_sha256') != digest):
        raise ApiError('Manifest identity or SHA-256 does not match')
    seen = set()
    run_keys = set()
    blocks = document.get('blocks')
    if not isinstance(blocks, list) or not blocks:
        raise ApiError('Manifest requires runs')
    for index, block in enumerate(blocks, 1):
        if (not isinstance(block, dict) or not isinstance(block.get('trials'), list)
                or type(block.get('block_index')) is not int or block['block_index'] != index):
            raise ApiError('Invalid manifest block order')
        try:
            run_key = (identifier(block.get('session_id', 'session-001')), identifier(block.get('run_id', f'run-{index:03d}')))
        except ValueError as error:
            raise ApiError('Invalid manifest session or run identity') from error
        if run_key in run_keys:
            raise ApiError('Duplicate run identity within a session')
        run_keys.add(run_key)
        for position, row in enumerate(block['trials'], 1):
            trial = _trial(row, position)
            if trial.trial_id in seen:
                raise ApiError('Duplicate trial identity')
            seen.add(trial.trial_id)
    if not seen:
        raise ApiError('Manifest has no trials')


def _trial(row: JSONObject, position: int) -> Trial:
    try:
        segment = Segment(**row['segment']) if row['segment'] is not None else None
        return Trial(row['trial_id'], row['media_id'], position, row['role'], row['media_type'], segment)
    except (ValueError, TypeError, KeyError) as error:
        raise ApiError('Invalid manifest trial') from error


class Experiment:
    def __init__(self, client: Client, experiment_id: str):
        self._client = client
        self.id = identifier(experiment_id)
        self._path = '/api/v1/experiments/' + self.id

    def summary(self) -> JSONObject:
        document = self._client._http._json('GET', self._path)
        settings = document['settings']
        parents = settings.get('parents_per_subject', 0)
        return dict(id=self.id, name=settings['name'], published=document.get('publication') is not None,
                    settings=settings, shared_fraction_realized=settings.get('shared_per_subject', 0) / parents if parents else 0,
                    subject_ids=[f'subject-{index + 1:03d}' for index in range(settings['subject_count'])])

    def publish(self) -> Experiment:
        self._client._http._json('POST', self._path + '/publish', {})
        return self

    def subject(self, id: str) -> Subject:
        return Subject(self, identifier(id))


class Subject:
    def __init__(self, experiment: Experiment, subject_id: str):
        self.experiment = experiment
        self.id = subject_id
        self._path = experiment._path + '/subjects/' + self.id
        self._manifest = experiment._client._http._json('GET', self._path + '/manifest')
        _manifest(self._manifest, experiment.id, self.id)
        self.session_ids = tuple(dict.fromkeys(block.get('session_id', 'session-001') for block in self._manifest['blocks']))

    def session(self, id: str = 'session-001', *, directory: str | Path | None = None) -> Session:
        if id not in self.session_ids:
            raise ValueError('Choose a session from subject.session_ids')
        return Session(self, id, directory=directory)


class Session:
    def __init__(self, subject: Subject, session_id: str, *, directory: str | Path | None = None):
        self.subject = subject
        self.id = identifier(session_id)
        self._http = subject.experiment._client._http
        self._path = subject._path + '/sessions/' + self.id
        self._writer = uuid.uuid4().hex
        self._pending: tuple[str, JSONObject] | None = None
        self._blocks = [block for block in subject._manifest['blocks'] if block.get('session_id', 'session-001') == self.id]
        self.run_ids = tuple(block.get('run_id', f"run-{block['block_index']:03d}") for block in self._blocks)
        self._directory = self._root(Path(directory)) if directory is not None else None

    def _state(self) -> JSONObject:
        if self._pending is not None:
            raise ApiError('A request has no acknowledgment. Retry the same operation before continuing.')
        state = self._http._json('GET', self._path)
        if state['manifest_sha256'] != self.subject._manifest['manifest_sha256']:
            raise ApiError('Session manifest changed')
        return state

    def _send(self, action: str, **values) -> None:
        key = json.dumps([action, values], sort_keys=True, allow_nan=False)
        if self._pending is not None and self._pending[0] != key:
            raise ApiError('Retry the unacknowledged operation first')
        body = self._pending[1] if self._pending else dict(request_id=uuid.uuid4().hex, writer_id=self._writer,
                                                         **json.loads(json.dumps(values, allow_nan=False)))
        self._pending = (key, body)
        try:
            acknowledgment = self._http._json('POST', self._path + '/' + action, body)
        except ApiError as error:
            if error.status is not None and 400 <= error.status < 500:
                self._pending = None
            raise
        if acknowledgment.get('accepted') != body['request_id']:
            raise ApiError('Server did not acknowledge this command')
        self._pending = None

    def start(self, *, takeover: bool = False) -> None:
        self._send('start', takeover=takeover)

    def finish(self, status: SessionStatus = 'completed') -> None:
        if status not in ('completed', 'interrupted', 'failed'):
            raise ValueError('Choose completed, interrupted or failed')
        self._send('finish', status=status)

    def run(self, id: str = 'latest') -> Run:
        if id == 'latest':
            id = self._state()['latest_run_id']
        if id not in self.run_ids:
            raise ValueError('Choose a run from session.run_ids')
        return Run(self, id, self._blocks[self.run_ids.index(id)])

    def download(self, directory: str | Path) -> Path:
        self._directory = self._root(Path(directory))
        for run_id in self.run_ids:
            for trial in self.run(run_id).trials:
                self._media(trial, 'cache')
        return self._directory

    def _root(self, directory: Path) -> Path:
        if directory.is_symlink():
            raise ValueError('Download directory must not be a symlink')
        directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        return directory.resolve()

    def _media(self, trial: Trial, mode: MediaMode) -> Path | str:
        endpoint = self.subject._path + '/trials/' + trial.trial_id
        if mode == 'remote':
            access = self._http._json('POST', endpoint + '/media-access', {})
            path = access.get('path', '')
            if not isinstance(path, str) or not re.fullmatch('/api/v1/media-access/[0-9a-f]{64}', path):
                raise ApiError('Server returned an unexpected media link')
            return self._http.origin + path
        if mode not in ('local', 'cache'):
            raise ValueError('Choose local, cache or remote')
        if self._directory is None:
            origin = hashlib.sha256(self._http.origin.encode()).hexdigest()[:16]
            self._directory = self._root(Path('.dbstim') / origin / self.subject.experiment.id /
                                         self.subject.id / self.subject._manifest['manifest_sha256'] / self.id)
        root = self._directory
        target = root / (trial.trial_id + '.mp4')
        receipt = root / (trial.trial_id + '.json')
        identity = dict(origin=self._http.origin, manifest_sha256=self.subject._manifest['manifest_sha256'],
                        session_id=self.id, trial_id=trial.trial_id)
        if target.is_symlink() or receipt.is_symlink():
            raise ApiError('Cached media or receipt is a symlink')
        if target.exists() and receipt.exists():
            try:
                saved = json.loads(receipt.read_text())
                if (any(saved.get(key) != value for key, value in identity.items())
                        or saved['sha256'] != _hash_file(target) or saved['size_bytes'] != target.stat().st_size):
                    raise ValueError('Mismatch')
            except (OSError, ValueError, KeyError) as error:
                raise ApiError('Cached media does not match its verified receipt') from error
            return target
        if mode == 'local':
            raise ApiError('Download this session first, or choose mode="cache"')
        with self._http._request('GET', endpoint + '/media', media=True) as response:
            expected = response.headers.get('X-Content-SHA256', '')
            size = response.headers.get('Content-Length', '')
            if (response.headers.get_content_type() != 'video/mp4' or not re.fullmatch('[0-9a-f]{64}', expected)
                    or not size.isdigit() or not 0 < int(size) <= self._http.max_media_bytes):
                raise ApiError('Media response is missing a valid size, type or checksum')
            digest = hashlib.sha256()
            written = 0
            with tempfile.TemporaryDirectory(dir=root) as temporary:
                partial = Path(temporary) / 'media.mp4'
                with partial.open('wb') as stream:
                    while chunk := response.read(min(1024 * 1024, int(size) - written + 1)):
                        written += len(chunk)
                        if written > int(size):
                            raise ApiError('Media exceeds declared size')
                        digest.update(chunk)
                        stream.write(chunk)
                if written != int(size) or digest.hexdigest() != expected:
                    raise ApiError('Media checksum or size mismatch')
                metadata = Path(temporary) / 'receipt.json'
                metadata.write_text(json.dumps(dict(identity, sha256=expected, size_bytes=written)))
                for source, destination in ((partial, target), (metadata, receipt)):
                    try:
                        os.link(source, destination)
                    except FileExistsError:
                        if destination.is_symlink() or _hash_file(destination) != _hash_file(source):
                            raise ApiError('Existing download conflicts with the verified media or receipt') from None
        return target


class Run:
    def __init__(self, session: Session, run_id: str, block: JSONObject):
        self.session = session
        self.id = run_id
        self.trials = tuple(_trial(row, index) for index, row in enumerate(block['trials'], 1))

    @property
    def results(self) -> list[JSONObject]:
        trial_ids = {trial.trial_id for trial in self.trials}
        return [result for result in self.session._state()['results'] if result['trial_id'] in trial_ids]

    def next_trial(self) -> Trial | None:
        state = self.session._state()
        if state['unconfirmed']:
            raise ApiError('An unconfirmed trial requires review; no automatic replay or skip')
        return next((trial for trial in self.trials if state['progress'].get(trial.trial_id, {}).get('status') != 'completed'), None)

    def media(self, trial: Trial, mode: MediaMode = 'local') -> Path | str:
        if trial not in self.trials:
            raise ValueError('Choose a trial from this run')
        return self.session._media(trial, mode)

    def record(self, trial: Trial, status: TrialStatus, result: JSONObject | None = None) -> None:
        if trial not in self.trials or status not in ('started', 'completed'):
            raise ValueError('Choose a trial from this run and started or completed')
        if result is not None and not isinstance(result, dict):
            raise ValueError('result must be a JSON object or None')
        self.session._send('trials', trial_id=trial.trial_id, status=status, result=result)
