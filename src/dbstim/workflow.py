"""Frozen experiment revisions, assignment validation and session checkout."""

from __future__ import annotations

from dataclasses import dataclass
from functools import cached_property
import hashlib
import json
from pathlib import Path
from typing import TYPE_CHECKING
import uuid

from ._download import download as _download, media_manifest
from ._transport import ApiError
from .models import JSONObject, Segment, _Assignment, identifier

if TYPE_CHECKING:
    from .client import Client


@dataclass(frozen=True)
class _Block:
    session_id: str
    run_id: str
    trials: tuple[_Assignment, ...]


def _manifest(document: JSONObject, experiment_id: str, subject_id: str) -> tuple[_Block, ...]:
    payload = {key: value for key, value in document.items() if key != 'manifest_sha256'}
    digest = hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(',', ':'),
                                      ensure_ascii=False, allow_nan=False).encode()).hexdigest()
    if (document.get('schema_version') != 'dbp-experiment-v1' or document.get('experiment_id') != experiment_id
            or document.get('subject_id') != subject_id or document.get('manifest_sha256') != digest):
        raise ApiError('Manifest identity or SHA-256 does not match')
    seen = set()
    parsed: list[_Block] = []
    blocks = document.get('blocks')
    if not isinstance(blocks, list) or not blocks:
        raise ApiError('Manifest requires runs')
    for index, block in enumerate(blocks, 1):
        if (not isinstance(block, dict) or not isinstance(block.get('trials'), list)
                or type(block.get('block_index')) is not int or block['block_index'] != index):
            raise ApiError('Invalid manifest block order')
        try:
            session_id = identifier(block.get('session_id', 'session-001'))
            run_id = identifier(block.get('run_id', f'run-{index:03d}'))
        except ValueError as error:
            raise ApiError('Invalid manifest session or run identity') from error
        if any((item.session_id, item.run_id) == (session_id, run_id) for item in parsed):
            raise ApiError('Duplicate run identity within a session')
        trials = tuple(_trial(row, position) for position, row in enumerate(block['trials'], 1))
        for trial in trials:
            if trial.trial_id in seen:
                raise ApiError('Duplicate trial identity')
            seen.add(trial.trial_id)
        parsed.append(_Block(session_id, run_id, trials))
    if not seen:
        raise ApiError('Manifest has no trials')
    return tuple(parsed)


def _trial(row: JSONObject, position: int) -> _Assignment:
    try:
        segment = Segment(**row['segment']) if row['segment'] is not None else None
        return _Assignment(row['trial_id'], row['media_id'], position, row['role'], row['media_type'], segment)
    except (ValueError, TypeError, KeyError) as error:
        raise ApiError('Invalid manifest trial') from error


class Experiment:
    def __init__(self, client: Client, experiment_id: str, *, _name: str | None = None,
                 _document: JSONObject | None = None):
        self._client = client
        self.id = identifier(experiment_id)
        self._path = '/api/v1/experiments/' + self.id
        self._name = _name
        self._saved_document = _document
        self._replacement_requests = {}

    def __repr__(self) -> str:
        return f'<Experiment {self._name!r} {self.id}>' if self._name else f'<Experiment {self.id}>'

    @cached_property
    def _document(self):
        return self._saved_document or self._client._http._json('GET', self._path)

    @property
    def name(self) -> str:
        return self._document['name']

    @property
    def revision(self) -> int:
        return self._document['revision']

    @cached_property
    def _media_manifest(self):
        return media_manifest(self._client._http, self._path, self.id)

    @property
    def videos(self) -> tuple[str, ...]:
        """Distinct catalog IDs in the stable manifest order (foil halves share a catalog ID)."""
        return tuple(dict.fromkeys(row['catalog_id'] for row in self._media_manifest['media']))

    def download(self, folder: str | Path) -> Path:
        """Write a portable BIDS stimuli folder; verify and reuse files already present."""
        return _download(self._client._http, self._path, self._media_manifest, folder)

    def replace(self, videos: list[str], *, reason: str = '') -> Experiment:
        """Replace the named catalog videos with eligible alternatives in a new revision."""
        if (not isinstance(videos, list) or not videos or any(not isinstance(video, str) for video in videos)
                or len(set(videos)) != len(videos)):
            raise ValueError('videos must be distinct catalog IDs from this experiment')
        if not isinstance(reason, str) or len(reason) > 1000 or (reason and not reason.isprintable()):
            raise ValueError('reason must be a printable string of at most 1000 characters')
        key = (tuple(sorted(videos)), reason)
        request_id = self._replacement_requests.setdefault(key, uuid.uuid4().hex)
        document = self._client._http._json('POST', self._path + '/replacements',
            dict(request_id=request_id, expected_digest=self._document['digest'], clip_ids=videos, reason=reason))
        return Experiment(self._client, document['id'], _document=document)

    def schedule(self, *, subjects: int, sessions: int = 1, runs: int = 1, trials_per_run: int,
                 foils_per_run: int = 0, repeats_per_run: int = 0, shared_fraction: float = 0) -> Experiment:
        """Add subjects, sessions and runs to this selection as a new frozen revision."""
        settings = dict(subject_count=subjects, sessions_per_subject=sessions, runs_per_session=runs,
                        trials_per_run=trials_per_run, foils_per_run=foils_per_run,
                        repeats_per_run=repeats_per_run, shared_fraction=shared_fraction)
        document = self._client._http._json('POST', self._path + '/schedule', dict(settings=settings))
        return Experiment(self._client, document['id'], _document=document)

    def summary(self) -> JSONObject:
        document = self._client._http._json('GET', self._path)
        settings = document['settings']
        parents = settings.get('parents_per_subject', 0)
        publication = document.get('publication')
        subject_ids = ([row['subject_id'] for row in publication['subjects']] if publication is not None
                       else [f'subject-{index + 1:03d}' for index in range(settings['subject_count'])])
        return dict(id=self.id, name=settings['name'], published=publication is not None,
                    settings=settings, shared_fraction_realized=settings.get('shared_per_subject', 0) / parents if parents else 0,
                    subject_ids=subject_ids)

    def publish(self) -> Experiment:
        self._client._http._json('POST', self._path + '/publish', {})
        return self

    def checkout(self, *, subject: int, session: int = 1, folder: str | Path, takeover: bool = False):
        """Reserve a subject session online and download its offline run plans and stimuli."""
        from ._session import checkout
        return checkout(self, subject=subject, session=session, folder=folder, takeover=takeover)
