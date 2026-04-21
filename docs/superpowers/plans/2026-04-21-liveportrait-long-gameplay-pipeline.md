# LivePortrait Long Gameplay Pipeline Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a resumable, chunked, single-purpose pipeline that scans `gameplays_crudos/`, processes long gameplay videos with the validated LivePortrait avatar workflow, and writes `output/<video_id>/final.mp4` with untouched original audio.

**Architecture:** The pipeline is a calibrated v1 orchestrator around three existing capabilities: FFmpeg chunking/remuxing, LivePortrait patch generation, and the custom compositor in `tools/composite_key_overlay.py`. It persists per-video manifests inside `.pipeline_work/` so every chunk can be retried or resumed independently, and it loops the 10-second source master to each chunk duration before inference so long chunks do not truncate.

**Tech Stack:** Python 3.11, stdlib `unittest`, JSON manifests, FFmpeg/ffprobe subprocesses, existing `inference.py`, existing `tools/composite_key_overlay.py`

---

## File structure

- Create: `pipeline_profiles/liveportrait_gameplay_v1.json` — editable runtime profile with fixed ROI box, chunk duration, source master path, LivePortrait flags, and compositor parameters.
- Create: `tools/pipeline_runtime.py` — pure data model + manifest + path helpers (`PipelineProfile`, `ChunkRecord`, `JobManifest`, `build_video_id`, `plan_chunks`, `load_manifest`, `save_manifest`).
- Create: `tools/pipeline_commands.py` — deterministic builders for FFmpeg, LivePortrait, compositor, concat, and remux commands.
- Create: `tools/run_long_gameplay_pipeline.py` — CLI orchestrator that discovers jobs, prepares source assets, processes chunks with resume support, assembles output, and writes reports.
- Modify: `tools/composite_key_overlay.py` — keep existing CLI-compatible behavior, no architectural rewrite required beyond using it from the orchestrator via subprocess.
- Create: `tests/test_pipeline_runtime.py` — unit tests for job IDs, chunk planning, manifest persistence, and path rules.
- Create: `tests/test_pipeline_commands.py` — unit tests for exact command construction, especially source looping and chunk outputs.
- Create: `tests/test_run_long_gameplay_pipeline.py` — unit tests for job discovery, resume filtering, runner ordering, and final report assembly with fake command runners.

All commands below assume execution from the repo root using the current Windows venv:

```powershell
& ".venv311\Scripts\python.exe" ...
```

## Shared implementation notes

- The source master is only ~10 seconds. **Every chunk must use an automatically looped source clip** matching the chunk duration before calling `inference.py`.
- The orchestrator must treat visual quality problems as acceptable best-effort output; only technical failures mark a chunk `failed`.
- The final pipeline must preserve the original gameplay audio untouched by remuxing it back only after all visual chunks are concatenated.
- Do not delete successful chunk outputs on rerun. Resume must start at the first non-`done` chunk.

### Task 1: Profile and manifest primitives

**Files:**
- Create: `pipeline_profiles/liveportrait_gameplay_v1.json`
- Create: `tools/pipeline_runtime.py`
- Test: `tests/test_pipeline_runtime.py`

- [ ] **Step 1: Write the failing runtime tests**

```python
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
            save_manifest(root / "manifest.json", manifest)

            reloaded = load_or_create_manifest(
                job_dir=root,
                input_video=Path("gameplays_crudos/demo.mp4"),
                total_seconds=61.0,
                profile=profile,
            )
            self.assertEqual(reloaded.chunks[0].status, "done")
            self.assertEqual(reloaded.chunks[1].status, "pending")


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run the runtime tests and verify they fail for the expected reason**

Run:

```powershell
& ".venv311\Scripts\python.exe" -m unittest tests.test_pipeline_runtime -v
```

Expected:
- FAIL / ERROR because `tools.pipeline_runtime` does not exist yet

- [ ] **Step 3: Create the editable profile file**

```json
{
  "chunk_seconds": 60,
  "roi": {
    "x": 6,
    "y": 811,
    "w": 259,
    "h": 268
  },
  "source_master": "john/john_video_45deg_ver4.mp4",
  "source_fps": 12,
  "liveportrait": {
    "flag_eye_retargeting": true,
    "flag_force_cpu": true,
    "flag_pasteback": false,
    "flag_use_half_precision": false
  },
  "compositor": {
    "alpha_cutoff": 0.48,
    "alpha_erode": 5,
    "alpha_post_blur": 0.45,
    "handoff_start": 0.72,
    "handoff_end": 0.94,
    "suppress_strength": 0.60,
    "suppress_dilate": 13,
    "suppress_blur": 3.0,
    "suppress_roi_blur_ksize": 19
  }
}
```

- [ ] **Step 4: Implement runtime dataclasses and manifest helpers**

```python
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


