"""Runner that executes commands via Popen and optionally parses stderr for progress."""

import re
import subprocess
import threading
from pathlib import Path
from typing import Callable

from tools.gpu_pipeline.progress_tracker import ProgressTracker


LINE_RE = re.compile(r"frame=\s*(\d+)")


def run_command_plain(command: str) -> None:
    """Original blocking runner (fallback)."""
    subprocess.run(["pwsh", "-NoProfile", "-Command", command], check=True)


def make_popen_runner(
    tracker: ProgressTracker,
    chunk_index: int,
    stage_name: str,
) -> Callable[[str], None]:
    """Return a runner that parses FFmpeg stderr and feeds the tracker."""

    def runner(command: str) -> None:
        # Detect if this is an FFmpeg command
        is_ffmpeg = command.strip().startswith("ffmpeg")

        if not is_ffmpeg:
            # Non-FFmpeg: just time it
            run_command_plain(command)
            return

        proc = subprocess.Popen(
            ["pwsh", "-NoProfile", "-Command", command],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            text=True,
        )

        def _stderr_reader() -> None:
            if proc.stderr is None:
                return
            for line in proc.stderr:
                m = LINE_RE.search(line)
                if m:
                    frames_done = int(m.group(1))
                    tracker.update_stage(chunk_index, stage_name, frames_done=frames_done)

        reader = threading.Thread(target=_stderr_reader, daemon=True)
        reader.start()
        proc.wait()
        reader.join(timeout=2.0)

        if proc.returncode != 0:
            raise subprocess.CalledProcessError(proc.returncode, proc.args)

    return runner
