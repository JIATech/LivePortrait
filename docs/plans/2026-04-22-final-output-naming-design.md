# Final Output Naming — Design Document

**Date:** 2026-04-22
**Status:** Approved

## Objective

Rename the final rendered output from the generic `final.mp4` to a user-friendly name based on the original input filename.

## Approved Approach

Keep the current output directory structure and change only the final output filename.

Current:

```text
output/<job_id>/final.mp4
```

Approved:

```text
output/<job_id>/<original_input_stem>_final.mp4
```

Example:

```text
output/RE9-part3-c72b77f9/RE9-part3_final.mp4
```

## Why This Approach

- Minimal change
- Preserves the existing `job_id`-based output directory layout
- Improves discoverability for the final file
- Avoids changing chunk paths, manifests, or job identity logic

## Required Code Changes

- `tools/gpu_pipeline/run_long_gameplay_pipeline_gpu.py`
  - build the final output path from `input_video.stem`
- `tools/gpu_pipeline/supervisor_runtime.py`
  - detect job completion using `<input_stem>_final.mp4`
- `tools/gpu_pipeline/gpu_pipeline_supervisor.py`
  - display the renamed final output path in the completed UI state and event log

## Non-Goals

- Changing the output directory name
- Keeping both `final.mp4` and `<name>_final.mp4`
- Renaming `visual_full.mp4`