def build_video_id(input_video: Path) -> str:
    digest = hashlib.sha1(str(input_video).encode("utf-8")).hexdigest()[:8]
    return f"{input_video.stem}-{digest}"


def plan_chunks(total_seconds: float, chunk_seconds: int) -> list[ChunkRecord]:
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


def _manifest_from_dict(data: dict[str, Any]) -> JobManifest:
    return JobManifest(
        video_id=data["video_id"],
        input_video=data["input_video"],
        total_seconds=data["total_seconds"],
        chunk_seconds=data["chunk_seconds"],
        source_master=data["source_master"],
        chunks=[ChunkRecord(**chunk) for chunk in data["chunks"]],
    )


def load_or_create_manifest(job_dir: Path, input_video: Path, total_seconds: float, profile: PipelineProfile) -> JobManifest:
    manifest_path = job_dir / "manifests" / "manifest.json"
    if manifest_path.exists():
        return _manifest_from_dict(json.loads(manifest_path.read_text(encoding="utf-8")))

    manifest = JobManifest(
        video_id=build_video_id(input_video),
        input_video=input_video.as_posix(),
        total_seconds=total_seconds,
        chunk_seconds=profile.chunk_seconds,
        source_master=profile.source_master,
        chunks=plan_chunks(total_seconds=total_seconds, chunk_seconds=profile.chunk_seconds),
    )
    save_manifest(manifest_path, manifest)
    return manifest
```

- [ ] **Step 5: Re-run the runtime tests and verify they pass**

Run:

```powershell
& ".venv311\Scripts\python.exe" -m unittest tests.test_pipeline_runtime -v
```

Expected:
- PASS for all tests in `tests.test_pipeline_runtime`

- [ ] **Step 6: Optional commit checkpoint (only if the user explicitly asks for commits)**

```bash
git add pipeline_profiles/liveportrait_gameplay_v1.json tools/pipeline_runtime.py tests/test_pipeline_runtime.py
git commit -m "feat: add pipeline runtime primitives"
```

### Task 2: Deterministic command builders

**Files:**
- Create: `tools/pipeline_commands.py`
- Test: `tests/test_pipeline_commands.py`

- [ ] **Step 1: Write the failing command-builder tests**

```python
import unittest
from pathlib import Path

from tools.pipeline_commands import (
    build_composite_cmd,
    build_full_chunk_cmd,
    build_liveportrait_cmd,
    build_loop_source_cmd,
    build_mux_audio_cmd,
    build_roi_chunk_cmd,
)


class PipelineCommandTests(unittest.TestCase):
    def test_build_loop_source_cmd_extends_source_to_chunk_duration(self):
        cmd = build_loop_source_cmd(
            source_video=Path(".pipeline_work/job/source/source_12fps.mp4"),
            duration_seconds=60.0,
            output_video=Path(".pipeline_work/job/source/chunk_000_source_looped.mp4"),
        )
        self.assertIn("-stream_loop -1", cmd)
        self.assertIn("-t 60.0", cmd)

    def test_build_roi_chunk_cmd_uses_fixed_crop_box(self):
        cmd = build_roi_chunk_cmd(
            full_chunk=Path("chunk_full.mp4"),
            roi={"x": 6, "y": 811, "w": 259, "h": 268},
            fps=12,
            output_roi=Path("chunk_roi.mp4"),
        )
        self.assertIn("crop=259:268:6:811", cmd)
        self.assertIn("fps=12", cmd)

    def test_build_liveportrait_cmd_enables_eye_retargeting(self):
        cmd = build_liveportrait_cmd(
            source_video=Path("source_looped.mp4"),
            driving_video=Path("chunk_roi.mp4"),
            output_dir=Path("chunk_out"),
            liveportrait_flags={
                "flag_eye_retargeting": True,
                "flag_force_cpu": True,
                "flag_pasteback": False,
                "flag_use_half_precision": False,
            },
        )
        self.assertIn("inference.py", cmd)
        self.assertIn("--flag-eye-retargeting", cmd)
        self.assertIn("--flag-force-cpu", cmd)
        self.assertIn("--no-flag-pasteback", cmd)

    def test_build_mux_audio_cmd_preserves_original_audio(self):
        cmd = build_mux_audio_cmd(
            original_gameplay=Path("gameplay.mp4"),
            composited_visual=Path("visual.mp4"),
            output_final=Path("final.mp4"),
        )
        self.assertIn("-map 0:v:0", cmd)
        self.assertIn("-map 1:a:0", cmd)
        self.assertIn("-c:v copy", cmd)
