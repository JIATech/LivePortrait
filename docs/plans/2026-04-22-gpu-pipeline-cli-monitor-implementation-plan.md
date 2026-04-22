# GPU Pipeline CLI Monitor — Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Build a real-time rich CLI monitor with animated per-stage progress bars for the LivePortrait GPU gameplay pipeline.

**Architecture:** A `ProgressTracker` writes atomic `state.json` updates from the pipeline; a separate `gpu_pipeline_monitor.py` (using `rich`) reads that file every 500ms and renders an animated UI. FFmpeg stages get real progress via `Popen` stderr parsing; LivePortrait gets time-based estimated progress.

**Tech Stack:** Python 3.13, `rich>=13.0.0`, existing `tools/pipeline_runtime.py` dataclasses.

---

## Prerequisites

- Virtual environment `.venv` is activated
- `pretrained_weights/` exist
- `gameplays_crudos/` has at least one `.mp4`

---

## Task 1: Install `rich`

**Step 1: Install the dependency**

```bash
.venv\Scripts\activate
pip install rich>=13.0.0
```

**Verification:**

```bash
.venv\Scripts\python -c "import rich; print(rich.__version__)"
```

Expected: version number printed (e.g., `13.0.0`)

---

## Task 2: Create `ProgressTracker`

**Files:**
- Create: `tools/gpu_pipeline/progress_tracker.py`

**Step 1: Write the module**

```python
"""ProgressTracker writes live pipeline state to a JSON file for the monitor."""

import json
import tempfile
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from tools.pipeline_runtime import JobManifest


@dataclass
class StageRecord:
    name: str
    status: str = "pending"  # pending | running | done | failed
    progress: float = 0.0    # 0.0 .. 1.0
    frames_done: int | None = None
    frames_total: int | None = None
    elapsed_seconds: float | None = None
    estimated_total_seconds: float | None = None
    error: str | None = None


@dataclass
class ChunkRecord:
    index: int
    status: str = "pending"  # pending | processing | done | failed
    stages: list[StageRecord] = field(default_factory=list)


@dataclass
class PipelineState:
    video_id: str
    input_video: str
    total_seconds: float
    total_chunks: int
    started_at: str
    current_chunk_index: int | None = None
    chunks: list[ChunkRecord] = field(default_factory=list)
    global_eta_seconds: float | None = None
    last_updated: str = ""


class ProgressTracker:
    def __init__(self, job_dir: Path, manifest: JobManifest):
        self.job_dir = job_dir
        self.state_path = job_dir / "state.json"
        self.manifest = manifest
        self._stage_start_time: float | None = None
        self._liveportrait_times: list[float] = []
        self._composite_times: list[float] = []

        self.state = PipelineState(
            video_id=manifest.video_id,
            input_video=manifest.input_video,
            total_seconds=manifest.total_seconds,
            total_chunks=len(manifest.chunks),
            started_at=_iso_now(),
            chunks=[
                ChunkRecord(
                    index=chunk.index,
                    stages=[
                        StageRecord(name="extract"),
                        StageRecord(name="roi_crop"),
                        StageRecord(name="source_loop"),
                        StageRecord(name="liveportrait"),
                        StageRecord(name="composite"),
                    ],
                )
                for chunk in manifest.chunks
            ],
        )
        self._flush()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------
    def mark_chunk_done(self, chunk_index: int) -> None:
        chunk = self.state.chunks[chunk_index]
        chunk.status = "done"
        for stage in chunk.stages:
            stage.status = "done"
            stage.progress = 1.0
        self._flush()

    def begin_stage(
        self,
        chunk_index: int,
        stage_name: str,
        frames_total: int | None = None,
    ) -> None:
        self.state.current_chunk_index = chunk_index
        chunk = self.state.chunks[chunk_index]
        chunk.status = "processing"
        stage = self._find_stage(chunk, stage_name)
        stage.status = "running"
        stage.progress = 0.0
        stage.frames_total = frames_total
        stage.frames_done = 0
        self._stage_start_time = time.perf_counter()
        self._flush()

    def update_stage(
        self,
        chunk_index: int,
        stage_name: str,
        frames_done: int | None = None,
        progress: float | None = None,
    ) -> None:
        stage = self._find_stage(self.state.chunks[chunk_index], stage_name)
        if frames_done is not None:
            stage.frames_done = frames_done
            if stage.frames_total:
                stage.progress = min(1.0, frames_done / stage.frames_total)
        if progress is not None:
            stage.progress = min(1.0, max(0.0, progress))
        self._flush()

    def end_stage(self, chunk_index: int, stage_name: str) -> None:
        stage = self._find_stage(self.state.chunks[chunk_index], stage_name)
        stage.status = "done"
        stage.progress = 1.0
        elapsed = time.perf_counter() - self._stage_start_time if self._stage_start_time else 0.0
        stage.elapsed_seconds = elapsed

        if stage_name == "liveportrait":
            self._liveportrait_times.append(elapsed)
        elif stage_name == "composite":
            self._composite_times.append(elapsed)

        self._stage_start_time = None
        self._recalculate_eta()
        self._flush()

    def fail_stage(self, chunk_index: int, stage_name: str, error: str) -> None:
        chunk = self.state.chunks[chunk_index]
        chunk.status = "failed"
        stage = self._find_stage(chunk, stage_name)
        stage.status = "failed"
        stage.error = error
        self._flush()

    def close(self) -> None:
        self._flush()

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------
    def _find_stage(self, chunk: ChunkRecord, name: str) -> StageRecord:
        for stage in chunk.stages:
            if stage.name == name:
                return stage
        raise KeyError(f"Stage {name!r} not found in chunk {chunk.index}")

    def _recalculate_eta(self) -> None:
        done = sum(1 for c in self.state.chunks if c.status == "done")
        pending = sum(1 for c in self.state.chunks if c.status == "pending")
        if done == 0:
            self.state.global_eta_seconds = None
            return
        elapsed_total = time.perf_counter() - _parse_iso(self.state.started_at)
        avg_per_chunk = elapsed_total / done
        self.state.global_eta_seconds = avg_per_chunk * pending

    def _flush(self) -> None:
        self.state.last_updated = _iso_now()
        data = asdict(self.state)
        # Atomic write
        tmp = self.state_path.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
        tmp.rename(self.state_path)


def _iso_now() -> str:
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).isoformat()


def _parse_iso(iso: str) -> float:
    from datetime import datetime, timezone
    return datetime.fromisoformat(iso).timestamp()
```

