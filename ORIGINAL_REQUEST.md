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
