# dbstim

Create experiments on [dbstimuli.com](https://dbstimuli.com), retrieve assigned
videos, and report presentation progress from your own task program.

**Client → Experiment → Subject → Session → Run → Trial**

## Try the notebook

```bash
git clone https://github.com/lobennett/dbstim.git
cd dbstim
uv sync --extra demo
uv run marimo edit notebooks/api.py
```

Enter your website credentials in the notebook. Never save passwords in code.
The new session endpoints must be deployed before using this version against
a server. Older `dbp_api` clients remain available at their pinned Git revisions.

## Create or load

```python
from getpass import getpass
from dbstim import Client, MetricFilter

client = Client("your-username", getpass("Website password: "))
client.metrics()
client.experiments()

experiment = client.create_experiment(
    "Memory demo", subjects=5, sessions_per_subject=1, runs_per_session=2,
    trials_per_run=24, foils_per_run=4, shared_fraction=0.1,
    filters=[MetricFilter("duration_seconds", "gte", 10),
             MetricFilter("duration_seconds", "lte", 20)], seed="demo-1",
)
experiment.summary()
experiment.publish()
```

Alternatively: `client.experiment("Memory demo")`, or
`client.experiment(id="exact-revision-id")`. Name lookup selects the latest
published revision; ambiguous names require an ID. Creation saves a draft.

`sampling` accepts `"random"`, `"balanced"`, or `"unique"` YouTube channels.
Foils and repeats count toward `trials_per_run`. With 24 trials and four foils,
each run has 16 full videos, four first halves, and four complementary halves.
The shared fraction counts unique parent videos, rounds down, and uses the same
shared sources for every subject with independently seeded order.

## Present

```python
subject = experiment.subject("subject-001")
session = subject.session("session-001")
session.download("downloads/subject-001/session-001")
session.start()
run = session.run("latest")

while (trial := run.next_trial()) is not None:
    path = run.media(trial)
    run.record(trial, "started")
    present(path)  # Your PGL, PsychoPy, or other presentation function.
    run.record(trial, "completed", result={"response": "seen"})

session.finish("interrupted")
```

Choose subsequent runs explicitly from `session.run_ids`. Finish `"completed"`
only after all session trials; use `"failed"` or `"interrupted"` when stopping early.
Stop presentation on any API error. Retry an unacknowledged call unchanged.
Started-but-unconfirmed trials block automatic continuation.
Their skip/retry policy remains undecided; do not mark uncertain trials completed.

In another program, reuse the download with
`subject.session("session-001", directory="downloads/subject-001/session-001")`.
`run.results` retrieves task results saved on the server.

Media modes: `"local"` verifies downloaded bytes; `"cache"` downloads on demand;
`"remote"` returns a five-minute playback URL. Halves are already rendered.
Request remote links just before playback. The server currently limits source
files to 32 MiB and rendered segments to 60 seconds.
The server stores progress; another device reads the same state.
`session.start(takeover=True)` explicitly replaces an active presenter after review.

## Development

```bash
uv run python -m unittest discover -s tests -v
uv run marimo check notebooks/api.py
```

[Objects](docs/api-classes.md) · [Data flow](docs/server-to-local-flow.md)
