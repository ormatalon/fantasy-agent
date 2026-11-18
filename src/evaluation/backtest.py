"""Weekly and rest-of-season backtests of the projection blend (docs/data-contract.md section 10).

    uv run python -m evaluation.backtest             both seasons, both kinds; writes results/<run_id>.json
    uv run python -m evaluation.backtest --sweep     tune n_games and weights on 2024 only
    uv run python -m evaluation.backtest --fit-cv    fit the variance floors on 2024 only
    uv run python -m evaluation.backtest --leakage   checks on the stored Sleeper rows

Ground truth is Sleeper /stats scored with REFERENCE_PPR for QB/RB/WR/TE. Sleeper ids on both sides, so the
crosswalk is not in the ground truth; it only gates the nflverse source.
"""

import argparse
import json
import math
import subprocess
from datetime import UTC, datetime
from pathlib import Path
from statistics import mean

from evaluation.metrics import calibration, paired_abs_diff, summarize
from projections import sleeper_data
from projections.blend import DEFAULT_WEIGHTS, blend
from projections.scoring import REFERENCE_PPR, score
from projections.sources import NflverseSource, SleeperSource

SKILL = {"QB", "RB", "WR", "TE"}
SEASONS = (2024, 2025)
WEEKLY_WEEKS = range(1, 18)  # weeks 3-17 are reported too: before cross-season history, weeks 1-2 had none
ROS_ORIGINS = (3, 6, 9, 12)
MIN_WEEKLY_PROJECTION = 3.0  # Sleeper reference points: who counts as a projected player
RESULTS = Path(__file__).parent / "results"


class Season:
    """Everything a season's backtest reads, loaded once."""

    def __init__(self, season: int):
        sleeper_data.prefetch(season, range(1, 19))
        self.season = season
        self.proj = {w: sleeper_data.weekly_projections(season, w) for w in range(1, 19)}
        self.ref = {(r["player_id"], w): score(r["stats"], REFERENCE_PPR) for w, rows in self.proj.items() for r in rows}
        self.actual = {
            (r["player_id"], w): score(r["stats"], REFERENCE_PPR)
            for w in range(1, 19)
            for r in sleeper_data.weekly_stats(season, w)
        }
        self.season_proj = {r["player_id"]: score(r["stats"], REFERENCE_PPR) for r in sleeper_data.season_projections(season)}
        self.position = {r["player_id"]: r["position"] for rows in self.proj.values() for r in rows}
        self.plays = {w: {r["nfl_team"] for r in rows} for w, rows in self.proj.items()}  # Sleeper team codes
        self.nflverse = NflverseSource.for_season(season)
        self.sleeper = SleeperSource(lambda _season, w: self.proj[w])

    def blend(self, nflverse: NflverseSource, weeks, weights: dict) -> dict:
        rows = {"sleeper": self.sleeper.weekly(self.season, list(weeks)), "nflverse": nflverse.weekly(self.season, list(weeks))}
        return blend(rows, weights, REFERENCE_PPR, self.position)


def weekly_predictions(season: Season, weights: dict, n_games: int | None = None, prior_weight: float | None = None) -> list[dict]:
    nflverse = season.nflverse.with_options(n_games, prior_weight)  # weekly() cuts the history at each target week itself
    blended = season.blend(nflverse, WEEKLY_WEEKS, weights)
    out = []
    for w in WEEKLY_WEEKS:
        for r in season.proj[w]:
            key = (r["player_id"], w)
            if r["position"] not in SKILL or season.ref[key] < MIN_WEEKLY_PROJECTION:
                continue
            b = blended[key]
            nfl = next((p["mean"] for p in b.sources if p["source"] == "nflverse"), None)
            out.append(
                {"player_id": r["player_id"], "week": w, "position": r["position"],
                 "actual": season.actual.get(key, 0.0), "sleeper": season.ref[key], "nflverse": nfl,
                 "blend": b.mean, "variance": b.variance, "has_nflverse": nfl is not None}
            )  # fmt: skip
    return out


def tier(name: str, rows: list[dict], pred: str, base: str | None) -> dict:
    result = {"tier": name, **summarize([(r[pred], r["actual"]) for r in rows])}
    result["by_position"] = {
        pos: summarize([(r[pred], r["actual"]) for r in rows if r["position"] == pos]) for pos in sorted({r["position"] for r in rows})
    }
    if base and base != pred:
        base_mae = summarize([(r[base], r["actual"]) for r in rows])["mae"]
        result["mae_vs_baseline_pct"] = 100 * (result["mae"] / base_mae - 1)
        result["paired_diff"] = paired_abs_diff([r[pred] for r in rows], [r[base] for r in rows], [r["actual"] for r in rows])
    return result


