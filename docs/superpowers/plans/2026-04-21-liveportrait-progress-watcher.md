# LivePortrait Progress Watcher Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build standalone CLI watcher that shows pipeline progress locally in `status` and `watch` modes without depending on assistant session.

**Architecture:** Add one focused tool, `tools/watch_pipeline_progress.py`, that reads `.pipeline_work/<video_id>/manifests/manifest.json` plus nearby chunk/output artifacts, computes a small serializable status model, then renders it either once (`status`) or in a refresh loop (`watch`). Keep ETA heuristic intentionally simple: derive it from completed chunks only.

**Tech Stack:** Python 3.11, stdlib (`argparse`, `json`, `time`, `datetime`, `pathlib`), existing `tools.pipeline_runtime.py`, stdlib `unittest`

---

## File structure

- Create: `tools/watch_pipeline_progress.py` — standalone watcher CLI with `status` and `watch` subcommands, pure status collection helpers, text renderer, and job resolution.
- Create: `tests/test_watch_pipeline_progress.py` — unit tests for job resolution, status metrics, ETA behavior, renderer output, and CLI entrypoint behavior.

## Shared implementation notes

- Watcher must read local disk state only. No dependency on assistant session, no remote state, no background agent.
- V1 data sources: manifest JSON + file timestamps/sizes + final output existence. Logs optional later, not required now.
- `status` must print once and exit 0.
- `watch` must refresh until interrupted with `Ctrl+C`.
- If no chunks are `done`, ETA must print `unknown`.
- If video/job not found, output must be explicit and actionable.

### Task 1: Status model and job resolution

**Files:**
- Create: `tools/watch_pipeline_progress.py`
- Test: `tests/test_watch_pipeline_progress.py`

- [ ] **Step 1: Write the failing tests**

```python
import json
import tempfile
import unittest
from pathlib import Path

from tools.watch_pipeline_progress import collect_pipeline_status, resolve_job_dir


class WatchPipelineProgressTests(unittest.TestCase):
    def test_resolve_job_dir_maps_video_name_to_existing_job_dir(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            job_dir = root / "RE9-part1-3153aeea"
            (job_dir / "manifests").mkdir(parents=True)
            manifest = {
                "video_id": "RE9-part1-3153aeea",
                "input_video": "gameplays_crudos/RE9-part1.mp4",
                "total_seconds": 120.0,
                "chunk_seconds": 60,
                "source_master": "john/john_video_45deg_ver4.mp4",
                "profile": {},
                "chunks": [],
            }
            (job_dir / "manifests" / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")

            resolved = resolve_job_dir(work_dir=root, video_name="RE9-part1.mp4")

            self.assertEqual(resolved, job_dir)

    def test_collect_pipeline_status_reports_counts_percent_and_eta(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            job_dir = root / "RE9-part1-3153aeea"
            manifests = job_dir / "manifests"
            manifests.mkdir(parents=True)
            manifest = {
                "video_id": "RE9-part1-3153aeea",
                "input_video": "gameplays_crudos/RE9-part1.mp4",
                "total_seconds": 180.0,
                "chunk_seconds": 60,
                "source_master": "john/john_video_45deg_ver4.mp4",
                "profile": {},
                "chunks": [
                    {"index": 0, "start_seconds": 0.0, "duration_seconds": 60.0, "status": "done", "error": None},
                    {"index": 1, "start_seconds": 60.0, "duration_seconds": 60.0, "status": "done", "error": None},
                    {"index": 2, "start_seconds": 120.0, "duration_seconds": 60.0, "status": "pending", "error": None},
                ],
            }
            (manifests / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")

            report = {
                "input_video": "gameplays_crudos/RE9-part1.mp4",
                "source_master": "john/john_video_45deg_ver4.mp4",
                "total_duration_seconds": 180.0,
                "chunk_count": 3,
                "processed_chunks": 2,
                "failed_chunks": [],
                "total_processing_seconds": 7200.0,
                "config_path": "pipeline_profiles/liveportrait_gameplay_v1.json",
                "execution_timestamp": "2026-04-21T10:00:00+00:00",
            }
            (job_dir / "output").mkdir(parents=True)
            (job_dir / "output" / "report.json").write_text(json.dumps(report), encoding="utf-8")

            status = collect_pipeline_status(job_dir)

            self.assertEqual(status["done_chunks"], 2)
            self.assertEqual(status["pending_chunks"], 1)
            self.assertEqual(status["failed_chunks"], 0)
            self.assertAlmostEqual(status["percent_complete"], 66.67, places=2)
            self.assertEqual(status["current_chunk"], 2)
            self.assertEqual(status["eta_seconds"], 3600.0)
```

