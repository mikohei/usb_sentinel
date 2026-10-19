"""SQLite記録のスキーマ定義（設計書第5.2節・第5.3節）。

列名はQRペイロードのキーに対応させる。容量はバイト、速度はMB/s、時刻はISO 8601（UTC）で、
result.jsonと同じ単位・形式にする。版はPRAGMA user_versionで管理し、以降の版では
ALTER TABLE ... ADD COLUMNだけを使う（列の削除・改名はしない）。
"""

SCHEMA_VERSION = 1

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

# (版, その版に上げるためのSQL文の並び)。user_versionが小さいDBには順に適用する。
MIGRATIONS: tuple[tuple[int, tuple[str, ...]], ...] = ((1, (CREATE_TABLE, *CREATE_INDEXES)),)
