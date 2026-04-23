"""GPU-optimized long gameplay LivePortrait pipeline.

This is a CUDA/NVENC variant of the gameplay pipeline designed for machines
with NVIDIA GPUs. Key differences from the CPU pipeline:

- LivePortrait inference runs on CUDA (no --flag-force-cpu).
- Half precision (FP16) is enabled for faster inference.
- FFmpeg uses h264_nvenc hardware encoder for all intermediate re-encodes.
- Chunk size defaults to 120s (GPU can handle longer chunks efficiently).
- Python interpreter is resolved generically (respects active venv).

Usage:
    # Activate your Python venv with CUDA PyTorch installed, then:
    python tools/gpu_pipeline/run_long_gameplay_pipeline_gpu.py \
        --input-dir gameplays_crudos \
        --profile tools/gpu_pipeline/gpu_profile_gameplay_v1.json

    # Process a single video:
    python tools/gpu_pipeline/run_long_gameplay_pipeline_gpu.py \
        --input-dir gameplays_crudos \
        --video gameplay_01.mp4

    # Monitor progress in another terminal:
    python tools/watch_pipeline_progress.py --job .pipeline_work/<job_id>
"""

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from tools.gpu_pipeline.gpu_pipeline_commands import (
    build_composite_cmd,
    build_concat_visual_cmd,
    build_full_chunk_cmd,
    build_liveportrait_cmd,
    build_loop_source_cmd,
    build_mux_audio_cmd,
    build_roi_chunk_cmd,
    q,
)
from tools.gpu_pipeline.popen_runner import make_popen_runner, run_command_plain
from tools.gpu_pipeline.progress_tracker import ProgressTracker
from tools.pipeline_runtime import (
    JobManifest,
    PipelineProfile,
    build_video_id,
    load_or_create_manifest,
    save_manifest,
)

Runner = Callable[[str], None]


@dataclass(frozen=True)
class JobPaths:
    job_dir: Path
    manifest_path: Path
    output_dir: Path
    final_video: Path


def build_final_video_name(input_video: Path) -> str:
    return f"{input_video.stem}_final.mp4"


def discover_input_videos(input_dir: Path) -> list[Path]:
    if not input_dir.is_dir():
        return []

    return sorted(
        path for path in input_dir.iterdir() if path.is_file() and path.suffix.lower() == ".mp4"
    )


def pending_chunk_indices(manifest: JobManifest) -> list[int]:
    """Return indices for all non-done chunks, including failed ones that should be retried."""
    return [chunk.index for chunk in manifest.chunks if chunk.status != "done"]


def load_profile(path: Path) -> PipelineProfile:
    data = json.loads(path.read_text(encoding="utf-8"))
    return PipelineProfile(**data)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run the long gameplay LivePortrait pipeline (GPU-optimized for NVIDIA CUDA)",
    )
    parser.add_argument("--input-dir", default="gameplays_crudos")
    parser.add_argument("--output-dir", default="output")
    parser.add_argument("--work-dir", default=".pipeline_work")
    parser.add_argument("--profile", default="tools/gpu_pipeline/gpu_profile_gameplay_v1.json")
    parser.add_argument("--video", default=None)
    return parser.parse_args()


def progress_tracking_enabled() -> bool:
    return os.getenv("LIVEPORTRAIT_DISABLE_PROGRESS_TRACKER") != "1"


def resolve_shell_exe() -> str:
    if shutil.which("pwsh"):
        return "pwsh"
    if shutil.which("powershell"):
        return "powershell"
    return "pwsh"


def run_command(command: str) -> None:
    subprocess.run([resolve_shell_exe(), "-NoProfile", "-Command", command], check=True)


