"""Schema definition for SQLite records (design doc sections 5.2 and 5.3).

Column names correspond to QR payload keys. Capacity is in bytes, speed in MB/s, and times in
ISO 8601 (UTC), matching the units and formats of result.json. The version is managed with
PRAGMA user_version; later versions use only ALTER TABLE ... ADD COLUMN (columns are never
dropped or renamed).

v2 adds `mode` (inspection mode: 'scan_only' or 'full'; NULL on rows written by v1). CREATE_TABLE
stays the v1 statement; a fresh DB runs migration 1 then 2.
"""

SCHEMA_VERSION = 2
ALTER_TABLE_V2 = "ALTER TABLE inspections ADD COLUMN mode TEXT"

CREATE_TABLE = """
CREATE TABLE IF NOT EXISTS inspections (
    inspection_id          TEXT PRIMARY KEY,
    state                  TEXT NOT NULL CHECK (state IN ('started', 'completed')),
    verdict                TEXT,
    verdict_code           TEXT,
    serial                 TEXT NOT NULL,
    kernel_name            TEXT NOT NULL,
    port_path              TEXT,
    vendor                 TEXT,
    model                  TEXT,
    started_at             TEXT NOT NULL,
    started_compact        TEXT NOT NULL,
    finished_at            TEXT,
    elapsed_s              REAL,
    result_digest          TEXT,
    step_malware           TEXT,
    step_hash_inventory    TEXT,
    step_capacity          TEXT,
    step_speed             TEXT,
    step_unmount           TEXT,
    malware_count          INTEGER,
    clamav_db_version      TEXT,
    clamav_db_date         TEXT,
    clamav_scanned_files   INTEGER,
    clamav_scan_time_s     REAL,
    nominal_bytes          INTEGER,
    advertised_bytes       INTEGER,
    actual_bytes           INTEGER,
    advertised_over        INTEGER,
    capacity_kind          TEXT,
    counterfeit_reasons    TEXT,
    lost_ratio             REAL,
    probe_verdict_line     TEXT,
    link_speed_mbps        INTEGER,
    device_class           TEXT,
    device_class_basis     TEXT,
    read_mb_s              REAL,
    write_mb_s             REAL,
    threshold_read_mb_s    REAL,
    threshold_write_mb_s   REAL,
    speed_basis_read       TEXT,
    speed_basis_write      TEXT,
    speed_severity         TEXT,
    reason_codes           TEXT,
    reasons                TEXT,
    hash_inventory_digest  TEXT,
    hash_inventory_lines   INTEGER,
    output_dir             TEXT NOT NULL,
    warnings               TEXT,
    created_at             TEXT NOT NULL,
    updated_at             TEXT NOT NULL
)
"""

CREATE_INDEXES: tuple[str, ...] = (
    "CREATE INDEX IF NOT EXISTS idx_inspections_started ON inspections (started_at DESC)",
    "CREATE INDEX IF NOT EXISTS idx_inspections_serial ON inspections (serial, started_at DESC)",
    "CREATE INDEX IF NOT EXISTS idx_inspections_port ON inspections (port_path, started_at DESC)",
    "CREATE INDEX IF NOT EXISTS idx_inspections_state ON inspections (state)",
    "CREATE INDEX IF NOT EXISTS idx_inspections_digest ON inspections (result_digest)",
)

# (version, SQL statements to upgrade to that version). Applied in order to DBs whose
# user_version is lower.
MIGRATIONS: tuple[tuple[int, tuple[str, ...]], ...] = (
    (1, (CREATE_TABLE, *CREATE_INDEXES)),
    (2, (ALTER_TABLE_V2,)),
)
