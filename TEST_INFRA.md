# Test Infrastructure & E2E Testing Strategy

**Project:** Real-time OSINT Gathering Service  
**Document Version:** 1.0.0  
**Owner:** test_writer_1  
**Target Architecture:** FastAPI Backend, Next.js Dashboard, Redis Pub/Sub, PostgreSQL, Telegram Alert Bot  
**Reference Baseline:** `ORIGINAL_REQUEST.md`, `PROJECT.md`, `spec_inventory.md`

---

## 1. Test Philosophy

The Real-time OSINT Gathering Service test suite is built upon an **opaque-box, requirement-driven, contract-verified** testing philosophy:

1. **Opaque-Box Verification:** Tests interact strictly with public contracts, HTTP REST API endpoints, WebSocket messages, message queue payloads, and external service interfaces. Tests make zero assumptions about internal private state, unexported classes, or specific database column order.
2. **Authoritative Specification Derivation:** Every test input, validation assertion, and expected output is directly derived from user requirements in `ORIGINAL_REQUEST.md` and architectural interface contracts in `PROJECT.md` and `spec_inventory.md`.
3. **Progressive Testability & Determinism:** Tests are deterministic, isolated, and self-contained. Offline mocks simulate external networks (Google Gemini API, Groq Cloud API, Telegram Bot API, Reddit API, NewsAPIs, and Maritime AIS feeds) with high fidelity, ensuring zero flaky network dependencies during CI and local execution.
4. **Adversarial & Edge-First Mindset:** The suite tests not only happy paths, but actively probes boundary extremes (score 1 and 10, empty payloads, malformed XML, mixed Farsi/Arabic scripts, rate limit bursts, and duplicate storms).
5. **Zero-Code Hot Reload Verification:** The test suite verifies that modifying theme keywords, scoring prompts, and source polling parameters takes immediate effect in the running system without requiring restarts.

---

## 2. Testing Methodology

Our testing approach uses four proven formal testing methodologies:

### 2.1 Category-Partition Method (Tier 1: Feature Coverage)
The functional domain is divided into distinct parameter partitions and categories:
- **Ingestion Source Types:** RSS/Atom (`rss`), Telegram MTProto (`telegram`), News APIs (`news_api`), Reddit PRAW (`reddit`), Maritime AIS (`maritime_ais`).
- **AI Pipeline Stages:** Stage 1 (Extraction & Multilingual Translation), Stage 2 (Importance Scoring 1-10), Stage 3 (Summarization & Digest), Stage 4 (Visuals & Trend Insights).
- **AI Providers:** Google Gemini vs. Groq Cloud (supporting per-stage heterogeneous provider assignment).
- **Theme Domains:** Dynamic topic definitions (US-Iran Conflict, China-Taiwan Tensions, Ukraine-Black Sea, Custom).
- **Security & Infrastructure:** Authentication headers (`X-API-Key`), health/readiness endpoints, structured JSON logging, resource metrics.

Every feature category contains at least 5 independent, comprehensive test cases.

### 2.2 Boundary Value Analysis (Tier 2: Boundary & Corner Cases)
Probes critical edge boundaries:
- **Importance Score Limits:** Min boundary (score = 1), max boundary (score = 10), clamping out-of-range model outputs (`0`, `11`, `"15/10"`).
- **Threshold Boundaries:** Exactly at threshold (e.g. score = 7 -> alert triggered), just below threshold (score = 6 -> alert suppressed).
- **Payload Extremes:** Zero-length strings, empty RSS XML `<rss></rss>`, 50KB large article bodies, truncated texts.
- **Multilingual & Encoding Extremes:** Pure Persian/Farsi (`فوری`), Arabic (`عاجل`), mixed RTL/LTR texts, emojis, control characters, SQL injection strings, JSON-breaking quotes.
- **Rate Limit & Fault Boundaries:** HTTP 429 Too Many Requests, HTTP 401 Unauthorized, HTTP 503 Service Unavailable, network timeouts, empty feed items.

