# Original User Request

## 2026-10-06T20:29:49Z

Build a production-grade, real-time OSINT (Open Source Intelligence) gathering service. The system scrapes, parses, and analyzes live data from multiple configurable source types using AI (Gemini and Groq APIs), presents analyzed intelligence on a modern interactive dashboard, and sends critical event notifications via a Telegram bot. The entire system is theme-driven — changing the monitoring topic (e.g., from "US-Iran conflict" to "China-Taiwan tensions") requires only reconfiguring settings through the dashboard UI, with zero code changes. Must run on a 1GB RAM Linux server via Docker.

Working directory: c:\Users\Developer\realtime-osint
Integrity mode: demo

## Requirements

### R1. Data Source Plugin Architecture (Python/FastAPI Backend)
Build a plugin-based source ingestion system in the Python backend that supports these source types, each as a separate module:

1. **RSS/Atom Feeds** — Poll configurable feed URLs (Reuters, AP, Al Jazeera, BBC, etc.) at configurable intervals (default 2-5 min).
2. **Telegram Channels** — Real-time event-driven monitoring of public Telegram channels using Telethon (MTProto). Requires user-provided `api_id` and `api_hash` from my.telegram.org.
3. **News APIs** — Integrate with NewsAPI.org, Currents API, and/or GDELT for broad coverage. Configurable polling intervals (default 5-10 min).
4. **Reddit** — Via Reddit API (PRAW). Monitor configurable subreddits (e.g., r/worldnews, r/geopolitics). Default polling interval 10 min.
5. **Maritime/AIS Tracking** — Basic integration with publicly available AIS/vessel tracking data for naval movement signals.

Each source plugin must:
- Be independently configurable (enable/disable, polling interval, specific URLs/channels/subreddits)
- Normalize output into a common event schema (timestamp, source, raw_text, metadata, location if available)
- Handle errors gracefully without crashing other plugins
- All source configurations manageable through the dashboard settings UI

### R2. AI Processing Pipeline with Per-Stage Model Configuration
Implement a multi-stage AI processing pipeline where each stage can independently use a different model/provider (Gemini or Groq). The available stages are:

1. **Data Parsing/Extraction** — Extract structured data from raw source content. Translate non-English content (especially Persian/Farsi, Arabic) to English.
2. **Importance Scoring** — Score each event on a 1-10 scale. The scoring criteria are driven by a user-configurable prompt (editable in settings) that tells the AI what to prioritize for the current theme. Events above a configurable threshold (default 7) trigger Telegram notifications.
3. **Summarization** — Generate concise AI summaries for each event and periodic digest summaries.
4. **Dashboard Visual Generation** — AI-assisted analysis for charts, trend detection, and narrative generation shown on the dashboard.

Settings UI must expose:
- Model provider dropdown (Gemini / Groq) per stage
- Specific model name per stage (e.g., `gemini-2.0-flash`, `llama-3.3-70b-versatile`)
- API keys for each provider
- The user-configurable scoring prompt that guides importance assessment

### R3. Theme System (Zero-Code Topic Configuration)
Implement a theme system where the entire monitoring focus is defined by configuration, not code:

- **Theme name and description** — e.g., "US-Iran War Monitoring"
- **Keywords and entities** — List of tracked keywords, named entities, locations
- **Scoring prompt** — Natural language instructions telling the AI what makes an event important for this theme
- **Source presets** — Which sources to activate and their specific configurations (which RSS feeds, which Telegram channels, which subreddits)
- **Notification rules** — Importance threshold, notification frequency limits

Ship with a pre-configured theme for the "US-Iran conflict" that includes relevant RSS feeds, suggested Telegram channels, relevant subreddits, and a tuned scoring prompt.

### R4. Modern Interactive Dashboard (Next.js + Tailwind CSS)
Build a dark-mode-native, modern dashboard using Next.js and Tailwind CSS. Primary view is an **event timeline / live feed** showing:

- Chronological event stream with real-time WebSocket (Socket.IO) updates
- Importance indicators (color-coded 1-10 scale badges)
- Source attribution icons/labels for each event
- AI-generated event summaries
- Click-to-expand for full event details and raw source data
- Trend charts showing event volume, importance distribution, and source activity over time
- Summary statistics cards (total events, high-priority events, active sources, last update time)

