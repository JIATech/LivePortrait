import hashlib
import json
import math
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any


@dataclass
class PipelineProfile:
    chunk_seconds: int
    roi: dict[str, int]
    source_master: str
    source_fps: int
    liveportrait: dict[str, Any]
    compositor: dict[str, Any]


@dataclass
class ChunkRecord:
    index: int
    start_seconds: float
    duration_seconds: float
    status: str = "pending"
    error: str | None = None


@dataclass
class JobManifest:
    video_id: str
    input_video: str
    total_seconds: float
    chunk_seconds: int
    source_master: str
    chunks: list[ChunkRecord]
    profile: dict[str, Any] | None = None


def build_video_id(input_video: Path) -> str:
    digest = hashlib.sha1(str(input_video).encode("utf-8")).hexdigest()[:8]
    return f"{input_video.stem}-{digest}"


def plan_chunks(total_seconds: float, chunk_seconds: int) -> list[ChunkRecord]:
    if chunk_seconds <= 0:
        raise ValueError("chunk_seconds must be greater than 0")

    count = math.ceil(total_seconds / chunk_seconds)
    chunks: list[ChunkRecord] = []
    for index in range(count):
        start = float(index * chunk_seconds)
        duration = min(float(chunk_seconds), max(0.0, total_seconds - start))
        chunks.append(ChunkRecord(index=index, start_seconds=start, duration_seconds=duration))
    return chunks


def save_manifest(path: Path, manifest: JobManifest) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(asdict(manifest), indent=2), encoding="utf-8")


def _manifest_profile(profile: PipelineProfile) -> dict[str, Any]:
    return {
        "roi": profile.roi,
        "source_fps": profile.source_fps,
        "liveportrait": profile.liveportrait,
        "compositor": profile.compositor,
    }


def _manifest_from_dict(data: dict[str, Any]) -> JobManifest:
    return JobManifest(
        video_id=data["video_id"],
        input_video=data["input_video"],
        total_seconds=data["total_seconds"],
        chunk_seconds=data["chunk_seconds"],
        source_master=data["source_master"],
        profile=data.get("profile"),
        chunks=[ChunkRecord(**chunk) for chunk in data["chunks"]],
    )


def _build_manifest(input_video: Path, total_seconds: float, profile: PipelineProfile) -> JobManifest:
    return JobManifest(
        video_id=build_video_id(input_video),
        input_video=input_video.as_posix(),
        total_seconds=total_seconds,
        chunk_seconds=profile.chunk_seconds,
        source_master=profile.source_master,
        profile=_manifest_profile(profile),
        chunks=plan_chunks(total_seconds=total_seconds, chunk_seconds=profile.chunk_seconds),
    )


def _manifest_matches(manifest: JobManifest, input_video: Path, total_seconds: float, profile: PipelineProfile) -> bool:
    return (
        manifest.input_video == input_video.as_posix()
        and manifest.total_seconds == total_seconds
        and manifest.chunk_seconds == profile.chunk_seconds
        and manifest.source_master == profile.source_master
        and manifest.profile == _manifest_profile(profile)
    )


def load_or_create_manifest(
    job_dir: Path,
    input_video: Path,
    total_seconds: float,
    profile: PipelineProfile,
) -> JobManifest:
    manifest_path = job_dir / "manifests" / "manifest.json"

    if manifest_path.exists():
        manifest = _manifest_from_dict(json.loads(manifest_path.read_text(encoding="utf-8")))
        if _manifest_matches(manifest, input_video, total_seconds, profile):
            return manifest

    manifest = _build_manifest(input_video, total_seconds, profile)
    save_manifest(manifest_path, manifest)
    return manifest
