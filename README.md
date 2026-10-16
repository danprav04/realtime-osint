<div align="center">

<img src="frontend/public/icon.svg" alt="OSINT Realtime Logo" width="80" height="80" />

# 🛰️ Real-Time OSINT Gathering Service

### *Autonomous Multi-Source Geopolitical Intelligence Aggregator, AI Analysis Pipeline & Live Dashboard*

[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)
[![Python 3.11](https://img.shields.io/badge/Python-3.11+-3776AB.svg?logo=python&logoColor=white)](https://www.python.org/)
[![Next.js 14/15](https://img.shields.io/badge/Next.js-14%2F15-black.svg?logo=next.js&logoColor=white)](https://nextjs.org/)
[![Docker Compose](https://img.shields.io/badge/Docker-Compose-2496ED.svg?logo=docker&logoColor=white)](docker-compose.yml)
[![Tests: 332/332 Passing](https://img.shields.io/badge/Tests-332%20%2F%20332%20Passed%20(100%25)-success.svg)](tests/)
[![RAM Footprint: <= 830MB](https://img.shields.io/badge/RAM%20Budget-%E2%89%A4%20830MB%20(1GB%20VPS)-green.svg)](#-production-infrastructure--1gb-ram-budget)

<br/>

<p align="center">
  <img src="docs/images/dashboard_live_feed.png" alt="Real-Time OSINT Live Feed Dashboard" width="100%" style="border-radius: 12px; border: 1px solid #2A2C33;" />
</p>

*Figure 1: The real-time OSINT intelligence dashboard running live in production dark mode with streaming events, AI BLUF summaries, 1–10 importance scores, and multi-source attribution.*

</div>

---

## 📑 Table of Contents

- [Overview](#-overview)
- [System Architecture](#-system-architecture)
- [Dashboard & User Interface](#-dashboard--user-interface)
- [Multi-Stage AI Pipeline](#-multi-stage-ai-pipeline)
- [Zero-Code Dynamic Theme Engine](#-zero-code-dynamic-theme-engine)
- [Data Ingestion Plugins](#-data-ingestion-plugins)
- [Telegram Notification Bot](#-telegram-notification-bot)
- [Production Infrastructure & 1GB RAM Budget](#-production-infrastructure--1gb-ram-budget)
- [Quick Start Guide](#-quick-start-guide)
  - [Production: Docker Compose (Recommended)](#1-production-docker-compose-recommended)
  - [Local Development Setup](#2-local-development-setup)
- [Configuration Reference](#-configuration-reference)
- [Verification & Automated Test Suite](#-verification--automated-test-suite)
- [Repository Structure](#-repository-structure)
- [License](#-license)

---

## 🔎 Overview

The **Real-Time OSINT Gathering Service** is a modular intelligence gathering and monitoring system designed to run on resource-constrained servers (specifically tested and budgeted for a **1GB RAM Linux VPS**).

### What it does:
1. **Aggregates live information** from RSS feeds, Telegram channels, News APIs, Reddit, and Maritime AIS tracking without manual polling.
2. **Processes each incoming event through a 4-stage AI pipeline** (powered by Google Gemini and Groq Cloud) to translate foreign languages (Arabic/Farsi), assess strategic importance (1–10 scale), generate concise Bottom-Line-Up-Front (BLUF) summaries, and extract visual trend tags.
3. **Presents intelligence on a real-time dashboard** over WebSocket (`Socket.IO`), featuring a live chronological event feed, priority badges, detailed modal inspection, and volume/distribution analytics.
4. **Sends instant Telegram alerts** to a configured channel or chat when events score at or above a user-defined threshold.
5. **Enables zero-code topic switching**: change monitored keywords, entities, sources, and scoring criteria directly from the settings UI without touching any code or restarting the application.

---

## 🏛️ System Architecture

The application is structured into four main layers:

```mermaid
flowchart TD
    subgraph Sources["1. Ingestion Layer (Plugins)"]
        RSS["RSS Feeds\n(feedparser)"]
        TG["Telegram Channels\n(Telethon MTProto / Web)"]
        News["News APIs\n(NewsAPI / GDELT 2.0)"]
        Reddit["Reddit\n(AsyncPRAW / Public JSON)"]
        AIS["Maritime AIS\n(Digitraffic GeoJSON)"]
    end

    subgraph Backend["2. FastAPI Application (<380MB RAM)"]
        Supervisor["Ingestion Supervisor\n(Isolated Exception Boundaries)"]
        DB[("PostgreSQL 16\n(Persistent Storage)")]
        Bus["Redis Pub/Sub Bus\n(Events & Reload Signals)"]
        
        subgraph Pipeline["4-Stage AI Pipeline"]
            S1["Stage 1: Extract & Translate\n(Groq / Gemini)"]
            S2["Stage 2: Importance Scoring\n(1-10 Scale via Custom Prompt)"]
            S3["Stage 3: BLUF Summarization"]
            S4["Stage 4: Visuals & Trends"]
        end

        Gateway["Socket.IO ASGI Gateway\n(/socket.io, Maxlen=100 Buffer)"]
        Bot["Telegram Alert Dispatcher\n(Token Bucket + 24h Dedup)"]
    end

    subgraph Frontend["3. Next.js 14/15 Standalone UI (<220MB RAM)"]
        Feed["Live Timeline Feed\n(/)"]
        Trends["Analytics & Trends\n(/trends)"]
        Settings["Settings Control Center\n(/settings)"]
    end

    Sources -->|NormalizedEvent| Supervisor
    Supervisor --> Pipeline
    Pipeline --> DB
    Pipeline -->|Enriched Event| Bus
    Bus --> Gateway
    Bus --> Bot
    Gateway -->|WebSocket Push| Feed
    Gateway -->|Stats Broadcast| Trends
    Settings -->|REST PUT/POST| DB
    Settings -->|Config Reload Signal| Bus
    Bus -.->|Reconcile Sources| Supervisor
    Bus -.->|Update Prompts| Pipeline
```

---

## 🖥️ Dashboard & User Interface

The frontend is built with **Next.js 14/15 App Router** and styled with **Tailwind CSS** using a dark-mode palette designed for comfortable, low-glare operational monitoring (canvas `#08090A`, card containers `#141518`, subtle borders `#2A2C33`).

### 1. Main Timeline View (`/`)
The primary operations view provides continuous real-time intelligence monitoring without requiring manual page refreshes:
- **Streaming Telemetry Cards**:
  - **Total Ingested Events**: Real-time counter of all normalized and AI-analyzed intelligence items with an active `STREAMING` indicator.
  - **High Priority (Score ≥ 7)**: Instant count and percentage of critical threats with an amber `ALERT TRIGGER` warning badge.
  - **Active Monitored Sources**: Dynamic tally of active ingestion plugins across all 5 engine types (RSS, Telegram, NewsAPI, Reddit, AIS).
  - **Last Ingestion Heartbeat**: Relative time since the latest event with sub-second Pub/Sub stream latency.
- **Unified Intelligence Filter Bar**:
  - **Search Query Bar**: Full-text searching across raw content, AI summaries, named entities, and locations.
  - **Min Score Slider**: Adjustable threshold slider (from `All` up to `10`) to filter noise instantly in-memory.
  - **Source Type Filter Chips**: One-click toggles for `All Sources`, `RSS`, `Telegram`, `News APIs`, `Reddit`, and `Maritime AIS`.
  - **Live Stream Toggle**: Pause or resume incoming WebSocket event prepend animations on the fly.
- **Card-Slick Event Intelligence Items**:
  - **Source Attribution & Timing**: Clear source engine badges (e.g., `RSS BBC World News`, `TELEGRAM Channel`) with relative timestamps.
  - **Dynamic Importance Indicators**: Color-coded badges indicating severity (`0/10 LOW` through `10/10 CRITICAL`).
  - **BLUF Summaries**: Direct, actionable Bottom-Line-Up-Front syntheses highlighting key actors and tactical impact.
  - **Multilingual Support**: Foreign-language original text (Arabic, Hebrew, Farsi) presented with translated English titles and summaries.
  - **Extracted Entity Pills**: Clickable named entity tags (e.g. `#IRGC`, `#CENTCOM`, `#StraitOfHormuz`, `#Spain`).
  - **Deep Forensics (`Inspect ↗`)**: Slide-over drawer displaying full raw JSON metadata, translation details, and original source hyperlinks.

### 2. AI Executive Summary (`/summary`)
- **Multi-Source Situation Synthesis**: Generates structured executive briefings with inline `[Event #]` citations, consensus clusters, key developments, and strategic outlook across configurable lookback windows (`1h`, `6h`, `12h`, `24h`, `72h`).
- **Interactive Citation Inspection**: Click any inline citation badge to inspect the underlying corroborated source events and original raw wire text.

### 3. 2D Semantic Relevance Map (`/map`)
- **Spatial Intelligence Galaxy**: Projects high-dimensional event embeddings (`gemini-embedding-2` / `gemini-embedding-001` or self-hosted Hugging Face TEI `all-MiniLM-L6-v2`) onto an interactive 2D canvas with topical clustering and multi-source corroboration scoring.
- **Consensus & Trustworthiness Badges**: Highlights stories corroborated independently across multiple source engines (RSS, Telegram, NewsAPI, Reddit, AIS).

### 4. Analytics & Trends (`/trends` & `/`)
- **Hourly Volume Timeline**: Time-series bar visualization tracking event influx over 24-hour windows.
- **Score Distribution Histogram**: Density histogram showing distribution across the 1–10 severity scale.
- **Source Engine Activity**: Radial breakdown of ingested intelligence across all 5 active plugins.
- **Tactical Alert Wheel (`AlertWheel.tsx`)**: Radial regional siren and kinetic alert visualizer on the live feed.

### 5. Settings Control Center (`/settings`)
- **Theme Configuration**: Zero-code monitoring topic switching, keyword lists, entity tracking, alert thresholds, and dynamic AI scoring prompts.
- **Source Ingestion Manager**: Add/remove feed URLs, toggle active status, adjust polling frequencies, configure historical lookback windows, or execute manual on-demand triggers.
- **AI Matrix Configuration**: Independent provider (**Gemini** vs. **Groq**), primary model selection, multi-tier automatic fallback chains, and temperature tuning for each of the 4 pipeline stages.
- **API Key & Credentials Vault**: In-browser configuration for Gemini, Groq, Hugging Face Space TEI URL & token, NewsAPI, Reddit, and Telegram Bot credentials with live quota/rate-limit links.

---

## 🧠 Multi-Stage AI Pipeline

The pipeline decomposes raw content processing into four independent stages. Each stage can be assigned to either **Google Gemini** or **Groq Cloud** depending on speed and reasoning requirements:

| Stage | Default Model | Purpose & Contract | Fallback Behavior |
|---|---|---|---|
| **0. Gatekeeper** | `gemini: gemma-4-26b-a4b-it` | High-RPD micro-batched filter (up to 12 items/call) that drops non-actionable chatter before storage. | `gemma-4-31b-it` -> `openai/gpt-oss-20b` -> `qwen/qwen3.8-27b` -> Keyword fast-pass |
| **1. Extraction & Translation** | `groq: qwen/qwen3.8-27b` | Detects Arabic and Farsi Unicode script blocks and translates them into English. Preserves military acronyms (`CENTCOM`, `IRGC`, `IAEA`) and Persian zero-width non-joiners (`\u200c`). | `gemini-3.5-flash-lite` -> `gemini-3.1-flash-lite` -> `gemma-4-26b-a4b-it` -> `openai/gpt-oss-120b` -> Regex fallback |
| **2. Importance Scoring** | `gemini: gemini-3.5-flash-lite` | Evaluates the event on a strict 1–10 scale based on the user's custom scoring prompt for the active theme. | `gemini-3.1-flash-lite` -> `gemma-4-26b-a4b-it` -> `openai/gpt-oss-120b` -> `qwen/qwen3.8-27b` -> Keyword density heuristic |
| **3. BLUF Summarization** | `groq: openai/gpt-oss-120b` | Generates a 2-3 sentence Bottom-Line-Up-Front summary highlighting actors, kinetic action, and strategic impact. | `qwen/qwen3.8-27b` -> `gemini-3.5-flash-lite` -> `gemini-3.1-flash-lite` -> `gemma-4-26b-a4b-it` -> `openai/gpt-oss-20b` -> Extractive lead summary |
| **4. Visual Analytics** | `groq: openai/gpt-oss-20b` | Extracts thematic cluster tags, location names, and event categories for dashboard charts. | `qwen/qwen3.8-27b` -> `gemini-3.5-flash-lite` -> `gemini-3.1-flash-lite` -> `gemma-4-26b-a4b-it` -> Rule-based tag mapper |

```mermaid
flowchart LR
    Raw["Raw Normalized\nEvent"] --> S1["Stage 1: Extract & Translate\n(Preserves ZWNJ & Acronyms)"]
    S1 --> Fork{"Sequential\nExtraction"}
    Fork --> S2["Stage 2: Scoring\n(Guided by Theme Prompt)"]
    Fork --> S3["Stage 3: Summary\n(BLUF Format)"]
    Fork --> S4["Stage 4: Visuals\n(Tags & Charts)"]
    S2 --> Join["Enriched Event\nSaved to PostgreSQL"]
    S3 --> Join
    S4 --> Join
    Join --> Broadcast["Broadcast to Redis Bus\n(Socket.IO & Telegram)"]
```

### 5-Tier Resilient JSON Parser
All model outputs pass through `backend/app/pipeline/parser.py`, which features a 5-tier parse-repair mechanism:
1. Direct `json.loads` parsing.
2. Markdown code block extraction (` ```json ... ``` `).
3. Bracket and brace balancing for truncated outputs.
4. Regex object matching.
5. Heuristic fallback generation when the model response is completely unparseable or when rate limits (HTTP 429) are reached.

---

## 🎯 Zero-Code Dynamic Theme Engine

A theme defines the entire scope of what the service monitors:
- **Name & Description**: e.g., *"US-Iran conflict"* or *"Taiwan Strait Monitoring"*
- **Tracked Keywords**: List of strings matched during ingestion.
- **Named Entities**: Key organizations, leaders, and military branches to extract.
- **Scoring Prompt**: A natural language instruction telling the AI how to evaluate importance for this specific topic.
- **Alert Threshold**: Minimum integer score (default: 7) required to trigger Telegram notifications.

### Out-of-the-Box Pre-Configured Theme
The database is automatically seeded on startup with the **"US-Iran conflict"** theme:
- **Keywords**: `Strait of Hormuz`, `IRGC`, `CENTCOM`, `uranium`, `sanctions`, `ballistic`, `proxy`, `Persian Gulf`, `Bab el-Mandeb`, `Shahed`, `Houthis`, `Hezbollah`, `enrichment`.
- **Entities**: `United States`, `Iran`, `Israel`, `IRGC`, `US Navy`, `CENTCOM`, `Khamenei`, `Pezeshkian`.
- **Scoring Prompt**:
  > *"You are a senior geopolitical intelligence analyst. Score the following event on a 1-10 importance scale for the US-Iran conflict theme. Guidelines: Score 9-10 for direct kinetic military engagements, strikes on naval/nuclear assets, or declared nuclear breakout. Score 7-8 for proxy attacks, major sanctions, troop deployments, or heightened naval blockades. Score 4-6 for standard diplomatic rhetoric, non-escalatory drills, or minor statements. Score 1-3 for background political commentary or irrelevant noise. Provide an integer score and a 1-sentence rationale."*

### Zero-Downtime Hot Reloading
When you activate a different theme or edit the scoring prompt in `/settings`, the backend publishes a `ConfigReloadPayload` to the Redis channel `osint:config:reload`. The ingestion supervisor and pipeline orchestrator immediately update active sources and prompts in memory with **zero downtime and no service restart**.

---

## 📡 Data Ingestion Plugins

Located in `backend/app/plugins/`, each plugin normalizes data into a unified `NormalizedEvent` schema:

| Plugin | Module | Features & Fallback Mechanisms |
|---|---|---|
| **RSS / Atom** | `rss_plugin.py` | Polls configurable feed URLs (Reuters, BBC, Al Jazeera, IRNA). Strips HTML markup, removes tracking parameters, and computes SHA-256 deduplication hashes. |
| **Telegram** | `telegram_plugin.py` | Connects via **Telethon** MTProto if credentials are provided. **Automatic fallback**: If uncredentialed, scrapes public channel web previews (`https://t.me/s/{channel}`) with HTML parsing. |
| **News APIs** | `newsapi_plugin.py` | Integrates with NewsAPI.org and Currents API. **Automatic fallback**: If no API key is set, automatically routes queries through the unmetered, public **GDELT 2.0 Doc API**. |
| **Reddit** | `reddit_plugin.py` | Connects via **AsyncPRAW** if credentials are set. **Automatic fallback**: If uncredentialed, polls public subreddit JSON feeds (`https://www.reddit.com/r/{subreddit}/hot.json`). |
| **Maritime AIS** | `ais_plugin.py` | Fetches live vessel positions and navigational status from Digitraffic Finland open GeoJSON APIs within defined geographic bounding boxes (e.g. Strait of Hormuz chokepoints). |
| **Supervisor** | `supervisor.py` | Orchestrates async worker tasks with complete fault isolation. An error or rate limit in one source never crashes other sources or the server. |

---

## 📲 Telegram Notification Bot

Implemented in `backend/app/services/telegram_bot.py`:

- **Threshold Filtering**: Only events with `importance_score >= alert_threshold` trigger a dispatch.
- **Formatted Alerts**: Formatted HTML message including headline, score, source name, timestamp (UTC), and a direct link to the original article.
- **Token-Bucket Anti-Flood**: Enforces a minimum 30-second cooldown between non-critical alerts to prevent flooding channels during major news spikes.
- **Emergency Score=10 Bypass**: Critical kinetic military actions (`importance_score == 10`) immediately bypass the 30-second cooldown timer.
- **24-Hour Deduplication**: Hashes URLs into Redis (`alert:dedup:{sha256}`) to suppress duplicate wire reports for the same incident.
- **Message Length Guard**: Automatically truncates text to $\le 3,000$ characters to stay within Telegram's 4,096-character limit.

---

## 📦 Production Infrastructure & 1GB RAM Budget

The system is configured in `docker-compose.yml` with strict resource bounds tailored for a **1GB RAM Linux VPS**:

| Service | Container Image | Memory Limit (`mem_limit`) | Memory Reservation | Key Memory Optimizations |
|---|---|:---:|:---:|---|
| **PostgreSQL 16** | `postgres:16-alpine` | **150 MB** | 64 MB | `shared_buffers=32MB`, `max_connections=20`, `work_mem=2MB` |
| **Redis 7** | `redis:7-alpine` | **80 MB** | 32 MB | `maxmemory 32mb`, eviction policy `allkeys-lru` |
| **FastAPI Backend** | `python:3.11-slim` | **512 MB** | 150 MB | Single worker (`uvicorn --workers 1`), direct `httpx` clients, 100-event ring buffer |
| **Next.js Frontend** | `node:20-alpine` | **220 MB** | 100 MB | Standalone server build (`server.js`), `NODE_OPTIONS="--max-old-space-size=180"` |
| **Total Allocation** | — | **962 MB** | **346 MB** | **Typical runtime RSS stays ~370MB across all 4 containers** |

### Security & Lifecycle Features
- **Rootless Containers**: Backend runs as `appuser` (UID 10001), frontend runs as `nextjs` (UID 1001).
- **Signal Handling**: Backend uses `dumb-init` as PID 1 to ensure SIGTERM signals gracefully drain active requests and polling tasks.
- **Observability**: Structured JSON logging (`LOG_FORMAT=json`) with automatic `X-Request-ID` correlation tracing.
- **Health Probes**: Container healthchecks querying `/api/health`, `/live`, and `/ready`.

---

## 🚀 Quick Start Guide

### 1. Production: Docker Compose (Recommended)

To deploy on any Docker-compatible server or local Docker Desktop:

```bash
# 1. Clone the repository
git clone https://github.com/danprav04/realtime-osint.git
cd realtime-osint

# 2. Configure environment variables (optional for live AI keys)
cp .env.example .env

# 3. Launch all containers in detached mode
docker compose up -d

# 4. Verify container status and resource consumption
docker compose ps
docker stats
```

- **Dashboard UI**: [http://localhost:3000](http://localhost:3000)
- **FastAPI OpenAPI Docs**: [http://localhost:8000/docs](http://localhost:8000/docs)
- **Health Telemetry**: [http://localhost:8000/api/health](http://localhost:8000/api/health)
- **Default API Key**: `osint_secret_key_2026` (configurable via `API_KEY` in `.env`)

---

### 2. Local Development Setup

#### Prerequisites
- Python 3.11+
- Node.js 18+ & npm
- PostgreSQL and Redis (or run locally with zero dependencies via built-in SQLite + in-memory bus fallbacks)

#### Backend Setup
```bash
cd backend
python -m venv .venv
source .venv/bin/activate  # On Windows: .venv\Scripts\activate
pip install -r requirements.txt

# Start FastAPI server with live reload
uvicorn backend.app.main:app --host 0.0.0.0 --port 8000 --reload
```

#### Frontend Setup
```bash
cd frontend
npm install

# Start Next.js development server
npm run dev
```

---

## ⚙️ Configuration Reference

All credentials, endpoints, and thresholds can be configured via `.env` or dynamically at runtime through the **Settings Control Center (`/settings`)**:

```ini
# --- Core Security & Server ---
API_KEY=osint_secret_key_2026
ADMIN_PASSWORD=admin123
ENVIRONMENT=production
LOG_LEVEL=INFO
LOG_FORMAT=json

# --- Database & Cache ---
POSTGRES_DB=osint
POSTGRES_USER=postgres
POSTGRES_PASSWORD=postgres_password
DATABASE_URL=postgresql+asyncpg://postgres:postgres_password@postgres:5432/osint
REDIS_URL=redis://redis:6379/0

# --- Frontend Endpoints ---
NEXT_PUBLIC_API_URL=http://localhost:8000
NEXT_PUBLIC_WS_URL=http://localhost:8000

# --- AI Providers ---
# Google Gemini (https://aistudio.google.com/)
GEMINI_API_KEY=
GEMINI_MODEL=gemini-3.5-flash-lite

# Groq Cloud (https://console.groq.com/)
GROQ_API_KEY=
GROQ_MODEL=qwen/qwen3.8-27b

# Hugging Face Space TEI Embeddings Endpoint (Optional: Self-hosted 24/7 Semantic Map Embeddings)
HF_EMBEDDING_URL=
HF_API_KEY=

# --- Telegram Notification Bot ---
TELEGRAM_BOT_TOKEN=
TELEGRAM_CHAT_ID=
ALERT_THRESHOLD=7

# --- Optional Source Ingestion Credentials (Public fallbacks used if blank) ---
TELEGRAM_API_ID=
TELEGRAM_API_HASH=
NEWSAPI_KEY=
REDDIT_CLIENT_ID=
REDDIT_CLIENT_SECRET=
```

---

## 🧪 Verification & Automated Test Suite

The platform includes an extensive automated test matrix covering unit, integration, and adversarial stress scenarios:

```bash
# 1. Run the Multi-Tier E2E Test Suite (96 Tests)
python tests/run_e2e_tests.py

# 2. Run the Full Backend Regression Suite (229 Tests)
python -m pytest backend/tests/ -q

# 3. Run the Tier 5 Adversarial Hardening Suite (42 Tests)
python -m pytest backend/tests/test_tier5_adversarial.py backend/tests/test_tier5_gateway.py -v

# 4. Run Frontend Component & Utility Tests (7 Tests)
cd frontend && npm test
```

### Test Suite Execution Summary
```
================================================================================
                         E2E TEST EXECUTION SUMMARY
================================================================================
Tier     | Category / Description                   | Tests  | Pass  | Fail  | Status
--------------------------------------------------------------------------------
Tier 1   | Feature Coverage (5 Plugins, AI, Theme.. | 58     | 58    | 0     | PASS
Tier 2   | Boundary Values & Edge Cases (Empty RS.. | 20     | 20    | 0     | PASS
Tier 3   | Cross-Feature Interactions (Hot-Reload.. | 10     | 10    | 0     | PASS
Tier 4   | Realistic Workloads (Live Stream, News.. | 8      | 8     | 0     | PASS
--------------------------------------------------------------------------------
TOTAL    | All 4 E2E Tiers Verified                 | 96     | 96    | 0     | 100.0%
================================================================================
  Backend Unit, Integration & Tier 5 Tests          | 229    | 229   | 0     | 100.0%
  Frontend Component & Resilience Tests             | 7      | 7     | 0     | 100.0%
================================================================================
  GRAND TOTAL                                       | 332    | 332   | 0     | 100.0%
================================================================================
```

---

## 📁 Repository Structure

```
realtime-osint/
├── docker-compose.yml              # Memory-capped 4-container production deployment
├── .env.example                    # Template environment variables
├── .gitignore                      # Python, Node.js, SQLite, and agent cache exclusions
├── README.md                       # Master system documentation
│
├── backend/                        # FastAPI Backend Application
│   ├── Dockerfile                  # Multi-stage Python 3.11 slim rootless Dockerfile
│   ├── requirements.txt            # Python dependencies
│   ├── app/
│   │   ├── main.py                 # FastAPI app, Socket.IO ASGI mount, JSON logging & tracing
│   │   ├── config.py               # Pydantic settings & environment configuration
│   │   ├── auth.py                 # Constant-time API key & password authentication
│   │   ├── api/                    # REST API route routers
│   │   │   ├── health.py           # Health checks (/api/health, /live, /ready) with RSS metrics
│   │   │   ├── themes.py           # Theme CRUD, activation & hot-reload publishing
│   │   │   ├── sources.py          # Source CRUD, intervals & manual polling triggers
│   │   │   ├── events.py           # Event timeline queries, filtering & pagination
│   │   │   ├── embeddings.py       # 2D semantic relevance map, backfills & quota status
│   │   │   ├── summary.py          # Multi-source AI executive situation summary endpoints
│   │   │   ├── stats.py            # Event aggregation & score distribution metrics
│   │   │   ├── pipeline.py         # AI pipeline manual triggers & stage status
│   │   │   └── settings.py         # Dynamic AI provider, fallback & credential settings
│   │   ├── db/                     # SQLAlchemy async ORM models, session & theme seed
│   │   ├── pipeline/               # 4-Stage AI Pipeline (Gemini & Groq)
│   │   │   ├── orchestrator.py     # Concurrent DAG executor with fallback logic
│   │   │   ├── parser.py           # 5-tier resilient JSON parse-repair engine
│   │   │   ├── arabic_normalizer.py# Arabic/Hebrew tactical normalization & acoustic classifier
│   │   │   ├── stage1_extract.py   # Entity extraction & Arabic/Farsi translation
│   │   │   ├── stage2_score.py     # Dynamic prompt-driven 1-10 importance scoring
│   │   │   ├── stage3_summary.py   # BLUF event and digest summarization
│   │   │   ├── stage4_visuals.py   # Visual clustering & trend detection
│   │   │   └── providers/          # Gemini, Groq, and Mock HTTP client providers
│   │   ├── plugins/                # Modular Ingestion Plugins
│   │   │   ├── base.py             # Base plugin contract & NormalizedEvent schema
│   │   │   ├── supervisor.py       # Concurrency loop & fault isolation supervisor
│   │   │   ├── rss_plugin.py       # RSS/Atom feed parser
│   │   │   ├── telegram_plugin.py  # Telethon MTProto + web preview scraper
│   │   │   ├── newsapi_plugin.py   # NewsAPI + GDELT 2.0 unmetered API
│   │   │   ├── reddit_plugin.py    # AsyncPRAW + public JSON fallback
│   │   │   └── ais_plugin.py       # Digitraffic & AISStream vessel telemetry
│   │   └── services/               # Real-time infrastructure services
│   │       ├── redis_bus.py        # Redis pub/sub event bus with memory fallback
│   │       ├── socket_server.py    # Authenticated Socket.IO ASGI server & ring buffer
│   │       ├── telegram_bot.py     # Token-bucket alert dispatcher & deduplication
│   │       ├── embedding_service.py# Gemini & HF Space TEI embeddings + 2D PCA projection
│   │       └── summary_service.py  # Multi-event clustering & executive briefing synthesis
│   └── tests/                      # Backend unit, integration & stress tests
│
├── frontend/                       # Next.js 14/15 Dashboard Application
│   ├── Dockerfile                  # Multi-stage Node.js 20 Alpine standalone Dockerfile
│   ├── package.json                # React, Socket.IO client, Tailwind CSS, Lucide icons
│   ├── next.config.js              # Configured for standalone server output
│   ├── src/
│   │   ├── app/
│   │   │   ├── page.tsx            # Live timeline feed with real-time Socket.IO stream
│   │   │   ├── summary/page.tsx    # AI Executive Situation Summary briefing view
│   │   │   ├── map/page.tsx        # Interactive 2D Semantic Relevance Map view
│   │   │   ├── trends/page.tsx     # Visual analytics & trend charts
│   │   │   ├── settings/page.tsx   # Zero-code theme, source & AI provider settings
│   │   │   └── globals.css         # Dark-mode styling tokens
│   │   └── components/
│   │       ├── Navbar.tsx          # Navigation header with live status pill
│   │       ├── AuthModal.tsx       # API key authentication dialog
│   │       ├── timeline/           # EventCard, ImportanceBadge, SourceBadge, AlertWheel, EventDetailsModal
│   │       ├── map/                # RelevanceMap, EventInspector
│   │       ├── charts/             # VolumeChart, ScoreDistributionChart, SourceActivityChart
│   │       ├── settings/           # ThemeForm, SourceManager, AIConfigMatrix, ApiKeyManager
│   │       └── stats/              # StatCards
│   └── tests/                      # Frontend component and utility tests
│
└── tests/                          # 96 E2E Integration Tests (Tiers 1-4)
    ├── run_e2e_tests.py            # Automated runner script for all tiers
    └── e2e/                        # Tier 1 Features, Tier 2 Boundaries, Tier 3 Cross, Tier 4 Scenarios
```

---

## 📜 License

This project is licensed under the **MIT License**. See the [LICENSE](LICENSE) file for details.
