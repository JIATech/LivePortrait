# LivePortrait agent notes

- This is a single-package Python repo. There is no repo-local test, lint, typecheck, formatter, task-runner, pre-commit, or CI config in this checkout. Do NOT invent `make`, `pytest`, `ruff`, or npm workflows.
- Run commands from the repo root. `app.py` and `app_animals.py` read `assets/...` with relative paths and only auto-add a bundled `./ffmpeg` directory from the current working directory.
- FFmpeg is mandatory for all runtime entrypoints; `inference*.py` and `app*.py` fail fast on `ffmpeg -version`.

## Setup

- Target Python is 3.10 (`readme.md`).
- Linux/Windows install path: install the appropriate PyTorch build for your CUDA version if needed, then `pip install -r requirements.txt`.
- macOS Apple Silicon install path: `pip install -r requirements_macOS.txt`; animals mode is not supported there.
- Download weights into `pretrained_weights/` with:
  `huggingface-cli download KlingTeam/LivePortrait --local-dir pretrained_weights --exclude "*.git*" "README.md" "docs"`
- The executable source of truth for checkpoint locations is `src/config/inference_config.py` and `src/config/crop_config.py`; expected directory layout is summarized in `assets/docs/directory-structure.md`.

## Entry points

- `python inference.py` — human CLI inference.
- `python app.py` — human Gradio UI.
- `python inference_animals.py` — animal CLI inference.
- `python app_animals.py` — animal Gradio UI.
- `python speed.py` — benchmark only; it calls `torch.compile` and `torch.cuda.synchronize()`, so treat it as CUDA-only.
- macOS Apple Silicon runtime commands in the README use `PYTORCH_ENABLE_MPS_FALLBACK=1` for `inference.py` and `app.py`.

## Animals mode

- Build the vendored XPose op before running animals mode:
  in `src/utils/dependencies/XPose/models/UniPose/ops`, run `python setup.py build install`.
- Animals support is wired through vendored XPose code under `src/utils/dependencies/XPose`; humans rely on vendored InsightFace helpers plus ONNX checkpoints.

## Architecture

- Top-level scripts mainly parse `src/config/argument_config.py` with Tyro, then hand off to pipelines.
- Human runtime flow: `inference.py` -> `src/live_portrait_pipeline.py` -> `src/live_portrait_wrapper.py`.
- Animal runtime flow: `inference_animals.py` -> `src/live_portrait_pipeline_animal.py`.
- UI flow: `app.py` / `app_animals.py` -> `src/gradio_pipeline.py`, which wraps the same pipelines.
- `src/live_portrait_wrapper.py` is where model loading, device selection (`cuda`, `mps`, or CPU), half precision, and optional `torch.compile` are applied.
- `src/config/argument_config.py` is the single source of truth for user-facing CLI/UI flags and defaults.

## Verification

- There is no automated verification suite in the repo; use narrow smoke checks.
- Cheapest CLI sanity check that avoids model weights: `python inference.py -h` and `python inference_animals.py -h`.
- Real runtime verification requires FFmpeg plus pretrained weights; use the bundled examples under `assets/examples/source` and `assets/examples/driving`.
- If you touch animals mode, verify the XPose extension still imports/builds before claiming success.

## Repo-specific gotchas

- Default output directory is `animations/` (gitignored).
- When `--driving/-d` is a video or image instead of a `.pkl`, the pipeline writes a motion template next to the driving input as `<driving_basename>.pkl`; later runs can reuse that template.
- Non-square driving videos are auto-cropped in the pipeline even if the crop flag is left off.
- `flag_use_half_precision=True` by default; `src/config/argument_config.py` explicitly says to disable it if outputs show black boxes.
- `--flag_do_torch_compile` is exposed in config/UI, but the README says it is not supported on Windows or macOS.
