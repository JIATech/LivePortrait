import argparse
from pathlib import Path

import cv2
import imageio
import numpy as np


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Composite a generated portrait patch onto a full gameplay clip.")
    parser.add_argument("--full-video", required=True, help="Path to the full gameplay video")
    parser.add_argument("--patch-video", required=True, help="Path to the generated patch video")
    parser.add_argument("--output", required=True, help="Path to the output gameplay video")
    parser.add_argument("--x", type=int, required=True, help="Overlay X position")
    parser.add_argument("--y", type=int, required=True, help="Overlay Y position")
    parser.add_argument("--width", type=int, required=True, help="Overlay width")
    parser.add_argument("--height", type=int, required=True, help="Overlay height")
    parser.add_argument("--alpha-blur", type=float, default=1.2, help="Gaussian blur sigma for alpha edge softening")
    parser.add_argument("--alpha-cutoff", type=float, default=0.18, help="Minimum alpha kept after key extraction")
    parser.add_argument("--alpha-erode", type=int, default=0, help="Odd erosion kernel size applied to alpha for stronger edge cleanup")
    parser.add_argument("--alpha-post-blur", type=float, default=0.8, help="Gaussian blur sigma after alpha erosion/cutoff")
    parser.add_argument("--handoff-start", type=float, default=1.0, help="Vertical ratio where avatar alpha starts fading out toward the lower neck")
    parser.add_argument("--handoff-end", type=float, default=1.0, help="Vertical ratio where avatar alpha reaches zero near the bottom")
    parser.add_argument("--relight-strength", type=float, default=0.0, help="Strength of local luminance matching from the gameplay ROI onto the avatar")
    parser.add_argument("--relight-blur-ksize", type=int, default=1, help="Odd blur kernel size for local relighting estimation")
    parser.add_argument("--relight-min-gain", type=float, default=0.85, help="Minimum luminance gain allowed during relighting")
    parser.add_argument("--relight-max-gain", type=float, default=1.18, help="Maximum luminance gain allowed during relighting")
    parser.add_argument("--relight-top-weight", type=float, default=0.25, help="Relative relighting weight at the top of the avatar; bottom reaches full weight")
    parser.add_argument("--suppress-strength", type=float, default=0.45, help="How strongly to soften the original ROI under the avatar")
    parser.add_argument("--suppress-dilate", type=int, default=9, help="Kernel size for expanding the suppression mask")
    parser.add_argument("--suppress-blur", type=float, default=2.2, help="Gaussian blur sigma for suppression mask softening")
    parser.add_argument("--suppress-roi-blur-ksize", type=int, default=13, help="Odd kernel size for blurring the ROI under the avatar")
    return parser.parse_args()


def border_connected_mask(binary_mask: np.ndarray) -> np.ndarray:
    num_labels, labels = cv2.connectedComponents(binary_mask.astype(np.uint8), connectivity=8)
    if num_labels <= 1:
        return np.zeros_like(binary_mask, dtype=bool)

    border_labels = set(np.unique(labels[0, :]))
    border_labels.update(np.unique(labels[-1, :]))
    border_labels.update(np.unique(labels[:, 0]))
    border_labels.update(np.unique(labels[:, -1]))
    border_labels.discard(0)

    if not border_labels:
        return np.zeros_like(binary_mask, dtype=bool)

    out = np.isin(labels, list(border_labels))
    return out


def build_alpha(frame_bgr: np.ndarray, alpha_blur: float) -> np.ndarray:
    h, w = frame_bgr.shape[:2]

    rect_margin_x = max(8, w // 18)
    rect_margin_y = max(8, h // 18)
    rect = (
        rect_margin_x,
        rect_margin_y,
        max(2, w - rect_margin_x * 2),
        max(2, h - rect_margin_y * 2),
    )

    grabcut_mask = np.zeros((h, w), np.uint8)
    bg_model = np.zeros((1, 65), np.float64)
    fg_model = np.zeros((1, 65), np.float64)
    cv2.grabCut(frame_bgr, grabcut_mask, rect, bg_model, fg_model, 3, cv2.GC_INIT_WITH_RECT)

    alpha = np.where(
        (grabcut_mask == cv2.GC_FGD) | (grabcut_mask == cv2.GC_PR_FGD),
        255,
        0,
    ).astype(np.uint8)

    hsv = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2HSV)
    gray = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2GRAY)

    black_binary = (gray < 20).astype(np.uint8)
    light_neutral_binary = ((hsv[:, :, 1] < 35) & (gray > 120)).astype(np.uint8)

    black_bg = border_connected_mask(black_binary)
    light_bg = border_connected_mask(light_neutral_binary)
    forced_bg = black_bg | light_bg

    alpha[forced_bg] = 0

    kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (3, 3))
    alpha = cv2.morphologyEx(alpha, cv2.MORPH_OPEN, kernel)
    alpha = cv2.morphologyEx(alpha, cv2.MORPH_CLOSE, kernel)

    if alpha_blur > 0:
        alpha = cv2.GaussianBlur(alpha, (0, 0), alpha_blur)

    return alpha.astype(np.float32) / 255.0


