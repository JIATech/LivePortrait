import json
import tempfile
import unittest
from pathlib import Path

from tools.pipeline_runtime import (
    PipelineProfile,
    build_video_id,
    load_or_create_manifest,
    plan_chunks,
    save_manifest,
)


class PipelineRuntimeTests(unittest.TestCase):
    def test_build_video_id_is_stable_for_same_input(self):
        a = build_video_id(Path("gameplays_crudos/session_01.mp4"))
        b = build_video_id(Path("gameplays_crudos/session_01.mp4"))
        self.assertEqual(a, b)
        self.assertIn("session_01", a)

    def test_plan_chunks_splits_duration_into_fixed_windows(self):
        chunks = plan_chunks(total_seconds=125.0, chunk_seconds=60)
        self.assertEqual(len(chunks), 3)
        self.assertEqual(chunks[0].start_seconds, 0.0)
        self.assertEqual(chunks[1].start_seconds, 60.0)
        self.assertEqual(chunks[2].duration_seconds, 5.0)

    def test_plan_chunks_rejects_non_positive_chunk_size(self):
        with self.assertRaisesRegex(ValueError, "chunk_seconds must be greater than 0"):
            plan_chunks(total_seconds=125.0, chunk_seconds=0)

    def test_manifest_round_trip_preserves_chunk_status(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            profile = PipelineProfile(
                chunk_seconds=60,
                roi={"x": 6, "y": 811, "w": 259, "h": 268},
                source_master="john/john_video_45deg_ver4.mp4",
                source_fps=12,
                liveportrait={"flag_eye_retargeting": True},
                compositor={},
            )
            manifest = load_or_create_manifest(
                job_dir=root,
                input_video=Path("gameplays_crudos/demo.mp4"),
                total_seconds=61.0,
                profile=profile,
            )
            manifest.chunks[0].status = "done"
            save_manifest(root / "manifests" / "manifest.json", manifest)

            reloaded = load_or_create_manifest(
                job_dir=root,
                input_video=Path("gameplays_crudos/demo.mp4"),
                total_seconds=61.0,
                profile=profile,
            )
            self.assertEqual(reloaded.chunks[0].status, "done")
            self.assertEqual(reloaded.chunks[1].status, "pending")

    def test_load_or_create_manifest_recreates_stale_manifest(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            profile = PipelineProfile(
                chunk_seconds=60,
                roi={"x": 6, "y": 811, "w": 259, "h": 268},
                source_master="john/john_video_45deg_ver4.mp4",
                source_fps=12,
                liveportrait={"flag_eye_retargeting": True},
                compositor={},
            )
            stale_manifest = {
                "video_id": "stale-id",
                "input_video": "gameplays_crudos/other.mp4",
                "total_seconds": 30.0,
                "chunk_seconds": 30,
                "source_master": "john/old_source.mp4",
                "chunks": [
                    {
                        "index": 0,
                        "start_seconds": 0.0,
                        "duration_seconds": 30.0,
                        "status": "done",
                        "error": None,
                    }
                ],
            }
            (root / "manifests").mkdir(parents=True, exist_ok=True)
            (root / "manifests" / "manifest.json").write_text(
                json.dumps(stale_manifest),
                encoding="utf-8",
            )

            manifest = load_or_create_manifest(
                job_dir=root,
                input_video=Path("gameplays_crudos/demo.mp4"),
                total_seconds=61.0,
                profile=profile,
            )

            self.assertEqual(manifest.input_video, "gameplays_crudos/demo.mp4")
            self.assertEqual(manifest.total_seconds, 61.0)
            self.assertEqual(manifest.chunk_seconds, 60)
            self.assertEqual(manifest.source_master, "john/john_video_45deg_ver4.mp4")
            self.assertEqual([chunk.status for chunk in manifest.chunks], ["pending", "pending"])

    def test_load_or_create_manifest_recreates_when_profile_affecting_fields_change(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            base_profile = PipelineProfile(
                chunk_seconds=60,
                roi={"x": 6, "y": 811, "w": 259, "h": 268},
                source_master="john/john_video_45deg_ver4.mp4",
                source_fps=12,
                liveportrait={"flag_eye_retargeting": True},
                compositor={"alpha_cutoff": 0.48},
            )
            changed_profiles = [
                PipelineProfile(
                    chunk_seconds=60,
                    roi={"x": 10, "y": 811, "w": 259, "h": 268},
                    source_master="john/john_video_45deg_ver4.mp4",
                    source_fps=12,
                    liveportrait={"flag_eye_retargeting": True},
                    compositor={"alpha_cutoff": 0.48},
                ),
                PipelineProfile(
                    chunk_seconds=60,
                    roi={"x": 6, "y": 811, "w": 259, "h": 268},
                    source_master="john/john_video_45deg_ver4.mp4",
                    source_fps=24,
                    liveportrait={"flag_eye_retargeting": True},
                    compositor={"alpha_cutoff": 0.48},
                ),
                PipelineProfile(
                    chunk_seconds=60,
                    roi={"x": 6, "y": 811, "w": 259, "h": 268},
                    source_master="john/john_video_45deg_ver4.mp4",
                    source_fps=12,
                    liveportrait={"flag_eye_retargeting": False},
                    compositor={"alpha_cutoff": 0.48},
                ),
                PipelineProfile(
                    chunk_seconds=60,
                    roi={"x": 6, "y": 811, "w": 259, "h": 268},
                    source_master="john/john_video_45deg_ver4.mp4",
                    source_fps=12,
                    liveportrait={"flag_eye_retargeting": True},
                    compositor={"alpha_cutoff": 0.55},
                ),
            ]

            for changed_profile in changed_profiles:
                with self.subTest(changed_profile=changed_profile):
                    manifest = load_or_create_manifest(
                        job_dir=root,
                        input_video=Path("gameplays_crudos/demo.mp4"),
                        total_seconds=61.0,
                        profile=base_profile,
                    )
                    manifest.chunks[0].status = "done"
                    save_manifest(root / "manifests" / "manifest.json", manifest)

                    recreated = load_or_create_manifest(
                        job_dir=root,
                        input_video=Path("gameplays_crudos/demo.mp4"),
                        total_seconds=61.0,
                        profile=changed_profile,
                    )

                    self.assertEqual(
                        [chunk.status for chunk in recreated.chunks],
                        ["pending", "pending"],
                    )

if __name__ == "__main__":
    unittest.main()
