from __future__ import annotations

import os
import tempfile
import types
import unittest
from pathlib import Path
from unittest import mock

from local_video_mvp.tui import LocalVideoMvpTui, ThumbnailConcept

try:
    from PIL import Image
except Exception:  # noqa: BLE001
    Image = None


def _make_tui(project_dir: Path, preview_dir: Path) -> LocalVideoMvpTui:
    """A LocalVideoMvpTui with only the attributes the thumbnail render path needs.

    Bypasses the curses-heavy __init__; the render helpers are otherwise pure.
    """
    tui = object.__new__(LocalVideoMvpTui)
    tui._active_project_dir = None
    tui.config = types.SimpleNamespace(
        project_dir=project_dir,
        prompt="",
        active_channel="general",
        script_language="en",
    )
    tui._append_log = lambda *a, **k: None
    tui._set_status = lambda *a, **k: None
    tui._thumbnail_preview_dir = lambda: preview_dir  # type: ignore[method-assign]
    return tui


class ThumbnailRenderTests(unittest.TestCase):
    @unittest.skipIf(Image is None, "Pillow not installed")
    def test_renders_1280x720_jpeg_via_gradient_fallback(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            project_dir = root / "proj"  # empty => no footage => designed gradient
            project_dir.mkdir()
            preview_dir = root / "preview"
            preview_dir.mkdir()
            tui = _make_tui(project_dir, preview_dir)

            concept = ThumbnailConcept(
                background_prompt="psalm of light and life",
                headline_text="luz e vida",
                headline_position="center",
                headline_color="white",
                outline_color="black",
                accent_color="#22c55e",
                style_hint="high-contrast hero",
                subject_hint="dramatic sky",
                seed=1,
            )

            # Force the deterministic gradient path regardless of ambient env.
            with mock.patch.dict(os.environ, {"IMAGINE_THUMBNAIL_BG": "auto"}):
                out = tui._render_thumbnail_image(concept)
            self.assertTrue(out.exists())
            with Image.open(out) as img:
                self.assertEqual(img.size, (1280, 720))
                self.assertEqual(img.format, "JPEG")
            self.assertGreater(out.stat().st_size, 5000)  # non-trivial render

    def test_bundled_display_font_is_used(self) -> None:
        # Must resolve to the bundled Anton font, not an OS fallback — otherwise a
        # missing/unshipped bundled font would go undetected on dev machines.
        tui = object.__new__(LocalVideoMvpTui)
        font_path = tui._thumbnail_font_path()
        self.assertIsNotNone(font_path)
        self.assertEqual(Path(font_path).name, "Anton-Regular.ttf")
        self.assertTrue(Path(font_path).exists())


if __name__ == "__main__":
    unittest.main()
