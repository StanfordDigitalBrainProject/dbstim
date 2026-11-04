# Recovering a run

Each run records to a journal in the checked-out folder,
`stimuli/.dbstim/run-001.jsonl`. Every `started`, `completed` or `abandoned`
call is forced to disk before it returns. The BIDS events file is rebuilt
from the journal.

## A program stopped mid-run

Open the same run again, on the same computer:

```python
session = dbstim.session("sub01_ses01")
with session.run(1) as run:
    for trial in run:   # continues after the last trial with an outcome
        ...
```

- A trial that was started with no ending is recorded `abandoned`, with
  duration `n/a`. It is never offered again.
- A half-written last line from a crash is dropped. A complete line that
  cannot be read raises an error instead of being discarded.
- Each reopening starts a new *part*, numbered in the events file's `part`
  column, so onsets from a restarted clock stay interpretable.
- If a disk write fails, that run refuses further calls; leave the block and
  open it again.

Only one program can have a session open; a second recorder or sync raises
`RuntimeError`. The lock ends when the program exits, even by crashing.

## Ending a run early

A run syncs only once every trial has an outcome. Until then `sync()` reports
it as `waiting`, and you can open it later to show the rest. To close a run you
will not finish, start and abandon each remaining trial.

## Moving to another computer

The checkout reserves the session for one computer. Another computer's
checkout fails with `ApiError` (HTTP 409) until it asks for the session
explicitly:

```python
experiment.checkout(subject=1, session=1, folder="sub01_ses01", takeover=True)
```

An unfinished run belongs to the computer that started it and cannot be
resumed elsewhere. After a takeover:

- Runs the first computer syncs are `kept as flagged attempt`. The server
  stores them separately, and their events move to
  `sourcedata/attempt-<id>/` in that folder.
- If the first computer synced a run before the takeover, the new computer's
  copy of that run is refused (see below).

## Sync

`session.sync()` uses the checkout's client, or `sync(client=client)`; a
reopened session prompts for the password when needed. It uploads each closed
run once and returns an outcome for every run:

| Outcome | Meaning |
| --- | --- |
| `accepted` | stored on the server now |
| `already synced` | stored earlier, including when the reply was lost |
| `kept as flagged attempt` | stored as a flagged attempt after a takeover |
| `waiting` | not closed yet; nothing sent |

After a lost connection (`ApiError`), run `sync()` again; the server stores
the resent record once. A refused run does not stop the others:
after trying them all, `sync()` raises `SyncError`, whose
`refused` maps run numbers to errors and `outcomes` holds the rest. A changed
record for a stored run is always refused.

## Upgrading from 0.5

0.5 sessions recorded progress on the server, and 1.0 cannot continue them.
Finish them with `v0.5.0`, which stays installable. Delete the unused 0.5
cache, `~/.cache/dbstim`, when convenient.
