from .models import ResultDetail

# Statuses where the capacity step's kind, lost_ratio, counterfeit_reasons are read (else no kind).
CAPACITY_JUDGED_STATUSES = frozenset({"pass", "fail"})


def _int(value: object) -> int | None:
    """Return a non-bool int as-is, else None."""
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def _float(value: object) -> float | None:
    """Return a non-bool number as float, else None."""
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    return float(value)


def _str(value: object) -> str | None:
    """Return a str as-is, else None."""
    return value if isinstance(value, str) else None


def _str_items(value: object) -> tuple[str, ...] | None:
    """Return a list as a tuple of its str elements, else None."""
    if not isinstance(value, list):
        return None
    return tuple(item for item in value if isinstance(item, str))


def _dict(value: object) -> dict[str, object] | None:
    """Return a dict as a copy with stringified keys, else None."""
    if not isinstance(value, dict):
        return None
    return {str(key): item for key, item in value.items()}


def _step(steps: object, name: str) -> dict[str, object] | None:
    """Return the step dict whose name matches from steps, or None."""
    if not isinstance(steps, list):
        return None
    for step in steps:
        if isinstance(step, dict) and step.get("name") == name:
            return _dict(step)
    return None


def _details(steps: object, name: str) -> dict[str, object] | None:
    """The step's details dict. None if there is no step or details is not a dict."""
    step = _step(steps, name)
    return _dict(step.get("details")) if step is not None else None


def _status(steps: object, name: str) -> str | None:
    """The step's status string, or None."""
    step = _step(steps, name)
    return _str(step.get("status")) if step is not None else None


def _db_version(value: object) -> str | None:
    """Turn the ClamAV DB version into a str (backend writes a str, but an int is accepted)."""
    if isinstance(value, str):
        return value
    number = _int(value)
    return str(number) if number is not None else None


def parse_detail(data: dict[str, object]) -> ResultDetail:
    """Extract detail QR (v:3) inputs from the contents of result.json.

    Every value is type-checked with isinstance and set to None on mismatch. On the ERROR/SKIPPED
    paths details has no keys, so read with .get(). The capacity kind, lost_ratio, and
    counterfeit_reasons are read only when the step is PASS/FAIL; actual_bytes regardless of status.

    Args:
        data: result.json after json.loads() (dict).

    Returns:
        The extracted values. Missing values are None; reason_codes is an empty tuple.
    """
    steps = data.get("steps")
    device = _dict(data.get("device")) or {}
    advertised = _dict(data.get("advertised_check")) or {}
    nominal_bytes = _int(advertised.get("nominal_bytes"))
    advertised_over = None if nominal_bytes is None else bool(advertised.get("exceeded"))

    database = _dict((_details(steps, "malware") or {}).get("database")) or {}

    capacity = _details(steps, "capacity") or {}
    capacity_judged = _status(steps, "capacity") in CAPACITY_JUDGED_STATUSES

    speed = _details(steps, "speed") or {}
    read = _dict(speed.get("read")) or {}
    write = _dict(speed.get("write")) or {}

    inventory = _details(steps, "hash_inventory") or {}

    return ResultDetail(
        elapsed_s=_float(data.get("elapsed_s")),
        clamav_db_version=_db_version(database.get("db_version")),
        clamav_db_date=_str(database.get("db_date")),
        nominal_bytes=nominal_bytes,
        advertised_bytes=_int(device.get("advertised_bytes")),
        actual_bytes=_int(capacity.get("actual_bytes")),
        advertised_over=advertised_over,
        capacity_kind=_str(capacity.get("kind")) if capacity_judged else None,
        lost_ratio=_float(capacity.get("lost_ratio")) if capacity_judged else None,
        counterfeit_reasons=(
            _str_items(capacity.get("counterfeit_reasons")) if capacity_judged else None
        ),
        link_speed_mbps=_int(device.get("link_speed_mbps")),
        device_class=_str(speed.get("device_class")),
        read_mb_s=_float(read.get("mb_s")),
        write_mb_s=_float(write.get("mb_s")),
        threshold_read_mb_s=_float(speed.get("threshold_read_mb_s")),
        threshold_write_mb_s=_float(speed.get("threshold_write_mb_s")),
        speed_basis_read=_str(speed.get("basis_read")),
        speed_basis_write=_str(speed.get("basis_write")),
        speed_severity=_str(speed.get("severity")),
        hash_digest=_str(inventory.get("digest")),
        hash_lines=_int(inventory.get("lines")),
        reason_codes=_str_items(data.get("reason_codes")) or (),
    )
