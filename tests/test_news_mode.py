from __future__ import annotations

import json
import tempfile
import types
import unittest
from pathlib import Path
from unittest import mock

from local_video_mvp.models import (
    AssetCandidate,
    AssetRight,
    NewsSourceCandidate,
    PipelineConfig,
    PlannedShot,
    Scene,
    ScriptPlan,
    ShotPlan,
    default_news_feed_urls,
)
from local_video_mvp.pipeline import VideoPipeline
from local_video_mvp.tui import ConfigEditResult, LocalVideoMvpTui, TuiConfig


class NewsModePipelineTests(unittest.TestCase):
    def _make_pipeline(self, project_dir: Path, **config_overrides: object) -> VideoPipeline:
        config = PipelineConfig(
            prompt="Iran conflict update",
            project_dir=project_dir,
            content_mode="news",
            news_feed_urls=["https://example.com/feed.xml"],
            news_max_candidates=2,
            script_engine="template",
            **config_overrides,
        )
        pipeline = VideoPipeline(config)
        pipeline._prepare_dirs()
        return pipeline

    def test_prepare_news_sources_limits_expensive_work_to_max_candidates(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            pipeline = self._make_pipeline(Path(tmpdir), news_max_age_hours=999)
            entries = []
            for index in range(5):
                entries.append(
                    types.SimpleNamespace(
                        link=f"https://example.com/article-{index}",
                        title=f"Story {index}",
                        summary=f"Summary {index}",
                        published=f"Sat, {15 - index:02d} Mar 2026 00:00:00 GMT",
                        source=types.SimpleNamespace(title="Example News"),
                    )
                )

            fake_feedparser = types.SimpleNamespace(parse=lambda _url: types.SimpleNamespace(entries=entries))
            fake_trafilatura = types.SimpleNamespace()
            article_urls: list[str] = []

            with mock.patch.dict(
                "sys.modules",
                {"feedparser": fake_feedparser, "trafilatura": fake_trafilatura},
            ):
                with mock.patch.object(
                    pipeline,
                    "_article_text_payload",
                    side_effect=lambda url: (article_urls.append(url) or ("html", f"text for {url}")),
                ):
                    with mock.patch.object(
                        pipeline,
                        "_capture_news_screenshot",
                        side_effect=lambda source_id, _url: (None, f"no screenshot for {source_id}"),
                    ):
                        with mock.patch.object(
                            pipeline,
                            "_build_news_source_card",
                            side_effect=lambda **kwargs: f"/tmp/{kwargs['source_id']}-card.png",
                        ):
                            result = pipeline._prepare_news_sources()

            self.assertEqual(result["candidates"], 2)
            self.assertEqual(len(article_urls), 2)

            payload = json.loads(pipeline.paths["news_source_candidates"].read_text(encoding="utf-8"))
            self.assertEqual(len(payload["candidates"]), 2)
            self.assertEqual(payload["news_min_approved_sources"], 2)
            self.assertEqual(payload["news_jurisdiction"], "us")

    def test_validate_news_review_gate_requires_distinct_domains(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            pipeline = self._make_pipeline(Path(tmpdir))
            candidates = [
                NewsSourceCandidate(
                    source_id="src-1",
                    feed_url="https://example.com/feed.xml",
                    article_url="https://example.com/a",
                    canonical_url="https://example.com/a",
                    domain="example.com",
                    title="First",
                    publisher="Example",
                    summary="First summary",
                    extracted_text="First fact",
                    source_card_path="/tmp/src-1-card.png",
                ),
                NewsSourceCandidate(
                    source_id="src-2",
                    feed_url="https://example.com/feed.xml",
                    article_url="https://example.com/b",
                    canonical_url="https://example.com/b",
                    domain="example.com",
                    title="Second",
                    publisher="Example",
                    summary="Second summary",
                    extracted_text="Second fact",
                    source_card_path="/tmp/src-2-card.png",
                ),
            ]
            pipeline._write_json(
                pipeline.paths["news_source_candidates"],
                {
                    "schema_version": 1,
                    "content_mode": "news",
                    "news_min_approved_sources": 2,
                    "candidates": [candidate.to_dict() for candidate in candidates],
                },
            )
            pipeline._save_news_review_state(
                {
                    "src-1": {"decision": "approve-facts", "updated_at": "2026-03-15T00:00:00+00:00"},
                    "src-2": {"decision": "approve-facts", "updated_at": "2026-03-15T00:00:00+00:00"},
                }
            )

            with self.assertRaisesRegex(RuntimeError, "distinct domains"):
                pipeline._validate_news_review_gate()

    def test_validate_news_review_gate_reports_zero_entry_feeds_clearly(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            pipeline = self._make_pipeline(Path(tmpdir))
            pipeline._write_json(
                pipeline.paths["news_source_candidates"],
                {
                    "schema_version": 1,
                    "content_mode": "news",
                    "feed_urls": ["https://news.google.com/topics/example"],
                    "raw_entry_count": 0,
                    "eligible_candidate_count": 0,
                    "candidates": [],
                },
            )
            pipeline._save_news_review_state({})

            with self.assertRaisesRegex(RuntimeError, "zero entries"):
                pipeline._validate_news_review_gate()

    def test_generate_news_script_plan_after_approved_sources(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            pipeline = self._make_pipeline(Path(tmpdir))
            candidates = [
                NewsSourceCandidate(
                    source_id="src-1",
                    feed_url="https://example.com/feed.xml",
                    article_url="https://example.com/a",
                    canonical_url="https://example.com/a",
                    domain="example.com",
                    title="First",
                    publisher="Example",
                    summary="First approved summary",
                    extracted_text="First fact from approved source.",
                    source_card_path="/tmp/src-1-card.png",
                ),
                NewsSourceCandidate(
                    source_id="src-2",
                    feed_url="https://example.org/feed.xml",
                    article_url="https://example.org/b",
                    canonical_url="https://example.org/b",
                    domain="example.org",
                    title="Second",
                    publisher="Example Org",
                    summary="Second approved summary",
                    extracted_text="Second fact from approved source.",
                    screenshot_path="/tmp/src-2-shot.png",
                    screenshot_available=True,
                ),
            ]
            pipeline._write_json(
                pipeline.paths["news_source_candidates"],
                {
                    "schema_version": 1,
                    "content_mode": "news",
                    "news_min_approved_sources": 2,
                    "candidates": [candidate.to_dict() for candidate in candidates],
                },
            )
            pipeline._save_news_review_state(
                {
                    "src-1": {"decision": "approve-facts", "updated_at": "2026-03-15T00:00:00+00:00"},
                    "src-2": {"decision": "approve-screenshot", "updated_at": "2026-03-15T00:00:00+00:00"},
                }
            )

            brief = pipeline._ensure_news_brief()
            plan = pipeline._generate_script_plan()

            self.assertEqual(len(brief.sources), 2)
            self.assertEqual(len(brief.facts), 2)
            self.assertTrue(pipeline.paths["news_brief"].exists())
            self.assertGreaterEqual(len(plan.scenes), 2)

            source_scene_strategies = {
                tuple(scene.source_refs): scene.visual_strategy
                for scene in plan.scenes
                if scene.source_refs
            }
            self.assertEqual(source_scene_strategies.get(("src-1",)), "source-card")
            self.assertEqual(source_scene_strategies.get(("src-2",)), "news-source-screenshot")

    def test_tui_seeds_curated_news_feeds_when_news_mode_has_none(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            config = TuiConfig(
                prompt="Iran conflict update",
                content_mode="news",
                asset_keywords=[],
                news_feed_urls=[],
                project_dir=Path(tmpdir),
                minutes=3,
                resolution="1280x720",
                burn_subtitles=True,
                subtitle_preset="regular",
                subtitle_position="bottom",
                subtitle_accent_color="sunflower",
                subtitle_box_color="sunflower",
                fast_mode=False,
                script_tone="conversational",
                target_audience="general audience",
                hook_style="surprising-fact",
                narrative_mode="explainer",
                example_density="balanced",
                asset_mode="prefer-video",
                image_motion_style="slow",
                tts_engine="melo",
                piper_voice_id="",
                piper_speaker_id=None,
                voice_profile="balanced",
                voice_speed=1.0,
                melo_language="EN",
                melo_speaker="EN-US",
                kokoro_lang_code="en-us",
                kokoro_voice="af_heart",
            )

            with mock.patch.object(LocalVideoMvpTui, "_load_persisted_settings", autospec=True, return_value=None):
                app = LocalVideoMvpTui(config)
            try:
                self.assertEqual(app.config.news_feed_urls, default_news_feed_urls())
            finally:
                app._shutdown()

    def test_tui_forces_hitl_on_for_news_mode(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            config = TuiConfig(
                prompt="Iran conflict update",
                content_mode="news",
                asset_keywords=[],
                news_feed_urls=[],
                project_dir=Path(tmpdir),
                minutes=3,
                resolution="1280x720",
                burn_subtitles=True,
                subtitle_preset="regular",
                subtitle_position="bottom",
                subtitle_accent_color="sunflower",
                subtitle_box_color="sunflower",
                fast_mode=False,
                script_tone="conversational",
                target_audience="general audience",
                hook_style="surprising-fact",
                narrative_mode="explainer",
                example_density="balanced",
                asset_mode="prefer-video",
                image_motion_style="slow",
                tts_engine="melo",
                piper_voice_id="",
                piper_speaker_id=None,
                voice_profile="balanced",
                voice_speed=1.0,
                melo_language="EN",
                melo_speaker="EN-US",
                kokoro_lang_code="en-us",
                kokoro_voice="af_heart",
            )

            with mock.patch.object(LocalVideoMvpTui, "_load_persisted_settings", autospec=True, return_value=None):
                app = LocalVideoMvpTui(config)
            try:
                app._hitl_enabled = False
                changed = app._enforce_news_hitl_policy()
                self.assertTrue(changed)
                self.assertTrue(app._hitl_enabled)
            finally:
                app._shutdown()

    def test_shot_plan_is_deterministic_and_caps_shots_per_scene(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            config = PipelineConfig(
                prompt="Why ports matter",
                project_dir=Path(tmpdir),
                content_mode="explainer",
                script_engine="template",
            )
            pipeline = VideoPipeline(config)
            plan = ScriptPlan(
                title="Ports",
                summary="Why ports matter",
                scenes=[
                    Scene(
                        scene_id="scene-1",
                        clip_name="intro",
                        heading="Why ports matter",
                        voiceover="Ports move huge amounts of goods every day. They shape prices, logistics, and trade policy.",
                        search_terms=["ports", "shipping", "trade"],
                        seconds=12.0,
                    ),
                    Scene(
                        scene_id="scene-2",
                        clip_name="followup",
                        heading="Container cranes",
                        voiceover="Container cranes dominate the skyline.",
                        search_terms=["container cranes"],
                        seconds=5.0,
                    ),
                ],
            )

            first = pipeline._build_shot_plan(plan)
            second = pipeline._build_shot_plan(plan)

            self.assertEqual(first.to_dict(), second.to_dict())
            self.assertEqual([shot.shot_id for shot in first.shots], ["scene-1_shot_01", "scene-1_shot_02", "scene-2_shot_01"])
            self.assertTrue(all(1 <= shot.total_shots <= 2 for shot in first.shots))

    def test_shot_review_state_blocks_only_low_confidence_shots(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            pipeline = VideoPipeline(
                PipelineConfig(
                    prompt="Ports",
                    project_dir=Path(tmpdir),
                    content_mode="explainer",
                    script_engine="template",
                )
            )
            pipeline._prepare_dirs()
            plan = ScriptPlan(
                title="Ports",
                summary="Why ports matter",
                scenes=[
                    Scene(
                        scene_id="scene-1",
                        clip_name="intro",
                        heading="Kharg Island oil hub",
                        voiceover=(
                            "Kharg Island is a critical oil export hub. "
                            "Its location shapes how regional energy shipments move."
                        ),
                        search_terms=["Kharg Island", "oil exports", "energy shipments"],
                        seconds=12.0,
                    )
                ],
            )
            shot_plan = pipeline._build_shot_plan(plan)
            shot_plan.shots[0].match_confidence = "low"
            if len(shot_plan.shots) > 1:
                shot_plan.shots[1].match_confidence = "high"

            payload = pipeline._build_shot_review_state(shot_plan, None)
            shots = payload["shots"]
            blocked_flags = {shot_id: bool(value["blocked"]) for shot_id, value in shots.items()}
            self.assertTrue(any(blocked_flags.values()))
            self.assertTrue(any(not value for value in blocked_flags.values()))

    def test_candidate_ranking_score_uses_scene_tokens_without_crashing(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            pipeline = VideoPipeline(
                PipelineConfig(
                    prompt="Ports",
                    project_dir=Path(tmpdir),
                    content_mode="explainer",
                    script_engine="template",
                )
            )
            scene = Scene(
                scene_id="scene-1",
                clip_name="intro",
                heading="Kharg Island oil hub",
                voiceover="Kharg Island is a critical oil export hub.",
                search_terms=["Kharg Island", "oil exports", "shipping"],
                seconds=8.0,
            )
            candidate = AssetCandidate(
                source_platform="pexels",
                media_type="video",
                download_url="https://example.com/download/asset-1.mp4",
                source_url="https://example.com/kharg-island-oil",
                source_asset_id="asset-1",
                description="Oil terminal activity at Kharg Island",
                width=1280,
                height=720,
                duration_seconds=12.0,
                query="Kharg Island oil exports",
            )

            score = pipeline._candidate_ranking_score(
                candidate,
                scene,
                quality_score=4.0,
                provider_rank=0,
                query_rank=0,
            )

            self.assertGreater(score, 4.0)

    def test_prepare_shot_candidates_writes_manifest_with_prefetched_video_and_image_candidates(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            pipeline = VideoPipeline(
                PipelineConfig(
                    prompt="Ports",
                    project_dir=Path(tmpdir),
                    content_mode="explainer",
                    script_engine="template",
                    asset_shortlist_size=6,
                )
            )
            pipeline._prepare_dirs()
            shot_plan = ShotPlan(
                title="Ports",
                summary="Why ports matter",
                shots=[
                    PlannedShot(
                        shot_id="scene-1_shot_01",
                        scene_id="scene-1",
                        clip_name="intro",
                        heading="Ports matter",
                        shot_index=1,
                        total_shots=1,
                        narration_text="Ports move goods.",
                        seconds=6.0,
                        narration_start=0.0,
                        narration_end=6.0,
                        shot_objective="Ports move goods.",
                        key_info="Ports move goods.",
                        required_entities=["Ports"],
                        search_queries=["ports shipping"],
                        fallback_strategy="stock -> still-image -> internal-card -> placeholder",
                        visual_type="stock-video",
                        match_confidence="medium",
                        fallback_level="exact",
                    )
                ],
            )
            pipeline._write_json(pipeline.paths["shot_plan"], shot_plan.to_dict())

            ranked_candidates = [
                AssetCandidate(
                    source_platform="pexels",
                    media_type="video",
                    download_url="https://example.com/video-1.mp4",
                    source_url="https://example.com/video-1",
                    source_asset_id="video-1",
                    description="Container ship at port",
                    query="ports shipping",
                    ranking_score=5.0,
                ),
                AssetCandidate(
                    source_platform="pixabay",
                    media_type="image",
                    download_url="https://example.com/image-1.jpg",
                    source_url="https://example.com/image-1",
                    source_asset_id="image-1",
                    description="Port cranes at sunset",
                    query="ports shipping",
                    ranking_score=4.5,
                ),
            ]

            with mock.patch.object(pipeline, "_rank_scene_candidates", return_value=ranked_candidates):
                with mock.patch.object(
                    pipeline,
                    "_prefetch_shot_candidate_preview",
                    side_effect=[
                        ("/tmp/video-1.mp4", True, None),
                        ("/tmp/image-1.jpg", True, None),
                    ],
                ):
                    outputs = pipeline.prepare_shot_candidates("scene-1_shot_01", key_info="Updated port logistics")

            manifest_path = Path(outputs["shot_candidate_manifest"])
            payload = json.loads(manifest_path.read_text(encoding="utf-8"))
            self.assertEqual(payload["shot_id"], "scene-1_shot_01")
            self.assertEqual(payload["key_info"], "Updated port logistics")
            self.assertFalse(payload["editorial_locked"])
            self.assertEqual(len(payload["candidates"]), 2)
            self.assertEqual(len(payload["video_candidates"]), 1)
            self.assertEqual(len(payload["image_candidates"]), 1)
            self.assertEqual(payload["candidates"][0]["preview_local_path"], "/tmp/video-1.mp4")
            self.assertEqual(payload["candidates"][1]["preview_local_path"], "/tmp/image-1.jpg")

    def test_apply_key_info_to_shot_manual_queries_override_previous_queries(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            pipeline = VideoPipeline(
                PipelineConfig(
                    prompt="Ports",
                    project_dir=Path(tmpdir),
                    content_mode="explainer",
                    script_engine="template",
                )
            )
            shot = PlannedShot(
                shot_id="scene-1_shot_01",
                scene_id="scene-1",
                clip_name="intro",
                heading="Ports matter",
                shot_index=1,
                total_shots=1,
                narration_text="Ports move goods.",
                seconds=6.0,
                narration_start=0.0,
                narration_end=6.0,
                shot_objective="Ports move goods.",
                key_info="Ports move goods.",
                required_entities=["Ports"],
                search_queries=["old query", "legacy term"],
                fallback_strategy="stock -> still-image -> internal-card -> placeholder",
                visual_type="stock-video",
                match_confidence="medium",
                fallback_level="exact",
            )

            updated = pipeline._apply_key_info_to_shot(
                shot,
                "Port logistics",
                search_queries=["container port", "cargo cranes"],
            )

            self.assertIn("container port", updated.search_queries)
            self.assertIn("cargo cranes", updated.search_queries)
            self.assertNotIn("old query", updated.search_queries)
            self.assertNotIn("legacy term", updated.search_queries)

    def test_prepare_shot_candidates_keeps_image_pool_when_top_mixed_shortlist_is_video_only(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            pipeline = VideoPipeline(
                PipelineConfig(
                    prompt="Ports",
                    project_dir=Path(tmpdir),
                    content_mode="explainer",
                    script_engine="template",
                    asset_shortlist_size=1,
                )
            )
            pipeline._prepare_dirs()
            shot_plan = ShotPlan(
                title="Ports",
                summary="Why ports matter",
                shots=[
                    PlannedShot(
                        shot_id="scene-1_shot_01",
                        scene_id="scene-1",
                        clip_name="intro",
                        heading="Ports matter",
                        shot_index=1,
                        total_shots=1,
                        narration_text="Ports move goods.",
                        seconds=6.0,
                        narration_start=0.0,
                        narration_end=6.0,
                        shot_objective="Ports move goods.",
                        key_info="Ports move goods.",
                        required_entities=["Ports"],
                        search_queries=["ports shipping"],
                        fallback_strategy="stock -> still-image -> internal-card -> placeholder",
                        visual_type="stock-video",
                        match_confidence="medium",
                        fallback_level="exact",
                    )
                ],
            )
            pipeline._write_json(pipeline.paths["shot_plan"], shot_plan.to_dict())

            ranked_candidates = [
                AssetCandidate(
                    source_platform="pexels",
                    media_type="video",
                    download_url="https://example.com/video-1.mp4",
                    source_url="https://example.com/video-1",
                    source_asset_id="video-1",
                    description="Container ship at port",
                    query="ports shipping",
                    ranking_score=5.0,
                ),
                AssetCandidate(
                    source_platform="pexels",
                    media_type="video",
                    download_url="https://example.com/video-2.mp4",
                    source_url="https://example.com/video-2",
                    source_asset_id="video-2",
                    description="Cargo loading at a port",
                    query="ports shipping",
                    ranking_score=4.9,
                ),
                AssetCandidate(
                    source_platform="pixabay",
                    media_type="image",
                    download_url="https://example.com/image-1.jpg",
                    source_url="https://example.com/image-1",
                    source_asset_id="image-1",
                    description="Port cranes at sunset",
                    query="ports shipping",
                    ranking_score=4.0,
                ),
            ]

            with mock.patch.object(pipeline, "_rank_scene_candidates", return_value=ranked_candidates):
                with mock.patch.object(
                    pipeline,
                    "_prefetch_shot_candidate_preview",
                    side_effect=[
                        ("/tmp/video-1.mp4", True, None),
                        ("/tmp/image-1.jpg", True, None),
                    ],
                ):
                    outputs = pipeline.prepare_shot_candidates("scene-1_shot_01")

            payload = json.loads(Path(outputs["shot_candidate_manifest"]).read_text(encoding="utf-8"))
            self.assertEqual([item["source_asset_id"] for item in payload["video_candidates"]], ["video-1"])
            self.assertEqual([item["source_asset_id"] for item in payload["image_candidates"]], ["image-1"])
            self.assertEqual(len(payload["candidates"]), 2)

    def test_prepare_shot_candidates_excludes_current_and_other_active_shot_assets(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            pipeline = VideoPipeline(
                PipelineConfig(
                    prompt="Ports",
                    project_dir=Path(tmpdir),
                    content_mode="explainer",
                    script_engine="template",
                    asset_shortlist_size=6,
                )
            )
            pipeline._prepare_dirs()
            shot_plan = ShotPlan(
                title="Ports",
                summary="Why ports matter",
                shots=[
                    PlannedShot(
                        shot_id="scene-1_shot_01",
                        scene_id="scene-1",
                        clip_name="intro",
                        heading="Ports matter",
                        shot_index=1,
                        total_shots=1,
                        narration_text="Ports move goods.",
                        seconds=6.0,
                        narration_start=0.0,
                        narration_end=6.0,
                        shot_objective="Ports move goods.",
                        key_info="Ports move goods.",
                        required_entities=["Ports"],
                        search_queries=["ports shipping"],
                        fallback_strategy="stock -> still-image -> internal-card -> placeholder",
                        visual_type="stock-video",
                        match_confidence="medium",
                        fallback_level="exact",
                    )
                ],
            )
            pipeline._write_json(pipeline.paths["shot_plan"], shot_plan.to_dict())
            pipeline._write_json(
                pipeline.paths["clip_catalog"],
                {
                    "clips": [
                        {
                            "shot_id": "scene-1_shot_01",
                            "candidates": [
                                {
                                    "selected": True,
                                    "source_platform": "pexels",
                                    "media_type": "video",
                                    "download_url": "https://example.com/video-current.mp4",
                                    "source_url": "https://example.com/video-current",
                                    "source_asset_id": "video-current",
                                }
                            ],
                        },
                        {
                            "shot_id": "scene-2_shot_01",
                            "candidates": [
                                {
                                    "selected": True,
                                    "source_platform": "pexels",
                                    "media_type": "video",
                                    "download_url": "https://example.com/video-other.mp4",
                                    "source_url": "https://example.com/video-other",
                                    "source_asset_id": "video-other",
                                }
                            ],
                        },
                    ]
                },
            )

            ranked_candidates = [
                AssetCandidate(
                    source_platform="pexels",
                    media_type="video",
                    download_url="https://example.com/video-current.mp4",
                    source_url="https://example.com/video-current",
                    source_asset_id="video-current",
                    description="Current shot clip",
                    query="ports shipping",
                    ranking_score=5.0,
                ),
                AssetCandidate(
                    source_platform="pexels",
                    media_type="video",
                    download_url="https://example.com/video-other.mp4",
                    source_url="https://example.com/video-other",
                    source_asset_id="video-other",
                    description="Other shot clip",
                    query="ports shipping",
                    ranking_score=4.9,
                ),
                AssetCandidate(
                    source_platform="pexels",
                    media_type="video",
                    download_url="https://example.com/video-fresh.mp4",
                    source_url="https://example.com/video-fresh",
                    source_asset_id="video-fresh",
                    description="Fresh clip",
                    query="ports shipping",
                    ranking_score=4.8,
                ),
                AssetCandidate(
                    source_platform="pixabay",
                    media_type="image",
                    download_url="https://example.com/image-fresh.jpg",
                    source_url="https://example.com/image-fresh",
                    source_asset_id="image-fresh",
                    description="Fresh image",
                    query="ports shipping",
                    ranking_score=4.2,
                ),
            ]

            with mock.patch.object(pipeline, "_rank_scene_candidates", return_value=ranked_candidates):
                with mock.patch.object(
                    pipeline,
                    "_prefetch_shot_candidate_preview",
                    side_effect=lambda shot_id, shortlist_index, candidate: (
                        f"/tmp/{candidate.source_asset_id}.{ 'mp4' if candidate.media_type == 'video' else 'jpg'}",
                        True,
                        None,
                    ),
                ):
                    outputs = pipeline.prepare_shot_candidates("scene-1_shot_01")

            payload = json.loads(Path(outputs["shot_candidate_manifest"]).read_text(encoding="utf-8"))
            self.assertEqual([item["source_asset_id"] for item in payload["video_candidates"]], ["video-fresh"])
            self.assertEqual([item["source_asset_id"] for item in payload["image_candidates"]], ["image-fresh"])

    def test_regenerate_shot_preserves_existing_asset_when_non_news_falls_back_to_internal_card(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            pipeline = VideoPipeline(
                PipelineConfig(
                    prompt="Ports",
                    project_dir=Path(tmpdir),
                    content_mode="explainer",
                    script_engine="template",
                )
            )
            pipeline._prepare_dirs()
            existing_asset = Path(tmpdir) / "existing.mp4"
            existing_asset.write_bytes(b"existing-video")
            shot_plan = ShotPlan(
                title="Ports",
                summary="Why ports matter",
                shots=[
                    PlannedShot(
                        shot_id="scene-1_shot_01",
                        scene_id="scene-1",
                        clip_name="intro",
                        heading="Ports matter",
                        shot_index=1,
                        total_shots=1,
                        narration_text="Ports move goods.",
                        seconds=6.0,
                        narration_start=0.0,
                        narration_end=6.0,
                        shot_objective="Ports move goods.",
                        key_info="Ports move goods.",
                        required_entities=["Ports"],
                        search_queries=["ports shipping"],
                        fallback_strategy="stock -> still-image -> internal-card -> placeholder",
                        visual_type="stock-video",
                        match_confidence="medium",
                        fallback_level="exact",
                        asset_path=str(existing_asset),
                        asset_provider="pexels",
                    )
                ],
            )
            pipeline._write_json(pipeline.paths["shot_plan"], shot_plan.to_dict())

            existing_right = AssetRight(
                scene_id="scene-1_shot_01",
                source_platform="pexels",
                source_asset_id="video-1",
                source_url="https://example.com/video-1",
                creator_name=None,
                creator_profile_url=None,
                license_name="License",
                license_url=None,
                downloaded_at="2026-03-16T00:00:00+00:00",
                local_path=str(existing_asset),
                sha256="abc123",
                media_type="video",
                width=1280,
                height=720,
                duration_seconds=6.0,
            )
            approved_plan = ScriptPlan(
                title="Ports",
                summary="Why ports matter",
                scenes=[
                    Scene(
                        scene_id="scene-1",
                        clip_name="intro",
                        heading="Ports matter",
                        voiceover="Ports move goods.",
                        search_terms=["ports shipping"],
                        seconds=6.0,
                    )
                ],
            )

            def fake_resolve(single_plan: ShotPlan, preferred_candidates=None, persist_state=False):
                single_plan.shots[0].asset_path = str(Path(tmpdir) / "internal-card.png")
                single_plan.shots[0].asset_provider = "internal-shot-card"
                single_plan.shots[0].fallback_level = "internal-card"
                return [
                    AssetRight(
                        scene_id="scene-1_shot_01",
                        source_platform="internal-shot-card",
                        source_asset_id="scene-1_shot_01",
                        source_url="internal://shot-card",
                        creator_name=None,
                        creator_profile_url=None,
                        license_name="Internal generated card",
                        license_url=None,
                        downloaded_at="2026-03-16T00:00:00+00:00",
                        local_path=str(Path(tmpdir) / "internal-card.png"),
                        sha256="def456",
                        media_type="image",
                        width=1280,
                        height=720,
                        duration_seconds=None,
                    )
                ]

            with mock.patch.object(pipeline, "_load_existing_rights", return_value=[existing_right]):
                with mock.patch.object(pipeline, "_resolve_shot_assets", side_effect=fake_resolve):
                    with mock.patch.object(pipeline, "_ensure_shot_previews", return_value=None):
                        with mock.patch.object(pipeline, "_load_preferred_script_plan", return_value=approved_plan):
                            with mock.patch.object(pipeline, "_prepare_bookend_backgrounds", return_value=None):
                                with mock.patch.object(pipeline, "_write_manifest_and_publish_artifacts", return_value=None):
                                    with mock.patch.object(pipeline, "_ensure_timeline", return_value=[]):
                                        pipeline.regenerate_shot("scene-1_shot_01", key_info="Updated port logistics")

            refreshed_payload = json.loads(pipeline.paths["shot_plan"].read_text(encoding="utf-8"))
            refreshed_shot = refreshed_payload["shots"][0]
            self.assertEqual(refreshed_shot["asset_path"], str(existing_asset))
            self.assertEqual(refreshed_shot["asset_provider"], "pexels")
            self.assertEqual(refreshed_shot["fallback_level"], "exact")

    def test_tui_runtime_elapsed_formatting(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            config = TuiConfig(
                prompt="Ports",
                content_mode="explainer",
                asset_keywords=[],
                news_feed_urls=[],
                project_dir=Path(tmpdir),
                minutes=3,
                resolution="1280x720",
                burn_subtitles=True,
                subtitle_preset="regular",
                subtitle_position="bottom",
                subtitle_accent_color="sunflower",
                subtitle_box_color="sunflower",
                fast_mode=False,
                script_tone="conversational",
                target_audience="general audience",
                hook_style="surprising-fact",
                narrative_mode="explainer",
                example_density="balanced",
                asset_mode="prefer-video",
                image_motion_style="slow",
                tts_engine="melo",
                piper_voice_id="",
                piper_speaker_id=None,
                voice_profile="balanced",
                voice_speed=1.0,
                melo_language="EN",
                melo_speaker="EN-US",
                kokoro_lang_code="en-us",
                kokoro_voice="af_heart",
            )

            with mock.patch.object(LocalVideoMvpTui, "_load_persisted_settings", autospec=True, return_value=None):
                app = LocalVideoMvpTui(config)
            try:
                self.assertEqual(app._format_runtime_elapsed(12.4), "12.4s elapsed")
                self.assertEqual(app._format_runtime_elapsed(126.8), "02:06 elapsed")
                self.assertEqual(app._format_runtime_elapsed(3723.4), "01:02:03 elapsed")
            finally:
                app._shutdown()

    def test_shot_review_play_returns_to_same_shot_action_modal(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            config = TuiConfig(
                prompt="Ports",
                content_mode="explainer",
                asset_keywords=[],
                news_feed_urls=[],
                project_dir=Path(tmpdir),
                minutes=3,
                resolution="1280x720",
                burn_subtitles=True,
                subtitle_preset="regular",
                subtitle_position="bottom",
                subtitle_accent_color="sunflower",
                subtitle_box_color="sunflower",
                fast_mode=False,
                script_tone="conversational",
                target_audience="general audience",
                hook_style="surprising-fact",
                narrative_mode="explainer",
                example_density="balanced",
                asset_mode="prefer-video",
                image_motion_style="slow",
                tts_engine="melo",
                piper_voice_id="",
                piper_speaker_id=None,
                voice_profile="balanced",
                voice_speed=1.0,
                melo_language="EN",
                melo_speaker="EN-US",
                kokoro_lang_code="en-us",
                kokoro_voice="af_heart",
            )
            preview_path = Path(tmpdir) / "preview.mp4"
            preview_path.write_bytes(b"preview")
            shot_entry = {
                "shot_id": "scene-1_shot_01",
                "scene_id": "scene-1",
                "clip_name": "intro",
                "heading": "Ports matter",
                "shot_index": 1,
                "total_shots": 1,
                "shot_objective": "Ports move goods.",
                "key_info": "Ports move goods.",
                "narration_text": "Ports move goods.",
                "required_entities": ["Ports"],
                "match_confidence": "low",
                "fallback_level": "exact",
                "asset_candidates": [],
                "preview_path": str(preview_path),
            }

            with mock.patch.object(LocalVideoMvpTui, "_load_persisted_settings", autospec=True, return_value=None):
                app = LocalVideoMvpTui(config)
            try:
                with mock.patch.object(app, "_load_shot_review_entries", return_value=[shot_entry]):
                    with mock.patch.object(
                        app,
                        "_load_shot_review_state",
                        return_value={"scene-1_shot_01": {"blocked": True, "approved": False}},
                    ):
                        with mock.patch.object(app, "_play_media_path", return_value=True) as play_mock:
                            with mock.patch.object(app, "_save_shot_review_state", return_value=None):
                                with mock.patch.object(
                                    app,
                                    "_select_from_list",
                                    side_effect=[
                                        "01. Ports matter | shot 1/1",
                                        "Play",
                                        None,
                                        None,
                                    ],
                                ) as select_mock:
                                    outcome = app._run_shot_review_prompt(Path(tmpdir))

                self.assertEqual(outcome, "cancel")
                self.assertEqual(play_mock.call_count, 1)
                shot_modal_calls = [
                    call
                    for call in select_mock.call_args_list
                    if call.kwargs.get("label") == "Shot scene-1_shot_01"
                ]
                self.assertEqual(len(shot_modal_calls), 2)
                self.assertEqual(
                    shot_modal_calls[0].kwargs.get("options"),
                    ["Play", "Approve", "Regenerate", "Try still image", "Refine keywords"],
                )
            finally:
                app._shutdown()

    def test_shot_review_reapprove_exits_shot_modal(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            config = TuiConfig(
                prompt="Ports",
                content_mode="explainer",
                asset_keywords=[],
                news_feed_urls=[],
                project_dir=Path(tmpdir),
                minutes=3,
                resolution="1280x720",
                burn_subtitles=True,
                subtitle_preset="regular",
                subtitle_position="bottom",
                subtitle_accent_color="sunflower",
                subtitle_box_color="sunflower",
                fast_mode=False,
                script_tone="conversational",
                target_audience="general audience",
                hook_style="surprising-fact",
                narrative_mode="explainer",
                example_density="balanced",
                asset_mode="prefer-video",
                image_motion_style="slow",
                tts_engine="melo",
                piper_voice_id="",
                piper_speaker_id=None,
                voice_profile="balanced",
                voice_speed=1.0,
                melo_language="EN",
                melo_speaker="EN-US",
                kokoro_lang_code="en-us",
                kokoro_voice="af_heart",
            )
            shot_entry = {
                "shot_id": "scene-1_shot_01",
                "scene_id": "scene-1",
                "clip_name": "intro",
                "heading": "Ports matter",
                "shot_index": 1,
                "total_shots": 1,
                "shot_objective": "Ports move goods.",
                "key_info": "Ports move goods.",
                "narration_text": "Ports move goods.",
                "required_entities": ["Ports"],
                "match_confidence": "low",
                "fallback_level": "exact",
                "asset_candidates": [],
                "preview_path": str(Path(tmpdir) / "preview.mp4"),
            }

            with mock.patch.object(LocalVideoMvpTui, "_load_persisted_settings", autospec=True, return_value=None):
                app = LocalVideoMvpTui(config)
            try:
                with mock.patch.object(app, "_load_shot_review_entries", return_value=[shot_entry]):
                    with mock.patch.object(
                        app,
                        "_load_shot_review_state",
                        return_value={"scene-1_shot_01": {"blocked": True, "approved": True}},
                    ):
                        with mock.patch.object(app, "_save_shot_review_state", return_value=None):
                            with mock.patch.object(
                                app,
                                "_select_from_list",
                                side_effect=[
                                    "01. Ports matter | shot 1/1",
                                    "Approve",
                                    None,
                                ],
                            ) as select_mock:
                                outcome = app._run_shot_review_prompt(Path(tmpdir))

                self.assertEqual(outcome, "cancel")
                shot_modal_calls = [
                    call
                    for call in select_mock.call_args_list
                    if call.kwargs.get("label") == "Shot scene-1_shot_01"
                ]
                self.assertEqual(len(shot_modal_calls), 1)
            finally:
                app._shutdown()

    def test_shot_review_keeps_same_selected_shot_after_regenerate(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            config = TuiConfig(
                prompt="Ports",
                content_mode="explainer",
                asset_keywords=[],
                news_feed_urls=[],
                project_dir=Path(tmpdir),
                minutes=3,
                resolution="1280x720",
                burn_subtitles=True,
                subtitle_preset="regular",
                subtitle_position="bottom",
                subtitle_accent_color="sunflower",
                subtitle_box_color="sunflower",
                fast_mode=False,
                script_tone="conversational",
                target_audience="general audience",
                hook_style="surprising-fact",
                narrative_mode="explainer",
                example_density="balanced",
                asset_mode="prefer-video",
                image_motion_style="slow",
                tts_engine="melo",
                piper_voice_id="",
                piper_speaker_id=None,
                voice_profile="balanced",
                voice_speed=1.0,
                melo_language="EN",
                melo_speaker="EN-US",
                kokoro_lang_code="en-us",
                kokoro_voice="af_heart",
            )
            shot_entries = [
                {
                    "shot_id": "scene-1_shot_01",
                    "scene_id": "scene-1",
                    "clip_name": "intro",
                    "heading": "Ports matter",
                    "shot_index": 1,
                    "total_shots": 1,
                    "shot_objective": "Ports move goods.",
                    "key_info": "Ports move goods.",
                    "narration_text": "Ports move goods.",
                    "required_entities": ["Ports"],
                    "match_confidence": "low",
                    "fallback_level": "exact",
                    "asset_candidates": [],
                    "preview_path": str(Path(tmpdir) / "preview-1.mp4"),
                },
                {
                    "shot_id": "scene-2_shot_01",
                    "scene_id": "scene-2",
                    "clip_name": "mid",
                    "heading": "Cargo moves",
                    "shot_index": 1,
                    "total_shots": 1,
                    "shot_objective": "Cargo moves fast.",
                    "key_info": "Cargo moves fast.",
                    "narration_text": "Cargo moves fast.",
                    "required_entities": ["Cargo"],
                    "match_confidence": "low",
                    "fallback_level": "exact",
                    "asset_candidates": [],
                    "preview_path": str(Path(tmpdir) / "preview-2.mp4"),
                },
            ]
            initial_state = {
                "scene-1_shot_01": {"blocked": True, "approved": False},
                "scene-2_shot_01": {"blocked": True, "approved": False},
            }
            regenerated_state = {
                "scene-1_shot_01": {"blocked": True, "approved": False},
                "scene-2_shot_01": {
                    "blocked": True,
                    "approved": False,
                    "regenerate_cycle": {"regenerated": True, "phase": "video", "video_tried": [1], "image_tried": []},
                },
            }

            with mock.patch.object(LocalVideoMvpTui, "_load_persisted_settings", autospec=True, return_value=None):
                app = LocalVideoMvpTui(config)
            try:
                with mock.patch.object(app, "_load_shot_review_entries", return_value=shot_entries):
                    with mock.patch.object(
                        app,
                        "_load_shot_review_state",
                        side_effect=[initial_state, regenerated_state],
                    ):
                        with mock.patch.object(app, "_save_shot_review_state", return_value=None):
                            with mock.patch.object(app, "_regenerate_shot_entry", return_value=True):
                                with mock.patch.object(
                                    app,
                                    "_select_from_list",
                                    side_effect=[
                                        "02. Cargo moves | shot 1/1",
                                        "Regenerate",
                                        None,
                                        None,
                                    ],
                                ) as select_mock:
                                    outcome = app._run_shot_review_prompt(Path(tmpdir))

                self.assertEqual(outcome, "cancel")
                shot_review_calls = [
                    call
                    for call in select_mock.call_args_list
                    if call.kwargs.get("label", "").startswith("Shot Review")
                ]
                self.assertEqual(len(shot_review_calls), 2)
                self.assertEqual(shot_review_calls[1].kwargs.get("current_value"), "02. [R] Cargo moves | shot 1/1")
            finally:
                app._shutdown()

    def test_next_shot_regenerate_candidate_stays_with_requested_media_type(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            config = TuiConfig(
                prompt="Ports",
                content_mode="explainer",
                asset_keywords=[],
                news_feed_urls=[],
                project_dir=Path(tmpdir),
                minutes=3,
                resolution="1280x720",
                burn_subtitles=True,
                subtitle_preset="regular",
                subtitle_position="bottom",
                subtitle_accent_color="sunflower",
                subtitle_box_color="sunflower",
                fast_mode=False,
                script_tone="conversational",
                target_audience="general audience",
                hook_style="surprising-fact",
                narrative_mode="explainer",
                example_density="balanced",
                asset_mode="prefer-video",
                image_motion_style="slow",
                tts_engine="melo",
                piper_voice_id="",
                piper_speaker_id=None,
                voice_profile="balanced",
                voice_speed=1.0,
                melo_language="EN",
                melo_speaker="EN-US",
                kokoro_lang_code="en-us",
                kokoro_voice="af_heart",
            )

            manifest = {
                "video_candidates": [
                    {"shortlist_index": 0, "selected": True, "media_type": "video", "source_asset_id": "video-0"},
                    {"shortlist_index": 1, "selected": False, "media_type": "video", "source_asset_id": "video-1"},
                    {"shortlist_index": 2, "selected": False, "media_type": "video", "source_asset_id": "video-2"},
                ],
                "image_candidates": [
                    {"shortlist_index": 3, "selected": False, "media_type": "image", "source_asset_id": "image-1"},
                    {"shortlist_index": 4, "selected": False, "media_type": "image", "source_asset_id": "image-2"},
                ],
            }

            with mock.patch.object(LocalVideoMvpTui, "_load_persisted_settings", autospec=True, return_value=None):
                app = LocalVideoMvpTui(config)
            try:
                cycle = app._fresh_regenerate_cycle([])
                first = app._next_shot_regenerate_candidate(manifest, cycle, media_type="video")
                self.assertEqual(first["source_asset_id"], "video-1")

                cycle["video_tried"] = [1, 2]
                second = app._next_shot_regenerate_candidate(manifest, cycle, media_type="video")
                self.assertIsNone(second)
                self.assertEqual(cycle["phase"], "video")
                self.assertTrue(cycle["exhausted"])

                cycle = app._fresh_regenerate_cycle([])
                image = app._next_shot_regenerate_candidate(manifest, cycle, media_type="image")
                self.assertEqual(image["source_asset_id"], "image-1")
                self.assertEqual(cycle["phase"], "image")
            finally:
                app._shutdown()

    def test_regenerate_shot_entry_uses_next_candidate_without_opening_editor(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            config = TuiConfig(
                prompt="Ports",
                content_mode="explainer",
                asset_keywords=[],
                news_feed_urls=[],
                project_dir=Path(tmpdir),
                minutes=3,
                resolution="1280x720",
                burn_subtitles=True,
                subtitle_preset="regular",
                subtitle_position="bottom",
                subtitle_accent_color="sunflower",
                subtitle_box_color="sunflower",
                fast_mode=False,
                script_tone="conversational",
                target_audience="general audience",
                hook_style="surprising-fact",
                narrative_mode="explainer",
                example_density="balanced",
                asset_mode="prefer-video",
                image_motion_style="slow",
                tts_engine="melo",
                piper_voice_id="",
                piper_speaker_id=None,
                voice_profile="balanced",
                voice_speed=1.0,
                melo_language="EN",
                melo_speaker="EN-US",
                kokoro_lang_code="en-us",
                kokoro_voice="af_heart",
            )
            entry = {
                "shot_id": "scene-1_shot_01",
                "scene_id": "scene-1",
                "clip_name": "intro",
                "heading": "Ports matter",
                "shot_index": 1,
                "total_shots": 1,
                "shot_objective": "Ports move goods.",
                "key_info": "Ports move goods.",
                "narration_text": "Ports move goods.",
                "required_entities": ["Ports"],
                "search_queries": ["ports shipping"],
                "match_confidence": "low",
                "fallback_level": "exact",
                "asset_candidates": [],
                "preview_path": str(Path(tmpdir) / "preview.mp4"),
            }
            manifest = {
                "key_info": "Ports move goods.",
                "search_queries": ["ports shipping"],
                "editorial_locked": False,
                "video_candidates": [
                    {
                        "shortlist_index": 1,
                        "selected": False,
                        "media_type": "video",
                        "source_asset_id": "video-1",
                    }
                ],
                "image_candidates": [],
            }

            with mock.patch.object(LocalVideoMvpTui, "_load_persisted_settings", autospec=True, return_value=None):
                app = LocalVideoMvpTui(config)
            try:
                with mock.patch.object(
                    app,
                    "_load_shot_review_state",
                    return_value={"scene-1_shot_01": {"blocked": True, "approved": False}},
                ):
                    with mock.patch.object(app, "_save_shot_review_state", return_value=None):
                        with mock.patch.object(app, "_load_shot_candidate_manifest", return_value=manifest):
                            with mock.patch.object(app, "_run_command_with_spinner", return_value=0) as run_mock:
                                with mock.patch.object(app, "_play_regenerated_shot_preview", return_value=True):
                                    with mock.patch.object(
                                        app,
                                        "_prompt_multiline_input",
                                        side_effect=AssertionError("unexpected editor"),
                                    ):
                                        regenerated = app._regenerate_shot_entry(Path(tmpdir), entry, mode="video")

                self.assertTrue(regenerated)
                command = run_mock.call_args.args[0]
                self.assertIn("--candidate-index", command)
                self.assertIn("1", command)
            finally:
                app._shutdown()

    def test_tui_running_state_hides_percent_and_eta(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            config = TuiConfig(
                prompt="Ports",
                content_mode="explainer",
                asset_keywords=[],
                news_feed_urls=[],
                project_dir=Path(tmpdir),
                minutes=3,
                resolution="1280x720",
                burn_subtitles=True,
                subtitle_preset="regular",
                subtitle_position="bottom",
                subtitle_accent_color="sunflower",
                subtitle_box_color="sunflower",
                fast_mode=False,
                script_tone="conversational",
                target_audience="general audience",
                hook_style="surprising-fact",
                narrative_mode="explainer",
                example_density="balanced",
                asset_mode="prefer-video",
                image_motion_style="slow",
                tts_engine="melo",
                piper_voice_id="",
                piper_speaker_id=None,
                voice_profile="balanced",
                voice_speed=1.0,
                melo_language="EN",
                melo_speaker="EN-US",
                kokoro_lang_code="en-us",
                kokoro_voice="af_heart",
            )

            with mock.patch.object(LocalVideoMvpTui, "_load_persisted_settings", autospec=True, return_value=None):
                app = LocalVideoMvpTui(config)
            try:
                with mock.patch.object(app, "_is_running", return_value=True):
                    with mock.patch.object(app, "_elapsed_seconds", return_value=126.8):
                        with mock.patch.object(app, "_spinner_frame", return_value="|"):
                            state_text, _state_attr = app._state_display()
                self.assertEqual(state_text, "| RUNNING  02:06 elapsed")
                self.assertNotIn("%", state_text)
                self.assertNotIn("ETA", state_text)
            finally:
                app._shutdown()

    def test_tui_multiline_help_text_prefers_ctrl_enter(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            config = TuiConfig(
                prompt="Ports",
                content_mode="explainer",
                asset_keywords=[],
                news_feed_urls=[],
                project_dir=Path(tmpdir),
                minutes=3,
                resolution="1280x720",
                burn_subtitles=True,
                subtitle_preset="regular",
                subtitle_position="bottom",
                subtitle_accent_color="sunflower",
                subtitle_box_color="sunflower",
                fast_mode=False,
                script_tone="conversational",
                target_audience="general audience",
                hook_style="surprising-fact",
                narrative_mode="explainer",
                example_density="balanced",
                asset_mode="prefer-video",
                image_motion_style="slow",
                tts_engine="melo",
                piper_voice_id="",
                piper_speaker_id=None,
                voice_profile="balanced",
                voice_speed=1.0,
                melo_language="EN",
                melo_speaker="EN-US",
                kokoro_lang_code="en-us",
                kokoro_voice="af_heart",
            )

            with mock.patch.object(LocalVideoMvpTui, "_load_persisted_settings", autospec=True, return_value=None):
                app = LocalVideoMvpTui(config)
            try:
                self.assertEqual(app._multiline_submit_help_text(), "Ctrl-Enter submit | Esc back")
            finally:
                app._shutdown()

    def test_preview_ready_checkpoint_sets_status_without_auto_open(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            config = TuiConfig(
                prompt="Ports",
                content_mode="explainer",
                asset_keywords=[],
                news_feed_urls=[],
                project_dir=Path(tmpdir),
                minutes=3,
                resolution="1280x720",
                burn_subtitles=True,
                subtitle_preset="regular",
                subtitle_position="bottom",
                subtitle_accent_color="sunflower",
                subtitle_box_color="sunflower",
                fast_mode=False,
                script_tone="conversational",
                target_audience="general audience",
                hook_style="surprising-fact",
                narrative_mode="explainer",
                example_density="balanced",
                asset_mode="prefer-video",
                image_motion_style="slow",
                tts_engine="melo",
                piper_voice_id="",
                piper_speaker_id=None,
                voice_profile="balanced",
                voice_speed=1.0,
                melo_language="EN",
                melo_speaker="EN-US",
                kokoro_lang_code="en-us",
                kokoro_voice="af_heart",
            )

            with mock.patch.object(LocalVideoMvpTui, "_load_persisted_settings", autospec=True, return_value=None):
                app = LocalVideoMvpTui(config)
            try:
                project_dir = Path(tmpdir)
                preview_path = project_dir / "review" / "preview.mp4"
                preview_path.parent.mkdir(parents=True, exist_ok=True)
                preview_path.write_bytes(b"preview")
                app._active_project_dir = project_dir
                app._pending_stage_transition_prompt = {
                    "next_stage": "finalize",
                    "title": "Preview Ready",
                    "body": "Preview is ready.",
                }

                with mock.patch.object(app, "_is_running", return_value=False):
                    with mock.patch.object(app, "_start_run_workflow", return_value=None) as start_mock:
                        with mock.patch.object(app, "_prompt_yes_no", side_effect=AssertionError("unexpected prompt")):
                            app._maybe_prompt_stage_transition()

                start_mock.assert_not_called()
                self.assertEqual(app._hitl_stage, "finalize")
                self.assertEqual(
                    app._get_status(),
                    f"Preview ready: {preview_path}. Press R to finalize when ready.",
                )
            finally:
                app._shutdown()

    def test_open_script_in_external_editor_uses_configured_command(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            config = TuiConfig(
                prompt="Ports",
                content_mode="explainer",
                asset_keywords=[],
                news_feed_urls=[],
                project_dir=Path(tmpdir),
                minutes=3,
                resolution="1280x720",
                burn_subtitles=True,
                subtitle_preset="regular",
                subtitle_position="bottom",
                subtitle_accent_color="sunflower",
                subtitle_box_color="sunflower",
                fast_mode=False,
                script_tone="conversational",
                target_audience="general audience",
                hook_style="surprising-fact",
                narrative_mode="explainer",
                example_density="balanced",
                asset_mode="prefer-video",
                image_motion_style="slow",
                tts_engine="melo",
                piper_voice_id="",
                piper_speaker_id=None,
                voice_profile="balanced",
                voice_speed=1.0,
                melo_language="EN",
                melo_speaker="EN-US",
                kokoro_lang_code="en-us",
                kokoro_voice="af_heart",
                external_editor_command="code --wait {file}",
            )

            with mock.patch.object(LocalVideoMvpTui, "_load_persisted_settings", autospec=True, return_value=None):
                app = LocalVideoMvpTui(config)
            try:
                project_dir = Path(tmpdir)
                script_path = project_dir / "script.json"
                script_path.write_text(json.dumps({"scenes": []}), encoding="utf-8")
                with mock.patch("local_video_mvp.tui.subprocess.run") as run_mock:
                    run_mock.return_value = mock.Mock(returncode=0)
                    opened = app._open_script_in_external_editor(project_dir)

                self.assertTrue(opened)
                self.assertEqual(run_mock.call_count, 1)
                command_text = run_mock.call_args.args[0]
                self.assertIn("code --wait", command_text)
                self.assertIn(str(script_path), command_text)
            finally:
                app._shutdown()

    def test_tui_preflight_omits_voice_steps(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            config = TuiConfig(
                prompt="Ports",
                content_mode="explainer",
                asset_keywords=[],
                news_feed_urls=[],
                project_dir=Path(tmpdir),
                minutes=3,
                resolution="1280x720",
                burn_subtitles=True,
                subtitle_preset="regular",
                subtitle_position="bottom",
                subtitle_accent_color="sunflower",
                subtitle_box_color="sunflower",
                fast_mode=False,
                script_tone="conversational",
                target_audience="general audience",
                hook_style="surprising-fact",
                narrative_mode="explainer",
                example_density="balanced",
                asset_mode="prefer-video",
                image_motion_style="slow",
                tts_engine="melo",
                piper_voice_id="",
                piper_speaker_id=None,
                voice_profile="balanced",
                voice_speed=1.0,
                melo_language="EN",
                melo_speaker="EN-US",
                kokoro_lang_code="en-us",
                kokoro_voice="af_heart",
            )

            with mock.patch.object(LocalVideoMvpTui, "_load_persisted_settings", autospec=True, return_value=None):
                app = LocalVideoMvpTui(config)
            try:
                seen_steps: list[str] = []
                with mock.patch.object(
                    app,
                    "_edit_named_config_field",
                    side_effect=lambda step, allow_escape=False: (seen_steps.append(step) or ConfigEditResult()),
                ):
                    with mock.patch.object(app, "_open_subtitle_settings_menu", return_value="closed"):
                        result = app._run_preflight()
                self.assertTrue(result)
                self.assertEqual(seen_steps, ["minutes", "resolution"])
            finally:
                app._shutdown()

    def test_tui_preflight_subtitles_finish_label_does_not_reference_voice(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            config = TuiConfig(
                prompt="Ports",
                content_mode="explainer",
                asset_keywords=[],
                news_feed_urls=[],
                project_dir=Path(tmpdir),
                minutes=3,
                resolution="1280x720",
                burn_subtitles=True,
                subtitle_preset="regular",
                subtitle_position="bottom",
                subtitle_accent_color="sunflower",
                subtitle_box_color="sunflower",
                fast_mode=False,
                script_tone="conversational",
                target_audience="general audience",
                hook_style="surprising-fact",
                narrative_mode="explainer",
                example_density="balanced",
                asset_mode="prefer-video",
                image_motion_style="slow",
                tts_engine="melo",
                piper_voice_id="",
                piper_speaker_id=None,
                voice_profile="balanced",
                voice_speed=1.0,
                melo_language="EN",
                melo_speaker="EN-US",
                kokoro_lang_code="en-us",
                kokoro_voice="af_heart",
            )

            with mock.patch.object(LocalVideoMvpTui, "_load_persisted_settings", autospec=True, return_value=None):
                app = LocalVideoMvpTui(config)
            try:
                with mock.patch.object(app, "_edit_named_config_field", return_value=ConfigEditResult()):
                    with mock.patch.object(app, "_open_subtitle_settings_menu", return_value="closed") as subtitle_menu:
                        result = app._run_preflight()
                self.assertTrue(result)
                subtitle_menu.assert_called_once()
                self.assertEqual(subtitle_menu.call_args.kwargs.get("continue_label"), "Finish preflight")
            finally:
                app._shutdown()


if __name__ == "__main__":
    unittest.main()
