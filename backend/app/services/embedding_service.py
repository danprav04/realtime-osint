"""
backend/app/services/embedding_service.py - Gemini Batch Embedding & Relevance Map Engine
========================================================================================
Handles compact composite text representation, batch embeddings via Google Gemini REST API,
strict DB deduplication, daily quota tracking (respecting 100 RPM / 30K TPM / 1K RPD),
PCA 2D dimensionality reduction, and multi-source trustworthiness/corroboration scoring.
"""

import asyncio
import hashlib
import json
import logging
import math
import os
import re
import time
import uuid
from datetime import datetime, timezone, timedelta
from typing import Any, Dict, List, Optional, Set, Tuple, Union

import httpx
import numpy as np
from sklearn.cluster import AgglomerativeClustering, KMeans
from sklearn.decomposition import PCA
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.config import get_settings
from backend.app.db.models import Event, EventEmbedding, SystemSettings, Theme, utc_now
from backend.app.pipeline.providers.base import classify_rate_limit_429

logger = logging.getLogger("osint.services.embedding")

GEMINI_BASE_URL = "https://generativelanguage.googleapis.com/v1beta/models"

# Available Gemini embedding models matching the user's quota tiers:
# Gemini Embedding 2 -> models/gemini-embedding-2   (100 RPM, 30K TPM, 1,000 RPD)
# Gemini Embedding 1 -> models/gemini-embedding-001 (100 RPM, 30K TPM, 1,000 RPD)
DEFAULT_EMBEDDING_MODEL = "gemini-embedding-2"
FALLBACK_EMBEDDING_MODEL = "gemini-embedding-001"

# Safe batch and pacing constants
MAX_ITEMS_PER_BATCH = 20  # Keep batch reasonable to stay well within 30K TPM
MAX_TEXT_CHAR_LENGTH = 1500  # ~350 tokens max per article
INTER_BATCH_DELAY_SEC = 1.0  # Polite pause between batches to protect RPM

QUOTA_SETTINGS_KEY = "gemini_embeddings_quota_tracker"


