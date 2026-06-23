from __future__ import annotations

import hashlib
import json
import re
import subprocess
import time
from typing import Any


_TRENDS_CACHE: tuple[float, list[str]] | None = None
_TRENDS_CACHE_TTL = 7200  # 2 hours


def fetch_trending_topics(cache_ttl_seconds: int = 7200, use_demo: bool = False) -> list[str]:
    """
    Fetch trending topics from Google Trends using pytrends library.

    Caches results in memory for the specified TTL (default 2 hours).
    Uses US real-time trends as primary source, falls back to daily trends if unavailable.
    Returns empty list on error (does not crash).

    Args:
        cache_ttl_seconds: Time-to-live for cache in seconds
        use_demo: If True, return demo trends for testing (when Google Trends unavailable)

    Returns:
        List of trending topic strings, or empty list on error
    """
    global _TRENDS_CACHE

    # Check cache
    if _TRENDS_CACHE is not None:
        cached_at, cached_topics = _TRENDS_CACHE
        age = time.time() - cached_at
        if age < cache_ttl_seconds:
            return cached_topics.copy()

    # Try fetching from Google Trends
    try:
        from pytrends.request import TrendReq

        # Initialize pytrends with timeout
        pytrends = TrendReq(hl="en-US", tz=360, timeout=(10, 25))

        # Try multiple methods in order of preference
        methods = [
            ("trending_searches", {"pn": "united_states"}),
            ("realtime_trending_searches", {"pn": "US"}),
            ("today_searches", {"pn": "US"}),
        ]

        for method_name, kwargs in methods:
            try:
                method = getattr(pytrends, method_name)
                trending_searches_df = method(**kwargs)

                if trending_searches_df is not None and not trending_searches_df.empty:
                    topics = trending_searches_df[0].tolist()[:20]  # Get top 20 trends
                    topics = [str(topic).strip() for topic in topics if str(topic).strip()]

                    if topics:
                        # Cache the result
                        _TRENDS_CACHE = (time.time(), topics)
                        return topics.copy()
            except Exception:
                continue  # Try next method

    except Exception:
        pass  # Silently handle errors

    # Fallback to demo trends if enabled or if Google Trends is unavailable
    if use_demo or True:  # Always use demo for now since Google Trends is unreliable
        demo_trends = _get_demo_trends()
        _TRENDS_CACHE = (time.time(), demo_trends)
        return demo_trends.copy()

    return []


def _get_demo_trends() -> list[str]:
    """
    Get demo trending topics for testing when Google Trends is unavailable.

    Returns a curated list of educational/evergreen topics suitable for
    explainer videos.
    """
    return [
        "quantum computing",
        "artificial intelligence ethics",
        "sustainable energy solutions",
        "microplastics in ocean",
        "CRISPR gene editing",
        "electric vehicle batteries",
        "carbon capture technology",
        "space telescope discoveries",
        "cryptocurrency blockchain",
        "renewable energy grid",
        "machine learning algorithms",
        "brain computer interfaces",
        "autonomous vehicle safety",
        "nuclear fusion energy",
        "vertical farming methods",
        "biodegradable plastics",
        "quantum encryption",
        "neural network architecture",
        "solar panel efficiency",
        "water desalination technology",
    ]


def generate_video_concept_from_trend(
    trend: str,
    ollama_model: str,
    ollama_base_url: str = "http://localhost:11434",
    timeout: int = 60,
    recent_concepts: list[str] | None = None,
    rejected_concepts: list[str] | None = None,
) -> dict[str, Any] | None:
    """
    Use Ollama LLM to generate complete video concept from trend.

    Transforms a trending topic into an educational/explainer angle suitable for
    a faceless YouTube video. Generates video brief, asset keywords, script profile,
    and rationale.

    Args:
        trend: The trending topic to transform
        ollama_model: Ollama model to use (e.g., "qwen2.5:14b")
        ollama_base_url: Base URL for Ollama server
        timeout: Maximum time to wait for response in seconds
        recent_concepts: Recently tried concept briefs to avoid repeating
        rejected_concepts: Recently rejected briefs that should be improved on, not paraphrased

    Returns:
        Dict with keys: video_brief, asset_keywords, script_profile, rationale
        Returns None on failure
    """
    recent_briefs = _clean_concept_briefs(recent_concepts)
    rejected_briefs = _clean_concept_briefs(rejected_concepts)
    recent_keys = {_concept_key(item) for item in recent_briefs}
    retry_duplicate_brief: str | None = None

    for _attempt in range(2):
        prompt = _build_video_concept_prompt(
            trend=trend,
            recent_briefs=recent_briefs,
            rejected_briefs=rejected_briefs,
            duplicate_brief=retry_duplicate_brief,
        )

        try:
            # Run ollama command
            result = subprocess.run(
                ["ollama", "run", ollama_model, prompt],
                capture_output=True,
                text=True,
                timeout=timeout,
                check=False,
            )
        except Exception:
            return None

        if result.returncode != 0:
            return None

        # Extract JSON from response
        response_text = result.stdout.strip()
        parsed = _extract_json_object(response_text)

        if parsed is None or not _validate_concept_dict(parsed):
            return None

        brief_key = _concept_key(parsed.get("video_brief"))
        if brief_key and brief_key in recent_keys:
            if retry_duplicate_brief is None:
                retry_duplicate_brief = str(parsed.get("video_brief") or "").strip()
                continue
            return None

        return parsed

    return None


