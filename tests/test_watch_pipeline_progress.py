import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from tools.watch_pipeline_progress import (
    collect_pipeline_status,
    find_last_artifact,
    format_status_text,
    main,
    parse_args,
    resolve_job_dir,
)


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

    def test_resolve_job_dir_skips_malformed_or_missing_input_video_manifests(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)

            broken_job = root / "broken-job"
            (broken_job / "manifests").mkdir(parents=True)
            (broken_job / "manifests" / "manifest.json").write_text("{not json}", encoding="utf-8")

            missing_key_job = root / "missing-key-job"
            (missing_key_job / "manifests").mkdir(parents=True)
            (missing_key_job / "manifests" / "manifest.json").write_text(
                json.dumps({"video_id": "missing-key-job"}),
                encoding="utf-8",
            )

            valid_job = root / "RE9-part1-3153aeea"
            (valid_job / "manifests").mkdir(parents=True)
            (valid_job / "manifests" / "manifest.json").write_text(
                json.dumps(
                    {
                        "video_id": "RE9-part1-3153aeea",
                        "input_video": "gameplays_crudos/RE9-part1.mp4",
                        "total_seconds": 120.0,
                        "chunk_seconds": 60,
                        "source_master": "john/john_video_45deg_ver4.mp4",
                        "profile": {},
                        "chunks": [],
                    }
                ),
                encoding="utf-8",
            )

            resolved = resolve_job_dir(work_dir=root, video_name="RE9-part1.mp4")

            self.assertEqual(resolved, valid_job)

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
            older_artifact = job_dir / "chunks" / "chunk_001_roi.pkl"
            older_artifact.parent.mkdir(parents=True)
            older_artifact.write_text("older roi", encoding="utf-8")
            last_artifact = job_dir / "chunks" / "chunk_002_roi.pkl"
            last_artifact.write_text("roi", encoding="utf-8")
            os.utime(older_artifact, (1, 1))
            os.utime(last_artifact, (2, 2))
            final_output = job_dir / "output" / "RE9-part1_final.mp4"
            final_output.write_text("video", encoding="utf-8")
            os.utime(final_output, (3, 3))

            status = collect_pipeline_status(job_dir)

            self.assertEqual(status["done_chunks"], 2)
            self.assertEqual(status["pending_chunks"], 1)
            self.assertEqual(status["failed_chunks"], 0)
            self.assertAlmostEqual(status["percent_complete"], 66.67, places=2)
            self.assertEqual(status["current_chunk"], 2)
            self.assertEqual(status["eta_seconds"], 3600.0)
            self.assertEqual(status["last_artifact"], str(final_output))
            self.assertEqual(status["final_output"], str(final_output))
            self.assertIsNone(status["last_error"])

    def test_collect_pipeline_status_fails_clearly_for_invalid_manifest_json(self):
        with tempfile.TemporaryDirectory() as tmp:
            job_dir = Path(tmp) / "RE9-part1-3153aeea"
            manifests = job_dir / "manifests"
            manifests.mkdir(parents=True)
            (manifests / "manifest.json").write_text("{not json}", encoding="utf-8")

            with self.assertRaisesRegex(ValueError, "Invalid manifest JSON"):
                collect_pipeline_status(job_dir)

    def test_collect_pipeline_status_fails_clearly_for_malformed_chunk_entries(self):
        with tempfile.TemporaryDirectory() as tmp:
            job_dir = Path(tmp) / "RE9-part1-3153aeea"
            manifests = job_dir / "manifests"
            manifests.mkdir(parents=True)
            (manifests / "manifest.json").write_text(
                json.dumps(
                    {
                        "video_id": "RE9-part1-3153aeea",
                        "input_video": "gameplays_crudos/RE9-part1.mp4",
                        "total_seconds": 180.0,
                        "chunk_seconds": 60,
                        "source_master": "john/john_video_45deg_ver4.mp4",
                        "profile": {},
                        "chunks": [
                            {"index": 0, "status": "done"},
                            "bad chunk",
                        ],
                    }
                ),
                encoding="utf-8",
            )

            with self.assertRaisesRegex(ValueError, "Invalid manifest chunk"):
                collect_pipeline_status(job_dir)

    def test_collect_pipeline_status_prioritizes_failed_chunk_for_current_chunk(self):
        with tempfile.TemporaryDirectory() as tmp:
            job_dir = Path(tmp) / "RE9-part1-3153aeea"
            manifests = job_dir / "manifests"
            manifests.mkdir(parents=True)
            (manifests / "manifest.json").write_text(
                json.dumps(
                    {
                        "video_id": "RE9-part1-3153aeea",
                        "input_video": "gameplays_crudos/RE9-part1.mp4",
                        "total_seconds": 180.0,
                        "chunk_seconds": 60,
                        "source_master": "john/john_video_45deg_ver4.mp4",
                        "profile": {},
                        "chunks": [
                            {"index": 0, "start_seconds": 0.0, "duration_seconds": 60.0, "status": "done", "error": None},
                            {"index": 1, "start_seconds": 60.0, "duration_seconds": 60.0, "status": "pending", "error": None},
                            {"index": 2, "start_seconds": 120.0, "duration_seconds": 60.0, "status": "failed", "error": "boom"},
                        ],
                    }
                ),
                encoding="utf-8",
            )

            status = collect_pipeline_status(job_dir)

            self.assertEqual(status["current_chunk"], 2)
            self.assertEqual(status["last_error"], "boom")

    def test_collect_pipeline_status_returns_unknown_eta_when_failures_exist(self):
        with tempfile.TemporaryDirectory() as tmp:
            job_dir = Path(tmp) / "RE9-part1-3153aeea"
            manifests = job_dir / "manifests"
            manifests.mkdir(parents=True)
            (manifests / "manifest.json").write_text(
                json.dumps(
                    {
                        "video_id": "RE9-part1-3153aeea",
                        "input_video": "gameplays_crudos/RE9-part1.mp4",
                        "total_seconds": 180.0,
                        "chunk_seconds": 60,
                        "source_master": "john/john_video_45deg_ver4.mp4",
                        "profile": {},
                        "chunks": [
                            {"index": 0, "start_seconds": 0.0, "duration_seconds": 60.0, "status": "done", "error": None},
                            {"index": 1, "start_seconds": 60.0, "duration_seconds": 60.0, "status": "failed", "error": "boom"},
                            {"index": 2, "start_seconds": 120.0, "duration_seconds": 60.0, "status": "pending", "error": None},
                        ],
                    }
                ),
                encoding="utf-8",
            )
            (job_dir / "output").mkdir(parents=True)
            (job_dir / "output" / "report.json").write_text(
                json.dumps({"total_processing_seconds": 7200.0}),
                encoding="utf-8",
            )

            status = collect_pipeline_status(job_dir)

            self.assertIsNone(status["eta_seconds"])

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
        self.assertIn("chunks: 4 done / 6 pending / 0 failed / 10 total", text)
        self.assertNotIn("\ndone: 4\n", f"\n{text}\n")
        self.assertIn("ETA: 3600.0s", text)
        self.assertIn("current_chunk: chunk 5 of 10", text)
        self.assertIn("last_artifact: .pipeline_work/.../chunk_004_roi.pkl", text)
        self.assertIn("final_output: not ready", text)
        self.assertIn("last_error: none", text)

    def test_parse_args_accepts_status_subcommand(self):
        args = parse_args(["status", "--work-dir", ".pipeline_work", "--video", "RE9-part1.mp4"])

        self.assertEqual(args.mode, "status")
        self.assertEqual(args.work_dir, ".pipeline_work")
        self.assertEqual(args.video, "RE9-part1.mp4")

    def test_parse_args_rejects_non_positive_watch_interval(self):
        with self.assertRaises(SystemExit):
            parse_args(["watch", "--work-dir", ".pipeline_work", "--video", "RE9-part1.mp4", "--interval", "0"])

    def test_find_last_artifact_prefers_most_recent_artifact(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            older = root / "chunk_001.mp4"
            newer = root / "final.mp4"
            older.write_bytes(b"older")
            newer.write_bytes(b"newer")
            os.utime(older, (1, 1))
            os.utime(newer, (2, 2))

            self.assertEqual(find_last_artifact([older, newer]), newer)

    def test_main_watch_loops_until_keyboard_interrupt(self):
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

    def test_main_watch_survives_one_transient_status_error_then_continues(self):
        calls = []

        def fake_collect(_job_dir):
            calls.append("collect")
            if len(calls) == 1:
                raise ValueError("Invalid manifest JSON: broken")

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

        sleep_calls = []

        def fake_sleep(_interval):
            sleep_calls.append("sleep")
            if len(sleep_calls) >= 2:
                raise KeyboardInterrupt

        with patch("tools.watch_pipeline_progress.collect_pipeline_status", side_effect=fake_collect), patch(
            "tools.watch_pipeline_progress.resolve_job_dir",
            return_value=Path(".pipeline_work/demo"),
        ), patch("tools.watch_pipeline_progress.time.sleep", side_effect=fake_sleep), patch(
            "builtins.print"
        ) as print_mock:
            exit_code = main(["watch", "--work-dir", ".pipeline_work", "--video", "demo.mp4", "--interval", "1"])

        self.assertEqual(exit_code, 0)
        self.assertEqual(calls, ["collect", "collect"])
        print_mock.assert_any_call("Temporary status error: Invalid manifest JSON: broken")

    def test_main_watch_survives_one_transient_filesystem_error_then_continues(self):
        calls = []

        def fake_collect(_job_dir):
            calls.append("collect")
            if len(calls) == 1:
                raise FileNotFoundError("manifest disappeared")

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

        sleep_calls = []

        def fake_sleep(_interval):
            sleep_calls.append("sleep")
            if len(sleep_calls) >= 2:
                raise KeyboardInterrupt

        with patch("tools.watch_pipeline_progress.collect_pipeline_status", side_effect=fake_collect), patch(
            "tools.watch_pipeline_progress.resolve_job_dir",
            return_value=Path(".pipeline_work/demo"),
        ), patch("tools.watch_pipeline_progress.time.sleep", side_effect=fake_sleep), patch(
            "builtins.print"
        ) as print_mock:
            exit_code = main(["watch", "--work-dir", ".pipeline_work", "--video", "demo.mp4", "--interval", "1"])

        self.assertEqual(exit_code, 0)
        self.assertEqual(calls, ["collect", "collect"])
        print_mock.assert_any_call("Temporary status error: manifest disappeared")

    def test_main_status_prints_actionable_message_when_job_is_missing(self):
        with patch(
            "tools.watch_pipeline_progress.resolve_job_dir",
            side_effect=FileNotFoundError("No pipeline job found for video: missing.mp4"),
        ), patch("builtins.print") as print_mock:
            exit_code = main(["status", "--work-dir", ".pipeline_work", "--video", "missing.mp4"])

        self.assertEqual(exit_code, 1)
        print_mock.assert_called_once_with("No pipeline job found for video: missing.mp4")


if __name__ == "__main__":
    unittest.main()
