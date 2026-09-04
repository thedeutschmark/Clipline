import unittest

from native.services.composite import (
    build_composite_export_args,
    build_composite_filter_complex,
    composite_geometry,
    game_crop_region,
    normalize_facecam_layout,
)


def _guide(**over):
    layout = {"enabled": True, "x": 0.02, "y": 0.02, "w": 0.25, "h": 0.25}
    layout.update(over)
    return layout


class TestNormalizeFacecamLayout(unittest.TestCase):
    def test_none_gives_disabled_default_rect(self):
        layout = normalize_facecam_layout(None)
        self.assertFalse(layout["enabled"])
        self.assertEqual(layout["game_mode"], "avoid_facecam")

    def test_extents_clamped_to_legacy_bounds(self):
        layout = normalize_facecam_layout({"enabled": True, "w": 0.9, "h": 0.01})
        self.assertAlmostEqual(layout["w"], 0.70)
        self.assertAlmostEqual(layout["h"], 0.08)

    def test_position_kept_inside_frame(self):
        layout = normalize_facecam_layout({"enabled": True, "x": 0.9, "y": 0.9, "w": 0.25, "h": 0.25})
        self.assertAlmostEqual(layout["x"], 0.75)
        self.assertAlmostEqual(layout["y"], 0.75)

    def test_invalid_game_mode_falls_back(self):
        layout = normalize_facecam_layout({"enabled": True, "game_mode": "banana"})
        self.assertEqual(layout["game_mode"], "avoid_facecam")

    def test_bad_values_coerced_not_raised(self):
        layout = normalize_facecam_layout({"enabled": True, "x": "not-a-number"})
        self.assertAlmostEqual(layout["x"], 0.0)


class TestGameCropRegion(unittest.TestCase):
    def test_full_frame_returns_whole_source(self):
        region = game_crop_region(1920, 1080, _guide(game_mode="full_frame"))
        self.assertEqual(region, {"x": 0, "y": 0, "w": 1920, "h": 1080})

    def test_left_facecam_crops_the_right_side(self):
        # 480px-wide cam on the left loses 25% of width; 270px-tall cam loses
        # 25% of height — tie, side crop preferred, keeps the cam-free side.
        region = game_crop_region(1920, 1080, _guide())
        self.assertEqual(region["y"], 0)
        self.assertEqual(region["h"], 1080)
        # crop starts just past the cam's right edge
        self.assertEqual(region["x"], 38 + 480)
        self.assertEqual(region["w"], 1920 - (38 + 480))

    def test_right_facecam_crops_the_left_side(self):
        region = game_crop_region(1920, 1080, _guide(x=0.73))
        self.assertEqual(region["x"], 0)
        cam_x = round(0.73 * 1920)
        self.assertEqual(region["w"], cam_x)

    def test_tall_center_cam_uses_vertical_crop(self):
        # A wide-but-short top-center cam loses less game area cropped
        # vertically, so the rows it occupies are cut instead.
        region = game_crop_region(1920, 1080, _guide(x=0.25, y=0.02, w=0.5, h=0.2))
        self.assertEqual(region["x"], 0)
        self.assertEqual(region["w"], 1920)
        cam_y = round(0.02 * 1080)
        cam_h = round(0.2 * 1080)
        self.assertEqual(region["y"], cam_y + cam_h)
        self.assertEqual(region["h"], 1080 - (cam_y + cam_h))

    def test_bottom_cam_keeps_top_rows(self):
        region = game_crop_region(1920, 1080, _guide(x=0.25, y=0.78, w=0.5, h=0.2))
        self.assertEqual(region["y"], 0)
        cam_y = round(0.78 * 1080)
        self.assertEqual(region["h"], cam_y)

    def test_top_center_cam_uses_row_cut_not_asymmetric_side_cut(self):
        # A centered cam costs ~62% of the frame cut from either side but
        # only ~27% cut from its rows — the true-loss model must pick rows.
        region = game_crop_region(1920, 1080, _guide(x=0.375, y=0.02))
        self.assertEqual(region["x"], 0)
        self.assertEqual(region["w"], 1920)
        cam_bottom = round(0.02 * 1080) + 270
        self.assertEqual(region["y"], cam_bottom)
        self.assertEqual(region["h"], 1080 - cam_bottom)

    def test_mid_height_side_cam_still_uses_side_cut(self):
        # Cutting this cam's rows would keep only a sliver; the true side
        # cost (edge past the cam) is far smaller, so the column goes.
        region = game_crop_region(1920, 1080, _guide(x=0.02, y=0.4, w=0.25, h=0.2))
        self.assertEqual(region["y"], 0)
        self.assertEqual(region["h"], 1080)
        self.assertEqual(region["x"], 38 + 480)


