"""ProgressTracker writes live pipeline state to a JSON file for the monitor."""

import json
import os
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path

from tools.pipeline_runtime import JobManifest


@dataclass
class StageRecord:
    name: str
    status: str = "pending"  # pending | running | done | failed
    progress: float = 0.0    # 0.0 .. 1.0
    frames_done: int | None = None
    frames_total: int | None = None
    elapsed_seconds: float | None = None
    estimated_total_seconds: float | None = None
    error: str | None = None


@dataclass
class ChunkRecord:
    index: int
    status: str = "pending"  # pending | processing | done | failed
    stages: list[StageRecord] = field(default_factory=list)


@dataclass
class PipelineState:
    video_id: str
    input_video: str
    total_seconds: float
    total_chunks: int
    started_at: str
    current_chunk_index: int | None = None
    chunks: list[ChunkRecord] = field(default_factory=list)
    global_eta_seconds: float | None = None
    last_updated: str = ""


class ProgressTracker:
    def __init__(self, job_dir: Path, manifest: JobManifest):
        self.job_dir = job_dir
        self.state_path = job_dir / "state.json"
        self.manifest = manifest
        self._started_monotonic = time.perf_counter()
        self._last_flush_monotonic = 0.0
        self._stage_start_time: float | None = None
        self._liveportrait_times: list[float] = []
        self._composite_times: list[float] = []

        self.state = PipelineState(
            video_id=manifest.video_id,
            input_video=manifest.input_video,
            total_seconds=manifest.total_seconds,
            total_chunks=len(manifest.chunks),
            started_at=_iso_now(),
            chunks=[
                ChunkRecord(
                    index=chunk.index,
                    stages=[
                        StageRecord(name="extract"),
                        StageRecord(name="roi_crop"),
                        StageRecord(name="source_loop"),
                        StageRecord(name="liveportrait"),
                        StageRecord(name="composite"),
                    ],
                )
                for chunk in manifest.chunks
            ],
        )
        self._flush()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------
    def mark_chunk_done(self, chunk_index: int) -> None:
        chunk = self.state.chunks[chunk_index]
        chunk.status = "done"
        for stage in chunk.stages:
            stage.status = "done"
            stage.progress = 1.0
        self._flush()

    def begin_stage(
        self,
        chunk_index: int,
        stage_name: str,
        frames_total: int | None = None,
    ) -> None:
        self.state.current_chunk_index = chunk_index
        chunk = self.state.chunks[chunk_index]
        chunk.status = "processing"
        stage = self._find_stage(chunk, stage_name)
        stage.status = "running"
        stage.progress = 0.0
        stage.frames_total = frames_total
        stage.frames_done = 0
        self._stage_start_time = time.perf_counter()
        self._flush()

    def update_stage(
        self,
        chunk_index: int,
        stage_name: str,
        frames_done: int | None = None,
        progress: float | None = None,
    ) -> None:
        stage = self._find_stage(self.state.chunks[chunk_index], stage_name)
        if frames_done is not None:
            stage.frames_done = frames_done
            if stage.frames_total:
                stage.progress = min(1.0, frames_done / stage.frames_total)
        if progress is not None:
            stage.progress = min(1.0, max(0.0, progress))
        self._flush(force=False)

    def end_stage(self, chunk_index: int, stage_name: str) -> None:
        stage = self._find_stage(self.state.chunks[chunk_index], stage_name)
        stage.status = "done"
        stage.progress = 1.0
        elapsed = time.perf_counter() - self._stage_start_time if self._stage_start_time else 0.0
        stage.elapsed_seconds = elapsed

        if stage_name == "liveportrait":
            self._liveportrait_times.append(elapsed)
        elif stage_name == "composite":
            self._composite_times.append(elapsed)

        self._stage_start_time = None
        self._recalculate_eta()
        self._flush()

    def fail_stage(self, chunk_index: int, stage_name: str, error: str) -> None:
        chunk = self.state.chunks[chunk_index]
        chunk.status = "failed"
        stage = self._find_stage(chunk, stage_name)
        stage.status = "failed"
        stage.error = error
        self._flush()

    def close(self) -> None:
        self._flush()

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------
    def _find_stage(self, chunk: ChunkRecord, name: str) -> StageRecord:
        for stage in chunk.stages:
            if stage.name == name:
                return stage
        raise KeyError(f"Stage {name!r} not found in chunk {chunk.index}")

    def _recalculate_eta(self) -> None:
        done = sum(1 for c in self.state.chunks if c.status == "done")
        pending = sum(1 for c in self.state.chunks if c.status == "pending")
        if done == 0:
            self.state.global_eta_seconds = None
            return
        elapsed_total = time.perf_counter() - self._started_monotonic
        avg_per_chunk = elapsed_total / done
        self.state.global_eta_seconds = avg_per_chunk * pending

    def _flush(self, force: bool = True) -> None:
        now = time.perf_counter()
        if not force and now - self._last_flush_monotonic < 0.25:
            return

        self.state.last_updated = _iso_now()
        payload = json.dumps(asdict(self.state), indent=2)
        tmp = self.state_path.with_suffix(f".{os.getpid()}.tmp")

        # State updates are best-effort. Monitoring must never abort the job.
        for _ in range(5):
            try:
                tmp.write_text(payload, encoding="utf-8")
                os.replace(str(tmp), str(self.state_path))
                self._last_flush_monotonic = time.perf_counter()
                return
            except OSError:
                time.sleep(0.05)

        try:
            self.state_path.write_text(payload, encoding="utf-8")
            self._last_flush_monotonic = time.perf_counter()
        except OSError:
            pass
        finally:
            try:
                if tmp.exists():
                    tmp.unlink()
            except OSError:
                pass


def _iso_now() -> str:
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).isoformat()
