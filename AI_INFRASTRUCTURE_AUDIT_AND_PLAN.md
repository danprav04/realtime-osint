# Real-Time OSINT AI Infrastructure Audit & Engineering Implementation Plan

**Target System**: Real-Time OSINT Gathering Service  
**Repository**: `danprav04/realtime-osint` (Public Open-Source)  
**Author**: Engineering Architecture Team (`worker_m2_1` & `worker_m2_2` synthesis incorporating Gate Review feedback from `reviewer_m3_2`, `challenger_m3_1`, and `challenger_m3_2`)  
**Date**: October 17, 2026  
**Status**: APPROVED FOR EXECUTION — REFINED & HARDENED GATE DELIVERABLE (ZERO TRACKED CODE MUTATIONS IN AUDIT PHASE)  

---

## 1. Executive Summary & System Overview

### 1.1 High-Level Status of the Real-Time OSINT AI Infrastructure
The Real-Time OSINT Gathering Service operates an automated, theme-driven open-source intelligence pipeline ingesting live signals from Telegram MTProto channels, Mastodon social streams, regional RSS/Atom news feeds, and seismic/geophysical sensors. Ingested events flow through an asynchronous, multi-stage artificial intelligence architecture responsible for relevance classification, multilingual entity extraction and translation, multi-dimensional tactical importance scoring, executive intelligence summarization, threat cartography visualization, and semantic vector embedding.

An exhaustive audit of the Python backend (`backend/app/pipeline/`, `backend/app/services/`), active provider adapters, live production telemetry on `ubuntu@production` (Oracle Cloud Micro `VM.Standard.E2.1.Micro`, 1 OCPU AMD EPYC 7742, 952 MiB RAM + 2.0 GiB Swap), and provider API specifications has confirmed that the core pipeline architecture is structurally sound and functional, yet contains **critical operational vulnerabilities, silent parsing fallbacks, and quota-depletion traps** that severely impair high-volume resilience.

### 1.2 Core Architectural Strengths
1. **Multi-Stage Separation of Concerns**: Isolating the intelligence lifecycle into Stage 0 (Gatekeeper), Stage 1 (Extraction/Translation), Stage 2 (Scoring), Stage 3 (Summarization), and Stage 4 (Visuals) provides granular modularity and per-stage model assignability.
2. **Multi-Key Groq Pool Architecture**: `GroqKeyPool` provides round-robin load distribution and per-model cooldown isolation across multiple API keys (`GROQ_API_KEYS`).
3. **Multi-Tier Robust Parsing**: `parser.py` implements a 5-tier extraction hierarchy (Direct JSON -> Markdown Fences -> Outermost Brackets -> Chunk Splitting -> Regex Sanitization -> Fallback Factory).
4. **Self-Hosted Hugging Face TEI Embeddings**: The primary embedding pipeline routes to a self-hosted Hugging Face Space running Text Embeddings Inference (`BAAI/bge-small-en-v1.5`), providing persistent, 24/7 unlimited embedding generation without incurring paid API costs or consuming Google Gemini embedding quotas.
5. **Lean Production Footprint**: The entire production Docker Compose stack (backend, frontend, PostgreSQL, Redis) operates within a **176.04 MiB physical RAM footprint** (backend: 108.5 MiB, frontend: 32.9 MiB, PostgreSQL: 30.3 MiB, Redis: 4.4 MiB), leaving 453 MiB available memory on a 952 MiB VPS.

### 1.3 Critical Vulnerabilities & Defect Summary
Despite these architectural strengths, the empirical audit uncovered five critical system defects:
1. **Pydantic Schema Root-Wrapping Bug (`parser.py:39-95`)**: When models like `ministral-8b-2512` output valid JSON enclosed in an outer schema wrapper (e.g., `{"Stage2Result": {"importance_score": 2, ...}}`), all parsing tiers fail to unwrap the root key. This silently triggers Tier 5 (Fallback Factory), substituting hardcoded synthetic scores (`importance_score=2`) and discarding genuine LLM intelligence analysis.
2. **In-Memory Tracking Disconnect (`usage_tracker.py:40-723`)**: `ProviderUsageTracker`, `AIHubMixQuotaTracker`, and `CloudflareNeuronTracker` are 100% in-memory Python singletons relying on `threading.Lock`. Redis contains **zero** rate-limit or quota keys (`KEYS "*usage*"` -> 0). In multi-worker Uvicorn configurations, each worker maintains isolated state, multiplying outbound request volume and triggering cascading 429s. Container restarts immediately wipe all rate and daily quota history.
3. **Absence of Formal Circuit Breakers**: The pipeline possesses zero formal Circuit Breakers (no open/half-open states, failure thresholds, or probe requests). It relies strictly on reactive 429 time cooldowns. Downstream HTTP 5xx errors, timeouts, or DNS drops do not cool down models, causing every incoming event to retry 3-4 times with exponential backoff on dead endpoints (accumulating 25–45s latency per event) before failing over.
4. **Global Database Lock Choke Point (`orchestrator.py:1061-1065`)**: In `_fetch_neighborhood`, the orchestrator acquires `async with self._db_lock:` while awaiting external network calls to Gemini's `batchEmbedContents` API. Network latency and Google API throttling directly stall all database operations, event persistence, and worker threads.
5. **Dormant Quota Sentinels & Missing Telemetry**:
   - `duration_ms` is omitted across all provider `record_call` invocations, leaving average and last latency permanently recorded as `0.0ms`.
   - `CloudflareNeuronTracker.record_neurons()` is never invoked, leaving the 10,000 Neuron/day budget unmonitored.
   - `AIHubMixQuotaTracker` burns daily requests before network dispatch without rollback on failure.

### 1.4 Key Empirical Production Figures (Live Host: `ubuntu@production`)
- **Total Ingested Events**: **2,697** (Telegram: 1,286, Mastodon: 1,011, RSS: 356, Seismic: 43, Seeds: 1)
- **Database Enrichment Coverage**: **2,697 Analyzed (100.0%)**, **2,697 Scored (100.0%)**, **2,697 Summarized (100.0%)**, **2,696 Translated (99.96%)**, **2,697 Embedded (100.0%)**
- **Critical Alerts Dispatched**: **277 alerts** (Importance Score >= 7 or Critical Threat)
- **Active Docker Memory Footprint**: **176.04 MiB** (Limit: 900.0 MiB, Peak Host Headroom: 453 MiB available)
- **Observed HTTP 429 Occurrences**: **8 on Groq Cloud**, **2 on Google Gemini** (Hard Free-Tier Daily Quota Exhaustion)
- **Groq Key Pool Exhaustion**: 4 out of 4 keys locked simultaneously on `openai/gpt-oss-20b` (`retry-after` up to 1,042s / 17.3 min)
- **Failover Telemetry**: **100% success rate (7/7 batches)** failing over to `mistral:ministral-3b-2512` with **261ms median latency**.

---

## 2. End-to-End AI Infrastructure Codebase Audit (Requirement R1)

### 2.1 Pipeline Data Flow Architecture
The intelligence pipeline executes an asynchronous flow from raw event capture through multi-stage enrichment to real-time dissemination:

```
[ Ingestion Sources ] (Telegram MTProto, Mastodon, RSS, Seismic Sensors)
        │
        ▼
[ IngestionSupervisor ] (plugins/supervisor.py:165-273)
        │  - Normalization into NormalizedEvent
        │  - Bloom filter / SHA-256 deduplication via Redis
        ▼
[ PipelineOrchestrator.enqueue() ] (orchestrator.py:284)
        │  - In-memory async queue: self._incoming_queue (maxsize=1000)
        │  - Worker pool: 2 concurrent workers (_worker_loop)
        ▼
[ Stage 0: Gatekeeper ] (stage0_gatekeeper.py:45-210)
        │  - Layer 1: Deterministic regex fast-pass (military/missile) & fast-drop (sports/ads)
        │  - Layer 2: LLM Micro-Batching (batches up to 10 events, timeout 0.5s)
        │  - Drops non-relevant events (relevance_score < 0.35)
        ▼ (Surviving Relevant Events)
[ Stage 1: Extraction & Translation ] (stage1_extract.py:52-247)
        │  - Translates Arabic, Hebrew, Persian, Russian, Ukrainian to English
        │  - Extracts Stage1Result (entities, locations, actors, equipment, casualties)
        │  - Fallback: _heuristic_extraction()
        ▼
[ Pre-Stage 2 Context & Embedding Resolution ] (orchestrator.py:1020-1068)
        │  - Computes 384d embedding via embedding_service.py
        │  - Computes Live Scoring Neighborhood (corroboration, echo, velocity, theater tempo)
        ▼
[ Concurrent Execution of Stages 2, 3, 4 ] (orchestrator.py:1110-1165)
   ├── [ Stage 2: Scoring ] (stage2_score.py:64-320)
   │        - Evaluates 4 guardrail calibration rules (credibility ceiling, impact vs severity)
   │        - Computes Stage2Result (military_importance, urgency, credibility, civil_impact, composite)
   ├── [ Stage 3: Summarization ] (stage3_summary.py:42-180)
   │        - Generates intelligence headline (<12 words) and BLUF (<50 words)
   └── [ Stage 4: Visuals & Cartography ] (stage4_visuals.py:38-165)
            - Classifies Stage4Result (threat_level, marker_type, color_code, cluster_tag)
        ▼
[ Persistence & Fan-Out ] (orchestrator.py:980-1120)
        │  - Persists AnalyzedEvent to PostgreSQL events table
        │  - Publishes enriched JSON to Redis Pub/Sub channel 'events.analyzed' (WebSockets)
        │  - If composite_score >= 75.0 or threat_level == 'CRITICAL':
        │        Triggers telegram_service.send_alert() -> Telegram Bot API
```

### 2.2 Background AI Services Audit

#### 2.2.1 Embedding Service (`backend/app/services/embedding_service.py`)
- **Primary Route**: Self-hosted Hugging Face Text Embeddings Inference (`BAAI/bge-small-en-v1.5` running on a private Hugging Face Space via `/embed` or `/v1/embeddings`).
- **Secondary Route**: Google Gemini `batchEmbedContents` (`models/gemini-embedding-001` — 100 RPM, 30K TPM, 1K RPD; plus `models/gemini-embedding-2` — 100 RPM, 30K TPM, 1K RPD). Micro-batches up to 100 texts per call (`outputDimensionality: 384`).
- **Tertiary Route**: Local hash-based deterministic vector fallback.
- **Audit Findings**:
  - The Hugging Face TEI integration in `call_hf_tei_api` (lines 597–658) fails to handle HTTP 503 cold starts properly. If the Hugging Face Space is idling, HF returns HTTP 503 with an `estimated_time` payload. When HF TEI cold-starts, traffic spills heavily onto `gemini-embedding-001` (pushing it to **`100/100` RPM** and **`962/1K` RPD** in Google AI Studio) while `gemini-embedding-2` (`37/100` RPM, `721/1K` RPD) is not dynamically load-balanced in `embedding_service.py`.