def weekly_run(season: Season, weights: dict) -> dict:
    rows = weekly_predictions(season, weights)
    rows3 = [r for r in rows if r["week"] >= 3]
    covered = [r for r in rows if r["has_nflverse"]]
    tiers = [
        tier("sleeper_only", rows, "sleeper", None),
        tier("blend", rows, "blend", "sleeper"),
        tier("sleeper_only_w3_17", rows3, "sleeper", None),
        tier("blend_w3_17", rows3, "blend", "sleeper"),
        tier("sleeper_only_where_nflverse", covered, "sleeper", None),
        tier("nflverse_only_where_present", covered, "nflverse", "sleeper"),
    ]
    gates = {}
    for key, t in (("", tiers[1]), ("_w3_17", tiers[3])):
        gates[f"beats_baseline{key}"] = t["mae_vs_baseline_pct"] < 0  # AGENT.md rule: lower MAE
        gates[f"significant{key}"] = t["paired_diff"]["ci95"][1] < 0  # the paired 95% interval excludes 0
    return {
        "kind": "weekly", "season": season.season, "weights": weights, "matchup_adjustment": False,
        "tiers": tiers, "nflverse_coverage": len(covered) / len(rows),
        "calibration": calibration([{**r, "pred": r["blend"]} for r in rows]),
        "gate": {"baseline": "sleeper_only", "candidate": "blend", **gates},
    }  # fmt: skip


def ros_predictions(season: Season, weights: dict) -> list[dict]:
    out = []
    for o in ROS_ORIGINS:
        weeks = range(o, 19)
        blended = season.blend(season.nflverse.with_options(cutoff=o), weeks, weights)  # weekly blend rows o..18, history before o
        for r in season.proj[o]:
            pid = r["player_id"]
            if r["position"] not in SKILL or pid not in season.season_proj or season.ref[(pid, o)] < MIN_WEEKLY_PROJECTION:
                continue  # who is projected is decided from the origin week only, never from later rows
            games_left = sum(r["nfl_team"] in season.plays[w] for w in weeks)
            out.append(
                {"player_id": pid, "origin": o, "position": r["position"],
                 "actual": sum(season.actual.get((pid, w), 0.0) for w in weeks),
                 "sleeper_season_prorated": season.season_proj[pid] * (19 - o) / 18,
                 "sleeper_weekly_sum": sum(season.ref.get((pid, w), 0.0) for w in weeks),
                 "blend_weekly_sum": sum(blended[(pid, w)].mean for w in weeks if (pid, w) in blended),
                 "sleeper_frozen": season.ref[(pid, o)] * games_left,
                 "blend_frozen": blended[(pid, o)].mean * games_left,
                 "variance": sum(blended[(pid, w)].variance for w in weeks if (pid, w) in blended)}
            )  # fmt: skip
    return out


ROS_TIERS = ["sleeper_season_prorated", "sleeper_weekly_sum", "blend_weekly_sum", "sleeper_frozen", "blend_frozen"]


def ros_run(season: Season, weights: dict) -> dict:
    rows = ros_predictions(season, weights)
    base = ROS_TIERS[0]
    tiers = [tier(n, rows, n, base) for n in ROS_TIERS]
    by_name = {t["tier"]: t for t in tiers}
    return {
        "kind": "ros", "season": season.season, "weights": weights, "matchup_adjustment": False,
        "tiers": tiers, "origins": list(ROS_ORIGINS),
        "mae_by_origin": {
            o: {n: summarize([(r[n], r["actual"]) for r in rows if r["origin"] == o])["mae"] for n in ROS_TIERS} for o in ROS_ORIGINS
        },
        "calibration": calibration([{**r, "pred": r["blend_weekly_sum"]} for r in rows]),
        "gate": {
            "baseline": base, "candidate": "blend_weekly_sum",
            "beats_baseline": by_name["blend_weekly_sum"]["mae_vs_baseline_pct"] < 0,
            "leak_free_pair": {
                "baseline": "sleeper_frozen", "candidate": "blend_frozen",
                "beats_baseline": by_name["blend_frozen"]["mae"] < by_name["sleeper_frozen"]["mae"],
            },
        },
        "leakage": "The season-projection baseline and the weekly-row sums use Sleeper snapshots stamped after the games "
                   "(docs/spikes.md O1), so they can carry knowledge of later injuries. The frozen tiers use only the origin week's row.",
    }  # fmt: skip