Dashboard must include a full **Settings page** for managing:
- Theme configuration (keywords, scoring prompt, source presets)
- Source management (add/remove/configure individual sources)
- AI model configuration per pipeline stage
- API keys (Gemini, Groq, Telegram, Reddit, News APIs)
- Notification settings (Telegram bot token, chat ID, importance threshold)
- Polling intervals per source type

Authentication via a configurable API key/password.

### R5. Telegram Bot Notifications
Implement a Telegram bot (using python-telegram-bot) that sends formatted notifications to a configured chat/channel when events exceed the importance threshold. Notifications should include:
- Event summary
- Importance score
- Source attribution
- Timestamp
- Link to source (if available)

The bot is notification-only (send alerts, no command handling).

### R6. Production Infrastructure (1GB RAM Linux server via Docker Compose)
The entire system must run on a **1GB RAM Linux server** via Docker Compose. This means:

- Optimize all services for minimal memory usage
- PostgreSQL for persistent storage (events, configurations, notification history)
- Redis for real-time pub/sub (WebSocket events) and caching
- Sensible connection pooling and resource limits
- Health check endpoints
- Structured logging
- Rate limiting on the API
- Graceful shutdown handling
- Docker Compose configuration for single-command deployment (`docker compose up`)

## Acceptance Criteria

### Core Pipeline
- [ ] Running `docker compose up` starts all services (backend, frontend, PostgreSQL, Redis) without errors
- [ ] Adding an RSS feed URL in the settings UI causes new articles from that feed to appear on the dashboard within the configured polling interval
- [ ] Events from at least 3 different source types (RSS, News API, Reddit) appear on the dashboard with correct source attribution
- [ ] Each event on the dashboard displays: timestamp, source label, AI-generated summary, and importance score (1-10)
- [ ] Changing the importance scoring prompt in settings demonstrably changes how subsequent events are scored

### AI Configuration
- [ ] The settings page allows selecting a different model provider (Gemini or Groq) for each of the 4 pipeline stages independently
- [ ] The system successfully processes events using Groq for one stage and Gemini for another in the same pipeline run
- [ ] Non-English source content (e.g., Arabic RSS feed) appears on the dashboard translated to English

### Theme System
- [ ] Changing the theme keywords and scoring prompt in settings causes the system to filter and score differently — no code changes, no restart required
- [ ] The pre-configured "US-Iran conflict" theme ships with working source configurations that produce real events on the dashboard

### Notifications
- [ ] Events scoring above the configured threshold trigger a Telegram message to the configured chat within 60 seconds of the event being processed
- [ ] Telegram notifications include event summary, score, source, and timestamp

### Dashboard UX
- [ ] Dashboard loads and displays events within 3 seconds
- [ ] New events appear on the dashboard in real-time via WebSocket without page refresh
- [ ] Dashboard is styled with a dark-mode-native, modern aesthetic (no pure black backgrounds, proper surface elevation, readable typography)
- [ ] Settings page successfully saves and applies all configuration changes without requiring a restart

### Production Readiness
- [ ] Total Docker memory usage stays under 900MB during normal operation
- [ ] API endpoints require authentication (API key)
- [ ] Application logs are structured (JSON) and include request tracing
- [ ] System recovers gracefully from temporary source failures (network errors, API rate limits) without crashing


## 2026-10-07T01:11:32Z

Build a production-grade, real-time OSINT (Open Source Intelligence) gathering service. The system scrapes, parses, and analyzes live data from multiple configurable source types using AI (Gemini and Groq APIs), presents analyzed intelligence on a modern interactive dashboard, and sends critical event notifications via a Telegram bot. The entire system is theme-driven — changing the monitoring topic (e.g., from "US-Iran conflict" to "China-Taiwan tensions") requires only reconfiguring settings through the dashboard UI, with zero code changes. Must run on a 1GB RAM Linux server via Docker.

Working directory: c:\Users\Developer\realtime-osint
Integrity mode: demo

