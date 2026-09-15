"""Apply a league's actual scoring settings to a raw per-category stat line.

Using this instead of picking among preset PPR/half-PPR/standard totals
means custom league scoring (bonus thresholds aside) is honored exactly,
not approximated.
"""


def score_stats(stats: dict[str, float], scoring_settings: dict[str, float]) -> float:
    """`stats` and `scoring_settings` both use Sleeper's stat-key naming
    (e.g. "pass_yd", "rec", "rush_td"), so no key translation is needed for
    the Sleeper source. Other sources translate their own stat columns into
    this same key space before calling this function.
    """
    return sum(stats.get(key, 0) * multiplier for key, multiplier in scoring_settings.items())