def sweep() -> None:
    s = Season(2024)
    sleeper = summarize([(r["sleeper"], r["actual"]) for r in weekly_predictions(s, DEFAULT_WEIGHTS)])["mae"]
    print(f"2024 weekly 1-17, sleeper-only MAE {sleeper:.3f}; rows: n_games prior_weight, then the blend MAE at nflverse weight 0.05 0.1 0.15 0.2 0.3")
    for n in (4, 6, 8):
        for pw in (1.0, 0.5):
            maes = [summarize([(r["blend"], r["actual"]) for r in weekly_predictions(s, {"sleeper": 1 - w, "nflverse": w}, n, pw)])["mae"] for w in (0.05, 0.1, 0.15, 0.2, 0.3)]
            print(n, pw, " ".join(f"{m:.3f}" for m in maes))


def fit_cv() -> None:
    """Position floors: sqrt(sum err^2 / sum pred^2) of the Sleeper-only forecast, 2024."""
    rows = weekly_predictions(Season(2024), DEFAULT_WEIGHTS)

    def cv(sub):
        return round(math.sqrt(sum((r["sleeper"] - r["actual"]) ** 2 for r in sub) / sum(r["sleeper"] ** 2 for r in sub)), 2)

    print({**{p: cv([r for r in rows if r["position"] == p]) for p in sorted(SKILL)}, "default": cv(rows)})


def leakage() -> None:
    for y in SEASONS:
        s = Season(y)
        lags = sorted(
            (datetime.fromtimestamp(r["last_modified"] / 1000, UTC) - datetime.fromisoformat(r["date"]).replace(tzinfo=UTC)).days
            for w in range(1, 19)
            for r in s.proj[w]
            if r["date"] and r["last_modified"]
        )
        dnp = [(r["player_id"], w) not in s.actual for w in WEEKLY_WEEKS for r in s.proj[w]
               if r["position"] in SKILL and s.ref[(r["player_id"], w)] >= MIN_WEEKLY_PROJECTION]  # fmt: skip
        print(f"{y}: stamp minus game date, days: min {lags[0]}, median {lags[len(lags) // 2]}, max {lags[-1]}")
        print(f"{y}: projected >= {MIN_WEEKLY_PROJECTION} points but no stats row (did not play): {mean(dnp):.1%} of {len(dnp)}")
        by_origin = ros_run(s, DEFAULT_WEIGHTS)["mae_by_origin"]
        for o, d in by_origin.items():
            print(f"{y}: ROS MAE origin {o}:", {n: round(v, 2) for n, v in d.items()})


def git_sha() -> str:
    try:
        return subprocess.run(["git", "rev-parse", "--short", "HEAD"], capture_output=True, text=True, check=True).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        return "unknown"


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--sweep", action="store_true")
    parser.add_argument("--fit-cv", action="store_true")
    parser.add_argument("--leakage", action="store_true")
    args = parser.parse_args()
    if args.sweep:
        return sweep()
    if args.fit_cv:
        return fit_cv()
    if args.leakage:
        return leakage()
    runs = []
    for y in SEASONS:
        s = Season(y)
        runs += [weekly_run(s, DEFAULT_WEIGHTS), ros_run(s, DEFAULT_WEIGHTS)]
    run_id = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    RESULTS.mkdir(exist_ok=True)
    path = RESULTS / f"{run_id}.json"
    path.write_text(json.dumps({"run_id": run_id, "git_sha": git_sha(), "runs": runs}, indent=1), encoding="utf-8")
    print(f"run {run_id} -> {path}")
    print(f"{'season':6} {'kind':7} {'tier':30} {'n':>6} {'mae':>6} {'rmse':>6} {'bias':>6} {'vs base':>8}")
    for r in runs:
        for t in r["tiers"]:
            pct = f"{t['mae_vs_baseline_pct']:+.1f}%" if "mae_vs_baseline_pct" in t else ""
            print(f"{r['season']:<6} {r['kind']:7} {t['tier']:30} {t['n']:>6} {t['mae']:>6.2f} {t['rmse']:>6.2f} {t['bias']:>+6.2f} {pct:>8}")
        print(f"       gate: {r['gate']}")


if __name__ == "__main__":
    main()
