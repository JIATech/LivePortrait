from dataclasses import dataclass
from pathlib import Path
from typing import Mapping


@dataclass(frozen=True)
class RoiBox:
    x: int
    y: int
    w: int
    h: int

    @classmethod
    def from_value(cls, value: "RoiBox | Mapping[str, int]") -> "RoiBox":
        if isinstance(value, cls):
            return value
        if isinstance(value, Mapping):
            return cls(
                x=_require_int(value, "x"),
                y=_require_int(value, "y"),
                w=_require_int(value, "w"),
                h=_require_int(value, "h"),
            )
        raise TypeError("roi must be a RoiBox or mapping with x, y, w, h")


@dataclass(frozen=True)
class LivePortraitFlags:
    flag_eye_retargeting: bool
    flag_force_cpu: bool
    flag_pasteback: bool
    flag_use_half_precision: bool

    @classmethod
    def from_value(cls, value: "LivePortraitFlags | Mapping[str, bool]") -> "LivePortraitFlags":
        if isinstance(value, cls):
            return value
        if isinstance(value, Mapping):
            return cls(
                flag_eye_retargeting=_require_bool(value, "flag_eye_retargeting"),
                flag_force_cpu=_require_bool(value, "flag_force_cpu"),
                flag_pasteback=_require_bool(value, "flag_pasteback"),
                flag_use_half_precision=_require_bool(value, "flag_use_half_precision"),
            )
        raise TypeError(
            "liveportrait_flags must be a LivePortraitFlags or mapping with expected flag keys"
        )


@dataclass(frozen=True)
class CompositorSettings:
    alpha_cutoff: float
    alpha_erode: int
    alpha_post_blur: float
    handoff_start: float
    handoff_end: float
    suppress_strength: float
    suppress_dilate: int
    suppress_blur: float
    suppress_roi_blur_ksize: int

    @classmethod
    def from_value(cls, value: "CompositorSettings | Mapping[str, float]") -> "CompositorSettings":
        if isinstance(value, cls):
            return value
        if isinstance(value, Mapping):
            return cls(
                alpha_cutoff=_require_float(value, "alpha_cutoff"),
                alpha_erode=_require_int(value, "alpha_erode"),
                alpha_post_blur=_require_float(value, "alpha_post_blur"),
                handoff_start=_require_float(value, "handoff_start"),
                handoff_end=_require_float(value, "handoff_end"),
                suppress_strength=_require_float(value, "suppress_strength"),
                suppress_dilate=_require_int(value, "suppress_dilate"),
                suppress_blur=_require_float(value, "suppress_blur"),
                suppress_roi_blur_ksize=_require_int(value, "suppress_roi_blur_ksize"),
            )
        raise TypeError(
            "compositor must be a CompositorSettings or mapping with expected compositor keys"
        )


def _require_int(data: Mapping[str, object], key: str) -> int:
    value = data[key]
    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError(f"{key} must be an int")
    return value


def _require_bool(data: Mapping[str, object], key: str) -> bool:
    value = data[key]
    if not isinstance(value, bool):
        raise TypeError(f"{key} must be a bool")
    return value


def _require_float(data: Mapping[str, object], key: str) -> float:
    value = data[key]
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise TypeError(f"{key} must be a float")
    return float(value)


def q(path: Path) -> str:
    escaped = path.as_posix().replace("'", "''")
    return f"'{escaped}'"


def build_full_chunk_cmd(input_video: Path, start_seconds: float, duration_seconds: float, output_video: Path) -> str:
    return (
        f"ffmpeg -y -ss {start_seconds} -i {q(input_video)} -t {duration_seconds} "
        f"-c:v libx264 -crf 18 -an {q(output_video)}"
    )


def build_roi_chunk_cmd(full_chunk: Path, roi: RoiBox | Mapping[str, int], fps: int, output_roi: Path) -> str:
    roi_box = RoiBox.from_value(roi)
    crop = f'crop={roi_box.w}:{roi_box.h}:{roi_box.x}:{roi_box.y},fps={fps}'
    return f'ffmpeg -y -i {q(full_chunk)} -vf "{crop}" -an -c:v libx264 -crf 18 {q(output_roi)}'


def build_loop_source_cmd(source_video: Path, duration_seconds: float, output_video: Path) -> str:
    return f'ffmpeg -y -stream_loop -1 -i {q(source_video)} -t {duration_seconds} -an -c:v libx264 -crf 18 {q(output_video)}'


def build_liveportrait_cmd(
    source_video: Path,
    driving_video: Path,
    output_dir: Path,
    liveportrait_flags: LivePortraitFlags | Mapping[str, bool],
) -> str:
    flags = LivePortraitFlags.from_value(liveportrait_flags)
    parts = [
        "$env:PYTHONIOENCODING='utf-8'; & \".venv311\\Scripts\\python.exe\" inference.py",
        f'-s {q(source_video)}',
        f'-d {q(driving_video)}',
        f'-o {q(output_dir)}',
        "--flag-eye-retargeting" if flags.flag_eye_retargeting else "--no-flag-eye-retargeting",
        "--flag-force-cpu" if flags.flag_force_cpu else "--no-flag-force-cpu",
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
    roi_box = RoiBox.from_value(roi)
    compositor_settings = CompositorSettings.from_value(compositor)
    return (
        '& ".venv311\\Scripts\\python.exe" tools\\composite_key_overlay.py '
        f'--full-video {q(full_chunk)} --patch-video {q(patch_video)} --output {q(output_video)} '
        f'--x {roi_box.x} --y {roi_box.y} --width {roi_box.w} --height {roi_box.h} '
        f'--alpha-cutoff {compositor_settings.alpha_cutoff} --alpha-erode {compositor_settings.alpha_erode} '
        f'--alpha-post-blur {compositor_settings.alpha_post_blur} --handoff-start {compositor_settings.handoff_start} '
        f'--handoff-end {compositor_settings.handoff_end} --suppress-strength {compositor_settings.suppress_strength} '
        f'--suppress-dilate {compositor_settings.suppress_dilate} --suppress-blur {compositor_settings.suppress_blur} '
        f'--suppress-roi-blur-ksize {compositor_settings.suppress_roi_blur_ksize}'
    )


def build_mux_audio_cmd(original_gameplay: Path, composited_visual: Path, output_final: Path) -> str:
    return (
        f'ffmpeg -y -i {q(composited_visual)} -i {q(original_gameplay)} '
        f'-map 0:v:0 -map 1:a:0 -c:v copy -c:a copy -shortest {q(output_final)}'
    )


def build_concat_visual_cmd(list_file: Path, output_video: Path) -> str:
    return (
        f"ffmpeg -y -f concat -safe 0 -i {q(list_file)} "
        f"-c:v libx264 -crf 18 -an {q(output_video)}"
    )
