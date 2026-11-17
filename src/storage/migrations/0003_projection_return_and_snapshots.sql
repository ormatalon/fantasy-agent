-- Return week for IR players (data-contract section 7).
alter table projection add column return_week integer;
alter table projection add column return_date text;
alter table projection add column return_date_source text;
alter table projection add column return_date_as_of text;

-- Every Sleeper projection pull, kept as it happens: the point-in-time rows a rest-of-season backtest needs
-- (the old pulls Sleeper serves are stamped after the games). week 0 = the season-total projection.
-- Only players with a non-empty projection; a pull that equals the player's latest stored one is not stored again.
create table projection_snapshot(season integer, week integer, player_id text, pulled_at text not null,
  stats text not null, source_modified text,
  primary key (season, week, player_id, pulled_at));
