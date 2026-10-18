"""
backend/app/pipeline/stage0_gatekeeper.py - Stage 0 Pre-Pipeline High-RPD Chatter & Noise Gatekeeper

Filters high-volume discussion, comment, and social streams BEFORE database persistence
and BEFORE the 4-stage AI pipeline using a hybrid 2-layer architecture:
  Layer 1: Zero-cost deterministic fast-pass (sirens, sensors, verified kinetic+theme matches)
           and fast-drop (deleted/removed comments, bot boilerplate, ultra-short noise, echoes).
  Layer 2: Micro-batched LLM triage (up to 12 messages per call) powered by high-RPD models
           (Google AI Studio Gemma 4 26B / 31B at 30 RPM & 14,400 RPD each, with Groq fallbacks).
"""

import logging
import re
from collections import deque
from datetime import datetime, timezone
from typing import Any, Callable, Deque, Dict, List, Optional, Set, Tuple, Union
from pydantic import BaseModel, Field, model_validator

from backend.app.pipeline.arabic_normalizer import (
    ALL_VERNACULAR_TRIGGERS,
    KINETIC_TRIGGERS,
    has_vernacular_signal,
    is_duplicate_echo,
    normalize_arabic_text,
)
from backend.app.pipeline.parser import extract_and_parse_json
from backend.app.pipeline.providers.base import BaseAIProvider
from backend.app.plugins.base import NormalizedEvent

logger = logging.getLogger("osint.pipeline.stage0")

# Boilerplate and off-topic patterns immediately dropped in Layer 1
BOT_AND_DELETED_PATTERNS: Tuple[str, ...] = (
    "[deleted]",
    "[removed]",
    "i am a bot, and this action was performed automatically",
    "this message was deleted",
    "automoderator",
    "remindme!",
    "!remindme",
)

CASUAL_NOISE_PATTERNS: Tuple[str, ...] = (
    "good morning everyone",
    "happy birthday",
    "where can i buy",
    "best restaurant in",
    "check out my youtube",
    "subscribe to my channel",
    "crypto giveaway",
    "airdrop claim",
    "free bitcoin",
    "onlyfans",
)

STAGE0_SYSTEM_PROMPT = """You are a high-speed OSINT Triage Gatekeeper filtering live regional discussion channels and citizen feeds.
Your job is to filter a numbered batch of incoming raw messages BEFORE they enter the intelligence database.

KEEP an item (include its integer index in "keep") if it reports, describes, or asks about ANY of the following:
1. First-hand sensory field observations: explosions heard ("loud boom", "انفجار", "صدای مهیب", "פיצוץ"), sonic booms, air-raid sirens, low-flying fighter jets, drones/UAVs buzzing overhead, air-defense interceptions, smoke plumes, tremors/shaking, power outages, or troop/convoy movements.
2. Military, naval, aviation, cyber, nuclear, sanctions, diplomatic, or geopolitical developments relevant to the active theme or theater.
3. Breaking news wires, tactical alerts, maritime incidents, or OSINT geolocation analysis.

DROP an item (exclude from "keep") if it is:
- Casual everyday conversation, jokes, memes, sports, food, dating, tourist questions, or personal small talk.
- Pure profanity/insults with zero factual or situational content.
- Spam, advertisements, crypto promotions, or moderator bot boilerplate.
- Completely unrelated domestic news from outside the monitored geopolitical theater.

Respond STRICTLY with valid JSON in this exact format:
{"keep": [0, 2, 5]}
Do not include markdown formatting or explanations."""


class Stage0BatchResult(BaseModel):
    """Schema for micro-batched LLM gatekeeper response."""
    keep: List[int] = Field(default_factory=list, description="0-based indices of relevant messages to keep")

    @model_validator(mode="before")
    @classmethod
    def normalize_input(cls, data: Any) -> Any:
        if isinstance(data, list):
            valid = []
            for item in data:
                try:
                    valid.append(int(item))
                except (ValueError, TypeError):
                    pass
            return {"keep": valid}
        if isinstance(data, dict):
            raw_keep = (
                data.get("keep")
                or data.get("kept")
                or data.get("keep_indices")
                or data.get("indices")
                or data.get("relevant")
                or data.get("results")
            )
            if raw_keep is None:
                return {"keep": []}
            if isinstance(raw_keep, (int, float)):
                return {"keep": [int(raw_keep)]}
            if isinstance(raw_keep, list):
                valid = []
                for item in raw_keep:
                    try:
                        valid.append(int(item))
                    except (ValueError, TypeError):
                        pass
                return {"keep": valid}
            return {"keep": []}
        return {"keep": []}


def _extract_event_text_and_meta(event: Union[NormalizedEvent, Dict[str, Any]]) -> Tuple[str, str, str, Dict[str, Any]]:
    if isinstance(event, dict):
        title = str(event.get("title") or "").strip()
        raw_text = str(event.get("raw_text") or "").strip()
        source_type = str(event.get("source_type") or "rss").lower().strip()
        source_name = str(event.get("source_name") or "").strip()
        meta = event.get("metadata") or event.get("metadata_json") or {}
    else:
        title = str(getattr(event, "title", "") or "").strip()
        raw_text = str(getattr(event, "raw_text", "") or "").strip()
        source_type = str(getattr(event, "source_type", "rss") or "rss").lower().strip()
        source_name = str(getattr(event, "source_name", "") or "").strip()
        meta = getattr(event, "metadata", {}) or {}
    combined = f"{title}\n{raw_text}".strip() if title and title != raw_text else (raw_text or title)
    return combined, source_type, source_name, (meta if isinstance(meta, dict) else {})