def probe_duration_seconds(input_video: Path, runner: Runner | None = None) -> float:
    result = subprocess.run(
        [
            "ffprobe",
            "-v",
            "error",
            "-show_entries",
            "format=duration",
            "-of",
            "default=noprint_wrappers=1:nokey=1",
            str(input_video),
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    return float(result.stdout.strip())


def prepare_source_master(job_dir: Path, profile: PipelineProfile, runner: Runner) -> Path:
    source_dir = job_dir / "source"
    source_dir.mkdir(parents=True, exist_ok=True)
    source_key = hashlib.sha1(profile.source_master.encode("utf-8")).hexdigest()[:8]
    prepared = source_dir / f"source_{source_key}_{profile.source_fps}fps.mp4"
    if prepared.exists():
        return prepared

    # Use NVENC for source preparation too
    command = (
        f"ffmpeg -y -i {q(Path(profile.source_master))} "
        f'-vf "fps={profile.source_fps}" -an -c:v h264_nvenc -preset p4 -cq 18 {q(prepared)}'
    )
    runner(command)
    return prepared


def build_job_paths(work_dir: Path, output_dir: Path, job_id: str, input_video: Path) -> JobPaths:
    job_dir = work_dir / job_id
    return JobPaths(
        job_dir=job_dir,
        manifest_path=job_dir / "manifests" / "manifest.json",
        output_dir=output_dir,
        final_video=output_dir / build_final_video_name(input_video),
    )


def build_chunk_paths(job_dir: Path, chunk_index: int) -> dict[str, Path]:
    full_dir = job_dir / "chunks" / "full"
    roi_dir = job_dir / "chunks" / "roi"
    source_dir = job_dir / "chunks" / "source"
    liveportrait_dir = job_dir / "chunks" / "liveportrait"
    composited_dir = job_dir / "chunks" / "composited"
    patch_dir = liveportrait_dir / f"chunk_{chunk_index:03d}"

    for path in (full_dir, roi_dir, source_dir, patch_dir, composited_dir):
        path.mkdir(parents=True, exist_ok=True)

    return {
        "full_chunk": full_dir / f"chunk_{chunk_index:03d}.mp4",
        "roi_chunk": roi_dir / f"chunk_{chunk_index:03d}_roi.mp4",
        "source_loop": source_dir / f"chunk_{chunk_index:03d}_source_looped.mp4",
        "patch_dir": patch_dir,
        "patch_video": patch_dir / "patch.mp4",
        "composited": composited_dir / f"chunk_{chunk_index:03d}_composited.mp4",
    }


def resolve_patch_video_path(patch_dir: Path) -> Path:
    candidates = sorted(
        path
        for path in patch_dir.glob("*.mp4")
        if path.is_file() and not path.stem.endswith("_concat")
    )
    if candidates:
        return candidates[0]

    raise FileNotFoundError(f"LivePortrait patch video was not created in: {patch_dir}")


def process_chunk(
    runner: Runner,
    input_video: Path,
    source_prepared: Path,
    chunk_index: int,
    start_seconds: float,
    duration_seconds: float,
    paths: dict[str, Path],
    profile: PipelineProfile,
    tracker: ProgressTracker | None = None,
) -> None:
    stages: list[tuple[str, Callable[[], str]]] = [
        ("extract", lambda: build_full_chunk_cmd(input_video, start_seconds, duration_seconds, paths["full_chunk"])),
        ("roi_crop", lambda: build_roi_chunk_cmd(paths["full_chunk"], profile.roi, profile.source_fps, paths["roi_chunk"])),
        ("source_loop", lambda: build_loop_source_cmd(source_prepared, duration_seconds, paths["source_loop"])),
        ("liveportrait", lambda: build_liveportrait_cmd(paths["source_loop"], paths["roi_chunk"], paths["patch_dir"], profile.liveportrait)),
        ("composite", lambda: build_composite_cmd(paths["full_chunk"], resolve_patch_video_path(paths["patch_dir"]), paths["composited"], profile.roi, profile.compositor)),
    ]

    for stage_name, cmd_builder in stages:
        cmd = cmd_builder()
        if tracker:
            tracker.begin_stage(chunk_index, stage_name)
        try:
            if stage_name in ("extract", "roi_crop", "source_loop") and tracker:
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


def build_report_payload(
    input_video: Path,
    source_master: str,
    total_duration_seconds: float,
    chunk_count: int,
    processed_chunks: int,
    failed_chunks: list[int],
    total_processing_seconds: float,
    config_path: Path,
    execution_timestamp: str,
) -> dict[str, object]:
    return {
        "input_video": input_video.as_posix(),
        "source_master": source_master,
        "total_duration_seconds": total_duration_seconds,
        "chunk_count": chunk_count,
        "processed_chunks": processed_chunks,
        "failed_chunks": failed_chunks,
        "total_processing_seconds": total_processing_seconds,
        "config_path": config_path.as_posix(),
        "execution_timestamp": execution_timestamp,
    }


def quote_concat_manifest_path(path: Path) -> str:
    return path.as_posix().replace("'", "'\\''")


def write_report(
    output_dir: Path,
    input_video: Path,
    profile: PipelineProfile,
    manifest: JobManifest,
    config_path: Path,
    total_processing_seconds: float,
    execution_timestamp: str,
) -> None:
    report_payload = build_report_payload(
        input_video=input_video,
        source_master=profile.source_master,
        total_duration_seconds=manifest.total_seconds,
        chunk_count=len(manifest.chunks),
        processed_chunks=sum(1 for chunk in manifest.chunks if chunk.status == "done"),
        failed_chunks=[chunk.index for chunk in manifest.chunks if chunk.status != "done"],
        total_processing_seconds=total_processing_seconds,
        config_path=config_path,
        execution_timestamp=execution_timestamp,
    )
    (output_dir / "report.json").write_text(
        json.dumps(report_payload, indent=2),
        encoding="utf-8",
    )


def assemble_final_output(
    job_dir: Path,
    input_video: Path,
    output_dir: Path,
    manifest: JobManifest,
    runner: Runner,
) -> Path:
    output_dir.mkdir(parents=True, exist_ok=True)
    manifests_dir = job_dir / "manifests"
    manifests_dir.mkdir(parents=True, exist_ok=True)
    list_file = manifests_dir / "concat_chunks.txt"
    composited_chunks = [
        (job_dir / "chunks" / "composited" / f"chunk_{chunk.index:03d}_composited.mp4").resolve()
        for chunk in manifest.chunks
    ]
    list_file.write_text(
        "".join(f"file '{quote_concat_manifest_path(path)}'\n" for path in composited_chunks),
        encoding="utf-8",
    )

    visual_full = output_dir / "visual_full.mp4"
    final_video = output_dir / build_final_video_name(input_video)
    runner(build_concat_visual_cmd(list_file, visual_full))
    runner(build_mux_audio_cmd(input_video, visual_full, final_video))
    return final_video


def run_job(
    input_video: Path,
    profile: PipelineProfile,
    output_dir: Path,
    work_dir: Path,
    runner: Runner,
    config_path: Path | None = None,
) -> Path:
    started_at = datetime.now(timezone.utc).isoformat()
    started_timer = time.perf_counter()
    job_id = build_video_id(input_video)
    job_paths = build_job_paths(
        work_dir=work_dir,
        output_dir=output_dir,
        job_id=job_id,
        input_video=input_video,
    )
    resolved_config_path = config_path or Path("tools/gpu_pipeline/gpu_profile_gameplay_v1.json")
    job_paths.output_dir.mkdir(parents=True, exist_ok=True)
    manifest = load_or_create_manifest(
        job_dir=job_paths.job_dir,
        input_video=input_video,
        total_seconds=probe_duration_seconds(input_video, runner),
        profile=profile,
    )
    source_prepared = prepare_source_master(job_paths.job_dir, profile, runner)
    tracker = ProgressTracker(job_paths.job_dir, manifest) if progress_tracking_enabled() else None

    try:
        for chunk in manifest.chunks:
            if chunk.status == "done":
                if tracker:
                    tracker.mark_chunk_done(chunk.index)
                continue

            try:
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
                if tracker:
                    tracker._flush()
    finally:
        if tracker:
            tracker.close()

    final_video = assemble_final_output(
        job_dir=job_paths.job_dir,
        input_video=input_video,
        output_dir=job_paths.output_dir,
        manifest=manifest,
        runner=runner,
    )
    write_report(
        output_dir=job_paths.output_dir,
        input_video=input_video,
        profile=profile,
        manifest=manifest,
        config_path=resolved_config_path,
        total_processing_seconds=time.perf_counter() - started_timer,
        execution_timestamp=started_at,
    )
    return final_video


def main() -> None:
    args = parse_args()
    profile = load_profile(Path(args.profile))
    runner = run_command
    input_dir = Path(args.input_dir)
    output_root = Path(args.output_dir)
    work_root = Path(args.work_dir)

    videos = discover_input_videos(input_dir)
    if args.video:
        videos = [video for video in videos if video.name == args.video]

    for input_video in videos:
        run_job(
            input_video=input_video,
            profile=profile,
            output_dir=output_root / build_video_id(input_video),
            work_dir=work_root,
            runner=runner,
            config_path=Path(args.profile),
        )


if __name__ == "__main__":
    main()