**Verification:**

```bash
.venv\Scripts\python -c "from tools.gpu_pipeline.progress_tracker import ProgressTracker; print('OK')"
```

Expected: `OK`

---

## Task 3: Create `PopenRunner`

**Files:**
- Create: `tools/gpu_pipeline/popen_runner.py`

**Step 1: Write the module**

```python
"""Runner that executes commands via Popen and optionally parses stderr for progress."""

import re
import subprocess
import threading
from pathlib import Path
from typing import Callable

from tools.gpu_pipeline.gpu_pipeline_commands import q
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
```

**Verification:**

```bash
.venv\Scripts\python -c "from tools.gpu_pipeline.popen_runner import make_popen_runner; print('OK')"
```

Expected: `OK`

---

## Task 4: Modify `run_long_gameplay_pipeline_gpu.py`

**Files:**
- Modify: `tools/gpu_pipeline/run_long_gameplay_pipeline_gpu.py`

**Step 1: Add imports near the top (after existing imports)**

```python
from tools.gpu_pipeline.progress_tracker import ProgressTracker
from tools.gpu_pipeline.popen_runner import make_popen_runner, run_command_plain
```

**Step 2: Modify `process_chunk()` signature and body**

Replace the entire `process_chunk` function:

```python
def process_chunk(
    runner: Callable[[str], None],
    input_video: Path,
    source_prepared: Path,
    chunk_index: int,
    start_seconds: float,
    duration_seconds: float,
    paths: dict[str, Path],
    profile: PipelineProfile,
    tracker: ProgressTracker | None = None,
) -> None:
    # Stage definitions: (name, cmd_builder, frames_total_estimate)
    stages: list[tuple[str, Callable[[], str], int | None]] = [
        ("extract", lambda: build_full_chunk_cmd(input_video, start_seconds, duration_seconds, paths["full_chunk"]), None),
        ("roi_crop", lambda: build_roi_chunk_cmd(paths["full_chunk"], profile.roi, profile.source_fps, paths["roi_chunk"]), None),
        ("source_loop", lambda: build_loop_source_cmd(source_prepared, duration_seconds, paths["source_loop"]), None),
        ("liveportrait", lambda: build_liveportrait_cmd(paths["source_loop"], paths["roi_chunk"], paths["patch_dir"], profile.liveportrait), None),
        ("composite", lambda: build_composite_cmd(paths["full_chunk"], resolve_patch_video_path(paths["patch_dir"]), paths["composited"], profile.roi, profile.compositor), None),
    ]

    for stage_name, cmd_builder, _ in stages:
        cmd = cmd_builder()
        if tracker:
            tracker.begin_stage(chunk_index, stage_name)
        try:
            if stage_name in ("extract", "roi_crop", "source_loop") and tracker:
                # Use Popen runner for FFmpeg stages to get frame progress
                popen_runner = make_popen_runner(tracker, chunk_index, stage_name)
                popen_runner(cmd)
            else:
                runner(cmd)
            if tracker:
                tracker.end_stage(chunk_index, stage_name)
        except Exception as exc:
            if tracker:
                tracker.fail_stage(chunk_index, stage_name, str(exc))
            raise
```