```

- [ ] **Step 2: Run the command-builder tests and verify they fail**

Run:

```powershell
& ".venv311\Scripts\python.exe" -m unittest tests.test_pipeline_commands -v
```

Expected:
- FAIL / ERROR because `tools.pipeline_commands` does not exist yet

- [ ] **Step 3: Implement the command builders**

```python
from pathlib import Path


def q(path: Path) -> str:
    return f'"{path.as_posix()}"'


def build_full_chunk_cmd(input_video: Path, start_seconds: float, duration_seconds: float, output_video: Path) -> str:
    return (
        f'ffmpeg -y -ss {start_seconds} -i {q(input_video)} -t {duration_seconds} '
        f'-c:v libx264 -crf 18 -an {q(output_video)}'
    )


def build_roi_chunk_cmd(full_chunk: Path, roi: dict[str, int], fps: int, output_roi: Path) -> str:
    crop = f'crop={roi["w"]}:{roi["h"]}:{roi["x"]}:{roi["y"]},fps={fps}'
    return f'ffmpeg -y -i {q(full_chunk)} -vf "{crop}" -an -c:v libx264 -crf 18 {q(output_roi)}'


def build_loop_source_cmd(source_video: Path, duration_seconds: float, output_video: Path) -> str:
    return f'ffmpeg -y -stream_loop -1 -i {q(source_video)} -t {duration_seconds} -an -c:v libx264 -crf 18 {q(output_video)}'


def build_liveportrait_cmd(source_video: Path, driving_video: Path, output_dir: Path, liveportrait_flags: dict[str, bool]) -> str:
    parts = [
        '& ".venv311\\Scripts\\python.exe" inference.py',
        f'-s {q(source_video)}',
        f'-d {q(driving_video)}',
        f'-o {q(output_dir)}',
    ]
    parts.append("--flag-eye-retargeting" if liveportrait_flags["flag_eye_retargeting"] else "--no-flag-eye-retargeting")
    parts.append("--flag-force-cpu" if liveportrait_flags["flag_force_cpu"] else "--no-flag-force-cpu")
    parts.append("--flag-pasteback" if liveportrait_flags["flag_pasteback"] else "--no-flag-pasteback")
    parts.append("--flag-use-half-precision" if liveportrait_flags["flag_use_half_precision"] else "--no-flag-use-half-precision")
    return " ".join(parts)


def build_composite_cmd(full_chunk: Path, patch_video: Path, output_video: Path, roi: dict[str, int], compositor: dict[str, float]) -> str:
    return (
        '& ".venv311\\Scripts\\python.exe" tools\\composite_key_overlay.py '
        f'--full-video {q(full_chunk)} --patch-video {q(patch_video)} --output {q(output_video)} '
        f'--x {roi["x"]} --y {roi["y"]} --width {roi["w"]} --height {roi["h"]} '
        f'--alpha-cutoff {compositor["alpha_cutoff"]} --alpha-erode {compositor["alpha_erode"]} '
        f'--alpha-post-blur {compositor["alpha_post_blur"]} --handoff-start {compositor["handoff_start"]} '
        f'--handoff-end {compositor["handoff_end"]} --suppress-strength {compositor["suppress_strength"]} '
        f'--suppress-dilate {compositor["suppress_dilate"]} --suppress-blur {compositor["suppress_blur"]} '
        f'--suppress-roi-blur-ksize {compositor["suppress_roi_blur_ksize"]}'
    )


def build_mux_audio_cmd(original_gameplay: Path, composited_visual: Path, output_final: Path) -> str:
    return (
        f'ffmpeg -y -i {q(composited_visual)} -i {q(original_gameplay)} '
        f'-map 0:v:0 -map 1:a:0 -c:v copy -c:a copy -shortest {q(output_final)}'
    )
