import argparse
import json
from pathlib import Path
import time


def _load_json_file(path: Path, label: str) -> dict[str, object]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"Invalid {label} JSON: {path}") from exc

    if not isinstance(data, dict):
        raise ValueError(f"Invalid {label} data: expected object in {path}")

    return data


def _require_manifest_keys(manifest: dict[str, object], path: Path, required_keys: tuple[str, ...]) -> None:
    missing = [key for key in required_keys if key not in manifest]
    if missing:
        raise ValueError(f"Invalid manifest data: missing {', '.join(missing)} in {path}")


def _validate_chunks(chunks: list[object], path: Path) -> None:
    for index, chunk in enumerate(chunks):
        if not isinstance(chunk, dict):
            raise ValueError(f"Invalid manifest chunk at index {index} in {path}")

        if "status" not in chunk or "index" not in chunk:
            raise ValueError(f"Invalid manifest chunk at index {index} in {path}: missing status or index")


def find_last_artifact(paths: list[Path]) -> Path | None:
    existing = [path for path in paths if path.exists()]
    if not existing:
        return None

    return max(existing, key=lambda path: path.stat().st_mtime)


def _find_final_output(job_dir: Path) -> str | None:
    output_dir = job_dir / "output"
    if not output_dir.exists():
        return None

    candidates = [path for path in output_dir.iterdir() if path.is_file() and path.name != "report.json"]
    if not candidates:
        return None

    return str(max(candidates, key=lambda path: path.stat().st_mtime))


def _format_current_chunk(status: dict[str, object]) -> str:
    current_chunk = status["current_chunk"]
    if isinstance(current_chunk, int):
        return f"chunk {current_chunk + 1} of {status['total_chunks']}"

    return str(current_chunk)


def _positive_int(value: str) -> int:
    parsed = int(value)
    if parsed <= 0:
        raise argparse.ArgumentTypeError("interval must be greater than 0")

    return parsed


def resolve_job_dir(work_dir: Path, video_name: str) -> Path:
    for manifest_path in work_dir.glob("*/manifests/manifest.json"):
        try:
            data = _load_json_file(manifest_path, "manifest")
        except ValueError:
            continue

        input_video = data.get("input_video")
        if not isinstance(input_video, str):
            continue

        if Path(input_video).name == video_name:
            return manifest_path.parent.parent

    raise FileNotFoundError(f"No pipeline job found for video: {video_name}")


def collect_pipeline_status(job_dir: Path) -> dict[str, object]:
    manifest_path = job_dir / "manifests" / "manifest.json"
    manifest = _load_json_file(manifest_path, "manifest")
    _require_manifest_keys(manifest, manifest_path, ("video_id", "input_video", "chunks"))

    chunks = manifest["chunks"]
    if not isinstance(chunks, list):
        raise ValueError(f"Invalid manifest data: chunks must be a list in {manifest_path}")
    _validate_chunks(chunks, manifest_path)

    done = sum(1 for chunk in chunks if chunk["status"] == "done")
    failed = sum(1 for chunk in chunks if chunk["status"] == "failed")
    pending = sum(1 for chunk in chunks if chunk["status"] == "pending")
    total = len(chunks)
    failed_chunk_data = next((chunk for chunk in chunks if chunk["status"] == "failed"), None)
    failed_chunk = failed_chunk_data["index"] if failed_chunk_data is not None else None
    pending_chunk = next((chunk["index"] for chunk in chunks if chunk["status"] == "pending"), None)
    current_chunk = failed_chunk if failed_chunk is not None else pending_chunk if pending_chunk is not None else "completed"

    report_path = job_dir / "output" / "report.json"
    report = _load_json_file(report_path, "report") if report_path.exists() else {}
    total_processing_seconds = float(report.get("total_processing_seconds", 0.0))
    eta_seconds = None
    if done > 0 and failed == 0:
        eta_seconds = (total_processing_seconds / done) * pending

    chunk_artifacts = list((job_dir / "chunks").glob("**/*.mp4")) + list((job_dir / "chunks").glob("**/*.pkl"))
    output_artifacts = [
        path
        for path in (job_dir / "output").glob("*")
        if path.is_file() and path.name != "report.json"
    ]
    last_artifact = find_last_artifact(chunk_artifacts + output_artifacts)
    final_output = _find_final_output(job_dir)
    last_error = next(
        (chunk.get("error") for chunk in reversed(chunks) if isinstance(chunk.get("error"), str) and chunk.get("error")),
        None,
    )

    return {
        "video_id": manifest["video_id"],
        "input_video": manifest["input_video"],
        "total_chunks": total,
        "done_chunks": done,
        "pending_chunks": pending,
        "failed_chunks": failed,
        "percent_complete": round((done / total) * 100, 2) if total else 0.0,
        "current_chunk": current_chunk,
        "eta_seconds": eta_seconds,
        "last_artifact": str(last_artifact) if last_artifact is not None else None,
        "final_output": final_output,
        "last_error": last_error,
    }


def format_seconds(value: float | None) -> str:
    if value is None:
        return "unknown"

    return f"{value:.1f}s"


def format_status_text(status: dict[str, object]) -> str:
    return "\n".join(
        [
            f"video_id: {status['video_id']}",
            f"input_video: {status['input_video']}",
            (
                f"chunks: {status['done_chunks']} done / {status['pending_chunks']} pending / "
                f"{status['failed_chunks']} failed / {status['total_chunks']} total"
            ),
            f"progress: {status['percent_complete']}%",
            f"current_chunk: {_format_current_chunk(status)}",
            f"ETA: {format_seconds(status['eta_seconds'])}",
            f"last_artifact: {status.get('last_artifact') or 'unknown'}",
            f"final_output: {status.get('final_output') or 'not ready'}",
            f"last_error: {status.get('last_error') or 'none'}",
        ]
    )


def clear_screen() -> None:
    print("\033[2J\033[H", end="")


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description="Watch LivePortrait pipeline progress")
    subcommands = parser.add_subparsers(dest="mode", required=True)

    status_parser = subcommands.add_parser("status")
    status_parser.add_argument("--work-dir", default=".pipeline_work")
    status_parser.add_argument("--video", required=True)

    watch_parser = subcommands.add_parser("watch")
    watch_parser.add_argument("--work-dir", default=".pipeline_work")
    watch_parser.add_argument("--video", required=True)
    watch_parser.add_argument("--interval", type=_positive_int, default=10)

    return parser.parse_args(argv)


def main(argv=None):
    args = parse_args(argv)
    try:
        job_dir = resolve_job_dir(Path(args.work_dir), args.video)
    except FileNotFoundError as exc:
        print(str(exc))
        return 1

    if args.mode == "status":
        print(format_status_text(collect_pipeline_status(job_dir)))
        return 0

    try:
        while True:
            clear_screen()
            try:
                print(format_status_text(collect_pipeline_status(job_dir)))
            except (ValueError, FileNotFoundError, PermissionError, OSError) as exc:
                print(f"Temporary status error: {exc}")
            time.sleep(args.interval)
    except KeyboardInterrupt:
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
