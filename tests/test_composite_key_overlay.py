import importlib.util
from pathlib import Path
import unittest

import numpy as np


MODULE_PATH = Path(__file__).resolve().parents[1] / "tools" / "composite_key_overlay.py"
SPEC = importlib.util.spec_from_file_location("composite_key_overlay", MODULE_PATH)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(MODULE)


class CompositeKeyOverlayTests(unittest.TestCase):
    def test_build_relight_gain_map_tracks_roi_luminance(self):
        patch = np.full((6, 4, 3), 100.0, dtype=np.float32)
        roi = np.full((6, 4, 3), 100.0, dtype=np.float32)
        roi[3:, :, :] = 160.0

        gain = MODULE.build_relight_gain_map(patch, roi, blur_ksize=1, min_gain=0.8, max_gain=1.4)

        self.assertAlmostEqual(float(gain[1, 1]), 1.0, places=3)
        self.assertGreater(float(gain[4, 1]), 1.0)
        self.assertLessEqual(float(gain[4, 1]), 1.4)

    def test_apply_local_relighting_prefers_lower_region(self):
        patch = np.full((6, 4, 3), 100.0, dtype=np.float32)
        roi = np.full((6, 4, 3), 100.0, dtype=np.float32)
        roi[3:, :, :] = 180.0
        alpha = np.ones((6, 4), dtype=np.float32)

        relit = MODULE.apply_local_relighting(
            patch,
            roi,
            alpha,
            strength=1.0,
            blur_ksize=1,
            min_gain=0.8,
            max_gain=1.5,
            top_weight=0.15,
        )

        self.assertLess(float(relit[1, 1, 0]), float(relit[4, 1, 0]))
        self.assertAlmostEqual(float(relit[0, 0, 0]), 100.0, delta=8.0)

    def test_refine_alpha_removes_soft_halo_pixels(self):
        alpha = np.zeros((9, 9), dtype=np.float32)
        alpha[2:7, 2:7] = 0.35
        alpha[3:6, 3:6] = 1.0

        refined = MODULE.refine_alpha(alpha, cutoff=0.5, erode_px=3, blur_sigma=0.0)

        self.assertGreater(refined[4, 4], 0.99)
        self.assertEqual(float(refined[2, 4]), 0.0)
        self.assertEqual(float(refined[0, 0]), 0.0)

    def test_apply_head_handoff_reduces_lower_neck_alpha(self):
        alpha = np.ones((10, 4), dtype=np.float32)

        handed_off = MODULE.apply_head_handoff(alpha, start_ratio=0.6, end_ratio=0.9)

        self.assertGreater(float(handed_off[0, 1]), 0.99)
        self.assertGreater(float(handed_off[6, 1]), 0.0)
        self.assertLess(float(handed_off[6, 1]), 1.0)
        self.assertEqual(float(handed_off[9, 1]), 0.0)

    def test_build_suppression_mask_expands_alpha_region(self):
        alpha = np.zeros((21, 21), dtype=np.float32)
        alpha[8:13, 8:13] = 1.0

        suppression = MODULE.build_suppression_mask(alpha, dilate_px=5, blur_sigma=0.0)

        self.assertGreater(suppression[10, 10], 0.99)
        self.assertGreater(suppression[7, 10], 0.0)
        self.assertEqual(float(suppression[0, 0]), 0.0)

    def test_apply_conservative_suppression_softens_detail_under_avatar(self):
        roi = np.zeros((21, 21, 3), dtype=np.float32)
        roi[:, :] = 40.0
        roi[10, 10] = 255.0

        alpha = np.zeros((21, 21), dtype=np.float32)
        alpha[8:13, 8:13] = 1.0

        suppressed = MODULE.apply_conservative_suppression(
            roi,
            alpha,
            strength=0.8,
            dilate_px=5,
            mask_blur_sigma=0.0,
            roi_blur_ksize=9,
        )

        self.assertLess(float(suppressed[10, 10, 0]), float(roi[10, 10, 0]))
        self.assertAlmostEqual(float(suppressed[0, 0, 0]), float(roi[0, 0, 0]), places=4)


if __name__ == "__main__":
    unittest.main()
