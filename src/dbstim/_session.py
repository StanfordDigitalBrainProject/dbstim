"""Offline tracked runs. The journal is authoritative; BIDS tables are derived output."""
from contextlib import contextmanager
import csv
from dataclasses import dataclass
from functools import lru_cache
import json
import numbers
import os
import platform
import re
import subprocess
from pathlib import Path
import time
import uuid
from typing import Literal

from ._download import _atomic, download
from ._transport import ApiError


@lru_cache(maxsize=1)
def _computer_id():
    # getnode() can fall back to a new random MAC in each process. Prefer the OS
    # machine identity so a crash/reboot keeps the online reservation usable.
    if platform.system() == 'Darwin':
        output = subprocess.check_output(['/usr/sbin/ioreg', '-rd1', '-c', 'IOPlatformExpertDevice'], text=True)
        identity = re.search(r'"IOPlatformUUID" = "([^"]+)"', output).group(1)
    elif os.name == 'nt':
        import winreg
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r'SOFTWARE\Microsoft\Cryptography',
                            access=winreg.KEY_READ | winreg.KEY_WOW64_64KEY) as key:
            identity = winreg.QueryValueEx(key, 'MachineGuid')[0]
    else:
        identity = ''
        for candidate in ('/etc/machine-id', '/var/lib/dbus/machine-id'):
            try:
                identity = Path(candidate).read_text().strip()
            except OSError:
                continue
            if identity:
                break
        if not identity:
            raise RuntimeError('Tracked runs need a stable machine identity in /etc/machine-id '
                               'or /var/lib/dbus/machine-id')
    return uuid.uuid5(uuid.NAMESPACE_OID, identity).hex


def _seconds(value):
    if not isinstance(value, numbers.Real) or isinstance(value, bool) or not 0 <= value <= 1e12:
        raise ValueError('Timing must be finite nonnegative seconds (at most 1e12)')
    return float(value)


def _save(path, value):
    with _atomic(path) as stream:
        json.dump(value, stream, ensure_ascii=False, allow_nan=False)
        stream.write('\n')
    _sync_directory(path.parent)


def _sync_directory(path):
    if os.name != 'nt':
        descriptor = os.open(path, os.O_RDONLY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)


class _Busy(RuntimeError):
    pass


@contextmanager
def _lock(path):
    """Process locks release on a crash, so reopening never needs stale-lock deletion."""
    with path.open('a+b') as stream:
        try:
            if os.name == 'nt':
                import msvcrt
                stream.seek(0)
                if not stream.read(1):
                    stream.write(b'\0')
                    stream.flush()
                stream.seek(0)
                msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as error:
            raise _Busy('This session is already open in another recorder or sync') from error
        try:
            yield
        finally:
            if os.name == 'nt':
                stream.seek(0)
                msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(stream, fcntl.LOCK_UN)


def checkout(experiment, *, subject, session=1, folder, takeover=False):
    for value in (subject, session):
        if type(value) is not int or value < 1:
            raise ValueError('subject and session must be positive integers')
    if type(takeover) is not bool:
        raise ValueError('takeover must be a boolean')
    from .workflow import _manifest
    subject_id, session_id = f'subject-{subject:03d}', f'session-{session:03d}'
    http = experiment._client._http
    path = experiment._path + '/subjects/' + subject_id
    manifest = http._json('GET', path + '/manifest')
    blocks = _manifest(manifest, experiment.id, subject_id)
    selected = [(raw, block) for raw, block in zip(manifest['blocks'], blocks) if block.session_id == session_id]
    if not selected:
        raise ValueError('No such subject session in this experiment')
    root = Path(folder).expanduser().resolve()
    state = root / 'stimuli' / '.dbstim'
    metadata = dict(schema_version=1, origin=http.origin, username=experiment._client.username,
                    experiment_id=experiment.id, subject=subject, session=session,
                    manifest_sha256=manifest['manifest_sha256'],
                    blocks=[dict(raw, run_id=block.run_id) for raw, block in selected])
    if (state / 'session.json').exists() and json.loads((state / 'session.json').read_text(encoding='utf-8')) != metadata:
        raise ValueError('Folder belongs to another subject session or revision')
    media = json.loads(json.dumps(experiment._media_manifest))
    wanted = {trial.stimulus_id for _, block in selected for trial in block.trials}
    media['media'] = [row for row in media['media'] if row['media_id'] in wanted]
    claim = dict(request_id=uuid.uuid4().hex, writer_id=_computer_id(), takeover=takeover)
    response = http._json('POST', path + '/sessions/' + session_id + '/start', claim)
    if response.get('accepted') != claim['request_id']:
        raise ApiError('Server did not acknowledge the session checkout')
    download(http, experiment._path, media, root)
    state.mkdir(exist_ok=True)
    _sync_directory(state.parent)
    with _lock(state / 'session.lock'):
        _save(state / 'session.json', metadata)
    return Session(root, _client=experiment._client)


