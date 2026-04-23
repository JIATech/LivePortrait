# GPU Pipeline Supervisor TUI Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Build a resilient supervisor TUI that launches the GPU pipeline worker in a separate visible terminal, tracks chunk progress and ETA, detects worker crashes/stalls, and relaunches automatically with infinite backoff.

**Architecture:** The supervisor owns process lifecycle and reads chunk progress from `manifest.json` and `report.json` instead of depending on hot-path tracker writes. The worker remains resumable and disposable; if it dies, the supervisor relaunches it and the pipeline resumes from the failed chunk.

**Tech Stack:** Python 3.13, `rich`, PowerShell process management on Windows, existing `tools/pipeline_runtime.py` manifest format.

---

### Task 1: Add supervisor runtime tests

**Files:**
- Create: `tests/test_gpu_pipeline_supervisor.py`

**Step 1: Write the failing tests**

```python
import tempfile
import unittest
from pathlib import Path

from tools.gpu_pipeline.supervisor_runtime import (
    WorkerBackoff,
    collect_job_snapshot,
    detect_worker_status,
    is_job_complete,
)


class WorkerBackoffTests(unittest.TestCase):
    def test_backoff_caps_at_five_minutes(self):
        backoff = WorkerBackoff()

        values = [backoff.next_delay_seconds() for _ in range(6)]

        self.assertEqual(values, [10, 30, 60, 120, 300, 300])


class JobSnapshotTests(unittest.TestCase):
    def test_collect_job_snapshot_reports_chunk_counts_and_eta(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            # write manifest/report fixtures here
            snapshot = collect_job_snapshot(root)
            self.assertEqual(snapshot.done_chunks, 2)
            self.assertEqual(snapshot.total_chunks, 5)
            self.assertGreaterEqual(snapshot.eta_seconds, 0.0)

    def test_is_job_complete_requires_all_chunks_done_and_final_output(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            # write completed manifest without final output
            self.assertFalse(is_job_complete(root))

    def test_detect_worker_status_marks_stalled_when_progress_age_exceeds_threshold(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            # write manifest/report older than threshold
            status = detect_worker_status(worker_alive=True, job_dir=root, stalled_after_seconds=60)
            self.assertEqual(status, "stalled")
```

**Step 2: Run the tests to verify they fail**

Run:
```bash
.venv\Scripts\python -m unittest tests.test_gpu_pipeline_supervisor -v
```

Expected: FAIL with `ModuleNotFoundError` for `tools.gpu_pipeline.supervisor_runtime`

**Step 3: Commit**

```bash
git add tests/test_gpu_pipeline_supervisor.py
git commit -m "test: add supervisor runtime expectations"
```

---

### Task 2: Implement supervisor runtime helpers

**Files:**
- Create: `tools/gpu_pipeline/supervisor_runtime.py`
- Test: `tests/test_gpu_pipeline_supervisor.py`

**Step 1: Write minimal implementation**

```python
from __future__ import annotations

import json
import time
from dataclasses import dataclass
from pathlib import Path


@dataclass
class JobSnapshot:
    total_chunks: int
    done_chunks: int
    pending_chunks: int
    failed_chunks: int
    current_chunk: int | None
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


def _load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def collect_job_snapshot(job_dir: Path) -> JobSnapshot:
    manifest = _load_json(job_dir / "manifests" / "manifest.json")
    chunks = manifest["chunks"]
    done = sum(1 for chunk in chunks if chunk["status"] == "done")
    failed = sum(1 for chunk in chunks if chunk["status"] == "failed")
    pending = sum(1 for chunk in chunks if chunk["status"] == "pending")
    current = next((chunk["index"] for chunk in chunks if chunk["status"] != "done"), None)

    eta_seconds = None
    if done:
        report_path = job_dir.parent.parent / "output" / manifest["video_id"] / "report.json"
        if report_path.exists():
            report = _load_json(report_path)
            avg_chunk = float(report.get("total_processing_seconds", 0.0)) / done
            eta_seconds = max(0.0, avg_chunk * (len(chunks) - done))

    manifest_mtime = (job_dir / "manifests" / "manifest.json").stat().st_mtime
    return JobSnapshot(
        total_chunks=len(chunks),
        done_chunks=done,
        pending_chunks=pending,
        failed_chunks=failed,
        current_chunk=current,
        eta_seconds=eta_seconds,
        last_progress_at=manifest_mtime,
    )


def is_job_complete(job_dir: Path) -> bool:
    manifest = _load_json(job_dir / "manifests" / "manifest.json")
    if any(chunk["status"] != "done" for chunk in manifest["chunks"]):
        return False
    final_output = Path("output") / manifest["video_id"] / "final.mp4"
    return final_output.exists()


def detect_worker_status(worker_alive: bool, job_dir: Path, stalled_after_seconds: int) -> str:
    if is_job_complete(job_dir):
        return "completed"
    if not worker_alive:
        return "crashed"
    snapshot = collect_job_snapshot(job_dir)
    if snapshot.last_progress_at is not None:
        age = time.time() - snapshot.last_progress_at
        if age > stalled_after_seconds:
            return "stalled"
    return "running"
```