```

- [ ] **Step 4: Re-run the command-builder tests and verify they pass**

Run:

```powershell
& ".venv311\Scripts\python.exe" -m unittest tests.test_pipeline_commands -v
```

Expected:
- PASS for all tests in `tests.test_pipeline_commands`

- [ ] **Step 5: Optional commit checkpoint (only if the user explicitly asks for commits)**

```bash
git add tools/pipeline_commands.py tests/test_pipeline_commands.py
git commit -m "feat: add pipeline command builders"
```

### Task 3: Job discovery and resume-aware orchestration skeleton

**Files:**
- Create: `tools/run_long_gameplay_pipeline.py`
- Test: `tests/test_run_long_gameplay_pipeline.py`

- [ ] **Step 1: Write the failing orchestration tests**

```python
import tempfile
import unittest
from pathlib import Path

from tools.run_long_gameplay_pipeline import discover_input_videos, pending_chunk_indices
from tools.pipeline_runtime import ChunkRecord, JobManifest


class PipelineOrchestratorTests(unittest.TestCase):
    def test_discover_input_videos_only_returns_mp4_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "a.mp4").write_bytes(b"a")
            (root / "clip.MP4").write_bytes(b"c")
            (root / "b.mov").write_bytes(b"b")
            (root / "note.txt").write_text("ignore", encoding="utf-8")
            found = discover_input_videos(root)
            self.assertEqual([p.name for p in found], ["a.mp4", "clip.MP4"])

    def test_discover_input_videos_returns_empty_list_for_non_directory_path(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            missing = root / "missing_dir"
            found = discover_input_videos(missing)
            self.assertEqual(found, [])

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


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run the orchestration tests and verify they fail**

Run:

```powershell
& ".venv311\Scripts\python.exe" -m unittest tests.test_run_long_gameplay_pipeline -v
```

Expected:
- FAIL / ERROR because `tools.run_long_gameplay_pipeline` does not exist yet

- [ ] **Step 3: Implement the orchestration skeleton and pure helpers**

```python
import argparse
import json
from pathlib import Path

from tools.pipeline_runtime import PipelineProfile, load_or_create_manifest


def discover_input_videos(input_dir: Path) -> list[Path]:
    return sorted(path for path in input_dir.glob("*.mp4") if path.is_file())


def pending_chunk_indices(manifest) -> list[int]:
    return [chunk.index for chunk in manifest.chunks if chunk.status != "done"]


def load_profile(path: Path) -> PipelineProfile:
    data = json.loads(path.read_text(encoding="utf-8"))
    return PipelineProfile(**data)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run the long gameplay LivePortrait pipeline")
    parser.add_argument("--input-dir", default="gameplays_crudos")
    parser.add_argument("--output-dir", default="output")
    parser.add_argument("--work-dir", default=".pipeline_work")
    parser.add_argument("--profile", default="pipeline_profiles/liveportrait_gameplay_v1.json")
    parser.add_argument("--video", default=None)
    return parser.parse_args()
```

- [ ] **Step 4: Re-run the orchestration tests and verify they pass**

Run:

```powershell
& ".venv311\Scripts\python.exe" -m unittest tests.test_run_long_gameplay_pipeline -v
```

Expected:
- PASS for all tests in `tests.test_run_long_gameplay_pipeline`

- [ ] **Step 5: Optional commit checkpoint (only if the user explicitly asks for commits)**

```bash
git add tools/run_long_gameplay_pipeline.py tests/test_run_long_gameplay_pipeline.py
git commit -m "feat: add pipeline orchestrator skeleton"
```

### Task 4: Chunk execution and resume-aware processing

**Files:**
- Modify: `tools/run_long_gameplay_pipeline.py`
- Modify: `tests/test_run_long_gameplay_pipeline.py`

- [ ] **Step 1: Extend the orchestration tests with a fake runner**

```python
from tools.run_long_gameplay_pipeline import process_chunk


class FakeRunner:
    def __init__(self):
        self.commands = []

    def __call__(self, command: str) -> None:
        self.commands.append(command)


def test_process_chunk_runs_expected_stages_in_order(self):
    runner = FakeRunner()
    paths = {
        "full_chunk": Path("full/chunk_000.mp4"),
        "roi_chunk": Path("roi/chunk_000_roi.mp4"),
        "source_loop": Path("source/chunk_000_source_loop.mp4"),
        "patch_dir": Path("liveportrait/chunk_000"),
        "patch_video": Path("liveportrait/chunk_000/patch.mp4"),
        "composited": Path("composited/chunk_000_composited.mp4"),
    }
    process_chunk(
        runner=runner,
        input_video=Path("gameplays_crudos/demo.mp4"),
        source_prepared=Path("source/source_master_12fps.mp4"),
        chunk_index=0,
        start_seconds=0.0,
        duration_seconds=60.0,
        paths=paths,
        profile=load_profile(Path("pipeline_profiles/liveportrait_gameplay_v1.json")),
    )
    assert len(runner.commands) == 5
    assert "crop=" in runner.commands[1]
    assert "inference.py" in runner.commands[3]
```

- [ ] **Step 2: Run the orchestration tests and verify they fail on the missing processor**

Run:

```powershell
& ".venv311\Scripts\python.exe" -m unittest tests.test_run_long_gameplay_pipeline -v
```

Expected:
- FAIL / ERROR because `process_chunk` does not exist yet

- [ ] **Step 3: Implement per-chunk path planning and execution**

```python
from tools.pipeline_commands import (
    build_composite_cmd,
    build_full_chunk_cmd,
    build_liveportrait_cmd,
    build_loop_source_cmd,
    build_roi_chunk_cmd,
)


def process_chunk(runner, input_video, source_prepared, chunk_index, start_seconds, duration_seconds, paths, profile):
    runner(build_full_chunk_cmd(input_video, start_seconds, duration_seconds, paths["full_chunk"]))
    runner(build_roi_chunk_cmd(paths["full_chunk"], profile.roi, profile.source_fps, paths["roi_chunk"]))
    runner(build_loop_source_cmd(source_prepared, duration_seconds, paths["source_loop"]))
    runner(build_liveportrait_cmd(paths["source_loop"], paths["roi_chunk"], paths["patch_dir"], profile.liveportrait))
    runner(build_composite_cmd(paths["full_chunk"], paths["patch_video"], paths["composited"], profile.roi, profile.compositor))
```

- [ ] **Step 4: Wire `process_chunk` into a resume-aware job runner**

```python
def run_job(input_video: Path, profile: PipelineProfile, output_dir: Path, work_dir: Path, runner) -> None:
    job_dir = work_dir / build_video_id(input_video)
    manifest = load_or_create_manifest(job_dir, input_video, probe_duration_seconds(input_video, runner), profile)
    source_prepared = prepare_source_master(job_dir, profile, runner)

    for chunk in manifest.chunks:
        if chunk.status == "done":
            continue
        try:
            paths = build_chunk_paths(job_dir, chunk.index)
            process_chunk(
                runner=runner,
                input_video=input_video,
                source_prepared=source_prepared,
                chunk_index=chunk.index,
                start_seconds=chunk.start_seconds,
                duration_seconds=chunk.duration_seconds,
                paths=paths,
                profile=profile,
            )
            chunk.status = "done"
            chunk.error = None
        except Exception as exc:
            chunk.status = "failed"
            chunk.error = str(exc)
            raise
        finally:
            save_manifest(job_dir / "manifests" / "manifest.json", manifest)
```

- [ ] **Step 5: Re-run the orchestration tests and verify they pass**

Run:

```powershell
& ".venv311\Scripts\python.exe" -m unittest tests.test_run_long_gameplay_pipeline -v
```

Expected:
- PASS for the new per-chunk orchestration tests

- [ ] **Step 6: Optional commit checkpoint (only if the user explicitly asks for commits)**

```bash
git add tools/run_long_gameplay_pipeline.py tests/test_run_long_gameplay_pipeline.py
git commit -m "feat: add resumable chunk execution"
```

### Task 5: Final assembly, report emission, and single-command UX

**Files:**
- Modify: `tools/pipeline_commands.py`
- Modify: `tools/run_long_gameplay_pipeline.py`
- Modify: `tests/test_pipeline_commands.py`
- Modify: `tests/test_run_long_gameplay_pipeline.py`

- [ ] **Step 1: Add failing tests for concat + audio remux + report generation**

```python
from tools.pipeline_commands import build_concat_visual_cmd
from tools.run_long_gameplay_pipeline import build_report_payload


def test_build_concat_visual_cmd_uses_concat_demuxer():
    cmd = build_concat_visual_cmd(Path("chunks.txt"), Path("visual_full.mp4"))
    assert "-f concat" in cmd
    assert "-safe 0" in cmd


def test_build_report_payload_includes_processing_summary():
    payload = build_report_payload(
        input_video=Path("gameplays_crudos/demo.mp4"),
        source_master="john/john_video_45deg_ver4.mp4",
        chunk_count=3,
        failed_chunks=[2],
        config_path=Path("pipeline_profiles/liveportrait_gameplay_v1.json"),
    )
    assert payload["input_video"].endswith("demo.mp4")
    assert payload["chunk_count"] == 3
    assert payload["failed_chunks"] == [2]
```

- [ ] **Step 2: Run the tests and verify they fail**

Run:

```powershell
& ".venv311\Scripts\python.exe" -m unittest discover -s tests -p "test_pipeline_*.py"
```

Expected:
- FAIL because concat/report helpers are missing

- [ ] **Step 3: Implement concat + remux builders and report payload**

```python
def build_concat_visual_cmd(list_file: Path, output_video: Path) -> str:
    return f'ffmpeg -y -f concat -safe 0 -i {q(list_file)} -c:v libx264 -crf 18 -an {q(output_video)}'


def build_report_payload(input_video: Path, source_master: str, chunk_count: int, failed_chunks: list[int], config_path: Path) -> dict:
    return {
        "input_video": input_video.as_posix(),
        "source_master": source_master,
        "chunk_count": chunk_count,
        "failed_chunks": failed_chunks,
        "config_path": config_path.as_posix(),
    }
```

- [ ] **Step 4: Implement final assembly in the orchestrator**

```python
def assemble_final_output(job_dir: Path, input_video: Path, output_dir: Path, manifest, runner) -> Path:
    output_dir.mkdir(parents=True, exist_ok=True)
    list_file = job_dir / "manifests" / "concat_chunks.txt"
    composed_chunks = [job_dir / "chunks" / "composited" / f"chunk_{chunk.index:03d}_composited.mp4" for chunk in manifest.chunks]
    list_file.write_text("".join(f"file '{path.as_posix()}'\n" for path in composed_chunks), encoding="utf-8")

    visual_full = output_dir / "visual_full.mp4"
    final_video = output_dir / "final.mp4"
    runner(build_concat_visual_cmd(list_file, visual_full))
    runner(build_mux_audio_cmd(input_video, visual_full, final_video))
    return final_video
```

- [ ] **Step 5: Add the single-command main flow**

```python
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
        )
```

- [ ] **Step 6: Re-run the pipeline tests and verify they pass**

Run:

```powershell
& ".venv311\Scripts\python.exe" -m unittest discover -s tests -p "test_pipeline_*.py"
```

Expected:
- PASS for runtime + command + orchestrator tests

- [ ] **Step 7: Perform one manual smoke run on a known short gameplay**

Run:

```powershell
& ".venv311\Scripts\python.exe" tools/run_long_gameplay_pipeline.py --video gameplay_full_10s.mp4
```

Expected:
- The job creates `.pipeline_work/<video_id>/...`
- Produces `output/<video_id>/final.mp4`
- Preserves original audio stream in the final output

- [ ] **Step 8: Optional commit checkpoint (only if the user explicitly asks for commits)**

```bash
git add tools/pipeline_commands.py tools/run_long_gameplay_pipeline.py tests/test_pipeline_commands.py tests/test_run_long_gameplay_pipeline.py
git commit -m "feat: add resumable long gameplay pipeline"
```

## Self-review

- **Spec coverage:**
  - Input discovery / fixed source / fixed ROI / 60 s chunks → Tasks 1, 2, 3, 4
  - Source looping to chunk duration → Task 2, Task 4
  - Resume behavior / manifests / no restart from zero → Task 1, Task 4
  - Composition with current winning stack → Task 2 command builder + Task 4 execution
  - Final concat + original audio remux + output/report → Task 5
- **Placeholder scan:** No `TODO`, `TBD`, or “similar to previous task” shortcuts remain.
- **Type consistency:** `PipelineProfile`, `ChunkRecord`, `JobManifest`, `process_chunk`, `run_job`, `assemble_final_output`, and command-builder names are consistent across tasks.
