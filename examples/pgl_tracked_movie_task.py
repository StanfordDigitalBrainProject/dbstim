"""Record a dbstim run from a PGL movie task.

This is example user code, not part of the dbstim package: dbstim never imports
PGL. Copy it into your task and adapt it. PGL owns the loop, the window, timing
and responses; the task's hooks tell dbstim what happened to each trial.

Three steps, each a command:

    # 1. Online, before the session: reserve subject 1, session 1 for this
    #    computer and download its videos and run plans.
    python pgl_tracked_movie_task.py checkout "Clips" 1 1 sub01_ses01

    # 2. Offline, on the presentation Mac: run 1. No website traffic until it ends.
    python pgl_tracked_movie_task.py run sub01_ses01 1

    # 3. Online, afterwards: upload every finished run.
    python pgl_tracked_movie_task.py sync sub01_ses01

Step 1 assumes the experiment already has a schedule, for example from
``client.open("Clips").schedule(subjects=5, runs=2, trials_per_run=4)``, which
assigns all 40 videos of the README's "Clips" selection.

Running step 2 again after a crash or Escape continues with the trials that
have no outcome yet. A trial that started without an ending, because the
program died or raised, for example on a movie that failed to load, is
recorded abandoned when the run block exits or the run is reopened; it is not
shown again. The run syncs once every trial has an outcome.

The PGL calls follow justingardner/pgl at e830691 (``pglTask`` hooks,
``pgl.movie(...).play()`` and the movie's ``presentedTimes``, ``movieTimes`` and
``duration``). This example has not yet been run with PGL; it is validated in
a hands-on Mac session after dbstim 1.0. Check the hook names against your PGL
version.
"""
import argparse

import dbstim

try:
    from pgl import pgl as PGL, pglExperiment, pglTask
except ImportError:  # checkout and sync work without PGL installed
    PGL = pglExperiment = None
    pglTask = object


class TrackedMovieTask(pglTask):
    """Play one dbstim trial per PGL trial: segment 0 loads the movie, segment 1 plays it."""

    def __init__(self, pgl, run):
        super().__init__(pgl)
        self.run = run
        self.trials = list(run)  # trials with no outcome yet, in planned order
        self.settings.taskName = 'dbstim movies'
        self.settings.nTrials = len(self.trials)
        self.settings.seglen = [0.5, float('inf')]

    def startTask(self, startTime):
        self.t0 = startTime  # this run's time zero: onsets are seconds after it
        return True

    def startSegment(self, startTime):
        super().startSegment(startTime)
        if self.state.currentSegment == 0:
            self.trial = self.trials[self.state.currentTrial]
            self.movie = self.pgl.movie(str(self.trial.file), displayWidth=30)
        elif self.state.currentSegment == 1:
            self.run.started(self.trial, onset=startTime - self.t0)
            self.movie.play()  # blocks until the movie ends or Escape
            summary = dbstim.frame_summary(self.movie.presentedTimes, self.movie.movieTimes,
                                           self.movie.duration)
            if summary['reached_end']:
                self.run.completed(self.trial, duration=summary['seconds_shown'], result=summary)
            else:
                self.run.abandoned(self.trial, duration=summary['seconds_shown'])
            self.jumpSegment()


def checkout(name, subject, session, folder):
    """Online: reserve the subject session for this computer and download it."""
    with dbstim.connect() as client:
        checked = client.open(name).checkout(subject=subject, session=session, folder=folder)
    print(f'Checked out subject {checked.subject}, session {checked.session}, runs {checked.runs} '
          f'to {checked.folder}')


def run(folder, number):
    """Offline: present one run with PGL. The whole run is the protected window."""
    if PGL is None:
        raise SystemExit('This step needs PGL: https://github.com/justingardner/pgl')
    session = dbstim.session(folder)
    with session.run(number) as tracked:
        if not list(tracked):
            print(f'Run {number} has no trials left to show.')
            return
        pgl = PGL()
        experiment = pglExperiment(pgl, subjectID=f'sub-{session.subject:02d}', experimentName='dbstim',
                                   sessionName=f'ses-{session.session:02d}', runName=f'run-{number:02d}')
        experiment.addTask(TrackedMovieTask(pgl, tracked))
        experiment.initScreen()
        experiment.run()  # PGL owns the loop and saves its own data
    # Leaving the block wrote sub-XX/ses-XX/beh/..._run-XX_events.tsv from the journal.
    print('Events written under', session.folder)


def sync(folder):
    """Online: upload each finished run once."""
    for number, outcome in dbstim.session(folder).sync().items():
        print(f'run {number}: {outcome}')


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    steps = parser.add_subparsers(dest='step', required=True)
    step = steps.add_parser('checkout', help='online: reserve and download a subject session')
    step.add_argument('experiment')
    step.add_argument('subject', type=int)
    step.add_argument('session', type=int)
    step.add_argument('folder')
    step = steps.add_parser('run', help='offline: present one run with PGL')
    step.add_argument('folder')
    step.add_argument('number', type=int)
    step = steps.add_parser('sync', help='online: upload finished runs')
    step.add_argument('folder')
    args = parser.parse_args(argv)
    if args.step == 'checkout':
        checkout(args.experiment, args.subject, args.session, args.folder)
    elif args.step == 'run':
        run(args.folder, args.number)
    else:
        sync(args.folder)


if __name__ == '__main__':
    main()
