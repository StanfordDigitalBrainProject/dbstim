"""Pure summaries of PGL timing arrays; no PGL or NumPy dependency."""
import math
from statistics import median


def frame_summary(presented_times, movie_times, duration):
    """Summarize PGL per-frame times (zero/nonfinite presentation times mean dropped).

    seconds_shown is elapsed presentation time between first and last shown frames.
    reached_end means the final shown movie time is within one median frame interval
    of duration. This is evidence for analysis, never a completion decision for dbstim.
    """
    presented, movie = list(presented_times), list(movie_times)
    if len(presented) != len(movie):
        raise ValueError('Timing arrays must have the same length')
    if type(duration) not in (int, float) or not math.isfinite(duration) or duration <= 0:
        raise ValueError('Movie duration must be finite and positive')
    try:
        presented, movie = [float(t) for t in presented], [float(t) for t in movie]
    except (TypeError, ValueError) as error:
        raise ValueError('Timing arrays must contain numbers') from error
    shown = [i for i, t in enumerate(presented) if math.isfinite(t) and t > 0]
    steps = [b - a for a, b in zip(movie, movie[1:]) if math.isfinite(a) and math.isfinite(b) and b > a]
    interval = median(steps) if steps else 0
    seconds = max(0, presented[shown[-1]] - presented[shown[0]]) if shown else 0
    end = movie[shown[-1]] if shown else float('-inf')
    return dict(frames_shown=len(shown), frames_dropped=len(presented) - len(shown),
                seconds_shown=round(seconds, 12), reached_end=math.isfinite(end) and end + interval >= duration - 1e-9)
