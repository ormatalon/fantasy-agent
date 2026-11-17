-- Sleeper's transaction "settings" object (waiver seq; a FAAB bid would be here, not yet seen in a FAAB league).
alter table transaction_log add column settings text;
