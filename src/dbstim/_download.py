"""Portable, verified experiment stimuli; no presentation or response state."""

from contextlib import contextmanager
import csv
import hashlib
import json
import math
import os
from pathlib import Path
import re
import tempfile
from urllib.parse import quote

from ._transport import ApiError


def _hash(path: Path) -> str:
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


@contextmanager
def _atomic(path: Path, *, binary=False):
    """Only a complete file takes its final name; interrupted files are never reusable."""
    descriptor, temporary = tempfile.mkstemp(prefix='.' + path.name + '-', dir=path.parent)
    try:
        options = {} if binary else dict(encoding='utf-8', newline='')
        with os.fdopen(descriptor, 'wb' if binary else 'w', **options) as stream:
            yield stream
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)


def media_manifest(http, path, experiment_id):
    document = http._json('GET', path + '/media-manifest')
    try:
        if (document['schema_version'] != 'dbp-experiment-media-v1'
                or document['experiment_id'] != experiment_id
                or not isinstance(document['name'], str)
                or type(document['revision']) is not int or document['revision'] < 1
                or not isinstance(document['dataset_version'], str)
                or not isinstance(document['seed'], (str, int)) or not isinstance(document['filters'], list)
                or not isinstance(document['media'], list) or not document['media']):
            raise ValueError()
        ids, files = set(), set()
        for row in document['media']:
            media_id = row['media_id']
            if (not isinstance(media_id, str) or not media_id or media_id in ids
                    or not isinstance(row['catalog_id'], str) or not row['catalog_id']
                    or not isinstance(row['sha256'], str) or not re.fullmatch('[0-9a-f]{64}', row['sha256'])
                    or type(row['size_bytes']) is not int or row['size_bytes'] <= 0
                    or type(row['duration_seconds']) not in (int, float)
                    or not math.isfinite(row['duration_seconds']) or row['duration_seconds'] <= 0):
                raise ValueError()
            # Catalog IDs can contain punctuation unsuitable for BIDS stim_file paths.
            # A digest keeps sanitization injective, including distinct foil renditions.
            filename = row['file_name']
            if not isinstance(filename, str):
                raise ValueError()
            if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.+-]*\.mp4', filename):
                filename = 'video-' + hashlib.sha256(media_id.encode()).hexdigest() + '.mp4'
            if filename in files:
                raise ValueError()
            row['file_name'] = filename
            ids.add(media_id)
            files.add(filename)
        document['media'].sort(key=lambda row: row['media_id'])
    except (TypeError, KeyError, ValueError):
        raise ApiError('Invalid experiment media manifest') from None
    return document


def _video(http, path, target, row):
    if target.is_file() and target.stat().st_size == row['size_bytes'] and _hash(target) == row['sha256']:
        return
    with http._request('GET', path + '/media/' + quote(row['media_id'], safe=''), media=True) as response:
        if response.headers.get_content_type() != 'video/mp4':
            raise ApiError('Expected video/mp4 media')
        if row['size_bytes'] > http.max_media_bytes:
            raise ApiError('Media exceeds configured size limit')
        digest, size = hashlib.sha256(), 0
        with _atomic(target, binary=True) as stream:
            while chunk := http._read(response, 1024 * 1024):
                size += len(chunk)
                if size > row['size_bytes']:
                    raise ApiError('Media size does not match manifest')
                digest.update(chunk)
                stream.write(chunk)
            if size != row['size_bytes'] or digest.hexdigest() != row['sha256']:
                raise ApiError('Media SHA-256 or size does not match manifest')


def download(http, path, document, folder):
    root = Path(folder).expanduser()
    stimuli = root / 'stimuli'
    manifest = stimuli / 'manifest.json'
    if manifest.exists():
        try:
            previous = json.loads(manifest.read_text(encoding='utf-8'))
        except (ValueError, UnicodeError):
            raise ApiError('Existing stimuli manifest is unreadable; choose another folder') from None
        if previous != document:
            raise ApiError('Folder belongs to another experiment revision or manifest; choose another folder')
    elif stimuli.exists() and any(stimuli.iterdir()):
        raise ApiError('Stimuli folder has no manifest; choose an empty folder')
    if stimuli.exists():
        listed = {row['file_name'] for row in document['media']} | {'manifest.csv'}
        if any(file.suffix.lower() in ('.mp4', '.csv') and file.name not in listed for file in stimuli.iterdir()):
            raise ApiError('Stimuli folder contains unlisted movies or CSV files; choose another folder')
    stimuli.mkdir(parents=True, exist_ok=True)
    with _atomic(manifest) as stream:
        json.dump(document, stream, indent=2, ensure_ascii=False, allow_nan=False)
        stream.write('\n')
    for row in document['media']:
        _video(http, path, stimuli / row['file_name'], row)
    with _atomic(stimuli / 'manifest.csv') as stream:
        writer = csv.DictWriter(stream, fieldnames=['filename', 'index', 'id', 'sha256', 'bytes', 'duration'])
        writer.writeheader()
        for index, row in enumerate(document['media'], 1):
            writer.writerow(dict(filename=row['file_name'], index=index, id=row['catalog_id'], sha256=row['sha256'],
                                 bytes=row['size_bytes'], duration=row['duration_seconds']))
    if not (root / 'dataset_description.json').exists():
        from . import __version__
        with _atomic(root / 'dataset_description.json') as stream:
            json.dump(dict(Name=document['name'], BIDSVersion='1.11.2', DatasetType='raw',
                           GeneratedBy=[dict(Name='dbstim', Version=__version__)]), stream, indent=2)
            stream.write('\n')
    if not (root / 'README').exists():
        with _atomic(root / 'README') as stream:
            stream.write('Stimuli downloaded with dbstim.\n'
                         'stimuli/manifest.json records the frozen experiment revision and file checksums.\n'
                         'stimuli/manifest.csv lists local movies for PGL. Paths are relative to stimuli/.\n'
                         'This folder contains stimuli only; behavioral responses belong to the presentation task.\n')
    return root
