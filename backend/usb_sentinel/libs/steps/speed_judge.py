"""Pure functions that build the inputs of the speed judgment (references, severity,
warnings).

fio is not called. Used only from run() in speed.py. speed_floor.py owns the industry minimum
table and class resolution; this module turns them into read/write references and wording.
"""

from collections.abc import Iterable
from typing import Literal

from ..speed_floor import DEVICE_FLOORS, USB2_HIGH_SPEED_MBPS, Reference, effective_mb_s

Mode = Literal["read", "write"]

# A FAIL with measured / reference below this is severe; any other FAIL is minor.
SEVERE_RATIO = 0.50
# Warn of a possible ceiling when the class reference is at least this fraction of the link
# speed's effective ceiling.
LINK_CEILING_RATIO = 0.9

FAIL_PREFIX: dict[str, str] = {
    "severe": "Speed shortfall (severe): ",
    "minor": "Speed shortfall (minor): ",
}


def reference_for(mode: Mode, nominal: float | None, device_class: str | None) -> Reference:
    """Decide the reference with priority: nominal value > class minimum > none.

    Args:
        mode: "read" or "write".
        nominal: Value of `nominal_*_mb_s`. None if unset.
        device_class: Class key resolved by resolve_device_class(). None if unknown.

    Returns:
        Reference value and basis. If neither exists, value None and basis "none".
    """
    if nominal is not None:
        return Reference(nominal, "nominal", device_class)
    if device_class is not None:
        floor = DEVICE_FLOORS[device_class]
        value = floor.read_mb_s if mode == "read" else floor.write_mb_s
        return Reference(value, "class", device_class)
    return Reference(None, "none", device_class)


def class_warnings(
    configured: str | None,
    class_basis: str,
    link_speed_mbps: int | None,
    *,
    has_nominal: bool,
) -> list[str]:
    """Return warnings about class resolution (inferred from link speed, USB 1.1 link).

    Args:
        configured: Value of `[speed] device_class`.
        class_basis: Basis returned by resolve_device_class().
        link_speed_mbps: USB link speed (Mbps). None if unknown.
        has_nominal: True if either `nominal_read_mb_s` or `nominal_write_mb_s` is set. If so,
            the judgment can use the nominal value, so the "set `nominal_*`" warning is not
            emitted.

    Returns:
        List of warnings. Empty if none apply.
    """
    warnings: list[str] = []
    if class_basis == "link_speed":
        warnings.append(
            "Device class not set; judged with USB flash defaults"
            " (set `[speed] device_class` for HDD, SSD, or SD)"
        )
    if (
        configured is None
        and not has_nominal
        and link_speed_mbps is not None
        and 0 < link_speed_mbps < USB2_HIGH_SPEED_MBPS
    ):
        warnings.append(
            f"USB1.1 link ({link_speed_mbps}Mbps) has no verdict criterion; set `nominal_*`"
        )
    return warnings


def link_ceiling_warning(
    references: Iterable[Reference], link_speed_mbps: int | None
) -> str | None:
    """Return at most one warning when a class reference nears the link's effective ceiling.

    Nominal values (basis "nominal") are given by the user, so they are not compared.

    Args:
        references: Read and write references.
        link_speed_mbps: USB link speed (Mbps). No check if None.

    Returns:
        Warning text. None if none applies.
    """
    if link_speed_mbps is None:
        return None
    effective = effective_mb_s(link_speed_mbps)
    for ref in references:
        if (
            ref.basis == "class"
            and ref.value is not None
            and ref.value >= effective * LINK_CEILING_RATIO
        ):
            return (
                f"Reference {ref.value:.1f}MB/s exceeds the effective upper limit of a"
                f" {link_speed_mbps}Mbps link (about {effective:.1f}MB/s);"
                " the host may cap the speed. Setting `nominal_*` is recommended"
            )
    return None


def basis_text(ref: Reference, pass_ratio: float, class_basis: str) -> str:
    """Build the parenthesized basis appended to a FAIL summary.

    Nominal: " (nominal 90.0 x 0.80)"; configured class: " (USB3.0 flash default 50.0 x 0.80)";
    inferred class: " (USB3.0 flash default 50.0 x 0.80, class inferred)".

    Args:
        ref: Reference that was compared. Empty string if value is None.
        pass_ratio: Pass ratio.
        class_basis: Basis of the class. "link_speed" adds the "class inferred" suffix.

    Returns:
        The parenthesized string.
    """
    if ref.value is None:
        return ""
    if ref.basis == "nominal" or ref.device_class is None:
        return f" (nominal {ref.value:.1f} x {pass_ratio:.2f})"
    label = DEVICE_FLOORS[ref.device_class].label
    suffix = ", class inferred" if class_basis == "link_speed" else ""
    return f" ({label} default {ref.value:.1f} x {pass_ratio:.2f}{suffix})"


def severity_for(ratios: Iterable[float]) -> str:
    """Decide the overall severity from the ratios of items below threshold. If either is
    severe, the overall result is severe.

    Args:
        ratios: Measured / reference (only for items below threshold).

    Returns:
        "severe" or "minor".
    """
    return "severe" if any(ratio < SEVERE_RATIO for ratio in ratios) else "minor"
