# API map

[api-interface.html](api-interface.html) is the interactive version: download
it and open it in any browser, offline. Both describe dbstim 1.0.0.

Colors: **blue** sends HTTP to dbstimuli.com, **green** stays in your Python
process, **amber** writes files on your computer, **purple** is your own code.

```mermaid
flowchart TD
    connect["dbstim.connect()"]:::http --> choice{New or existing?}
    choice -->|new| create["client.create(name, filters, count, seed)"]:::http
    choice -->|existing| reopen["client.open(name, revision=None)"]:::http
    create --> experiment["Experiment revision"]:::local
    reopen --> experiment
    experiment -->|"Journey A"| download["experiment.download(folder)"]:::disk
    experiment -->|"Journey B"| schedule["experiment.schedule(...)"]:::http
    schedule --> checkout["experiment.checkout(subject, session, folder)"]:::http
    checkout --> folder[("BIDS folder")]:::disk
    download --> folder
    subgraph offline["Offline: the whole run"]
        reopenSession["dbstim.session(folder)"]:::local --> run["with session.run(n) as run"]:::disk
        run --> trial["for trial in run"]:::local
        trial --> started["run.started(trial, onset)"]:::disk
        started --> present["play trial.file"]:::yours
        present --> ended["run.completed(trial, duration, result)<br/>or run.abandoned(trial)"]:::disk
        ended --> trial
    end
    folder --> reopenSession
    run -->|"leave the block"| events["events.tsv + .json"]:::disk
    events --> sync["session.sync()"]:::http
    classDef http fill:#e6eefb,stroke:#1f5fbf,color:#1d232b
    classDef local fill:#e7f1e9,stroke:#3f6b4a,color:#1d232b
    classDef disk fill:#f8eedb,stroke:#94600f,color:#1d232b
    classDef yours fill:#f1e8f8,stroke:#74419c,color:#1d232b
```

Journey A stops at the folder: videos, `manifest.json` and a PGL-loadable
`manifest.csv` under `stimuli/`. Journey B checks out one subject session to
this computer, records runs with no network, and syncs afterwards.

## Trial outcomes

```mermaid
stateDiagram-v2
    direction LR
    [*] --> Started: run.started
    Started --> Completed: run.completed
    Started --> Abandoned: run.abandoned
    Started --> Abandoned: crash, then reopen
    Completed --> [*]
    Abandoned --> [*]
    note right of Started
        Written to the journal before
        the call returns. Leaving the
        with block abandons it.
    end note
```

Neither ending is shown again. A run closes, and can sync, once every trial has
an outcome. `sync()` reports each run as `accepted`, `already synced`,
`kept as flagged attempt` or `waiting`, and raises `SyncError` at the end if the
server refused any.

## Recovery

- **Crash or power cut:** reopen the run on the same computer. The open trial
  becomes `abandoned` and the loop continues.
- **Dropped connection during sync:** run `sync()` again; the same record is
  stored once.
- **Another computer:** `checkout(..., takeover=True)`. The first computer's
  later syncs become flagged attempts.

dbstim does not render video, judge whether a trial was seen, or import PGL.

[API objects](api-classes.md) · [Data flow](server-to-local-flow.md) · [Recovery](recovery.md)
