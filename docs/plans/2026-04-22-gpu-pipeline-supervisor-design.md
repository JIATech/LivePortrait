# GPU Pipeline Supervisor TUI — Design Document

**Date:** 2026-04-22
**Status:** Approved

## Objective

Build a resilient terminal UI that acts as the orchestrator for long LivePortrait GPU jobs on Windows. The UI must launch the pipeline worker in a separate visible terminal, show chunk-level progress and ETA, detect worker crashes, and automatically relaunch the worker with infinite backoff so the user does not need to babysit the run.

## Background

The previous monitor design put real-time progress tracking in the pipeline hot path through `state.json` writes. On Windows this created file lock contention with the monitor reader, causing the tracker to raise `PermissionError` and crash the pipeline. That architecture is too fragile for multi-hour jobs.

The new design moves orchestration responsibility into a separate supervisor process and keeps the pipeline worker resumable and disposable. Monitoring must never be able to kill the actual work.

## Architecture

```
Supervisor TUI  ──────── launches/relaunches ────────>  Worker Terminal
      │                                                    │
      │                                                    │
      ├── reads manifest/report/logs <─────────────────────┘
      ├── tracks PID / exit code
      ├── computes ETA from completed chunks
      └── event log (restarts, failures, stalls, completion)
```

### Responsibilities

| Component | File | Purpose |
|-----------|------|---------|
| `SupervisorRuntime` | `tools/gpu_pipeline/supervisor_runtime.py` | Launch worker terminal, track process lifecycle, compute restart backoff, parse manifest/report state |
| `SupervisorTUI` | `tools/gpu_pipeline/gpu_pipeline_supervisor.py` | Main user-facing terminal UI, event log, chunk summary, ETA, worker status |
| `Pipeline Worker` | `tools/gpu_pipeline/run_long_gameplay_pipeline_gpu.py` | Existing chunk processor, resumed/restarted by supervisor |

### Worker Launch Model

- The user launches only the supervisor.
- The supervisor starts a new visible PowerShell window for the worker.
- The worker runs the existing pipeline command:

```powershell
.venv\Scripts\python.exe tools/gpu_pipeline/run_long_gameplay_pipeline_gpu.py --video RE9-part3.mp4
```

- Worker stdout/stderr are also redirected to log files in the job directory for diagnostics.

## Source of Truth

The supervisor should not depend on `state.json` for correctness.

Primary sources:
- `.pipeline_work/<job_id>/manifests/manifest.json`
- `output/<job_id>/report.json`
- worker process liveness / exit code
- supervisor-owned event log

Secondary/optional sources:
- worker stdout/stderr logs
- `state.json` only as best-effort auxiliary info if present

## UI Design

The UI stays clean and operationally focused.

### Header

- input video name
- worker status: `starting | running | backoff | crashed | stalled | completed`
- restart count
- current chunk
- total chunks
- ETA

Example:

```
RE9-part3.mp4 | worker: running | restarts: 2 | chunk: 5/43 | ETA: 2h 14m
```

### Chunk Summary

- total chunks
- done / pending / failed / running
- progress bar based on completed chunks
- average time per completed chunk

Example:

```
[███████░░░░░░░░░░░░░░░░░░░░] 5 / 43 chunks done
avg chunk time: 8m 41s
```

### Chunk List

Each chunk has a compact row:

- `done`
- `running`
- `pending`
- `failed`

The list is chunk-oriented, not stage-oriented. This reduces coupling and keeps the UI stable even when the worker is relaunched.

### Event Log

The bottom panel shows only recent operationally relevant events.

Examples:

- `20:58 worker started (pid 18580)`
- `21:14 chunk 4 failed`
- `21:14 restart scheduled in 30s`
- `21:15 worker restarted (attempt 2)`
- `22:07 chunk 5 completed`
- `22:09 worker appears stalled (no manifest change for 20m)`

Only the last 10-20 events are shown.

