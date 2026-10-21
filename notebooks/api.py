import marimo

__generated_with = '0.25.2'
app = marimo.App(width='medium')


@app.cell
def _():
    import marimo as mo
    from pathlib import Path
    from dbstim import Client, MetricFilter
    return Client, MetricFilter, Path, mo


@app.cell
def _(mo):
    heading = mo.md('''# dbstim
    **Client → Experiment → Subject → Session → Run → Trial**

    Choose stimuli here; present them in your task program.
    Requires the updated session API on the server.
    ''')
    credentials = mo.ui.dictionary({
        'url': mo.ui.text(value='https://dbstimuli.com', label='Website'),
        'username': mo.ui.text(label='Username'),
        'password': mo.ui.text(kind='password', label='Password'),
    }).form(submit_button_label='Connect')
    mo.vstack([heading, credentials])
    return (credentials,)


@app.cell
def _(Client, credentials, mo):
    mo.stop(credentials.value is None)
    client = Client(**credentials.value)
    mo.md(f'Connected as **{client.username}**')
    return (client,)


@app.cell
def _(client, mo):
    mo.ui.table(client.metrics()['metrics'], selection=None)
    return


@app.cell
def _(client, mo):
    available = client.experiments()
    choices = {'Create a small demo': 'new'}
    choices.update({f"{row['name']} · {row['id'][:8]}": row.get('latest_published_revision_id') or row['id']
                    for row in available})
    choice = mo.ui.dropdown(choices, label='Experiment', value='Create a small demo')
    open_experiment = mo.ui.run_button(label='Open / create experiment')
    mo.hstack([choice, open_experiment])
    return choice, open_experiment


@app.cell
def _(MetricFilter, choice, client, mo, open_experiment):
    mo.stop(not open_experiment.value)
    if choice.value == 'new':
        experiment = client.create_experiment(
            'dbstim demo', subjects=2, sessions_per_subject=1, runs_per_session=2,
            trials_per_run=12, foils_per_run=2, shared_fraction=0.1,
            seed='demo-1', sampling='random',
            filters=[MetricFilter('duration_seconds', 'gte', 10), MetricFilter('duration_seconds', 'lte', 20)],
        )
    else:
        experiment = client.experiment(id=choice.value)
    experiment.summary()
    return (experiment,)


@app.cell
def _(experiment, mo):
    summary = experiment.summary()
    publish = mo.ui.run_button(label='Publish this revision', disabled=summary['published'])
    mo.vstack([mo.md('Publishing freezes the assigned videos and trial order.'), publish])
    return publish, summary


@app.cell
def _(experiment, mo, publish, summary):
    if publish.value:
        experiment.publish()
    mo.stop(not summary['published'] and not publish.value)
    subject_choice = mo.ui.dropdown(summary['subject_ids'], value=summary['subject_ids'][0], label='Subject')
    subject_choice
    return (subject_choice,)


@app.cell
def _(experiment, mo, subject_choice):
    subject = experiment.subject(subject_choice.value)
    session_choice = mo.ui.dropdown(list(subject.session_ids), value=subject.session_ids[0], label='Session')
    session_choice
    return session_choice, subject


@app.cell
def _(mo, session_choice, subject):
    session = subject.session(session_choice.value)
    run_choice = mo.ui.dropdown(['latest', *session.run_ids], value='latest', label='Run')
    download = mo.ui.run_button(label='Download this session')
    mo.hstack([run_choice, download])
    return download, run_choice, session


@app.cell
def _(run_choice, session):
    run = session.run(run_choice.value)
    run.trials
    return (run,)


@app.cell
def _(Path, download, experiment, mo, run, session, subject):
    mo.stop(not download.value)
    directory = session.download(Path('downloads') / experiment.id / subject.id / session.id)
    trial = run.next_trial()
    mo.stop(trial is None, mo.md('This run is complete. Choose another run explicitly.'))
    mo.vstack([mo.md(f'**{trial.trial_id}** · {trial.role}'), mo.video(str(run.media(trial)))])
    return directory, trial


@app.cell
def _(mo):
    mo.md(r"""
    ## In your presentation program

    ```python
    directory = Path("downloads") / experiment.id / subject.id / "session-001"
    session = subject.session("session-001", directory=directory)
    session.start()
    run = session.run("latest")
    trial = run.next_trial()
    if trial is not None:
        path = run.media(trial)  # or mode="cache" / mode="remote"
        run.record(trial, "started")
        # Present path with PGL, PsychoPy, or another player.
        # Only after successful presentation:
        run.record(trial, "completed", result={"response": "seen"})
    session.finish("interrupted")  # "completed" requires all session trials.
    ```

    Previews above do not record presentations. If presentation fails, stop and
    finish with `"failed"` or `"interrupted"`; an unconfirmed trial requires review.
    Reopening the session on another computer reads server progress.
    Use your downloaded session's directory; `run.results` retrieves saved responses.
    """)
    return


if __name__ == '__main__':
    app.run()
