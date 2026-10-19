from .models import ResultDetail

# 容量ステップのkind・lost_ratio・counterfeit_reasonsを読む状態（それ以外ではkindが無い）。
CAPACITY_JUDGED_STATUSES = frozenset({"pass", "fail"})


def _int(value: object) -> int | None:
    """boolでない整数ならそのまま、違えばNone。"""
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def _float(value: object) -> float | None:
    """boolでない数値ならfloatに、違えばNone。"""
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    return float(value)


def _str(value: object) -> str | None:
    """文字列ならそのまま、違えばNone。"""
    return value if isinstance(value, str) else None


def _str_items(value: object) -> tuple[str, ...] | None:
    """リストならstr要素だけのタプルに、違えばNone。"""
    if not isinstance(value, list):
        return None
    return tuple(item for item in value if isinstance(item, str))


def _dict(value: object) -> dict[str, object] | None:
    """辞書ならキーを文字列化した複製を、違えばNone。"""
    if not isinstance(value, dict):
        return None
    return {str(key): item for key, item in value.items()}


def _step(steps: object, name: str) -> dict[str, object] | None:
    """stepsから名前が一致するステップ辞書を返す。無ければNone。"""
    if not isinstance(steps, list):
        return None
    for step in steps:
        if isinstance(step, dict) and step.get("name") == name:
            return _dict(step)
    return None


def _details(steps: object, name: str) -> dict[str, object] | None:
    """該当ステップのdetails辞書。ステップが無い、またはdetailsが辞書でなければNone。"""
    step = _step(steps, name)
    return _dict(step.get("details")) if step is not None else None


def _status(steps: object, name: str) -> str | None:
    """該当ステップのstatus文字列。無ければNone。"""
    step = _step(steps, name)
    return _str(step.get("status")) if step is not None else None


def _db_version(value: object) -> str | None:
    """ClamAV DBバージョンを文字列にする（backendは文字列だが整数でも受ける）。"""
    if isinstance(value, str):
        return value
    number = _int(value)
    return str(number) if number is not None else None


def parse_detail(data: dict[str, object]) -> ResultDetail:
    """result.jsonの内容から詳細QR（v:3）の材料を取り出す。

    すべての値はisinstanceで型を確かめ、違えばNoneにする。ERROR／SKIPPEDの経路では
    detailsにキーが無いので.get()で読む。容量のkind・lost_ratio・counterfeit_reasonsは
    ステップがPASS／FAILのときだけ読み、actual_bytesは状態を問わず読む。

    Args:
        data: json.loads()したresult.json（dict）。

    Returns:
        取り出した値。無い値はNone、reason_codesは空タプル。
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
