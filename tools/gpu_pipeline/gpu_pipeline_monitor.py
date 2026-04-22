"""Rich CLI monitor for the GPU gameplay pipeline."""

import argparse
import json
import time
from pathlib import Path

from rich.console import Console
from rich.layout import Layout
from rich.live import Live
from rich.panel import Panel
from rich.table import Table


def _load_state(path: Path) -> dict | None:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return None


def _format_duration(seconds: float | None) -> str:
    if seconds is None:
        return "unknown"
    m, s = divmod(int(seconds), 60)
    h, m = divmod(m, 60)
    if h:
        return f"{h}h {m:02d}m {s:02d}s"
    return f"{m}m {s:02d}s"


def _format_eta(state: dict) -> str:
    eta = state.get("global_eta_seconds")
    if eta is None:
        return "calculating..."
    return _format_duration(eta)


def _build_chunk_grid(state: dict) -> Table:
    table = Table(show_header=False, box=None, padding=(0, 1))
    chunks = state.get("chunks", [])
    for chunk in chunks:
        idx = chunk["index"]
        status = chunk["status"]
        progress = _chunk_progress(chunk)
        bar = "█" * int(progress * 40) + "░" * (40 - int(progress * 40))
        label = status.upper()
        if status == "failed":
            label = f"[red]{label}[/red]"
        elif status == "done":
            label = f"[green]{label}[/green]"
        elif status == "processing":
            label = f"[yellow]{label}[/yellow]"
        table.add_row(f"{bar} Chunk {idx:02d}  {label}")
    return table


def _chunk_progress(chunk: dict) -> float:
    stages = chunk.get("stages", [])
    if not stages:
        return 0.0
    return sum(s.get("progress", 0.0) for s in stages) / len(stages)


def _build_active_detail(state: dict) -> Table:
    table = Table(show_header=False, box=None, padding=(0, 1))
    current = state.get("current_chunk_index")
    if current is None:
        table.add_row("No active chunk")
        return table

    chunk = state["chunks"][current]
    for stage in chunk.get("stages", []):
        name = stage["name"]
        prog = stage.get("progress", 0.0)
        pct = int(prog * 100)
        bar = "█" * int(prog * 40) + "░" * (40 - int(prog * 40))
        detail = ""
        if stage.get("frames_total"):
            detail = f" ({stage.get('frames_done', 0)}/{stage['frames_total']} fr)"
        elif stage.get("estimated_total_seconds"):
            detail = f" ({stage.get('elapsed_seconds', 0):.0f}s/{stage['estimated_total_seconds']:.0f}s est)"
        elif stage["status"] == "pending":
            detail = " (pending)"
        table.add_row(f"  {name:12s} [{bar}] {pct:3d}%{detail}")
    return table


def _build_summary(state: dict) -> str:
    chunks = state.get("chunks", [])
    done = sum(1 for c in chunks if c["status"] == "done")
    pending = sum(1 for c in chunks if c["status"] == "pending")
    failed = sum(1 for c in chunks if c["status"] == "failed")
    processing = sum(1 for c in chunks if c["status"] == "processing")
    total = len(chunks)
    pct = round((done / total) * 100, 1) if total else 0.0
    return (
        f"Done: {done} | Processing: {processing} | Pending: {pending} | Failed: {failed}  ({pct}% total)"
    )


def build_layout(state: dict | None) -> Layout:
    layout = Layout()
    if state is None:
        layout.update(Panel("[yellow]Waiting for pipeline…[/yellow]", title="GPU Pipeline Monitor"))
        return layout

    header_text = (
        f"[bold]{Path(state['input_video']).name}[/bold]  |  "
        f"Chunks: {state['total_chunks']}  |  "
        f"ETA: {_format_eta(state)}"
    )
    header = Panel(header_text, title="GPU Pipeline Monitor")
    grid = Panel(_build_chunk_grid(state), title="Chunk Grid", border_style="blue")
    detail = Panel(_build_active_detail(state), title=f"Active Chunk {state.get('current_chunk_index', '?')}", border_style="yellow")
    summary = Panel(_build_summary(state), title="Summary", border_style="green")

    layout.split_column(
        Layout(header, size=3),
        Layout(grid, size=state["total_chunks"] + 2),
        Layout(detail, size=8),
        Layout(summary, size=3),
    )
    return layout


def monitor_loop(job_dir: Path, interval: float = 0.5) -> None:
    state_path = job_dir / "state.json"
    console = Console()
    with Live(console=console, refresh_per_second=2, screen=True) as live:
        while True:
            state = _load_state(state_path)
            live.update(build_layout(state))
            time.sleep(interval)


def resolve_job_dir(work_dir: Path, video_name: str) -> Path:
    for manifest_path in work_dir.glob("*/manifests/manifest.json"):
        data = json.loads(manifest_path.read_text(encoding="utf-8"))
        input_video = data.get("input_video", "")
        if Path(input_video).name == video_name:
            return manifest_path.parent.parent
    raise FileNotFoundError(f"No pipeline job found for video: {video_name}")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Rich GPU pipeline monitor")
    parser.add_argument("--work-dir", default=".pipeline_work")
    parser.add_argument("--video", required=True)
    parser.add_argument("--interval", type=float, default=0.5)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    job_dir = resolve_job_dir(Path(args.work_dir), args.video)
    try:
        monitor_loop(job_dir, interval=args.interval)
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
