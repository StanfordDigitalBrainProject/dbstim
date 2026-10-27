# API map

[api-interface.html](api-interface.html) is the interactive version: download
it and open it in any browser, offline. Both describe dbstim 0.5.0.

Colors: **blue** sends HTTP to dbstimuli.com, **green** stays in your Python
process, **amber** uses the media cache, **purple** is your own code.

```mermaid
flowchart TD
    connect["dbstim.connect()"]:::http --> choice{New or existing?}
    choice -->|new| create["client.create_experiment(...)"]:::http
    choice -->|existing| reopen["client.experiment(name)<br/>id=... sends nothing"]:::http
    create -->|draft| publish["experiment.publish()"]:::http
    reopen -->|draft| publish
    reopen -->|published| subjects
    publish --> subjects["experiment.subjects[0]"]:::http
    subjects --> sessions["subject.sessions[0]"]:::local
    sessions --> download["session.download()<br/>records no progress"]:::disk
    sessions --> enter
    subgraph tracked["Tracked presentation"]
        enter["with session: start()"]:::http --> nextRun["session.next_run()"]:::http
        nextRun -->|Run| nextTrial["run.next_trial()"]:::http
        nextTrial -->|Trial| media["run.media(trial)"]:::disk
        media --> started["run.record(trial, 'started')"]:::http
        started --> present["present(path)"]:::yours
        present --> ended["run.record(trial, 'completed', result=...)<br/>or 'abandoned'"]:::http
        ended --> nextTrial
        nextTrial -->|None| nextRun
        nextRun -->|None| finish["leave block: finish(status)"]:::http
    end
    classDef http fill:#e6eefb,stroke:#1f5fbf,color:#1d232b
    classDef local fill:#e7f1e9,stroke:#3f6b4a,color:#1d232b
    classDef disk fill:#f8eedb,stroke:#94600f,color:#1d232b
    classDef yours fill:#f1e8f8,stroke:#74419c,color:#1d232b
```

Downloading fills the per-user cache (`~/.cache/dbstim`); `run.media(trial)`
then returns a verified file without a request, or downloads that one video if
it is missing. `mode="remote"` returns a short-lived URL instead.

## Trial outcomes

```mermaid
stateDiagram-v2
    direction LR
    [*] --> Started: record "started"
    Started --> Completed: record "completed"
    Started --> Abandoned: record "abandoned"
    Completed --> [*]
    Abandoned --> [*]
    note right of Started
        Unconfirmed until it has an ending.
        session.unconfirmed lists it and
        run.next_trial() raises in every run.
    end note
```

Neither ending is presented again. Leaving `with session:` finishes the session
as `completed` when every trial is completed, `interrupted` after Ctrl-C or when
any trial is abandoned or unfinished, and `failed` after an exception.

## Recovery

- **Stopped mid-trial:** record each of `session.unconfirmed` as `"completed"`
  only if it was shown to the end, otherwise `"abandoned"`.
- **Dropped connection:** `start`, `record` and `finish` are resent unchanged on
  connection failures and HTTP 500, 502, 503 or 504, never on 4xx. If every
  attempt fails, repeat that call on the same session object before any other.
- **Another presenter:** once it has stopped, `session.start(takeover=True)`.

dbstim does not render video, judge whether a trial was seen, write a
standalone manifest, or record progress offline.

[API objects](api-classes.md) · [Data flow](server-to-local-flow.md) · [Recovery](recovery.md)