**Step 2: Run the tests to verify they pass**

Run:
```bash
.venv\Scripts\python -m unittest tests.test_gpu_pipeline_supervisor -v
```

Expected: PASS

**Step 3: Commit**

```bash
git add tools/gpu_pipeline/supervisor_runtime.py tests/test_gpu_pipeline_supervisor.py
git commit -m "feat: add supervisor runtime helpers"
```

---

### Task 3: Add worker launch abstraction tests

**Files:**
- Modify: `tests/test_gpu_pipeline_supervisor.py`

**Step 1: Add failing tests**

```python
from unittest.mock import patch

from tools.gpu_pipeline.supervisor_runtime import build_worker_command, launch_worker_terminal


class WorkerLaunchTests(unittest.TestCase):
    def test_build_worker_command_uses_venv_python_and_video_name(self):
        command = build_worker_command("RE9-part3.mp4")
        self.assertIn(".venv\\Scripts\\python.exe", command)
        self.assertIn("run_long_gameplay_pipeline_gpu.py --video RE9-part3.mp4", command)

    def test_launch_worker_terminal_invokes_powershell_start_process(self):
        with patch("tools.gpu_pipeline.supervisor_runtime.subprocess.Popen") as popen:
            launch_worker_terminal("RE9-part3.mp4", Path(".pipeline_work/demo-job"))
        self.assertTrue(popen.called)
```

**Step 2: Run just this test block**

Run:
```bash
.venv\Scripts\python -m unittest tests.test_gpu_pipeline_supervisor.WorkerLaunchTests -v
```

Expected: FAIL with missing symbols

**Step 3: Commit**

```bash
git add tests/test_gpu_pipeline_supervisor.py
git commit -m "test: define worker launch contract"
```

---

### Task 4: Implement worker launch helpers

**Files:**
- Modify: `tools/gpu_pipeline/supervisor_runtime.py`
- Test: `tests/test_gpu_pipeline_supervisor.py`

**Step 1: Add minimal launch helpers**

```python
import subprocess


def build_worker_command(video_name: str) -> str:
    return (
        f".venv\\Scripts\\python.exe tools/gpu_pipeline/run_long_gameplay_pipeline_gpu.py "
        f"--video {video_name}"
    )


def launch_worker_terminal(video_name: str, job_dir: Path):
    stdout_log = job_dir / "worker.stdout.log"
    stderr_log = job_dir / "worker.stderr.log"
    command = build_worker_command(video_name)
    ps_command = (
        "Start-Process pwsh "
        f"-ArgumentList '-NoExit','-Command','Set-Location {Path.cwd()}; {command}' "
        f"-RedirectStandardOutput '{stdout_log}' "
        f"-RedirectStandardError '{stderr_log}' "
        "-PassThru"
    )
    return subprocess.Popen(["pwsh", "-NoProfile", "-Command", ps_command])
```

**Step 2: Run the tests**

Run:
```bash
.venv\Scripts\python -m unittest tests.test_gpu_pipeline_supervisor -v
```

Expected: PASS

**Step 3: Commit**

```bash
git add tools/gpu_pipeline/supervisor_runtime.py tests/test_gpu_pipeline_supervisor.py
git commit -m "feat: add worker terminal launch helpers"
```

---

### Task 5: Add supervisor TUI tests

**Files:**
- Modify: `tests/test_gpu_pipeline_supervisor.py`

**Step 1: Add failing UI tests**

```python
from tools.gpu_pipeline.gpu_pipeline_supervisor import build_layout, format_event


class SupervisorUiTests(unittest.TestCase):
    def test_build_layout_returns_rich_layout(self):
        snapshot = JobSnapshot(
            total_chunks=10,
            done_chunks=4,
            pending_chunks=6,
            failed_chunks=0,
            current_chunk=4,
            eta_seconds=3600.0,
            last_progress_at=None,
        )
        layout = build_layout(
            video_name="RE9-part3.mp4",
            worker_status="running",
            restart_count=2,
            snapshot=snapshot,
            events=["worker restarted"],
        )
        self.assertEqual(layout.__class__.__name__, "Layout")
```

**Step 2: Run only the UI test**

