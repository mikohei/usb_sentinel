"""row_from_result(). Verify column values come from the same values in result.json (to_json)."""

from __future__ import annotations

import json
from pathlib import Path

from tests.test_store import _completed, _started
from usb_sentinel.libs import StepResult, StepStatus
from usb_sentinel.libs.report.json_report import digest, to_json
from usb_sentinel.libs.store import row_from_result


class TestRowFromResult:
    def test_columns_match_result_json(self, tmp_path: Path) -> None:
        """Column values come from the same values in result.json (to_json)."""
        result = _completed(tmp_path)
        payload = json.loads(to_json(result))
        steps = {s["name"]: s for s in payload["steps"]}

        row = row_from_result(result, digest(result))

        assert row["verdict"] == payload["verdict"]
        assert row["started_at"] == payload["started_at"]
        assert row["finished_at"] == payload["finished_at"]
        assert row["elapsed_s"] == payload["elapsed_s"]
        assert row["serial"] == payload["device"]["serial"]
        assert row["port_path"] == payload["device"]["port_path"]
        assert row["link_speed_mbps"] == payload["device"]["link_speed_mbps"]
        assert row["advertised_bytes"] == payload["device"]["advertised_bytes"]
        assert row["nominal_bytes"] == payload["advertised_check"]["nominal_bytes"]
        assert row["advertised_over"] == int(payload["advertised_check"]["exceeded"])
        assert row["step_capacity"] == steps["capacity"]["status"]
        assert row["actual_bytes"] == steps["capacity"]["details"]["actual_bytes"]
        assert row["capacity_kind"] == steps["capacity"]["details"]["kind"]
        assert row["read_mb_s"] == steps["speed"]["details"]["read"]["mb_s"]
        assert row["speed_severity"] == steps["speed"]["details"]["severity"]
        assert row["clamav_db_date"] == steps["malware"]["details"]["database"]["db_date"][:10]
        assert row["hash_inventory_lines"] == steps["hash_inventory"]["details"]["lines"]
        assert json.loads(row["reason_codes"]) == payload["reason_codes"]
        assert json.loads(row["reasons"]) == payload["reasons"]
        assert row["output_dir"] == payload["output_dir"]
        assert row["inspection_id"] == Path(payload["output_dir"]).name
        assert row["result_digest"] == digest(result)

    def test_missing_steps_and_keys_become_none(self, tmp_path: Path) -> None:
        """Fill with None when a step is missing or details lacks a key (ERROR path)."""
        result = _started(tmp_path)
        result.advertised_check = {}
        result.steps = [StepResult("malware", StepStatus.ERROR, "Mount failed: x")]

        row = row_from_result(result, None)

        assert row["step_malware"] == "error"
        assert row["step_capacity"] is None
        assert row["malware_count"] == 0
        assert row["clamav_db_date"] is None
        assert row["nominal_bytes"] is None
        assert row["advertised_over"] == 0
        assert json.loads(row["counterfeit_reasons"]) == []
        assert row["read_mb_s"] is None
        assert row["verdict_code"] == "I"
        assert row["finished_at"] is None
