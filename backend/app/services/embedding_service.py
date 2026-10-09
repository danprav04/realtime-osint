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
import time
from datetime import datetime, timezone, timedelta
from typing import Any, Dict, List, Optional, Set, Tuple, Union

import httpx
import numpy as np
from sklearn.decomposition import PCA
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.config import get_settings
from backend.app.db.models import Event, EventEmbedding, SystemSettings, Theme, utc_now

logger = logging.getLogger("osint.services.embedding")

GEMINI_BASE_URL = "https://generativelanguage.googleapis.com/v1beta/models"

# Available Gemini embedding models matching the user's quota tiers:
# Gemini Embedding 1 -> models/gemini-embedding-001 (100 RPM, 30K TPM, 1,000 RPD)
# Gemini Embedding 2 -> models/gemini-embedding-2   (100 RPM, 30K TPM, 1,000 RPD)
DEFAULT_EMBEDDING_MODEL = "gemini-embedding-001"
FALLBACK_EMBEDDING_MODEL = "gemini-embedding-2"

# Safe batch and pacing constants
MAX_ITEMS_PER_BATCH = 20  # Keep batch reasonable to stay well within 30K TPM
MAX_TEXT_CHAR_LENGTH = 1500  # ~350 tokens max per article
INTER_BATCH_DELAY_SEC = 1.0  # Polite pause between batches to protect RPM

QUOTA_SETTINGS_KEY = "gemini_embeddings_quota_tracker"