def _heuristic_keep_decision(
    combined_text: str,
    source_type: str,
    theme_context: Optional[Dict[str, Any]] = None,
) -> bool:
    """
    Deterministic relevance fallback used when LLM gatekeeper is offline or in heuristic fallback mode.
    Drops explicit casual chatter/spam while preserving vernacular observations and on-theme OSINT.
    """
    if not combined_text:
        return False

    lower = combined_text.lower().strip()
    for pat in BOT_AND_DELETED_PATTERNS + CASUAL_NOISE_PATTERNS:
        if pat in lower:
            return False

    # Check vernacular kinetic/acoustic lexicon (Arabic, Farsi, Hebrew, English)
    if has_vernacular_signal(combined_text):
        return True

    # Check theme keywords and entities
    ctx = theme_context or {}
    keywords = [str(k).lower() for k in (ctx.get("keywords") or []) if k]
    entities = [str(e).lower() for e in (ctx.get("entities") or []) if e]
    for kw in keywords + entities:
        if kw in lower:
            return True

    # Broad geopolitical / defense / OSINT terms
    osint_terms = (
        "missile", "strike", "drone", "uav", "irgc", "centcom", "idf", "hezbollah",
        "houthi", "iran", "israel", "lebanon", "beirut", "tehran", "syria", "damascus",
        "baghdad", "iraq", "yemen", "red sea", "hormuz", "naval", "vessel", "warship",
        "airstrike", "explosion", "siren", "intercept", "radar", "fighter", "bomber",
        "tanker", "sanction", "nuclear", "uranium", "iaea", "pentagon", "military",
        "troops", "artillery", "rocket", "ballistic", "hypersonic", "seismic", "tremor",
        "ukmto", "maritime", "ais", "osint", "breaking", "alert", "casualt", "diplomat",
        "talks", "ceasefire", "frontline", "convoy",
    )
    if any(t in lower for t in osint_terms):
        return True

    # For high-noise social/discussion streams, require a vernacular, theme, or OSINT match above
    if source_type in ("reddit", "reddit_comment", "telegram", "bluesky", "mastodon") or len(combined_text) < 25:
        return False

    return True


Literal_Decision = str  # "fast_pass" | "fast_drop" | "llm_candidate"