## Failure Handling

### 1. Worker crash

If the worker exits unexpectedly:

- detect exit code / process death
- inspect `manifest.json` to determine the failed/incomplete chunk
- log the event
- schedule restart using backoff
- relaunch worker in a new terminal

### 2. Infinite backoff policy

The approved restart policy is infinite retries with backoff:

```
10s -> 30s -> 60s -> 120s -> 300s -> 300s -> ...
```

This policy maximizes autonomy for intermittent failures like file locks, CUDA hiccups, or user-closed worker terminals.

### 3. Stalled worker detection

A worker can be alive but not making progress.

The supervisor tracks manifest/report timestamps and completed chunk count. If the worker is alive but no progress has changed for a configurable threshold, for example 20 minutes:

- worker status becomes `stalled`
- event is logged
- optional behavior: terminate and restart worker automatically after the threshold

Recommended initial behavior:
- detect and restart stalled workers automatically using the same backoff policy

### 4. Normal completion

When all chunks are `done` and final output exists:

- worker status becomes `completed`
- UI shows final output path
- no further restart attempts are made

## ETA Calculation

ETA must be based on completed chunks, not per-stage timings.

### Formula

- `completed_chunks = count(status == done)`
- `pending_chunks = total_chunks - completed_chunks`
- `elapsed_wall_clock = now - first_worker_start_time`
- `avg_chunk_seconds = elapsed_wall_clock / completed_chunks`
- `eta_seconds = avg_chunk_seconds * pending_chunks`

Rules:

- if `completed_chunks == 0`, show `ETA: calculating...`
- never allow negative ETA
- restart downtime counts toward wall-clock ETA because it affects real finish time for the user

## Data Model

The supervisor should maintain its own runtime state in memory and optionally persist to a small supervisor metadata file under the job directory.

Suggested fields:

```json
{
  "video": "RE9-part3.mp4",
  "job_id": "RE9-part3-c72b77f9",
  "worker_pid": 18580,
  "worker_status": "running",
  "restart_count": 2,
  "backoff_seconds": 30,
  "last_progress_at": "2026-04-22T21:14:00Z",
  "last_chunk_done": 4,
  "events": [
    {"at": "2026-04-22T20:58:00Z", "message": "worker started (pid 18580)"},
    {"at": "2026-04-22T21:14:00Z", "message": "chunk 4 failed"}
  ]
}
```

This metadata belongs to the supervisor, not to the worker.

## Minimal Pipeline Changes

The worker should remain as unchanged as possible.

Required changes:
- ensure `.venv\Scripts\python.exe` is used explicitly where needed
- keep resume behavior via manifest intact

Not required for correctness:
- per-stage tracker
- hot-path `state.json` writes

The supervisor must be able to function even if all fine-grained progress tracking is disabled.

## Verification Strategy

The real verification target is resilience, not just rendering.

### Scenario 1: Happy path

1. launch supervisor for `test_smoke.mp4`
2. confirm worker terminal opens
3. confirm chunks progress to completion
4. confirm final output exists

### Scenario 2: Manual worker kill

1. launch supervisor
2. kill worker process manually during a chunk
3. confirm TUI detects crash
4. confirm event log records it
5. confirm supervisor waits for backoff
6. confirm worker relaunches
7. confirm pipeline resumes from failed chunk

### Scenario 3: Worker terminal closed by user

1. close the visible worker terminal
2. confirm the supervisor treats it as a crash and relaunches

### Scenario 4: Long-running chunk ETA

1. complete at least 2 chunks
2. confirm ETA is based on average completed chunk time
3. confirm ETA never goes negative or nonsense

## Future Extensions

- keyboard controls in TUI: pause/restart/quit
- optional stage-level detail panel from auxiliary state if available
- GPU health polling (`nvidia-smi`) for VRAM and utilization
- persisted supervisor session recovery after the supervisor itself crashes
