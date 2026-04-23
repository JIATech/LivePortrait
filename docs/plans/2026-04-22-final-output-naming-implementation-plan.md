# Final Output Naming Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Rename the final rendered output to `<original_input_stem>_final.mp4` while keeping the existing output directory structure unchanged.

**Architecture:** The pipeline will keep writing into the same `output/<job_id>/` directory, but the final muxed file will use a name derived from `input_video.stem`. Completion checks and the supervisor UI will be updated to look for and display that renamed file.

**Tech Stack:** Python 3.13, existing pipeline/supervisor code, `unittest`

---

### Task 1: Add failing tests for final output naming

**Files:**
- Modify: `tests/test_run_long_gameplay_pipeline.py`
- Modify: `tests/test_gpu_pipeline_supervisor.py`

**Step 1: Write the failing tests**

Add a test in `tests/test_run_long_gameplay_pipeline.py` asserting that `build_job_paths(...)` computes:

```python
self.assertEqual(paths.final_video, output_dir / "RE9-part3_final.mp4")
```

Use an input video path like `Path("gameplays_crudos/RE9-part3.mp4")` and derive the expected name from its stem.

Add a test in `tests/test_gpu_pipeline_supervisor.py` asserting `is_job_complete(...)` looks for `RE9-part3_final.mp4` rather than `final.mp4`.

**Step 2: Run the tests to verify they fail**

Run:
```bash
.venv\Scripts\python -m unittest tests.test_run_long_gameplay_pipeline tests.test_gpu_pipeline_supervisor -v
```

Expected: FAIL because the code still expects `final.mp4`

**Step 3: Commit**

```bash
git add tests/test_run_long_gameplay_pipeline.py tests/test_gpu_pipeline_supervisor.py
git commit -m "test: define named final output behavior"
```

---

### Task 2: Rename the pipeline final output file

**Files:**
- Modify: `tools/gpu_pipeline/run_long_gameplay_pipeline_gpu.py`
- Test: `tests/test_run_long_gameplay_pipeline.py`

**Step 1: Implement the minimal code**

Update `build_job_paths(...)` so the final output is based on the input filename stem instead of the hardcoded `final.mp4`.

Example target:

```python
final_video = output_dir / f"{input_video.stem}_final.mp4"
```

This likely means passing `input_video` into `build_job_paths(...)` or computing the named file where `JobPaths` is created.

**Step 2: Run the focused tests**

Run:
```bash
.venv\Scripts\python -m unittest tests.test_run_long_gameplay_pipeline -v
```

Expected: PASS

**Step 3: Commit**

```bash
git add tools/gpu_pipeline/run_long_gameplay_pipeline_gpu.py tests/test_run_long_gameplay_pipeline.py
git commit -m "feat: name gpu pipeline final output after source video"
```

---

### Task 3: Update supervisor completion detection

**Files:**
- Modify: `tools/gpu_pipeline/supervisor_runtime.py`
- Test: `tests/test_gpu_pipeline_supervisor.py`

**Step 1: Implement the minimal code**

Change `is_job_complete(...)` to look for:

```python
output_root / manifest["video_id"] / f"{Path(manifest['input_video']).stem}_final.mp4"
```

Do not change the directory layout.

**Step 2: Run the focused tests**

Run:
```bash
.venv\Scripts\python -m unittest tests.test_gpu_pipeline_supervisor -v
```

Expected: PASS

**Step 3: Commit**

```bash
git add tools/gpu_pipeline/supervisor_runtime.py tests/test_gpu_pipeline_supervisor.py
git commit -m "fix: detect named final output in supervisor"
```

---

### Task 4: Show the renamed file in the supervisor UI

**Files:**
- Modify: `tools/gpu_pipeline/gpu_pipeline_supervisor.py`
- Test: `tests/test_gpu_pipeline_supervisor.py`

**Step 1: Write/adjust failing test**

Add or update a UI test so the completed-state event / summary references `RE9-part3_final.mp4` rather than `final.mp4`.

**Step 2: Implement the minimal code**

Update the completed-state path construction in `gpu_pipeline_supervisor.py` to derive the final output name from `args.video`:

```python
final_output = output_dir / job_dir.name / f"{Path(args.video).stem}_final.mp4"
```

**Step 3: Run the focused tests**

Run:
```bash
.venv\Scripts\python -m unittest tests.test_gpu_pipeline_supervisor -v
```

Expected: PASS

**Step 4: Commit**

```bash
git add tools/gpu_pipeline/gpu_pipeline_supervisor.py tests/test_gpu_pipeline_supervisor.py
git commit -m "feat: show named final output in supervisor ui"
```

---

### Task 5: Run regression verification

**Files:**
- Verify existing tests still pass

**Step 1: Run all relevant tests**

Run:
```bash
.venv\Scripts\python -m unittest \
  tests.test_run_long_gameplay_pipeline \
  tests.test_gpu_pipeline_supervisor \
  tests.test_run_long_gameplay_pipeline_gpu \
  tests.test_gpu_progress_tracker -v
```

Expected: all pass

**Step 2: Compile touched modules**

Run:
```bash
.venv\Scripts\python -m py_compile \
  tools/gpu_pipeline/run_long_gameplay_pipeline_gpu.py \
  tools/gpu_pipeline/supervisor_runtime.py \
  tools/gpu_pipeline/gpu_pipeline_supervisor.py
```

Expected: no output

**Step 3: Optional smoke check**

Run a short supervised job on `test_smoke.mp4` and verify the final file is:

```text
output/test_smoke-<hash>/test_smoke_final.mp4
```

**Step 4: Final commit**

```bash
git add tools/gpu_pipeline/ tests/ docs/plans/
git commit -m "feat: name final outputs after source videos"
```
