-- Schema from docs/data-contract.md section 1.

-- user data (kept across upgrades)
create table settings(key text primary key, value text not null);
create table chat_message(id integer primary key, league_id text not null,
  role text not null, content text not null, created_at text not null, meta text);
create index chat_message_league on chat_message(league_id, id);
create table roster_diff(id integer primary key, league_id text not null, roster_id integer not null,
  detected_at text not null, added text not null, dropped text not null);

-- league state (cache)
create table league(league_id text primary key, season integer not null, name text not null,
  status text not null, settings text not null, scoring_settings text not null,
  roster_positions text not null, format text not null, fetched_at text not null);
create table fantasy_team(league_id text, roster_id integer, owner_id text, name text not null,
  wins integer, losses integer, ties integer, points_for real, waiver_budget_used integer,
  fetched_at text not null, primary key (league_id, roster_id));
create table roster_player(league_id text, roster_id integer, player_id text,
  role text not null, slot_index integer,
  primary key (league_id, roster_id, player_id));
create index roster_player_player on roster_player(league_id, player_id);
create table matchup(league_id text, week integer, roster_id integer, matchup_id integer, points real,
  fetched_at text not null, primary key (league_id, week, roster_id));
create table matchup_player(league_id text, week integer, roster_id integer, player_id text,
  points real, is_starter integer not null, slot_index integer,
  primary key (league_id, week, roster_id, player_id));
create table transaction_log(league_id text, transaction_id text, week integer, type text, status text,
  created_ms integer, adds text, drops text, roster_ids text, waiver_budget text, draft_picks text,
  primary key (league_id, transaction_id));

-- players, games, NFL facts, actuals (cache)
create table player(player_id text primary key,
  full_name text, position text, fantasy_positions text, nfl_team text, status text, active integer,
  injury_status text, injury_body_part text, practice_participation text,
  depth_chart_position text, depth_chart_order integer, search_rank integer,
  gsis_id text, espn_id text, fetched_at text not null);
create table player_id_map(player_id text not null, source text not null,
  source_id text not null, method text not null, primary key (source, source_id));
create table nfl_team(abbr text primary key, name text, espn_id text, espn_abbr text,
  nflverse_code text, bye_week integer);
create table nfl_game(game_id text primary key, season integer, week integer, home text, away text,
  kickoff_utc text, status text not null, home_score integer, away_score integer,
  sleeper_game_id text, espn_event_id text, source text, as_of text, fetched_at text not null);
create index nfl_game_week on nfl_game(season, week);
create table nfl_roster(player_id text, source text, nfl_team text, roster_status text,
  as_of text, fetched_at text, primary key (player_id, source));
create table nfl_depth(source text, nfl_team text, unit text, slot_label text, rank integer,
  player_id text, as_of text, fetched_at text,
  primary key (source, nfl_team, slot_label, player_id));
create table nfl_injury(player_id text, season integer, week integer, source text,
  designation text, practice_status text, body_part text, detail text,
  return_date text,
  raw text, as_of text, fetched_at text, primary key (player_id, season, week, source));
create index nfl_injury_player on nfl_injury(player_id, season, week);
create table nfl_usage(player_id text, season integer, week integer, source text,
  offense_pct real, defense_pct real, st_pct real, targets integer, carries integer, target_share real,
  as_of text, fetched_at text, primary key (player_id, season, week, source));
create table nfl_inactive(game_id text, player_id text, source text, as_of text, fetched_at text,
  primary key (game_id, player_id, source));
create table news(source text, source_key text, player_id text, published_at text,
  title text, description text, analysis text, url text, fetched_at text,
  primary key (source, source_key));
create index news_player on news(player_id, published_at desc);
create table player_week_stat(season integer, week integer, player_id text, stats text not null,
  fetched_at text not null, primary key (season, week, player_id));
create table refresh_log(source text, scope text, last_attempt_at text, last_ok_at text, last_error text,
  coverage text, primary key (source, scope));

-- projection tables (cache)
create table projection(league_id text, season integer, player_id text, horizon text, week integer,
  mean real not null, variance real not null, n_games integer not null, zero_reason text,
  mean_if_healthy real, sources text not null, computed_at text not null,
  primary key (league_id, season, player_id, horizon, week));
create table projection_meta(league_id text primary key, scoring_hash text not null,
  unprojected_scoring_items text not null, weights text not null, computed_at text not null);
create table source_projection(source text, season integer, week integer, player_id text,
  stats text not null, points_variance real, fetched_at text not null,
  primary key (source, season, week, player_id));