**Step 3: Modify `run_job()` to instantiate tracker**

Replace the `run_job` function body (keep signature). The key change is adding tracker initialization before the chunk loop, and passing it to `process_chunk`:

```python
def run_job(
    input_video: Path,
    profile: PipelineProfile,
    output_dir: Path,
    work_dir: Path,
    runner: Callable[[str], None],
    config_path: Path | None = None,
) -> Path:
    started_at = datetime.now(timezone.utc).isoformat()
    started_timer = time.perf_counter()
    job_id = build_video_id(input_video)
    job_paths = build_job_paths(work_dir=work_dir, output_dir=output_dir, job_id=job_id)
    resolved_config_path = config_path or Path("tools/gpu_pipeline/gpu_profile_gameplay_v1.json")
    job_paths.output_dir.mkdir(parents=True, exist_ok=True)
    manifest = load_or_create_manifest(
        job_dir=job_paths.job_dir,
        input_video=input_video,
        total_seconds=probe_duration_seconds(input_video, runner),
        profile=profile,
    )
    source_prepared = prepare_source_master(job_paths.job_dir, profile, runner)

    tracker = ProgressTracker(job_paths.job_dir, manifest)

    try:
        for chunk in manifest.chunks:
            if chunk.status == "done":
                tracker.mark_chunk_done(chunk.index)
                continue

            paths = build_chunk_paths(job_paths.job_dir, chunk.index)
            process_chunk(
                runner=runner,
                input_video=input_video,
                source_prepared=source_prepared,
                chunk_index=chunk.index,
                start_seconds=chunk.start_seconds,
                duration_seconds=chunk.duration_seconds,
                paths=paths,
                profile=profile,
                tracker=tracker,
            )
            chunk.status = "done"
            chunk.error = None
        finally:
            save_manifest(job_paths.manifest_path, manifest)
            tracker.close()

        # ... rest of run_job stays the same (assemble_final_output, write_report)
        # BUT we need to fix the try/finally structure - see note below
```

Wait - the existing code uses `try/except/finally` inside the loop. We need to restructure carefully. Here's the correct replacement for the loop section:

```python
    tracker = ProgressTracker(job_paths.job_dir, manifest)

    try:
        for chunk in manifest.chunks:
            if chunk.status == "done":
                tracker.mark_chunk_done(chunk.index)
                continue

            paths = build_chunk_paths(job_paths.job_dir, chunk.index)
            try:
                process_chunk(
                    runner=runner,
                    input_video=input_video,
                    source_prepared=source_prepared,
                    chunk_index=chunk.index,
                    start_seconds=chunk.start_seconds,
                    duration_seconds=chunk.duration_seconds,
                    paths=paths,
                    profile=profile,
                    tracker=tracker,
                )
                chunk.status = "done"
                chunk.error = None
            except Exception as exc:
                chunk.status = "failed"
                chunk.error = str(exc)
                write_report(
                    output_dir=job_paths.output_dir,
                    input_video=input_video,
                    profile=profile,
                    manifest=manifest,
                    config_path=resolved_config_path,
                    total_processing_seconds=time.perf_counter() - started_timer,
                    execution_timestamp=started_at,
                )
                raise
            finally:
                save_manifest(job_paths.manifest_path, manifest)
                tracker._flush()
    finally:
        tracker.close()
```

