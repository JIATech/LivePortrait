import unittest
from pathlib import Path

from tools.pipeline_commands import (
    CompositorSettings,
    LivePortraitFlags,
    RoiBox,
    build_composite_cmd,
    build_concat_visual_cmd,
    build_full_chunk_cmd,
    build_liveportrait_cmd,
    build_loop_source_cmd,
    build_mux_audio_cmd,
    build_roi_chunk_cmd,
    q,
)


class PipelineCommandTests(unittest.TestCase):
    def test_q_returns_powershell_safe_single_quoted_path(self):
        self.assertEqual(q(Path("clip\"name's.mp4")), "'clip\"name''s.mp4'")

    def test_build_full_chunk_cmd_builds_exact_command(self):
        cmd = build_full_chunk_cmd(
            input_video=Path("gameplay source.mp4"),
            start_seconds=60.0,
            duration_seconds=12.5,
            output_video=Path("chunk full.mp4"),
        )
        self.assertEqual(
            cmd,
            "ffmpeg -y -ss 60.0 -i 'gameplay source.mp4' -t 12.5 -c:v libx264 -crf 18 -an 'chunk full.mp4'",
        )

    def test_build_loop_source_cmd_extends_source_to_chunk_duration(self):
        cmd = build_loop_source_cmd(
            source_video=Path(".pipeline_work/job/source/source_12fps.mp4"),
            duration_seconds=60.0,
            output_video=Path(".pipeline_work/job/source/chunk_000_source_looped.mp4"),
        )
        self.assertEqual(
            cmd,
            "ffmpeg -y -stream_loop -1 -i '.pipeline_work/job/source/source_12fps.mp4' -t 60.0 "
            "-an -c:v libx264 -crf 18 '.pipeline_work/job/source/chunk_000_source_looped.mp4'",
        )

    def test_build_roi_chunk_cmd_uses_fixed_crop_box(self):
        cmd = build_roi_chunk_cmd(
            full_chunk=Path("chunk_full.mp4"),
            roi=RoiBox(x=6, y=811, w=259, h=268),
            fps=12,
            output_roi=Path("chunk_roi.mp4"),
        )
        self.assertEqual(
            cmd,
            "ffmpeg -y -i 'chunk_full.mp4' -vf \"crop=259:268:6:811,fps=12\" -an -c:v libx264 "
            "-crf 18 'chunk_roi.mp4'",
        )

    def test_build_roi_chunk_cmd_accepts_roi_dict_via_coercion(self):
        cmd = build_roi_chunk_cmd(
            full_chunk=Path("chunk_full.mp4"),
            roi={"x": 6, "y": 811, "w": 259, "h": 268},
            fps=12,
            output_roi=Path("chunk_roi.mp4"),
        )
        self.assertEqual(
            cmd,
            "ffmpeg -y -i 'chunk_full.mp4' -vf \"crop=259:268:6:811,fps=12\" -an -c:v libx264 "
            "-crf 18 'chunk_roi.mp4'",
        )

    def test_build_liveportrait_cmd_enables_eye_retargeting(self):
        cmd = build_liveportrait_cmd(
            source_video=Path("source_looped.mp4"),
            driving_video=Path("chunk_roi.mp4"),
            output_dir=Path("chunk_out"),
            liveportrait_flags=LivePortraitFlags(
                flag_eye_retargeting=True,
                flag_force_cpu=True,
                flag_pasteback=False,
                flag_use_half_precision=False,
            ),
        )
        self.assertEqual(
            cmd,
            "$env:PYTHONIOENCODING='utf-8'; & \".venv311\\Scripts\\python.exe\" inference.py -s 'source_looped.mp4' -d 'chunk_roi.mp4' "
            "-o 'chunk_out' --flag-eye-retargeting --flag-force-cpu --no-flag-pasteback "
            "--no-flag-use-half-precision",
        )

    def test_build_liveportrait_cmd_accepts_flag_dict_via_coercion(self):
        cmd = build_liveportrait_cmd(
            source_video=Path('source"looped.mp4'),
            driving_video=Path("chunk_roi.mp4"),
            output_dir=Path("chunk_out"),
            liveportrait_flags={
                "flag_eye_retargeting": True,
                "flag_force_cpu": True,
                "flag_pasteback": False,
                "flag_use_half_precision": False,
            },
        )
        self.assertEqual(
            cmd,
            "$env:PYTHONIOENCODING='utf-8'; & \".venv311\\Scripts\\python.exe\" inference.py -s 'source\"looped.mp4' -d 'chunk_roi.mp4' "
            "-o 'chunk_out' --flag-eye-retargeting --flag-force-cpu --no-flag-pasteback "
            "--no-flag-use-half-precision",
        )

    def test_build_composite_cmd_builds_exact_command(self):
        cmd = build_composite_cmd(
            full_chunk=Path("full chunk.mp4"),
            patch_video=Path("patch's video.mp4"),
            output_video=Path("out.mp4"),
            roi=RoiBox(x=6, y=811, w=259, h=268),
            compositor=CompositorSettings(
                alpha_cutoff=0.48,
                alpha_erode=5,
                alpha_post_blur=0.45,
                handoff_start=0.72,
                handoff_end=0.94,
                suppress_strength=0.60,
                suppress_dilate=13,
                suppress_blur=3.0,
                suppress_roi_blur_ksize=19,
            ),
        )
        self.assertEqual(
            cmd,
            "& \".venv311\\Scripts\\python.exe\" tools\\composite_key_overlay.py --full-video 'full chunk.mp4' "
            "--patch-video 'patch''s video.mp4' --output 'out.mp4' --x 6 --y 811 --width 259 --height 268 "
            "--alpha-cutoff 0.48 --alpha-erode 5 --alpha-post-blur 0.45 --handoff-start 0.72 --handoff-end 0.94 "
            "--suppress-strength 0.6 --suppress-dilate 13 --suppress-blur 3.0 --suppress-roi-blur-ksize 19",
        )

    def test_build_mux_audio_cmd_preserves_original_audio(self):
        cmd = build_mux_audio_cmd(
            original_gameplay=Path("gameplay.mp4"),
            composited_visual=Path("visual.mp4"),
            output_final=Path("final.mp4"),
        )
        self.assertEqual(
            cmd,
            "ffmpeg -y -i 'visual.mp4' -i 'gameplay.mp4' -map 0:v:0 -map 1:a:0 -c:v copy -c:a copy "
            "-shortest 'final.mp4'",
        )

    def test_build_concat_visual_cmd_uses_concat_demuxer(self):
        cmd = build_concat_visual_cmd(Path("chunks.txt"), Path("visual_full.mp4"))

        self.assertIn("-f concat", cmd)
        self.assertIn("-safe 0", cmd)
        self.assertEqual(
            cmd,
            "ffmpeg -y -f concat -safe 0 -i 'chunks.txt' -c:v libx264 -crf 18 -an 'visual_full.mp4'",
        )


if __name__ == "__main__":
    unittest.main()
