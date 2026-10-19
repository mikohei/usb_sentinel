"""デバイス種別ごとの業界最低速度（SOP準拠）。

`config.py`が`device_class`の検証に参照するため、このモジュールは`dataclasses`以外を
importしない（`steps/`配下をimportすると`config`→`steps`→`config`の循環になる）。
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class Floor:
    """種別1つ分の最低速度。

    Attributes:
        read_mb_s: 読み取りの最低値（MB/s、10^6バイト/秒）。
        write_mb_s: 書き込みの最低値（MB/s）。
        label: summary用の日本語名。
        code: QR・SQLite用の2文字コード。
    """

    read_mb_s: float
    write_mb_s: float
    label: str
    code: str


@dataclass(frozen=True)
class Reference:
    """速度判定で比較する基準値1つ分（読みまたは書き）。

    Attributes:
        value: 比較する基準値（MB/s）。無ければNone。
        basis: 基準の根拠。"nominal"（公称値）/ "class"（種別の最低値）/ "none"。
        device_class: 決定した種別キー。不明ならNone。
    """

    value: float | None
    basis: str
    device_class: str | None


# USB2.0 High Speedのリンク速度（Mbps）。この値のときだけusb2_flashと推定する。
USB2_HIGH_SPEED_MBPS = 480
# USB3.x SuperSpeed以上のリンク速度（Mbps）の下限。これ以上はusb3_flashと推定する。
USB3_SUPER_SPEED_MBPS = 5000
# USB3.2 Gen2以上は128b/132b符号化、未満（Gen1以下）は8b/10b符号化。
GEN2_MBPS = 10000


# microSDはSDと同じ値なので別キーは作らず、sd_c10・sd_u3を使う。
DEVICE_FLOORS: dict[str, Floor] = {
    "hdd_5400": Floor(80.0, 80.0, "HDD 5400rpm", "h5"),
    "hdd_7200": Floor(120.0, 120.0, "HDD 7200rpm", "h7"),
    "sata_ssd": Floor(400.0, 400.0, "SATA SSD", "ss"),
    "nvme_ssd": Floor(500.0, 500.0, "NVMe/USB3.2 SSD", "ns"),
    "sd_c10": Floor(25.0, 10.0, "SD Class10/U1", "s1"),
    "sd_u3": Floor(90.0, 30.0, "SD U3/V30", "s3"),
    "usb2_flash": Floor(15.0, 5.0, "USB2.0フラッシュ", "u2"),
    "usb3_flash": Floor(50.0, 15.0, "USB3.0フラッシュ", "u3"),
}


def resolve_device_class(
    configured: str | None, link_speed_mbps: int | None
) -> tuple[str | None, str]:
    """(種別キー, 根拠)を返す。根拠は"config" / "link_speed" / "unknown"。

    設定があればそれを優先する。無ければUSBリンク速度から推定する。480Mbps（USB2.0 High
    Speed）はusb2_flash、5000Mbps以上（USB3.x）はusb3_flash。USB1.1（0 < s < 480）と
    規格に無い値（480 < s < 5000）、不明（None）は推定しない。

    Args:
        configured: `[speed] device_class`の設定値。未設定ならNone。
        link_speed_mbps: sysfsから読んだリンク速度（Mbps）。読めなければNone。

    Returns:
        種別キー（DEVICE_FLOORSのキーまたはNone）と根拠の組。
    """
    if configured is not None:
        return configured, "config"
    if link_speed_mbps is None:
        return None, "unknown"
    if link_speed_mbps == USB2_HIGH_SPEED_MBPS:
        return "usb2_flash", "link_speed"
    if link_speed_mbps >= USB3_SUPER_SPEED_MBPS:
        return "usb3_flash", "link_speed"
    return None, "unknown"


def effective_mb_s(link_speed_mbps: int) -> float:
    """リンク速度（Mbps）から符号化損失を除いた実効上限（MB/s）を求める。

    Gen1以下（10000Mbps未満）は8b/10b符号化なので/10、Gen2以上は128b/132b符号化なので
    ×128/132/8。

    Args:
        link_speed_mbps: リンク速度（Mbps）。

    Returns:
        実効上限（MB/s、10^6バイト/秒）。
    """
    if link_speed_mbps < GEN2_MBPS:
        return link_speed_mbps / 10
    return link_speed_mbps * 128 / 132 / 8
