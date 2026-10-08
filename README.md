<div align="center">

# 🛰️ Real-Time OSINT Gathering Service

### *Autonomous Multi-Source Intelligence Aggregator, AI Analysis Pipeline & Live Situation Dashboard*

[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)
[![Python 3.11](https://img.shields.io/badge/Python-3.11+-3776AB.svg?logo=python&logoColor=white)](https://www.python.org/)
[![Next.js 14/15](https://img.shields.io/badge/Next.js-14%2F15-black.svg?logo=next.js&logoColor=white)](https://nextjs.org/)
[![Docker Compose](https://img.shields.io/badge/Docker-Compose-2496ED.svg?logo=docker&logoColor=white)](https://www.docker.com/)
[![Tests: 332/332 Passing](https://img.shields.io/badge/Tests-332%20%2F%20332%20Passed%20(100%25)-success.svg)](tests/)
[![RAM Budget: <830MB](https://img.shields.io/badge/RAM%20Budget-%3C830MB%20(1GB%20VPS)-green.svg)](#-production-infrastructure--1gb-ram-budget)

<br/>

<img src="docs/assets/hero_banner.jpg" alt="Real-Time OSINT Intelligence Dashboard" width="100%" style="border-radius: 12px; border: 1px solid rgba(255,255,255,0.1); box-shadow: 0 8px 32px rgba(0,0,0,0.6);" />

<br/>
<br/>

**A production-grade, real-time Open Source Intelligence (OSINT) platform engineered to continuously ingest, translate, analyze, and visualize live multi-source geopolitical data.** Powered by a multi-stage AI pipeline (Google Gemini & Groq Cloud), low-latency WebSocket live streaming, zero-code dynamic topic configuration, instant Telegram alert dispatching, and a strict **<830MB RAM footprint** tuned specifically for 1GB Linux VPS instances.

</div>

---

## 📑 Table of Contents

- [Key Capabilities](#-key-capabilities)
- [System Architecture](#-system-architecture)
- [Multi-Stage AI Pipeline](#-multi-stage-ai-pipeline)
- [Zero-Code Dynamic Theme Engine](#-zero-code-dynamic-theme-engine)
- [Modular Ingestion Plugins](#-modular-ingestion-plugins)
- [Interactive Next.js Dashboard](#-interactive-nextjs-dashboard)
- [Telegram Notification Bot](#-telegram-notification-bot)
- [Production Infrastructure & 1GB RAM Budget](#-production-infrastructure--1gb-ram-budget)
- [Quick Start Guide](#-quick-start-guide)
  - [Docker Compose Deployment (Recommended)](#1-docker-compose-deployment-recommended)
  - [Local Development Setup](#2-local-development-setup)
- [Configuration Reference](#-configuration-reference)
- [Testing & Quality Verification](#-testing--quality-verification)
- [Project Directory Layout](#-project-directory-layout)
- [License](#-license)

---

## ⚡ Key Capabilities

- **🌐 5 Pluggable Live Sources**: Concurrently ingests from RSS/Atom feeds, Telegram channels (Telethon MTProto + public web previews), News APIs (NewsAPI, Currents, unmetered GDELT 2.0), Reddit (AsyncPRAW + JSON feeds), and Maritime AIS vessel telemetry (Digitraffic Finland & AISStream).
- **🧠 Granular AI Model Selection**: Independently assign different models and providers (e.g. `llama-3.3-70b-versatile` on Groq for ultra-fast translation vs. `gemini-2.0-flash` on Google for reasoning) across each of the 4 pipeline stages.
- **🌍 Native Arabic & Persian Translation**: Automatically translates Arabic and Farsi OSINT sources into English while strictly preserving military acronyms (`CENTCOM`, `IRGC`, `IAEA`) and Persian zero-width non-joiners (ZWNJ).
- **🎯 Dynamic Prompt-Driven Scoring**: Importance scores (1–10) are governed by a user-editable natural language prompt. Changing the criteria in the settings immediately shifts the AI's scoring focus without redeploying code.
- **⚡ Instant Zero-Code Topic Switching**: Change the entire monitoring scope (e.g., from *US-Iran War* to *China-Taiwan Tensions*) via the Settings UI. Emits Redis reload signals (`osint:config:reload`) to hot-swap ingestion workers and prompts on the fly.
- **🖥️ Tactile Dark-Mode Dashboard**: Next.js 14/15 App Router interface built with Obsidian surface elevation (`#08090A` canvas, `#141518` cards, specular hairline highlights), Socket.IO real-time feed, importance indicators, and interactive analytics charts.
- **📲 Telegram Alert Dispatcher**: Pushes breaking events (score $\ge$ threshold) with HTML formatting, 30s token-bucket anti-flood rate limiting, 24h SHA-256 deduplication, and an emergency bypass for score 10 kinetic events.
- **📦 1GB VPS RAM Optimization**: Hard-capped in `docker-compose.yml` to **830MB total memory** across PostgreSQL (150MB), Redis (80MB), FastAPI Backend (380MB), and Next.js Frontend (220MB), leaving >170MB OS safety headroom.

---

## 🏛️ System Architecture

```mermaid
flowchart TD
    subgraph Sources["📡 Ingestion Layer (Plugins)"]
        RSS["RSS/Atom Feeds\n(Reuters, AP, Al Jazeera, IRNA)"]
        TG["Telegram Channels\n(Telethon MTProto + Web Fallback)"]
        News["News APIs\n(NewsAPI, Currents, GDELT 2.0)"]
        Reddit["Reddit Communities\n(r/worldnews, r/geopolitics)"]
        AIS["Maritime AIS\n(Strait of Hormuz Telemetry)"]
    end

    subgraph Backend["⚙️ Python FastAPI Backend (<380MB RAM)"]
        Supervisor["Ingestion Supervisor\n(Isolated Fault Boundaries)"]
        DB[("PostgreSQL 16\n(Persistent Storage)")]
        RedisBus["Redis Pub/Sub Bus\n(Event Streams & Reload Signals)"]
        
        subgraph AIPipeline["4-Stage AI Pipeline (Gemini & Groq)"]
            S1["Stage 1: Extract & Translate\n(Arabic / Farsi to English)"]
            S2["Stage 2: Dynamic Theme Scoring\n(1-10 Scale via Custom Prompt)"]
            S3["Stage 3: BLUF Summarization\n(Event & Digest Summaries)"]
            S4["Stage 4: Visuals & Trends\n(Cluster Tags & Timeline Coordinates)"]
        end

        SocketGateway["ASGI Socket.IO Gateway\n(/socket.io, Bounded Ring Buffer)"]
        TelegramBot["Telegram Alert Bot\n(Anti-Flood + Dedup + Score 10 Bypass)"]
    end

    subgraph Frontend["💻 Next.js 14/15 Standalone Frontend (<220MB RAM)"]
        LiveFeed["Live Timeline Feed\n(Real-Time WebSocket Push)"]
        Trends["Analytics & Trends\n(Volume & Distribution Charts)"]
        SettingsUI["Settings Control Center\n(Zero-Code Themes & Model Matrix)"]
    end

    Sources -->|NormalizedEvent| Supervisor
    Supervisor --> AIPipeline
    AIPipeline --> DB
    AIPipeline -->|Enriched Event| RedisBus
    RedisBus --> SocketGateway
    RedisBus --> TelegramBot
    SocketGateway -->|WebSocket Push| LiveFeed
    SocketGateway -->|Stats Broadcast| Trends
    SettingsUI -->|REST PUT/POST| DB
    SettingsUI -->|Publish ConfigReload| RedisBus
    RedisBus -.->|Hot-Reload Sources| Supervisor
    RedisBus -.->|Hot-Reload Prompts| AIPipeline
```

---

## 🧠 Multi-Stage AI Pipeline

The pipeline decomposes intelligence processing into 4 isolated stages, with full flexibility to assign Google Gemini or Groq Cloud individually:

```mermaid
flowchart LR
    Raw["Raw Normalized\nEvent"] --> S1["Stage 1\nExtraction & Translation"]
    S1 --> Fork{"Sequential\nExtraction Pass"}
    Fork --> S2["Stage 2\nImportance Scoring\n(1-10 Scale)"]
    Fork --> S3["Stage 3\nBLUF Summarization\n(Event & Digest)"]
    Fork --> S4["Stage 4\nVisual Analytics\n(Clusters & Trends)"]
    S2 --> Join["Enriched Event Entity"]
    S3 --> Join
    S4 --> Join
```

| Stage | Default Provider & Model | Responsibility | Resilient Fallback |
|---|---|---|---|
| **1. Extraction & Translation** | `groq: llama-3.3-70b-versatile` | Detects Arabic/Farsi Unicode script blocks, translates to fluent English, preserves defense acronyms (`CENTCOM`, `IRGC`, `IAEA`) and Persian ZWNJ (`\u200c`). | Heuristic extractor with raw text passthrough |
| **2. Importance Scoring** | `gemini: gemini-2.0-flash` | Evaluates event significance on a 1–10 integer scale against the user's custom theme prompt. | Dynamic keyword density heuristic fallback |
| **3. Summarization** | `groq: llama-3.3-70b-versatile` | Generates a 2-3 sentence **BLUF** (Bottom Line Up Front) summary highlighting kinetic action, parties involved, and impact. | First paragraph extractive summary |
| **4. Visual Analytics** | `gemini: gemini-2.0-flash` | Extracts strategic cluster tags, geographic entities, and timeline volume markers for the frontend chart widgets. | Rule-based entity & tag extractor |

> **5-Tier Resilient Parser**: All LLM outputs pass through an automated parse-repair engine that strips markdown code blocks, balances brackets, corrects truncated JSON arrays, and falls back to deterministic heuristic results under API rate limits (HTTP 429).

---

## 🎯 Zero-Code Dynamic Theme Engine

Changing what the system monitors requires **zero code changes and zero server restarts**:

```mermaid
sequenceDiagram
    autonumber
    actor User as Analyst / User
    participant UI as Next.js Settings Page
    participant API as FastAPI /api/themes
    participant DB as PostgreSQL 16
    participant Bus as Redis Pub/Sub (osint:config:reload)
    participant Sup as Ingestion Supervisor
    participant Pipe as AI Pipeline Orchestrator

    User->>UI: Select Theme (or edit scoring prompt & keywords)
    UI->>API: POST /api/themes/{id}/activate
    API->>DB: Atomically activate theme & deactivate peers
    API->>Bus: Publish ConfigReloadPayload(action="theme_update")
    par Broadcast Invalidation
        Bus->>Sup: Reconcile active sources & polling timers
        Bus->>Pipe: Invalidate cached theme scoring prompts
    end
    Sup-->>API: Active sources swapped in memory (0 downtime)
    API-->>UI: 200 OK (Theme Activated)
    UI-->>User: Dashboard timeline updates immediately
```

### Pre-Configured "US-Iran Conflict" Theme
The service ships ready-to-run with a seeded theme:
- **Monitored Keywords**: `Strait of Hormuz`, `IRGC`, `CENTCOM`, `Shahed`, `Ballistic Missile`, `IAEA`, `Natanz`, `Persian Gulf`, `Bab el-Mandeb`, `Sanctions`, `Drone Strike`.
- **Scoring Prompt**:
  > *"Score 9-10 for direct kinetic military engagements, missile/drone launches, attacks on commercial shipping in the Strait of Hormuz, or nuclear enrichment breakthroughs. Score 7-8 for high-level diplomatic ultimatums, troop deployments, or major naval movements. Score 4-6 for standard political statements. Score 1-3 for routine administrative news."*
- **Pre-Configured Sources**: Curated RSS feeds (Reuters, BBC, Al Jazeera, IRNA), Telegram channels (`@OSINTdefender`, `@MiddleEastSpectator`), Reddit (`r/worldnews`, `r/geopolitics`), and Maritime AIS chokepoints.

---

## 📡 Modular Ingestion Plugins

All data sources implement the `BaseSourcePlugin` contract in `backend/app/plugins/`:

```
backend/app/plugins/
├── base.py              # NormalizedEvent schema & plugin interface
├── supervisor.py        # Async scheduling & error boundary supervisor
├── rss_plugin.py        # RSS/Atom polling with HTML sanitization & SHA-256 deduplication
├── telegram_plugin.py   # Telethon MTProto client + public web scraper fallback
├── newsapi_plugin.py    # NewsAPI & Currents + unmetered GDELT 2.0 Doc API
├── reddit_plugin.py     # AsyncPRAW client + public subreddit JSON fallback
└── ais_plugin.py        # Digitraffic Finland & AISStream vessel telemetry
```

### Zero-Configuration Out-of-the-Box Fallbacks
Even before you input personal API keys, the system ingests live data out of the box:
- **News**: Automatically routes to unmetered **GDELT 2.0 Doc API** when NewsAPI keys are unset.
- **Telegram**: Automatically falls back to public web previews (`https://t.me/s/{channel}`) with HTML regex parsing when Telethon credentials are unset.
- **Reddit**: Automatically falls back to public JSON endpoints (`https://www.reddit.com/r/{sub}/hot.json`) when Reddit API keys are unset.
- **Maritime AIS**: Ingests open marine traffic telemetry from Digitraffic Finland.

---

## 🖥️ Interactive Next.js Dashboard

Designed following **Engineered Tactile Minimalism** principles:
- **Surface Elevation**: Canvas at `#08090A`, sidebars at `#0F1012`, cards at `#141518`, with 1px top specular bevels (`inset 0 1px 0 rgba(255,255,255,0.12)`).
- **Live Timeline**: Real-time event cards pushed over WebSocket (`Socket.IO`), featuring click-to-expand raw data modals, source badges, and color-coded importance badges:
  - 🔴 **Critical (9–10)**: Red border & ambient glow
  - 🟠 **High (7–8)**: Orange/Amber badge
  - 🟡 **Medium (5–6)**: Yellow badge
  - 🔵 **Low (1–4)**: Blue/Slate badge
- **Analytics & Trends** (`/trends`): Volume timeline charts, score distribution histograms, and source activity breakdowns.
- **Settings Control Center** (`/settings`):
  - **Theme Form**: Edit keywords, entities, alert thresholds, and the natural-language scoring prompt.
  - **Source Manager**: Add, toggle, delete, or trigger on-demand polling for feeds with custom polling intervals.
  - **AI Model Matrix**: Independently select model providers (Gemini vs. Groq) and specific model names per pipeline stage.
  - **API Key & Notification Manager**: Configure Telegram bot token, chat ID, and third-party API credentials.

---

## 📲 Telegram Notification Bot

The notification engine in `backend/app/services/telegram_bot.py` dispatches real-time alerts to your Telegram chat or channel:

```
🚨 [CRITICAL ALERT] Score: 9/10
IRGC fast-attack craft conduct live-fire exercises in the Strait of Hormuz

📰 Summary: 
Iranian naval forces initiated unannounced live-fire missile drills near 
commercial shipping lanes in the Strait of Hormuz. US CENTCOM issued a 
warning to commercial traffic transiting the Persian Gulf.

📡 Source: Reuters (RSS)
🕒 Time: 2026-10-08 02:45 UTC
🔗 Link: https://www.reuters.com/...
```

### Safety & Delivery Features
- **Token-Bucket Anti-Flood**: Enforces a strict 30-second cooldown between non-critical alerts to avoid Telegram flood limits.
- **Emergency Score=10 Bypass**: Critical kinetic events (`importance_score == 10`) immediately bypass the cooldown timer.
- **24-Hour SHA-256 Deduplication**: Hashes event URLs into Redis (`alert:dedup:{hash}`) to prevent duplicate notifications for wire updates.
- **Safe Truncation**: Summaries are cleanly truncated to $\le$ 3,000 characters to stay within Telegram's 4,096-character limit.

---

## 📦 Production Infrastructure & 1GB RAM Budget

Designed to run reliably on an inexpensive **1GB RAM Linux VPS instance**:

### Container Memory Allocation Matrix
| Container | Hard Limit (`mem_limit`) | Soft Reservation (`mem_reservation`) | Optimization Strategies |
|---|:---:|:---:|---|
| **PostgreSQL 16** | **150 MB** | 64 MB | `shared_buffers=32MB`, `max_connections=20`, `work_mem=2MB` |
| **Redis 7** | **80 MB** | 32 MB | `maxmemory 32mb`, `maxmemory-policy allkeys-lru` |
| **FastAPI Backend** | **380 MB** | 150 MB | Single worker (`uvicorn --workers 1`), direct `httpx` clients (no heavy SDKs), bounded 100-event ring buffer |
| **Next.js Frontend** | **220 MB** | 100 MB | Next.js standalone build (`server.js`), `NODE_OPTIONS="--max-old-space-size=180"` |
| **Total Container Cap** | **830 MB** | **346 MB** | **Leaves >170MB RAM buffer for Linux kernel & host OS** |

### Hardened Container Security
- **Rootless Execution**: Backend runs as `appuser:10001` (`python:3.11-slim`), Frontend runs as `nextjs:1001` (`node:20-alpine`).
- **Signal Forwarding**: Backend uses `dumb-init` for proper PID 1 signal management and clean SIGTERM graceful shutdown.
- **Structured JSON Observability**: Request-tracing middleware injects `X-Request-ID` correlation IDs across all logs.
- **Health Probes**: Integrated Kubernetes/Docker probes on `/api/health`, `/live`, and `/ready` with live process RSS memory reporting.

---

## 🚀 Quick Start Guide

### 1. Docker Compose Deployment (Recommended)

To launch the complete platform on any Docker-enabled machine (Linux VPS, macOS, or Windows Docker Desktop):

```bash
# 1. Clone the repository
git clone https://github.com/danprav04/realtime-osint.git
cd realtime-osint

# 2. Configure environment variables (optional for live AI keys)
cp .env.example .env

# 3. Build and launch all services in detached mode
docker compose up -d

# 4. Verify memory consumption is under the 830MB limit
docker stats
```

Access the services:
- **Dashboard UI**: [http://localhost:3000](http://localhost:3000)
- **FastAPI Documentation**: [http://localhost:8000/docs](http://localhost:8000/docs)
- **Health Endpoint**: [http://localhost:8000/api/health](http://localhost:8000/api/health)
- **Default Dashboard API Key**: `dev-secret-key-12345` (configurable in `.env`)

---

### 2. Local Development Setup

#### Backend Setup
```bash
cd backend
python -m venv .venv
source .venv/bin/activate  # On Windows: .venv\Scripts\activate
pip install -r requirements.txt

# Start backend server with auto-reload
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

Edit `.env` (or pass environment variables to Docker Compose) to customize settings:

```ini
# --- Core Security ---
API_KEY=dev-secret-key-12345
SECRET_KEY=change-this-in-production-to-a-random-32-byte-hex
ENVIRONMENT=production

# --- Database & Cache ---
DATABASE_URL=postgresql+asyncpg://osint:osint_pass@localhost:5432/osint
REDIS_URL=redis://localhost:6379/0

# --- AI Providers ---
# Google Gemini
GEMINI_API_KEY=your_gemini_api_key_here
GEMINI_MODEL=gemini-2.0-flash

# Groq Cloud
GROQ_API_KEY=your_groq_api_key_here
GROQ_MODEL=llama-3.3-70b-versatile

# Per-Stage Provider Defaults (gemini | groq | mock)
STAGE1_PROVIDER=groq
STAGE2_PROVIDER=gemini
STAGE3_PROVIDER=groq
STAGE4_PROVIDER=gemini

# --- Telegram Alert Bot ---
TELEGRAM_BOT_TOKEN=your_telegram_bot_token_here
TELEGRAM_CHAT_ID=your_telegram_chat_or_channel_id
ALERT_THRESHOLD=7

# --- Ingestion Credentials (Optional) ---
TELEGRAM_API_ID=
TELEGRAM_API_HASH=
NEWSAPI_KEY=
REDDIT_CLIENT_ID=
REDDIT_CLIENT_SECRET=
```

---

## 🧪 Testing & Quality Verification

The platform maintains a comprehensive multi-tier test harness with **332 total tests** passing at 100%:

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

### Verification Matrix Summary
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
TOTAL    | All 4 Tiers Verified                     | 96     | 96    | 0     | 100.0%
================================================================================
  Backend Unit, Integration & Tier 5 Tests          | 229    | 229   | 0     | 100.0%
  Frontend Component & Resilience Tests             | 7      | 7     | 0     | 100.0%
================================================================================
  GRAND TOTAL                                       | 332    | 332   | 0     | 100.0%
================================================================================
```

---

## 📁 Project Directory Layout

```
realtime-osint/
├── docker-compose.yml              # 830MB memory-bounded 4-container production deployment
├── .env.example                    # Sample environment variables configuration
├── .gitignore                      # Python bytecode, Next.js, and DB ignore patterns
├── README.md                       # Master system documentation
│
├── backend/                        # FastAPI Backend Application
│   ├── Dockerfile                  # Multi-stage Python 3.11 slim rootless Dockerfile
│   ├── requirements.txt            # Python dependencies
│   ├── app/
│   │   ├── main.py                 # FastAPI app, Socket.IO ASGI mount, JSON logging & tracing
│   │   ├── config.py               # Pydantic settings & environment configuration
│   │   ├── auth.py                 # Constant-time API key & password authentication
│   │   ├── api/                    # REST API route endpoints
│   │   │   ├── health.py           # Health checks (/api/health, /live, /ready) with memory metrics
│   │   │   ├── themes.py           # Theme CRUD, activation & hot-reload broadcast
│   │   │   ├── sources.py          # Source CRUD, intervals & manual polling triggers
│   │   │   ├── events.py           # Event timeline queries, filtering & pagination
│   │   │   ├── stats.py            # Event aggregation & score distribution metrics
│   │   │   ├── pipeline.py         # AI pipeline manual triggers & stage status
│   │   │   └── settings.py         # Dynamic AI provider & model settings
│   │   ├── db/                     # SQLAlchemy async ORM models, session & theme seed
│   │   ├── pipeline/               # 4-Stage AI Pipeline (Gemini & Groq)
│   │   │   ├── orchestrator.py     # Concurrent DAG executor with fallback fallbacks
│   │   │   ├── parser.py           # 5-tier resilient JSON parse-repair engine
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
│   │       └── telegram_bot.py     # Token-bucket alert dispatcher & deduplication
│   └── tests/                      # 229 backend unit, integration & stress tests
│
├── frontend/                       # Next.js 14/15 Dashboard Application
│   ├── Dockerfile                  # Multi-stage Node.js 20 Alpine standalone Dockerfile
│   ├── package.json                # React 19, Socket.IO client, Tailwind CSS, Lucide icons
│   ├── next.config.js              # Configured for standalone server output
│   ├── src/
│   │   ├── app/
│   │   │   ├── page.tsx            # Live timeline feed with real-time Socket.IO stream
│   │   │   ├── trends/page.tsx     # Visual analytics & trend charts
│   │   │   ├── settings/page.tsx   # Zero-code theme, source & AI provider settings
│   │   │   └── globals.css         # Obsidian dark-mode surface elevation tokens
│   │   └── components/
│   │       ├── Navbar.tsx          # Navigation header with live status pill
│   │       ├── AuthModal.tsx       # API key authentication dialog
│   │       ├── timeline/           # EventCard, ImportanceBadge, SourceBadge, EventDetailsModal
│   │       ├── charts/             # VolumeChart, ScoreDistributionChart, SourceActivityChart
│   │       ├── settings/           # ThemeForm, SourceManager, AIConfigMatrix, ApiKeyManager
│   │       └── stats/              # StatCards summary metrics
│   └── tests/                      # Frontend component and utility tests
│
├── tests/                          # 96 E2E Integration Tests (Tiers 1-4)
│   ├── run_e2e_tests.py            # Automated runner script for all tiers
│   └── e2e/                        # Tier 1 Features, Tier 2 Boundaries, Tier 3 Cross, Tier 4 Scenarios
│
└── docs/
    └── assets/                     # Visual illustrations & dashboard banners
```

---

## 📜 License

This project is licensed under the **MIT License**. See the [LICENSE](LICENSE) file for details.

<div align="center">
<sub>Built with precision for real-time situational awareness. Verified and audited for production deployment.</sub>
</div>