#### 2.2.2 Memory Rollup Service (`backend/app/services/memory_rollup_service.py`)
- **Schedule**: Background cron triggered every 24 hours at 02:00 UTC.
- **Function**: Queries events older than 48 hours, executes hierarchical agglomerative clustering into broad thematic incident clusters, and prompts an LLM to generate narrative daily rollups.
- **Audit Findings**:
  - Model selection is tied to the global default LLM. If the default LLM is exhausted or in cooldown, rollup synthesis fails silently without retrying alternate fallback models.

#### 2.2.3 Summary Service & Scheduler (`backend/app/services/summary_service.py`, `summary_scheduler.py`)
- **Schedule**: Periodic cron executing every 30 minutes.
- **Function**: Extracts high-importance events (`composite_score >= 50.0`) from the trailing 6 hours, performs KMeans clustering into 3–5 tactical storylines, and invokes the `situation_summary` stage to produce the rolling OSINT Intelligence Digest shown on the dashboard.
- **Audit Findings**:
  - `summary_service.py` hardcodes an expectation of extensive prompt completions. When routed to reasoning models on Groq, token budgets are easily saturated, risking 400 Bad Request responses.

---

### 2.3 In-Depth Audit of Rate-Limiting & Quota Tracking

#### 2.3.1 `UsageTracker` (`backend/app/pipeline/usage_tracker.py:40-723`)
`ProviderUsageTracker` serves as the centralized telemetry and rate-limiting engine. 

```python
class ProviderUsageTracker:
    def __init__(self):
        self._lock = threading.Lock()
        self._models: Dict[str, Dict[str, Any]] = {}
        self._keys: Dict[str, Dict[str, Any]] = {}
        self._providers: Dict[str, Dict[str, Any]] = {}
        self._daily_reset_time = time.time()
```

- **In-Memory Architecture**: All rate limits, sliding window deques, and token tallies are maintained exclusively in Python dictionary heaps guarded by a standard `threading.Lock()`.
- **Absence of Redis Commands**: There is not a single call to Redis (`redis.get`, `redis.incr`, `redis.expire`, `redis.setnx`) in `usage_tracker.py`.
- **Multi-Worker Isolation Hazard**: When Uvicorn runs with multiple worker processes (`--workers 2` or `--workers 4`), each process instantiates a completely independent `ProviderUsageTracker`. If an API provider enforces a 15 RPM limit, 4 workers will issue up to 60 RPM before internal client pacing intervenes, causing immediate provider 429 errors.
- **State Loss on Restart**: Any container restart, redeployment, or OOM event wipes all rate tracking, active model cooldowns, and daily request tallies.

#### 2.3.2 `GroqKeyPool` (`backend/app/pipeline/providers/key_pool.py:55-320`)
- **Pool Mechanics**: Manages a pool of $N$ Groq API keys loaded from `GROQ_API_KEYS`. Key selection uses round-robin rotation via `acquire_next_key()`.
- **Cooldown Isolation**: Distinguishes between key-level failures (HTTP 401/403) and model-level rate limits (HTTP 429). When a model hits 429, only `key._model_cooldowns[model_name]` is locked. The key remains active for alternate models.
- **Header Parsing**: Parses `x-ratelimit-remaining-requests`, `x-ratelimit-remaining-tokens`, `x-ratelimit-reset-requests`, `x-ratelimit-reset-tokens`, and `retry-after`.
- **Vulnerability**: While model cooldowns are isolated, the pool lacks awareness of shared organization boundaries. If all 4 keys belong to the same Groq organization, a TPM exhaustion on one key exhausts the organization's quota across all keys simultaneously.

#### 2.3.3 Circuit Breakers: Complete Absence of Formal State Machines
- The codebase contains **no formal circuit breaker pattern** (`CLOSED` -> `OPEN` -> `HALF-OPEN`).
- There are no failure rate thresholds (e.g., trip after 5 consecutive 5xx errors within 30 seconds), no half-open probe requests, and no automatic failure recovery timers.
- Cooldowns are established **only** upon receiving an explicit HTTP 429 `RateLimitError`.
- **Outage Hazard**: If a provider suffers an infrastructure outage returning HTTP 500, 502, 503, or connection timeouts, no cooldown is recorded. Every incoming event attempts the dead provider, waits for 3 retries with exponential backoff (2s + 4s + 8s + network timeouts = ~30–45s delay per event), and only then falls back. This quickly starves pipeline workers and overflows `self._incoming_queue`.

#### 2.3.4 Database Concurrency Choke Point (`orchestrator.py:1061-1065`)
In `_fetch_neighborhood`, the orchestrator executes:
```python
async with self._db_lock:
    neighborhood = await asyncio.shield(_fetch_neighborhood())
```
Inside `_fetch_neighborhood()` (lines 1042–1059), `embedding_service.compute_live_scoring_neighborhood()` is called. This method makes an external HTTP network call to Google Gemini's `batchEmbedContents` API.
Holding `self._db_lock` across a network boundary blocks all other coroutines attempting database writes, event persistence, status updates, or deduplication checks.

#### 2.3.5 Quota Accounting Bug in `AIHubMixQuotaTracker` (`aihubmix.py:88-102`)
In `AIHubMixQuotaTracker.record_request()`, the daily request counter is incremented **prior** to dispatching the HTTP call:
```python
def record_request(self, estimated_tokens: int = 500) -> bool:
    with self._lock:
        if self._daily_requests >= self.MAX_DAILY_REQUESTS: # 100
            return False
        self._daily_requests += 1  # Incremented before HTTP call is made!
        self._daily_tokens += estimated_tokens
        return True
```
If the downstream HTTP request fails due to network drop, DNS timeout, or HTTP 503, the burned request count is never refunded. Under intermittent network instability, the 100 RPD daily quota is exhausted without completing a single successful call.

#### 2.3.6 Groq Reasoning Model Token Reservation & 8K TPM Collision (`groq.py:195-255`)
Reasoning models on Groq (`openai/gpt-oss-120b`, `openai/gpt-oss-20b`, `qwen/qwen3.8-27b`) emit hidden chain-of-thought tokens prior to generating their JSON response.
When `max_tokens=1024` is requested, reasoning tokens can consume the budget, truncating the JSON payload mid-stream and causing Groq to return HTTP 400 with `"json_validate_failed"`. However, `groq.py:253` unconditionally bumps `effective_max_tokens = max(max_tokens, 3072)` for all `gpt-oss` and `qwen` JSON requests. Because Groq's Free Plan enforces an **8,000 TPM (Tokens Per Minute)** cap (and separate OTPM limits on some orgs) on `openai/gpt-oss-120b`, `openai/gpt-oss-20b`, and `qwen/qwen3.8-27b`, just **2 to 3 concurrent requests** with `max_tokens=3072` immediately saturate the 8K TPM bucket!

#### 2.3.7 Multilingual Handling & Heuristic Fallback Information Loss
- **Token Inflation**: Subword tokenizers fragment Arabic, Persian, and Hebrew script if vocabulary coverage is narrow, whereas **Gemini 3.5/3.1 Flash Lite & Gemma 4** (~1.22x), **Qwen 3.8 (`qwen/qwen3.8-27b`)** (~1.3x), and **Mistral (`mistral-large-2512`, `mistral-small-2603`)** handle multilingual scripts cleanly.
- **Catastrophic Heuristic Fallback**: In `stage1_extract.py:80-185`, when extraction fails across all LLM models, `_heuristic_stage1_fallback()` returns a degraded `Stage1Result` with empty military units and munitions:
  ```python
  return Stage1Result(
      extracted_entities=all_entities,
      detected_language=lang,
      translated_text=translated,
      translated_title=translated_title,
      key_facts=extra_facts,
      entity_breakdown={"military_units": [], "munitions": [], "locations": [...]},
  )
  ```
  All vital tactical data (munitions, casualties, military units, GPS coordinates) is completely erased from the intelligence database.

---

## 3. Active Provider Documentation & Rate-Limit Research (Requirement R2)

A rigorous examination was conducted of all six AI providers actively configured with credentials in `.env`, cross-referenced against the live Google AI Studio and Groq Console rate-limit dashboards.

### 3.1 Google Gemini & Gemma 4 (Google AI Studio REST API)

#### 3.1.1 Official 2026 Rate Limits & Observed Live Console Telemetry
Rate limits are enforced **per Google Cloud / AI Studio project** (shared across all API keys under that project):

| Console Model Name | API Model ID | Category | Free RPM | Free TPM | Free RPD | Live Observed Peak / Usage | Operational Status & Notes |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| **Gemini 3.5 Flash Lite** | **`gemini-3.5-flash-lite`** | Text-out models | **15 RPM** | **250K TPM** | **500 RPD** | `20 / 15` RPM \| `23.17K / 250K` TPM \| **`504 / 500` RPD** | **Daily RPD Exhausted (100.8%)** — Fastest (~0.4s), native `systemInstruction` & `responseMimeType`, but strict **500 RPD** cap. |
| **Gemini 3.1 Flash Lite** | **`gemini-3.1-flash-lite`** | Text-out models | **15 RPM** | **250K TPM** | **500 RPD** | `16 / 15` RPM \| `16.67K / 250K` TPM \| **`505 / 500` RPD** | **Daily RPD Exhausted (101.0%)** — Secondary 500 RPD bucket (1,000 RPD combined across 3.5 + 3.1 Flash Lite). |
| **Gemma 4 26B** | **`gemma-4-26b-a4b-it`** | Other models | **30 RPM** | **16K TPM** | **14.4K RPD** | `22 / 30` RPM \| **`26.12K / 16K` TPM** \| `6.21K / 14.4K` RPD | **TPM Burst Exceeded (163%)** — Massive **14,400 RPD** & **30 RPM** capacity, but constrained by **16,000 TPM** window. |
| **Gemma 4 31B** | **`gemma-4-31b-it`** | Other models | **30 RPM** | **16K TPM** | **14.4K RPD** | `22 / 30` RPM \| **`16.66K / 16K` TPM** \| `3.51K / 14.4K` RPD | **TPM Burst Exceeded (104%)** — Second **14,400 RPD** & **30 RPM** bucket (**28,800 RPD combined** across Gemma 4 26B + 31B). |
| **Gemini Embedding 1** | **`gemini-embedding-001`** | Other models | **100 RPM** | **30K TPM** | **1K RPD** | **`100 / 100` RPM** \| `16.78K / 30K` TPM \| **`962 / 1K` RPD** | **96.2% Daily RPD Used & 100% RPM Hit** — Each item in `batchEmbedContents` counts toward 100 RPM / 1,000 RPD. |
| **Gemini Embedding 2** | **`gemini-embedding-2`** | Other models | **100 RPM** | **30K TPM** | **1K RPD** | `37 / 100` RPM \| `3.31K / 30K` TPM \| **`721 / 1K` RPD** | **72.1% Daily RPD Used** — Primary 3072d embedding model (`1,683 / 2,000` combined RPD used across Embedding 1 & 2). |

*Daily Reset Timing*: Google AI Studio daily request quotas (RPD) reset at midnight Pacific Time (08:00 UTC).

