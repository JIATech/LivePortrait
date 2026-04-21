import os
import subprocess
import sys
import tempfile
import unittest
import json
from pathlib import Path
from unittest.mock import patch

from tools.pipeline_runtime import ChunkRecord, JobManifest, PipelineProfile, build_video_id
from tools.run_long_gameplay_pipeline import (
    assemble_final_output,
    build_report_payload,
    build_job_paths,
    build_chunk_paths,
    discover_input_videos,
    main,
    pending_chunk_indices,
    prepare_source_master,
    process_chunk,
    resolve_patch_video_path,
    run_command,
    run_job,
)


class FakeRunner:
    def __init__(self):
        self.commands = []

    def __call__(self, command: str) -> None:
        self.commands.append(command)


class PatchProducingRunner(FakeRunner):
    def __init__(self, patch_dir: Path, patch_video_name: str):
        super().__init__()
        self.patch_dir = patch_dir
        self.patch_video_name = patch_video_name

    def __call__(self, command: str) -> None:
        super().__call__(command)
        if "inference.py" in command:
            self.patch_dir.mkdir(parents=True, exist_ok=True)
            (self.patch_dir / self.patch_video_name).write_bytes(b"patch")
            (self.patch_dir / f"{Path(self.patch_video_name).stem}_concat.mp4").write_bytes(b"concat")


