from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol

from ..config import Config
from ..models import DeviceInfo, StepResult
from ..runner import CommandRunner


@dataclass
class StepContext:
    runner: CommandRunner
    config: Config
    device: DeviceInfo
    output_dir: Path
    mountpoints: list[Path] = field(default_factory=list)


class Step(Protocol):
    def run(self, ctx: StepContext) -> StepResult:
        """Run the step once and return a StepResult with status, summary, and details filled."""
        ...
