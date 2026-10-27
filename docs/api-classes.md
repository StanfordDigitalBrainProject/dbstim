# API objects

`dbstim.connect()` asks for your password and returns a `Client` signed in to
**dbstimuli.com** (`url=` selects another server). Arrows show how you get each
object.

```mermaid
classDiagram
    direction TB
    class Client {
        username
        url
        metrics() list_of_Metric
        experiments()
        create_experiment(name, settings) Experiment
        experiment(name_or_id) Experiment
    }
    class Experiment {
        id
        summary()
        subjects
        publish() Experiment
        subject(id) Subject
    }
    class Subject {
        id
        sessions
        session_ids
        session(id, directory) Session
    }
    class Session {
        id
        run_ids
        unconfirmed
        abandoned
        results
        with session
        next_run() Run_or_None
        download(directory=None) Path
        record(trial, status, result)
        run(id) Run
        start(takeover)
        finish(status)
    }
    class Run {
        id
        trials
        results
        next_trial() Trial_or_None
        media(trial, mode) Path_or_URL
        record(trial, status, result)
    }
    class Trial {
        <<frozen dataclass>>
        trial_id
        stimulus_id
        position
        role
        media_type
        segment
    }
    class Segment {
        <<frozen dataclass>>
        start_seconds
        end_seconds
    }
    class MetricFilter {
        <<frozen dataclass>>
        metric_id
        operator
        value
        to_dict()
    }
    Client --> Experiment : create or load
    Experiment --> Subject : subjects
    Subject --> Session : sessions
    Session --> Run : next_run
    Run --> Trial : trials or next_trial
    Trial --> "0..1" Segment : source interval
    Client ..> MetricFilter : creation filters
```

Signatures are shortened; creation settings are keyword arguments.
`experiment("Exact name")` prefers the latest published revision; if several
experiments share the name, use `experiment(id="revision-id")`. Close the client
with `Client.close()` or a `with` block.

## What the objects mean

- **Experiment:** a saved selection and design; publishing freezes its
  assignments.
- **Subject:** one participant's sessions, from a published, hash-verified
  manifest.
- **Session:** the presentation claim and progress. `with session:` starts it
  and finishes it as `completed`, `interrupted` (Ctrl-C or an abandoned trial),
  or `failed`; `finish(status)` does so by hand. `record()` accepts any trial in
  the session.
- **Run:** trials in order; `next_trial()` reads progress from the server.
  `results` is a property holding the recorded results, not a method.
- **Trial:** an immutable assignment, not a player or progress record. Positions
  start at 1 in each run; roles are `parent`, `repeat`, or `foil`.
- **Segment:** the optional source interval. First halves and their foils are
  already cut; play them from time zero.
- **MetricFilter:** metric, comparison operator (`">="` or `"gte"`; `"=="`/`"!="`
  with `True`/`False`), and threshold.
- **Metric:** one filterable metric from `client.metrics()`.

`session.next_run()` returns the first run with unfinished trials, or `None`;
`session.run("latest")` returns the most recently started run. A trial recorded
`started` with no ending, listed in `session.unconfirmed`, blocks `next_trial()`
in every run until you record it `completed` or `abandoned`.
`session.abandoned` lists every abandoned trial, with or without a result;
`session.results` lists only the result payloads you recorded, each with its
status. `run.media(trial)` returns a verified file from the per-user cache,
downloading it first if needed.

Workflow classes are in `src/dbstim/workflow.py`, sign-in in `client.py`, and
frozen data types in `models.py`. Video is currently the only supported media
type. Your PGL or PsychoPy task presents the video; **the server owns progress**.

[Metric storage and filters](metrics-and-filters.md) · [Media and session flow](server-to-local-flow.md) · [Recovery](recovery.md)