class Stage0Gatekeeper:
    """
    Hybrid 2-Layer Pre-Pipeline Gatekeeper.
    Evaluates batches of newly polled events before DB persistence and Stages 1-4.
    """

    def __init__(self, batch_size: int = 6, recent_buffer_size: int = 150):
        self.batch_size = max(1, min(batch_size, 25))
        self._recent_texts: Deque[str] = deque(maxlen=recent_buffer_size)
        self.evaluated_count: int = 0
        self.passed_count: int = 0
        self.fast_passed_count: int = 0
        self.dropped_deterministic_count: int = 0
        self.dropped_llm_count: int = 0
        self.batch_calls_count: int = 0
        self.last_model_used: Optional[str] = "gemma-4-26b-a4b-it"
        self.last_evaluated_at: Optional[str] = None

    def get_stats(self) -> Dict[str, Any]:
        total_dropped = self.dropped_deterministic_count + self.dropped_llm_count
        drop_rate = round((total_dropped / self.evaluated_count) * 100.0, 1) if self.evaluated_count > 0 else 0.0
        return {
            "evaluated_count": self.evaluated_count,
            "passed_count": self.passed_count,
            "fast_passed_count": self.fast_passed_count,
            "dropped_deterministic_count": self.dropped_deterministic_count,
            "dropped_llm_count": self.dropped_llm_count,
            "total_dropped_count": total_dropped,
            "drop_rate_pct": drop_rate,
            "batch_calls_count": self.batch_calls_count,
            "batch_size": self.batch_size,
            "last_model_used": self.last_model_used,
            "last_evaluated_at": self.last_evaluated_at,
        }

    def reset_stats(self) -> None:
        self.evaluated_count = 0
        self.passed_count = 0
        self.fast_passed_count = 0
        self.dropped_deterministic_count = 0
        self.dropped_llm_count = 0
        self.batch_calls_count = 0
        self._recent_texts.clear()

    def triage_layer1(
        self,
        event: Union[NormalizedEvent, Dict[str, Any]],
        theme_context: Optional[Dict[str, Any]] = None,
    ) -> Literal_Decision:
        """
        Layer 1 Zero-Cost Deterministic Triage:
        Returns:
          - "fast_pass": Directly relevant sensor/siren alert or high-confidence kinetic+theme hit.
          - "fast_drop": Empty, deleted, bot spam, ultra-short non-vernacular noise, or near-duplicate echo.
          - "llm_candidate": Needs Layer 2 micro-batched LLM evaluation.
        """
        combined, source_type, source_name, meta = _extract_event_text_and_meta(event)

        # 1. Hard empty / deleted / bot drop
        if not combined or len(combined.strip()) < 3:
            return "fast_drop"

        lower = combined.lower().strip()
        for pat in BOT_AND_DELETED_PATTERNS:
            if lower == pat or pat in lower:
                return "fast_drop"

        for noise_pat in CASUAL_NOISE_PATTERNS:
            if noise_pat in lower and not has_vernacular_signal(combined):
                return "fast_drop"

        # 2. Ultra-short noise check (< 12 chars with no vernacular or theme keyword)
        if len(combined.strip()) < 12 and not has_vernacular_signal(combined):
            return "fast_drop"

        # 3. Near-duplicate cross-channel echo check against recent rolling window
        if len(combined) >= 20 and self._recent_texts:
            is_dup, _ = is_duplicate_echo(combined, list(self._recent_texts), threshold=0.90)
            if is_dup:
                return "fast_drop"

        # 4. Direct Sensor & Tactical Siren Fast-Pass
        if source_type in ("ais", "maritime_ais", "ukmto", "seismic", "emsc", "geophysical", "oref", "pikud_haoref", "homefront", "hfc"):
            self._recent_texts.append(combined)
            return "fast_pass"

        if meta.get("is_alert_channel") or meta.get("is_urgent_alert"):
            self._recent_texts.append(combined)
            return "fast_pass"

        # 5. Vernacular kinetic/explosion/acoustic trigger fast-pass
        norm_text = normalize_arabic_text(combined).lower()
        has_kinetic = any(normalize_arabic_text(k).lower() in norm_text for k in KINETIC_TRIGGERS)
        if has_kinetic or has_vernacular_signal(combined):
            self._recent_texts.append(combined)
            return "fast_pass"

        return "llm_candidate"

    async def evaluate_llm_chunk(
        self,
        chunk: List[Union[NormalizedEvent, Dict[str, Any]]],
        provider: BaseAIProvider,
        theme_context: Optional[Dict[str, Any]] = None,
        model: Optional[str] = None,
        temperature: float = 0.0,
        raise_on_error: bool = False,
    ) -> List[int]:
        """
        Send a single micro-batch (up to self.batch_size events) to the configured high-RPD LLM
        and return the list of 0-based indices within `chunk` that should be KEPT.
        """
        if not chunk:
            return []

        ctx = theme_context or {}
        theme_name = ctx.get("name", "US-Iran & Middle East Conflict")
        theme_desc = ctx.get("description", "Monitoring military, geopolitical, maritime, and kinetic field developments")
        keywords = ", ".join(str(k) for k in (ctx.get("keywords") or [])[:25])

        lines: List[str] = [
            f"Active Theme: {theme_name} ({theme_desc})",
            f"Key Monitored Terms: {keywords}",
            "",
            "Messages to evaluate:",
        ]
        for idx, ev in enumerate(chunk):
            combined, src_type, src_name, meta = _extract_event_text_and_meta(ev)
            feed_mode = meta.get("feed_mode", "")
            tag = f"{src_type}:{src_name}" + (f"/{feed_mode}" if feed_mode else "")
            snippet = combined.replace("\n", " ")[:280]
            lines.append(f"[{idx}] ({tag}) {snippet}")

        lines.append('\nReturn valid JSON with the indices of relevant OSINT/field messages to keep: {"keep": [...]}')
        prompt = "\n".join(lines)

        def heuristic_fallback() -> Stage0BatchResult:
            kept = []
            for i, ev in enumerate(chunk):
                c_text, s_type, _, m = _extract_event_text_and_meta(ev)
                eff_type = "reddit_comment" if m.get("is_comment") else s_type
                if _heuristic_keep_decision(c_text, eff_type, ctx):
                    kept.append(i)
            return Stage0BatchResult(keep=kept)

        try:
            self.batch_calls_count += 1
            from backend.app.services.world_theater_service import get_world_theater_service
            theater_block = get_world_theater_service().get_prompt_block("gatekeeper")
            sys_prompt = f"{STAGE0_SYSTEM_PROMPT}\n\n{theater_block}"

            raw_resp = await provider.generate_text(
                prompt=prompt,
                system_prompt=sys_prompt,
                model=model,
                temperature=temperature if temperature is not None else 0.1,
                max_tokens=2048,
                json_mode=True,
            )
            self.last_model_used = f"{provider.provider_name}:{model or provider.default_model}"
            parsed = extract_and_parse_json(
                raw_text=raw_resp,
                target_model=Stage0BatchResult,
                fallback_factory=None if raise_on_error else heuristic_fallback,
            )
            valid_indices = [i for i in parsed.keep if isinstance(i, int) and 0 <= i < len(chunk)]
            return valid_indices
        except PermissionError:
            raise
        except Exception as exc:
            if raise_on_error:
                raise
            logger.warning(f"Stage 0 LLM batch call failed ({exc}); using deterministic vernacular/theme heuristic.")
            return heuristic_fallback().keep