NOTE ON CURRENT STATE:
Milestone 1 (Foundations: DB models, Redis pub/sub bus, auth, seeding) and Milestone 2 (Data Source Ingestion Plugins: RSS, Telegram Telethon/Web, NewsAPI/GDELT, Reddit AsyncPRAW, Maritime AIS, and IngestionSupervisor) are already implemented and committed in `backend/` and tested in `tests/` (96 E2E tests + 57 backend tests passing).
Resume seamlessly and continue execution from Milestone 3 onwards:
- Milestone 3: Multi-Stage AI Pipeline (Gemini & Groq, per-stage configurable model, data parsing, importance scoring with user-configurable prompt, summarization, dashboard visual generation, translation)
- Milestone 4: Real-time Gateway (WebSocket/Socket.IO), Theme Engine (zero-code configuration, dynamic reload), and Telegram Notification Bot
- Milestone 5: Modern Interactive Dashboard (Next.js + Tailwind CSS, dark-mode-native, live feed timeline, charts, stats, full settings UI)
- Milestone 6: Production Containerization & Resource Optimization (<900MB RAM footprint for 1GB RAM Linux server via Docker Compose)
- Milestone 7: Final E2E Test Suite Verification & Hardening


## 2026-10-07T10:44:23Z

Build a production-grade, real-time OSINT (Open Source Intelligence) gathering service. The system scrapes, parses, and analyzes live data from multiple configurable source types using AI (Gemini and Groq APIs), presents analyzed intelligence on a modern interactive dashboard, and sends critical event notifications via a Telegram bot. The entire system is theme-driven — changing the monitoring topic (e.g., from "US-Iran conflict" to "China-Taiwan tensions") requires only reconfiguring settings through the dashboard UI, with zero code changes. Must run on a 1GB RAM Linux server via Docker.

Working directory: c:\Users\Developer\realtime-osint
Integrity mode: demo

NOTE ON CURRENT STATE (RESUMPTION):
- Milestone 1 (Foundation: DB models, Redis pub/sub bus, auth, seeding) is COMPLETED and verified.
- Milestone 2 (Data Ingestion Plugins: RSS, Telegram Telethon/Web, NewsAPI/GDELT, Reddit AsyncPRAW, Maritime AIS, and IngestionSupervisor) is COMPLETED and verified.
- Milestone 3 (Multi-Stage AI Pipeline: Gemini & Groq providers, 4 stages including translation, dynamic scoring prompt, summarization, visual analytics, REST endpoints in api/pipeline.py and api/settings.py) is COMPLETED and verified (97 backend tests pass).
- All changes up to Milestone 3 are committed and pushed to origin/main (commit 3b71cd2).

NEXT TASKS TO EXECUTE:
- Milestone 4: Real-time Gateway (WebSocket / Socket.IO), Theme Engine (zero-code configuration, dynamic reload without restart), and Telegram Notification Bot (formatting, importance threshold filtering, delivery rate limits).
- Milestone 5: Modern Interactive Dashboard (Next.js + Tailwind CSS, dark-mode-native, live feed timeline with Socket.IO updates, importance indicators, source badges, trend charts, stats cards, and full Settings UI).
- Milestone 6: Production Containerization & Resource Optimization (<900MB RAM total Docker memory budget for 1GB RAM Linux server via docker-compose.yml with healthchecks, non-root users, resource limits).
- Milestone 7: Final E2E Test Suite Verification & Hardening (execute all test suites, ensure 100% pass rate).

## Requirements

### R1. Data Source Plugin Architecture (Python/FastAPI Backend)
[Completed & Verified]

### R2. AI Processing Pipeline with Per-Stage Model Configuration
[Completed & Verified]

### R3. Theme System (Zero-Code Topic Configuration)
Implement a theme system where the entire monitoring focus is defined by configuration, not code:
- Theme name and description — e.g., "US-Iran War Monitoring"
- Keywords and entities — List of tracked keywords, named entities, locations
- Scoring prompt — Natural language instructions telling the AI what makes an event important for this theme
- Source presets — Which sources to activate and their specific configurations (which RSS feeds, which Telegram channels, which subreddits)
- Notification rules — Importance threshold, notification frequency limits
- Ship with a pre-configured theme for the "US-Iran conflict" that includes relevant RSS feeds, suggested Telegram channels, relevant subreddits, and a tuned scoring prompt.
- Dynamic reload: Changing the theme in settings updates ingestion and pipeline without restart.

