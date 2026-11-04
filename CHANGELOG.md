# Changelog

## 1.0.0

dbstim 1.0 replaces the 0.5 online session API. Tracking now happens offline, so
a run never waits on the website. Code written for 0.5 needs changes; tags
`v0.3.0` to `v0.5.0` and all website data stay available.

Needs a dbstimuli.com server with selections, experiment media manifests and
run sync (dbp-dataset-browser `6799d6d` or later).

### New

- `client.create(name, filters=, count=, seed=)` saves a frozen selection of
  videos. Names are unique per account. `client.open(name, revision=None)` or
  `client.open(id=...)` reopens one.
- `Filter(metric, op, value)` is a metric condition. `client.metrics()` returns
  metric names.
- `experiment.download(folder)` writes a portable BIDS folder: videos,
  `manifest.json` and a PGL-loadable `manifest.csv` under `stimuli/`, plus
  `dataset_description.json` and `README`.
- `experiment.schedule(...)` adds subjects, sessions and runs as a new
  revision. `experiment.replace(videos)` swaps videos in a new revision.
- `experiment.checkout(subject=, session=, folder=)` reserves a subject session
  for this computer and downloads its videos and run plans.
  `takeover=True` moves the reservation to another computer.
- `dbstim.session(folder)` reopens a checked-out session with no network.
  `with session.run(n) as run: for trial in run:` records
  `run.started`, `run.completed` and `run.abandoned` to a crash-safe journal.
  dbstim writes a BIDS `*_events.tsv` and sidecar for each run.
- `session.sync()` uploads each finished run once. It returns `accepted`,
  `already synced`, `kept as flagged attempt` or `waiting` for each run, and
  raises `SyncError` at the end if the server refused any run.
- `dbstim.frame_summary(presented_times, movie_times, duration)` summarizes
  PGL frame timing for a trial result.
- `examples/pgl_tracked_movie_task.py` shows a PGL task that records a run.
  dbstim itself does not import PGL.

### Removed (breaking)

- `Subject`, `experiment.subjects` and `experiment.subject()`.
- The online `Session` and `Run`: `with session:`, `start()`, `finish()`,
  `next_run()`, `next_trial()`, `record(trial, status)`, `media(trial, mode=)`,
  `session.download()`, `unconfirmed`, `abandoned` and `results`.
- The per-user media cache in `~/.cache/dbstim`. Downloads go to the folder you
  name.

### Changed (breaking)

- `Trial` is now `(number, file, video_id, role)`, with `role` one of
  `"video"`, `"repeat"` or `"foil"`. 0.5 had `trial_id`, `stimulus_id`,
  `position`, `role` (`"parent"`, ...), `media_type` and `segment`.
- `client.metrics()` returns a list of metric names instead of `Metric` objects.
- `dbstim.Session` and `dbstim.Run` are the offline classes above.

### Still present from 0.5

`client.create_experiment()`, `client.experiment()`, `client.experiments()`,
`experiment.publish()`, `experiment.summary()`, `MetricFilter` and `Metric` are
unchanged. They are not part of the 1.0 workflow; new code should use
`create`, `open`, `Filter` and `schedule`.