@dataclass(frozen=True)
class Trial:
    number: int
    file: Path
    video_id: str
    role: Literal['video', 'repeat', 'foil']


class SyncError(ApiError):
    """The server refused some closed runs; the other runs still synced.

    refused maps each refused run number to the server's error. outcomes maps every
    other run number to its sync outcome.
    """
    def __init__(self, refused, outcomes):
        super().__init__('Sync refused ' + '; '.join(f'run {number}: {error}' for number, error in refused.items()))
        self.refused = refused
        self.outcomes = outcomes


class Session:
    """A portable checked-out subject session. Opening and recording require no network."""
    def __init__(self, folder, *, _client=None):
        self.folder = Path(folder).expanduser().resolve()
        self._state = self.folder / 'stimuli' / '.dbstim'
        self._metadata = json.loads((self._state / 'session.json').read_text(encoding='utf-8'))
        self.subject = self._metadata['subject']
        self.session = self._metadata['session']
        self.runs = tuple(range(1, len(self._metadata['blocks']) + 1))
        self._client = _client
        self._media = {row['media_id']: row for row in
                       json.loads((self.folder / 'stimuli/manifest.json').read_text(encoding='utf-8'))['media']}
        self._path = (f"/api/v1/experiments/{self._metadata['experiment_id']}/subjects/subject-{self.subject:03d}"
                      f'/sessions/session-{self.session:03d}')

    def run(self, number):
        if type(number) is not int or number not in self.runs:
            raise ValueError(f'Choose a run from {self.runs}')
        return Run(self, number)

    def sync(self, *, client=None):
        """Upload each closed run once and return {run number: outcome} for every run.

        Outcomes are 'accepted', 'already synced', 'kept as flagged attempt', or 'waiting'
        for a run that is not closed yet. client= reuses an authenticated connection;
        otherwise a reopened session prompts through connect() when a run needs uploading.
        When the server refuses runs, the remaining runs still sync and SyncError then
        names each refused run. RuntimeError means another recorder or sync has the
        session open.
        """
        from .client import connect
        outcomes, refused = {}, {}
        owned_client = None
        try:
            with _lock(self._state / 'session.lock'):
                for number in self.runs:
                    run = self.run(number)
                    run._read()
                    if not run._closed:
                        outcomes[number] = 'waiting'
                        continue
                    receipt_path = self._state / f'run-{number:03d}-receipt.json'
                    previous = receipt_path.exists()
                    if previous:
                        response = json.loads(receipt_path.read_text(encoding='utf-8'))['response']
                    else:
                        connection = client or self._client
                        if connection is None:
                            if owned_client is None:
                                owned_client = connect(self._metadata['username'], url=self._metadata['origin'])
                            connection = owned_client
                        if connection._http.origin != self._metadata['origin']:
                            raise ValueError('Sync client belongs to another server')
                        try:
                            response, previous = run._upload(connection._http)
                        except ApiError as error:
                            if error.status is None:
                                raise
                            refused[number] = error
                            continue
                        _save(receipt_path, dict(response=response))
                    run._write_events(flagged=response['flagged'])
                    outcomes[number] = ('kept as flagged attempt' if response['flagged'] else
                                        'already synced' if previous else 'accepted')
        finally:
            if owned_client is not None:
                owned_client.close()
        if refused:
            raise SyncError(refused, outcomes)
        return outcomes


def session(folder):
    """Reopen a checked-out session without connecting to the server."""
    return Session(folder)


def _json(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=False, allow_nan=False).encode('utf-8')


