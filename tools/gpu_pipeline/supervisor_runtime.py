from __future__ import annotations

import json
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass
from json import JSONDecodeError
from pathlib import Path

from tools.pipeline_runtime import build_video_id


@dataclass
class JobSnapshot:
    total_chunks: int
    done_chunks: int
    pending_chunks: int
    failed_chunks: int
    current_chunk: int | None
    chunk_statuses: list[str]
    eta_seconds: float | None
    last_progress_at: float | None


class WorkerBackoff:
    def __init__(self) -> None:
        self._sequence = [10, 30, 60, 120, 300]
        self._index = 0

    def next_delay_seconds(self) -> int:
        value = self._sequence[min(self._index, len(self._sequence) - 1)]
        self._index += 1
        return value

    def reset(self) -> None:
        self._index = 0


def resolve_job_dir(video_name: str, input_dir: Path, work_dir: Path) -> Path:
    return work_dir / build_video_id(input_dir / video_name)


def resolve_final_output_path(output_root: Path, video_id: str, input_video: str | Path) -> Path:
    return output_root / video_id / f"{Path(input_video).stem}_final.mp4"


def resolve_python_exe() -> str:
    return sys.executable or ".venv\\Scripts\\python.exe"


def resolve_shell_exe() -> str:
    if shutil.which("pwsh"):
        return "pwsh"
    if shutil.which("powershell"):
        return "powershell"
    return "pwsh"


def estimate_eta_seconds(total_chunks: int, done_chunks: int, elapsed_seconds: float) -> float | None:
    if done_chunks <= 0:
        return None
    pending_chunks = max(total_chunks - done_chunks, 0)
    avg_chunk_seconds = max(elapsed_seconds, 0.0) / done_chunks
    return max(avg_chunk_seconds * pending_chunks, 0.0)


def _load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def collect_job_snapshot(job_dir: Path) -> JobSnapshot:
    manifest_path = job_dir / "manifests" / "manifest.json"
    if not manifest_path.exists():
        return JobSnapshot(
            total_chunks=0,
            done_chunks=0,
            pending_chunks=0,
            failed_chunks=0,
            current_chunk=None,
            chunk_statuses=[],
            eta_seconds=None,
            last_progress_at=None,
        )

    try:
        manifest = _load_json(manifest_path)
    except (OSError, JSONDecodeError, ValueError):
        return JobSnapshot(
            total_chunks=0,
            done_chunks=0,
            pending_chunks=0,
            failed_chunks=0,
            current_chunk=None,
            chunk_statuses=[],
            eta_seconds=None,
            last_progress_at=None,
        )
    chunks = manifest.get("chunks", [])
    statuses = [chunk.get("status", "pending") for chunk in chunks]
    done = sum(1 for status in statuses if status == "done")
    failed = sum(1 for status in statuses if status == "failed")
    pending = sum(1 for status in statuses if status == "pending")
    current = next((index for index, status in enumerate(statuses) if status != "done"), None)

    worker_log = job_dir / "worker.console.log"
    last_progress_at = manifest_path.stat().st_mtime
    if worker_log.exists():
        last_progress_at = max(last_progress_at, worker_log.stat().st_mtime)

    return JobSnapshot(
        total_chunks=len(chunks),
        done_chunks=done,
        pending_chunks=pending,
        failed_chunks=failed,
        current_chunk=current,
        chunk_statuses=statuses,
        eta_seconds=None,
        last_progress_at=last_progress_at,
    )


def is_job_complete(job_dir: Path, output_root: Path) -> bool:
    manifest_path = job_dir / "manifests" / "manifest.json"
    if not manifest_path.exists():
        return False
    try:
        manifest = _load_json(manifest_path)
    except (OSError, JSONDecodeError, ValueError):
        return False
    chunks = manifest.get("chunks", [])
    if any(chunk.get("status") != "done" for chunk in chunks):
        return False
    final_output = resolve_final_output_path(
        output_root,
        manifest["video_id"],
        manifest["input_video"],
    )
    return final_output.exists()


def detect_worker_status(
    worker_alive: bool,
    job_dir: Path,
    output_root: Path,
    stalled_after_seconds: int,
) -> str:
    if is_job_complete(job_dir, output_root):
        return "completed"
    snapshot = collect_job_snapshot(job_dir)
    if not worker_alive:
        return "starting" if snapshot.total_chunks == 0 else "crashed"
    if stalled_after_seconds > 0 and snapshot.last_progress_at is not None and time.time() - snapshot.last_progress_at > stalled_after_seconds:
        return "stalled"
    return "starting" if snapshot.total_chunks == 0 else "running"


def build_worker_command(
    video_name: str,
    input_dir: str = "gameplays_crudos",
    output_dir: str = "output",
    work_dir: str = ".pipeline_work",
) -> str:
    def ps_quote(value: str) -> str:
        return "'" + value.replace("'", "''") + "'"

    return (
        "$env:LIVEPORTRAIT_DISABLE_PROGRESS_TRACKER='1'; "
        f"& {ps_quote(resolve_python_exe())} {ps_quote('tools/gpu_pipeline/run_long_gameplay_pipeline_gpu.py')} "
        f"--video {ps_quote(video_name)} --input-dir {ps_quote(input_dir)} "
        f"--output-dir {ps_quote(output_dir)} --work-dir {ps_quote(work_dir)}"
    )


def launch_worker_terminal(
    video_name: str,
    job_dir: Path,
    workspace: Path,
    input_dir: str = "gameplays_crudos",
    output_dir: str = "output",
    work_dir: str = ".pipeline_work",
) -> subprocess.Popen:
    job_dir.mkdir(parents=True, exist_ok=True)
    log_path = (job_dir / "worker.console.log").resolve()
    worker_command = build_worker_command(
        video_name,
        input_dir=input_dir,
        output_dir=output_dir,
        work_dir=work_dir,
    )
    ps_command = (
        f'Set-Location "{workspace}"; '
        f'& {{ {worker_command} 2>&1 | Tee-Object -FilePath "{log_path}" -Append }}; '
        'exit $LASTEXITCODE'
    )
    creationflags = getattr(subprocess, "CREATE_NEW_CONSOLE", 0)
    return subprocess.Popen(
        [resolve_shell_exe(), "-NoProfile", "-Command", ps_command],
        creationflags=creationflags,
    )


def terminate_worker_tree(process: subprocess.Popen) -> None:
    subprocess.run(
        ["taskkill", "/T", "/F", "/PID", str(process.pid)],
        check=False,
        capture_output=True,
        text=True,
    )


def format_duration(seconds: float | None) -> str:
    if seconds is None:
        return "calculating..."
    total = max(int(seconds), 0)
    minutes, secs = divmod(total, 60)
    hours, minutes = divmod(minutes, 60)
    if hours:
        return f"{hours}h {minutes:02d}m {secs:02d}s"
    return f"{minutes}m {secs:02d}s"


def format_event(message: str) -> str:
    return time.strftime("%H:%M:%S") + " " + message