class RunLongGameplayPipelineTests(unittest.TestCase):
    def test_resolve_patch_video_path_prefers_primary_mp4_over_concat_output(self):
        with tempfile.TemporaryDirectory() as tmp:
            patch_dir = Path(tmp) / "chunk_000"
            patch_dir.mkdir(parents=True, exist_ok=True)
            concat_video = patch_dir / "chunk_000_source_looped--chunk_000_roi_concat.mp4"
            patch_video = patch_dir / "chunk_000_source_looped--chunk_000_roi.mp4"
            concat_video.write_bytes(b"concat")
            patch_video.write_bytes(b"patch")

            self.assertEqual(resolve_patch_video_path(patch_dir), patch_video)

    def test_run_command_invokes_pwsh_explicitly(self):
        command = "& '.venv311\\Scripts\\python.exe' tools/run_long_gameplay_pipeline.py"

        with patch("tools.run_long_gameplay_pipeline.subprocess.run") as subprocess_run:
            run_command(command)

        subprocess_run.assert_called_once_with(
            ["pwsh", "-NoProfile", "-Command", command],
            check=True,
        )

    def test_script_entrypoint_help_runs_successfully(self):
        result = subprocess.run(
            [
                sys.executable,
                "tools/run_long_gameplay_pipeline.py",
                "--help",
            ],
            cwd=Path(__file__).resolve().parents[1],
            capture_output=True,
            text=True,
        )

        self.assertEqual(result.returncode, 0, msg=result.stderr)
        self.assertIn("Run the long gameplay LivePortrait pipeline", result.stdout)

    def test_process_chunk_runs_expected_stages_in_order(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            paths = {
                "full_chunk": root / "full" / "chunk_000.mp4",
                "roi_chunk": root / "roi" / "chunk_000_roi.mp4",
                "source_loop": root / "source" / "chunk_000_source_loop.mp4",
                "patch_dir": root / "liveportrait" / "chunk_000",
                "patch_video": root / "liveportrait" / "chunk_000" / "patch.mp4",
                "composited": root / "composited" / "chunk_000_composited.mp4",
            }
            runner = PatchProducingRunner(
                paths["patch_dir"],
                "chunk_000_source_looped--chunk_000_roi.mp4",
            )

            process_chunk(
                runner=runner,
                input_video=Path("gameplays_crudos/demo.mp4"),
                source_prepared=Path("source/source_master_12fps.mp4"),
                chunk_index=0,
                start_seconds=0.0,
                duration_seconds=60.0,
                paths=paths,
                profile=PipelineProfile(
                    chunk_seconds=60,
                    roi={"x": 6, "y": 811, "w": 259, "h": 268},
                    source_master="john/john_video_45deg_ver4.mp4",
                    source_fps=12,
                    liveportrait={
                        "flag_eye_retargeting": True,
                        "flag_force_cpu": True,
                        "flag_pasteback": False,
                        "flag_use_half_precision": False,
                    },
                    compositor={
                        "alpha_cutoff": 0.48,
                        "alpha_erode": 5,
                        "alpha_post_blur": 0.45,
                        "handoff_start": 0.72,
                        "handoff_end": 0.94,
                        "suppress_strength": 0.60,
                        "suppress_dilate": 13,
                        "suppress_blur": 3.0,
                        "suppress_roi_blur_ksize": 19,
                    },
                ),
            )

            self.assertEqual(len(runner.commands), 5)
            self.assertIn("ffmpeg -y -ss 0.0", runner.commands[0])
            self.assertIn("crop=259:268:6:811,fps=12", runner.commands[1])
            self.assertIn("-stream_loop -1", runner.commands[2])
            self.assertIn("inference.py", runner.commands[3])
            self.assertIn("composite_key_overlay.py", runner.commands[4])
            self.assertIn("chunk_000_source_looped--chunk_000_roi.mp4", runner.commands[4])
            self.assertNotIn("patch.mp4", runner.commands[4])

    def test_process_chunk_fails_when_liveportrait_patch_video_is_missing(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            paths = {
                "full_chunk": root / "full" / "chunk_000.mp4",
                "roi_chunk": root / "roi" / "chunk_000_roi.mp4",
                "source_loop": root / "source" / "chunk_000_source_loop.mp4",
                "patch_dir": root / "liveportrait" / "chunk_000",
                "patch_video": root / "liveportrait" / "chunk_000" / "patch.mp4",
                "composited": root / "composited" / "chunk_000_composited.mp4",
            }
            runner = FakeRunner()

            with self.assertRaisesRegex(FileNotFoundError, "patch video was not created in"):
                process_chunk(
                    runner=runner,
                    input_video=Path("gameplays_crudos/demo.mp4"),
                    source_prepared=Path("source/source_master_12fps.mp4"),
                    chunk_index=0,
                    start_seconds=0.0,
                    duration_seconds=60.0,
                    paths=paths,
                    profile=PipelineProfile(
                        chunk_seconds=60,
                        roi={"x": 6, "y": 811, "w": 259, "h": 268},
                        source_master="john/john_video_45deg_ver4.mp4",
                        source_fps=12,
                        liveportrait={
                            "flag_eye_retargeting": True,
                            "flag_force_cpu": True,
                            "flag_pasteback": False,
                            "flag_use_half_precision": False,
                        },
                        compositor={
                            "alpha_cutoff": 0.48,
                            "alpha_erode": 5,
                            "alpha_post_blur": 0.45,
                            "handoff_start": 0.72,
                            "handoff_end": 0.94,
                            "suppress_strength": 0.60,
                            "suppress_dilate": 13,
                            "suppress_blur": 3.0,
                            "suppress_roi_blur_ksize": 19,
                        },
                    ),
                )

            self.assertEqual(len(runner.commands), 4)
            self.assertIn("inference.py", runner.commands[3])

    def test_build_chunk_paths_uses_job_dir_chunk_structure(self):
        with tempfile.TemporaryDirectory() as tmp:
            job_dir = Path(tmp) / "job-123"

            paths = build_chunk_paths(job_dir, 7)

            self.assertEqual(paths["full_chunk"], job_dir / "chunks" / "full" / "chunk_007.mp4")
            self.assertEqual(paths["roi_chunk"], job_dir / "chunks" / "roi" / "chunk_007_roi.mp4")
            self.assertEqual(
                paths["source_loop"],
                job_dir / "chunks" / "source" / "chunk_007_source_looped.mp4",
            )
            self.assertEqual(paths["patch_dir"], job_dir / "chunks" / "liveportrait" / "chunk_007")
            self.assertEqual(
                paths["patch_video"],
                job_dir / "chunks" / "liveportrait" / "chunk_007" / "patch.mp4",
            )
            self.assertEqual(
                paths["composited"],
                job_dir / "chunks" / "composited" / "chunk_007_composited.mp4",
            )
            self.assertTrue(paths["patch_dir"].is_dir())
            self.assertTrue((job_dir / "chunks" / "full").is_dir())
            self.assertTrue((job_dir / "chunks" / "roi").is_dir())
            self.assertTrue((job_dir / "chunks" / "source").is_dir())
            self.assertTrue((job_dir / "chunks" / "composited").is_dir())

    def test_build_job_paths_makes_work_and_output_roles_explicit(self):
        work_dir = Path(".pipeline_work")
        output_dir = Path("output/demo-123")

        paths = build_job_paths(work_dir=work_dir, output_dir=output_dir, job_id="demo-123")

        self.assertEqual(paths.job_dir, work_dir / "demo-123")
        self.assertEqual(paths.manifest_path, work_dir / "demo-123" / "manifests" / "manifest.json")
        self.assertEqual(paths.output_dir, output_dir)
        self.assertEqual(paths.final_video, output_dir / "final.mp4")

    def test_discover_input_videos_only_returns_mp4_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "a.mp4").write_bytes(b"a")
            (root / "clip.MP4").write_bytes(b"c")
            (root / "b.mov").write_bytes(b"b")
            (root / "note.txt").write_text("ignore", encoding="utf-8")

            found = discover_input_videos(root)

            self.assertEqual([path.name for path in found], ["a.mp4", "clip.MP4"])

    def test_discover_input_videos_returns_empty_list_for_non_directory_path(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            invalid_path = root / "not_a_directory.mp4"
            invalid_path.write_bytes(b"a")

            self.assertEqual(discover_input_videos(invalid_path), [])

    def test_pending_chunk_indices_skips_done_chunks(self):
        manifest = JobManifest(
            video_id="demo",
            input_video="demo.mp4",
            total_seconds=120.0,
            chunk_seconds=60,
            source_master="john/john_video_45deg_ver4.mp4",
            chunks=[
                ChunkRecord(index=0, start_seconds=0.0, duration_seconds=60.0, status="done"),
                ChunkRecord(index=1, start_seconds=60.0, duration_seconds=60.0, status="pending"),
                ChunkRecord(index=2, start_seconds=120.0, duration_seconds=5.0, status="failed"),
            ],
        )

        self.assertEqual(pending_chunk_indices(manifest), [1, 2])

    def test_build_report_payload_includes_processing_summary(self):
        payload = build_report_payload(
            input_video=Path("gameplays_crudos/demo.mp4"),
            source_master="john/john_video_45deg_ver4.mp4",
            total_duration_seconds=180.0,
            chunk_count=3,
            processed_chunks=2,
            failed_chunks=[2],
            total_processing_seconds=12.5,
            config_path=Path("pipeline_profiles/liveportrait_gameplay_v1.json"),
            execution_timestamp="2026-04-21T10:30:00+00:00",
        )

        self.assertTrue(payload["input_video"].endswith("demo.mp4"))
        self.assertEqual(payload["source_master"], "john/john_video_45deg_ver4.mp4")
        self.assertEqual(payload["total_duration_seconds"], 180.0)
        self.assertEqual(payload["chunk_count"], 3)
        self.assertEqual(payload["processed_chunks"], 2)
        self.assertEqual(payload["failed_chunks"], [2])
        self.assertEqual(payload["total_processing_seconds"], 12.5)
        self.assertEqual(payload["execution_timestamp"], "2026-04-21T10:30:00+00:00")
        self.assertEqual(
            payload["config_path"],
            "pipeline_profiles/liveportrait_gameplay_v1.json",
        )

    def test_prepare_source_master_cache_key_changes_with_source_master(self):
        with tempfile.TemporaryDirectory() as tmp:
            job_dir = Path(tmp) / "job"
            first_profile = PipelineProfile(
                chunk_seconds=60,
                roi={"x": 6, "y": 811, "w": 259, "h": 268},
                source_master="john/source_a.mp4",
                source_fps=12,
                liveportrait={},
                compositor={},
            )
            second_profile = PipelineProfile(
                chunk_seconds=60,
                roi={"x": 6, "y": 811, "w": 259, "h": 268},
                source_master="john/source_b.mp4",
                source_fps=12,
                liveportrait={},
                compositor={},
            )
            first_runner = FakeRunner()
            second_runner = FakeRunner()

            first_prepared = prepare_source_master(job_dir, first_profile, first_runner)
            first_prepared.parent.mkdir(parents=True, exist_ok=True)
            first_prepared.write_bytes(b"prepared")

            second_prepared = prepare_source_master(job_dir, second_profile, second_runner)

            self.assertNotEqual(first_prepared, second_prepared)
            self.assertEqual(len(first_runner.commands), 1)
            self.assertEqual(len(second_runner.commands), 1)
            self.assertIn("source_a.mp4", first_runner.commands[0])
            self.assertIn("source_b.mp4", second_runner.commands[0])

    def test_assemble_final_output_writes_concat_list_and_runs_concat_then_remux(self):
        manifest = JobManifest(
            video_id="demo-123",
            input_video="gameplays_crudos/demo.mp4",
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
            job_dir = root / ".pipeline_work" / "demo-123"
            runner = FakeRunner()

            final_video = assemble_final_output(
                job_dir=job_dir,
                input_video=Path("gameplays_crudos/demo.mp4"),
                output_dir=root / "output" / "demo-123",
                manifest=manifest,
                runner=runner,
            )

            concat_list = job_dir / "manifests" / "concat_chunks.txt"
            self.assertTrue(concat_list.exists())
            self.assertEqual(
                concat_list.read_text(encoding="utf-8"),
                "file '" + (job_dir / "chunks" / "composited" / "chunk_000_composited.mp4").as_posix() + "'\n"
                "file '" + (job_dir / "chunks" / "composited" / "chunk_001_composited.mp4").as_posix() + "'\n",
            )
            self.assertEqual(final_video, root / "output" / "demo-123" / "final.mp4")
            self.assertEqual(len(runner.commands), 2)
            self.assertIn("-f concat -safe 0", runner.commands[0])
            self.assertIn("visual_full.mp4", runner.commands[0])
            self.assertIn("-map 0:v:0 -map 1:a:0", runner.commands[1])
            self.assertIn("final.mp4", runner.commands[1])

    def test_assemble_final_output_escapes_apostrophes_in_concat_manifest(self):
        manifest = JobManifest(
            video_id="demo-123",
            input_video="gameplays_crudos/demo.mp4",
            total_seconds=60.0,
            chunk_seconds=60,
            source_master="john/john_video_45deg_ver4.mp4",
            chunks=[
                ChunkRecord(index=0, start_seconds=0.0, duration_seconds=60.0, status="done"),
            ],
        )

        with tempfile.TemporaryDirectory(prefix="job's-") as tmp:
            root = Path(tmp)
            job_dir = root / ".pipeline_work" / "demo-123"

            assemble_final_output(
                job_dir=job_dir,
                input_video=Path("gameplays_crudos/demo.mp4"),
                output_dir=root / "output" / "demo-123",
                manifest=manifest,
                runner=FakeRunner(),
            )

            concat_list = job_dir / "manifests" / "concat_chunks.txt"
            expected_path = (
                job_dir / "chunks" / "composited" / "chunk_000_composited.mp4"
            ).as_posix().replace("'", "'\\''")
            self.assertEqual(
                concat_list.read_text(encoding="utf-8"),
                f"file '{expected_path}'\n",
            )

    def test_assemble_final_output_writes_absolute_chunk_paths_when_job_dir_is_relative(self):
        manifest = JobManifest(
            video_id="demo-123",
            input_video="gameplays_crudos/demo.mp4",
            total_seconds=120.0,
            chunk_seconds=60,
            source_master="john/john_video_45deg_ver4.mp4",
            chunks=[
                ChunkRecord(index=0, start_seconds=0.0, duration_seconds=60.0, status="done"),
                ChunkRecord(index=1, start_seconds=60.0, duration_seconds=60.0, status="done"),
            ],
        )

        with tempfile.TemporaryDirectory() as tmp:
            previous_cwd = Path.cwd()
            os.chdir(tmp)
            try:
                job_dir = Path(".pipeline_work") / "demo-123"

                assemble_final_output(
                    job_dir=job_dir,
                    input_video=Path("gameplays_crudos/demo.mp4"),
                    output_dir=Path("output") / "demo-123",
                    manifest=manifest,
                    runner=FakeRunner(),
                )

                concat_list = job_dir / "manifests" / "concat_chunks.txt"
                self.assertEqual(
                    concat_list.read_text(encoding="utf-8"),
                    "file '"
                    + (Path(tmp) / ".pipeline_work" / "demo-123" / "chunks" / "composited" / "chunk_000_composited.mp4").as_posix()
                    + "'\n"
                    "file '"
                    + (Path(tmp) / ".pipeline_work" / "demo-123" / "chunks" / "composited" / "chunk_001_composited.mp4").as_posix()
                    + "'\n",
                )
            finally:
                os.chdir(previous_cwd)

    def test_main_filters_requested_video_and_runs_each_job_into_output_video_id(self):
        profile = PipelineProfile(
            chunk_seconds=60,
            roi={"x": 6, "y": 811, "w": 259, "h": 268},
            source_master="john/john_video_45deg_ver4.mp4",
            source_fps=12,
            liveportrait={},
            compositor={},
        )
        selected_video = Path("gameplays_crudos/gameplay_full_10s.mp4")
        skipped_video = Path("gameplays_crudos/other.mp4")

        with patch(
            "tools.run_long_gameplay_pipeline.parse_args",
            return_value=type(
                "Args",
                (),
                {
                    "input_dir": "gameplays_crudos",
                    "output_dir": "output",
                    "work_dir": ".pipeline_work",
                    "profile": "pipeline_profiles/liveportrait_gameplay_v1.json",
                    "video": "gameplay_full_10s.mp4",
                },
            )(),
        ), patch("tools.run_long_gameplay_pipeline.load_profile", return_value=profile), patch(
            "tools.run_long_gameplay_pipeline.discover_input_videos",
            return_value=[selected_video, skipped_video],
        ), patch("tools.run_long_gameplay_pipeline.run_job") as run_job_mock:
            main()

        run_job_mock.assert_called_once_with(
            input_video=selected_video,
            profile=profile,
            output_dir=Path("output") / build_video_id(selected_video),
            work_dir=Path(".pipeline_work"),
            runner=unittest.mock.ANY,
            config_path=Path("pipeline_profiles/liveportrait_gameplay_v1.json"),
        )

    def test_run_job_skips_done_chunks_and_marks_pending_chunk_done(self):
        manifest = JobManifest(
            video_id="demo",
            input_video="gameplays_crudos/demo.mp4",
            total_seconds=120.0,
            chunk_seconds=60,
            source_master="john/john_video_45deg_ver4.mp4",
            chunks=[
                ChunkRecord(index=0, start_seconds=0.0, duration_seconds=60.0, status="done"),
                ChunkRecord(index=1, start_seconds=60.0, duration_seconds=60.0, status="pending"),
            ],
        )
        processed = []
        saved_statuses = []

        def fake_process_chunk(**kwargs):
            processed.append(kwargs["chunk_index"])
            expected_job_id = build_video_id(Path("gameplays_crudos/demo.mp4"))
            self.assertEqual(kwargs["paths"]["full_chunk"].name, "chunk_001.mp4")
            self.assertEqual(
                kwargs["paths"]["full_chunk"].parent,
                root / ".pipeline_work" / expected_job_id / "chunks" / "full",
            )

        def fake_save_manifest(_path, current_manifest):
            saved_statuses.append([chunk.status for chunk in current_manifest.chunks])

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            profile = PipelineProfile(
                chunk_seconds=60,
                roi={"x": 6, "y": 811, "w": 259, "h": 268},
                source_master="john/john_video_45deg_ver4.mp4",
                source_fps=12,
                liveportrait={},
                compositor={},
            )

            with (
                patch("tools.run_long_gameplay_pipeline.probe_duration_seconds", return_value=120.0),
                patch("tools.run_long_gameplay_pipeline.load_or_create_manifest", return_value=manifest),
                patch(
                    "tools.run_long_gameplay_pipeline.prepare_source_master",
                    return_value=root / "source" / "source_master_12fps.mp4",
                ),
                patch("tools.run_long_gameplay_pipeline.process_chunk", side_effect=fake_process_chunk),
                patch("tools.run_long_gameplay_pipeline.save_manifest", side_effect=fake_save_manifest),
            ):
                run_job(
                    input_video=Path("gameplays_crudos/demo.mp4"),
                    profile=profile,
                    output_dir=root / "output",
                    work_dir=root / ".pipeline_work",
                    runner=FakeRunner(),
                )

            self.assertTrue((root / "output").is_dir())

        self.assertEqual(processed, [1])
        self.assertEqual(manifest.chunks[0].status, "done")
        self.assertEqual(manifest.chunks[1].status, "done")
        self.assertIsNone(manifest.chunks[1].error)
        self.assertEqual(saved_statuses, [["done", "done"]])

    def test_run_job_marks_chunk_failed_and_saves_manifest_before_reraising(self):
        manifest = JobManifest(
            video_id="demo",
            input_video="gameplays_crudos/demo.mp4",
            total_seconds=60.0,
            chunk_seconds=60,
            source_master="john/john_video_45deg_ver4.mp4",
            chunks=[
                ChunkRecord(index=0, start_seconds=0.0, duration_seconds=60.0, status="pending"),
            ],
        )
        saved_statuses = []

        def fake_save_manifest(_path, current_manifest):
            saved_statuses.append(
                [(chunk.status, chunk.error) for chunk in current_manifest.chunks]
            )

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            profile = PipelineProfile(
                chunk_seconds=60,
                roi={"x": 6, "y": 811, "w": 259, "h": 268},
                source_master="john/john_video_45deg_ver4.mp4",
                source_fps=12,
                liveportrait={},
                compositor={},
            )

            with (
                patch("tools.run_long_gameplay_pipeline.probe_duration_seconds", return_value=60.0),
                patch("tools.run_long_gameplay_pipeline.load_or_create_manifest", return_value=manifest),
                patch(
                    "tools.run_long_gameplay_pipeline.prepare_source_master",
                    return_value=root / "source" / "source_master_12fps.mp4",
                ),
                patch(
                    "tools.run_long_gameplay_pipeline.process_chunk",
                    side_effect=RuntimeError("chunk boom"),
                ),
                patch("tools.run_long_gameplay_pipeline.save_manifest", side_effect=fake_save_manifest),
            ):
                with self.assertRaisesRegex(RuntimeError, "chunk boom"):
                    run_job(
                        input_video=Path("gameplays_crudos/demo.mp4"),
                        profile=profile,
                        output_dir=root / "output",
                        work_dir=root / ".pipeline_work",
                        runner=FakeRunner(),
                    )

        self.assertEqual(manifest.chunks[0].status, "failed")
        self.assertEqual(manifest.chunks[0].error, "chunk boom")
        self.assertEqual(saved_statuses, [[("failed", "chunk boom")]])

    def test_run_job_writes_report_json_before_reraising_chunk_failure(self):
        manifest = JobManifest(
            video_id="demo",
            input_video="gameplays_crudos/demo.mp4",
            total_seconds=60.0,
            chunk_seconds=60,
            source_master="john/john_video_45deg_ver4.mp4",
            chunks=[
                ChunkRecord(index=0, start_seconds=0.0, duration_seconds=60.0, status="pending"),
            ],
        )

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            profile = PipelineProfile(
                chunk_seconds=60,
                roi={"x": 6, "y": 811, "w": 259, "h": 268},
                source_master="john/john_video_45deg_ver4.mp4",
                source_fps=12,
                liveportrait={},
                compositor={},
            )
            output_dir = root / "output"

            with (
                patch("tools.run_long_gameplay_pipeline.probe_duration_seconds", return_value=60.0),
                patch("tools.run_long_gameplay_pipeline.load_or_create_manifest", return_value=manifest),
                patch(
                    "tools.run_long_gameplay_pipeline.prepare_source_master",
                    return_value=root / "source" / "source_master_12fps.mp4",
                ),
                patch(
                    "tools.run_long_gameplay_pipeline.process_chunk",
                    side_effect=RuntimeError("chunk boom"),
                ),
            ):
                with self.assertRaisesRegex(RuntimeError, "chunk boom"):
                    run_job(
                        input_video=Path("gameplays_crudos/demo.mp4"),
                        profile=profile,
                        output_dir=output_dir,
                        work_dir=root / ".pipeline_work",
                        runner=FakeRunner(),
                        config_path=Path("pipeline_profiles/liveportrait_gameplay_v1.json"),
                    )

            report_payload = json.loads((output_dir / "report.json").read_text(encoding="utf-8"))
            self.assertEqual(report_payload["total_duration_seconds"], 60.0)
            self.assertEqual(report_payload["chunk_count"], 1)
            self.assertEqual(report_payload["processed_chunks"], 0)
            self.assertEqual(report_payload["failed_chunks"], [0])
            self.assertIn("total_processing_seconds", report_payload)
            self.assertGreaterEqual(report_payload["total_processing_seconds"], 0.0)
            self.assertIn("execution_timestamp", report_payload)
            self.assertEqual(report_payload["config_path"], "pipeline_profiles/liveportrait_gameplay_v1.json")

if __name__ == "__main__":
    unittest.main()