- [ ] **Step 2: Run tests to verify RED**

Run:

```powershell
& ".venv311\Scripts\python.exe" -m unittest tests.test_watch_pipeline_progress -v
```

Expected:
- FAIL with `ModuleNotFoundError: No module named 'tools.watch_pipeline_progress'`

- [ ] **Step 3: Write minimal status collection implementation**

```python
import json
from pathlib import Path


def resolve_job_dir(work_dir: Path, video_name: str) -> Path:
    for manifest_path in work_dir.glob("*/manifests/manifest.json"):
        data = json.loads(manifest_path.read_text(encoding="utf-8"))
        if Path(data["input_video"]).name == video_name:
            return manifest_path.parent.parent
    raise FileNotFoundError(f"No pipeline job found for video: {video_name}")


def collect_pipeline_status(job_dir: Path) -> dict[str, object]:
    manifest = json.loads((job_dir / "manifests" / "manifest.json").read_text(encoding="utf-8"))
    chunks = manifest["chunks"]
    done = sum(1 for chunk in chunks if chunk["status"] == "done")
    failed = sum(1 for chunk in chunks if chunk["status"] == "failed")
    pending = sum(1 for chunk in chunks if chunk["status"] == "pending")
    total = len(chunks)
    current_chunk = next((chunk["index"] for chunk in chunks if chunk["status"] != "done"), "completed")

    report_path = job_dir / "output" / "report.json"
    report = json.loads(report_path.read_text(encoding="utf-8")) if report_path.exists() else {}
    total_processing_seconds = float(report.get("total_processing_seconds", 0.0))
    eta_seconds = None
    if done > 0:
        eta_seconds = (total_processing_seconds / done) * pending

    return {
        "video_id": manifest["video_id"],
        "input_video": manifest["input_video"],
        "total_chunks": total,
        "done_chunks": done,
        "pending_chunks": pending,
        "failed_chunks": failed,
        "percent_complete": round((done / total) * 100, 2) if total else 0.0,
        "current_chunk": current_chunk,
        "eta_seconds": eta_seconds,
    }
```

- [ ] **Step 4: Run tests to verify GREEN**

Run:

```powershell
& ".venv311\Scripts\python.exe" -m unittest tests.test_watch_pipeline_progress -v
```

Expected:
- PASS for both tests

- [ ] **Step 5: Commit only if user later asks**

```bash
git add tools/watch_pipeline_progress.py tests/test_watch_pipeline_progress.py
git commit -m "feat: add pipeline progress status collector"
```

### Task 2: Renderer and `status` subcommand

**Files:**
- Modify: `tools/watch_pipeline_progress.py`
- Modify: `tests/test_watch_pipeline_progress.py`

- [ ] **Step 1: Add failing tests for renderer and one-shot CLI**