def get_today_utc_date_str() -> str:
    """Return current UTC date in YYYY-MM-DD format."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


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


def generate_deterministic_mock_embedding(text: str, dim: int = 3072) -> List[float]:
    """
    Generate a deterministic bag-of-words pseudo-random unit vector.
    Texts sharing key vocabulary will yield realistic cosine similarities.
    """
    cleaned = text.lower().strip()
    words = [w.strip(".,;:!?\"'()[]{}") for w in cleaned.split() if w.strip(".,;:!?\"'()[]{}")]

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


class EmbeddingService:
    """
    Central service for generating embeddings via Gemini batch API,
    caching vectors in SQLite/Postgres, tracking rate limits,
    computing PCA 2D coordinates, and scoring trustworthiness.
    """

    def __init__(self, api_key: Optional[str] = None):
        self._api_key = api_key or os.getenv("GEMINI_API_KEY")
        self.default_model = DEFAULT_EMBEDDING_MODEL
        self.fallback_model = FALLBACK_EMBEDDING_MODEL
        self.last_pca_model: Optional[PCA] = None

    @property
    def api_key(self) -> Optional[str]:
        if not self._api_key:
            self._api_key = os.getenv("GEMINI_API_KEY")
        return self._api_key

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
        Execute a single POST request to Google Gemini's batchEmbedContents endpoint.
        Returns list of float vectors (3072d).
        """
        url = f"{GEMINI_BASE_URL}/{model}:batchEmbedContents?key={api_key}"
        payload = {
            "requests": [
                {
                    "model": f"models/{model}",
                    "content": {"parts": [{"text": text}]},
                }
                for text in texts
            ]
        }

        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.post(url, json=payload)
            if resp.status_code == 200:
                data = resp.json()
                raw_embs = data.get("embeddings", [])
                vectors = [item.get("values", []) for item in raw_embs]
                if len(vectors) != len(texts):
                    raise ValueError(
                        f"Expected {len(texts)} embeddings, received {len(vectors)}"
                    )
                return vectors
            elif resp.status_code == 429:
                err_text = resp.text
                logger.warning(f"Gemini API rate limit 429 for model {model}: {err_text}")
                raise RuntimeError(f"QUOTA_EXCEEDED: {err_text}")
            else:
                raise RuntimeError(
                    f"Gemini API error {resp.status_code}: {resp.text[:300]}"
                )

    async def call_hf_tei_api(
        self, texts: List[str], endpoint_url: str, api_key: Optional[str] = None
    ) -> List[List[float]]:
        """
        Execute batch request to a private Hugging Face Space running Text Embeddings Inference (TEI).
        Supports native TEI (/embed) and OpenAI-compatible (/v1/embeddings).
        Zero rate limits, runs 24/7 on free HF CPU Basic tier (2 vCPUs, 16 GB RAM).
        """
        clean_url = endpoint_url.strip().rstrip("/")
        if not clean_url.endswith("/embed") and not clean_url.endswith("/v1/embeddings"):
            url = f"{clean_url}/embed"
        else:
            url = clean_url

        headers = {"Content-Type": "application/json"}
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"

        if url.endswith("/v1/embeddings"):
            payload = {"input": texts, "model": "sentence-transformers/all-MiniLM-L6-v2"}
        else:
            payload = {"inputs": texts, "truncate": True}

        async with httpx.AsyncClient(timeout=45.0) as client:
            resp = await client.post(url, json=payload, headers=headers)
            if resp.status_code == 200:
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
            else:
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
        Handles failover from Gemini Embedding 1 -> Gemini Embedding 2 -> HF Space TEI -> Mock.
        Returns (list_of_embeddings, model_name_used).
        """
        if not texts:
            return [], self.default_model

        active_key = self.api_key
        hf_url = os.getenv("HF_EMBEDDING_URL")
        hf_token = os.getenv("HF_API_KEY")

        # Check if HF Space is explicitly requested as primary
        if preferred_model in ("hf-space-tei", "huggingface") and hf_url:
            try:
                vectors = await self.call_hf_tei_api(texts, hf_url, hf_token)
                return vectors, "hf-space-tei"
            except Exception as e:
                logger.warning(f"HF Space primary request failed ({e}), falling back to Gemini...")

        if use_mock or not active_key or active_key.startswith("mock_"):
            logger.info("Using deterministic mock embeddings (mock mode or no API key)")
            results = [generate_deterministic_mock_embedding(t) for t in texts]
            return results, "mock-embedding-3072"

        primary = preferred_model or self.default_model
        secondary = (
            self.fallback_model if primary == self.default_model else self.default_model
        )

        all_vectors: List[List[float]] = []
        actual_model_used = primary

        # Process in chunks of MAX_ITEMS_PER_BATCH
        total = len(texts)
        for i in range(0, total, MAX_ITEMS_PER_BATCH):
            batch = texts[i : i + MAX_ITEMS_PER_BATCH]
            try:
                vectors = await self.call_gemini_batch_api(batch, primary, active_key)
                all_vectors.extend(vectors)
                actual_model_used = primary
            except RuntimeError as exc:
                if "QUOTA_EXCEEDED" in str(exc):
                    logger.warning(
                        f"Primary model {primary} exhausted quota, failing over to {secondary}..."
                    )
                    # Attempt fallback model (Gemini Embedding 2)
                    try:
                        vectors = await self.call_gemini_batch_api(
                            batch, secondary, active_key
                        )
                        all_vectors.extend(vectors)
                        actual_model_used = secondary
                    except Exception as fallback_exc:
                        logger.warning(
                            f"Fallback model {secondary} also failed: {fallback_exc}. Checking HF Space TEI..."
                        )
                        # Attempt tertiary fallback: Hugging Face Space private endpoint
                        if hf_url:
                            try:
                                logger.info(f"Failing over to private Hugging Face Space TEI at {hf_url}...")
                                hf_vecs = await self.call_hf_tei_api(batch, hf_url, hf_token)
                                all_vectors.extend(hf_vecs)
                                actual_model_used = "hf-space-tei"
                                continue
                            except Exception as hf_err:
                                logger.error(f"HF Space TEI also failed: {hf_err}")

                        # Ultimate deterministic fallback
                        all_vectors.extend(
                            [generate_deterministic_mock_embedding(t) for t in batch]
                        )
                        actual_model_used = "mock-fallback"
                else:
                    logger.error(
                        f"Error in batch embedding: {exc}. Using deterministic mock fallback."
                    )
                    all_vectors.extend(
                        [generate_deterministic_mock_embedding(t) for t in batch]
                    )
                    actual_model_used = "mock-fallback"

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
        stmt = select(EventEmbedding.event_id).where(
            EventEmbedding.event_id.in_(event_ids)
        )
        existing_ids: Set[str] = set((await session.execute(stmt)).scalars().all())

        pending_events = [e for e in events if e.id not in existing_ids]
        if not pending_events:
            logger.info(
                f"All {len(events)} events already have cached embeddings. Skipping API calls."
            )
            # Return existing embeddings
            stmt_all = select(EventEmbedding).where(
                EventEmbedding.event_id.in_(event_ids)
            )
            return list((await session.execute(stmt_all)).scalars().all())

        logger.info(
            f"Embedding {len(pending_events)} new events (out of {len(events)} requested)..."
        )
        texts = [build_embedding_document(e) for e in pending_events]

        vectors, model_name = await self.generate_embeddings_batch(
            texts=texts, use_mock=use_mock, session=session
        )

        new_records: List[EventEmbedding] = []
        for event, vec in zip(pending_events, vectors):
            rec = EventEmbedding(
                event_id=event.id,
                embedding_vector=vec,
                model_name=model_name,
                embedded_at=utc_now(),
            )
            session.add(rec)
            new_records.append(rec)

        await session.commit()
        logger.info(
            f"Successfully stored {len(new_records)} new embeddings via {model_name}."
        )

        # Retrieve all requested records
        stmt_all = select(EventEmbedding).where(EventEmbedding.event_id.in_(event_ids))
        return list((await session.execute(stmt_all)).scalars().all())

    def compute_pca_2d(
        self, vectors: List[List[float]]
    ) -> Tuple[List[Tuple[float, float]], Optional[PCA]]:
        """
        Project high-dimensional vectors to 2D (x, y) coordinates normalized to [-90, 90].
        """
        if not vectors:
            return [], None

        n = len(vectors)
        if n == 1:
            return [(0.0, 0.0)], None

        arr = np.array(vectors, dtype=np.float32)
        # Handle zero or constant vectors
        if np.all(arr == arr[0]):
            return [(0.0, 0.0) for _ in range(n)], None

        pca = PCA(n_components=2, random_state=42)
        coords = pca.fit_transform(arr)

        # Normalize to [-90, 90] bounds for canvas rendering
        x_min, x_max = coords[:, 0].min(), coords[:, 0].max()
        y_min, y_max = coords[:, 1].min(), coords[:, 1].max()

        x_span = x_max - x_min if (x_max - x_min) > 1e-5 else 1.0
        y_span = y_max - y_min if (y_max - y_min) > 1e-5 else 1.0

        scaled_x = ((coords[:, 0] - x_min) / x_span) * 160.0 - 80.0
        scaled_y = ((coords[:, 1] - y_min) / y_span) * 160.0 - 80.0

        results = [(round(float(x), 2), round(float(y), 2)) for x, y in zip(scaled_x, scaled_y)]
        return results, pca

    def compute_trustworthiness_and_corroboration(
        self,
        events: List[Event],
        embeddings: List[List[float]],
        similarity_threshold: float = 0.40,
    ) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]], List[Dict[str, Any]]]:
        """
        Compute pairwise cosine similarity, clustering, and intelligence trustworthiness metrics.
        Returns:
            - node_trust_metrics: List of dicts with trust scores, labels, nearest neighbors, etc.
            - edges: List of high-similarity link edges between reports
            - clusters: List of topical clusters with labels and centroids
        """
        n = len(events)
        if n == 0:
            return [], [], []

        arr = np.array(embeddings, dtype=np.float32)
        # Normalize vectors for cosine similarity computation
        norms = np.linalg.norm(arr, axis=1, keepdims=True)
        norms[norms == 0] = 1e-8
        norm_arr = arr / norms

        # Cosine similarity matrix: N x N
        sim_matrix = np.dot(norm_arr, norm_arr.T)

        node_metrics: List[Dict[str, Any]] = []
        edges: List[Dict[str, Any]] = []
        edge_set: Set[Tuple[int, int]] = set()

        # Adjacency for cluster grouping
        adj: Dict[int, List[int]] = {i: [] for i in range(n)}

        for i in range(n):
            ev_i = events[i]
            neighbors_idx: List[int] = []
            similarities_list: List[Tuple[int, float]] = []

            for j in range(n):
                if i == j:
                    continue
                score = float(sim_matrix[i, j])
                similarities_list.append((j, score))
                if score >= similarity_threshold:
                    neighbors_idx.append(j)
                    adj[i].append(j)
                    edge_pair = (min(i, j), max(i, j))
                    if edge_pair not in edge_set:
                        edge_set.add(edge_pair)
                        edges.append({
                            "source": ev_i.id,
                            "target": events[j].id,
                            "similarity": round(score, 3),
                        })

            similarities_list.sort(key=lambda x: x[1], reverse=True)
            top_neighbors = similarities_list[:4]

            # Analyze corroborating sources
            corrob_events = [events[j] for j in neighbors_idx]
            unique_sources: Set[str] = set()
            unique_source_types: Set[str] = set()

            for c_ev in corrob_events:
                unique_sources.add(c_ev.source_name)
                unique_source_types.add(c_ev.source_type)

            max_similarity = top_neighbors[0][1] if top_neighbors else 0.0
            corrob_count = len(corrob_events)
            num_independent_sources = len(unique_sources - {ev_i.source_name})
            num_source_types = len(unique_source_types)

            # --- Trustworthiness Algorithm ---
            # 1. Isolated Outlier: No other report shares >= 0.30 similarity
            if max_similarity < 0.30:
                trust_score = 25
                trust_label = "Isolated Outlier"
                verdict = "Single unverified singleton report. No matching intelligence found."
            # 2. Single-source Unverified: Only 0 corroborating reports
            elif corrob_count == 0 or num_independent_sources == 0:
                trust_score = 45
                trust_label = "Single-Source Unverified"
                verdict = "Reported by a single outlet with no corroboration yet."
            # 3. Developing / Cross-Checked: 1 or 2 corroborating independent sources
            elif num_independent_sources == 1 or corrob_count <= 2:
                trust_score = 70 + min(10, int(max_similarity * 10))
                trust_label = "Developing / Verified"
                verdict = f"Corroborated by {len(unique_sources)} sources ({', '.join(list(unique_sources)[:2])})."
            # 4. High Multi-Source Consensus: 3+ independent sources or multiple platforms
            else:
                diversity_bonus = 10 if num_source_types >= 2 else 5
                trust_score = min(98, 80 + (num_independent_sources * 4) + diversity_bonus)
                trust_label = "Highly Corroborated"
                verdict = f"Strong multi-source consensus confirmed by {len(unique_sources)} sources across {num_source_types} platforms."

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
                "corroboration_count": corrob_count,
                "corroborating_sources": list(unique_sources),
                "source_diversity_count": len(unique_sources),
                "nearest_neighbors": nearest_items,
                "max_similarity": round(max_similarity, 3),
            })

        # --- Connected Component Clustering ---
        visited = set()
        clusters: List[Dict[str, Any]] = []
        cluster_id_counter = 1

        for i in range(n):
            if i in visited:
                continue
            component = []
            queue = [i]
            visited.add(i)

            while queue:
                curr = queue.pop(0)
                component.append(curr)
                for neighbor in adj[curr]:
                    if neighbor not in visited:
                        visited.add(neighbor)
                        queue.append(neighbor)

            # Assign cluster metadata
            comp_events = [events[idx] for idx in component]
            # Dominant label from most important event in component
            sorted_comp = sorted(
                comp_events,
                key=lambda x: getattr(x, "importance_score", 5) or 5,
                reverse=True,
            )
            top_title = sorted_comp[0].headline or sorted_comp[0].title
            cluster_label = top_title[:60] if len(top_title) > 60 else top_title

            for idx in component:
                node_metrics[idx]["cluster_id"] = cluster_id_counter
                node_metrics[idx]["cluster_label"] = cluster_label

            clusters.append({
                "id": cluster_id_counter,
                "label": cluster_label,
                "size": len(component),
                "event_ids": [e.id for e in comp_events],
            })
            cluster_id_counter += 1

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
        computes PCA 2D coordinates & trustworthiness, and saves projections.
        """
        query = select(Event).order_by(Event.published_at.desc())

        if theme_id is not None:
            if str(theme_id).isdigit():
                query = query.where(Event.theme_id == int(theme_id))
            else:
                t_stmt = select(Theme).where(
                    (Theme.slug == theme_id) | (Theme.name.ilike(f"%{theme_id}%"))
                )
                t_res = (await session.execute(t_stmt)).scalars().first()
                if t_res:
                    query = query.where(Event.theme_id == t_res.id)

        if time_range_hours:
            cutoff = utc_now() - timedelta(hours=time_range_hours)
            query = query.where(Event.published_at >= cutoff)

        if min_score:
            query = query.where(Event.importance_score >= min_score)

        query = query.limit(limit)
        events: List[Event] = list((await session.execute(query)).scalars().all())

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

        # 2. Compute PCA 2D coordinates
        coords_2d, pca_model = self.compute_pca_2d(vectors)
        self.last_pca_model = pca_model

        # 3. Compute trustworthiness and cluster metrics
        node_metrics, edges, clusters = self.compute_trustworthiness_and_corroboration(
            valid_events, vectors
        )

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

            # Calculate cluster centroid
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
                "trustworthiness": {
                    "score": m["trustworthiness_score"],
                    "label": m["trustworthiness_label"],
                    "verdict": m["verdict"],
                    "corroboration_count": m["corroboration_count"],
                    "corroborating_sources": m["corroborating_sources"],
                    "source_diversity": m["source_diversity_count"],
                },
                "nearest_neighbors": m["nearest_neighbors"],
            })

        await session.commit()

        # Compute cluster centroids
        for c in clusters:
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


# Global singleton instance
_embedding_service: Optional[EmbeddingService] = None


def get_embedding_service() -> EmbeddingService:
    global _embedding_service
    if _embedding_service is None:
        _embedding_service = EmbeddingService()
    return _embedding_service
