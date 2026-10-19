import hashlib
import json
from dataclasses import asdict

from ..models import InspectionResult


def to_json(result: InspectionResult) -> str:
    """Build the body of result.json: sorted keys, non-ASCII unescaped, one trailing newline.

    Args:
        result: Inspection result.

    Returns:
        JSON string indented by 2.
    """
    return json.dumps(asdict(result), ensure_ascii=False, sort_keys=True, indent=2) + "\n"


def digest(result: InspectionResult) -> str:
    """SHA-256 (lowercase hex) of the UTF-8 bytes of to_json().

    The QR code and SQLite use it to link back to result.json.

    Args:
        result: Inspection result.

    Returns:
        64-character hex digest.
    """
    return hashlib.sha256(to_json(result).encode("utf-8")).hexdigest()