**Verification:**

```bash
.venv\Scripts\python -m py_compile tools/gpu_pipeline/run_long_gameplay_pipeline_gpu.py
```

Expected: No output (success)

---

## Task 5: Create `gpu_pipeline_monitor.py`

**Files:**
- Create: `tools/gpu_pipeline/gpu_pipeline_monitor.py`

**Step 1: Write the monitor**

```python
"""Rich CLI monitor for the GPU gameplay pipeline."""

import argparse
import json
import time
from pathlib import Path

from rich.console import Console
from rich.layout import Layout
from rich.live import Live
from rich.panel import Panel
from rich.progress import BarColumn, Progress, SpinnerColumn, TaskProgressColumn, TextColumn
from rich.table import Table


def _load_state(path: Path) -> dict | None:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return None


def _format_duration(seconds: float | None) -> str:
    if seconds is None:
        return "unknown"
    m, s = divmod(int(seconds), 60)
    h, m = divmod(m, 60)
    if h:
        return f"{h}h {m:02d}m {s:02d}s"
    return f"{m}m {s:02d}s"


def _format_eta(state: dict) -> str:
    eta = state.get("global_eta_seconds")
    if eta is None:
        return "calculating..."
    return _format_duration(eta)


def _build_chunk_grid(state: dict) -> Table:
    table = Table(show_header=False, box=None, padding=(0, 1))
    chunks = state.get("chunks", [])
    for chunk in chunks:
        idx = chunk["index"]
        status = chunk["status"]
        progress = _chunk_progress(chunk)
        bar = "█" * int(progress * 40) + "░" * (40 - int(progress * 40))
        label = status.upper()
        if status == "failed":
            label = f"[red]{label}[/red]"
        elif status == "done":
            label = f"[green]{label}[/green]"
        elif status == "processing":
            label = f"[yellow]{label}[/yellow]"
        table.add_row(f"{bar} Chunk {idx:02d}  {label}")
    return table


def _chunk_progress(chunk: dict) -> float:
    stages = chunk.get("stages", [])
    if not stages:
        return 0.0
    return sum(s.get("progress", 0.0) for s in stages) / len(stages)


def _build_active_detail(state: dict) -> Table:
    table = Table(show_header=False, box=None, padding=(0, 1))
    current = state.get("current_chunk_index")
    if current is None:
        table.add_row("No active chunk")
        return table

    chunk = state["chunks"][current]
    for stage in chunk.get("stages", []):
        name = stage["name"]
        prog = stage.get("progress", 0.0)
        pct = int(prog * 100)
        bar = "█" * int(prog * 40) + "░" * (40 - int(prog * 40))
        detail = ""
        if stage.get("frames_total"):
            detail = f" ({stage.get('frames_done', 0)}/{stage['frames_total']} fr)"
        elif stage.get("estimated_total_seconds"):
            detail = f" ({stage.get('elapsed_seconds', 0):.0f}s/{stage['estimated_total_seconds']:.0f}s est)"
        elif stage["status"] == "pending":
            detail = " (pending)"
        table.add_row(f"  {name:12s} [{bar}] {pct:3d}%{detail}")
    return table


def _build_summary(state: dict) -> str:
    chunks = state.get("chunks", [])
    done = sum(1 for c in chunks if c["status"] == "done")
    pending = sum(1 for c in chunks if c["status"] == "pending")
    failed = sum(1 for c in chunks if c["status"] == "failed")
    processing = sum(1 for c in chunks if c["status"] == "processing")
    total = len(chunks)
    pct = round((done / total) * 100, 1) if total else 0.0
    return (
        f"Done: {done} | Processing: {processing} | Pending: {pending} | Failed: {failed}  ({pct}% total)"
    )


def build_layout(state: dict | None) -> Layout:
    layout = Layout()
    if state is None:
        layout.update(Panel("[yellow]Waiting for pipeline…[/yellow]", title="GPU Pipeline Monitor"))
        return layout

    header_text = (
        f"[bold]{Path(state['input_video']).name}[/bold]  |  "
        f"Chunks: {state['total_chunks']}  |  "
        f"ETA: {_format_eta(state)}"
    )
    header = Panel(header_text, title="GPU Pipeline Monitor")
    grid = Panel(_build_chunk_grid(state), title="Chunk Grid", border_style="blue")
    detail = Panel(_build_active_detail(state), title=f"Active Chunk {state.get('current_chunk_index', '?')}", border_style="yellow")
    summary = Panel(_build_summary(state), title="Summary", border_style="green")

    layout.split_column(
        Layout(header, size=3),
        Layout(grid, size=state["total_chunks"] + 2),
        Layout(detail, size=8),
        Layout(summary, size=3),
    )
    return layout


def monitor_loop(job_dir: Path, interval: float = 0.5) -> None:
    state_path = job_dir / "state.json"
    console = Console()
    with Live(console=console, refresh_per_second=2, screen=True) as live:
        while True:
            state = _load_state(state_path)
            live.update(build_layout(state))
            time.sleep(interval)


def resolve_job_dir(work_dir: Path, video_name: str) -> Path:
    for manifest_path in work_dir.glob("*/manifests/manifest.json"):
        data = json.loads(manifest_path.read_text(encoding="utf-8"))
        input_video = data.get("input_video", "")
        if Path(input_video).name == video_name:
            return manifest_path.parent.parent
    raise FileNotFoundError(f"No pipeline job found for video: {video_name}")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Rich GPU pipeline monitor")
    parser.add_argument("--work-dir", default=".pipeline_work")
    parser.add_argument("--video", required=True)
    parser.add_argument("--interval", type=float, default=0.5)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    job_dir = resolve_job_dir(Path(args.work_dir), args.video)
    try:
        monitor_loop(job_dir, interval=args.interval)
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
```

