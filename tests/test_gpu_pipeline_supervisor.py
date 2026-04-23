import json
import io
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch


def write_manifest(job_dir: Path, *, video_id: str = "demo-job", statuses: list[str]) -> None:
    (job_dir / "manifests").mkdir(parents=True, exist_ok=True)
    manifest = {
        "video_id": video_id,
        "input_video": "gameplays_crudos/demo.mp4",
        "total_seconds": float(len(statuses) * 60),
        "chunk_seconds": 60,
        "source_master": "john/john_video_45deg_ver4.mp4",
        "profile": {},
        "chunks": [
            {
                "index": index,
                "start_seconds": float(index * 60),
                "duration_seconds": 60.0,
                "status": status,
                "error": None,
            }
            for index, status in enumerate(statuses)
        ],
    }
    (job_dir / "manifests" / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")


def write_report(output_root: Path, video_id: str, total_processing_seconds: float) -> None:
    out_dir = output_root / video_id
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "report.json").write_text(
        json.dumps({"total_processing_seconds": total_processing_seconds}),
        encoding="utf-8",
    )


class WorkerBackoffTests(unittest.TestCase):
    def test_backoff_caps_at_five_minutes(self):
        from tools.gpu_pipeline.supervisor_runtime import WorkerBackoff

        backoff = WorkerBackoff()

        values = [backoff.next_delay_seconds() for _ in range(6)]

        self.assertEqual(values, [10, 30, 60, 120, 300, 300])


class JobSnapshotTests(unittest.TestCase):
    def test_collect_job_snapshot_reports_chunk_counts(self):
        from tools.gpu_pipeline.supervisor_runtime import collect_job_snapshot

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            job_dir = root / ".pipeline_work" / "demo-job"
            write_manifest(job_dir, statuses=["done", "done", "failed", "pending"])

            snapshot = collect_job_snapshot(job_dir)

            self.assertEqual(snapshot.total_chunks, 4)
            self.assertEqual(snapshot.done_chunks, 2)
            self.assertEqual(snapshot.failed_chunks, 1)
            self.assertEqual(snapshot.pending_chunks, 1)
            self.assertEqual(snapshot.current_chunk, 2)
            self.assertEqual(snapshot.chunk_statuses, ["done", "done", "failed", "pending"])

    def test_estimate_eta_seconds_uses_completed_chunks_and_never_goes_negative(self):
        from tools.gpu_pipeline.supervisor_runtime import estimate_eta_seconds

        self.assertEqual(estimate_eta_seconds(total_chunks=5, done_chunks=2, elapsed_seconds=200.0), 300.0)
        self.assertIsNone(estimate_eta_seconds(total_chunks=5, done_chunks=0, elapsed_seconds=200.0))
        self.assertEqual(estimate_eta_seconds(total_chunks=5, done_chunks=6, elapsed_seconds=200.0), 0.0)

    def test_is_job_complete_requires_all_chunks_done_and_final_output(self):
        from tools.gpu_pipeline.supervisor_runtime import is_job_complete, resolve_final_output_path

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            job_dir = root / ".pipeline_work" / "demo-job"
            output_root = root / "output"
            write_manifest(job_dir, statuses=["done", "done"])

            self.assertFalse(is_job_complete(job_dir, output_root))

            visual_only = output_root / "demo-job" / "visual_full.mp4"
            visual_only.parent.mkdir(parents=True, exist_ok=True)
            visual_only.write_bytes(b"video")

            self.assertFalse(is_job_complete(job_dir, output_root))

            final_output = resolve_final_output_path(
                output_root=output_root,
                video_id="demo-job",
                input_video="gameplays_crudos/demo.mp4",
            )
            final_output.parent.mkdir(parents=True, exist_ok=True)
            final_output.write_bytes(b"video")

            self.assertTrue(is_job_complete(job_dir, output_root))

    def test_resolve_final_output_path_uses_original_input_stem(self):
        from tools.gpu_pipeline.supervisor_runtime import resolve_final_output_path

        self.assertEqual(
            resolve_final_output_path(
                Path("output"),
                "RE9-part3-c72b77f9",
                "gameplays_crudos/RE9-part3.mp4",
            ),
            Path("output") / "RE9-part3-c72b77f9" / "RE9-part3_final.mp4",
        )

    def test_detect_worker_status_marks_stalled_when_progress_is_old(self):
        from tools.gpu_pipeline.supervisor_runtime import detect_worker_status

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            job_dir = root / ".pipeline_work" / "demo-job"
            output_root = root / "output"
            write_manifest(job_dir, statuses=["done", "pending"])
            manifest_path = job_dir / "manifests" / "manifest.json"
            stale = time.time() - 3600
            manifest_path.touch()
            os_times = (stale, stale)
            import os
            os.utime(manifest_path, os_times)

            status = detect_worker_status(
                worker_alive=True,
                job_dir=job_dir,
                output_root=output_root,
                stalled_after_seconds=60,
            )

            self.assertEqual(status, "stalled")

    def test_detect_worker_status_does_not_mark_stalled_when_threshold_disabled(self):
        from tools.gpu_pipeline.supervisor_runtime import detect_worker_status

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            job_dir = root / ".pipeline_work" / "demo-job"
            output_root = root / "output"
            write_manifest(job_dir, statuses=["done", "pending"])
            manifest_path = job_dir / "manifests" / "manifest.json"
            stale = time.time() - 3600
            import os
            os.utime(manifest_path, (stale, stale))

            status = detect_worker_status(
                worker_alive=True,
                job_dir=job_dir,
                output_root=output_root,
                stalled_after_seconds=0,
            )

            self.assertEqual(status, "running")

    def test_detect_worker_status_uses_worker_log_activity_as_progress_signal(self):
        from tools.gpu_pipeline.supervisor_runtime import detect_worker_status

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            job_dir = root / ".pipeline_work" / "demo-job"
            output_root = root / "output"
            write_manifest(job_dir, statuses=["done", "pending"])
            manifest_path = job_dir / "manifests" / "manifest.json"
            stale = time.time() - 3600
            import os
            os.utime(manifest_path, (stale, stale))

            log_path = job_dir / "worker.console.log"
            log_path.write_text("still alive", encoding="utf-8")

            status = detect_worker_status(
                worker_alive=True,
                job_dir=job_dir,
                output_root=output_root,
                stalled_after_seconds=60,
            )

            self.assertEqual(status, "running")

    def test_collect_job_snapshot_tolerates_transient_invalid_manifest(self):
        from tools.gpu_pipeline.supervisor_runtime import collect_job_snapshot

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            job_dir = root / ".pipeline_work" / "demo-job"
            (job_dir / "manifests").mkdir(parents=True, exist_ok=True)
            (job_dir / "manifests" / "manifest.json").write_text("{bad json}", encoding="utf-8")

            snapshot = collect_job_snapshot(job_dir)

            self.assertEqual(snapshot.total_chunks, 0)
            self.assertEqual(snapshot.chunk_statuses, [])