class TestCompositeGeometry(unittest.TestCase):
    def test_reference_layout_numbers(self):
        geom = composite_geometry(1920, 1080, _guide(), 1080, 1920)
        face = geom["facecam"]
        game = geom["game"]
        self.assertEqual(face["out_w"], 476)
        self.assertEqual(face["out_h"], 268)  # keeps the source rect's aspect
        self.assertEqual(face["x"], (1080 - 476) // 2)
        self.assertEqual(face["y"], 58)  # 3% top buffer
        self.assertEqual(game["out_h"], 832)  # 1402x1080 crop scaled to 1080 wide
        self.assertEqual(game["y"], face["y"] + face["out_h"] + 58)  # 3% gap
        self.assertEqual(face["radius"], 19)  # 4% of facecam width
        self.assertEqual(geom["blur_radius"], 20)

    def test_geometry_scales_for_4k(self):
        geom_hd = composite_geometry(1920, 1080, _guide(), 1080, 1920)
        geom_4k = composite_geometry(1920, 1080, _guide(), 2160, 3840)
        for key in ("out_w", "out_h"):
            self.assertAlmostEqual(
                geom_4k["facecam"][key], geom_hd["facecam"][key] * 2, delta=2
            )
        self.assertAlmostEqual(geom_4k["game"]["out_h"], geom_hd["game"]["out_h"] * 2, delta=2)
        self.assertGreaterEqual(geom_4k["blur_radius"], geom_hd["blur_radius"])

    def test_even_output_dimensions(self):
        for out_w, out_h in ((1080, 1920), (2160, 3840), (1080, 1350), (1080, 1080)):
            geom = composite_geometry(1920, 1080, _guide(x=0.31, y=0.63), out_w, out_h)
            self.assertEqual(geom["facecam"]["out_w"] % 2, 0)
            self.assertEqual(geom["facecam"]["out_h"] % 2, 0)
            self.assertEqual(geom["game"]["out_h"] % 2, 0)

    def test_tall_guide_caps_facecam_band_and_keeps_aspect(self):
        # A 0.08x0.70 guide would scale to a band taller than the frame —
        # it caps at 38% of out_h instead, shrinking width to keep aspect.
        geom = composite_geometry(1920, 1080, _guide(x=0.02, y=0.02, w=0.08, h=0.7), 1080, 1920)
        face = geom["facecam"]
        self.assertEqual(face["out_h"], 730)  # 0.38 * 1920, evened
        self.assertAlmostEqual(
            face["out_w"] / face["out_h"], (0.08 * 1920) / (0.7 * 1080), places=2
        )

    def test_big_guide_game_band_stays_out_of_caption_zone(self):
        # A big corner cam pushes the game band down; it must be cropped to
        # fit above the caption zone instead of running off the frame.
        geom = composite_geometry(1920, 1080, _guide(x=0.02, y=0.02, w=0.4, h=0.7), 1080, 1920)
        game = geom["game"]
        self.assertLessEqual(game["y"] + game["out_h"], int(0.82 * 1920) + 2)
        self.assertLess(game["crop_h"], 1080)  # center-trimmed to fit
        self.assertEqual(game["crop_w"], 1114)  # cam-free right column

    def test_every_normalized_guide_composes_safely(self):
        # Sweep the whole clamp space coarsely: nothing may overflow the
        # frame or land negative, at every portrait format.
        xs = (0.0, 0.2, 0.4, 0.6, 0.75)
        ws = (0.08, 0.2, 0.4, 0.7)
        for x in xs:
            for y in xs:
                for w in ws:
                    for h in ws:
                        layout = normalize_facecam_layout(
                            {"enabled": True, "x": x, "y": y, "w": w, "h": h}
                        )
                        for out_w, out_h in ((1080, 1920), (2160, 3840)):
                            with self.subTest(x=x, y=y, w=w, h=h, out=(out_w, out_h)):
                                geom = composite_geometry(1920, 1080, layout, out_w, out_h)
                                f, g = geom["facecam"], geom["game"]
                                self.assertGreater(f["out_w"], 0)
                                self.assertGreater(f["out_h"], 0)
                                self.assertGreater(g["crop_w"], 1)
                                self.assertGreater(g["crop_h"], 1)
                                self.assertLessEqual(f["y"] + f["out_h"], out_h)
                                self.assertLessEqual(g["y"] + g["out_h"], int(0.82 * out_h) + 2)


class TestCompositeFilterComplex(unittest.TestCase):
    def _geom(self):
        return composite_geometry(1920, 1080, _guide(), 1080, 1920)

    def test_graph_layers_and_order(self):
        fc, maps = build_composite_filter_complex(self._geom())
        game_i = fc.index("[game]")
        face_i = fc.index("[face]")
        bg_i = fc.index("[bg]")
        overlay_i = fc.index("[bg][game]overlay")
        # every label is defined before the overlays consume it
        self.assertLess(max(game_i, face_i, bg_i), overlay_i)
        # game is a crop past the facecam, scaled to full width
        self.assertIn("crop=1402:1080:518:0", fc)
        self.assertIn("scale=1080:832:flags=lanczos", fc)
        # facecam keeps its source aspect and gets a rounded-corner alpha
        self.assertIn("crop=480:270:38:22", fc)
        self.assertIn("scale=476:268", fc)
        self.assertIn("format=yuva420p", fc)
        self.assertIn("geq=", fc)
        self.assertIn("hypot", fc)
        # background: cover-scale, crop, blur, dim
        self.assertIn("force_original_aspect_ratio=increase", fc)
        self.assertIn("boxblur=20:2", fc)
        self.assertIn("eq=brightness=-0.3", fc)
        # overlays: game onto bg at its band, facecam last
        self.assertIn("[bg][game]overlay=0:384", fc)
        self.assertIn("[g1][face]overlay=302:58", fc)
        self.assertEqual(maps, ["-map", "[v]", "-map", "0:a?"])

    def test_ass_appended_last_without_captions_null(self):
        fc, _ = build_composite_filter_complex(self._geom())
        self.assertTrue(fc.rstrip().endswith("[g2]null[v]"))
        fc_ass, _ = build_composite_filter_complex(self._geom(), subtitle_ass=r"C\:/tmp/c.ass")
        self.assertTrue(fc_ass.rstrip().endswith(r"[g2]ass='C\:/tmp/c.ass'[v]"))


class TestCompositeExportArgs(unittest.TestCase):
    def test_args_shape(self):
        geom = composite_geometry(1920, 1080, _guide(), 1080, 1920)
        args = build_composite_export_args(1500, 91000, geom, subtitle_ass=r"C\:/tmp/c.ass")
        self.assertEqual(args[:4], ["-ss", "1.500", "-to", "91.000"])
        self.assertIn("-filter_complex", args)
        self.assertNotIn("-vf", args)  # mutually exclusive with -filter_complex
        self.assertIn("-map", args)
        self.assertIn("-c:v", args)
        # loudnorm moved into the graph (audio), not -af next to -filter_complex
        self.assertNotIn("-af", args)
        fc = args[args.index("-filter_complex") + 1]
        self.assertIn("loudnorm", fc)

    def test_no_normalization_maps_input_audio(self):
        geom = composite_geometry(1920, 1080, _guide(), 1080, 1920)
        args = build_composite_export_args(0, 1000, geom, normalize_audio=False)
        fc = args[args.index("-filter_complex") + 1]
        self.assertNotIn("loudnorm", fc)
        self.assertIn("0:a?", args)


if __name__ == "__main__":
    unittest.main()
