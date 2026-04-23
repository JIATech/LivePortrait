import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from tools.pipeline_runtime import ChunkRecord, JobManifest
from tools.gpu_pipeline.run_long_gameplay_pipeline_gpu import (
    assemble_final_output,
    build_job_paths,
    progress_tracking_enabled,
    resolve_shell_exe,
)


class FakeRunner:
    def __init__(self):
        self.commands = []

    def __call__(self, command: str) -> None:
        self.commands.append(command)


class GpuPipelineProgressTrackingTests(unittest.TestCase):
    @patch.dict(os.environ, {}, clear=True)
    def test_progress_tracking_enabled_by_default(self):
        self.assertTrue(progress_tracking_enabled())

    @patch.dict(os.environ, {"LIVEPORTRAIT_DISABLE_PROGRESS_TRACKER": "1"}, clear=True)
    def test_progress_tracking_can_be_disabled_for_supervised_runs(self):
        self.assertFalse(progress_tracking_enabled())

    @patch("tools.gpu_pipeline.run_long_gameplay_pipeline_gpu.shutil.which")
    def test_resolve_shell_exe_falls_back_to_powershell(self, which):
        which.side_effect = [None, "C:/Windows/System32/WindowsPowerShell/v1.0/powershell.exe"]

        self.assertEqual(resolve_shell_exe(), "powershell")


class GpuPipelineOutputNamingTests(unittest.TestCase):
    def test_build_job_paths_uses_input_stem_for_final_output_name(self):
        paths = build_job_paths(
            work_dir=Path(".pipeline_work"),
            output_dir=Path("output/RE9-part3-c72b77f9"),
            job_id="RE9-part3-c72b77f9",
            input_video=Path("gameplays_crudos/RE9-part3.mp4"),
        )

        self.assertEqual(
            paths.final_video,
            Path("output/RE9-part3-c72b77f9") / "RE9-part3_final.mp4",
        )

    def test_assemble_final_output_writes_input_stem_final_video(self):
        manifest = JobManifest(
            video_id="RE9-part3-c72b77f9",
            input_video="gameplays_crudos/RE9-part3.mp4",
            total_seconds=120.0,
            chunk_seconds=60,
            source_master="john/john_video_45deg_ver4.mp4",
            chunks=[
                ChunkRecord(index=0, start_seconds=0.0, duration_seconds=60.0, status="done"),
                ChunkRecord(index=1, start_seconds=60.0, duration_seconds=60.0, status="done"),
            ],
        )

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            runner = FakeRunner()

            final_video = assemble_final_output(
                job_dir=root / ".pipeline_work" / manifest.video_id,
                input_video=Path(manifest.input_video),
                output_dir=root / "output" / manifest.video_id,
                manifest=manifest,
                runner=runner,
            )

            self.assertEqual(
                final_video,
                root / "output" / manifest.video_id / "RE9-part3_final.mp4",
            )
            self.assertEqual(len(runner.commands), 2)
            self.assertIn("visual_full.mp4", runner.commands[0])
            self.assertIn("RE9-part3_final.mp4", runner.commands[1])
