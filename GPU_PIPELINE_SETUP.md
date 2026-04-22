# GPU Pipeline Setup Guide

This guide walks through setting up the LivePortrait GPU-optimized pipeline on a Windows machine with an NVIDIA GPU (tested with GTX 1660 SUPER, 6GB VRAM).

## Prerequisites

- **Windows 10/11**
- **NVIDIA GPU** with 6GB+ VRAM (GTX 1660 SUPER or better)
- **NVIDIA drivers** up to date
- **Python 3.10 or 3.11** installed
- **Git** installed
- **FFmpeg** available in PATH (download from [ffmpeg.org](https://ffmpeg.org/download.html) or use `winget install ffmpeg`)

## Step 1: Clone the repository

```powershell
git clone https://github.com/JIATech/LivePortrait.git
cd LivePortrait
```

## Step 2: Create virtual environment and install dependencies

```powershell
# Create venv
python -m venv .venv
.venv\Scripts\activate

# Install PyTorch with CUDA 12.4 support
pip install torch torchvision torchaudio --index-url https://download.pytorch.org/whl/cu124

# Install remaining dependencies
pip install -r requirements.txt
```

## Step 3: Download model weights

```powershell
huggingface-cli download KlingTeam/LivePortrait --local-dir pretrained_weights --exclude "*.git*" "README.md" "docs"
```

This downloads ~500MB-1GB of model checkpoints into `pretrained_weights/`.

## Step 4: Place input files

You should receive two files from the project owner:

| File | Where to place it |
|------|-------------------|
| Gameplay video (raw, several GB) | `gameplays_crudos\<name>.mp4` |
| Source master video (`john_video_45deg_ver4.mp4`) | Already in repo under `john/` |

Create the input directory if it doesn't exist:

```powershell
mkdir gameplays_crudos
# Copy the raw gameplay video into this folder
```

## Step 5: Verify setup

```powershell
# Check FFmpeg is available
ffmpeg -version

# Check PyTorch sees the GPU
python -c "import torch; print(f'CUDA available: {torch.cuda.is_available()}'); print(f'Device: {torch.cuda.get_device_name(0) if torch.cuda.is_available() else 'N/A'}')"

# Check pipeline help
python tools/gpu_pipeline/run_long_gameplay_pipeline_gpu.py -h
```

Expected output for CUDA check:
```
CUDA available: True
Device: NVIDIA GeForce GTX 1660 SUPER
```

## Step 6: Run the pipeline

### Process a single video

```powershell
python tools/gpu_pipeline/run_long_gameplay_pipeline_gpu.py --video <filename>.mp4
```

### Process all videos in gameplays_crudos/

```powershell
python tools/gpu_pipeline/run_long_gameplay_pipeline_gpu.py
```

### Monitor progress (in a second terminal)

```powershell
# Find the job directory name first
ls .pipeline_work

# Then monitor
python tools/watch_pipeline_progress.py --job .pipeline_work\<job_id>
```

## What to expect

- **Chunk size**: 120 seconds per chunk (configurable in the profile)
- **Output**: Results appear in `output\<video_id>\final.mp4`
- **Resume support**: If interrupted, re-run the same command — it picks up where it left off
- **Manifest**: Progress is tracked in `.pipeline_work\<job_id>\manifests\manifest.json`

## GPU Profile settings

The profile at `tools/gpu_pipeline/gpu_profile_gameplay_v1.json` controls:

| Setting | Value | Purpose |
|---------|-------|---------|
| `chunk_seconds` | 120 | Duration of each processing chunk |
| `roi` | `{x:6, y:811, w:259, h:268}` | Rectangle coordinates of the face cam in the gameplay |
| `source_master` | `john/john_video_45deg_ver4.mp4` | Avatar reference video |
| `source_fps` | 12 | Frames per second for the avatar |
| `flag_force_cpu` | `false` | Run on GPU (CUDA) |
| `flag_use_half_precision` | `true` | Use FP16 for faster inference |
| `flag_eye_retargeting` | `true` | Enable eye movement tracking |

## Troubleshooting

### "CUDA out of memory"

Reduce chunk size in the profile to 60 seconds:
```json
"chunk_seconds": 60
```

### "No module named 'torch'"

Make sure the venv is activated: `.venv\Scripts\activate`

### "ffmpeg is not recognized"

Install FFmpeg: `winget install ffmpeg` then restart your terminal.

### Black boxes in output

Set `flag_use_half_precision` to `false` in the profile.

### Slow processing

Verify CUDA is actually being used:
```powershell
python -c "import torch; print(torch.cuda.is_available())"
```
If `False`, reinstall PyTorch with the CUDA index URL from Step 2.
