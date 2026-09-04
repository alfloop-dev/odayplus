-- ODP-FCT-ROOT-CAUSE-CONTRACT-001: Disposition of FCT-004 root_cause contract
-- Mark core.work_orders.root_cause as RESERVED (unproduced by automated engine in current release).
-- Owner: ForecastOps / Platform Ops. Target Milestone: Wave 5+.

COMMENT ON COLUMN core.work_orders.root_cause IS 'RESERVED: No automated producer exists in current release (ODP-FR-FCT-004). Owner: ForecastOps/Platform; Target: Wave 5+';