class WorkerLaunchTests(unittest.TestCase):
    def test_build_worker_command_uses_venv_python_and_video_name(self):
        from tools.gpu_pipeline.supervisor_runtime import build_worker_command

        command = build_worker_command("RE9-part3.mp4")

        self.assertIn(Path(sys.executable).name, command)
        self.assertIn("LIVEPORTRAIT_DISABLE_PROGRESS_TRACKER", command)
        self.assertIn('run_long_gameplay_pipeline_gpu.py', command)
        self.assertIn("--video 'RE9-part3.mp4'", command)

    def test_build_worker_command_quotes_video_names_with_spaces(self):
        from tools.gpu_pipeline.supervisor_runtime import build_worker_command

        command = build_worker_command("RE9 part3 final.mp4")

        self.assertIn("--video 'RE9 part3 final.mp4'", command)

    def test_launch_worker_terminal_invokes_subprocess_popen(self):
        from tools.gpu_pipeline.supervisor_runtime import launch_worker_terminal

        with tempfile.TemporaryDirectory() as tmp:
            job_dir = Path(tmp) / "demo-job"
            job_dir.mkdir(parents=True, exist_ok=True)

            with patch("tools.gpu_pipeline.supervisor_runtime.subprocess.Popen") as popen:
                launch_worker_terminal("RE9-part3.mp4", job_dir, Path("D:/proyectos/LivePortrait"))

        self.assertTrue(popen.called)

    def test_resolve_shell_exe_falls_back_to_windows_powershell(self):
        from tools.gpu_pipeline.supervisor_runtime import resolve_shell_exe

        with patch("tools.gpu_pipeline.supervisor_runtime.shutil.which") as which:
            which.side_effect = [None, "C:/Windows/System32/WindowsPowerShell/v1.0/powershell.exe"]
            shell = resolve_shell_exe()

        self.assertEqual(shell, "powershell")

    def test_terminate_worker_tree_uses_taskkill_with_process_tree_flag(self):
        from tools.gpu_pipeline.supervisor_runtime import terminate_worker_tree

        class FakeProcess:
            pid = 4321

        with patch("tools.gpu_pipeline.supervisor_runtime.subprocess.run") as run:
            terminate_worker_tree(FakeProcess())

        run.assert_called_once()
        args = run.call_args.args[0]
        self.assertEqual(args[:3], ["taskkill", "/T", "/F"])
        self.assertEqual(args[-2:], ["/PID", "4321"])


class SupervisorUiTests(unittest.TestCase):
    def test_build_layout_returns_rich_layout_and_shows_named_final_output(self):
        from tools.gpu_pipeline.gpu_pipeline_supervisor import build_layout
        from tools.gpu_pipeline.supervisor_runtime import JobSnapshot
        from rich.console import Console

        snapshot = JobSnapshot(
            total_chunks=4,
            done_chunks=2,
            pending_chunks=1,
            failed_chunks=1,
            current_chunk=2,
            chunk_statuses=["done", "done", "failed", "pending"],
            eta_seconds=300.0,
            last_progress_at=None,
        )

        layout = build_layout(
            video_name="RE9-part3.mp4",
            worker_status="backoff",
            restart_count=2,
            snapshot=snapshot,
            events=["20:58 worker started", "21:14 restart scheduled in 30s"],
            final_output=Path("output/RE9-part3-c72b77f9/RE9-part3_final.mp4"),
        )

        self.assertEqual(layout.__class__.__name__, "Layout")
        console = Console(record=True, width=160, file=io.StringIO(), force_terminal=False)
        console.print(layout)
        rendered = console.export_text()
        self.assertIn("RE9-part3_final.mp4", rendered)

    def test_supervisor_script_help_runs_successfully(self):
        result = subprocess.run(
            [
                sys.executable,
                "tools/gpu_pipeline/gpu_pipeline_supervisor.py",
                "--help",
            ],
            cwd=Path(__file__).resolve().parents[1],
            capture_output=True,
            text=True,
        )

        self.assertEqual(result.returncode, 0, msg=result.stderr)
        self.assertIn("GPU pipeline supervisor", result.stdout)