```python
from tools.watch_pipeline_progress import format_status_text, parse_args


def test_format_status_text_includes_key_fields(self):
    text = format_status_text(
        {
            "video_id": "RE9-part1-3153aeea",
            "input_video": "gameplays_crudos/RE9-part1.mp4",
            "total_chunks": 10,
            "done_chunks": 4,
            "pending_chunks": 6,
            "failed_chunks": 0,
            "percent_complete": 40.0,
            "current_chunk": 4,
            "eta_seconds": 3600.0,
            "last_artifact": ".pipeline_work/.../chunk_004_roi.pkl",
            "final_output": None,
            "last_error": None,
        }
    )
    self.assertIn("video_id: RE9-part1-3153aeea", text)
    self.assertIn("done: 4", text)
    self.assertIn("ETA: 3600.0s", text)


def test_parse_args_accepts_status_subcommand(self):
    args = parse_args(["status", "--work-dir", ".pipeline_work", "--video", "RE9-part1.mp4"])
    self.assertEqual(args.mode, "status")
    self.assertEqual(args.video, "RE9-part1.mp4")
```

- [ ] **Step 2: Run tests to verify RED**

Run:

```powershell
& ".venv311\Scripts\python.exe" -m unittest tests.test_watch_pipeline_progress -v
```

Expected:
- FAIL because renderer / parser extensions do not exist yet

- [ ] **Step 3: Implement renderer and status CLI plumbing**

```python
import argparse


def format_seconds(value: float | None) -> str:
    if value is None:
        return "unknown"
    return f"{value:.1f}s"


def format_status_text(status: dict[str, object]) -> str:
    return "\n".join(
        [
            f"video_id: {status['video_id']}",
            f"input_video: {status['input_video']}",
            f"chunks: {status['done_chunks']} done / {status['pending_chunks']} pending / {status['failed_chunks']} failed / {status['total_chunks']} total",
            f"progress: {status['percent_complete']}%",
            f"current_chunk: {status['current_chunk']}",
            f"ETA: {format_seconds(status['eta_seconds'])}",
            f"last_artifact: {status.get('last_artifact', 'unknown')}",
            f"final_output: {status.get('final_output') or 'not ready'}",
            f"last_error: {status.get('last_error') or 'none'}",
        ]
    )


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description="Watch LivePortrait pipeline progress")
    sub = parser.add_subparsers(dest="mode", required=True)

    status_parser = sub.add_parser("status")
    status_parser.add_argument("--work-dir", default=".pipeline_work")
    status_parser.add_argument("--video", required=True)

    watch_parser = sub.add_parser("watch")
    watch_parser.add_argument("--work-dir", default=".pipeline_work")
    watch_parser.add_argument("--video", required=True)
    watch_parser.add_argument("--interval", type=int, default=10)

    return parser.parse_args(argv)
```

- [ ] **Step 4: Run tests to verify GREEN**

Run:

```powershell
& ".venv311\Scripts\python.exe" -m unittest tests.test_watch_pipeline_progress -v
```

Expected:
- PASS with renderer and parser tests green

- [ ] **Step 5: Commit only if user later asks**

```bash
git add tools/watch_pipeline_progress.py tests/test_watch_pipeline_progress.py
git commit -m "feat: add pipeline progress status command"
```

### Task 3: `watch` loop and artifact/error enrichment

**Files:**
- Modify: `tools/watch_pipeline_progress.py`
- Modify: `tests/test_watch_pipeline_progress.py`

- [ ] **Step 1: Add failing tests for watch behavior and enriched status fields**

