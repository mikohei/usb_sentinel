from collections.abc import Generator
from contextlib import contextmanager
from pathlib import Path

from .device import block_info, filesystems
from .steps import StepContext


@contextmanager
def mounted(ctx: StepContext) -> Generator[list[Path]]:
    """デバイスの全ファイルシステムを`ro,noexec,nosuid,nodev`でマウントし、抜けるときにumountする。

    このマウントオプションは安全規則であり外さない。マウント先は`mount_root/<kernel_name>/<n>`。
    mountまたはumountが失敗するとRuntimeError（CommandResult.check()）を伝える。

    Args:
        ctx: ステップ文脈。マウント中は`ctx.mountpoints`にマウント先を入れ、抜けるときに空にする。

    Yields:
        マウント先のパス一覧（ファイルシステムの順）。
    """
    paths: list[Path] = []
    try:
        for index, device_path in enumerate(filesystems(block_info(ctx.runner, ctx.device.path))):
            target = ctx.config.mount_root / ctx.device.kernel_name / str(index)
            target.mkdir(parents=True, exist_ok=True)
            ctx.runner.run(
                [
                    "mount",
                    "-o",
                    "ro,noexec,nosuid,nodev",
                    device_path,
                    str(target),
                ]
            ).check()
            paths.append(target)
        ctx.mountpoints = paths
        yield paths
    finally:
        for target in reversed(paths):
            ctx.runner.run(["umount", str(target)]).check()
        ctx.mountpoints = []
