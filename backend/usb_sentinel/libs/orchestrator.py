import re
import time
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

from .config import Config
from .device import block_info, ensure_safe, is_mounted, probe
from .hashing import inventory
from .models import DeviceInfo, InspectionResult, StepResult, StepStatus, Verdict
from .mounter import mounted
from .runner import CommandRunner
from .steps import StepContext
from .steps.capacity import CapacityStep
from .steps.capacity_judge import precheck_advertised
from .steps.malware import MalwareScanStep
from .steps.speed import SpeedStep
from .store import InspectionStore, NullStore


def result_dir_for(root: Path, started: datetime, device: DeviceInfo) -> Path:
    """結果ディレクトリ`<root>/<YYYYmmddTHHMMSSZ>-<serial>`を決める。

    シリアルは英数字と`_.-`以外を`_`に置き換えて128文字に切る。空なら`<kernel_name>-unknown-serial`。
    frontendの`result_file.serial_for_dir()`と同じ規則。

    Args:
        root: 結果ルート（config.results_dir）。
        started: 検査開始時刻（UTC）。
        device: デバイス情報。

    Returns:
        結果ディレクトリのパス（作成はしない）。
    """
    serial = device.serial or f"{device.kernel_name}-unknown-serial"
    serial = re.sub(r"[^a-zA-Z0-9_.-]", "_", serial)[:128]
    return root / f"{started.strftime('%Y%m%dT%H%M%SZ')}-{serial}"


def classify(result: InspectionResult) -> tuple[Verdict, list[str]]:
    """判定を決める。優先順位はMalware→Counterfeit Suspected→Defective→Spec Mismatch
    →No Defect→Inconclusive。"""
    malware = result.step("malware")
    if malware and malware.status == StepStatus.FAIL:
        return Verdict.MALWARE_DETECTED, [
            f"マルウェアを {len(malware.details.get('detections', []))} 件検知"
        ]
    failures = [step for step in result.steps if step.status == StepStatus.FAIL]
    reasons = [f"{step.name}: {step.summary}" for step in failures]
    # 非破壊チェック（マウント前の申告容量比較）の超過も偽装疑いの条件に含める。
    over_advertised = result.advertised_check.get("exceeded") is True
    if over_advertised:
        reasons.insert(0, result.advertised_check["summary"])
    if over_advertised or any(step.details.get("counterfeit") is True for step in failures):
        return Verdict.COUNTERFEIT_SUSPECTED, reasons
    if any(step.details.get("defect") is True for step in failures):
        # 容量は申告どおりだがデータが壊れる（f3readが非OKセクタを報告）。
        return Verdict.DEFECTIVE, reasons
    if failures:
        return Verdict.SPEC_MISMATCH, reasons
    if result.steps and all(step.status == StepStatus.PASS for step in result.steps):
        return Verdict.NO_DEFECT, ["全ステップ Pass"]
    return Verdict.INCONCLUSIVE, [
        f"{step.name}: {step.summary}" for step in result.steps if step.status != StepStatus.PASS
    ]


# reason_codesのステップ略号（画面QRのstと同じ）。
_STEP_ABBR = {"malware": "m", "hash_inventory": "h", "capacity": "c", "speed": "s", "unmount": "u"}
# 容量ステップのcounterfeit_reasons → コード。
_COUNTERFEIT_CODES = {
    "over_advertised": "c:over",
    "over_actual": "c:over",
    "probe_bad_news": "c:probe",
    "lost_ratio": "c:lost",
}


def _fail_codes(step: StepResult) -> list[str]:
    """FAILステップの理由コード。"""
    if step.name == "malware":
        return ["m:found"]
    if step.name == "capacity":
        codes = [
            _COUNTERFEIT_CODES[reason] for reason in step.details.get("counterfeit_reasons", [])
        ]
        codes += {"short": ["c:short"], "defect": ["c:defect"]}.get(step.details.get("kind"), [])
        return codes
    if step.name == "speed":
        # severityはspeed.pyの重大度判定で入る。無ければコードを出さない。
        severity = step.details.get("severity")
        return [f"s:{severity}"] if severity in ("severe", "minor") else []
    return []


def reason_codes_for(result: InspectionResult) -> list[str]:
    """各ステップのstatusとdetailsから機械可読の理由コードを組む。classify()とは独立。

    形式は「<ステップ略号>:<コード>」。c:overは非破壊チェックとステップの両方で
    発火しても1回だけ入れる。result.stepsの順に並べる。
    """
    codes: list[str] = []
    if result.advertised_check.get("exceeded") is True:
        codes.append("c:over")
    for step in result.steps:
        if step.status == StepStatus.PASS:
            continue
        if step.status == StepStatus.FAIL:
            candidates = _fail_codes(step)
        else:
            abbr = _STEP_ABBR.get(step.name, step.name[:1])
            timed_out = step.details.get("timed_out") is True
            candidates = [f"{abbr}:timeout" if timed_out else f"{abbr}:{step.status.value}"]
        for code in candidates:
            if code == "c:over" and code in codes:
                continue
            codes.append(code)
    return codes