**Verification:**

```bash
.venv\Scripts\python -m py_compile tools/gpu_pipeline/gpu_pipeline_monitor.py
.venv\Scripts\python tools/gpu_pipeline/gpu_pipeline_monitor.py --help
```

Expected: Help text shown.

---

## Task 6: End-to-End Smoke Test

**Step 1: Start the monitor in one terminal**

```bash
.venv\Scripts\activate
python tools/gpu_pipeline/gpu_pipeline_monitor.py --video RE9-part3.mp4
```

**Step 2: In a second terminal, start a short pipeline run**

Create a 5-second test video first (or reuse existing small file), then run the pipeline:

```bash
.venv\Scripts\activate
python tools/gpu_pipeline/run_long_gameplay_pipeline_gpu.py --video RE9-part3.mp4
```

**Expected behavior in monitor:**
- Header shows `RE9-part3.mp4` and chunk count
- Chunk grid shows first chunk turning yellow (PROCESSING) and its sub-bars filling
- LivePortrait stage shows time-based bar growing
- Summary row updates

**Step 3: Verify `state.json` exists and is well-formed**

```bash
Get-Content .pipeline_work\RE9-part3-c72b77f9\state.json | ConvertFrom-Json | Select-Object video_id, total_chunks, current_chunk_index
```

Expected: `video_id`, `total_chunks`, and `current_chunk_index` printed.

---

## Task 7: Commit

```bash
git add tools/gpu_pipeline/progress_tracker.py tools/gpu_pipeline/popen_runner.py tools/gpu_pipeline/gpu_pipeline_monitor.py tools/gpu_pipeline/run_long_gameplay_pipeline_gpu.py docs/plans/2026-04-22-gpu-pipeline-cli-monitor-design.md
git commit -m "feat: add rich CLI monitor with per-stage animated progress bars"
```

---

## Notes / Edge Cases

- **No tests in repo:** Verification is manual smoke-test only (per AGENTS.md).
- **Windows paths:** `state.json` atomic write uses `.rename()` which is atomic on NTFS.
- **FFmpeg stderr parsing:** `frame=\s*(\d+)` regex covers both `frame= 123` and `frame=123`.
- **LivePortrait timing:** First 3 chunks calibrate the estimate; before that, uses 0.1s/frame fallback.
- **Ctrl+C in monitor:** Caught via `KeyboardInterrupt` inside `Live` context manager; terminal is restored cleanly.
