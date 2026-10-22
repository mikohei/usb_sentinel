"""result.txt / console output.

The order and number of the first three lines (verdict, device, advertised capacity) and the
format of the step lines (`[ OK ] step: summary`) must not change. The fourth line is the
inspection mode (`Mode: scan_only` / `Mode: full`). Headings are in English.
Items added in phase 2 are read with `details.get()`, and the whole line is omitted when the
value is absent (design doc section 6.1).
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
    """The verdict, device, advertised capacity and mode lines, plus extra lines when values
    exist."""
    size = result.device.advertised_bytes
    lines = [
        f"Verdict: {result.verdict}",
        f"Device: {result.device.path} ({result.device.serial})",
        f"Advertised capacity: {_gb(size) if size else 'unknown'}",
        f"Mode: {result.mode}",
    ]
    if result.device.link_speed_mbps is not None:
        lines.append(f"Link speed: {result.device.link_speed_mbps}Mbps")
    if result.elapsed_s is not None:
        lines.append(f"Elapsed: {result.elapsed_s}s")
    if result.reformat:
        lines.append(
            f"Reformat: {result.reformat.get('status')} ({result.reformat.get('summary')})"
        )
    if result.advertised_check.get("exceeded") is True:
        lines.append(f"Advertised capacity exceeded: {result.advertised_check.get('summary', '')}")
    lines.extend(f"Warning: {warning}" for warning in result.warnings)
    return lines


def _step_detail_lines(step: StepResult) -> list[str]:
    """Step-specific extra lines. Empty when the new keys are absent."""
    details: dict[str, Any] = step.details
    lines: list[str] = []
    if step.name == "capacity":
        nominal = details.get("nominal_bytes")
        if isinstance(nominal, int):
            lines.append(f"  Nominal (product): {_gb(nominal)}")
        kind = details.get("kind")
        if kind is not None:
            lines.append(f"  Kind: {kind}")
    if step.name == "speed":
        severity = details.get("severity")
        if severity is not None:
            lines.append(f"  Severity: {severity}")
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
