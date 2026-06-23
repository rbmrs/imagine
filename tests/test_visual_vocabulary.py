from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from local_video_mvp.models import AssetCandidate, PipelineConfig, PlannedShot, Scene
from local_video_mvp.pipeline import VideoPipeline


class VisualVocabularyTests(unittest.TestCase):
    def _make_pipeline(self, project_dir: Path, **config_overrides: object) -> VideoPipeline:
        config_kwargs = {
            "prompt": "Mensagem de fe para hoje",
            "project_dir": project_dir,
            "script_engine": "template",
            "channel_profile": "refugio_da_fe",
            "asset_keywords": ["community", "hope"],
        }
        config_kwargs.update(config_overrides)
        config = PipelineConfig(
            **config_kwargs,
        )
        pipeline = VideoPipeline(config)
        pipeline._prepare_dirs()
        return pipeline

    def test_religious_scene_matches_curated_terms_with_accents(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            pipeline = self._make_pipeline(Path(tmpdir))
            scene = Scene(
                scene_id="scene_001",
                clip_name="scene-001",
                heading="Oração da manhã",
                voiceover="Hoje vamos falar sobre oração, fé, milagre e a paz de Jesus.",
                search_terms=["oração", "fé"],
                seconds=8.0,
            )

            context = pipeline._scene_query_context(scene)

            self.assertIn("prayer", context["matched_terms"])
            self.assertIn("faith", context["matched_terms"])
            self.assertTrue(context["effective_queries"])
            self.assertLessEqual(len(context["effective_queries"]), 5)
            self.assertIn(context["effective_queries"][0], {"jesus", "prayer"})

    def test_generic_scene_falls_back_without_channel_match(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            pipeline = self._make_pipeline(Path(tmpdir))
            scene = Scene(
                scene_id="scene_002",
                clip_name="scene-002",
                heading="Rotina diária",
                voiceover="Uma rotina tranquila pode mudar a forma como voce observa o dia.",
                search_terms=["rotina", "calma"],
                seconds=7.0,
            )

            context = pipeline._scene_query_context(scene)

            self.assertEqual(context["matched_terms"], [])
            self.assertTrue(context["effective_queries"])
            self.assertIn("rotina", " ".join(context["effective_queries"]).lower())

    def test_strict_query_override_keeps_manual_queries_primary(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            pipeline = self._make_pipeline(Path(tmpdir), asset_keywords=["church", "bible"])
            shot = PlannedShot(
                shot_id="scene_001_shot_01",
                scene_id="scene_001",
                clip_name="scene-001",
                heading="Esperança",
                shot_index=1,
                total_shots=1,
                narration_text="Uma palavra sobre esperança e oração.",
                seconds=6.0,
                narration_start=0.0,
                narration_end=6.0,
                shot_objective="Esperança e oração",
                key_info="Esperança e oração",
            )

            updated = pipeline._apply_key_info_to_shot(
                shot,
                "Esperança e oração",
                search_queries=["prayer hands", "open bible"],
                strict_query_override=True,
            )

            self.assertEqual(updated.search_queries, ["prayer hands", "open bible"])
            self.assertEqual(updated.effective_search_queries[0], "prayer hands")
            self.assertEqual(updated.effective_search_queries[1], "open bible")
            self.assertTrue(any("prayer" in query.lower() for query in updated.effective_search_queries[:4]))
            self.assertNotIn("church bible", updated.effective_search_queries)

    def test_religious_candidate_outranks_irrelevant_seasonal_candidate(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            pipeline = self._make_pipeline(Path(tmpdir))
            scene = Scene(
                scene_id="scene_003",
                clip_name="scene-003",
                heading="Momento de oração",
                voiceover="Uma cena de oração e esperança em comunidade.",
                search_terms=["oração", "comunidade"],
                seconds=8.0,
            )
            prayer_candidate = AssetCandidate(
                source_platform="pexels",
                media_type="video",
                download_url="https://example.com/prayer.mp4",
                source_url="https://example.com/prayer",
                description="People praying together in church with open hands",
                width=1920,
                height=1080,
                duration_seconds=14.0,
                query="prayer church",
            )
            seasonal_candidate = AssetCandidate(
                source_platform="pexels",
                media_type="video",
                download_url="https://example.com/christmas.mp4",
                source_url="https://example.com/christmas",
                description="Christmas tree decorations and santa holiday party",
                width=1920,
                height=1080,
                duration_seconds=14.0,
                query="christmas holiday",
            )

            prayer_quality = pipeline._candidate_quality_score(prayer_candidate, scene)
            seasonal_quality = pipeline._candidate_quality_score(seasonal_candidate, scene)
            prayer_rank = pipeline._candidate_ranking_score(
                prayer_candidate,
                scene,
                quality_score=prayer_quality,
                provider_rank=0,
                query_rank=0,
            )
            seasonal_rank = pipeline._candidate_ranking_score(
                seasonal_candidate,
                scene,
                quality_score=seasonal_quality,
                provider_rank=0,
                query_rank=0,
            )

            self.assertGreater(prayer_quality, seasonal_quality)
            self.assertGreater(prayer_rank, seasonal_rank)


if __name__ == "__main__":
    unittest.main()