```python
from unittest.mock import patch

from tools.watch_pipeline_progress import find_last_artifact, main


def test_find_last_artifact_prefers_most_recent_chunk_file(self):
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        a = root / "chunk_a.mp4"
        b = root / "chunk_b.mp4"
        a.write_bytes(b"a")
        b.write_bytes(b"b")
        os.utime(a, (1, 1))
        os.utime(b, (2, 2))
        self.assertEqual(find_last_artifact([a, b]), b)


def test_main_watch_runs_until_keyboard_interrupt(self):
    calls = []

    def fake_collect(_job_dir):
        calls.append("collect")
        return {
            "video_id": "demo",
            "input_video": "demo.mp4",
            "total_chunks": 1,
            "done_chunks": 0,
            "pending_chunks": 1,
            "failed_chunks": 0,
            "percent_complete": 0.0,
            "current_chunk": 0,
            "eta_seconds": None,
            "last_artifact": None,
            "final_output": None,
            "last_error": None,
        }

    with patch("tools.watch_pipeline_progress.collect_pipeline_status", side_effect=fake_collect), patch(
        "tools.watch_pipeline_progress.resolve_job_dir",
        return_value=Path(".pipeline_work/demo"),
    ), patch("tools.watch_pipeline_progress.time.sleep", side_effect=KeyboardInterrupt), patch(
        "builtins.print"
    ):
        main(["watch", "--work-dir", ".pipeline_work", "--video", "demo.mp4", "--interval", "1"])

    self.assertEqual(calls, ["collect"])
```

- [ ] **Step 2: Run tests to verify RED**

Run:

```powershell
& ".venv311\Scripts\python.exe" -m unittest tests.test_watch_pipeline_progress -v
```

Expected:
- FAIL because watch loop / artifact helpers are missing

- [ ] **Step 3: Implement enriched status collection and watch loop**

```python
import os
import time


def find_last_artifact(paths: list[Path]) -> Path | None:
    existing = [path for path in paths if path.exists()]
    if not existing:
        return None
    return max(existing, key=lambda path: path.stat().st_mtime)


def collect_pipeline_status(job_dir: Path) -> dict[str, object]:
    ...
    artifacts = list((job_dir / "chunks").glob("**/*.mp4")) + list((job_dir / "chunks").glob("**/*.pkl"))
    last_artifact = find_last_artifact(artifacts)
    final_output = job_dir.parent.parent / "output" / manifest["video_id"] / "final.mp4"
    last_error = next((chunk["error"] for chunk in chunks if chunk.get("error")), None)
    return {
        ...,
        "last_artifact": last_artifact.as_posix() if last_artifact else None,
        "final_output": final_output.as_posix() if final_output.exists() else None,
        "last_error": last_error,
    }


def clear_screen() -> None:
    print("\033[2J\033[H", end="")


def main(argv=None):
    args = parse_args(argv)
    job_dir = resolve_job_dir(Path(args.work_dir), args.video)

    if args.mode == "status":
        print(format_status_text(collect_pipeline_status(job_dir)))
        return

    try:
        while True:
            clear_screen()
            print(format_status_text(collect_pipeline_status(job_dir)))
            time.sleep(args.interval)
    except KeyboardInterrupt:
        return
```

- [ ] **Step 4: Run tests to verify GREEN**

Run:

```powershell
& ".venv311\Scripts\python.exe" -m unittest tests.test_watch_pipeline_progress -v
```

Expected:
- PASS with watch-mode tests green

- [ ] **Step 5: Manual smoke check on existing RE9 job**

Run:

```powershell
& ".venv311\Scripts\python.exe" tools/watch_pipeline_progress.py status --work-dir .pipeline_work --video RE9-part1.mp4
```

Expected:
- Prints one compact status snapshot
- Includes chunk counts and current chunk guess

- [ ] **Step 6: Commit only if user later asks**

```bash
git add tools/watch_pipeline_progress.py tests/test_watch_pipeline_progress.py
git commit -m "feat: add standalone pipeline progress watcher"
```

## Self-review

- **Spec coverage:**
  - `status` and `watch` modes → Tasks 2 and 3
  - Local-only data sources → Tasks 1 and 3
  - Output contract fields → Tasks 1, 2, 3
  - ETA heuristic from done chunks → Task 1
  - Explicit UX / non-directory and missing-job handling → Tasks 1 and 3
- **Placeholder scan:** No placeholders, no deferred “add validation later” steps.
- **Type consistency:** `resolve_job_dir`, `collect_pipeline_status`, `format_status_text`, `find_last_artifact`, and `main` are named consistently across tasks.
