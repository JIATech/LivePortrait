from __future__ import annotations

import argparse
import sys
import time
from collections import deque
from pathlib import Path

from rich.layout import Layout
from rich.live import Live
from rich.panel import Panel
from rich.table import Table

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from tools.gpu_pipeline.supervisor_runtime import (
    JobSnapshot,
    WorkerBackoff,
    collect_job_snapshot,
    detect_worker_status,
    estimate_eta_seconds,
    format_duration,
    format_event,
    is_job_complete,
    launch_worker_terminal,
    resolve_final_output_path,
    resolve_job_dir,
    terminate_worker_tree,
)


def build_layout(
    video_name: str,
    worker_status: str,
    restart_count: int,
    snapshot: JobSnapshot,
    events: list[str],
    avg_chunk_seconds: float | None = None,
    final_output: Path | None = None,
) -> Layout:
    layout = Layout()

    eta_text = format_duration(snapshot.eta_seconds)
    current_chunk = "-"
    if snapshot.current_chunk is not None and snapshot.total_chunks:
        current_chunk = f"{snapshot.current_chunk + 1}/{snapshot.total_chunks}"

    header = Panel(
        f"{video_name} | worker: {worker_status} | restarts: {restart_count} | "
        f"chunk: {current_chunk} | ETA: {eta_text}",
        title="GPU Pipeline Supervisor",
    )

    completed_bar_width = 28
    completed_ratio = 0.0
    if snapshot.total_chunks:
        completed_ratio = snapshot.done_chunks / snapshot.total_chunks
    completed_blocks = int(completed_ratio * completed_bar_width)
    progress_bar = "█" * completed_blocks + "░" * (completed_bar_width - completed_blocks)
    running_chunks = 0
    if snapshot.current_chunk is not None and worker_status in {"running", "starting", "stalled", "backoff"}:
        running_chunks = 1
    avg_text = format_duration(avg_chunk_seconds) if avg_chunk_seconds is not None else "calculating..."
    final_text = f"\nfinal: {final_output}" if final_output is not None else ""
    summary = Panel(
        f"[{progress_bar}] {snapshot.done_chunks} / {snapshot.total_chunks} chunks done\n"
        f"done={snapshot.done_chunks} pending={snapshot.pending_chunks} failed={snapshot.failed_chunks} running={running_chunks}\n"
        f"avg chunk: {avg_text}{final_text}",
        title="Summary",
    )

    chunk_table = Table(show_header=False, box=None, padding=(0, 1))
    for index, status in enumerate(snapshot.chunk_statuses):
        rendered = status.upper()
        if status == "done":
            rendered = f"[green]{rendered}[/green]"
        elif status == "failed":
            rendered = f"[red]{rendered}[/red]"
        elif snapshot.current_chunk == index and worker_status in {"running", "starting", "stalled", "backoff"}:
            rendered = f"[yellow]{worker_status.upper()}[/yellow]"
        chunk_table.add_row(f"Chunk {index:02d}", rendered)

    event_table = Table(show_header=False, box=None, padding=(0, 1))
    if events:
        for event in events[-12:]:
            event_table.add_row(event)
    else:
        event_table.add_row("No events yet")

    layout.split_column(
        Layout(header, size=3),
        Layout(summary, size=6 if final_output is not None else 5),
        Layout(Panel(chunk_table, title="Chunk Grid")),
        Layout(Panel(event_table, title="Events"), size=14),
    )
    return layout


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="GPU pipeline supervisor")
    parser.add_argument("--video", required=True)
    parser.add_argument("--input-dir", default="gameplays_crudos")
    parser.add_argument("--work-dir", default=".pipeline_work")
    parser.add_argument("--output-dir", default="output")
    parser.add_argument("--stalled-after-seconds", type=int, default=1200)
    parser.add_argument("--poll-interval", type=float, default=1.0)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    workspace = Path.cwd()
    input_dir = Path(args.input_dir)
    work_dir = Path(args.work_dir)
    output_dir = Path(args.output_dir)
    job_dir = resolve_job_dir(args.video, input_dir, work_dir)

    events: deque[str] = deque(maxlen=20)
    backoff = WorkerBackoff()
    restart_count = 0
    started_monotonic = time.perf_counter()
    next_restart_at: float | None = None
    completion_logged = False

    worker = launch_worker_terminal(
        args.video,
        job_dir,
        workspace,
        input_dir=args.input_dir,
        output_dir=args.output_dir,
        work_dir=args.work_dir,
    )
    events.append(format_event(f"worker started (pid {worker.pid})"))
    previous_statuses: list[str] = []
    previous_done_chunks = 0

    with Live(refresh_per_second=4, screen=True) as live:
        while True:
            snapshot = collect_job_snapshot(job_dir)
            snapshot.eta_seconds = estimate_eta_seconds(
                snapshot.total_chunks,
                snapshot.done_chunks,
                time.perf_counter() - started_monotonic,
            )
            avg_chunk_seconds = None
            if snapshot.done_chunks > 0:
                avg_chunk_seconds = max(time.perf_counter() - started_monotonic, 0.0) / snapshot.done_chunks

            if previous_statuses and len(previous_statuses) == len(snapshot.chunk_statuses):
                for index, (before, after) in enumerate(zip(previous_statuses, snapshot.chunk_statuses)):
                    if before != "done" and after == "done":
                        events.append(format_event(f"chunk {index} completed"))
                    elif before != "failed" and after == "failed":
                        events.append(format_event(f"chunk {index} failed"))
            previous_statuses = list(snapshot.chunk_statuses)
            if snapshot.done_chunks > previous_done_chunks:
                backoff.reset()
                previous_done_chunks = snapshot.done_chunks

            worker_alive = worker.poll() is None
            worker_status = detect_worker_status(
                worker_alive=worker_alive,
                job_dir=job_dir,
                output_root=output_dir,
                stalled_after_seconds=args.stalled_after_seconds,
            )

            if is_job_complete(job_dir, output_dir):
                worker_status = "completed"
                final_output = resolve_final_output_path(output_dir, job_dir.name, args.video)
                if not completion_logged:
                    events.append(format_event(f"job completed: {final_output}"))
                    completion_logged = True
                live.update(
                    build_layout(
                        args.video,
                        worker_status,
                        restart_count,
                        snapshot,
                        list(events),
                        avg_chunk_seconds=avg_chunk_seconds,
                        final_output=final_output,
                    )
                )
                time.sleep(2)
                return

            if worker_status == "stalled" and worker.poll() is None:
                terminate_worker_tree(worker)
                delay = backoff.next_delay_seconds()
                next_restart_at = time.time() + delay
                events.append(format_event(f"worker stalled; restart in {delay}s"))

            elif not worker_alive:
                if next_restart_at is None:
                    delay = backoff.next_delay_seconds()
                    next_restart_at = time.time() + delay
                    exit_code = worker.poll()
                    events.append(format_event(f"worker exited ({exit_code}); restart in {delay}s"))
                worker_status = "backoff"

            if next_restart_at is not None and time.time() >= next_restart_at:
                worker = launch_worker_terminal(
                    args.video,
                    job_dir,
                    workspace,
                    input_dir=args.input_dir,
                    output_dir=args.output_dir,
                    work_dir=args.work_dir,
                )
                restart_count += 1
                events.append(format_event(f"worker restarted (pid {worker.pid})"))
                next_restart_at = None
                worker_status = "running"

            live.update(
                build_layout(
                    args.video,
                    worker_status,
                    restart_count,
                    snapshot,
                    list(events),
                    avg_chunk_seconds=avg_chunk_seconds,
                )
            )
            time.sleep(args.poll_interval)


if __name__ == "__main__":
    main()