def refine_alpha(alpha: np.ndarray, cutoff: float, erode_px: int, blur_sigma: float) -> np.ndarray:
    alpha = np.clip(alpha.astype(np.float32), 0.0, 1.0)
    alpha = np.where(alpha >= float(cutoff), alpha, 0.0)

    erode_px = max(0, int(erode_px))
    if erode_px > 1:
        if erode_px % 2 == 0:
            erode_px += 1
        kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (erode_px, erode_px))
        alpha_u8 = np.clip(alpha * 255.0, 0, 255).astype(np.uint8)
        alpha = cv2.erode(alpha_u8, kernel, iterations=1).astype(np.float32) / 255.0

    if blur_sigma > 0:
        alpha = cv2.GaussianBlur(alpha, (0, 0), blur_sigma)

    return np.clip(alpha, 0.0, 1.0)


def apply_head_handoff(alpha: np.ndarray, start_ratio: float, end_ratio: float) -> np.ndarray:
    alpha = np.clip(alpha.astype(np.float32), 0.0, 1.0)

    if end_ratio <= start_ratio or start_ratio >= 1.0:
        return alpha

    h = alpha.shape[0]
    y = np.linspace(0.0, 1.0, h, dtype=np.float32)
    ramp = np.ones_like(y)
    ramp[y >= end_ratio] = 0.0

    mid_mask = (y > start_ratio) & (y < end_ratio)
    ramp[mid_mask] = 1.0 - ((y[mid_mask] - start_ratio) / (end_ratio - start_ratio))

    return alpha * ramp[:, None]


def build_relight_gain_map(
    patch_bgr: np.ndarray,
    roi_bgr: np.ndarray,
    blur_ksize: int,
    min_gain: float,
    max_gain: float,
) -> np.ndarray:
    patch_gray = cv2.cvtColor(np.clip(patch_bgr, 0, 255).astype(np.uint8), cv2.COLOR_BGR2GRAY).astype(np.float32)
    roi_gray = cv2.cvtColor(np.clip(roi_bgr, 0, 255).astype(np.uint8), cv2.COLOR_BGR2GRAY).astype(np.float32)

    blur_ksize = max(1, int(blur_ksize))
    if blur_ksize % 2 == 0:
        blur_ksize += 1
    if blur_ksize > 1:
        patch_gray = cv2.GaussianBlur(patch_gray, (blur_ksize, blur_ksize), 0)
        roi_gray = cv2.GaussianBlur(roi_gray, (blur_ksize, blur_ksize), 0)

    gain = (roi_gray + 1.0) / (patch_gray + 1.0)
    return np.clip(gain.astype(np.float32), float(min_gain), float(max_gain))


def apply_local_relighting(
    patch_bgr: np.ndarray,
    roi_bgr: np.ndarray,
    alpha: np.ndarray,
    strength: float,
    blur_ksize: int,
    min_gain: float,
    max_gain: float,
    top_weight: float,
) -> np.ndarray:
    if strength <= 0:
        return patch_bgr

    gain = build_relight_gain_map(
        patch_bgr,
        roi_bgr,
        blur_ksize=blur_ksize,
        min_gain=min_gain,
        max_gain=max_gain,
    )

    h = patch_bgr.shape[0]
    vertical = np.linspace(float(top_weight), 1.0, h, dtype=np.float32)[:, None]
    weight = np.clip(alpha.astype(np.float32), 0.0, 1.0) * vertical * float(strength)
    effective_gain = 1.0 + (gain - 1.0) * weight
    relit = patch_bgr * effective_gain[..., None]
    return np.clip(relit, 0.0, 255.0)