### R4. Modern Interactive Dashboard (Next.js + Tailwind CSS)
Build a dark-mode-native, modern dashboard using Next.js and Tailwind CSS. Primary view is an **event timeline / live feed** showing:
- Chronological event stream with real-time WebSocket (Socket.IO) updates
- Importance indicators (color-coded 1-10 scale badges)
- Source attribution icons/labels for each event
- AI-generated event summaries
- Click-to-expand for full event details and raw source data
- Trend charts showing event volume, importance distribution, and source activity over time
- Summary statistics cards (total events, high-priority events, active sources, last update time)
- Full Settings page for managing theme, sources, AI models per stage, API keys, notification rules.
- Authentication via configurable API key/password.

### R5. Telegram Bot Notifications
Implement a Telegram bot (using python-telegram-bot / aiohttp) that sends formatted notifications to a configured chat/channel when events exceed the importance threshold. Notifications include:
- Event summary
- Importance score
- Source attribution
- Timestamp
- Link to source (if available)
- Notification-only (send alerts, no command handling).

### R6. Production Infrastructure (1GB RAM Linux server via Docker Compose)
The entire system must run on a 1GB RAM Linux server via Docker Compose:
- Optimize all services for minimal memory usage (<900MB total)
- PostgreSQL for persistent storage (events, configurations, notification history)
- Redis for real-time pub/sub (WebSocket events) and caching
- Sensible connection pooling and resource limits
- Health check endpoints
- Structured logging
- Rate limiting on the API
- Graceful shutdown handling
- Docker Compose configuration for single-command deployment (`docker compose up`)

## Acceptance Criteria
- [ ] Running `docker compose up` starts all services without errors
- [ ] Adding an RSS feed URL causes new articles from that feed to appear on dashboard within polling interval
- [ ] Events from at least 3 source types appear with correct source attribution
- [ ] Each event displays timestamp, source label, AI summary, and importance score (1-10)
- [ ] Changing importance scoring prompt changes how subsequent events are scored
- [ ] Settings allows selecting provider (Gemini/Groq) for each of 4 pipeline stages independently
- [ ] System successfully processes events using Groq for one stage and Gemini for another
- [ ] Non-English source content appears translated to English
- [ ] Changing theme keywords & scoring prompt works without restart
- [ ] Pre-configured US-Iran conflict theme ships ready to use
- [ ] Events scoring above threshold trigger Telegram message within 60s
- [ ] Telegram notifications include summary, score, source, timestamp
- [ ] Dashboard loads and displays events within 3s
- [ ] New events appear in real-time via WebSocket without refresh
- [ ] Modern dark-mode aesthetic with proper surface elevation and readable typography
- [ ] Settings page saves and applies all changes without restart
- [ ] Total Docker memory stays under 900MB during normal operation
- [ ] API endpoints require authentication (API key)
- [ ] Structured JSON logging with request tracing
- [ ] Recovers gracefully from temporary source failures without crashing


## 2026-10-07T11:09:26Z

The server restarted and we were briefly cut off. Please resume execution from Milestone 4 worker implementation and drive through Milestones 4-7.


## 2026-10-07T16:30:08Z

The server restarted and we experienced an abrupt stop. Please resume execution immediately:
1. Finish the Milestone 5 Gate evaluation (Next.js Dashboard is built and committed in 483cb99).
2. Execute Milestone 6: Production Containerization & Resource Optimization (<900MB RAM footprint for 1GB VPS via docker-compose.yml, multi-stage Dockerfiles for backend and frontend, PostgreSQL and Redis resource tuning, health checks).
3. Execute Milestone 7: Final E2E Test Suite Verification & Hardening.
4. Report final completion.


## 2026-10-07T21:50:33Z

Both reviewer_m7_1 (APPROVE) and auditor_m7_1 (CLEAN) have completed their reviews with unanimous approvals and zero defects. All 332 tests across backend, frontend, and E2E pass 100%. Please finalize Milestone 7, trigger the independent Victory Auditor, and deliver the final victory report.
