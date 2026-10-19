"""result.txt / console output.

The format of the first three lines (verdict, device, nominal capacity) and of the step lines
(`[ OK ] step: summary`) must not change. Items added in phase 2 are read with `details.get()`,
and the whole line is omitted when the value is absent (design doc section 6.1).
"""

from typing import Any

from ..models import InspectionResult, StepResult, StepStatus

_LABELS = {
    StepStatus.PASS: " OK ",
    StepStatus.FAIL: "FAIL",
    StepStatus.ERROR: "ERR ",
    StepStatus.SKIPPED: " -- ",
    StepStatus.NOT_IMPLEMENTED: " -- ",
}


def _gb(size: int) -> str:
    """Format bytes as "12.34 GB" (10^9, with a space). Unlike capacity_judge._gb(), has a space."""
    return f"{size / 1e9:.2f} GB"


def _header_lines(result: InspectionResult) -> list[str]:
    """The verdict, device, and nominal capacity lines, plus extra lines only when values exist."""
    size = result.device.advertised_bytes
    lines = [
        f"判定: {result.verdict}",
        f"デバイス: {result.device.path} ({result.device.serial})",
        f"公称容量   : {_gb(size) if size else '不明'}",
    ]
    if result.device.link_speed_mbps is not None:
        lines.append(f"リンク速度: {result.device.link_speed_mbps}Mbps")
    if result.elapsed_s is not None:
        lines.append(f"所要時間: {result.elapsed_s}秒")
    if result.advertised_check.get("exceeded") is True:
        lines.append(f"申告容量超過: {result.advertised_check.get('summary', '')}")
    lines.extend(f"警告: {warning}" for warning in result.warnings)
    return lines


def _step_detail_lines(step: StepResult) -> list[str]:
    """Step-specific extra lines. Empty when the new keys are absent."""
    details: dict[str, Any] = step.details
    lines: list[str] = []
    if step.name == "capacity":
        nominal = details.get("nominal_bytes")
        if isinstance(nominal, int):
            lines.append(f"  公称（商品）: {_gb(nominal)}")
        kind = details.get("kind")
        if kind is not None:
            lines.append(f"  区別: {kind}")
    if step.name == "speed":
        severity = details.get("severity")
        if severity is not None:
            lines.append(f"  重大度: {severity}")
    return lines


def render_text(result: InspectionResult) -> str:
    """Render the inspection result as human-readable text.

    Args:
        result: Inspection result.

    Returns:
        Newline-separated text (with one trailing newline).
    """
    lines = _header_lines(result)
    for step in result.steps:
        lines.append(f"[{_LABELS[step.status]}] {step.name}: {step.summary}")
        lines.extend(_step_detail_lines(step))
        lines.extend(f"  {warning}" for warning in step.warnings)
        lines.extend(
            f"  {detection['signature']}: {detection['path']}"
            for detection in step.details.get("detections", [])
        )
    return "\n".join(lines) + "\n"