### 2.3 Pairwise Combinatorial Testing (Tier 3: Cross-Feature Interactions)
Validates interactions across decoupled components:
- **Hot-Reload Pipeline:** Theme keyword modification -> Source poller reconfiguration -> Ingestion -> AI scoring with new prompt -> Telegram alert dispatch.
- **Heterogeneous AI Multi-Provider:** Stage 1 executed via Groq (`llama-3.3-70b-versatile`) while Stage 2 is executed via Gemini (`gemini-2.0-flash`) within the exact same event run.
- **Source Toggling & Stats:** Disabling a source shuts down polling tasks, updates active sources stat counter, and rejects subsequent manual triggers.
- **Cross-Source Deduplication:** Identical breaking news reported by both an RSS feed and a News API within a short window is deduplicated via SHA-256 fingerprinting before incurring duplicate LLM pipeline costs.

### 2.4 Realistic Operational Workloads (Tier 4: End-to-End Scenarios)
Simulates real-world operational environments:
- **Simulated US-Iran Conflict Event Stream:** End-to-end ingestion of multilingual news (English, Persian, Arabic) from diverse sources, translating, scoring, summarizing, and publishing to the live dashboard timeline.
- **Breaking News Surge & Deduplication Storm:** Sudden burst of 50 concurrent events with duplicate stories; verifies deduplication, token-bucket alert throttling, and anti-flood protection.
- **Sub-60s Critical Alert Guarantee:** Urgent military kinetic strike event (score >= 9) verified from ingestion to Telegram alert delivery within 60 seconds.
- **Full Operational Lifecycle:** Initial seed load -> Theme activation -> Polling -> AI enrichment -> WebSocket push -> Notification -> Persistence query.

---

## 3. Feature Inventory & Tier Mapping