def build_suppression_mask(alpha: np.ndarray, dilate_px: int, blur_sigma: float) -> np.ndarray:
    dilate_px = max(1, int(dilate_px))
    if dilate_px % 2 == 0:
        dilate_px += 1

    alpha_u8 = np.clip(alpha * 255.0, 0, 255).astype(np.uint8)
    kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (dilate_px, dilate_px))
    suppression = cv2.dilate(alpha_u8, kernel, iterations=1)

    if blur_sigma > 0:
        suppression = cv2.GaussianBlur(suppression, (0, 0), blur_sigma)

    return suppression.astype(np.float32) / 255.0


def apply_conservative_suppression(
    roi_bgr: np.ndarray,
    alpha: np.ndarray,
    strength: float,
    dilate_px: int,
    mask_blur_sigma: float,
    roi_blur_ksize: int,
) -> np.ndarray:
    if strength <= 0:
        return roi_bgr

    roi_blur_ksize = max(1, int(roi_blur_ksize))
    if roi_blur_ksize % 2 == 0:
        roi_blur_ksize += 1

    suppression = build_suppression_mask(alpha, dilate_px=dilate_px, blur_sigma=mask_blur_sigma)
    suppression = np.clip(suppression * float(strength), 0.0, 1.0)
    blurred_roi = cv2.GaussianBlur(roi_bgr, (roi_blur_ksize, roi_blur_ksize), 0)
    suppression_3 = suppression[..., None]
    return roi_bgr * (1.0 - suppression_3) + blurred_roi * suppression_3


def load_patch_frames(
    patch_path: str,
    width: int,
    height: int,
    alpha_blur: float,
    alpha_cutoff: float,
    alpha_erode: int,
    alpha_post_blur: float,
    handoff_start: float,
    handoff_end: float,
):
    cap = cv2.VideoCapture(patch_path)
    fps = cap.get(cv2.CAP_PROP_FPS) or 12.0
    frames = []

    while True:
        ok, frame = cap.read()
        if not ok:
            break
        resized = cv2.resize(frame, (width, height), interpolation=cv2.INTER_CUBIC)
        alpha = build_alpha(resized, alpha_blur)
        alpha = refine_alpha(alpha, cutoff=alpha_cutoff, erode_px=alpha_erode, blur_sigma=alpha_post_blur)
        alpha = apply_head_handoff(alpha, start_ratio=handoff_start, end_ratio=handoff_end)
        frames.append((resized.astype(np.float32), alpha))

    cap.release()

    if not frames:
        raise RuntimeError(f"No patch frames could be read from {patch_path}")

    return fps, frames


def main() -> None:
    args = parse_args()

    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    patch_fps, patch_frames = load_patch_frames(
        args.patch_video,
        args.width,
        args.height,
        args.alpha_blur,
        args.alpha_cutoff,
        args.alpha_erode,
        args.alpha_post_blur,
        args.handoff_start,
        args.handoff_end,
    )

    full_cap = cv2.VideoCapture(args.full_video)
    full_fps = full_cap.get(cv2.CAP_PROP_FPS) or 60.0

    writer = imageio.get_writer(
        output_path.as_posix(),
        fps=full_fps,
        format="mp4",
        codec="libx264",
        ffmpeg_params=["-crf", "18"],
        pixelformat="yuv420p",
        macro_block_size=1,
    )

    frame_index = 0
    while True:
        patch_index = int(frame_index * patch_fps / full_fps)
        if patch_index >= len(patch_frames):
            break

        ok, frame = full_cap.read()
        if not ok:
            break

        patch_bgr, alpha = patch_frames[patch_index]
        roi_original = frame[args.y:args.y + args.height, args.x:args.x + args.width].astype(np.float32)
        patch_bgr = apply_local_relighting(
            patch_bgr,
            roi_original,
            alpha,
            strength=args.relight_strength,
            blur_ksize=args.relight_blur_ksize,
            min_gain=args.relight_min_gain,
            max_gain=args.relight_max_gain,
            top_weight=args.relight_top_weight,
        )
        roi = roi_original
        roi = apply_conservative_suppression(
            roi,
            alpha,
            strength=args.suppress_strength,
            dilate_px=args.suppress_dilate,
            mask_blur_sigma=args.suppress_blur,
            roi_blur_ksize=args.suppress_roi_blur_ksize,
        )
        alpha_3 = alpha[..., None]
        comp = patch_bgr * alpha_3 + roi * (1.0 - alpha_3)
        frame[args.y:args.y + args.height, args.x:args.x + args.width] = np.clip(comp, 0, 255).astype(np.uint8)
        writer.append_data(frame[:, :, ::-1])
        frame_index += 1

    full_cap.release()
    writer.close()


if __name__ == "__main__":
    main()