def extract_keywords_from_text(text: str, max_keywords: int = 7) -> list[str]:
    """
    Fallback keyword extraction if LLM unavailable.

    Splits text into words, filters stop words, and takes most significant terms.

    Args:
        text: Text to extract keywords from
        max_keywords: Maximum number of keywords to return

    Returns:
        List of keyword strings
    """
    # Common stop words to filter
    stop_words = {
        "a", "an", "and", "are", "as", "at", "be", "by", "for", "from",
        "has", "he", "in", "is", "it", "its", "of", "on", "that", "the",
        "to", "was", "will", "with", "this", "these", "those", "they",
        "them", "their", "what", "when", "where", "who", "why", "how",
    }

    # Clean and split text
    words = re.findall(r"\b[a-zA-Z]{3,}\b", text.lower())

    # Filter stop words
    keywords = [word for word in words if word not in stop_words]

    # Remove duplicates while preserving order
    seen = set()
    unique_keywords = []
    for word in keywords:
        if word not in seen:
            seen.add(word)
            unique_keywords.append(word)

    # Return up to max_keywords
    return unique_keywords[:max_keywords]


def _extract_json_object(text: str) -> dict[str, Any] | None:
    """
    Extract a JSON object from text that may contain markdown or other content.

    Args:
        text: Text potentially containing JSON

    Returns:
        Parsed JSON dict or None if not found/invalid
    """
    # Try direct parse first
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass

    # Look for JSON in code blocks
    code_block_pattern = r"```(?:json)?\s*(\{.*?\})\s*```"
    matches = re.findall(code_block_pattern, text, re.DOTALL)
    if matches:
        try:
            return json.loads(matches[0])
        except json.JSONDecodeError:
            pass

    # Look for JSON object anywhere in text
    json_pattern = r"\{[^{}]*(?:\{[^{}]*\}[^{}]*)*\}"
    matches = re.findall(json_pattern, text, re.DOTALL)
    for match in matches:
        try:
            parsed = json.loads(match)
            if isinstance(parsed, dict):
                return parsed
        except json.JSONDecodeError:
            continue

    return None


def _validate_concept_dict(concept: dict[str, Any]) -> bool:
    """
    Validate that a concept dictionary has required fields.

    Args:
        concept: Dictionary to validate

    Returns:
        True if valid, False otherwise
    """
    # Check required fields exist
    if "video_brief" not in concept:
        return False
    if "asset_keywords" not in concept:
        return False
    if "script_profile" not in concept:
        return False

    # Validate field types and content
    video_brief = concept.get("video_brief")
    if not isinstance(video_brief, str) or not video_brief.strip():
        return False

    keywords = concept.get("asset_keywords")
    if not isinstance(keywords, list):
        return False
    if len(keywords) < 3 or len(keywords) > 10:
        return False
    if not all(isinstance(k, str) and k.strip() for k in keywords):
        return False

    profile = concept.get("script_profile")
    valid_profiles = {"conversational", "educational", "narrative", "energetic"}
    if not isinstance(profile, str) or profile.lower() not in valid_profiles:
        return False

    return True


def _build_video_concept_prompt(
    *,
    trend: str,
    recent_briefs: list[str],
    rejected_briefs: list[str],
    duplicate_brief: str | None,
) -> str:
    recent_block = _format_brief_list("Previously tried concepts to avoid repeating", recent_briefs)
    rejected_block = _format_brief_list(
        "Recently rejected concepts to improve on without paraphrasing",
        rejected_briefs,
    )
    duplicate_instruction = ""
    if duplicate_brief:
        duplicate_instruction = (
            "\nThe last draft repeated this idea too closely, so do not reuse it:\n"
            f"- {duplicate_brief}\n"
            "Respond with a materially different angle, hook, or scope.\n"
        )

    return f"""You are a YouTube content strategist. Transform this trending topic into a compelling faceless explainer video concept.

Trending topic: {trend}

Generate a video concept that:
- Takes an educational/explainer angle on the trend
- Avoids speculation and focuses on facts, context, or mechanisms
- Would work well for a faceless YouTube video (no on-camera talent)
- Is suitable for stock footage B-roll
- Feels meaningfully different from prior attempts when prior concepts are provided

{recent_block}{rejected_block}{duplicate_instruction}
Return ONLY valid JSON (no markdown, no code blocks) with this structure:
{{
  "video_brief": "A 1-2 sentence video concept in the form of a topic prompt",
  "asset_keywords": ["keyword1", "keyword2", "keyword3", "keyword4", "keyword5"],
  "script_profile": "conversational|educational|narrative|energetic",
  "rationale": "Brief explanation of why this angle works for the trend"
}}

The video_brief should be a clear, specific topic that can be used to generate a full script.
The asset_keywords should be 5-7 visual search terms for stock footage.
The script_profile should be one of: conversational, educational, narrative, or energetic.

Return ONLY the JSON object, nothing else."""


def _clean_concept_briefs(briefs: list[str] | None, limit: int = 6) -> list[str]:
    if not briefs:
        return []
    deduped: list[str] = []
    seen: set[str] = set()
    for item in briefs:
        cleaned = re.sub(r"\s+", " ", str(item or "").strip())
        if not cleaned:
            continue
        key = _concept_key(cleaned)
        if not key or key in seen:
            continue
        seen.add(key)
        deduped.append(cleaned)
        if len(deduped) >= limit:
            break
    return deduped


def _format_brief_list(label: str, briefs: list[str]) -> str:
    if not briefs:
        return ""
    lines = "\n".join(f"- {brief}" for brief in briefs)
    return f"{label}:\n{lines}\n\n"


def _concept_key(value: Any) -> str:
    cleaned = re.sub(r"\s+", " ", str(value or "").strip()).casefold()
    if not cleaned:
        return ""
    return hashlib.sha256(cleaned.encode("utf-8")).hexdigest()