Run:
```bash
.venv\Scripts\python -m unittest tests.test_gpu_pipeline_supervisor.SupervisorUiTests -v
```

Expected: FAIL with missing module `tools.gpu_pipeline.gpu_pipeline_supervisor`

**Step 3: Commit**

```bash
git add tests/test_gpu_pipeline_supervisor.py
git commit -m "test: define supervisor tui rendering contract"
```

---

### Task 6: Implement supervisor TUI entrypoint

**Files:**
- Create: `tools/gpu_pipeline/gpu_pipeline_supervisor.py`
- Test: `tests/test_gpu_pipeline_supervisor.py`

**Step 1: Write minimal TUI implementation**

```python
import argparse
import time
from collections import deque
from pathlib import Path

from rich.layout import Layout
from rich.live import Live
from rich.panel import Panel
from rich.table import Table

from tools.gpu_pipeline.supervisor_runtime import (
    WorkerBackoff,
    collect_job_snapshot,
    detect_worker_status,
    launch_worker_terminal,
)
from tools.watch_pipeline_progress import resolve_job_dir


def build_layout(video_name: str, worker_status: str, restart_count: int, snapshot, events: list[str]) -> Layout:
    layout = Layout()
    header = Panel(
        f"{video_name} | worker: {worker_status} | restarts: {restart_count} | "
        f"chunk: {snapshot.current_chunk if snapshot.current_chunk is not None else '-'} / {snapshot.total_chunks} | "
        f"ETA: {snapshot.eta_seconds if snapshot.eta_seconds is not None else 'calculating...'}",
        title="GPU Pipeline Supervisor",
    )

    summary = Panel(
        f"done={snapshot.done_chunks} pending={snapshot.pending_chunks} failed={snapshot.failed_chunks}",
        title="Chunks",
    )

    chunk_table = Table(show_header=False, box=None)
    for index in range(snapshot.total_chunks):
        if index < snapshot.done_chunks:
            status = "DONE"
        elif snapshot.current_chunk == index:
            status = worker_status.upper()
        else:
            status = "PENDING"
        chunk_table.add_row(f"Chunk {index:02d}", status)

    event_table = Table(show_header=False, box=None)
    for event in events[-10:]:
        event_table.add_row(event)

    layout.split_column(
        Layout(header, size=3),
        Layout(Panel(summary, title="Summary"), size=3),
        Layout(Panel(chunk_table, title="Chunk Grid")),
        Layout(Panel(event_table, title="Events"), size=12),
    )
    return layout


def main() -> None:
    parser = argparse.ArgumentParser(description="GPU pipeline supervisor")
    parser.add_argument("--work-dir", default=".pipeline_work")
    parser.add_argument("--video", required=True)
    parser.add_argument("--stalled-after-seconds", type=int, default=1200)
    args = parser.parse_args()

    job_dir = resolve_job_dir(Path(args.work_dir), args.video)
    events = deque(maxlen=20)
    backoff = WorkerBackoff()
    restart_count = 0
    launch_worker_terminal(args.video, job_dir)
    events.append("worker started")

    with Live(refresh_per_second=2, screen=True) as live:
        while True:
            snapshot = collect_job_snapshot(job_dir)
            worker_status = detect_worker_status(False, job_dir, args.stalled_after_seconds)
            live.update(build_layout(args.video, worker_status, restart_count, snapshot, list(events)))
            time.sleep(0.5)
```

**Step 2: Run the tests**

Run:
```bash
.venv\Scripts\python -m unittest tests.test_gpu_pipeline_supervisor -v
```

Expected: PASS or fail only on behaviors not yet implemented in later tasks

**Step 3: Run help command**

Run:
```bash
.venv\Scripts\python tools/gpu_pipeline/gpu_pipeline_supervisor.py --help
```

Expected: help text prints with `--video`

**Step 4: Commit**

```bash
git add tools/gpu_pipeline/gpu_pipeline_supervisor.py tests/test_gpu_pipeline_supervisor.py
git commit -m "feat: add supervisor tui entrypoint"
```

---

### Task 7: Implement real restart and stall supervision

**Files:**
- Modify: `tools/gpu_pipeline/supervisor_runtime.py`
- Modify: `tools/gpu_pipeline/gpu_pipeline_supervisor.py`
- Test: `tests/test_gpu_pipeline_supervisor.py`

**Step 1: Add failing behavior tests**

```python
class SupervisorRestartTests(unittest.TestCase):
    def test_supervisor_schedules_restart_after_worker_crash(self):
        # mock dead worker, incomplete manifest, verify restart event and delay
        ...

    def test_supervisor_marks_stalled_and_restarts_worker(self):
        # mock alive worker with stale manifest timestamp
        ...
```

