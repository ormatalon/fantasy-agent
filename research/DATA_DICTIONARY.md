# Player-game dataset

Built by `build_dataset.py`. One row = one QB/RB/WR/TE in one regular-season week, if he was on his team's
53-man roster (nflverse weekly rosters, status `ACT` or `INA`), his team played, he played in one of the team's
last 4 games or was preseason-ranked, and he was not listed Out. Key: `player_id, season, week`.

**No weekly (per-game) projection from any platform is a feature.** Sleeper's weekly projection is stored only in
`data/benchmark_sleeper.parquet` (same rows, plus `played`) for scoring the benchmark.

- `data/player_games_train.parquet`: 2022-2025
- `data/player_games_test.parquet`: 2026 weeks 1-7 (out-of-time test)

All columns except `act` are known before kickoff.

| Column | Meaning |
|---|---|
| `player_id`, `gsis_id`, `name` | Sleeper id, NFL id, name |
| `season`, `week`, `kickoff` | Game time (kickoff in US Eastern, no timezone) |
| `team`, `opponent`, `position` | Point-in-time team, opponent and roster position (nflverse abbreviations) |
| `act` | **Target.** Actual PPR points scored from the stat line; 0 if the player did not play |
| `home`, `div_game` | 1 if home team / division game |
| `team_spread` | Vegas spread from the player's team's side; > 0 means favored |
| `total_line`, `implied_total` | Vegas over/under; implied team points = (total + spread) / 2 |
| `roof`, `rest` | Stadium roof type; days since the team's previous game |
| `age`, `years_exp` | Age at kickoff; seasons since draft year (0 = rookie) |
| `report_status`, `practice_status` | Official injury report for that week; missing when not listed |
| `*_l3`, `*_l8` | Mean over the player's previous 3 / 8 played games (any season): `pts`, `snap_share`, `tgt_share`, `rec_tgt`, `rush_att`, `pass_att`, `rz_opps` (red-zone targets + carries), `rec_air_yd` |
| `pts_std_l8`, `pts_max_l8` | Std and max of points over the previous 8 played games |
| `pts_season_avg`, `games_season` | Mean points and games played earlier this season |
| `pts_prev_season`, `games_prev_season` | Mean points and games played last season |
| `weeks_since_played` | Weeks since his last played game (1 = played last week; byes count) |
| `xfp_l3`, `xfp_l8` | Mean expected PPR points of his previous 3 / 8 games (nflverse ff_opportunity: what his usage was worth) |
| `fp_over_xfp_l8` | Mean actual minus expected points over the previous 8 games (efficiency) |
| `opp_pts_allowed_rel_l8` | Points the opponent's defense allowed to this position per game, trailing 8 games, minus the league average that week |
| `opp_pos_over_avg_l8` | Schedule-adjusted defense: points players at this position scored against the opponent above their own previous-8-game average, trailing 8 games |
| `opp_pass_epa_allowed_rel_l8`, `opp_rush_epa_allowed_rel_l8`, `opp_sack_rate_rel_l8`, `opp_points_allowed_rel_l8`, `opp_plays_allowed_rel_l8` | Opponent defense: EPA per dropback / per carry allowed, sack rate, points and plays allowed, trailing 8 games, relative to the league that week |
| `team_plays_rel_l8`, `team_pass_rate_rel_l8`, `team_pass_epa_rel_l8`, `team_rush_epa_rel_l8`, `team_points_rel_l8` | Own team offense: pace, pass rate, EPA per dropback / per carry, points, trailing 8 games, relative to the league |
| `qb_epa_db_rel_l8`, `qb_cpoe_rel_l8`, `qb_ppr_rel_l8` | The expected starting QB's EPA per dropback, CPOE and PPR points over his last 8 starts (10+ attempts), relative to the league; same value for every teammate |
| `qb_change`, `is_expected_qb` | 1 if the expected starter is not the QB who started the team's previous game; (QB rows) 1 if this player is the expected starter. Expected starter = last game's starter if available, else the available QB with the most recent usage |
| `team_pos_rank`, `team_pos_share` | Rank and share of recent expected points among available teammates at the position (available = on the roster, not Out/Doubtful) |
| `vacated_pts` | Recent points of teammates at the position who played for the team in its last 2 games but are unavailable this week (Out, Doubtful, IR, cut, traded) |
| `wopr_l8`, `air_yards_share_l8`, `rec_epa_tgt_l8`, `rush_epa_carry_l8` | Player efficiency, last 8 games (nflverse weekly stats): weighted opportunity, air-yards share, receiving EPA per target, rushing EPA per carry |
| `fp_ecr`, `fp_pos_rank` | FantasyPros' last preseason PPR consensus rank (overall / within position); frozen for the season |
| `sleeper_adp`, `sleeper_pos_adp` | Sleeper's PPR ADP as of week 1 (overall / within position); frozen for the season; missing = undrafted |

History starts in 2021. Weather is not included: nflverse fills it from post-game game books, so it isn't
available at prediction time. Sleeper's stored season projections are not used either: they are end-of-season
versions that already reflect injuries and breakouts.