def scan_executed(malware: StepResult) -> bool:
    """clamscanがスキャンを実行したか（実行後にだけdetectionsが入る）。"""
    return "detections" in malware.details


def _timed(run: Callable[[StepContext], StepResult], ctx: StepContext) -> StepResult:
    """ステップを実行し、所要時間（秒、小数2桁）をelapsed_sに入れて返す。"""
    started = time.monotonic()
    result = run(ctx)
    result.elapsed_s = round(time.monotonic() - started, 2)
    return result


def inspect(
    runner: CommandRunner, config: Config, path: str, store: InspectionStore | None = None
) -> InspectionResult:
    """検査シーケンス全体を駆動する（probe→安全ガード→roマウント→malware→台帳→破壊的検査→判定）。

    マルウェア検知時は破壊的検査（f3、fio）に進まない。破壊的検査に進む条件`permitted`
    （全ステップPASSかつallow_destructiveかつアンマウント済み）は安全ゲートであり緩めない。
    ハッシュ台帳はclamscanがスキャンを実行した後にだけ作る。

    Args:
        runner: コマンド実行器。
        config: 設定。
        path: 対象デバイスのパス。
        store: 開始記録の保存先。Noneなら記録しない（NullStore）。

    Returns:
        判定・理由コード・所要時間まで埋めた検査結果。

    Raises:
        UnsafeDeviceError: 安全ガードを満たさないデバイス（マウントもスキャンもしない）。
    """
    if store is None:
        store = NullStore()
    device = probe(runner, path)
    ensure_safe(runner, device)
    started = datetime.now(UTC)
    output = result_dir_for(config.results_dir, started, device)
    # Repeated inspections must never overwrite previous evidence.
    if output.exists():
        output = output.with_name(f"{output.name}-{started.strftime('%f')}")
    output.mkdir(parents=True)
    result = InspectionResult(device, started.isoformat(), output_dir=str(output))
    # 申告容量の非破壊チェック。probe()済みのlsblk値だけを使い、デバイスには触れない。
    # result.stepsには入れない（permittedと画面QRのstの桁数に影響させない）。
    result.advertised_check = precheck_advertised(
        device.advertised_bytes, config.capacity.nominal_bytes, config.capacity.over_ratio
    )
    # 開始記録。失敗しても検査は続ける（storeは例外を出さず理由文字列を返す）。
    failure = store.record_started(result)
    if failure:
        result.warnings.append(f"SQLite記録失敗（開始）: {failure}")
    ctx = StepContext(runner, config, device, output)
    try:
        with mounted(ctx):
            malware = _timed(MalwareScanStep().run, ctx)
            result.steps.append(malware)
            # 台帳はclamscanがファイルを開いた後にだけ作る（SOP: スキャン前にファイルを開かない）。
            # スキャン後なら結果（PASS・FAIL・ERROR・タイムアウト）にかかわらず作る。
            if not config.hash_inventory:
                result.steps.append(
                    StepResult(
                        "hash_inventory", StepStatus.SKIPPED, "無効（hash_inventory = false）"
                    )
                )
            elif not scan_executed(malware):
                result.steps.append(
                    StepResult(
                        "hash_inventory", StepStatus.SKIPPED, "スキャン未実行のため作成しない"
                    )
                )
            else:
                result.steps.append(_timed(inventory, ctx))
    except RuntimeError as exc:
        if not result.steps:
            result.steps.append(StepResult("malware", StepStatus.ERROR, f"マウント失敗: {exc}"))
        else:
            result.steps.append(StepResult("unmount", StepStatus.ERROR, str(exc)))
        if result.step("hash_inventory") is None:
            result.steps.append(StepResult("hash_inventory", StepStatus.SKIPPED, "マウント失敗"))
    permitted = all(
        step.status == StepStatus.PASS
        or (step.name == "hash_inventory" and not config.hash_inventory)
        for step in result.steps
    )
    reason = "スキャン・ハッシュ処理未完了"
    if not config.allow_destructive:
        permitted, reason = False, "allow_destructive = false"
    elif is_mounted(block_info(runner, path)):
        permitted, reason = False, "マウントが残っている"
    for name, step in (("capacity", CapacityStep()), ("speed", SpeedStep())):
        result.steps.append(
            _timed(step.run, ctx) if permitted else StepResult(name, StepStatus.SKIPPED, reason)
        )
    result.verdict, result.reasons = classify(result)
    result.reason_codes = reason_codes_for(result)
    now = datetime.now(UTC)
    result.finished_at = now.isoformat()
    result.elapsed_s = round((now - started).total_seconds(), 2)
    return result