def get_today_utc_date_str() -> str:
    """Return current UTC date in YYYY-MM-DD format."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def _pad_and_normalize_vector(vector: List[float], target_dim: int = 3072) -> List[float]:
    """Pad (or truncate) an embedding vector to target_dim and L2 normalize."""
    if not vector:
        return [0.0] * target_dim
    if len(vector) >= target_dim:
        vec = [float(x) for x in vector[:target_dim]]
    else:
        vec = [float(x) for x in vector] + [0.0] * (target_dim - len(vector))
    norm = math.sqrt(sum(x * x for x in vec))
    if norm > 1e-9:
        vec = [x / norm for x in vec]
    return vec


def build_embedding_document(event: Event, theme_name: Optional[str] = None) -> str:
    """
    Construct a dense, high-signal composite text representation of an event.
    Combines: Title/Headline, Timestamp, Location, Domain/Field, Entities, Content, and Source.
    Truncates text to ~1500 chars (~350 tokens) to strictly obey Gemini 30K TPM limits.
    """
    headline = (event.headline or event.title or "Untitled").strip()
    parts = [f"Headline: {headline}"]

    if event.published_at:
        try:
            pub_time = event.published_at.strftime("%Y-%m-%d %H:%M UTC")
            parts.append(f"Timestamp: {pub_time}")
        except Exception:
            parts.append(f"Timestamp: {str(event.published_at)[:16]}")

    # Location (only if present)
    if isinstance(event.location_json, dict) and event.location_json:
        loc = event.location_json.get("name") or event.location_json.get("country")
        if loc:
            parts.append(f"Location: {loc}")
    elif isinstance(event.location_json, str) and event.location_json.strip():
        parts.append(f"Location: {event.location_json.strip()}")

    # Domain / Field
    if theme_name:
        parts.append(f"Domain: {theme_name}")

    # Entities (only if present)
    if event.entities_json:
        parts.append(f"Entities: {', '.join(event.entities_json[:8])}")

    parts.append(f"Source: {event.source_name} ({event.source_type})")

    content_text = (event.summary or event.translated_text or event.raw_text or "").strip()
    if content_text:
        if len(content_text) > 800:
            content_text = content_text[:797] + "..."
        parts.append(f"Content: {content_text}")

    doc = "\n".join(parts)
    if len(doc) > MAX_TEXT_CHAR_LENGTH:
        doc = doc[:MAX_TEXT_CHAR_LENGTH]
    return doc


def truncate_at_word_boundary(text: str, max_len: int = 44) -> str:
    """
    Cleanly truncate a headline or label at a word boundary without chopping words in half.
    Strips trailing outlet attributions (e.g., ' - Reuters') and leading urgent prefixes.
    """
    if not text:
        return "Untitled"
    cleaned = re.sub(r"^[\s🔴⭕️🚨⚡️⚠️\*\-\#•]+", "", text).strip()
    cleaned = re.sub(
        r"^(urgent|breaking|عاجل|مهم|update|live|watch|report)\s*[:\-–—]\s*",
        "",
        cleaned,
        flags=re.IGNORECASE,
    ).strip()
    # Strip trailing source attribution like " - Reuters" or " | Al Jazeera"
    cleaned = re.sub(r"\s+[\-–—\|]\s+[A-Z][\w\s\.&]{1,25}$", "", cleaned).strip()

    if len(cleaned) <= max_len:
        return cleaned

    cut = cleaned[:max_len]
    if not cleaned[max_len].isspace():
        last_space = cut.rfind(" ")
        if last_space >= int(max_len * 0.55):
            cut = cut[:last_space]
    cut = cut.rstrip(" ,;:-–—")
    return cut + "…"


def build_semantic_embedding_text(event: Event) -> str:
    """
    Construct a pure semantic text representation for vector embedding.
    Excludes non-semantic boilerplate (timestamps, source feed names, template labels)
    so unrelated articles from the same feed or hour do not share artificial similarity.
    Weights headline and named entities prominently for event-level clustering accuracy.
    """
    headline = (event.headline or event.title or "Untitled").strip()
    headline = re.sub(r"^[\s🔴⭕️🚨⚡️⚠️\*\-\#]+", "", headline).strip()

    parts = [headline]

    # Location context
    loc_str = ""
    if isinstance(event.location_json, dict) and event.location_json:
        loc_str = str(
            event.location_json.get("name")
            or event.location_json.get("country")
            or ""
        ).strip()
    elif isinstance(event.location_json, str) and event.location_json.strip():
        loc_str = event.location_json.strip()

    # Entities context
    entities_list = [str(e).strip() for e in (event.entities_json or []) if str(e).strip()]
    if loc_str and loc_str not in entities_list:
        entities_list.insert(0, loc_str)

    if entities_list:
        parts.append(", ".join(entities_list[:8]))

    # Prefer English summary or translated text over raw untranslated text
    content_text = (event.summary or event.translated_text or event.raw_text or "").strip()
    if content_text and content_text.lower() != headline.lower():
        if len(content_text) > 900:
            content_text = content_text[:897] + "..."
        parts.append(content_text)

    doc = "\n".join(parts)
    if len(doc) > MAX_TEXT_CHAR_LENGTH:
        doc = doc[:MAX_TEXT_CHAR_LENGTH]
    return doc


def _extract_event_Lookup_tokens(event: Event) -> Set[str]:
    """
    Extract normalized significant entities, locations, and proper/content tokens
    from an event to verify whether two semantically similar articles discuss the same actors/places.
    """
    tokens: Set[str] = set()
    if event.entities_json:
        for ent in event.entities_json:
            for w in re.findall(r"[a-zA-Z0-9]{3,}", str(ent).lower()):
                if w not in MOCK_STOPWORDS:
                    tokens.add(w)

    if isinstance(event.location_json, dict) and event.location_json:
        loc_val = event.location_json.get("name") or event.location_json.get("country") or ""
        for w in re.findall(r"[a-zA-Z0-9]{3,}", str(loc_val).lower()):
            if w not in MOCK_STOPWORDS:
                tokens.add(w)

    headline = (event.headline or event.title or "").lower()
    for w in re.findall(r"[a-zA-Z0-9]{4,}", headline):
        if w not in MOCK_STOPWORDS:
            tokens.add(w)

    return tokens


def compute_recency_weight(
    published_at: Optional[datetime],
    now: Optional[datetime] = None,
    half_life_hours: float = 18.0,
    min_weight: float = 0.10,
) -> float:
    """
    Compute a continuous exponential recency weight w(Δt) = max(min_weight, 0.5^(Δt / half_life)).
    Δt is hours elapsed between published_at and now.
    Recent intelligence (last few hours) receives weights close to 1.0, smoothly decaying to min_weight.
    """
    if not published_at:
        return min_weight
    if now is None:
        now = utc_now()

    if isinstance(published_at, str):
        try:
            t_pub = datetime.fromisoformat(published_at.replace("Z", "+00:00"))
        except Exception:
            return min_weight
    elif isinstance(published_at, datetime):
        t_pub = published_at
    else:
        return min_weight

    if t_pub.tzinfo is None:
        t_pub = t_pub.replace(tzinfo=timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)

    hours_diff = max(0.0, (now - t_pub).total_seconds() / 3600.0)
    decay = 0.5 ** (hours_diff / max(0.1, half_life_hours))
    return float(max(min_weight, min(1.0, decay)))


def _compute_pairwise_context_bonus(ev_a: Event, ev_b: Event) -> float:
    """
    Compute a small contextual adjustment [-0.07, +0.035] to raw cosine similarity
    based on temporal proximity and shared named entities/locations.
    Prevents unrelated stories separated by days or concerning disjoint actors
    from falsely corroborating each other on generic conflict vocabulary.
    """
    adjustment = 0.0

    # 1. Temporal proximity check (smooth continuous decay)
    if ev_a.published_at and ev_b.published_at:
        try:
            t_a = ev_a.published_at
            t_b = ev_b.published_at
            if t_a.tzinfo is None:
                t_a = t_a.replace(tzinfo=timezone.utc)
            if t_b.tzinfo is None:
                t_b = t_b.replace(tzinfo=timezone.utc)
            hours_diff = abs((t_a - t_b).total_seconds()) / 3600.0
            if hours_diff <= 12.0:
                adjustment += 0.015 * (1.0 - (hours_diff / 12.0) * 0.3)
            elif hours_diff <= 24.0:
                adjustment += 0.008 * (1.0 - (hours_diff - 12.0) / 12.0)
            elif hours_diff > 96.0:
                decay_factor = min(1.0, (hours_diff - 96.0) / 96.0)
                adjustment -= (0.045 + 0.025 * decay_factor)
            elif hours_diff > 48.0:
                adjustment -= (0.015 + 0.025 * ((hours_diff - 48.0) / 48.0))
        except Exception:
            pass

    # 2. Entity & key-term overlap check
    tok_a = _extract_event_Lookup_tokens(ev_a)
    tok_b = _extract_event_Lookup_tokens(ev_b)
    if tok_a and tok_b:
        overlap = tok_a.intersection(tok_b)
        if len(overlap) >= 2:
            adjustment += 0.025
        elif len(overlap) == 1:
            adjustment += 0.01
        elif len(tok_a) >= 3 and len(tok_b) >= 3:
            # Both have specific entities/keywords but zero overlap
            adjustment -= 0.03

    return adjustment



MOCK_STOPWORDS = {
    "the", "a", "an", "in", "on", "at", "to", "for", "of", "and", "or", "is", "was", "are",
    "by", "with", "from", "headline", "timestamp", "content", "source", "domain", "entities",
    "location", "utc", "http", "https", "that", "this", "it", "as", "be", "has", "have", "had",
    "will", "would", "about", "into", "over", "after"
}


def generate_deterministic_mock_embedding(text: str, dim: int = 3072) -> List[float]:
    """
    Generate a deterministic bag-of-words pseudo-random unit vector.
    Texts sharing key vocabulary will yield realistic cosine similarities.
    Filters common boilerplate/stopwords so dissimilar stories do not link falsely.
    """
    cleaned = text.lower().strip()
    words = [w.strip(".,;:!?\"'()[]{}") for w in cleaned.split() if w.strip(".,;:!?\"'()[]{}")]
    filtered_words = [w for w in words if w not in MOCK_STOPWORDS]
    if filtered_words:
        words = filtered_words

    if not words:
        full_hash = int(hashlib.sha256(cleaned.encode("utf-8")).hexdigest()[:14], 16)
        rng = np.random.RandomState(full_hash % (2**32))
        base_vec = rng.randn(dim).astype(np.float32)
        norm = np.linalg.norm(base_vec)
        return [float(x) for x in (base_vec / norm if norm > 0 else base_vec)]

    vec = np.zeros(dim, dtype=np.float32)
    for word in words:
        w_hash = int(hashlib.md5(word.encode("utf-8")).hexdigest()[:12], 16)
        w_rng = np.random.RandomState(w_hash % (2**32))
        word_dir = w_rng.randn(dim).astype(np.float32)
        w_norm = np.linalg.norm(word_dir)
        if w_norm > 0:
            vec += word_dir / w_norm

    norm = np.linalg.norm(vec)
    if norm > 0:
        vec = vec / norm
    return [float(x) for x in vec]


def resolve_node_collisions(
    coords: np.ndarray,
    min_dist: float = 8.5,
    max_iter: int = 40,
    bounds: float = 85.0,
) -> np.ndarray:
    """
    Relax 2D node coordinates using iterative spring repulsion so no two nodes overlap.
    Guarantees pairwise Euclidean distance >= min_dist while keeping positions inside [-bounds, bounds].
    """
    n = len(coords)
    if n <= 1:
        return coords

    pts = coords.copy().astype(np.float32)
    rng = np.random.RandomState(42)

    for _ in range(max_iter):
        moved = False
        for i in range(n):
            for j in range(i + 1, n):
                diff = pts[i] - pts[j]
                d = float(np.linalg.norm(diff))
                if d < min_dist:
                    moved = True
                    if d < 1e-4:
                        angle = rng.uniform(0.0, 2.0 * np.pi)
                        u = np.array([np.cos(angle), np.sin(angle)], dtype=np.float32)
                    else:
                        u = diff / d
                    overlap = (min_dist - d) * 0.5
                    pts[i] += u * overlap
                    pts[j] -= u * overlap
        if not moved:
            break

    return np.clip(pts, -bounds, bounds)



def _homogenize_vectors(vectors: List[List[float]]) -> np.ndarray:
    """
    Ensure a list of embedding vectors has a uniform 2D shape before converting to np.ndarray.
    Handles mixed dimensions (e.g., 3072d Gemini vectors + 384d HF TEI vectors in DB)
    by zero-padding shorter vectors to the maximum dimension present.
    """
    if not vectors:
        return np.empty((0, 0), dtype=np.float32)
    lengths = {len(v) for v in vectors}
    if len(lengths) == 1:
        return np.array(vectors, dtype=np.float32)
    max_dim = max(lengths)
    out = np.zeros((len(vectors), max_dim), dtype=np.float32)
    for idx, v in enumerate(vectors):
        if v:
            out[idx, : len(v)] = np.asarray(v, dtype=np.float32)
    return out


class EmbeddingService:
    """
    Central service for generating embeddings via Gemini batch API,
    caching vectors in SQLite/Postgres, tracking rate limits,
    computing PCA 2D coordinates, and scoring trustworthiness.
    """

    def __init__(self, api_key: Optional[str] = None):
        self._api_key = api_key
        self.default_model = DEFAULT_EMBEDDING_MODEL
        self.fallback_model = FALLBACK_EMBEDDING_MODEL
        self.last_pca_model: Optional[PCA] = None
        self.max_items_per_minute: int = 90  # Safely under 100 RPM limit
        self._rpm_item_timestamps: Dict[str, List[float]] = {}
        self._pacing_lock = asyncio.Lock()

    async def _acquire_embedding_pacing_slot(self, model: str, item_count: int) -> None:
        """
        Enforce sliding-window item rate limiting (<= 90 items per 60s) so batchEmbedContents
        never exceeds Gemini's 100 RPM per-item quota.
        """
        if self.max_items_per_minute <= 0 or item_count <= 0:
            return
        while True:
            wait_sec = 0.0
            async with self._pacing_lock:
                now = time.monotonic()
                history = self._rpm_item_timestamps.get(model, [])
                cutoff = now - 60.0
                history = [ts for ts in history if ts > cutoff]
                if len(history) + item_count > self.max_items_per_minute and history:
                    # Wait until enough old items expire from the 60s window
                    excess = (len(history) + item_count) - self.max_items_per_minute
                    idx = min(excess - 1, len(history) - 1)
                    wait_sec = max(0.2, (history[idx] + 60.1) - now)
                else:
                    history.extend([now] * item_count)
                    self._rpm_item_timestamps[model] = history
                    return
            if wait_sec > 0:
                logger.info(
                    f"[gemini-embeddings] Pacing {model}: {len(history)} items in last 60s "
                    f"(limit {self.max_items_per_minute}/min). Waiting {wait_sec:.1f}s before batch of {item_count}..."
                )
                await asyncio.sleep(wait_sec)

    @property
    def api_key(self) -> Optional[str]:
        if self._api_key:
            return self._api_key
        env_key = os.getenv("GEMINI_API_KEY")
        if env_key:
            return env_key
        try:
            return get_settings().GEMINI_API_KEY
        except Exception:
            return None

    @property
    def hf_embedding_url(self) -> Optional[str]:
        env_url = os.getenv("HF_EMBEDDING_URL")
        if env_url:
            return env_url
        try:
            return get_settings().HF_EMBEDDING_URL
        except Exception:
            return None

    @property
    def hf_api_key(self) -> Optional[str]:
        env_token = os.getenv("HF_API_KEY") or os.getenv("HF_TOKEN")
        if env_token:
            return env_token
        try:
            return get_settings().HF_API_KEY
        except Exception:
            return None

    async def get_quota_tracker(self, session: AsyncSession) -> Dict[str, Any]:
        """Retrieve or initialize persistent daily quota tracker from system_settings."""
        stmt = select(SystemSettings).where(SystemSettings.key == QUOTA_SETTINGS_KEY)
        record = (await session.execute(stmt)).scalars().first()
        today = get_today_utc_date_str()

        default_data = {
            "date": today,
            "models": {
                DEFAULT_EMBEDDING_MODEL: {"requests_today": 0, "limit": 1000},
                FALLBACK_EMBEDDING_MODEL: {"requests_today": 0, "limit": 1000},
            },
            "total_embeddings_generated": 0,
            "quota_saved_requests": 0,
        }

        if not record:
            return default_data

        data = record.value_json or default_data
        # If new day, reset daily counters
        if data.get("date") != today:
            data["date"] = today
            for m in data.get("models", {}).values():
                m["requests_today"] = 0
            record.value_json = data
            await session.commit()

        return data

    async def record_usage(
        self, session: AsyncSession, model_used: str, items_embedded: int
    ) -> None:
        """Increment daily quota counters in system_settings."""
        stmt = select(SystemSettings).where(SystemSettings.key == QUOTA_SETTINGS_KEY)
        record = (await session.execute(stmt)).scalars().first()
        today = get_today_utc_date_str()

        if not record:
            tracker = {
                "date": today,
                "models": {
                    DEFAULT_EMBEDDING_MODEL: {"requests_today": 0, "limit": 1000},
                    FALLBACK_EMBEDDING_MODEL: {"requests_today": 0, "limit": 1000},
                },
                "total_embeddings_generated": items_embedded,
                "quota_saved_requests": 0,
            }
            if model_used in tracker["models"]:
                tracker["models"][model_used]["requests_today"] += items_embedded
            record = SystemSettings(key=QUOTA_SETTINGS_KEY, value_json=tracker)
            session.add(record)
        else:
            data = dict(record.value_json)
            if data.get("date") != today:
                data["date"] = today
                for m in data.get("models", {}).values():
                    m["requests_today"] = 0
            if "models" not in data:
                data["models"] = {}
            if model_used not in data["models"]:
                data["models"][model_used] = {"requests_today": 0, "limit": 1000}
            data["models"][model_used]["requests_today"] += items_embedded
            data["total_embeddings_generated"] = data.get("total_embeddings_generated", 0) + items_embedded
            record.value_json = data

        await session.commit()

    async def call_gemini_batch_api(
        self, texts: List[str], model: str, api_key: str
    ) -> List[List[float]]:
        """
        Execute a POST request to Google Gemini's batchEmbedContents endpoint.
        Paces within the 100 RPM limit and retries on per-minute 429 limits before failing over.
        Returns list of float vectors (3072d).
        """
        url = f"{GEMINI_BASE_URL}/{model}:batchEmbedContents"
        headers = {
            "Content-Type": "application/json",
            "x-goog-api-key": api_key,
        }
        payload = {
            "requests": [
                {
                    "model": f"models/{model}",
                    "content": {"parts": [{"text": text}]},
                }
                for text in texts
            ]
        }

        max_minute_retries = 3
        est_tokens = max(1, sum(len(t) for t in texts) // 3)
        for attempt in range(max_minute_retries + 1):
            await self._acquire_embedding_pacing_slot(model, len(texts))
            t0 = time.monotonic()
            async with httpx.AsyncClient(timeout=30.0) as client:
                resp = await client.post(url, json=payload, headers=headers)
                duration_ms = (time.monotonic() - t0) * 1000.0
                if resp.status_code == 200:
                    data = resp.json()
                    raw_embs = data.get("embeddings", [])
                    vectors = [item.get("values", []) for item in raw_embs]
                    if len(vectors) != len(texts):
                        raise ValueError(
                            f"Expected {len(texts)} embeddings, received {len(vectors)}"
                        )
                    try:
                        from backend.app.pipeline.usage_tracker import get_usage_tracker
                        get_usage_tracker().record_call(
                            provider="gemini",
                            model=model,
                            key=api_key,
                            key_field="gemini_api_key",
                            prompt_tokens=est_tokens,
                            completion_tokens=0,
                            total_tokens=est_tokens,
                            duration_ms=duration_ms,
                            success=True,
                            status_code=200,
                        )
                    except Exception:
                        pass
                    return vectors
                elif resp.status_code == 429:
                    err_text = resp.text
                    is_daily, parsed_wait, reason = classify_rate_limit_429(resp, "gemini")
                    try:
                        from backend.app.pipeline.usage_tracker import get_usage_tracker
                        get_usage_tracker().record_call(
                            provider="gemini",
                            model=model,
                            key=api_key,
                            key_field="gemini_api_key",
                            success=False,
                            status_code=429,
                            error_message=reason or "Gemini Embeddings 429",
                            is_rate_limit=True,
                            is_daily_quota=is_daily,
                            duration_ms=duration_ms,
                        )
                    except Exception:
                        pass
                    if is_daily:
                        logger.warning(
                            f"Gemini Embeddings daily quota (RPD) reached for {model} ({reason}): {err_text[:200]}"
                        )
                        raise RuntimeError(f"QUOTA_EXCEEDED: {err_text}")
                    if attempt < max_minute_retries:
                        wait_sec = parsed_wait or 0.0
                        if wait_sec <= 0:
                            wait_sec = min(15.0 * (attempt + 1), 60.0)
                        else:
                            wait_sec = min(wait_sec + 1.0, 65.0)
                        logger.warning(
                            f"Gemini Embeddings {reason} for {model} "
                            f"(attempt {attempt + 1}/{max_minute_retries + 1}). "
                            f"Waiting {wait_sec:.1f}s to retry on {model}..."
                        )
                        await asyncio.sleep(wait_sec)
                        continue
                    logger.warning(f"Gemini API rate limit 429 persisted for model {model}: {err_text[:200]}")
                    raise RuntimeError(f"QUOTA_EXCEEDED: {err_text}")
                else:
                    try:
                        from backend.app.pipeline.usage_tracker import get_usage_tracker
                        get_usage_tracker().record_call(
                            provider="gemini",
                            model=model,
                            key=api_key,
                            key_field="gemini_api_key",
                            success=False,
                            status_code=resp.status_code,
                            error_message=resp.text[:200],
                            duration_ms=duration_ms,
                        )
                    except Exception:
                        pass
                    raise RuntimeError(
                        f"Gemini API error {resp.status_code}: {resp.text[:300]}"
                    )
        raise RuntimeError(f"QUOTA_EXCEEDED: Exhausted retries for {model}")

    async def call_mistral_batch_api(
        self, texts: List[str], model: str = "mistral-embed-2312"
    ) -> List[List[float]]:
        """
        Execute batch embedding request to Mistral AI (https://api.mistral.ai/v1/embeddings)
        rotating across keys in MistralKeyPool and padding 1024-dim vectors to 3072-dim.
        """
        from backend.app.pipeline.providers.factory import get_shared_mistral_pool
        pool = get_shared_mistral_pool()
        if not pool or not pool.keys:
            raise RuntimeError("No Mistral API keys configured in MistralKeyPool")

        key_slot = await pool.acquire_next_key(model="mistral-embed-2312")
        url = "https://api.mistral.ai/v1/embeddings"
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {key_slot.api_key}",
        }
        payload = {
            "model": "mistral-embed",
            "input": texts,
        }
        t0 = time.monotonic()
        await key_slot.acquire_pacing_slot("mistral-embed-2312")
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.post(url, json=payload, headers=headers)
            duration_ms = (time.monotonic() - t0) * 1000.0
            await key_slot.update_from_response(
                resp, "mistral-embed-2312", duration_sec=duration_ms / 1000.0
            )
            if resp.status_code == 429:
                key_slot.mark_rate_limited("mistral-embed-2312", delay=10.0)
                raise RuntimeError(f"Mistral embedding rate limited: {resp.text[:200]}")
            resp.raise_for_status()
            data = resp.json()
            items = data.get("data", [])
            tokens = data.get("usage", {}).get("total_tokens", sum(len(t) // 3 for t in texts))
            try:
                from backend.app.pipeline.usage_tracker import get_usage_tracker
                get_usage_tracker().record_call(
                    provider="mistral",
                    model="mistral-embed-2312",
                    key=key_slot.api_key,
                    key_field="mistral_api_key",
                    prompt_tokens=tokens,
                    completion_tokens=0,
                    total_tokens=tokens,
                    duration_ms=duration_ms,
                    success=True,
                    status_code=200,
                )
            except Exception:
                pass
            raw_vectors = [item["embedding"] for item in items]
            return [_pad_and_normalize_vector(v, 3072) for v in raw_vectors]

    async def call_hf_tei_api(
        self, texts: List[str], endpoint_url: str, api_key: Optional[str] = None
    ) -> List[List[float]]:
        """
        Execute batch request to a Hugging Face Space running Text Embeddings Inference (TEI)
        or a Gradio Space exposing /embed, /v1/embeddings, or /gradio_api/call/embed_batch.
        """
        clean_url = endpoint_url.strip().rstrip("/")
        base_url = clean_url
        for suffix in ("/embed", "/v1/embeddings"):
            if base_url.endswith(suffix):
                base_url = base_url[: -len(suffix)]

        if not clean_url.endswith("/embed") and not clean_url.endswith("/v1/embeddings"):
            url = f"{clean_url}/embed"
        else:
            url = clean_url

        headers = {
            "Content-Type": "application/json",
            "x-wait-for-model": "true",
        }
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"

        if url.endswith("/v1/embeddings"):
            payload = {"input": texts, "model": "sentence-transformers/all-MiniLM-L6-v2"}
        else:
            payload = {"inputs": texts, "truncate": True}

        async with httpx.AsyncClient(timeout=45.0, follow_redirects=True) as client:
            resp = await client.post(url, json=payload, headers=headers)
            if resp.status_code == 503:
                # HF Space is sleeping / cold-starting
                try:
                    err_json = resp.json()
                    est_time = float(err_json.get("estimated_time", 4.0))
                except Exception:
                    est_time = 4.0
                sleep_sec = min(max(2.0, est_time), 12.0)
                logger.info(f"HF Space TEI is cold-starting (HTTP 503). Waiting {sleep_sec:.1f}s...")
                await asyncio.sleep(sleep_sec)
                resp = await client.post(url, json=payload, headers=headers)

            if resp.status_code == 200:
                try:
                    from backend.app.pipeline.usage_tracker import get_usage_tracker
                    get_usage_tracker().record_call(
                        provider="huggingface",
                        model="sentence-transformers/all-MiniLM-L6-v2",
                        key=api_key or "hf_tei_space",
                        key_field="hf_api_key",
                        prompt_tokens=max(1, sum(len(t) for t in texts) // 3),
                        completion_tokens=0,
                        total_tokens=max(1, sum(len(t) for t in texts) // 3),
                        duration_ms=0.0,
                        success=True,
                        status_code=200,
                    )
                except Exception:
                    pass
                data = resp.json()
                if isinstance(data, list):
                    # Native TEI returns [[float, ...], [float, ...]]
                    return data
                elif isinstance(data, dict) and "data" in data:
                    # OpenAI format
                    return [item["embedding"] for item in data["data"]]
                elif isinstance(data, dict) and "embeddings" in data:
                    return data["embeddings"]
                else:
                    raise ValueError(f"Unrecognized response format from HF TEI endpoint: {type(data)}")

            # Fallback to Gradio 5/6 native API (/gradio_api/call/embed_batch) if /embed returned 404/405
            if resp.status_code in (404, 405):
                gradio_call_url = f"{base_url}/gradio_api/call/embed_batch"
                call_resp = await client.post(
                    gradio_call_url,
                    json={"data": [json.dumps(texts)]},
                    headers=headers,
                )
                if call_resp.status_code == 200:
                    event_id = call_resp.json().get("event_id")
                    if event_id:
                        sse_resp = await client.get(
                            f"{gradio_call_url}/{event_id}", headers=headers
                        )
                        if sse_resp.status_code == 200:
                            for line in sse_resp.text.splitlines():
                                if line.startswith("data: "):
                                    raw_data = line[6:].strip()
                                    if raw_data and raw_data != "null":
                                        arr = json.loads(raw_data)
                                        if isinstance(arr, list) and arr:
                                            first = arr[0]
                                            parsed = json.loads(first) if isinstance(first, str) else first
                                            if isinstance(parsed, dict) and "embeddings" in parsed:
                                                return parsed["embeddings"]
                                            if isinstance(parsed, list):
                                                return parsed

            raise RuntimeError(f"HF Space TEI error {resp.status_code}: {resp.text[:300]}")

    async def generate_embeddings_batch(
        self,
        texts: List[str],
        preferred_model: Optional[str] = None,
        use_mock: bool = False,
        session: Optional[AsyncSession] = None,
    ) -> Tuple[List[List[float]], str]:
        """
        Generate embeddings for a list of texts.
        Chunk into batches of up to MAX_ITEMS_PER_BATCH.
        Handles failover from Gemini Embedding 2 -> Gemini Embedding 1 -> HF Space TEI -> Mock.
        Returns (list_of_embeddings, model_name_used).
        """
        if not texts:
            return [], self.default_model

        if use_mock:
            logger.info("Using deterministic mock embeddings (explicit mock mode)")
            results = [generate_deterministic_mock_embedding(t) for t in texts]
            return results, "mock-embedding-3072"

        active_key = self.api_key
        hf_url = self.hf_embedding_url
        hf_token = self.hf_api_key

        # Check if HF Space is explicitly requested as primary
        if preferred_model in ("hf-space-tei", "huggingface") and hf_url:
            try:
                vectors = await self.call_hf_tei_api(texts, hf_url, hf_token)
                if session:
                    try:
                        await self.record_usage(session, "hf-space-tei", len(texts))
                    except Exception:
                        pass
                return vectors, "hf-space-tei"
            except Exception as e:
                logger.warning(f"HF Space primary request failed ({e}), falling back to Gemini...")

        if not active_key or active_key.startswith("mock_"):
            # Try Mistral fallback first if configured
            try:
                vectors = await self.call_mistral_batch_api(texts, model="mistral-embed-2312")
                return vectors, "mistral-embed-2312"
            except Exception:
                pass
            if hf_url and not (active_key and active_key.startswith("mock_")):
                try:
                    logger.info(f"No Gemini API key; using Hugging Face Space TEI at {hf_url}...")
                    vectors = await self.call_hf_tei_api(texts, hf_url, hf_token)
                    return [_pad_and_normalize_vector(v, 3072) for v in vectors], "hf-space-tei"
                except Exception as hf_err:
                    logger.warning(f"HF Space TEI failed ({hf_err}), using deterministic mock embeddings.")
            logger.info("Using deterministic mock embeddings (mock mode or no API key)")
            results = [generate_deterministic_mock_embedding(t) for t in texts]
            return results, "mock-embedding-3072"

        primary = preferred_model or self.default_model
        all_vectors: List[List[float]] = []
        actual_model_used = primary

        # Process in chunks of MAX_ITEMS_PER_BATCH
        total = len(texts)
        for i in range(0, total, MAX_ITEMS_PER_BATCH):
            batch = texts[i : i + MAX_ITEMS_PER_BATCH]
            batch_vectors: Optional[List[List[float]]] = None

            # 1. Primary: Gemini Embedding 2
            try:
                batch_vectors = await self.call_gemini_batch_api(batch, primary, active_key)
                actual_model_used = primary
            except Exception as gemini_exc:
                logger.warning(
                    f"Gemini embedding failed for {primary} ({gemini_exc}); failing over to Mistral mistral-embed-2312..."
                )

                # 2. Fallback 1: Mistral mistral-embed-2312 (via 4-key MistralKeyPool)
                try:
                    batch_vectors = await self.call_mistral_batch_api(batch, model="mistral-embed-2312")
                    actual_model_used = "mistral-embed-2312"
                except Exception as mistral_exc:
                    logger.warning(
                        f"Mistral embedding fallback failed ({mistral_exc}); checking Hugging Face Space TEI..."
                    )

                    # 3. Fallback 2: Hugging Face Space TEI
                    if hf_url:
                        try:
                            logger.info(f"Failing over to Hugging Face Space TEI at {hf_url}...")
                            hf_raw = await self.call_hf_tei_api(batch, hf_url, hf_token)
                            batch_vectors = [_pad_and_normalize_vector(v, 3072) for v in hf_raw]
                            actual_model_used = "hf-space-tei"
                        except Exception as hf_err:
                            logger.error(f"HF Space TEI also failed: {hf_err}")

            if batch_vectors is None:
                # 4. Ultimate deterministic mock fallback
                logger.warning("All external embedding providers failed; using deterministic mock fallback.")
                batch_vectors = [generate_deterministic_mock_embedding(t) for t in batch]
                actual_model_used = "mock-fallback"

            all_vectors.extend(batch_vectors)

            if i + MAX_ITEMS_PER_BATCH < total:
                await asyncio.sleep(INTER_BATCH_DELAY_SEC)

        if session and actual_model_used not in ("mock-embedding-3072", "mock-fallback"):
            try:
                await self.record_usage(session, actual_model_used, len(texts))
            except Exception as e:
                logger.warning(f"Failed to record usage in tracker: {e}")

        return all_vectors, actual_model_used

    async def embed_events(
        self,
        events: List[Event],
        session: AsyncSession,
        use_mock: bool = False,
    ) -> List[EventEmbedding]:
        """
        Embed a list of Event objects, skipping any that are already embedded in the DB.
        Persists newly generated EventEmbedding records and commits.
        """
        if not events:
            return []

        event_ids = [e.id for e in events]
        # Query existing embeddings to prevent redundant API calls
        stmt = select(EventEmbedding).where(
            EventEmbedding.event_id.in_(event_ids)
        )
        existing_records = list((await session.execute(stmt)).scalars().all())
        existing_by_id: Dict[str, EventEmbedding] = {r.event_id: r for r in existing_records}

        pending_events = [e for e in events if e.id not in existing_by_id]
        if not pending_events:
            logger.info(
                f"All {len(events)} events already have cached embeddings. Skipping API calls."
            )
            return existing_records

        logger.info(
            f"Embedding {len(pending_events)} new events (out of {len(events)} requested)..."
        )
        texts = [build_semantic_embedding_text(e) for e in pending_events]

        vectors, model_name = await self.generate_embeddings_batch(
            texts=texts, use_mock=use_mock, session=session
        )

        bind = session.get_bind()
        dialect_name = getattr(getattr(bind, "dialect", None), "name", "")
        if dialect_name == "postgresql":
            from sqlalchemy.dialects.postgresql import insert as pg_insert

            values_to_insert = [
                {
                    "id": str(uuid.uuid4()),
                    "event_id": event.id,
                    "embedding_vector": vec,
                    "model_name": model_name,
                    "embedded_at": utc_now(),
                }
                for event, vec in zip(pending_events, vectors)
            ]
            if values_to_insert:
                stmt_insert = (
                    pg_insert(EventEmbedding)
                    .values(values_to_insert)
                    .on_conflict_do_nothing(index_elements=["event_id"])
                )
                await session.execute(stmt_insert)
                await session.commit()
        else:
            new_records: List[EventEmbedding] = []
            for event, vec in zip(pending_events, vectors):
                try:
                    async with session.begin_nested():
                        rec = EventEmbedding(
                            id=str(uuid.uuid4()),
                            event_id=event.id,
                            embedding_vector=vec,
                            model_name=model_name,
                            embedded_at=utc_now(),
                        )
                        session.add(rec)
                        await session.flush()
                        new_records.append(rec)
                except Exception:
                    pass
            await session.commit()

        logger.info(
            f"Successfully processed {len(pending_events)} new embeddings via {model_name}."
        )

        # Retrieve all requested records
        stmt_all = select(EventEmbedding).where(EventEmbedding.event_id.in_(event_ids))
        return list((await session.execute(stmt_all)).scalars().all())

    def compute_pca_2d(
        self, vectors: List[List[float]], min_distance: float = 8.5
    ) -> Tuple[List[Tuple[float, float]], Optional[PCA]]:
        """
        Project high-dimensional vectors to 2D (x, y) coordinates normalized to [-90, 90]
        with automatic collision separation to prevent node stacking.
        """
        if not vectors:
            return [], None

        n = len(vectors)
        if n == 1:
            return [(0.0, 0.0)], None

        pca = PCA(n_components=2, random_state=42)
        arr = _homogenize_vectors(vectors)
        # Handle zero or constant vectors
        if np.all(arr == arr[0]):
            pts = np.zeros((n, 2), dtype=np.float32)
            resolved = resolve_node_collisions(pts, min_dist=min_distance)
            pca.fit(resolved)
            return [(round(float(x), 2), round(float(y), 2)) for x, y in resolved], pca
        coords = pca.fit_transform(arr)

        # Normalize to [-90, 90] bounds for canvas rendering
        x_min, x_max = coords[:, 0].min(), coords[:, 0].max()
        y_min, y_max = coords[:, 1].min(), coords[:, 1].max()

        x_span = x_max - x_min if (x_max - x_min) > 1e-5 else 1.0
        y_span = y_max - y_min if (y_max - y_min) > 1e-5 else 1.0

        scaled_x = ((coords[:, 0] - x_min) / x_span) * 140.0 - 70.0
        scaled_y = ((coords[:, 1] - y_min) / y_span) * 140.0 - 70.0

        pts = np.column_stack([scaled_x, scaled_y])
        pts = resolve_node_collisions(pts, min_dist=min_distance)

        results = [(round(float(x), 2), round(float(y), 2)) for x, y in pts]
        return results, pca

    def compute_galaxy_coordinates(
        self,
        vectors: List[List[float]],
        clusters: List[Dict[str, Any]],
        node_metrics: List[Dict[str, Any]],
        min_distance: float = 7.8,
    ) -> List[Tuple[float, float]]:
        """
        Compute intuitive 2D coordinates arranged as a Core-to-Periphery Intelligence Radar:
        - Distance from center encodes Story Gravity & Multi-Source Consensus:
          * High-impact, highly corroborated galaxies sit in the Inner Core (r ~ 16-28).
          * Developing story clusters orbit in the Middle Band (r ~ 38-56).
          * Isolated / uncorroborated outliers sit on the Outer Perimeter Ring (r ~ 81).
        - Angular position around the radar preserves 2D PCA semantic neighborhood so
          related topics sit adjacent to one another.
        - Circle-packing repulsion between cluster discs guarantees galaxies never overlap.
        - Intra-cluster nodes place the central Medoid/Lead report at the galaxy core and
          group corroborated neighbors together by semantic angle.
        """
        n = len(vectors)
        if n == 0:
            return []
        if n == 1:
            return [(0.0, 0.0)]

        arr = _homogenize_vectors(vectors)
        norms = np.linalg.norm(arr, axis=1, keepdims=True)
        norms[norms == 0] = 1e-8
        norm_arr = arr / norms

        # Compute 2D PCA directions for semantic angular placement
        pca_pts = np.zeros((n, 2), dtype=np.float32)
        if n >= 2 and not np.all(norm_arr == norm_arr[0]):
            try:
                pca = PCA(n_components=2, random_state=42)
                pca_pts = pca.fit_transform(norm_arr).astype(np.float32)
            except Exception:
                pass

        cluster_by_idx = [m.get("cluster_id", 0) for m in node_metrics]
        main_clusters = [c for c in clusters if c["id"] > 0 and c["size"] >= 2]
        k_clusters = len(main_clusters)

        # Step 1: Compute compact local node offsets within each cluster first so we know exact cluster radii
        local_cluster_offsets: Dict[int, Dict[int, np.ndarray]] = {}
        true_radii: Dict[int, float] = {}

        for c in main_clusters:
            cid = c["id"]
            c_indices = [i for i in range(n) if cluster_by_idx[i] == cid]
            if not c_indices:
                true_radii[cid] = 9.0
                local_cluster_offsets[cid] = {}
                continue

            # Sort members so Lead node is rank 0 (at center), then by centrality & trust score
            c_indices.sort(
                key=lambda idx: (
                    1 if node_metrics[idx].get("is_cluster_lead") else 0,
                    node_metrics[idx].get("centrality", 0.0),
                    node_metrics[idx].get("trustworthiness_score", 50),
                ),
                reverse=True,
            )

            c_pca_mean = np.mean(pca_pts[c_indices], axis=0)
            local_pts = np.zeros((len(c_indices), 2), dtype=np.float32)

            for rank, node_idx in enumerate(c_indices):
                if rank == 0:
                    local_pts[rank] = np.array([0.0, 0.0], dtype=np.float32)
                else:
                    r_offset = math.sqrt(rank) * 2.45
                    local_vec = pca_pts[node_idx] - c_pca_mean
                    if float(np.linalg.norm(local_vec)) > 1e-4:
                        pca_angle = math.atan2(float(local_vec[1]), float(local_vec[0]))
                        golden_angle = rank * 2.39996323
                        angle = math.atan2(
                            0.40 * math.sin(pca_angle) + 0.60 * math.sin(golden_angle),
                            0.40 * math.cos(pca_angle) + 0.60 * math.cos(golden_angle),
                        )
                    else:
                        angle = rank * 2.39996323
                    local_pts[rank] = np.array(
                        [r_offset * math.cos(angle), r_offset * math.sin(angle)],
                        dtype=np.float32,
                    )

            # Resolve intra-cluster node collisions locally while keeping Lead node (index 0) anchored near (0,0)
            for _ in range(40):
                moved_local = False
                for i_l in range(len(c_indices)):
                    for j_l in range(i_l + 1, len(c_indices)):
                        diff = local_pts[i_l] - local_pts[j_l]
                        dist = float(np.linalg.norm(diff))
                        if dist < 4.3:
                            moved_local = True
                            u = np.array([1.0, 0.0], dtype=np.float32) if dist < 1e-4 else diff / dist
                            push = (4.3 - dist) * 0.52
                            if i_l == 0:
                                local_pts[j_l] -= u * (push * 1.8)
                            else:
                                local_pts[i_l] += u * push
                                local_pts[j_l] -= u * push
                if not moved_local:
                    break

            max_d = max((float(np.linalg.norm(pt)) for pt in local_pts), default=3.0)
            true_radii[cid] = float(max(7.5, max_d + 3.6))
            local_cluster_offsets[cid] = {
                c_indices[rank]: local_pts[rank] for rank in range(len(c_indices))
            }

        # If total cluster areas are very large, uniformly scale down local offsets so all discs fit cleanly inside r<=74
        total_area = sum(math.pi * (r + 2.0) ** 2 for r in true_radii.values())
        max_packing_area = math.pi * (64.0 ** 2) * 0.56
        if total_area > max_packing_area and total_area > 0:
            shrink = max(0.62, math.sqrt(max_packing_area / total_area))
            for cid in list(true_radii.keys()):
                true_radii[cid] = max(6.5, true_radii[cid] * shrink)
                for n_idx in local_cluster_offsets[cid]:
                    local_cluster_offsets[cid][n_idx] = local_cluster_offsets[cid][n_idx] * shrink

        cluster_centers: Dict[int, np.ndarray] = {}

        if k_clusters == 1:
            cluster_centers[main_clusters[0]["id"]] = np.array([0.0, 0.0], dtype=np.float32)
        elif k_clusters == 2:
            sep = max(28.0, (true_radii[main_clusters[0]["id"]] + true_radii[main_clusters[1]["id"]]) * 0.7)
            cluster_centers[main_clusters[0]["id"]] = np.array([-sep, 0.0], dtype=np.float32)
            cluster_centers[main_clusters[1]["id"]] = np.array([sep, 0.0], dtype=np.float32)
        elif k_clusters > 2:
            # Sort clusters by their semantic PCA angle so related galaxies sit near each other
            cluster_angles: List[Tuple[float, Dict[str, Any]]] = []
            for c in main_clusters:
                cid = c["id"]
                c_indices = [i for i in range(n) if cluster_by_idx[i] == cid]
                if c_indices:
                    mean_pca = np.mean(pca_pts[c_indices], axis=0)
                    raw_angle = float(math.atan2(float(mean_pca[1]), float(mean_pca[0])))
                else:
                    raw_angle = 0.0
                cluster_angles.append((raw_angle, c))

            # C#1 (highest gravity/consensus cluster) ALWAYS anchors the Inner Core when k_clusters >= 3
            core_cluster = main_clusters[0]
            core_cid = core_cluster["id"]
            cluster_centers[core_cid] = np.array([0.0, 0.0], dtype=np.float32)
            orbit_clusters = [item for item in cluster_angles if item[1]["id"] != core_cid]

            orbit_clusters.sort(key=lambda x: x[0])
            weights = [true_radii[item[1]["id"]] + 4.0 for item in orbit_clusters]
            total_weight = sum(weights) or 1.0

            cum_angle = -math.pi / 2.0
            for idx_o, (_, c) in enumerate(orbit_clusters):
                cid = c["id"]
                sector = 2.0 * math.pi * (weights[idx_o] / total_weight)
                angle = cum_angle + sector * 0.5
                cum_angle += sector

                avg_trust = float(c.get("avg_trust", 70.0))
                trust_factor = max(0.0, min(1.0, (90.0 - avg_trust) / 45.0))
                min_orbit = true_radii[core_cid] + true_radii[cid] + 5.0
                base_r = max(min_orbit, 38.0 + trust_factor * 12.0 + (3.0 if idx_o % 2 == 1 else -2.0))
                base_r = min(base_r, 73.0 - true_radii[cid])
                cluster_centers[cid] = np.array(
                    [base_r * math.cos(angle), base_r * math.sin(angle)],
                    dtype=np.float32,
                )

            # Strict circle-packing relaxation using exact true_radii so galaxy circles NEVER overlap
            cids = [c["id"] for c in main_clusters]
            for _ in range(140):
                moved = False
                for i_c in range(len(cids)):
                    for j_c in range(i_c + 1, len(cids)):
                        id_a, id_b = cids[i_c], cids[j_c]
                        diff = cluster_centers[id_a] - cluster_centers[id_b]
                        dist = float(np.linalg.norm(diff))
                        min_sep = true_radii[id_a] + true_radii[id_b] + 3.2
                        if dist < min_sep:
                            moved = True
                            if dist < 1e-4:
                                u = np.array([1.0, 0.0], dtype=np.float32)
                            else:
                                u = diff / dist
                            push = (min_sep - dist) * 0.55
                            if id_a == core_cid:
                                cluster_centers[id_b] -= u * (push * 1.85)
                                cluster_centers[id_a] += u * (push * 0.15)
                            elif id_b == core_cid:
                                cluster_centers[id_a] += u * (push * 1.85)
                                cluster_centers[id_b] -= u * (push * 0.15)
                            else:
                                cluster_centers[id_a] += u * push
                                cluster_centers[id_b] -= u * push

                # Keep C#1 anchored near the core and all orbit cluster outer edges inside r <= 75
                core_norm = float(np.linalg.norm(cluster_centers[core_cid]))
                if core_norm > 6.0:
                    cluster_centers[core_cid] = cluster_centers[core_cid] * (6.0 / core_norm)

                for cid in cids:
                    if cid == core_cid:
                        continue
                    c_norm = float(np.linalg.norm(cluster_centers[cid]))
                    max_allowed_center = max(18.0, 74.5 - true_radii[cid])
                    if c_norm > max_allowed_center and c_norm > 1e-4:
                        cluster_centers[cid] = cluster_centers[cid] * (max_allowed_center / c_norm)
                if not moved:
                    break

        coords = np.zeros((n, 2), dtype=np.float32)
        outlier_indices: List[int] = []

        # Combine exact cluster centers with pre-resolved local offsets
        for c in main_clusters:
            cid = c["id"]
            if cid not in cluster_centers:
                continue
            c_center = cluster_centers[cid]
            for node_idx, offset_vec in local_cluster_offsets.get(cid, {}).items():
                coords[node_idx] = c_center + offset_vec

        for i in range(n):
            if cluster_by_idx[i] not in cluster_centers:
                outlier_indices.append(i)

        # Place uncorroborated outliers on the Outer Perimeter Ring (r = 81.0) sorted by semantic angle
        n_outliers = len(outlier_indices)
        if n_outliers > 0:
            orbit_r = 81.0
            outlier_indices.sort(
                key=lambda idx: math.atan2(float(pca_pts[idx][1]), float(pca_pts[idx][0]))
            )
            for out_rank, i in enumerate(outlier_indices):
                sem_angle = math.atan2(float(pca_pts[i][1]), float(pca_pts[i][0]))
                even_angle = (2.0 * math.pi * out_rank / n_outliers) - (math.pi / 2.0)
                angle = math.atan2(
                    0.25 * math.sin(sem_angle) + 0.75 * math.sin(even_angle),
                    0.25 * math.cos(sem_angle) + 0.75 * math.cos(even_angle),
                )
                coords[i] = np.array(
                    [orbit_r * math.cos(angle), orbit_r * math.sin(angle)],
                    dtype=np.float32,
                )

        # Update cluster centroids and exact radii in the cluster objects
        for c in clusters:
            if c["id"] == 0:
                c["center_x"] = 0.0
                c["center_y"] = 0.0
                c["radius"] = 81.0
                c["is_perimeter"] = True
                continue

            cid = c["id"]
            if cid in cluster_centers:
                cx = float(cluster_centers[cid][0])
                cy = float(cluster_centers[cid][1])
                c["center_x"] = round(cx, 2)
                c["center_y"] = round(cy, 2)
                c["radius"] = round(float(true_radii.get(cid, 12.0)), 2)
            else:
                c["center_x"] = 0.0
                c["center_y"] = 0.0
                c["radius"] = 12.0

        return [(round(float(x), 2), round(float(y), 2)) for x, y in coords]

    def compute_trustworthiness_and_corroboration(
        self,
        events: List[Event],
        embeddings: List[List[float]],
        similarity_threshold: Optional[float] = None,
    ) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]], List[Dict[str, Any]]]:
        """
        Compute pairwise context-calibrated similarity, agglomerative story clustering,
        and intelligence trustworthiness metrics.
         Combines neural cosine similarity with temporal proximity and shared entity/location anchors.
        Returns:
            - node_trust_metrics: List of dicts with trust scores, labels, nearest neighbors, etc.
            - edges: List of high-similarity link edges between reports within same story clusters
            - clusters: List of coherent topical story clusters ordered by gravity
        """
        n = len(events)
        if n == 0:
            return [], [], []

        arr = _homogenize_vectors(embeddings)
        norms = np.linalg.norm(arr, axis=1, keepdims=True)
        norms[norms == 0] = 1e-8
        norm_arr = arr / norms

        # Raw cosine similarity matrix: N x N
        raw_sim_matrix = np.dot(norm_arr, norm_arr.T)

        # Apply temporal & entity context adjustments so unrelated stories don't falsely corroborate
        sim_matrix = raw_sim_matrix.copy()
        for i in range(n):
            for j in range(i + 1, n):
                bonus = _compute_pairwise_context_bonus(events[i], events[j])
                adj_sim = float(np.clip(raw_sim_matrix[i, j] + bonus, 0.0, 1.0))
                sim_matrix[i, j] = adj_sim
                sim_matrix[j, i] = adj_sim

        # 1. Determine baseline similarity distribution to auto-calibrate thresholds
        off_diag_sims = raw_sim_matrix[~np.eye(n, dtype=bool)] if n > 1 else np.array([0.0])
        p50 = float(np.percentile(off_diag_sims, 50)) if len(off_diag_sims) > 0 else 0.0

        if similarity_threshold is not None:
            corrob_threshold = similarity_threshold
            cluster_threshold = max(0.22, similarity_threshold - 0.04)
            verified_threshold = max(0.25, similarity_threshold - 0.04)
            outlier_threshold = max(0.15, similarity_threshold - 0.16)
        elif p50 > 0.50:
            # Dense neural embeddings (Gemini 3072d / HF TEI)
            corrob_threshold = 0.775
            cluster_threshold = 0.768
            verified_threshold = 0.745
            outlier_threshold = 0.63
        else:
            # Sparse / bag-of-words / mock embeddings
            corrob_threshold = 0.38
            cluster_threshold = 0.31
            verified_threshold = 0.34
            outlier_threshold = 0.20

        # 2. Compute per-node Trustworthiness & Corroboration first
        node_metrics: List[Dict[str, Any]] = []
        edges: List[Dict[str, Any]] = []
        edge_set: Set[Tuple[int, int]] = set()

        for i in range(n):
            ev_i = events[i]
            similarities_list: List[Tuple[int, float]] = []

            for j in range(n):
                if i == j:
                    continue
                score = float(sim_matrix[i, j])
                similarities_list.append((j, score))

            similarities_list.sort(key=lambda x: x[1], reverse=True)
            top_neighbors = similarities_list[:4]
            max_similarity = top_neighbors[0][1] if top_neighbors else 0.0

            # Corroborating events: must exceed corrob_threshold
            corrob_idx = [j for j, s in similarities_list if s >= corrob_threshold]
            corrob_events = [events[j] for j in corrob_idx]

            from backend.app.pipeline.arabic_normalizer import classify_claim_stance
            cand_text_i = f"{ev_i.headline or ev_i.title or ''} {ev_i.summary or ev_i.translated_text or ev_i.raw_text or ''}".strip()

            unique_sources: Set[str] = {ev_i.source_name}
            unique_source_types: Set[str] = {ev_i.source_type}
            contradiction_count = 0
            contradiction_reasons: List[str] = []

            for c_ev in corrob_events:
                peer_text_c = f"{c_ev.headline or c_ev.title or ''} {c_ev.summary or c_ev.translated_text or c_ev.raw_text or ''}".strip()
                stance_info = classify_claim_stance(cand_text_i, peer_text_c)
                if stance_info.get("is_contradiction"):
                    contradiction_count += 1
                    contradiction_reasons.append(f"{c_ev.source_name}: {stance_info.get('explanation')}")
                else:
                    unique_sources.add(c_ev.source_name)
                    unique_source_types.add(c_ev.source_type)

            independent_sources = unique_sources - {ev_i.source_name}
            same_source_updates = sum(1 for c_ev in corrob_events if c_ev.source_name == ev_i.source_name)
            num_independent_sources = len(independent_sources)
            num_source_types = len(unique_source_types)

            # --- Granular Calibrated Trustworthiness Algorithm ---
            if contradiction_count > 0:
                trust_score = min(48, max(15, int(round(50 - contradiction_count * 10))))
                trust_label = "Contested / Disputed"
                verdict = f"Disputed report: Conflicting statements / official denial detected ({'; '.join(contradiction_reasons[:2])})."
            elif max_similarity < outlier_threshold:
                ratio = max(0.0, min(1.0, max_similarity / max(outlier_threshold, 0.01)))
                trust_score = int(round(18 + ratio * 18))
                trust_label = "Isolated Outlier"
                verdict = "Lone uncorroborated report. No matching intelligence found across any monitored source."
            elif num_independent_sources == 0 or max_similarity < verified_threshold:
                sim_factor = max(0.0, min(1.0, (max_similarity - outlier_threshold) / max(0.05, corrob_threshold - outlier_threshold)))
                trust_score = min(58, int(round(42 + min(6, same_source_updates * 3) + sim_factor * 10)))
                trust_label = "Single-Source Unverified"
                if same_source_updates >= 1:
                    verdict = f"Reported only by {ev_i.source_name} ({same_source_updates + 1} related posts on same feed). 0 independent outlets confirming this claim."
                else:
                    verdict = f"Reported only by {ev_i.source_name}. 0 independent cross-sources confirming this claim yet."
            elif num_independent_sources == 1:
                diversity_bonus = 4 if num_source_types >= 2 else 0
                sim_bonus = min(6, int(round(max(0.0, (max_similarity - corrob_threshold) / 0.14) * 6)))
                trust_score = min(79, 67 + diversity_bonus + sim_bonus)
                trust_label = "Developing / Verified"
                other_src = sorted(independent_sources)[0]
                verdict = f"Developing story confirmed across 2 independent outlets ({ev_i.source_name} and {other_src})."
            else:
                diversity_bonus = 6 if num_source_types >= 2 else 2
                sim_bonus = min(5, int(round(max(0.0, (max_similarity - corrob_threshold) / 0.14) * 5)))
                trust_score = min(98, 80 + min(11, (num_independent_sources - 2) * 3) + diversity_bonus + sim_bonus)
                trust_label = "Highly Corroborated"
                top_sources = [ev_i.source_name] + sorted(independent_sources)[:2]
                verdict = f"Multi-source consensus verified across {len(unique_sources)} independent outlets ({', '.join(top_sources)})."

            nearest_items = [
                {
                    "event_id": events[idx].id,
                    "title": events[idx].headline or events[idx].title,
                    "source_name": events[idx].source_name,
                    "similarity": round(sim, 3),
                }
                for idx, sim in top_neighbors
            ]

            node_metrics.append({
                "event_id": ev_i.id,
                "trustworthiness_score": trust_score,
                "trustworthiness_label": trust_label,
                "verdict": verdict,
                "corroboration_count": num_independent_sources,
                "same_source_updates": same_source_updates,
                "corroborating_sources": sorted(unique_sources),
                "independent_sources": sorted(independent_sources),
                "source_diversity_count": len(unique_sources),
                "nearest_neighbors": nearest_items,
                "max_similarity": round(max_similarity, 3),
                "centrality": 0.0,
                "is_cluster_lead": False,
                "is_disputed": contradiction_count > 0,
                "contradiction_reasons": contradiction_reasons,
                "recency_weight": round(compute_recency_weight(ev_i.published_at), 3),
            })

        # 3. Coherent Story Clustering via Cosine-Threshold Agglomerative Clustering
        if n <= 1:
            raw_labels = np.zeros(n, dtype=int)
        elif n == 2:
            if sim_matrix[0, 1] >= cluster_threshold:
                raw_labels = np.zeros(2, dtype=int)
            else:
                raw_labels = np.array([0, 1], dtype=int)
        else:
            dist_matrix = np.clip(1.0 - (sim_matrix + sim_matrix.T) * 0.5, 0.0, 2.0)
            np.fill_diagonal(dist_matrix, 0.0)
            dist_thresh = float(max(0.05, 1.0 - cluster_threshold))
            try:
                agg = AgglomerativeClustering(
                    n_clusters=None,
                    metric="precomputed",
                    linkage="average",
                    distance_threshold=dist_thresh,
                )
                raw_labels = agg.fit_predict(dist_matrix)
            except Exception:
                K = min(8, max(2, int(np.sqrt(n * 0.55))))
                km = KMeans(n_clusters=K, random_state=42, n_init="auto")
                raw_labels = km.fit_predict(norm_arr)

        # Group non-outlier nodes by discovered cluster label
        candidate_groups: Dict[int, List[int]] = {}
        outlier_indices_set: Set[int] = set()

        for idx in range(n):
            if node_metrics[idx]["trustworthiness_label"] == "Isolated Outlier":
                outlier_indices_set.add(idx)
            else:
                lbl = int(raw_labels[idx])
                candidate_groups.setdefault(lbl, []).append(idx)

        # Filter to true multi-report story clusters (size >= 2) and split any oversized bucket (> 20 nodes)
        valid_groups: List[List[int]] = []
        for lbl, members in candidate_groups.items():
            if len(members) < 2:
                outlier_indices_set.update(members)
            else:
                coherent_members = []
                for idx in members:
                    max_in_group = max(
                        (float(sim_matrix[idx, other]) for other in members if other != idx),
                        default=0.0,
                    )
                    if max_in_group >= outlier_threshold:
                        coherent_members.append(idx)
                    else:
                        outlier_indices_set.add(idx)

                if len(coherent_members) > 20:
                    # Sub-divide oversized generic clusters into tighter sub-stories
                    sub_k = max(2, int(math.ceil(len(coherent_members) / 14.0)))
                    try:
                        sub_km = KMeans(n_clusters=sub_k, random_state=42, n_init="auto")
                        sub_lbls = sub_km.fit_predict(norm_arr[coherent_members])
                        sub_buckets: Dict[int, List[int]] = {}
                        for pos_i, s_lbl in enumerate(sub_lbls):
                            sub_buckets.setdefault(int(s_lbl), []).append(coherent_members[pos_i])
                        for sb in sub_buckets.values():
                            if len(sb) >= 2:
                                valid_groups.append(sb)
                            else:
                                outlier_indices_set.update(sb)
                    except Exception:
                        valid_groups.append(coherent_members)
                elif len(coherent_members) >= 2:
                    valid_groups.append(coherent_members)
                else:
                    outlier_indices_set.update(coherent_members)

        # Cap at top 9 primary story galaxies ranked by gravity; do NOT force-merge unrelated overflow clusters
        now_dt = utc_now()

        def _group_gravity(group_idxs: List[int]) -> float:
            sz = len(group_idxs)
            avg_t = sum(node_metrics[i]["trustworthiness_score"] for i in group_idxs) / sz
            avg_imp = sum((getattr(events[i], "importance_score", 5) or 5) for i in group_idxs) / sz
            recency_weights = [
                compute_recency_weight(events[i].published_at, now=now_dt, half_life_hours=24.0, min_weight=0.20)
                for i in group_idxs
            ]
            avg_recency = sum(recency_weights) / sz
            return sz * (0.55 + 0.45 * (avg_t / 100.0)) * (0.65 + 0.35 * (avg_imp / 10.0)) * (0.50 + 0.50 * avg_recency)

        valid_groups.sort(key=_group_gravity, reverse=True)
        if len(valid_groups) > 9:
            primary_groups = valid_groups[:9]
            overflow_groups = valid_groups[9:]
            primary_centroids = [
                np.mean(norm_arr[g], axis=0) / (np.linalg.norm(np.mean(norm_arr[g], axis=0)) + 1e-8)
                for g in primary_groups
            ]
            for ov_g in overflow_groups:
                ov_vec = np.mean(norm_arr[ov_g], axis=0)
                ov_vec = ov_vec / (np.linalg.norm(ov_vec) + 1e-8)
                sims_to_prim = [float(np.dot(ov_vec, pc)) for pc in primary_centroids]
                best_k = int(np.argmax(sims_to_prim))
                # Only merge into a primary cluster if genuinely same story AND won't bloat past 20 nodes
                if sims_to_prim[best_k] >= cluster_threshold and len(primary_groups[best_k]) + len(ov_g) <= 20:
                    primary_groups[best_k].extend(ov_g)
                else:
                    outlier_indices_set.update(ov_g)
            valid_groups = primary_groups
            valid_groups.sort(key=_group_gravity, reverse=True)

        # --- Build Final Clusters Metadata & Select Medoid Headlines ---
        clusters: List[Dict[str, Any]] = []
        for cluster_id_counter, comp_indices in enumerate(valid_groups, start=1):
            # Compute intra-cluster centrality for each member to find the true Medoid
            best_lead_idx = comp_indices[0]
            best_lead_score = -1.0

            for idx in comp_indices:
                if len(comp_indices) > 1:
                    centrality = sum(
                        float(sim_matrix[idx, other]) for other in comp_indices if other != idx
                    ) / (len(comp_indices) - 1)
                else:
                    centrality = 1.0
                node_metrics[idx]["centrality"] = round(centrality, 3)

                imp = float(getattr(events[idx], "importance_score", 5) or 5) / 10.0
                trust = float(node_metrics[idx]["trustworthiness_score"]) / 100.0
                lead_score = centrality * 0.55 + imp * 0.25 + trust * 0.20
                if lead_score > best_lead_score:
                    best_lead_score = lead_score
                    best_lead_idx = idx

            node_metrics[best_lead_idx]["is_cluster_lead"] = True
            lead_event = events[best_lead_idx]
            raw_title = lead_event.headline or lead_event.title
            cluster_label = truncate_at_word_boundary(raw_title, max_len=44)

            avg_trust = int(
                round(
                    sum(node_metrics[idx]["trustworthiness_score"] for idx in comp_indices)
                    / len(comp_indices)
                )
            )
            avg_imp = round(
                sum((getattr(events[idx], "importance_score", 5) or 5) for idx in comp_indices)
                / len(comp_indices),
                1,
            )

            # Cluster freshness metrics
            comp_dates = [events[idx].published_at for idx in comp_indices if events[idx].published_at]
            if comp_dates:
                norm_dates = [d.replace(tzinfo=timezone.utc) if d.tzinfo is None else d for d in comp_dates]
                latest_pub = max(norm_dates)
                age_hours = round(max(0.0, (now_dt - latest_pub).total_seconds() / 3600.0), 1)
                is_fresh = age_hours <= 12.0
                cluster_recency = round(compute_recency_weight(latest_pub, now=now_dt, half_life_hours=24.0), 3)
            else:
                age_hours = 999.0
                is_fresh = False
                cluster_recency = 0.10

            for idx in comp_indices:
                node_metrics[idx]["cluster_id"] = cluster_id_counter
                node_metrics[idx]["cluster_label"] = cluster_label

            clusters.append({
                "id": cluster_id_counter,
                "label": cluster_label,
                "size": len(comp_indices),
                "center_x": 0.0,
                "center_y": 0.0,
                "radius": 15.0,
                "avg_trust": avg_trust,
                "avg_importance": avg_imp,
                "lead_event_id": lead_event.id,
                "event_ids": [events[idx].id for idx in comp_indices],
                "is_fresh": is_fresh,
                "latest_event_age_hours": age_hours,
                "cluster_recency_weight": cluster_recency,
            })

        outlier_events = [events[idx] for idx in sorted(outlier_indices_set)]
        for idx in outlier_indices_set:
            node_metrics[idx]["cluster_id"] = 0
            node_metrics[idx]["cluster_label"] = "Uncorroborated Perimeter"

        if outlier_events:
            clusters.append({
                "id": 0,
                "label": "Uncorroborated Perimeter",
                "size": len(outlier_events),
                "center_x": 0.0,
                "center_y": 0.0,
                "radius": 81.0,
                "is_perimeter": True,
                "avg_trust": int(
                    round(
                        sum(node_metrics[idx]["trustworthiness_score"] for idx in outlier_indices_set)
                        / len(outlier_indices_set)
                    )
                ),
                "event_ids": [e.id for e in outlier_events],
                "is_fresh": False,
                "latest_event_age_hours": 999.0,
                "cluster_recency_weight": 0.10,
            })

        # --- Curated Intra-Cluster Edges: STRICTLY Cross-Source Corroboration ---
        for i in range(n):
            ev_i = events[i]
            c_i = node_metrics[i].get("cluster_id")
            if c_i == 0:
                continue
            sims_in_cluster = []
            for j in range(n):
                if i == j:
                    continue
                # Strictly cross-source! Do not connect a source to itself as corroboration
                if events[i].source_name == events[j].source_name:
                    continue
                score = float(sim_matrix[i, j])
                c_j = node_metrics[j].get("cluster_id")
                if score >= corrob_threshold and (c_i == c_j or score >= 0.84):
                    sims_in_cluster.append((j, score))

            sims_in_cluster.sort(key=lambda x: x[1], reverse=True)
            for j, score in sims_in_cluster[:2]:
                edge_pair = (min(i, j), max(i, j))
                if edge_pair not in edge_set:
                    edge_set.add(edge_pair)
                    edges.append({
                        "source": ev_i.id,
                        "target": events[j].id,
                        "similarity": round(score, 3),
                        "intra_cluster": c_i == node_metrics[j].get("cluster_id"),
                    })

        return node_metrics, edges, clusters

    async def get_relevance_map_data(
        self,
        session: AsyncSession,
        theme_id: Optional[Union[str, int]] = None,
        time_range_hours: Optional[int] = None,
        min_score: Optional[int] = None,
        limit: int = 150,
        force_reproject: bool = False,
    ) -> Dict[str, Any]:
        """
        Build the complete Relevance Map payload for the frontend:
        Queries events, auto-embeds un-embedded records if needed,
        computes Galaxy 2D coordinates & trustworthiness, and saves projections.
        """
        query = select(Event).order_by(Event.published_at.desc())
        target_theme: Optional[Theme] = None

        if theme_id is not None and str(theme_id).lower() != "all":
            from backend.app.api.themes import find_theme
            target_theme = await find_theme(session, str(theme_id))
            if target_theme:
                query = query.where(Event.theme_id == target_theme.id)
            elif str(theme_id).isdigit():
                query = query.where(Event.theme_id == int(theme_id))

        if time_range_hours:
            cutoff = utc_now() - timedelta(hours=time_range_hours)
            query = query.where(Event.published_at >= cutoff)

        if min_score:
            if min_score <= 4:
                # Exclude explicitly low-scored noise (1-3) while preserving unanalyzed live events
                query = query.where(
                    (Event.importance_score >= min_score) | (Event.importance_score.is_(None))
                )
            else:
                query = query.where(Event.importance_score >= min_score)

        # Fetch slightly larger pool if we need to filter off-theme articles in Python
        fetch_limit = limit * 2 if (target_theme or min_score) else limit
        events: List[Event] = list((await session.execute(query.limit(fetch_limit))).scalars().all())

        # If target_theme was not explicitly passed, check if all events belong to a single theme
        if target_theme is None and events and min_score and min_score >= 4:
            first_tid = events[0].theme_id
            if first_tid:
                target_theme = (
                    await session.execute(select(Theme).where(Theme.id == first_tid))
                ).scalars().first()

        # Filter out off-theme conflict noise (e.g., Russia-Ukraine stories inside US-Iran conflict feed)
        if target_theme and (min_score or (theme_id and str(theme_id).lower() != "all")):
            t_name = (target_theme.name or "").lower()
            if "iran" in t_name or "middle east" in t_name or "hormuz" in t_name:
                mideast_anchors = (
                    "iran", "iranian", "tehran", "irgc", "israel", "israeli", "idf",
                    "hezbollah", "lebanon", "beirut", "houthi", "yemen", "sanaa", "taiz",
                    "red sea", "hormuz", "persian gulf", "gaza", "hamas", "syria",
                    "damascus", "iraq", "baghdad", "centcom", "pentagon", "middle east",
                    "khamenei", "pezeshkian", "netanyahu", "natantz", "fordow", "nuclear",
                )
                off_theme_ukraine_russia = (
                    "zelensky", "zelenskyy", "ukraine", "ukrainian", "kyiv", "kharkiv",
                    "donbas", "crimea", "zaporizhzhia", "kremlin", "putin", "moscow",
                )
                filtered_events: List[Event] = []
                for ev in events:
                    hl_lower = f"{ev.headline or ''} {ev.title or ''}".lower()
                    full_lower = f"{hl_lower} {ev.summary or ''} {ev.raw_text or ''}".lower()
                    has_off_theme_hl = any(w in hl_lower for w in off_theme_ukraine_russia)
                    has_mideast_anchor = any(a in full_lower for a in mideast_anchors)
                    if has_off_theme_hl and not has_mideast_anchor:
                        continue
                    filtered_events.append(ev)
                if filtered_events:
                    events = filtered_events

        events = events[:limit]

        if not events:
            return {
                "nodes": [],
                "edges": [],
                "clusters": [],
                "stats": {
                    "total_nodes": 0,
                    "coverage_pct": 100,
                    "active_model": self.default_model,
                },
            }

        # 1. Fetch or generate embeddings
        embeddings = await self.embed_events(events, session)
        emb_by_event_id = {emb.event_id: emb for emb in embeddings}

        # Order matching events
        valid_events = [e for e in events if e.id in emb_by_event_id]
        vectors = [emb_by_event_id[e.id].embedding_vector for e in valid_events]

        # 2. Compute trustworthiness and cluster metrics
        node_metrics, edges, clusters = self.compute_trustworthiness_and_corroboration(
            valid_events, vectors
        )

        # 3. Compute Galaxy 2D coordinates with collision avoidance
        coords_2d = self.compute_galaxy_coordinates(vectors, clusters, node_metrics)
        if len(vectors) > 1:
            try:
                pca = PCA(n_components=2, random_state=42)
                pca.fit(_homogenize_vectors(vectors))
                self.last_pca_model = pca
            except Exception:
                self.last_pca_model = None
        else:
            self.last_pca_model = None

        # 4. Update coordinates and trust metrics in database
        nodes: List[Dict[str, Any]] = []
        for i, (ev, (x, y)) in enumerate(zip(valid_events, coords_2d)):
            m = node_metrics[i]
            rec = emb_by_event_id[ev.id]
            rec.x_coord = x
            rec.y_coord = y
            rec.trustworthiness_score = m["trustworthiness_score"]
            rec.corroboration_count = m["corroboration_count"]
            rec.cluster_id = m.get("cluster_id")
            rec.cluster_label = m.get("cluster_label")

            nodes.append({
                "id": ev.id,
                "event_id": ev.id,
                "title": ev.title,
                "headline": ev.headline or ev.title,
                "summary": ev.summary or ev.raw_text[:250],
                "source_name": ev.source_name,
                "source_type": ev.source_type,
                "source_url": getattr(ev, "source_url", None),
                "published_at": ev.published_at.isoformat() if ev.published_at else None,
                "importance_score": ev.importance_score or 5,
                "location": ev.location_json,
                "x": x,
                "y": y,
                "cluster_id": m.get("cluster_id"),
                "cluster_label": m.get("cluster_label"),
                "is_cluster_lead": bool(m.get("is_cluster_lead", False)),
                "centrality": m.get("centrality", 0.0),
                "trustworthiness": {
                    "score": m["trustworthiness_score"],
                    "label": m["trustworthiness_label"],
                    "verdict": m["verdict"],
                    "corroboration_count": m["corroboration_count"],
                    "corroborating_sources": m["corroborating_sources"],
                    "independent_sources": m["independent_sources"],
                    "same_source_updates": m["same_source_updates"],
                    "source_diversity": m["source_diversity_count"],
                },
                "nearest_neighbors": m["nearest_neighbors"],
            })

        await session.commit()

        # Compute cluster centroids for main galaxies (do not overwrite Perimeter ring id=0)
        for c in clusters:
            if c["id"] == 0 or c.get("is_perimeter"):
                c["center_x"] = 0.0
                c["center_y"] = 0.0
                c["radius"] = 81.0
                continue
            c_nodes = [n for n in nodes if n["cluster_id"] == c["id"]]
            if c_nodes:
                c["center_x"] = round(sum(n["x"] for n in c_nodes) / len(c_nodes), 2)
                c["center_y"] = round(sum(n["y"] for n in c_nodes) / len(c_nodes), 2)
            else:
                c["center_x"] = 0.0
                c["center_y"] = 0.0

        # Stats summary
        quota_data = await self.get_quota_tracker(session)
        stats = {
            "total_nodes": len(nodes),
            "total_edges": len(edges),
            "total_clusters": len(clusters),
            "active_model": embeddings[0].model_name if embeddings else self.default_model,
            "quota_tracker": quota_data,
        }

        return {
            "nodes": nodes,
            "edges": edges,
            "clusters": clusters,
            "stats": stats,
        }

    async def backfill_all_events(
        self, session: AsyncSession, limit: int = 100, use_mock: bool = False
    ) -> Dict[str, Any]:
        """
        Scan database for events without embeddings and batch-embed them.
        """
        # Find events without embeddings
        stmt = (
            select(Event)
            .outerjoin(EventEmbedding, Event.id == EventEmbedding.event_id)
            .where(EventEmbedding.id == None)
            .order_by(Event.published_at.desc())
            .limit(limit)
        )
        missing_events = list((await session.execute(stmt)).scalars().all())

        if not missing_events:
            return {
                "status": "up_to_date",
                "processed_count": 0,
                "message": "All events already have cached embeddings.",
            }

        logger.info(f"Backfilling embeddings for {len(missing_events)} events...")
        embs = await self.embed_events(missing_events, session, use_mock=use_mock)

        # Trigger re-projection
        await self.get_relevance_map_data(session, limit=150, force_reproject=True)

        return {
            "status": "success",
            "processed_count": len(embs),
            "message": f"Successfully generated embeddings for {len(embs)} events.",
        }

    async def compute_live_scoring_neighborhood(
        self,
        session: AsyncSession,
        candidate_event_id: str,
        theme_id: int,
        title: str,
        intelligence_text: str,
        extracted_entities: List[str],
        source_name: str,
        source_type: str = "rss",
        published_at: Optional[datetime] = None,
        location_json: Optional[Any] = None,
        alert_threshold: int = 7,
        use_mock: bool = False,
        lookback_hours: int = 48,
        max_recent_events: int = 80,
    ) -> Dict[str, Any]:
        """
        Pre-Stage 2 inline semantic embedding & situational context lookup.
        1. Embeds the incoming candidate event using its Stage 1 English translation and entities.
        2. Queries recent analyzed events and their cached EventEmbeddings in the active theme.
        3. Computes context-calibrated cosine similarity + Jaccard echo detection to find:
           - Top semantically related recent reports
           - Independent corroborating sources & event IDs
           - Same-source echo / repetitive update counts
           - 6h / 24h theater reporting velocity and active high-priority storylines
        """
        from backend.app.pipeline.arabic_normalizer import compute_jaccard_similarity

        now = utc_now()
        pub_dt = published_at or now
        if isinstance(pub_dt, str):
            try:
                pub_dt = datetime.fromisoformat(pub_dt.replace("Z", "+00:00"))
            except Exception:
                pub_dt = now
        if pub_dt.tzinfo is None:
            pub_dt = pub_dt.replace(tzinfo=timezone.utc)

        # Build a lightweight transient Event object for semantic text & pairwise context bonus
        temp_event = Event(
            id=candidate_event_id,
            theme_id=theme_id,
            source_type=source_type,
            source_name=source_name,
            external_id=candidate_event_id,
            title=title,
            headline=title,
            raw_text=intelligence_text,
            translated_text=intelligence_text,
            summary=intelligence_text[:450] if intelligence_text else title,
            entities_json=list(extracted_entities or []),
            location_json=location_json if isinstance(location_json, dict) else None,
            published_at=pub_dt,
            ingested_at=now,
        )

        # 1. Generate semantic embedding for candidate event
        sem_text = build_semantic_embedding_text(temp_event)
        cand_vectors, model_used = await self.generate_embeddings_batch(
            texts=[sem_text],
            use_mock=use_mock,
            session=session,
        )
        cand_vec = cand_vectors[0] if cand_vectors else generate_deterministic_mock_embedding(sem_text)

        # 2. Fetch recent analyzed events in this theme (excluding candidate itself)
        cutoff_lookback = now - timedelta(hours=lookback_hours)
        stmt_recent = (
            select(Event)
            .where(
                (Event.theme_id == theme_id)
                & (Event.id != candidate_event_id)
                & (Event.is_analyzed == True)
                & (Event.published_at >= cutoff_lookback)
            )
            .order_by(Event.published_at.desc())
            .limit(max_recent_events)
        )
        recent_events: List[Event] = list((await session.execute(stmt_recent)).scalars().all())

        # If no events within 48h (e.g. seeded historical test data), fall back to latest analyzed events in theme
        if not recent_events:
            stmt_fallback = (
                select(Event)
                .where((Event.theme_id == theme_id) & (Event.id != candidate_event_id) & (Event.is_analyzed == True))
                .order_by(Event.published_at.desc())
                .limit(min(30, max_recent_events))
            )
            recent_events = list((await session.execute(stmt_fallback)).scalars().all())

        # 3. Compute 6h / 24h theater velocity and active storylines
        since_6h = now - timedelta(hours=6)
        since_24h = now - timedelta(hours=24)
        events_last_6h = 0
        events_prev_18h = 0
        critical_alerts_last_6h = 0
        scores_24h: List[int] = []
        active_storylines: List[str] = []
        seen_storyline_hl: Set[str] = set()

        for rev in recent_events:
            r_score = int(rev.importance_score or 5)
            r_pub = rev.published_at
            if r_pub:
                if r_pub.tzinfo is None:
                    r_pub = r_pub.replace(tzinfo=timezone.utc)
                if r_pub >= since_6h:
                    events_last_6h += 1
                    if r_score >= alert_threshold:
                        critical_alerts_last_6h += 1
                elif r_pub >= since_24h:
                    events_prev_18h += 1
                if r_pub >= since_24h:
                    scores_24h.append(r_score)
            else:
                scores_24h.append(r_score)

            if r_score >= alert_threshold and len(active_storylines) < 4:
                hl = (rev.headline or rev.title or "").strip()
                if hl and hl.lower() not in seen_storyline_hl:
                    seen_storyline_hl.add(hl.lower())
                    active_storylines.append(f"[{rev.source_name} | Score {r_score}/10] {hl}")

        rate_6h = events_last_6h / 6.0
        rate_prev_18h = max(0.2, events_prev_18h / 18.0)
        if events_last_6h >= 6 and rate_6h >= rate_prev_18h * 1.6:
            velocity_status = "Surge"
        elif events_last_6h >= 3 and rate_6h >= rate_prev_18h * 1.15:
            velocity_status = "Elevated"
        elif not recent_events:
            velocity_status = "Quiet"
        else:
            velocity_status = "Normal"

        avg_score_24h = round(sum(scores_24h) / len(scores_24h), 1) if scores_24h else 5.0

        if not recent_events:
            return {
                "embedding_vector": cand_vec,
                "embedding_model_name": model_used,
                "related_reports": [],
                "corroboration_count": 0,
                "corroborating_sources": [],
                "corroborating_event_ids": [],
                "recency_weighted_corroboration": 0.0,
                "is_contradiction": False,
                "contradicting_sources": [],
                "contradicting_event_ids": [],
                "contradiction_details": [],
                "same_source_echo_count": 0,
                "is_same_source_echo": False,
                "max_similarity": 0.0,
                "corroboration_status": "First Report / Unverified",
                "velocity_status": velocity_status,
                "events_last_6h": events_last_6h,
                "critical_alerts_last_6h": critical_alerts_last_6h,
                "avg_score_24h": avg_score_24h,
                "active_storylines": active_storylines,
                "historical_precedents": [],
                "entity_baselines": {},
            }

        # 4. Retrieve or compute embeddings for recent peer events
        recent_ids = [r.id for r in recent_events]
        stmt_embs = select(EventEmbedding).where(EventEmbedding.event_id.in_(recent_ids))
        existing_embs = list((await session.execute(stmt_embs)).scalars().all())
        emb_map: Dict[str, List[float]] = {e.event_id: e.embedding_vector for e in existing_embs if e.embedding_vector}

        missing_peers = [r for r in recent_events if r.id not in emb_map]
        if missing_peers:
            # Generate deterministic embeddings for any un-embedded peers to avoid blocking stage latency
            for mp in missing_peers:
                mp_text = build_semantic_embedding_text(mp)
                emb_map[mp.id] = generate_deterministic_mock_embedding(mp_text, dim=len(cand_vec) or 3072)

        peer_vectors = [emb_map[r.id] for r in recent_events]
        all_vecs = _homogenize_vectors([cand_vec] + peer_vectors)
        norms = np.linalg.norm(all_vecs, axis=1, keepdims=True)
        norms[norms == 0] = 1e-8
        norm_vecs = all_vecs / norms

        cand_norm = norm_vecs[0]
        peer_norms = norm_vecs[1:]
        raw_sims = np.dot(peer_norms, cand_norm)

        # Determine threshold calibration (dense neural vs sparse/mock vectors)
        is_mock_vec = model_used in ("mock-embedding-3072", "mock-fallback") or use_mock
        p50 = float(np.percentile(raw_sims, 50)) if len(raw_sims) > 0 else 0.0
        if not is_mock_vec and p50 > 0.45:
            corrob_threshold = 0.775
            echo_threshold = 0.81
            outlier_threshold = 0.63
        else:
            corrob_threshold = 0.38
            echo_threshold = 0.44
            outlier_threshold = 0.20

        cand_tokens = _extract_event_Lookup_tokens(temp_event)
        cand_clean_text = f"{title} {intelligence_text}".strip()

        from backend.app.pipeline.arabic_normalizer import classify_claim_stance

        scored_peers: List[Tuple[Event, float, float]] = []
        corroborating_sources_set: Set[str] = set()
        corroborating_event_ids: List[str] = []
        contradicting_sources_set: Set[str] = set()
        contradicting_event_ids: List[str] = []
        contradiction_details: List[str] = []
        same_source_echo_count = 0

        for idx, peer in enumerate(recent_events):
            raw_s = float(raw_sims[idx])
            bonus = _compute_pairwise_context_bonus(temp_event, peer)
            adj_s = float(np.clip(raw_s + bonus, 0.0, 1.0))

            peer_tokens = _extract_event_Lookup_tokens(peer)
            shared_tokens = len(cand_tokens.intersection(peer_tokens)) if (cand_tokens and peer_tokens) else 0

            # Check Jaccard similarity for near-duplicate text echo
            peer_clean_text = f"{peer.headline or peer.title or ''} {peer.translated_text or peer.summary or peer.raw_text or ''}".strip()
            jaccard_sim = compute_jaccard_similarity(cand_clean_text, peer_clean_text, n=3)
            effective_sim = max(adj_s, jaccard_sim)

            scored_peers.append((peer, effective_sim, jaccard_sim))

            # Check claim stance: contradiction/denial vs corroboration
            stance_info = classify_claim_stance(cand_clean_text, peer_clean_text)
            is_contradiction = stance_info.get("is_contradiction", False)

            is_same_source = (peer.source_name or "").strip().lower() == (source_name or "").strip().lower()
            if is_same_source:
                if effective_sim >= echo_threshold or jaccard_sim >= 0.75 or (adj_s >= corrob_threshold and shared_tokens >= 2):
                    same_source_echo_count += 1
            else:
                if is_contradiction and effective_sim >= outlier_threshold:
                    if peer.source_name:
                        contradicting_sources_set.add(peer.source_name)
                    contradicting_event_ids.append(peer.id)
                    contradiction_details.append(
                        f"[{peer.source_name}] {stance_info.get('explanation', 'Denial/contradiction')} "
                        f"(Ref: '{peer.headline or peer.title}')"
                    )
                elif effective_sim >= corrob_threshold or (adj_s >= corrob_threshold - 0.03 and shared_tokens >= 2):
                    if peer.source_name:
                        corroborating_sources_set.add(peer.source_name)
                    corroborating_event_ids.append(peer.id)

        scored_peers.sort(key=lambda x: x[1], reverse=True)
        max_similarity = scored_peers[0][1] if scored_peers else 0.0

        # Calculate recency-weighted corroboration score across independent corroborating sources
        corrob_events_list = [p for p in recent_events if p.id in corroborating_event_ids]
        recency_weighted_corrob = sum(
            compute_recency_weight(p.published_at, now=now, half_life_hours=18.0)
            for p in corrob_events_list
        )

        related_reports: List[Dict[str, Any]] = []
        for peer, sim_val, _ in scored_peers[:4]:
            if sim_val >= outlier_threshold:
                p_rec = compute_recency_weight(peer.published_at, now=now, half_life_hours=18.0)
                peer_pub_iso = peer.published_at.isoformat() if peer.published_at else None
                related_reports.append({
                    "event_id": peer.id,
                    "headline": peer.headline or peer.title,
                    "source_name": peer.source_name,
                    "source_type": peer.source_type,
                    "importance_score": int(peer.importance_score or 5),
                    "similarity": round(sim_val, 3),
                    "recency_weight": round(p_rec, 3),
                    "published_at": peer_pub_iso,
                })

        corrob_count = len(corroborating_sources_set)
        is_same_source_echo = same_source_echo_count >= 1

        if len(contradicting_sources_set) > 0:
            corrob_status = "Disputed / Conflicting Reports"
        elif is_same_source_echo and corrob_count == 0:
            corrob_status = "Same-Source Echo"
        elif corrob_count >= 2:
            corrob_status = "Highly Corroborated"
        elif corrob_count == 1:
            corrob_status = "Developing / Verified"
        elif max_similarity < outlier_threshold and len(recent_events) >= 3:
            corrob_status = "Isolated Outlier"
        else:
            corrob_status = "First Report / Unverified"

        # 5. Query historical rollup precedents and baselines
        historical_context = []
        entity_baselines = {}
        try:
            from backend.app.services.memory_rollup_service import get_memory_rollup_service
            rollup_svc = get_memory_rollup_service()
            loc_str = ""
            if isinstance(location_json, dict):
                loc_str = str(location_json.get("name") or location_json.get("country") or "").strip()
            elif isinstance(location_json, str):
                loc_str = location_json.strip()
            hist_res = await rollup_svc.get_historical_context_and_baselines(
                session=session,
                theme_id=theme_id,
                candidate_entities=list(extracted_entities or []),
                candidate_location=loc_str,
            )
            historical_context = hist_res.get("precedents", [])
            entity_baselines = hist_res.get("entity_baselines", {})
        except Exception as e:
            logger.debug(f"[neighborhood] Historical rollups query omitted: {e}")

        return {
            "embedding_vector": cand_vec,
            "embedding_model_name": model_used,
            "related_reports": related_reports,
            "corroboration_count": corrob_count,
            "corroborating_sources": sorted(corroborating_sources_set),
            "corroborating_event_ids": corroborating_event_ids,
            "recency_weighted_corroboration": round(recency_weighted_corrob, 2),
            "is_contradiction": len(contradicting_sources_set) > 0,
            "contradicting_sources": sorted(contradicting_sources_set),
            "contradicting_event_ids": contradicting_event_ids,
            "contradiction_details": contradiction_details,
            "same_source_echo_count": same_source_echo_count,
            "is_same_source_echo": is_same_source_echo,
            "max_similarity": round(max_similarity, 3),
            "corroboration_status": corrob_status,
            "velocity_status": velocity_status,
            "events_last_6h": events_last_6h,
            "critical_alerts_last_6h": critical_alerts_last_6h,
            "avg_score_24h": avg_score_24h,
            "active_storylines": active_storylines,
            "historical_precedents": historical_context,
            "entity_baselines": entity_baselines,
        }


# Global singleton instance
_embedding_service: Optional[EmbeddingService] = None


def get_embedding_service() -> EmbeddingService:
    global _embedding_service
    if _embedding_service is None:
        _embedding_service = EmbeddingService()
    return _embedding_service

