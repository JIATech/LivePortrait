import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from tools.gpu_pipeline.progress_tracker import ProgressTracker
from tools.pipeline_runtime import ChunkRecord, JobManifest


def build_manifest(chunk_count: int = 2) -> JobManifest:
    chunks = [
        ChunkRecord(index=index, start_seconds=float(index * 60), duration_seconds=60.0)
        for index in range(chunk_count)
    ]
    return JobManifest(
        video_id="demo-job",
        input_video="gameplays_crudos/demo.mp4",
        total_seconds=float(chunk_count * 60),
        chunk_seconds=60,
        source_master="john/john_video_45deg_ver4.mp4",
        chunks=chunks,
        profile={},
    )


class ProgressTrackerTests(unittest.TestCase):
    def test_begin_stage_does_not_raise_when_state_file_is_temporarily_locked(self):
        with tempfile.TemporaryDirectory() as tmp:
            tracker = ProgressTracker(Path(tmp), build_manifest(chunk_count=1))

            with patch("os.replace", side_effect=PermissionError("locked")):
                tracker.begin_stage(0, "extract")

            self.assertEqual(tracker.state.chunks[0].status, "processing")
            self.assertEqual(tracker.state.chunks[0].stages[0].status, "running")

    def test_recalculate_eta_never_returns_negative_seconds(self):
        with tempfile.TemporaryDirectory() as tmp:
            tracker = ProgressTracker(Path(tmp), build_manifest(chunk_count=2))

            tracker.state.chunks[0].status = "done"
            tracker._recalculate_eta()

            self.assertIsNotNone(tracker.state.global_eta_seconds)
            self.assertGreaterEqual(tracker.state.global_eta_seconds, 0.0)
