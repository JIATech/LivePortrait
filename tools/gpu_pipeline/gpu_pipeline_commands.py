"""GPU-optimized command builders for the LivePortrait gameplay pipeline.

Differences from the CPU pipeline:
- FFmpeg uses h264_nvenc (NVIDIA hardware encoder) instead of libx264.
- LivePortrait runs on CUDA without --flag-force-cpu.
- Half precision (FP16) is enabled for faster inference on NVIDIA GPUs.
- Python interpreter is resolved generically (no hardcoded venv path).
"""

from dataclasses import dataclass
from pathlib import Path
from typing import Mapping

from tools.pipeline_commands import (
    CompositorSettings,
    LivePortraitFlags,
    RoiBox,
    q,
)


# NVENC preset: p1=fastest, p7=best quality.
# p4 is a good balance for intermediate pipeline steps.
NVENC_PRESET = "p4"
NVENC_PROFILE = "high"
NVENC_RC = "vbr"  # Variable bitrate for better quality/size balance
NVENC_CQ = "22"  # Constant quality target (similar to CRF 18 in libx264)


def _nvenc_opts() -> str:
    """Return h264_nvenc encoder options string."""
    return (
        f"h264_nvenc -preset {NVENC_PRESET} -profile {NVENC_PROFILE} "
        f"-rc {NVENC_RC} -cq {NVENC_CQ}"
    )


def _python_exe() -> str:
    r"""Return the Python interpreter to use.

    Prefers the venv Python at .venv\Scripts\python.exe if it exists,
    otherwise falls back to the system 'python'.
    """
    venv_python = Path(".venv") / "Scripts" / "python.exe"
    if venv_python.exists():
        return str(venv_python)
    return "python"


def build_full_chunk_cmd(input_video: Path, start_seconds: float, duration_seconds: float, output_video: Path) -> str:
    """Extract a chunk from the source gameplay using NVENC hardware encoding."""
    return (
        f"ffmpeg -y -ss {start_seconds} -i {q(input_video)} -t {duration_seconds} "
        f"-an -c:v {_nvenc_opts()} {q(output_video)}"
    )


def build_roi_chunk_cmd(full_chunk: Path, roi: RoiBox | Mapping[str, int], fps: int, output_roi: Path) -> str:
    """Crop to the streamer ROI and re-encode with NVENC."""
    roi_box = RoiBox.from_value(roi)
    crop = f'crop={roi_box.w}:{roi_box.h}:{roi_box.x}:{roi_box.y},fps={fps}'
    return f'ffmpeg -y -i {q(full_chunk)} -vf "{crop}" -an -c:v {_nvenc_opts()} {q(output_roi)}'


def build_loop_source_cmd(source_video: Path, duration_seconds: float, output_video: Path) -> str:
    """Loop the source avatar video for the chunk duration using NVENC."""
    return f'ffmpeg -y -stream_loop -1 -i {q(source_video)} -t {duration_seconds} -an -c:v {_nvenc_opts()} {q(output_video)}'


def build_liveportrait_cmd(
    source_video: Path,
    driving_video: Path,
    output_dir: Path,
    liveportrait_flags: LivePortraitFlags | Mapping[str, bool],
) -> str:
    """Run LivePortrait inference on CUDA.

    Uses the generic 'python' interpreter (respects active venv).
    Does NOT force CPU execution — CUDA is used when available.
    """
    flags = LivePortraitFlags.from_value(liveportrait_flags)

    # Safety: if somehow flag_force_cpu is True in the profile, override it
    # for the GPU pipeline — this module is explicitly for CUDA.
    force_cpu = False

    parts = [
        "$env:PYTHONIOENCODING='utf-8';",
        _python_exe(),
        "inference.py",
        f"-s {q(source_video)}",
        f"-d {q(driving_video)}",
        f"-o {q(output_dir)}",
        "--flag-eye-retargeting" if flags.flag_eye_retargeting else "--no-flag-eye-retargeting",
        "--flag-force-cpu" if force_cpu else "--no-flag-force-cpu",
        "--flag-pasteback" if flags.flag_pasteback else "--no-flag-pasteback",
        "--flag-use-half-precision" if flags.flag_use_half_precision else "--no-flag-use-half-precision",
    ]
    return " ".join(parts)


def build_composite_cmd(
    full_chunk: Path,
    patch_video: Path,
    output_video: Path,
    roi: RoiBox | Mapping[str, int],
    compositor: CompositorSettings | Mapping[str, float],
) -> str:
    """Composite the LivePortrait patch onto the full gameplay chunk.

    This step uses OpenCV (CPU-bound) and is shared with the CPU pipeline.
    The command is identical — only the Python interpreter is generic.
    """
    roi_box = RoiBox.from_value(roi)
    compositor_settings = CompositorSettings.from_value(compositor)
    return (
        f'{_python_exe()} tools/composite_key_overlay.py '
        f'--full-video {q(full_chunk)} --patch-video {q(patch_video)} --output {q(output_video)} '
        f'--x {roi_box.x} --y {roi_box.y} --width {roi_box.w} --height {roi_box.h} '
        f'--alpha-cutoff {compositor_settings.alpha_cutoff} --alpha-erode {compositor_settings.alpha_erode} '
        f'--alpha-post-blur {compositor_settings.alpha_post_blur} --handoff-start {compositor_settings.handoff_start} '
        f'--handoff-end {compositor_settings.handoff_end} --suppress-strength {compositor_settings.suppress_strength} '
        f'--suppress-dilate {compositor_settings.suppress_dilate} --suppress-blur {compositor_settings.suppress_blur} '
        f'--suppress-roi-blur-ksize {compositor_settings.suppress_roi_blur_ksize}'
    )


def build_mux_audio_cmd(original_gameplay: Path, composited_visual: Path, output_final: Path) -> str:
    """Mux audio from original gameplay onto the composited video (no re-encode)."""
    return (
        f'ffmpeg -y -i {q(composited_visual)} -i {q(original_gameplay)} '
        f'-map 0:v:0 -map 1:a:0 -c:v copy -c:a copy -shortest {q(output_final)}'
    )


def build_concat_visual_cmd(list_file: Path, output_video: Path) -> str:
    """Concatenate all composited chunks using NVENC for the final visual output."""
    return (
        f"ffmpeg -y -f concat -safe 0 -i {q(list_file)} "
        f"-an -c:v {_nvenc_opts()} {q(output_video)}"
    )