#### 3.1.2 Root-Cause Diagnosis of Observed Gemini & Gemma Overages
1. **Why `gemini-3.5-flash-lite` (`504/500`) and `gemini-3.1-flash-lite` (`505/500`) Exhausted RPD**:
   - Each Flash-Lite model only has **500 RPD** (not 1,500 RPD). In `orchestrator.py:143-184`, `gemini-3.5-flash-lite` was assigned as Primary for Stage 2 (`scoring`) and Fallback 1 across `extraction`, `summarization`, and `visuals`, burning through all 500 RPD (`504/500`) and immediately cascading into `gemini-3.1-flash-lite` (`505/500`).
2. **Why `gemma-4-26b-a4b-it` (`26.12K / 16K` TPM) and `gemma-4-31b-it` (`16.66K / 16K` TPM) Exceeded 16K TPM**:
   - In `gemini.py:131-133`, `est_input_tokens = int(prompt_chars / 3.5) + 15` underestimates Arabic/Hebrew/Farsi token counts (where 1 token $\approx 1.6$ chars) and `_acquire_pacing_slot()` releases `self._pacing_lock` *before* the HTTP response returns `usageMetadata.totalTokenCount` (which includes Gemma's 400–1,450 internal `thought=True` reasoning tokens). Concurrent workers therefore dispatch overlapping requests whose combined server-side token count spikes to `26.12K / 16K TPM`.
   - Meanwhile, `gemini.py:53` artificially throttled Gemma to `self.max_rpm = 15` (`min_interval_sec = 4.0`) even though Google AI Studio grants **30 RPM** and **14,400 RPD** to both `gemma-4-26b-a4b-it` and `gemma-4-31b-it`.

#### 3.1.3 HTTP Headers & Error Payloads
- **HTTP 200 OK**: Gemini REST does **not** return `x-ratelimit-*` headers; token consumption is reported in JSON body `usageMetadata` (`promptTokenCount`, `candidatesTokenCount`, `thoughtsTokenCount`, `totalTokenCount`).
- **HTTP 429 Too Many Requests**: Returns `Retry-After` header and JSON body containing `error.details[].retryDelay` (e.g., `"15s"`, `"24.5s"`) and `quotaId` (`GenerateRequestsPerDayPerProjectPerModel-FreeTier` vs `InputTokensPerModelPerMinute-FreeTier`).
- **Gemma 4 Payload Constraints**: Gemma 4 models (`gemma-4-26b-a4b-it`, `gemma-4-31b-it`) reject `systemInstruction` and `responseMimeType="application/json"` (HTTP 400/500) and crash if `temperature == 0.0`. `gemini.py:90-108` properly folds `system_prompt` into the user prompt, clamps `temperature >= 0.1`, and strips `thought=True` parts.

---

### 3.2 Groq Cloud (Multi-Key Pool)

#### 3.2.1 Official 2026 Free Plan Rate Limits
Rate limits apply at the **organization level** (not individual users). In addition to combined TPM, organizations may also have separate per-minute caps on input tokens (**ITPM**) and output tokens (**OTPM**). **Cached tokens do not count towards rate limits.**

| Model ID | Free RPM | Free RPD | Free TPM | Free TPD | Audio (ASH / ASD) | Primary Role in OSINT Pipeline |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| **`openai/gpt-oss-120b`** | **30 RPM** | **1K (1,000)** | **8K (8,000)** | **200K (200,000)** | — | Deep reasoning, Stage 3 Summarization & Stage 5 SitRep |
| **`openai/gpt-oss-20b`** | **30 RPM** | **1K (1,000)** | **8K (8,000)** | **200K (200,000)** | — | Fast structured classification, Stage 4 Visuals & Fallback |
| **`openai/gpt-oss-safeguard-20b`** | **3 RPM** | **1K (1,000)** | **2K (2,000)** | **200K (200,000)** | — | Low-RPM moderation/safeguard (avoid in high-rate stages) |
| **`qwen/qwen3.8-27b`** | **30 RPM** | **1K (1,000)** | **8K (8,000)** | **200K (200,000)** | — | High-speed Multilingual NLP, Stage 1 Extraction & Translation |
| **`meta-llama/llama-prompt-guard-2-22m`** | **30 RPM** | **14.4K (14,400)** | **15K (15,000)** | **500K (500,000)** | — | Lightweight prompt-guard / safety classifier |
| **`meta-llama/llama-prompt-guard-2-86m`** | **30 RPM** | **14.4K (14,400)** | **15K (15,000)** | **500K (500,000)** | — | Lightweight prompt-guard / safety classifier |
| **`whisper-large-v3` / `v3-turbo`** | **20 RPM** | **2K (2,000)** | — | — | **7.2K / 28.8K** | Speech-to-Text audio transcription |
| **`canopylabs/orpheus-arabic-saudi` / `v1-english`** | **10 RPM** | **100** | **1.2K (1,200)** | **3.6K (3,600)** | — | Text-to-Speech synthesis |

*Multi-Key Pool Multiplier*: Across 4 independent Groq keys/projects in `GroqKeyPool`, each of the three primary text models (`openai/gpt-oss-120b`, `openai/gpt-oss-20b`, `qwen/qwen3.8-27b`) provides an aggregate capacity of **120 RPM, 4,000 RPD, 32,000 TPM, and 800,000 TPD** (or **12,000 RPD and 2,400,000 TPD combined** across all three models).

#### 3.2.2 Official Groq Rate Limit Headers Semantics
Groq includes rate-limit headers on every HTTP response (`retry-after` is **only** set on HTTP 429):

| Header | Example Value | Official Groq Documentation Definition |
| :--- | :--- | :--- |
| **`retry-after`** | `2` | In seconds (**only set if you hit the rate limit and status code 429 is returned**) |
| **`x-ratelimit-limit-requests`** | `1000` / `14400` | **Always refers to Requests Per Day (RPD)** |
| **`x-ratelimit-remaining-requests`** | `985` / `14370` | **Always refers to Requests Per Day (RPD)** |
| **`x-ratelimit-reset-requests`** | `2m59.56s` | **Always refers to Requests Per Day (RPD)** |
| **`x-ratelimit-limit-tokens`** | `8000` / `18000` | **Always refers to Tokens Per Minute (TPM)** |
| **`x-ratelimit-remaining-tokens`** | `7420` / `17997` | **Always refers to Tokens Per Minute (TPM)** |
| **`x-ratelimit-reset-tokens`** | `7.66s` | **Always refers to Tokens Per Minute (TPM)** |

#### 3.2.3 Prompt Caching & Token Efficiency on Groq
- **Zero Rate-Limit Cost for Cached Tokens**: Per Groq official documentation, **cached tokens do not count towards your rate limits (ITPM / TPM / TPD)**. Keeping static system instructions and JSON schema definitions in an unchanging prefix (`messages[0] = {"role": "system", ...}`) with variable event data solely in the user message allows Groq's automatic prefix cache to eliminate 50–70% of input token burn.
- **Right-Sizing `max_tokens` for 8K TPM**: Because `openai/gpt-oss-120b`, `openai/gpt-oss-20b`, and `qwen/qwen3.8-27b` have an **8,000 TPM** ceiling per key, forcing `max_tokens = 3072` (`groq.py:253`) risks OTPM/TPM throttling when multiple requests hit the same key within 60 seconds. Right-sizing `max_tokens` to `1280–1536` for standard JSON stages (and `2560` only for Stage 5 SitReps) prevents mid-JSON truncation while doubling per-minute concurrency headroom.

---

### 3.3 AIHubMix (OpenAI-Compatible Gateway)

#### 3.3.1 Official Rate Limits
Enforced per account across all free-tier models:
- **Rate Limit**: **10 RPM** (Enforces strict client-side pacing of **6.0 seconds between requests**).
- **Daily Quotas**: **100 RPD** and **1,000,000 TPD** (resets at 00:00 UTC).
- **Free Model Catalog**: `coding-glm-5.3-free` (1.05M context flagship reasoning), `coding-glm-5.3-flash-free`, `coding-minimax-m3-free`, `nemotron-3-super-120b-a12b-free`.

---

### 3.4 Mistral AI (La Plateforme) — Smart Decision & High-Throughput Tiering

#### 3.4.1 Official Rate Limits & Model Hierarchy
Mistral's La Plateforme provides four distinct model tiers configured in the codebase (`mistral.py`, `AIConfigMatrix.tsx`):

| Model Name | Intelligence Tier | Throughput / Rate Limit | Token Limit (TPM) | Strategic OSINT Pipeline Role |
| :--- | :--- | :--- | :--- | :--- |
| **`mistral-large-2512`** | **Flagship Reasoning (Tier 1)** | **1.0 RPS (60 RPM)** | **500,000 TPM** | **High-Stakes Executive Decisions**: Stage 5 Situational Summary (`situation_summary`), Memory Rollups, and Stage 2 Critical Threat Scoring (`scoring`) fallback. |
| **`mistral-small-2603`** | **Smart Reasoning & Structured Output (Tier 2)** | **1.0 RPS (60 RPM)** | **500,000 TPM** | **Core Per-Event Decisions**: Stage 2 Importance & Threat Scoring (`scoring`), Stage 1 Multilingual Extraction (`extraction`), and Stage 3 Summarization (`summarization`). |
| **`ministral-8b-2512`** | **Dense 8B Mid-Tier (Tier 3)** | **3.13 RPS (187 RPM)** | **625,000 TPM** | **Low-Stakes Classification**: Stage 4 Visuals & Cartography tag classification (`visuals`). |
| **`ministral-3b-2512`** | **Ultra-Fast Edge 3B (Tier 4)** | **12.50 RPS (750 RPM)** | **1,300,000 TPM** | **High-Volume Pre-Filter**: Stage 0 Chatter & Noise Gatekeeper (`gatekeeper` — 261ms median latency in prod). |

#### 3.4.2 HTTP Headers & Error Payloads
- Headers: `X-RateLimit-Remaining`, `X-RateLimit-Limit`, `Retry-After` (integer seconds on HTTP 429, error code `1300`).
- **Model-Aware Pacing Requirement**: `mistral.py:58` currently hardcodes `min_interval_sec = 0.2` (`300 RPM`) for all Mistral models. When calling **`mistral-large-2512`** or **`mistral-small-2603`** (which enforce **1.0 RPS / 60 RPM**), bursts faster than 1.0s trigger HTTP 429 (`code: 1300`). `MistralProvider` must enforce model-aware pacing (`min_interval_sec = 1.05`, `max_rpm = 55` for `mistral-large-2512` and `mistral-small-2603`; `0.35s` for `ministral-8b-2512`; `0.15s` for `ministral-3b-2512`).

---

### 3.5 Cloudflare Workers AI

#### 3.5.1 Official Rate Limits & Neuron Budget
- **Daily Budget**: **10,000 Neurons per day** (Free tier, resets at 00:00 UTC).
- **Rate Limit**: **300 RPM** for text generation (`@cf/meta/llama-3.2-1b-instruct` ~4.1K RPD, `@cf/meta/llama-3.2-3b-instruct` ~2.4K RPD, `@cf/meta/llama-3.1-8b-instruct-fp8-fast` ~2.2K RPD).

#### 3.5.2 Exact Neuron Calculation Formula
$$\text{Total Neurons} = \left(\frac{\text{Input Tokens}}{1,000,000} \times \text{Input Rate}\right) + \left(\frac{\text{Output Tokens}}{1,000,000} \times \text{Output Rate}\right)$$
- `@cf/meta/llama-3.2-1b-instruct`: Input 2,457 Neurons/1M, Output 18,252 Neurons/1M ($\approx 0.0019$ Neurons/call $\rightarrow \sim 4,100\text{–}5,000$ RPD).
- `@cf/meta/llama-3.2-3b-instruct`: $\approx 0.0044$ Neurons/call ($\sim 2,400$ RPD).
- `@cf/meta/llama-3.1-8b-instruct-fp8-fast`: $\approx 0.0245$ Neurons/call ($\sim 400\text{–}2,200$ RPD depending on prompt length).

---

### 3.6 Embeddings Infrastructure (Gemini Embedding 2/1 + Hugging Face Space TEI)

1. **Google Gemini Embeddings (`embedding_service.py`)**:
   - **Primary (`gemini-embedding-2`)**: **100 RPM | 30K TPM | 1,000 RPD** (Observed: `37/100` RPM, `3.31K/30K` TPM, `721/1K` RPD).
   - **Secondary (`gemini-embedding-001`)**: **100 RPM | 30K TPM | 1,000 RPD** (Observed: `100/100` RPM, `16.78K/30K` TPM, `962/1K` RPD).
2. **Self-Hosted Hugging Face Space TEI (`HF_EMBEDDING_URL`)**:
   - Endpoint: `https://danprav-osint-embeddings-tei.hf.space/embed` (`BAAI/bge-small-en-v1.5` / `all-MiniLM-L6-v2`).
   - Capacity: **~3,000 RPM (50 RPS)** persistent Space with unlimited RPD and zero cost.

---

### 3.7 Comprehensive Multi-Provider Comparative Matrix

| Metric / Capability | Google Gemini Flash-Lite (`3.5` / `3.1`) | Google Gemma 4 (`26B` / `31B`) | Groq Cloud (4-Key Pool: `qwen3.8-27b`, `gpt-oss-120b/20b`) | Mistral AI (`large-2512`, `small-2603`, `ministral-3b/8b`) | AIHubMix (`coding-glm-5.3-free`) | Cloudflare Workers AI (`llama-3.2-1b/3b`) |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| **Free RPM** | **15 RPM** each | **30 RPM** each | **30 RPM / key** (120 RPM pool) | **60 RPM** (`large`/`small`) / **750 RPM** (`3b`) | **10 RPM** (6.0s gap) | **300 RPM** |
| **Free RPD** | **500 RPD** each (1,000 total) | **14,400 RPD** each (28,800 total) | **1,000 RPD / key / model** (4,000 RPD/model pool) | Credit-backed ($10/mo) | **100 RPD** (shared) | **~2,400–4,100 RPD** |
| **Free TPM** | **250,000 TPM** | **16,000 TPM** | **8,000 TPM / key / model** (32K TPM pool) | **500K–1.3M TPM** | Unmetered (TPD-capped) | Unmetered (Neuron-capped) |
| **Free TPD** | Unmetered (RPD-capped) | Unmetered (TPM/RPD-capped) | **200,000 TPD / key / model** (800K TPD/model pool) | Credit-backed | **1,000,000 TPD** | **10,000 Neurons/day** |
| **Rate-Limit Headers**| `Retry-After`, `retryDelay` | `Retry-After`, `retryDelay` | `x-ratelimit-*-requests` (RPD), `*-tokens` (TPM), `retry-after` (on 429) | `X-RateLimit-*`, `Retry-After` | `Retry-After` | `Ratelimit`, `Retry-After` |
| **Best Use Cases** | Fast precision scoring & extraction fallback | High-RPD Stage 0/1/2/3/4 workhorse (with 16K TPM pacing) | Primary Extraction (`qwen3.8-27b`), Summarization (`gpt-oss-120b`), Visuals (`gpt-oss-20b`) | **Important Decisions** (`mistral-large-2512`, `mistral-small-2603`) & Stage 0 (`ministral-3b-2512`) | Stage 5 Executive SitRep & Daily Memory Rollups | High-RPM Stage 0 & Stage 4 overflow fallback |

---

### 3.8 Detailed Table of 10 Codebase Discrepancies

| # | Component & Location | Current Codebase Behavior | Official Provider Specification & Console Reality | Impact & Failure Mode |
|---|---|---|---|---|
| 1 | `backend/app/pipeline/providers/gemini.py:51-58` | Sets `max_rpm = 15` for Gemma (`is_gemma`), and `max_tpm = 0` for `gemini-3.5-flash-lite` / `3.1-flash-lite`. | Gemma 4 (`26B`/`31B`) allows **30 RPM / 16K TPM / 14.4K RPD**; Flash-Lite (`3.5`/`3.1`) allows **15 RPM / 250K TPM / 500 RPD**. | Under-utilizes Gemma's 30 RPM while failing to track Flash-Lite's 250K TPM and 500 RPD ceiling (`504/500` and `505/500` hit in prod). |
| 2 | `backend/app/pipeline/providers/gemini.py:131-139` & `base.py:309` | Estimates `est_input_tokens = int(prompt_chars / 3.5) + 15` and releases `_pacing_lock` before actual `totalTokenCount` (including Gemma `thought` tokens) returns. | Gemma 4 has a strict **16,000 TPM** cap and emits 400–1,450 thinking tokens; Arabic/Hebrew is ~1.6 chars/token. | Concurrent workers burst past 16K TPM before token reconciliation completes (caused **`26.12K / 16K` TPM** on Gemma 4 26B and **`16.66K / 16K` TPM** on Gemma 4 31B). |
| 3 | `backend/app/pipeline/providers/groq.py:252-253` | Forces `effective_max_tokens = max(max_tokens, 3072)` for all `gpt-oss` and `qwen` JSON calls. | `openai/gpt-oss-120b`, `openai/gpt-oss-20b`, and `qwen/qwen3.8-27b` have an **8,000 TPM** (and OTPM) limit per org/key and **200,000 TPD**. | 2–3 concurrent calls reserve >9,000 tokens, immediately breaching the **8K TPM / OTPM** limit and accelerating 200K TPD exhaustion. |
| 4 | `backend/app/pipeline/providers/key_pool.py:289-295, 356` | Stores `remaining_requests` (RPD) and `remaining_tokens` (TPM) per-key (overwriting across models) and only rotates on HTTP 200 when `rem_tokens == 0`. | `x-ratelimit-remaining-requests` is per-model **RPD (1,000)** and `x-ratelimit-remaining-tokens` is per-model **TPM (8,000)**. | Keys overwrite telemetry across models and fail to rotate proactively when `rem_tokens < 1,200`, guaranteeing a 429 on the next call. |
| 5 | `backend/app/pipeline/providers/key_pool.py:309` | Marks `is_daily = lock_delay > 3600.0` on HTTP 429. | Groq's **200K TPD** and **1K RPD** rolling windows return `retry-after` values of `199s–1,042s` (3–17 minutes) as tokens roll off the 24h window. | Rolling TPD/RPD exhaustions (`retry-after: 1042s` in prod) are misclassified as minute rate limits instead of daily quota exhaustions. |
| 6 | `backend/app/pipeline/orchestrator.py:134,149,163` & `settings.py:44,58,71` | Routes Stage 1 (`extraction`), Stage 2 (`scoring`), and Stage 3 (`summarization`) Mistral fallbacks to **`ministral-8b-2512`**. | Mistral provides **`mistral-large-2512`** (Flagship) and **`mistral-small-2603`** (Smart Reasoning) at 1.0 RPS (60 RPM) / 500K TPM. | Crucial intelligence decisions (scoring 1–10 alert thresholds, entity extraction, BLUF summaries) degrade to an 8B edge model that also wraps JSON in `{"Stage2Result": ...}`. |
| 7 | `backend/app/pipeline/providers/mistral.py:58` | Hardcodes `min_interval_sec = 0.2`, `max_rpm = 300` across all Mistral models. | `mistral-large-2512` and `mistral-small-2603` enforce **1.0 RPS (60 RPM)**; `ministral-8b-2512` is 3.13 RPS; `ministral-3b-2512` is 12.5 RPS. | Calling `mistral-large-2512` or `mistral-small-2603` without 1.0s model-aware spacing triggers HTTP 429 error code `1300`. |
| 8 | `backend/app/pipeline/providers/aihubmix.py:198,261` | `quota_tracker.record_request()` decrements 100 RPD quota before HTTP dispatch without refund on failure; `usage_tracker.record_call()` only called on HTTP 200. | Quota should be refunded if network/5xx fails before completion, and failed calls must be logged in telemetry. | Transient network errors permanently burn the 100 RPD budget while hiding failures from `/api/settings/provider-usage`. |
| 9 | `backend/app/pipeline/providers/cloudflare.py:38,238`| `CloudflareNeuronTracker` defined but `record_neurons()` is **never called**; reads `usage.get("neurons", 0)` which is always 0. | Cloudflare 10,000 Neuron daily budget must be computed from `prompt_tokens` and `completion_tokens`. | Neuron budget tracker is dormant; system cannot track or prevent hitting the 10,000 Neuron daily cap. |
| 10 | `backend/app/services/embedding_service.py:556,625` | Single-event neighborhood calls burn `gemini-embedding-001` (`962/1K` RPD & `100/100` RPM) and `gemini-embedding-2` (`721/1K` RPD); `call_hf_tei_api` drops to mock on HTTP 503. | Each item in `batchEmbedContents` counts toward 100 RPM / 1,000 RPD; HF Spaces return HTTP 503 with `estimated_time` on cold starts. | Gemini Embedding 1 & 2 approach daily exhaustion (`1,683 / 2,000` combined RPD), and HF Space cold starts drop to mock vectors instead of waiting. |

---

## 4. Production Log & Live Telemetry Analysis (Requirement R3)

### 4.1 Quantitative Empirical Metrics from `ubuntu@production`
- **Total Ingested Events**: **2,697**
- **Enrichment Breakdown**:
  - Analyzed: 2,697 (100.0%)
  - Scored: 2,697 (100.0%)
  - Summarized: 2,697 (100.0%)
  - Translated: 2,696 (99.96%)
  - Embedded: 2,697 (100.0%)
- **Alerts Dispatched**: **277**
- **Physical Memory Footprint**: **176.04 MiB** (Backend: 108.5 MiB, Frontend: 32.9 MiB, Postgres: 30.3 MiB, Redis: 4.4 MiB)
- **Active Redis Keys**: Exactly 20 keys, all matching `alert:dedup:<sha256_hash>`. **Zero usage tracking keys exist in Redis.**

### 4.2 Outbound HTTP Request Breakdown by Provider
Extracted from structured JSON logs of `osint-backend` on `ubuntu@production`:

| Target Host / Provider | 200 OK | 301/302/307 | 403 Forbidden | 429 Too Many Requests | Total Calls |
| :--- | :--- | :--- | :--- | :--- | :--- |
| **Groq Cloud API** (`api.groq.com`) | 14 | 0 | 0 | **8** | **22** |
| **Mistral AI API** (`api.mistral.ai`) | 14 | 0 | 0 | **0** | **14** |
| **Google Gemini API** (`generativelanguage.googleapis.com`) | 2 | 0 | 0 | **2** | **4** |
| **AIHubMix API** (`api.aihubmix.com`) | 0 | 0 | 0 | 0 | 0 |
| **Cloudflare Workers AI** (`api.cloudflare.com`) | 0 | 0 | 0 | 0 | 0 |
| **Telegram Scraping & Bot API** | 115 | 27 | 0 | 0 | 142 |
| **Sensors & Feeds** (Oref, RSS, Mastodon, Bluesky) | 56 | 9 | 6 | 0 | 71 |

### 4.3 HTTP 429 Occurrences & Key Pool Exhaustion
1. **Google Gemini Daily Free-Tier Quota Ceiling Hit**:
   - Timestamps: `2026-10-17T01:31:35.338Z` & `01:31:35.698Z`
   - Verbatim Log:
     ```
     {"timestamp": "2026-10-17T01:31:35.698885+00:00", "level": "WARNING", "logger": "osint.ai.provider", "message": "[gemini:gemini-3.1-flash-lite] Daily quota exhausted (daily quota exhausted (GenerateRequestsPerDayPerProjectPerModel-FreeTier, generativelanguage.googleapis.com/generate_content_free_tier_requests)). Failing over immediately."}
     ```
   - Impact: Both `gemini-3.5-flash-lite` and `gemini-3.1-flash-lite` hit the Google Cloud project RPD ceiling. The single configured `GEMINI_API_KEY` was locked out for the remainder of the UTC day.
2. **Groq 4-Key Pool Exhaustion on `openai/gpt-oss-20b`**:
   - Total 429 hits observed: **8**
   - Individual Key Cooldown Delays:
     - Key `gsk_...P0Jj`: 429 with retry-after **80s** (01:31:24)
     - Key `gsk_...2e4P`: 429 with retry-after **23s** (01:31:25)
     - Key `gsk_...P0Jj`: 429 with retry-after **37s** (01:31:36)
     - Key `gsk_...ZEDF`: 429 with retry-after **199s** (01:33:25)
     - Key `gsk_...P0Jj`: 429 with retry-after **379s** (01:33:25)
     - Key `gsk_...eH6A`: 429 with retry-after **1,042s (17.3 minutes!)** (01:33:25)
     - Key `gsk_...2e4P`: 429 with retry-after **741s (12.35 minutes)** (01:33:25)
     - Key `gsk_...2e4P`: 429 with retry-after **673s (11.2 minutes)** (01:33:25)
   - **Simultaneous Exhaustion Event**: At `01:33:25.586Z`, all 4 keys in the pool were simultaneously locked on `openai/gpt-oss-20b`.
   - **Failover Execution**: The orchestrator triggered cross-provider failover to `mistral:ministral-3b-2512`.

### 4.4 Failover Telemetry
- **Stage 0 (Gatekeeper)**: Evaluated 7 batches on fallback `mistral:ministral-3b-2512`.
  - HTTP Success Rate: **7 out of 7 succeeded (100.0%)**.
  - Drop Rate: 33 out of 35 evaluated events dropped as non-relevant (94.3% drop rate).
  - Latency: Median 261ms, p95 361ms.
- **Stage 1 & Stage 4**: Handled by `groq:qwen/qwen3.8-27b` with 100% success (0 fallbacks needed).

### 4.5 Concrete Codebase Bugs Exposed by Production Logs

#### Bug 1: Top-Level Schema Name Wrapping in `parser.py`
In `osint-backend` production logs:
```
{"timestamp": "2026-10-17T01:31:39.087899+00:00", "level": "WARNING", "logger": "osint.pipeline.parser", "message": "All JSON extraction tiers failed for Stage2Result. Executing fallback factory. Raw text snippet: {\n\t\"Stage2Result\": {\n\t\t\"importance_score\": 2,\n\t\t\"score_rationale\": \"The event lacks direct relevance to the US-Iran conflict theme..."}}
```
- **Root Cause**: When Mistral is called with system instructions mentioning `Stage2Result`, it returns valid JSON wrapped in `{"Stage2Result": {...}}`. `parser.py` attempts direct Pydantic validation via `Stage2Result.model_validate(data)`. Because `importance_score` is nested inside `data["Stage2Result"]`, validation raises `ValidationError`.
- **Consequence**: All five extraction tiers fail, and the parser invokes Tier 5 (Fallback Factory), replacing genuine AI intelligence with static default scores (`importance_score=2`).

#### Bug 2: Missing `duration_ms` in `record_call`
Production telemetry endpoint `GET /api/settings/provider-usage` reports `"avg_latency_ms": 0.0` and `"last_latency_ms": 0.0` for all active providers and models. In `groq.py:281`, `gemini.py:183`, and `mistral.py:134`, callers omit `duration_ms`, leaving it at the default `0.0`.

#### Bug 3: Redis Keyspace Completely Empty of AI Metrics
`redis-cli KEYS "*"` confirmed exactly 20 alert dedup keys and 0 usage tracking keys. `ProviderUsageTracker` stores all data in memory, making it vulnerable to process isolation and restart wipes.

### 4.6 Empirical Latency Distribution Table

| Provider & Model | Pipeline Stage | Sample Size | Min Latency | Median Latency | p95 Latency | Max Latency |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| **Mistral** `ministral-3b-2512` | Stage 0 Gatekeeper | 7 batches | 108 ms | **261 ms** | 361 ms | 361 ms |
| **Mistral** `ministral-8b-2512` | Stage 2 Scoring | 2 events | 1,180 ms | **1,220 ms** | 1,245 ms | 1,245 ms |
| **Groq** `qwen/qwen3.8-27b` | Stage 1 & Stage 4 | 4 events | 237 ms | **425 ms** | 510 ms | 510 ms |
| **Groq** `openai/gpt-oss-20b` | Stage 0 Gatekeeper | 8 requests | 315 ms | **450 ms** | 680 ms | 680 ms |
| **Groq** `openai/gpt-oss-120b` | Stage 3 Summarize | 3 requests | 699 ms | **825 ms** | 950 ms | 950 ms |
| **Gemini** `gemini-3.5-flash-lite` | Stage 2 Scoring (429) | 2 requests | 360 ms | **375 ms** | 390 ms | 390 ms |

---

## 5. Task-to-Model Routing Matrix & Capacity Budget Math (Requirement R4)

### 5.1 Comprehensive Task-to-Model Routing Matrix (Smart Mistral Decision Tiering + 2026 Quotas)
To achieve 24/7 operational stability within the exact 2026 quotas while prioritizing **smarter Mistral models (`mistral-large-2512` and `mistral-small-2603`) for high-stakes intelligence decisions**, each task is mapped by:
1. **Decision Criticality**: High-impact decisions—**Stage 2 Importance & Alert Scoring (`scoring`)**, **Stage 5 Executive Situational Summary (`situation_summary`)**, **Stage 1 Entity/Coordinate Extraction (`extraction`)**, and **Stage 3 BLUF Summarization (`summarization`)**—are routed to flagship/reasoning models (**`mistral:mistral-large-2512`**, **`mistral:mistral-small-2603`**, **`groq:openai/gpt-oss-120b`**, **`groq:qwen/qwen3.8-27b`**, and **`gemini:gemini-3.5-flash-lite`**) rather than `ministral-8b-2512`.
2. **Quota Partitioning across Groq Models & Keys**: Each Groq model (`qwen/qwen3.8-27b`, `openai/gpt-oss-120b`, `openai/gpt-oss-20b`) has an independent **1,000 RPD / 8,000 TPM / 200,000 TPD** bucket per key (**4,000 RPD / 32,000 TPM / 800,000 TPD per model across the 4-key pool**). Assigning each Groq model to a distinct primary stage prevents intra-model quota contention.
3. **Preserving 500 RPD Gemini Flash-Lite & Unlocking 28,800 RPD Gemma 4**: Because `gemini-3.5-flash-lite` and `gemini-3.1-flash-lite` each have only **500 RPD**, they are paired with **`mistral:mistral-small-2603` / `mistral:mistral-large-2512`** and backed by **`gemma-4-31b-it` (14,400 RPD)** and **`gemma-4-26b-a4b-it` (14,400 RPD)**.

| Pipeline Stage / Task | Decision Criticality | Primary Model | Fallback 1 | Fallback 2 | Fallback 3 / 4 | Rationale & Quota Math |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| **Stage 0: Gatekeeper** *(Micro-batched chatter & noise filter)* | **Pre-Filter** *(High Volume, Low Complexity)* | **`mistral:ministral-3b-2512`** *(12.5 RPS / 1.3M TPM)* | **`gemini:gemma-4-26b-a4b-it`** *(30 RPM / **14.4K RPD** / 16K TPM)* | **`groq:openai/gpt-oss-20b`** *(4-key pool: 4K RPD / 800K TPD)* | `cloudflare:@cf/meta/llama-3.2-1b-instruct` $\rightarrow$ `gemini:gemma-4-31b-it` *(14.4K RPD)* | Offloads Stage 0 from `openai/gpt-oss-20b` (which previously locked all 4 Groq keys in prod). `ministral-3b-2512` achieved 100% (7/7) success at **261ms median latency** in prod; `gemma-4-26b-a4b-it` provides a 14,400 RPD safety net. |
| **Stage 1: Extraction & Translation** *(Entities, coordinates, Arabic/Hebrew/Farsi translation)* | **HIGH** *(Factual Grounding & Geolocation)* | **`groq:qwen/qwen3.8-27b`** *(4-key pool: 120 RPM / **4K RPD** / **800K TPD**)* | **`mistral:mistral-small-2603`** *(Smart Reasoning \| 1.0 RPS / 500K TPM)* | **`gemini:gemini-3.5-flash-lite`** *(15 RPM / 250K TPM / 500 RPD)* | `gemini:gemma-4-31b-it` *(14.4K RPD)* $\rightarrow$ `gemini:gemini-3.1-flash-lite` *(500 RPD)* $\rightarrow$ `cloudflare:@cf/meta/llama-3.1-8b-instruct-fp8-fast` | Dedicates `qwen/qwen3.8-27b` (4,000 RPD across 4 keys, best multilingual Qwen tokenizer) to Stage 1. Upgrades Fallback 1 from `ministral-8b-2512` to **`mistral-small-2603`** for superior entity/coordinate extraction and translation accuracy. |
| **Stage 2: Scoring** *(1–10 Threat Score, 4 Calibration Guardrails, Alert Trigger)* | **CRITICAL** *(Triggers Telegram & Live Alerts)* | **`mistral:mistral-small-2603`** *(Smart Reasoning \| 1.0 RPS / 500K TPM)* | **`mistral:mistral-large-2512`** *(Flagship Reasoning \| 1.0 RPS / 500K TPM)* | **`gemini:gemini-3.5-flash-lite`** *(15 RPM / 250K TPM / 500 RPD)* | `gemini:gemini-3.1-flash-lite` *(500 RPD)* $\rightarrow$ `groq:openai/gpt-oss-120b` *(4K RPD)* $\rightarrow$ `gemini:gemma-4-31b-it` *(14.4K RPD)* | **Upgrades the core alert decision from `gemini-3.5-flash-lite` (which exhausted at 504/500 RPD) and `ministral-8b-2512` to `mistral-small-2603` + `mistral-large-2512`**, backed by both Gemini Flash-Lite buckets (1,000 RPD combined) and `gpt-oss-120b`. |
| **Stage 3: Summarization** *(Intelligence Headline <12w & Tactical BLUF)* | **HIGH** *(Operator-Facing Alert & Feed Copy)* | **`groq:openai/gpt-oss-120b`** *(4-key pool: 120 RPM / **4K RPD** / **800K TPD**)* | **`mistral:mistral-small-2603`** *(Smart Reasoning \| 1.0 RPS / 500K TPM)* | **`mistral:mistral-large-2512`** *(Flagship \| 1.0 RPS / 500K TPM)* | `gemini:gemini-3.5-flash-lite` *(500 RPD)* $\rightarrow$ `gemini:gemma-4-31b-it` *(14.4K RPD)* $\rightarrow$ `aihubmix:coding-glm-5.3-flash-free` | Dedicates `openai/gpt-oss-120b` (4,000 RPD / 800K TPD across 4 keys) to synthesis; replaces `ministral-8b-2512` with **`mistral-small-2603`** and **`mistral-large-2512`** to prevent degenerate headlines. |
| **Stage 4: Visuals** *(Threat category & cluster tags)* | **MODERATE** *(UI Symbology & Tagging)* | **`groq:openai/gpt-oss-20b`** *(4-key pool: 120 RPM / **4K RPD** / **800K TPD**)* | **`gemini:gemma-4-26b-a4b-it`** *(30 RPM / **14.4K RPD** / 16K TPM)* | **`mistral:ministral-8b-2512`** *(3.13 RPS / 625K TPM)* | `cloudflare:@cf/meta/llama-3.2-3b-instruct` $\rightarrow$ `gemini:gemini-3.1-flash-lite` *(500 RPD)* | Moves Stage 4 off `qwen/qwen3.8-27b` onto `openai/gpt-oss-20b` (now freed from Stage 0) and `gemma-4-26b-a4b-it` (14.4K RPD), keeping `ministral-8b-2512` only for lightweight tag classification. |
| **Stage 5: Situational Summary (`situation_summary`)** *(6h Rolling SitRep & On-Demand Executive Briefs)* | **CRITICAL** *(Executive SitRep & Strategic Forecast)* | **`mistral:mistral-large-2512`** *(Flagship Reasoning \| 1.0 RPS / 500K TPM)* | **`groq:openai/gpt-oss-120b`** *(4-key pool: 4K RPD / 800K TPD)* | **`aihubmix:coding-glm-5.3-free`** *(10 RPM / 100 RPD / 1M TPD)* | `mistral:mistral-small-2603` $\rightarrow$ `gemini:gemma-4-31b-it` *(14.4K RPD)* $\rightarrow$ `gemini:gemini-3.5-flash-lite` | Uses flagship **`mistral-large-2512`** for the 48 scheduled daily SitReps + human on-demand briefings, backed by `openai/gpt-oss-120b` (120B reasoning) and `coding-glm-5.3-free` (1M TPD). |
| **Memory Rollup Service** *(Daily 02:00 UTC thematic cluster rollup)* | **HIGH** *(Long-Context Historical Synthesis)* | **`aihubmix:coding-glm-5.3-free`** *(or `coding-minimax-m3-free`)* | **`mistral:mistral-large-2512`** *(Flagship \| 500K TPM)* | **`groq:openai/gpt-oss-120b`** *(4-key pool)* | `gemini:gemma-4-31b-it` *(14.4K RPD)* | Executes 1x/day; leverages AIHubMix's 1M TPD and **`mistral-large-2512`** for deep historical cluster synthesis. |
| **Embedding Service** *(Semantic vectors & live scoring neighborhood)* | **INFRASTRUCTURE** *(Corroboration & Deduplication)* | **`gemini:gemini-embedding-2`** *(100 RPM / 30K TPM / **1K RPD**)* | **`gemini:gemini-embedding-001`** *(100 RPM / 30K TPM / **1K RPD**)* | **`huggingface:hf-space-tei`** *(Persistent Space \| ~3,000 RPM / **Unlimited RPD**)* | Local/Deterministic Fallback | Combines both Gemini Embedding tiers (**2,000 RPD total** at 3072d) with automatic overflow to the persistent Hugging Face TEI Space (`HF_EMBEDDING_URL`) so daily volume >2,000 events never fails. |

---

### 5.2 Operational Load Profiles & Pipeline Reduction Dynamics

#### 5.2.1 Operational Load Profiles
- **Baseline Load**: Continuous monitoring ingestion of **5 to 10 incoming raw events per minute** (~10,000 raw events/day).
- **Crisis Surge Load**: Elevated regional conflict escalation of **30 to 60 incoming raw events per minute** (~60,000 raw events/day).
- **Extreme Peak Burst**: Instantaneous kinetic barrage of **100 incoming raw events per minute**.

#### 5.2.2 Event Reduction Pipeline Dynamics
1. **Stage 0 Gatekeeper Filtering (`stage0_gatekeeper.py`)**:
   - **Layer 1 (Deterministic Regex Fast-Pass / Fast-Drop)**: Filters ~30% of incoming raw items with zero LLM calls.
   - **Layer 2 (LLM Micro-Batching, `batch_size=6–10`)**: Evaluates remaining 70% in micro-batches.
     - Baseline (10 raw epm): **1 to 2 micro-batch calls/min** (~45–1,200 batch calls/day).
     - Crisis Surge (60 raw epm): **6 to 7 micro-batch calls/min**.
   - **Empirical Drop Rate**: **90%–94.3%** of evaluated raw items are rejected as noise before Stages 1–4.
2. **Surviving Relevant Events Entering Stages 1–4**:
   - **Baseline**: **~0.8 to 1.0 surviving event/min** (~600–1,000 events/day).
   - **Crisis Surge**: **~5.0 to 6.0 surviving events/min** (~1,080–2,500 events/day across multi-hour escalations).
   - **Extreme Peak Burst**: **~10.0 surviving events/min**.

---

### 5.3 Multi-Tier Load Profile Capacity & Budget Math Proofs (2026 Quotas)

#### 5.3.1 Partitioned Groq 4-Key Pool Math (`1K RPD / 8K TPM / 200K TPD` per key per model)
With 4 keys in `GroqKeyPool`, each model has **4,000 RPD, 32,000 TPM (4 × 8,000 TPM), and 800,000 TPD (4 × 200,000 TPD)** when keys are in separate projects/orgs (or **1,000 RPD / 8,000 TPM / 200,000 TPD** per org):
- **Stage 1 (`groq:qwen/qwen3.8-27b`)**:
  - Dedicated 100% to Stage 1 (removed from Stage 4 Visuals).
  - Baseline demand (~600 events/day, ~900 tokens/call with prefix caching reducing uncached input tokens by ~50%):
    - RPD Utilization: $\frac{600}{4,000} = \mathbf{15.0\% \text{ of 4-key pool RPD}}$ (or 60% of a single org's 1,000 RPD).
    - Effective uncached tokens/call $\approx 550\text{ tokens} \implies 600 \times 550 = \mathbf{330,000 \text{ TPD}}$ ($\mathbf{41.2\%}$ of 800K 4-key TPD; if a single-org 200K TPD cap is reached, seamlessly fails over to **`mistral:mistral-small-2603`** and **`gemini:gemma-4-31b-it`**).
    - Surge TPM (6 events/min $\times 550$ uncached tokens): **3,300 TPM** ($\mathbf{41.2\%}$ of a single key's 8,000 TPM limit, **10.3%** of 4-key 32,000 TPM pool).
- **Stage 3 (`groq:openai/gpt-oss-120b`)**:
  - Dedicated to Stage 3 Summarization + Stage 5 Fallback.
  - Baseline demand (~600 events/day, ~500 uncached tokens/call):
    - RPD Utilization: $\frac{600}{4,000} = \mathbf{15.0\% \text{ of 4-key pool RPD}}$.
    - TPD Utilization: $600 \times 500 = \mathbf{300,000 \text{ TPD}}$ ($\mathbf{37.5\%}$ of 800K 4-key TPD).
    - Surge TPM (6 events/min $\times 500$ tokens): **3,000 TPM** ($\mathbf{37.5\%}$ of a single key's 8,000 TPM limit).
- **Stage 4 (`groq:openai/gpt-oss-20b`)**:
  - Dedicated to Stage 4 Visuals (removed from Stage 0 Gatekeeper!).
  - Baseline demand (~600 events/day, ~400 uncached tokens/call):
    - RPD Utilization: $\frac{600}{4,000} = \mathbf{15.0\% \text{ of 4-key pool RPD}}$.
    - TPD Utilization: $600 \times 400 = \mathbf{240,000 \text{ TPD}}$ ($\mathbf{30.0\%}$ of 800K 4-key TPD).

#### 5.3.2 Smart Mistral Tiering Math (`mistral-large-2512`, `mistral-small-2603`, `ministral-3b-2512`)
- **Stage 0 Gatekeeper (`mistral:ministral-3b-2512`)**:
  - Capacity: **12.5 RPS (750 RPM)**, **1,300,000 TPM**.
  - Demand: 1.5 RPM (baseline) to 6.0 RPM (surge) = **0.2% to 0.8% of 750 RPM limit**; ~4,500 TPM = **0.35% of 1.3M TPM**.
- **Stage 2 Scoring (`mistral:mistral-small-2603` Primary $\rightarrow$ `mistral:mistral-large-2512` Fallback 1)**:
  - Capacity: **1.0 RPS (60 RPM)**, **500,000 TPM**.
  - Demand: 1.0 RPM (baseline, ~600 RPD) to 6.0 RPM (surge, ~1,080 RPD) = **1.7% to 10.0% of 60 RPM limit**; ~4,800 TPM = **0.96% of 500K TPM**.
  - Monthly Credit Sustainability: `mistral-small-2603` ($0.10/1M in, $0.30/1M out) consumes only **~$0.06/day (~$1.80/month)** for 100% of Stage 2 Scoring decisions, preserving the $10 monthly Experiment credit while delivering 24B-class reasoning on every alert threshold decision!
- **Stage 5 Situational Summary (`mistral:mistral-large-2512` Primary)**:
  - Demand: 48 scheduled SitReps/day (1 every 30 min) + ~5 on-demand human SitReps/day = **53 RPD** (~0.04 RPM << 60 RPM).
  - Token volume: $53 \times 3,500\text{ tokens} \approx 185\text{K tokens/day}$, giving flagship **`mistral-large-2512`** intelligence on all executive SitReps with automatic failover to `groq:openai/gpt-oss-120b` and `aihubmix:coding-glm-5.3-free`.

#### 5.3.3 Google Gemini Flash-Lite (`500 RPD` each) & Gemma 4 (`14.4K RPD / 16K TPM` each) Math
- **`gemini-3.5-flash-lite` (500 RPD / 15 RPM / 250K TPM) & `gemini-3.1-flash-lite` (500 RPD / 15 RPM / 250K TPM)**:
  - Previously exhausted (`504/500` and `505/500`) because `gemini-3.5-flash-lite` was Primary on Stage 2 Scoring and Fallback 1 on Stages 1, 3, and 4.
  - By moving Stage 2 Primary to **`mistral:mistral-small-2603`** and placing `gemini-3.5-flash-lite` / `gemini-3.1-flash-lite` as precision fallbacks, their combined **1,000 RPD** budget is preserved for burst absorption (<30% daily utilization).
- **`gemma-4-26b-a4b-it` (14,400 RPD / 30 RPM / 16K TPM) & `gemma-4-31b-it` (14,400 RPD / 30 RPM / 16K TPM)**:
  - Combined daily capacity: **28,800 RPD** and **60 RPM** across both models.
  - With the fixed token estimator (`1.6 chars/token` for non-Latin scripts + `1,000` thinking-token reservation) and strict **12,500 TPM** client sliding-window cap in `gemini.py`, Gemma 4 stays strictly below its **16,000 TPM** ceiling (eliminating the `26.12K / 16K` TPM burst) while providing 28,800 RPD of high-capacity fallback across all stages.

---

## 6. Prioritized Engineering Implementation Plan

The engineering implementation is organized into four sequential, verifiable phases.

```
┌────────────────────────────────────────────────────────────────────────┐
│ Phase 1: High-Priority Fixes (Critical Bugs & Failure Modes)           │
│   ├── Task 1.1: Root Schema Unwrapping in parser.py                    │
│   ├── Task 1.2: Pass duration_ms in record_call Across All Providers   │
│   ├── Task 1.3: Release self._db_lock Before External Network Calls    │
│   └── Task 1.4: Right-Size Groq max_tokens & Enable Prefix Caching     │
└───────────────────────────────────┬────────────────────────────────────┘
                                    ▼
┌────────────────────────────────────────────────────────────────────────┐
│ Phase 2: Quota & Rate-Limiting Hardening (2026 Console Specs)          │
│   ├── Task 2.1: Migrate UsageTracker to Redis Sliding Windows          │
│   ├── Task 2.2: Model-Aware Pacing for Gemini/Gemma, Mistral & Groq    │
│   ├── Task 2.3: Per-Model Header Tracking & Proactive 8K TPM Rotation  │
│   ├── Task 2.4: Activate Cloudflare Token-to-Neuron Accounting         │
│   └── Task 2.5: AIHubMix Quota Refund & Embedding RPD/503 Hardening    │
└───────────────────────────────────┬────────────────────────────────────┘
                                    ▼
┌────────────────────────────────────────────────────────────────────────┐
│ Phase 3: True Circuit Breaker & Resilient Fallback Orchestration       │
│   ├── Task 3.1: Formal 3-State CircuitBreaker Implementation           │
│   └── Task 3.2: Wire Circuit Breaker into Orchestrator Fallback Engine │
└───────────────────────────────────┬────────────────────────────────────┘
                                    ▼
┌────────────────────────────────────────────────────────────────────────┐
│ Phase 4: Smart Mistral & 2026 Quota Stage Re-Routing                   │
│   ├── Task 4.1: Update Stage Routing Matrix (Backend + Frontend UI)    │
│   └── Task 4.2: Add Health & Rate-Limit Self-Test Suite                │
└────────────────────────────────────────────────────────────────────────┘
```

---

### Phase 1: High-Priority Fixes (Critical Bugs & Failure Modes)

#### Task 1.1: Fix Root Schema Name Wrapping in `parser.py`
- **Target File**: `backend/app/pipeline/parser.py` (line 16)
- **Target Function**: `extract_and_parse_json(raw_text: str, target_model: Type[T], fallback_factory: Optional[Callable[[], T]] = None) -> T`
- **Exact Modification**:
  In Tier 1, Tier 2, Tier 3, and Tier 4, immediately after `json.loads(candidate_str)`, inspect if `isinstance(data, dict)`. If `data` contains a single root key matching `target_model.__name__`, `target_model.__name__.lower()`, or snake_case conversion of `target_model.__name__`, unwrap `data = data[root_key]` (e.g. unwrapping `{"Stage2Result": {...}}` before `Stage2Result.model_validate(data)`).
  Also add a general single-key wrapper check: if `len(data) == 1` and the sole value is a `dict` whose keys overlap with `target_model.model_fields`, unwrap that inner dictionary.
- **Verification Command**:
  ```powershell
  python -c "from backend.app.pipeline.parser import extract_and_parse_json; from backend.app.pipeline.models import Stage2Result; raw = '{\"Stage2Result\": {\"importance_score\": 8, \"score_rationale\": \"strategic escalation\"}}'; res = extract_and_parse_json(raw, Stage2Result); assert res.importance_score == 8; print('Unwrap verified.')"
  ```

#### Task 1.2: Pass `duration_ms` in `record_call` Across All Provider Adapters
- **Target Files**:
  - `backend/app/pipeline/providers/groq.py` (lines 144, 281)
  - `backend/app/pipeline/providers/gemini.py` (line 183)
  - `backend/app/pipeline/providers/mistral.py` (line 134)
  - `backend/app/pipeline/providers/cloudflare.py` (line 220)
  - `backend/app/pipeline/providers/aihubmix.py` (line 255)
- **Exact Modification**:
  Capture `t_start = time.perf_counter()` immediately before dispatching the HTTP request, compute `duration_ms = (time.perf_counter() - t_start) * 1000.0` upon response receipt, and pass `duration_ms=duration_ms` into `get_usage_tracker().record_call(...)`.
- **Verification Command**:
  ```powershell
  pytest backend/tests/test_usage_tracker.py -v
  ```

#### Task 1.3: Release `self._db_lock` Before External Embedding Calls
- **Target File**: `backend/app/pipeline/orchestrator.py` (lines 1042–1065)
- **Target Method**: `PipelineOrchestrator._build_pre_stage2_context(...)`
- **Exact Modification**:
  Remove `async with self._db_lock:` wrapping `await asyncio.shield(_fetch_neighborhood())` so external HTTP calls to `gemini-embedding-2` / `gemini-embedding-001` / `hf-space-tei` never block database writes or concurrent pipeline workers.
- **Verification Command**:
  ```powershell
  pytest backend/tests/test_m3_pipeline.py -v
  ```

#### Task 1.4: Right-Size Groq `max_tokens` for 8K TPM & Maximize Prompt Prefix Caching
- **Target File**: `backend/app/pipeline/providers/groq.py` (lines 238–265)
- **Exact Modification**:
  1. Instead of forcing `effective_max_tokens = max(max_tokens, 3072)` on every `gpt-oss` / `qwen` JSON request (which saturates the **8,000 TPM** cap in 2–3 calls), scale `effective_max_tokens` to `max(max_tokens, 1536)` for standard JSON calls (and `2560` only when `max_tokens >= 2000` such as `situation_summary`).
  2. When `json_mode=True`, ensure the static `"Provide output strictly in valid JSON format."` directive is appended to `system_prompt` (the cached prefix) rather than mutating the end of `user_content`, preserving deterministic prefix caching on Groq (`Cached tokens do not count towards your rate limits`).
- **Verification Command**:
  ```powershell
  pytest backend/tests/test_groq_key_pool.py -v
  ```

---

### Phase 2: Quota & Rate-Limiting Hardening (2026 Console Specs)

#### Task 2.1: Migrate `UsageTracker` Sliding Windows and Daily Quotas to Redis
- **Target File**: `backend/app/pipeline/usage_tracker.py`
- **Target Class**: `ProviderUsageTracker`
- **Exact Modification**:
  1. Persist provider, model, and key counters plus daily RPD/TPD tallies (`usage:daily:{provider}:{model}:{YYYYMMDD}`) in Redis via `get_redis_bus()` with 48h TTL (`EXPIRE 172800`), while retaining in-memory dictionaries as a fast synchronous cache and fallback when Redis is disconnected.
  2. Enforce explicit daily RPD sentinels matching the user's live console limits:
     - `gemini:gemini-3.5-flash-lite`: `500 RPD` (`15 RPM`, `250K TPM`)
     - `gemini:gemini-3.1-flash-lite`: `500 RPD` (`15 RPM`, `250K TPM`)
     - `gemini:gemma-4-26b-a4b-it`: `14,400 RPD` (`30 RPM`, `16K TPM`)
     - `gemini:gemma-4-31b-it`: `14,400 RPD` (`30 RPM`, `16K TPM`)
     - `groq:openai/gpt-oss-120b`, `groq:openai/gpt-oss-20b`, `groq:qwen/qwen3.8-27b`: `1,000 RPD`, `8,000 TPM`, `200,000 TPD` per key.
- **Verification Command**:
  ```powershell
  pytest backend/tests/test_usage_tracker.py -v
  ```

#### Task 2.2: Model-Aware Pacing for Gemini/Gemma (`16K TPM` Fix) & Mistral (`1.0 RPS` vs `12.5 RPS`)
- **Target Files**:
  - `backend/app/pipeline/providers/gemini.py` (lines 50–59, 131–140)
  - `backend/app/pipeline/providers/mistral.py` (lines 57–61, 76–112)
- **Exact Modification**:
  1. **In `GeminiProvider` (`gemini.py`)**:
     - For `gemma-4-26b-a4b-it` and `gemma-4-31b-it`: Set `self.max_rpm = 28` (official limit **30 RPM**), `self.min_interval_sec = 2.1`, and `self.max_tpm = 12500` (safe margin below **16,000 TPM**).
     - Fix token estimation for non-Latin scripts and Gemma thinking tokens: compute `non_ascii_chars = sum(1 for c in prompt_text if ord(c) > 127)`, `ascii_chars = len(prompt_text) - non_ascii_chars`, `est_input_tokens = int(ascii_chars / 3.8 + non_ascii_chars / 1.5) + 25`, and for Gemma add a conservative `+ 1100` token reservation for internal `thought` + output tokens (instead of `+ 2048` or underestimating Arabic text).
     - For `gemini-3.5-flash-lite` and `gemini-3.1-flash-lite`: Set `self.max_rpm = 14` (official limit **15 RPM**), `self.min_interval_sec = 4.1`, and `self.max_tpm = 220000` (official limit **250,000 TPM**).
  2. **In `MistralProvider` (`mistral.py`)**:
     - Make pacing model-aware based on `model_name`:
       - `mistral-large-2512` and `mistral-small-2603` (1.0 RPS / 60 RPM): `min_interval_sec = 1.05`, `max_rpm = 55`, `max_tpm = 450000`.
       - `ministral-8b-2512` (3.13 RPS / 187 RPM): `min_interval_sec = 0.35`, `max_rpm = 170`, `max_tpm = 550000`.
       - `ministral-3b-2512` (12.5 RPS / 750 RPM): `min_interval_sec = 0.12`, `max_rpm = 500`, `max_tpm = 1100000`.
- **Verification Command**:
  ```powershell
  pytest backend/tests/test_new_providers.py -v
  ```

#### Task 2.3: Per-Model Header Tracking & Proactive 8K TPM Key Rotation in `GroqKeyPool`
- **Target File**: `backend/app/pipeline/providers/key_pool.py` (lines 109–160, 262–397)
- **Exact Modification**:
  1. Track `remaining_requests` (RPD), `remaining_tokens` (TPM), `reset_requests_sec`, and `reset_tokens_sec` **per model** (`self._model_headers[norm_model]`) in addition to the last-seen key summary, since Groq enforces limits per `(organization, model)`.
  2. **Proactive TPM Key Rotation on HTTP 200**: Because `x-ratelimit-remaining-tokens` is **always TPM (out of 8,000)**, when an HTTP 200 response reports `rem_tokens is not None and rem_tokens < 1200` and `reset_tokens_sec > 0`, proactively set `self._model_cooldowns[norm_m] = now + min(reset_tokens_sec, 15.0)` on that key so `acquire_next_key(model=model_name)` rotates to the next healthy key in the pool *before* hitting HTTP 429!
  3. **Accurate TPD / RPD Exhaustion Classification**: On HTTP 429, if `retry_after > 90.0` (e.g., `199s–1,042s` rolling 200K TPD / 1K RPD window lockouts observed in production) or `rem_requests == 0`, classify `is_daily = True` so telemetry accurately increments `daily_quota_exhaustions` and the orchestrator fails over immediately when all keys are locked.
- **Verification Command**:
  ```powershell
  pytest backend/tests/test_groq_key_pool.py -v
  ```

#### Task 2.4: Activate Token-to-Neuron Accounting in `CloudflareProvider`
- **Target File**: `backend/app/pipeline/providers/cloudflare.py`
- **Exact Modification**:
  Compute neurons consumed from `prompt_tokens` and `completion_tokens` in `generate_text()` and invoke `self.neuron_tracker.record_neurons(neurons)`. Check `has_budget()` prior to dispatch so the 10,000 Neuron/day cap is enforced.
- **Verification Command**:
  ```powershell
  pytest backend/tests/test_new_providers.py -k "cloudflare" -v
  ```

#### Task 2.5: Pre-Call Quota Refund in `AIHubMixQuotaTracker` & Embedding RPD/503 Hardening
- **Target Files**:
  - `backend/app/pipeline/providers/aihubmix.py`
  - `backend/app/services/embedding_service.py`
- **Exact Modification**:
  1. Add `refund_request()` to `AIHubMixQuotaTracker` and invoke it if the HTTP request raises an exception prior to completion, and record failed/429 calls in `get_usage_tracker().record_call()`.
  2. In `embedding_service.py`:
     - Check `get_quota_tracker()` before calling `gemini-embedding-2` (1,000 RPD) or `gemini-embedding-001` (1,000 RPD); when a model reaches `>= 950` RPD (`962/1K` observed in prod), proactively route to the secondary model or `hf-space-tei` (`HF_EMBEDDING_URL`) without waiting for a 429.
     - In `call_hf_tei_api()`, include header `'x-wait-for-model': 'true'` and retry on HTTP 503 using `estimated_time`.
- **Verification Command**:
  ```powershell
  pytest backend/tests/test_aihubmix_provider.py backend/tests/test_embeddings_map.py -v
  ```

---

### Phase 3: True Circuit Breaker & Resilient Fallback Orchestration

#### Task 3.1: Formal 3-State `CircuitBreaker` Implementation
- **Target File**: Create `backend/app/pipeline/circuit_breaker.py`
- **Target Class**: `CircuitBreaker`
- **Specification**:
  - **States**: `State.CLOSED` (normal operation), `State.OPEN` (failing fast, zero outbound calls), `State.HALF_OPEN` (single probe request allowed).
  - **Configurable Thresholds**: `failure_threshold: int = 4` (consecutive 5xx errors or network timeouts), `recovery_timeout_sec: float = 60.0`, `success_threshold: int = 1`.
  - **Methods**: `record_success()`, `record_failure(error: Exception)`, `can_execute() -> bool`.
- **Verification Command**:
  ```powershell
  pytest backend/tests/test_circuit_breaker.py -v
  ```

#### Task 3.2: Wire Circuit Breaker into Orchestrator Fallback Engine
- **Target File**: `backend/app/pipeline/orchestrator.py`
- **Target Method**: `_execute_stage_with_fallbacks(...)`
- **Exact Modification**:
  Maintain a registry of `CircuitBreaker` instances keyed by `f"{provider}:{model_name}"`. Skip any candidate whose circuit is `OPEN`, record `record_success()` on completion, and record `record_failure()` on HTTP 5xx or timeout so dead endpoints trip open instead of stalling every event for 30–45s.
- **Verification Command**:
  ```powershell
  pytest backend/tests/test_m3_pipeline.py -v
  ```

---

### Phase 4: Smart Mistral & 2026 Quota Stage Re-Routing

#### Task 4.1: Update Default Pipeline Stage Routing Matrix (Backend & Frontend UI)
- **Target Files**:
  - `backend/app/pipeline/orchestrator.py:111-198` (`_init_default_configs()`)
  - `backend/app/api/settings.py:24-105` (`DEFAULT_AI_STAGES`)
  - `frontend/src/components/settings/AIConfigMatrix.tsx:42-114` (`defaultGatekeeperStage`, `defaultSituationSummaryStage`, initial state)
- **Exact Modification**:
  Synchronize default stage routing across `orchestrator.py`, `settings.py`, and `AIConfigMatrix.tsx` to use **smarter Mistral models (`mistral-large-2512` and `mistral-small-2603`) for important decisions** and partition Groq / Gemini / Gemma 4 models according to their true 2026 rate limits:
  - **`gatekeeper` (Stage 0)**:
    - Primary: `mistral:ministral-3b-2512`
    - Fallbacks: `gemini:gemma-4-26b-a4b-it` $\rightarrow$ `groq:openai/gpt-oss-20b` $\rightarrow$ `cloudflare:@cf/meta/llama-3.2-1b-instruct` $\rightarrow$ `gemini:gemma-4-31b-it` $\rightarrow$ `zai:glm-4.7-flash`
  - **`extraction` (Stage 1 — Multilingual NLP & Entities)**:
    - Primary: `groq:qwen/qwen3.8-27b`
    - Fallbacks: **`mistral:mistral-small-2603`** $\rightarrow$ `gemini:gemini-3.5-flash-lite` $\rightarrow$ `gemini:gemma-4-31b-it` $\rightarrow$ `gemini:gemini-3.1-flash-lite` $\rightarrow$ `cloudflare:@cf/meta/llama-3.1-8b-instruct-fp8-fast` $\rightarrow$ `groq:openai/gpt-oss-120b`
  - **`scoring` (Stage 2 — Critical 1–10 Importance & Alert Decision)**:
    - Primary: **`mistral:mistral-small-2603`**
    - Fallbacks: **`mistral:mistral-large-2512`** $\rightarrow$ `gemini:gemini-3.5-flash-lite` $\rightarrow$ `gemini:gemini-3.1-flash-lite` $\rightarrow$ `groq:openai/gpt-oss-120b` $\rightarrow$ `gemini:gemma-4-31b-it` $\rightarrow$ `groq:qwen/qwen3.8-27b`
  - **`summarization` (Stage 3 — Tactical BLUF & Headline)**:
    - Primary: `groq:openai/gpt-oss-120b`
    - Fallbacks: **`mistral:mistral-small-2603`** $\rightarrow$ **`mistral:mistral-large-2512`** $\rightarrow$ `gemini:gemini-3.5-flash-lite` $\rightarrow$ `gemini:gemma-4-31b-it` $\rightarrow$ `aihubmix:coding-glm-5.3-flash-free`
  - **`visuals` (Stage 4 — Symbology & Trend Tags)**:
    - Primary: `groq:openai/gpt-oss-20b`
    - Fallbacks: `gemini:gemma-4-26b-a4b-it` $\rightarrow$ `mistral:ministral-8b-2512` $\rightarrow$ `cloudflare:@cf/meta/llama-3.2-3b-instruct` $\rightarrow$ `gemini:gemini-3.1-flash-lite`
  - **`situation_summary` (Stage 5 — Executive SitRep & Narrative Fusion)**:
    - Primary: **`mistral:mistral-large-2512`** (or `groq:openai/gpt-oss-120b` with `mistral:mistral-large-2512` Fallback 1)
    - Fallbacks: `groq:openai/gpt-oss-120b` $\rightarrow$ `aihubmix:coding-glm-5.3-free` $\rightarrow$ **`mistral:mistral-small-2603`** $\rightarrow$ `gemini:gemma-4-31b-it` $\rightarrow$ `gemini:gemini-3.5-flash-lite`
- **Verification Command**:
  ```powershell
  pytest backend/tests/test_m3_pipeline.py backend/tests/test_summary_api.py -v
  ```

#### Task 4.2: Add Health & Rate-Limit Self-Test Suite
- **Target File**: `backend/tests/test_ai_infrastructure_health.py`
- **Exact Content**:
  Unit tests validating:
  1. `GroqKeyPool` per-model header isolation (`x-ratelimit-remaining-requests` = RPD, `x-ratelimit-remaining-tokens` = 8K TPM) and proactive rotation when `rem_tokens < 1200`.
  2. `GeminiProvider` model-aware pacing (`30 RPM / 12.5K TPM` for `gemma-4-26b-a4b-it` & `gemma-4-31b-it` vs `15 RPM / 250K TPM` for `gemini-3.5-flash-lite` & `gemini-3.1-flash-lite`) and non-Latin token estimation.
  3. `MistralProvider` model-aware pacing (`1.05s` for `mistral-large-2512` & `mistral-small-2603` vs `0.12s` for `ministral-3b-2512`).
  4. `CircuitBreaker` state transitions (`CLOSED` -> `OPEN` -> `HALF_OPEN` -> `CLOSED`).
  5. `parser.py` root schema unwrapping (`{"Stage2Result": {...}}`) across all stage models.
- **Verification Command**:
  ```powershell
  pytest backend/tests/test_ai_infrastructure_health.py -v
  ```

---

## 7. Public Repository Safety & Non-Mutation Attestation

### 7.1 Non-Mutation Attestation
In strict adherence to the mandate for this audit phase:
- Worker introduced zero modifications to application source files.
- Pre-existing uncommitted modifications on 8 files (`backend/app/pipeline/models.py`, `backend/app/pipeline/orchestrator.py`, `backend/app/pipeline/providers/mock.py`, `backend/app/services/summary_service.py`, `backend/tests/test_summary_api.py`, `frontend/src/app/display/page.tsx`, `frontend/src/app/summary/page.tsx`, `frontend/src/lib/types.ts`) originated between 00:58Z and 01:07Z prior to the dispatch of `orchestrator_5` at 01:27Z.
- Only `AI_INFRASTRUCTURE_AUDIT_AND_PLAN.md` and metadata in `.agents/teamwork/` were created during this audit phase.

### 7.2 Secret & Infrastructure Sanitization Attestation
In strict adherence to `.agents/skills/public-repo-safety/SKILL.md`:
- **Zero Real API Keys or Tokens**: No actual Google Gemini, Groq, Mistral, AIHubMix, Cloudflare, Telegram, or Hugging Face secrets are present in this document. All references utilize environment variable names (e.g., `GEMINI_API_KEY`, `GROQ_API_KEYS`) or masked fingerprints (e.g., `gsk_...2e4P`, `mstr...72t9`).
- **Zero Private Server IP Addresses**: No Tailscale IP addresses (`100.x.y.z`) or Oracle VPS public IP addresses (`129.x.y.z`) are exposed. All host references use generic identifiers (`ubuntu@production`, `localhost`).
- **Zero Personal Workstation Paths**: No personal user directories are exposed in production configuration documentation.