**Step 2: Run tests and confirm failure**

Run:
```bash
.venv\Scripts\python -m unittest tests.test_gpu_pipeline_supervisor.SupervisorRestartTests -v
```

Expected: FAIL with missing restart behavior

**Step 3: Implement minimal restart loop**

Key behaviors:
- store worker handle / pid
- if worker dies and job incomplete:
  - append `worker crashed`
  - sleep backoff seconds
  - relaunch worker
  - increment restart count
- if worker stalls:
  - terminate worker
  - append `worker stalled`
  - relaunch using same backoff policy
- if job completes:
  - append `job completed`
  - stop restart loop

**Step 4: Run full supervisor test suite**

Run:
```bash
.venv\Scripts\python -m unittest tests.test_gpu_pipeline_supervisor -v
```

Expected: PASS

**Step 5: Commit**

```bash
git add tools/gpu_pipeline/supervisor_runtime.py tools/gpu_pipeline/gpu_pipeline_supervisor.py tests/test_gpu_pipeline_supervisor.py
git commit -m "feat: supervise worker crashes and stalls with infinite backoff"
```

---

### Task 8: Remove hot-path dependency on fragile tracker

**Files:**
- Modify: `tools/gpu_pipeline/run_long_gameplay_pipeline_gpu.py`
- Modify: `tools/gpu_pipeline/progress_tracker.py`
- Test: `tests/test_gpu_progress_tracker.py`

**Step 1: Write failing regression test**

Add a test that proves the pipeline logic can proceed even if tracker flush always fails.

**Step 2: Run test to confirm failure**

Run:
```bash
.venv\Scripts\python -m unittest tests.test_gpu_progress_tracker -v
```

Expected: FAIL on pipeline/tracker coupling

**Step 3: Implement minimal fix**

- make tracker strictly best-effort
- ensure all tracker write failures are swallowed
- optionally stop calling tracker from the real worker path if supervisor now covers operational needs

**Step 4: Run regression tests**

Run:
```bash
.venv\Scripts\python -m unittest tests.test_gpu_progress_tracker -v
```

Expected: PASS

**Step 5: Commit**

```bash
git add tools/gpu_pipeline/run_long_gameplay_pipeline_gpu.py tools/gpu_pipeline/progress_tracker.py tests/test_gpu_progress_tracker.py
git commit -m "fix: make progress tracking non-fatal for pipeline execution"
```

---

### Task 9: End-to-end resilience smoke test

**Files:**
- No code changes required unless failures appear

**Step 1: Start supervisor on short video**

Run:
```bash
.venv\Scripts\python tools/gpu_pipeline/gpu_pipeline_supervisor.py --video test_smoke.mp4
```

Expected:
- TUI opens
- worker terminal opens automatically
- events show `worker started`

**Step 2: Kill worker manually during processing**

Run in another terminal:
```bash
taskkill /F /IM python.exe
```

Expected:
- supervisor marks worker as crashed
- event log shows restart scheduling
- after backoff, worker terminal reopens
- pipeline resumes from failed chunk

**Step 3: Verify final output exists**

Run:
```bash
ls output\test_smoke-*\final.mp4
```

Expected: final output file exists

**Step 4: Optional long-run test on real video**

Run:
```bash
.venv\Scripts\python tools/gpu_pipeline/gpu_pipeline_supervisor.py --video RE9-part3.mp4
```

Expected:
- chunks complete sequentially
- ETA appears after first chunk
- if worker is closed, it relaunches automatically

**Step 5: Commit**

```bash
git add .
git commit -m "test: verify supervisor resilience end to end"
```

---

### Task 10: Final verification

**Files:**
- Verify all touched files

**Step 1: Run all relevant tests**

Run:
```bash
.venv\Scripts\python -m unittest \
  tests.test_gpu_pipeline_supervisor \
  tests.test_gpu_progress_tracker \
  tests.test_run_long_gameplay_pipeline \
  tests.test_watch_pipeline_progress -v
```

Expected: all pass

**Step 2: Compile modified modules**

Run:
```bash
.venv\Scripts\python -m py_compile \
  tools/gpu_pipeline/supervisor_runtime.py \
  tools/gpu_pipeline/gpu_pipeline_supervisor.py \
  tools/gpu_pipeline/run_long_gameplay_pipeline_gpu.py \
  tools/gpu_pipeline/progress_tracker.py
```

Expected: no output

**Step 3: Final commit**

```bash
git add tools/gpu_pipeline/ tests/ docs/plans/
git commit -m "feat: add resilient gpu pipeline supervisor tui"
```
