# Project Map — Realtime OSINT

## Backend (`backend/`)
- `app/main.py` — FastAPI application entrypoint
- `app/config.py` — Environment and runtime settings
- `app/auth.py` — Authentication and security helpers
- `app/api/` — REST API endpoints (`events.py`, `settings.py`, `sources.py`, `themes.py`, `stats.py`, `pipeline.py`, `embeddings.py`, `summary.py`, `pizza_meter.py`, `health.py`)
- `app/db/` — SQLAlchemy models (`models.py`), session (`session.py`), and initial seeding (`seed.py`)
- `app/pipeline/` — Multi-stage AI processing pipeline (`orchestrator.py`, `stage1_extract.py`, `stage2_score.py`, `stage3_summary.py`, `stage4_visuals.py`, `providers/gemini.py`, `providers/groq.py`)
- `app/plugins/` — Ingestion plugins (`rss_plugin.py`, `telegram_plugin.py`, `newsapi_plugin.py`, `reddit_plugin.py`, `ais_plugin.py`, `supervisor.py`)
- `app/services/` — Background services (`redis_bus.py`, `socket_server.py`, `telegram_bot.py`, `embedding_service.py`, `summary_service.py`)

## Frontend (`frontend/`)
- `src/app/` — Next.js App Router pages (`page.tsx`, `summary/page.tsx`, `map/page.tsx`, `trends/page.tsx`, `settings/page.tsx`, `layout.tsx`, `globals.css`)
- `src/components/Navbar.tsx` — Top navigation bar and status indicators
- `src/components/settings/` — Settings panels (`AIConfigMatrix.tsx`, `ApiKeyManager.tsx`, `ProviderRateLimitsBar.tsx`, `SourceManager.tsx`, `ThemeForm.tsx`)
- `src/components/timeline/` — Live intelligence feed components (`EventCard.tsx`, `EventDetailsModal.tsx`, `AlertWheel.tsx`, `ImportanceBadge.tsx`, `SourceBadge.tsx`)
- `src/components/map/` — Semantic relevance map (`RelevanceMap.tsx`, `EventInspector.tsx`)
- `src/components/charts/` — Analytics charts (`VolumeChart.tsx`, `ScoreDistributionChart.tsx`, `SourceActivityChart.tsx`)
- `src/components/stats/` — Dashboard metrics (`StatCards.tsx`, `PentagonPizzaMeter.tsx`)
- `src/lib/` — API client (`api.ts`), WebSocket client (`socket.ts`), TypeScript types (`types.ts`), and utilities (`utils.ts`)

## Tests (`tests/` & `backend/tests/` & `frontend/tests/`)
- E2E, unit, backend, and frontend test suites