| Feature ID | Feature Name | Description | Test Tier | Primary Test File |
|------------|--------------|-------------|-----------|-------------------|
| **F01** | RSS/Atom Ingestion | Ingest from configurable RSS/Atom feeds | Tier 1 | `tests/e2e/test_tier1_features.py` |
| **F02** | Configurable RSS Polling | Polling interval configurable per feed | Tier 1 | `tests/e2e/test_tier1_features.py` |
| **F03** | Telegram Channel Listener | Telethon MTProto event ingestion | Tier 1 | `tests/e2e/test_tier1_features.py` |
| **F04** | Telegram Credentials Config | API ID / Hash and session configuration | Tier 1 | `tests/e2e/test_tier1_features.py` |
| **F05** | News APIs Ingestion | NewsAPI, Currents, GDELT 2.0 Doc API | Tier 1 | `tests/e2e/test_tier1_features.py` |
| **F06** | News APIs Polling Config | Configurable interval for News APIs | Tier 1 | `tests/e2e/test_tier1_features.py` |
| **F07** | Reddit API Ingestion | Subreddit monitoring via AsyncPRAW | Tier 1 | `tests/e2e/test_tier1_features.py` |
| **F08** | Reddit Polling Config | Subreddit selection and interval config | Tier 1 | `tests/e2e/test_tier1_features.py` |
| **F09** | Maritime / AIS Tracking | AIS vessel tracking in critical waterways | Tier 1 | `tests/e2e/test_tier1_features.py` |
| **F10** | Independent Source Control | Enable/disable and interval control per source | Tier 1 | `tests/e2e/test_tier1_features.py` |
| **F11** | Common Event Normalization | NormalizedEvent schema compliance | Tier 1 | `tests/e2e/test_tier1_features.py` |
| **F12** | Isolated Error Handling | Single source failure does not crash others | Tier 1, Tier 2 | `test_tier1_features.py`, `test_tier2_boundaries.py` |
| **F13** | Dashboard Source Management | CRUD API for sources | Tier 1 | `tests/e2e/test_tier1_features.py` |
| **F14** | AI Stage 1: Extraction | Entity and key fact extraction | Tier 1 | `tests/e2e/test_tier1_features.py` |
| **F15** | AI Stage 1: Translation | Persian/Arabic translation to English | Tier 1, Tier 2 | `test_tier1_features.py`, `test_tier2_boundaries.py` |
| **F16** | AI Stage 2: Scoring | Importance assessment (1-10) | Tier 1, Tier 2 | `test_tier1_features.py`, `test_tier2_boundaries.py` |
| **F17** | AI Stage 2: Custom Prompt | Dynamic scoring instructions | Tier 1, Tier 3 | `test_tier1_features.py`, `test_tier3_cross_feature.py` |
| **F18** | AI Stage 2: Alert Threshold | Configurable alert threshold triggering | Tier 1, Tier 3 | `test_tier1_features.py`, `test_tier3_cross_feature.py` |
| **F19** | AI Stage 3: Summarization | Concise event summary generation | Tier 1 | `tests/e2e/test_tier1_features.py` |
| **F20** | AI Stage 3: Digest Generation | Periodic executive intelligence digest | Tier 1 | `tests/e2e/test_tier1_features.py` |
| **F21** | AI Stage 4: Visuals & Trends | Trend detection and visual narrative | Tier 1 | `tests/e2e/test_tier1_features.py` |
| **F22** | Per-Stage Provider Selection | Independent Gemini vs Groq selection | Tier 1, Tier 3 | `test_tier1_features.py`, `test_tier3_cross_feature.py` |
| **F23** | Per-Stage Model Selection | Specific model names per stage | Tier 1 | `tests/e2e/test_tier1_features.py` |
| **F24** | API Key Management | Independent keys for AI and providers | Tier 1, Tier 2 | `test_tier1_features.py`, `test_tier2_boundaries.py` |
| **F25** | Concurrent Multi-Provider | Groq and Gemini in the same pipeline run | Tier 3 | `tests/e2e/test_tier3_cross_feature.py` |
| **F26** | Theme Metadata Definition | Theme name, ID, and description | Tier 1 | `tests/e2e/test_tier1_features.py` |
| **F27** | Theme Keywords & Entities | Tracked keywords, entities, locations | Tier 1, Tier 3 | `test_tier1_features.py`, `test_tier3_cross_feature.py` |
| **F28** | Theme Scoring Prompt | Theme natural language prompt | Tier 1, Tier 3 | `test_tier1_features.py`, `test_tier3_cross_feature.py` |
| **F29** | Theme Source Presets | Presets linked to specific themes | Tier 1 | `tests/e2e/test_tier1_features.py` |
| **F30** | Theme Notification Rules | Threshold and frequency limits | Tier 1, Tier 3 | `test_tier1_features.py`, `test_tier3_cross_feature.py` |
| **F31** | Pre-configured US-Iran Theme | Default seed US-Iran conflict theme | Tier 1, Tier 4 | `test_tier1_features.py`, `test_tier4_scenarios.py` |
| **F32** | Zero-Code Reconfiguration | Dynamic update without server restart | Tier 3 | `tests/e2e/test_tier3_cross_feature.py` |
| **F33** | Dark-Mode-Native Schema | Theme and layout styling attributes | Tier 1 | `tests/e2e/test_tier1_features.py` |
| **F34** | Event Timeline & WebSockets | Chronological feed and live broadcast | Tier 1, Tier 4 | `test_tier1_features.py`, `test_tier4_scenarios.py` |
| **F35** | Importance Badges | Color-coded 1-10 badges | Tier 1, Tier 2 | `test_tier1_features.py`, `test_tier2_boundaries.py` |
| **F36** | Source Attribution & Expansion | Metadata, source badge, raw text | Tier 1 | `tests/e2e/test_tier1_features.py` |
| **F37** | Trend Charts & Stat Cards | Event volume, score dist, 4 stat cards | Tier 1 | `tests/e2e/test_tier1_features.py` |
| **F38** | Full Settings Management UI | CRUD REST API backing Settings page | Tier 1 | `tests/e2e/test_tier1_features.py` |
| **F39** | API Authentication | API key security (`X-API-Key`) | Tier 1, Tier 2 | `test_tier1_features.py`, `test_tier2_boundaries.py` |
| **F40** | Telegram Bot Alert System | Formatted alert with summary, score, link | Tier 1, Tier 3 | `test_tier1_features.py`, `test_tier3_cross_feature.py` |
| **F41** | Persistent Storage & Cache | Event, theme, source persistence | Tier 1, Tier 4 | `test_tier1_features.py`, `test_tier4_scenarios.py` |
| **F42** | Production Health & Limits | Health endpoints, memory < 900MB | Tier 1, Tier 2 | `test_tier1_features.py`, `test_tier2_boundaries.py` |

