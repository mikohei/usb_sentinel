from collections.abc import Generator
from contextlib import contextmanager
from pathlib import Path

from .device import block_info, filesystems
from .steps import StepContext


@contextmanager
def mounted(ctx: StepContext) -> Generator[list[Path]]:
    """Mount every filesystem on the device with `ro,noexec,nosuid,nodev`; umount on exit.

    These mount options are a safety rule and must not be removed. Mount targets are
    `mount_root/<kernel_name>/<n>`. If mount or umount fails, a RuntimeError
    (from CommandResult.check()) propagates.

    Args:
        ctx: Step context. While mounted, `ctx.mountpoints` holds the mount targets; it is
            emptied on exit.

    Yields:
        List of mount target paths (in filesystem order).
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