class Run:
    """Record offline inside a with block; reopen to resume unfinished trials.

    Started trials without an outcome become abandoned on recovery. Completed and
    abandoned trials never replay. A run closes for sync once all trials resolve;
    leaving an unfinished run writes its current events file and permits resuming.
    """
    def __init__(self, session, number):
        self.session, self.number = session, number
        self._block = session._metadata['blocks'][number - 1]
        self._journal = session._state / f'run-{number:03d}.jsonl'
        self._trials = tuple(Trial(i, session.folder / 'stimuli' / session._media[row['media_id']]['file_name'],
                                   session._media[row['media_id']]['catalog_id'],
                                   'video' if row['role'] == 'parent' else row['role'])
                             for i, row in enumerate(self._block['trials'], 1))
        self._records = []
        self._events = []
        self._active = False
        self._write_failed = False

    @property
    def _closed(self):
        return bool(self._records and self._records[-1].get('kind') == 'closed')

    def _read(self):
        self._records = []
        self._events = []
        if not self._journal.exists():
            return
        with self._journal.open('r+b') as stream:
            valid = 0
            for line in stream:
                if not line.endswith(b'\n'):
                    stream.truncate(valid)
                    stream.flush()
                    os.fsync(stream.fileno())
                    break
                record = json.loads(line)
                self._records.append(record)
                if 'event_id' in record:
                    self._events.append(record)
                valid += len(line)

    def _append(self, record):
        encoded = _json(record) + b'\n'
        try:
            created = not self._journal.exists()
            with self._journal.open('ab') as stream:
                stream.write(encoded)
                stream.flush()
                os.fsync(stream.fileno())
            if created:
                _sync_directory(self._journal.parent)
        except BaseException:
            self._write_failed = True
            raise
        stored = json.loads(encoded)
        self._records.append(stored)
        if 'event_id' in stored:
            self._events.append(stored)

    def __enter__(self):
        if self._active:
            raise RuntimeError('Run is already open')
        self._guard = _lock(self.session._state / 'session.lock')
        self._guard.__enter__()
        try:
            self._write_failed = False
            self._read()
            resumed = bool(self._records)
            if not resumed:
                self._append(dict(kind='opened', started_at=time.time(), request_id=uuid.uuid4().hex,
                                  writer_id=_computer_id()))
            if not self._closed:
                if self._records[0]['writer_id'] != _computer_id():
                    raise RuntimeError('An unfinished run belongs to another computer')
                if resumed:
                    self._append(dict(kind='resumed', resumed_at=time.time()))
            self._active = True
            if self._events and self._events[-1]['status'] == 'started':
                trial = self._trials[len(self._events) // 2]
                self.abandoned(trial)
            self._write_events()
            return self
        except BaseException:
            self._active = False
            self._guard.__exit__(None, None, None)
            raise

    def __exit__(self, kind, error, traceback):
        try:
            if self._write_failed:
                return
            if self._events and self._events[-1]['status'] == 'started':
                self.abandoned(self._trials[len(self._events) // 2])
            if not self._closed and len(self._events) == 2 * len(self._trials):
                status = 'abandoned' if any(e['status'] == 'abandoned' for e in self._events) else 'completed'
                self._append(dict(kind='closed', ended_at=self._now(), status=status))
            self._write_events()
        finally:
            self._active = False
            self._guard.__exit__(kind, error, traceback)

    def __iter__(self):
        self._require_open()
        resolved = {e['trial_id'] for e in self._events if e['status'] != 'started'}
        return (trial for trial, row in zip(self._trials, self._block['trials']) if row['trial_id'] not in resolved)

    def _require_open(self):
        if not self._active:
            raise RuntimeError('Use the run inside a with block')
        if self._write_failed:
            raise RuntimeError('Journal write failed; reopen the run before recording again')

    def _now(self):
        return max(time.time(), self._events[-1]['occurred_at'] if self._events else self._records[0]['started_at'])

    def _event(self, trial, status, **values):
        self._require_open()
        events = self._events
        index = len(events) // 2
        pending = bool(len(events) % 2)
        if (self._closed or index >= len(self._trials) or trial != self._trials[index]
                or (status == 'started') == pending):
            raise ValueError('Record the next assigned trial: started, then completed or abandoned')
        row = self._block['trials'][index]
        if status == 'started':
            values['planned_duration'] = self.session._media[row['media_id']]['duration_seconds']
        attempt = uuid.uuid4().hex if status == 'started' else events[-1]['attempt_id']
        self._append(dict(event_id=uuid.uuid4().hex, attempt_id=attempt, trial_id=row['trial_id'],
                          status=status, occurred_at=self._now(), **values))

    def started(self, trial, *, onset):
        """Record the code's onset in seconds from this run's time zero."""
        self._event(trial, 'started', onset=_seconds(onset))

    def completed(self, trial, *, duration, result=None):
        """Record the code's completion claim, actual seconds shown and optional JSON evidence."""
        if result is not None and type(result) is not dict:
            raise ValueError('result must be a plain JSON object or None')
        try:
            _json(result)
        except (TypeError, ValueError) as error:
            raise ValueError('result must contain plain finite JSON values') from error
        self._event(trial, 'completed', duration=_seconds(duration), result=result)

    def abandoned(self, trial, *, duration=None):
        """Record an unfinished presentation; omit duration when actual time is unknown."""
        self._event(trial, 'abandoned', **({} if duration is None else dict(duration=_seconds(duration))))

    def _payload(self):
        header, close = self._records[0], self._records[-1]
        return dict(request_id=header['request_id'], writer_id=header['writer_id'],
                    revision_id=self.session._metadata['experiment_id'],
                    manifest_sha256=self.session._metadata['manifest_sha256'],
                    started_at=header['started_at'], ended_at=close['ended_at'], status=close['status'], events=self._events)

    def _upload(self, http):
        """POST this closed run; previous tells a record stored before a lost reply from a new one."""
        path, payload = self.session._path, self._payload()
        try:
            http._json('GET', path + '/run-records/' + payload['request_id'])
            previous = True
        except ApiError as error:
            if error.status != 404:
                raise
            previous = False
        response = http._json('POST', path + '/runs/' + self._block['run_id'] + '/sync', payload)
        if response.get('accepted') != payload['request_id'] or type(response.get('flagged')) is not bool:
            raise ApiError('Server did not acknowledge this run')
        return response, previous

    def _events_path(self, flagged):
        root = self.session.folder
        if flagged:
            root = root / 'sourcedata' / ('attempt-' + self._records[0]['request_id'])
        subject, session = f'sub-{self.session.subject:02d}', f'ses-{self.session.session:02d}'
        return root / subject / session / 'beh' / f'{subject}_{session}_task-dbstim_run-{self.number:02d}_events.tsv'

    def _write_events(self, *, flagged=None):
        if not self._records:
            return
        receipt = self.session._state / f'run-{self.number:03d}-receipt.json'
        if flagged is None:
            flagged = receipt.exists() and json.loads(receipt.read_text(encoding='utf-8'))['response']['flagged']
        target = self._events_path(flagged)
        target.parent.mkdir(parents=True, exist_ok=True)
        parts, opening, started = {}, 0, []
        for record in self._records:
            if record.get('kind') in ('opened', 'resumed'):
                opening += 1
            elif record.get('status') == 'started':
                started.append(parts.setdefault(opening, len(parts) + 1))
        rows = []
        for trial, plan, start, end, part in zip(self._trials, self._block['trials'], self._events[::2],
                                                 self._events[1::2], started):
            media = self.session._media[plan['media_id']]
            result = end.get('result')
            rows.append(dict(onset=start['onset'], duration=end.get('duration', 'n/a'), trial_type=trial.role,
                             stim_file=media['file_name'], video_id=trial.video_id,
                             planned_duration=media['duration_seconds'], outcome=end['status'], part=part,
                             result=_json(result).decode() if result is not None else 'n/a'))
        with _atomic(target) as stream:
            writer = csv.DictWriter(stream, delimiter='\t', quotechar=None, quoting=csv.QUOTE_NONE, fieldnames=[
                'onset', 'duration', 'trial_type', 'stim_file', 'video_id', 'planned_duration', 'outcome', 'part',
                'result'])
            writer.writeheader()
            writer.writerows(rows)
        _save(target.with_suffix('.json'), dict(
            trial_type=dict(Description='Assigned stimulus role.', Levels=dict(video='Original video', repeat='Repeated video', foil='Foil segment')),
            stim_file=dict(Description='Movie path relative to the dataset stimuli directory.'),
            video_id=dict(Description='Catalog video identifier.'),
            planned_duration=dict(Description='Manifest presentation length.', Units='s'),
            outcome=dict(Description='Outcome reported by presentation code; dbstim does not verify display.',
                         Levels=dict(completed='Reported shown to the end.', abandoned='Started but not completed.')),
            part=dict(Description='Recording part of the run, numbered from 1. Resuming an interrupted run starts '
                                  'a new part; onset counts from the time zero the presentation code used in that part.'),
            result=dict(Description='Opaque compact JSON object from presentation code, or n/a.')))
        if flagged:
            raw = self._events_path(False)
            raw.unlink(missing_ok=True)
            raw.with_suffix('.json').unlink(missing_ok=True)
            _sync_directory(raw.parent)
