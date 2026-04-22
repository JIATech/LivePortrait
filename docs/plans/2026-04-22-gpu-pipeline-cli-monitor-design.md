# GPU Pipeline CLI Monitor — Design Document

**Date:** 2026-04-22
**Status:** Approved

## Objective

Build a rich CLI monitor that shows per-chunk, per-stage animated progress bars for the LivePortrait GPU gameplay pipeline, so the user can observe in real time how each chunk advances through its 5 processing stages.

## Background

The existing `watch_pipeline_progress.py` only shows coarse chunk-level status (done/pending/failed) as plain text. The pipeline itself (`run_long_gameplay_pipeline_gpu.py`) runs each chunk through 5 stages sequentially but emits no structured progress data. Users cannot tell whether a chunk is stuck in LivePortrait inference or still extracting frames.

## Architecture

```
Pipeline  →  state.json  →  Monitor (rich UI)
   ↑___________________________↓
          (user observes)
```

### Components

| Component | File | Purpose |
|-----------|------|---------|
| `ProgressTracker` | `tools/gpu_pipeline/progress_tracker.py` | Writes live state JSON from the pipeline process |
| `PopenRunner` | `tools/gpu_pipeline/popen_runner.py` | Executes FFmpeg with `Popen`, parses stderr frames, feeds tracker |
| `PipelineMonitor` | `tools/gpu_pipeline/gpu_pipeline_monitor.py` | Reads `state.json` every 500ms and renders animated UI with `rich` |

### Modified Files

- `tools/gpu_pipeline/run_long_gameplay_pipeline_gpu.py` — Accept an optional `ProgressTracker` and pass it through `process_chunk()`

## State File Format (`state.json`)

Located at `.pipeline_work/<job_id>/state.json`. Overwritten atomically on every stage change.

```json
{
  "video_id": "RE9-part3-c72b77f9",
  "input_video": "gameplays_crudos/RE9-part3.mp4",
  "total_seconds": 2543.37,
  "total_chunks": 43,
  "started_at": "2026-04-22T16:00:00Z",
  "current_chunk_index": 5,
  "chunks": [
    {
      "index": 0,
      "status": "done",
      "stages": [
        {"name": "extract",    "status": "done", "progress": 1.0, "frames_done": 3600, "frames_total": 3600},
        {"name": "roi_crop",   "status": "done", "progress": 1.0, "frames_done": 720,  "frames_total": 720},
        {"name": "source_loop","status": "done", "progress": 1.0, "frames_done": 720,  "frames_total": 720},
        {"name": "liveportrait","status": "done", "progress": 1.0, "elapsed_seconds": 142.3, "estimated_total_seconds": 142.3},
        {"name": "composite",  "status": "done", "progress": 1.0, "elapsed_seconds": 8.5,  "estimated_total_seconds": 8.5}
      ]
    },
    {
      "index": 5,
      "status": "processing",
      "stages": [
        {"name": "extract",    "status": "done",    "progress": 1.0},
        {"name": "roi_crop",   "status": "done",    "progress": 1.0},
        {"name": "source_loop","status": "done",    "progress": 1.0},
        {"name": "liveportrait","status": "running","progress": 0.34, "elapsed_seconds": 48.2, "estimated_total_seconds": 142.0},
        {"name": "composite",  "status": "pending", "progress": 0.0}
      ]
    }
  ],
  "global_eta_seconds": 3847.5,
  "last_updated": "2026-04-22T16:12:34Z"
}
```

### Stage Progress Logic

| Stage | Progress Source |
|-------|-----------------|
| `extract` | Parse FFmpeg stderr `frame= X` vs `duration × input_fps` |
| `roi_crop` | Parse FFmpeg stderr `frame= X` vs `duration × roi_fps` (12) |
| `source_loop` | Parse FFmpeg stderr `frame= X` vs `duration × source_fps` (12) |
| `liveportrait` | Time-based. Estimate = avg seconds/frame from first 3 completed chunks; fallback = 0.1s/frame before calibration |
| `composite` | Time-based. Estimate = avg of previous chunks' composite time; fallback = 5s |

