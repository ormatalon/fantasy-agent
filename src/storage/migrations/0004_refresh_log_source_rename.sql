delete from refresh_log where source = 'sleeper' and scope in ('catalog', 'stats', 'projections', 'projections_ros');