---

## 4. Test Suite Structure & Layout

```
tests/
├── __init__.py
├── conftest.py                  # Core fixtures, offline mocks, contract reference app, client helpers
├── run_e2e_tests.py             # Custom CLI test runner with tier statistics & reporting
└── e2e/
    ├── __init__.py
    ├── test_tier1_features.py   # Tier 1: Feature coverage (>=5 tests per requirement)
    ├── test_tier2_boundaries.py # Tier 2: Boundary value analysis & error handling
    ├── test_tier3_cross_feature.py # Tier 3: Pairwise combinatorial & hot-reload integration
    └── test_tier4_scenarios.py  # Tier 4: Real-world operational scenarios & news storm
```

---

## 5. Coverage Thresholds & Quality Gates

To ensure software delivery integrity, the following gates are enforced:

1. **Pass Rate:** **100% of all tests must pass cleanly.** Zero skips (unless explicitly marked conditional on missing external hardware), zero failures, zero errors.
2. **Tier 1 Feature Coverage:**
   - Source Ingestion Plugins: >= 5 test cases per plugin (RSS, Telegram, NewsAPI, Reddit, Maritime AIS) = 25+ tests
   - AI Multi-Stage Pipeline: >= 5 test cases
   - Theme System: >= 5 test cases
   - WebSocket & Timeline: >= 5 test cases
   - Telegram Bot Alert Engine: >= 5 test cases
   - Authentication & Security: >= 5 test cases
   - Health Check & System Monitoring: >= 5 test cases
   - Total Tier 1: >= 55 test cases
3. **Tier 2 Boundary Coverage:** >= 15 test cases covering empty inputs, malformed XML, missing keys, 429 backoff, Unicode scripts, score clamping (1-10), and payload stress.
4. **Tier 3 Cross-Feature Coverage:** >= 10 test cases covering pairwise combinations, dynamic hot-reloads, multi-provider runs, and cross-source deduplication.
5. **Tier 4 Scenario Coverage:** >= 8 test cases validating live US-Iran event stream, breaking news storm, sub-60s alert delivery, and full system lifecycle.
6. **Total Suite Volume:** >= 88 comprehensive test cases.

---

## 6. Execution Instructions

### Running with Pytest Directly
To execute the entire E2E test suite with verbose reporting:
```powershell
python -m pytest tests/ -v
```

To run a specific tier:
```powershell
python -m pytest tests/e2e/test_tier1_features.py -v
python -m pytest tests/e2e/test_tier2_boundaries.py -v
python -m pytest tests/e2e/test_tier3_cross_feature.py -v
python -m pytest tests/e2e/test_tier4_scenarios.py -v
```

### Running with the Custom E2E Runner
The project includes a dedicated test runner that collects execution statistics across all tiers, validates coverage requirements, and prints an executive summary:
```powershell
python tests/run_e2e_tests.py
```
Exit code is `0` on 100% test pass and non-zero on any failure.