## UI Design (rich)

```
┌─ GPU Pipeline: RE9-part3.mp4 ───────────────────────────────────────────────┐
│  Total: 42m 23s | Chunks: 43 | Progress: 11.6% | ETA: 1h 04m 12s           │
├─ Chunk Grid ────────────────────────────────────────────────────────────────┤
│ [████████████████████░░░░░░░░░░░░░░░░░░░░] Chunk 00  DONE                  │
│ [████████████████████░░░░░░░░░░░░░░░░░░░░] Chunk 01  DONE                  │
│ [████████████████████░░░░░░░░░░░░░░░░░░░░] Chunk 02  DONE                  │
│ [████████████████████████████████████████] Chunk 03  DONE                  │
│ [████████████████████████████████░░░░░░░░] Chunk 04  DONE                  │
│ [░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░] Chunk 05  PROCESSING            │
│ [░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░] Chunk 06  PENDING               │
│ ...                                                                        │
├─ Active Chunk 05 Detail ────────────────────────────────────────────────────┤
│  extract     [████████████████████████████████████████] 100% (3600/3600 fr)│
│  roi_crop    [████████████████████████████████████████] 100% (720/720 fr)  │
│  source_loop [████████████████████████████████████████] 100% (720/720 fr)  │
│  liveportrait[██████████████░░░░░░░░░░░░░░░░░░░░░░░░░]  34% (48s/142s est)│
│  composite   [░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░]   0% (pending)      │
├─ Summary ───────────────────────────────────────────────────────────────────┤
│  Done: 5 | Processing: 1 | Pending: 37 | Failed: 0                         │
│  Avg time per chunk: 3m 12s | LivePortrait: ~2m 22s/chunk                  │
└────────────────────────────────────────────────────────────────────────────┘
```

**Behavior:**
- Updates every 500ms
- If pipeline not running → "Waiting for pipeline…"
- If error → chunk bar turns red, shows truncated error
- `Ctrl+C` exits cleanly

## Pipeline Modifications

### `process_chunk()` injection

```python
def process_chunk(
    runner, input_video, source_prepared, chunk_index, start_seconds,
    duration_seconds, paths, profile, tracker: ProgressTracker | None = None
):
    stages = [
        ("extract",     build_full_chunk_cmd,     duration_seconds * INPUT_FPS),
        ("roi_crop",    build_roi_chunk_cmd,      duration_seconds * ROI_FPS),
        ("source_loop", build_loop_source_cmd,      duration_seconds * SOURCE_FPS),
        ("liveportrait", build_liveportrait_cmd,    None),  # time-based
        ("composite",   build_composite_cmd,        None),  # time-based
    ]
    for stage_name, cmd_builder, frames_total in stages:
        if tracker:
            tracker.begin_stage(chunk_index, stage_name, frames_total=frames_total)
        
        # FFmpeg stages use PopenRunner for real-time frame parsing
        # LivePortrait uses subprocess.run + elapsed-time tracking
        runner(...)
        
        if tracker:
            tracker.end_stage(chunk_index, stage_name)
```

### `run_job()` integration

```python
from tools.gpu_pipeline.progress_tracker import ProgressTracker

def run_job(...):
    tracker = ProgressTracker(job_paths.job_dir, manifest)
    tracker.start()
    try:
        for chunk in manifest.chunks:
            if chunk.status == "done":
                tracker.mark_chunk_done(chunk.index)
                continue
            process_chunk(..., tracker=tracker)
            chunk.status = "done"
    finally:
        tracker.close()
```

## Dependencies

- `rich>=13.0.0` — Rich text and progress rendering in terminal

## Error Handling

- `state.json` is written atomically (write to temp, rename) to prevent the monitor from reading a partial file
- If `state.json` is missing or corrupt, the monitor shows "Waiting for pipeline…"
- If a stage fails, tracker writes `status: "failed"` + `error` message; monitor highlights the chunk in red

## Future Extensibility

- Add `--json` flag to the monitor for machine-readable output
- WebSocket bridge to expose state to a browser dashboard
- Per-GPU VRAM usage overlay (via `nvidia-smi` polling)
