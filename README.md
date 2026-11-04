# dbstim

Choose videos on [dbstimuli.com](https://dbstimuli.com), download them as a
portable BIDS stimuli folder, and record what your task showed. Recording
works offline; you sync afterwards.

## Install

Requires Python 3.11+. No other dependencies.

    pip install "git+https://github.com/StanfordDigitalBrainProject/dbstim.git@v1.0.0"

or with uv:

    uv add "dbstim @ git+https://github.com/StanfordDigitalBrainProject/dbstim.git@v1.0.0"

For the example notebook, add the Jupyter extra:

    pip install "dbstim[jupyter] @ git+https://github.com/StanfordDigitalBrainProject/dbstim.git@v1.0.0"

Sign in with your dbstimuli.com account. Upgrading from 0.5? The API changed;
see the [changelog](CHANGELOG.md).

## A. Download videos for your own code

```python
import dbstim
from dbstim import Filter

client = dbstim.connect()  # asks for your username and password
client.metrics()           # metric names you can filter on
experiment = client.create("Clips", filters=[Filter("duration_seconds", ">=", 10)],
                           count=40, seed=1)
experiment.download("clips_bids")
```

Later, `client.open("Clips")` reopens the latest revision. The folder works
without dbstim:

    clips_bids/dataset_description.json, README
    clips_bids/stimuli/*.mp4
    clips_bids/stimuli/manifest.json   revision, filters, sizes and SHA-256
    clips_bids/stimuli/manifest.csv    filename, index, id, ... for PGL

No subjects, scheduling or tracking are needed.

## B. Track a run

Online, before the session:

```python
experiment = client.open("Clips").schedule(subjects=5, runs=2, trials_per_run=4)
experiment.checkout(subject=1, session=1, folder="sub01_ses01")
```

Checkout reserves the session for this computer and downloads its videos.
Then, offline, with no website traffic during the run:

```python
session = dbstim.session("sub01_ses01")
with session.run(1) as run:
    for trial in run:                    # trials still to show, in order
        run.started(trial, onset=0.0)    # seconds from your run's time zero
        ...                              # your code plays trial.file
        run.completed(trial, duration=4.5, result={"key": "j"})
        # or run.abandoned(trial) if it was not shown to the end
```

Every call is written to disk before it returns. Leaving the block writes
`sub-01/ses-01/beh/sub-01_ses-01_task-dbstim_run-01_events.tsv`. Online
again, afterwards:

```python
session.sync()  # {1: 'accepted', 2: 'waiting'}
```

With PGL, call these from your `pglTask` hooks; see
[examples/pgl_tracked_movie_task.py](examples/pgl_tracked_movie_task.py).

## When something goes wrong

- **Crash or power cut:** open the run again. A trial started without an
  ending is recorded `abandoned`; the loop continues with the next trial.
  Nothing is shown twice.
- **Another computer:** `checkout(..., takeover=True)` moves the reservation.
  Runs the first computer syncs afterwards are kept as flagged attempts.
- **Sync refused:** the other runs still sync; `SyncError` then lists the
  refused ones.

[Recovery](docs/recovery.md) has the details.

## Development

```bash
uv run python -m unittest discover -s tests -v
```

Set `DBSTIM_SERVER` to a dbp-dataset-browser checkout to also run the tests
against the real server code on temporary local databases.

[Guide](https://stanforddigitalbrainproject.github.io/dbstim/) · [API map](docs/api-interface.md) · [Objects](docs/api-classes.md) · [Data flow](docs/server-to-local-flow.md) · [Notebook](notebooks/api.ipynb)
