import logging
from typing import Dict, Any, List
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from backend.app.db.models import Theme, Source, SystemSettings

logger = logging.getLogger("osint.seed")

DEFAULT_THEME_DATA: Dict[str, Any] = {
    "name": "US-Iran conflict",
    "slug": "us-iran-conflict",
    "description": (
        "Real-time tactical intelligence tracking military friction, maritime security, "
        "diplomatic posturing, and proxy actions involving the United States, Iran, Israel, "
        "and regional proxies across the Middle East."
    ),
    "keywords": [
        "Strait of Hormuz",
        "IRGC",
        "CENTCOM",
        "uranium",
        "sanctions",
        "ballistic",
        "proxy",
        "Persian Gulf",
        "Bab el-Mandeb",
        "Shahed",
        "Houthis",
        "Hezbollah",
        "enrichment",
        "Red Sea",
        "Dahiyeh",
        "Gaza",
        "Nevatim",
        "Dimona",
        "Iron Dome",
        "Arrow 3",
        "Fattah",
        "Al-Asad",
    ],
    "entities": [
        "United States",
        "Iran",
        "Israel",
        "IRGC",
        "US Navy",
        "CENTCOM",
        "Khamenei",
        "Pezeshkian",
        "Hezbollah",
        "Houthis",
        "Ansar Allah",
        "IDF",
    ],
    "scoring_prompt": (
        "You are a senior geopolitical intelligence analyst. Score the following event on a "
        "1-10 importance scale for the US-Iran conflict theme. Guidelines: "
        "Score 9-10 for direct kinetic military engagements, strikes on naval/nuclear assets, or declared nuclear breakout. "
        "Score 7-8 for proxy attacks, major sanctions, troop deployments, or heightened naval blockades. "
        "Score 4-6 for standard diplomatic rhetoric, non-escalatory drills, or minor statements. "
        "Score 1-3 for background political commentary or irrelevant noise. "
        "Provide an integer score and a 1-sentence rationale."
    ),
    "is_active": True,
    "alert_threshold": 7,
}

DEFAULT_SOURCES_DATA: List[Dict[str, Any]] = [
    # --- Tactical Siren & Threat Feeds (High-Priority Fast Ingestion: Tier 1) ---
    {
        "name": "Rocket Alert (Tactical Sirens)",
        "source_type": "telegram",
        "config_json": {"target": "@RocketAlert", "is_alert_channel": True},
        "polling_interval_sec": 60,
        "is_enabled": True,
    },
    {
        "name": "Miklat Alerts (Arabic Mirror)",
        "source_type": "telegram",
        "config_json": {"target": "@miklat_alerts_ar", "is_alert_channel": True},
        "polling_interval_sec": 60,
        "is_enabled": True,
    },
    # --- High-Speed Arabic Breaking Wires (Tier 1 & 2: 1-3 min) ---
    {
        "name": "Al Jazeera Urgent (الجزيرة - عاجل)",
        "source_type": "telegram",
        "config_json": {"target": "@AJABreaking"},
        "polling_interval_sec": 120,
        "is_enabled": True,
    },
    {
        "name": "Al Arabiya Urgent (العربية - عاجل)",
        "source_type": "telegram",
        "config_json": {"target": "@AlArabiya_Brk"},
        "polling_interval_sec": 120,
        "is_enabled": True,
    },
    {
        "name": "Al Mayadeen Breaking (الميادين - عاجل)",
        "source_type": "telegram",
        "config_json": {"target": "@almayadeen"},
        "polling_interval_sec": 120,
        "is_enabled": True,
    },
    {
        "name": "Sky News Arabia Breaking (سكاي نيوز - عاجل)",
        "source_type": "telegram",
        "config_json": {"target": "@skynewsarabia_breaking"},
        "polling_interval_sec": 120,
        "is_enabled": True,
    },
    {
        "name": "Al Hadath Breaking News",
        "source_type": "telegram",
        "config_json": {"target": "@alhadath"},
        "polling_interval_sec": 120,
        "is_enabled": True,
    },
    # --- On-Field Tactical & Frontline Ground Sensors (Arabic: Tier 1, 30-90s) ---
    {
        "name": "Bint Jbeil News (South Lebanon Ground)",
        "source_type": "telegram",
        "config_json": {"target": "@bintjbeilnews"},
        "polling_interval_sec": 120,
        "is_enabled": True,
    },
    {
        "name": "Ali Shoeib (Blue Line Frontline Correspondent)",
        "source_type": "telegram",
        "config_json": {"target": "@alishoeib1970"},
        "polling_interval_sec": 120,
        "is_enabled": True,
    },
    {
        "name": "Quds News Network (Palestine / Gaza)",
        "source_type": "telegram",
        "config_json": {"target": "@QudsN"},
        "polling_interval_sec": 120,
        "is_enabled": True,
    },
    {
        "name": "Shehab News Agency (Palestine)",
        "source_type": "telegram",
        "config_json": {"target": "@ShehabTelegram"},
        "polling_interval_sec": 120,
        "is_enabled": True,
    },
    {
        "name": "Hezbollah Central Military Media",
        "source_type": "telegram",
        "config_json": {"target": "@mmirleb"},
        "polling_interval_sec": 180,
        "is_enabled": True,
    },
    {
        "name": "Sabereen News (Iraq Resistance Tripwire)",
        "source_type": "telegram",
        "config_json": {"target": "@Sabereen_News"},
        "polling_interval_sec": 120,
        "is_enabled": True,
    },
    {
        "name": "PMF Media War (Iraqi Paramilitary)",
        "source_type": "telegram",
        "config_json": {"target": "@teamsmediawar_1"},
        "polling_interval_sec": 180,
        "is_enabled": True,
    },
    {
        "name": "Al-Masirah Urgent (Yemen / Red Sea Kinetic)",
        "source_type": "telegram",
        "config_json": {"target": "@almasirah2"},
        "polling_interval_sec": 120,
        "is_enabled": True,
    },
    {
        "name": "Yemeni Armed Forces Spox Yahya Saree",
        "source_type": "telegram",
        "config_json": {"target": "@army21ye"},
        "polling_interval_sec": 180,
        "is_enabled": True,
    },
    {
        "name": "SANA Urgent (Syrian Official Media Wire)",
        "source_type": "telegram",
        "config_json": {"target": "@sana_gov_sy"},
        "polling_interval_sec": 180,
        "is_enabled": True,
    },
    # --- Open Discussion Supergroups & OSINT Communities ---
    {
        "name": "War Monitors Discussion Supergroup",
        "source_type": "telegram",
        "config_json": {"target": "@WarMonitors_Discussion"},
        "polling_interval_sec": 120,
        "is_enabled": True,
    },
    # --- Elite Multilingual & Defense OSINT Telegram Aggregators ---
    {
        "name": "War Monitor (Rapid OSINT Synthesis)",
        "source_type": "telegram",
        "config_json": {"target": "@warmonitors"},
        "polling_interval_sec": 120,
        "is_enabled": True,
    },
    {
        "name": "Clash Report (Tactical Military OSINT)",
        "source_type": "telegram",
        "config_json": {"target": "@ClashReport"},
        "polling_interval_sec": 180,
        "is_enabled": True,
    },
    {
        "name": "Middle East Spectator",
        "source_type": "telegram",
        "config_json": {"target": "@middleeastspectator"},
        "polling_interval_sec": 180,
        "is_enabled": True,
    },
    {
        "name": "Faytuks News (Breaking Wire OSINT)",
        "source_type": "telegram",
        "config_json": {"target": "@FaytuksNews"},
        "polling_interval_sec": 180,
        "is_enabled": True,
    },
    {
        "name": "OSINTdefender (Defense Telemetry)",
        "source_type": "telegram",
        "config_json": {"target": "@sentdefender"},
        "polling_interval_sec": 180,
        "is_enabled": True,
    },
    {
        "name": "IntelSky (Air & Radar ISR Tracking)",
        "source_type": "telegram",
        "config_json": {"target": "@Intel_Sky"},
        "polling_interval_sec": 180,
        "is_enabled": True,
    },
    {
        "name": "Aurora Intel (Conflict Verification)",
        "source_type": "telegram",
        "config_json": {"target": "@auroraintel"},
        "polling_interval_sec": 180,
        "is_enabled": True,
    },
    {
        "name": "301 Arab World News (Field Alerts)",
        "source_type": "telegram",
        "config_json": {"target": "@arabworld301news"},
        "polling_interval_sec": 180,
        "is_enabled": True,
    },
    {
        "name": "Iran International English",
        "source_type": "telegram",
        "config_json": {"target": "@IranIntl_En"},
        "polling_interval_sec": 180,
        "is_enabled": True,
    },
    {
        "name": "Al Mayadeen English",
        "source_type": "telegram",
        "config_json": {"target": "@AlMayadeenEnglish"},
        "polling_interval_sec": 240,
        "is_enabled": True,
    },
    # --- Low-Latency Regional Arabic & International Defense RSS Wires ---
    {
        "name": "Sky News Arabia RSS",
        "source_type": "rss",
        "config_json": {"url": "https://www.skynewsarabia.com/rss"},
        "polling_interval_sec": 180,
        "is_enabled": True,
    },
    {
        "name": "Al Jazeera Arabic Portal RSS",
        "source_type": "rss",
        "config_json": {"url": "https://www.aljazeera.net/aljazeerarss"},
        "polling_interval_sec": 180,
        "is_enabled": True,
    },
    {
        "name": "BBC Arabic Breaking RSS",
        "source_type": "rss",
        "config_json": {"url": "https://feeds.bbci.co.uk/arabic/rss.xml"},
        "polling_interval_sec": 240,
        "is_enabled": True,
    },
    {
        "name": "Syrian Arab News Agency (SANA RSS)",
        "source_type": "rss",
        "config_json": {"url": "https://sana.sy/en/?feed=rss2"},
        "polling_interval_sec": 300,
        "is_enabled": True,
    },
    {
        "name": "The War Zone (TWZ Defense Tech)",
        "source_type": "rss",
        "config_json": {"url": "https://www.twz.com/feed"},
        "polling_interval_sec": 300,
        "is_enabled": True,
    },
    {
        "name": "USNI News (Naval Operations)",
        "source_type": "rss",
        "config_json": {"url": "https://news.usni.org/feed"},
        "polling_interval_sec": 300,
        "is_enabled": True,
    },
    {
        "name": "Al-Monitor (Middle East Pulse)",
        "source_type": "rss",
        "config_json": {"url": "https://www.al-monitor.com/rss"},
        "polling_interval_sec": 300,
        "is_enabled": True,
    },
    {
        "name": "Long War Journal (IRGC & Proxy Networks)",
        "source_type": "rss",
        "config_json": {"url": "http://feeds.feedburner.com/LongWarJournal"},
        "polling_interval_sec": 300,
        "is_enabled": True,
    },
    {
        "name": "Defense News (Air & Missile Defense)",
        "source_type": "rss",
        "config_json": {"url": "https://www.defensenews.com/arc/outboundfeeds/rss/"},
        "polling_interval_sec": 300,
        "is_enabled": True,
    },
    {
        "name": "IRNA English (Tehran State Wire)",
        "source_type": "rss",
        "config_json": {"url": "https://en.irna.ir/rss"},
        "polling_interval_sec": 300,
        "is_enabled": True,
    },
    # --- Community OSINT & Open-Source Forum Signals ---
    {
        "name": "Reddit Geopolitics, OSINT & CombatFootage",
        "source_type": "reddit",
        "config_json": {
            "subreddits": ["worldnews", "geopolitics", "OSINT", "CombatFootage"],
            "min_upvotes": 50,
        },
        "polling_interval_sec": 600,
        "is_enabled": True,
    },
    # --- Strategic Maritime Chokepoint Tracking ---
    {
        "name": "Maritime Strait of Hormuz",
        "source_type": "maritime_ais",
        "config_json": {
            "bbox": [25.0, 54.0, 27.5, 57.5],
            "zone_name": "Strait of Hormuz",
            "provider": "aisstream",
        },
        "polling_interval_sec": 600,
        "is_enabled": True,
    },
    {
        "name": "Maritime Bab el-Mandeb (Red Sea)",
        "source_type": "maritime_ais",
        "config_json": {
            "bbox": [11.5, 42.5, 14.0, 44.5],
            "zone_name": "Bab el-Mandeb & Red Sea",
            "provider": "aisstream",
        },
        "polling_interval_sec": 600,
        "is_enabled": True,
    },
]

DEFAULT_SETTINGS_DATA: Dict[str, Dict[str, Any]] = {
    "ai_pipeline_stages": {
        "extraction_translation": {
            "stage_id": "extraction_translation",
            "provider": "groq",
            "model": "qwen/qwen3.8-27b",
            "temperature": 0.2,
            "enabled": True,
            "fallbacks": [
                {"provider": "gemini", "model": "gemini-3.5-flash-lite", "temperature": 0.2},
                {"provider": "gemini", "model": "gemini-3.1-flash-lite", "temperature": 0.2},
            ],
        },
        "importance_scoring": {
            "stage_id": "importance_scoring",
            "provider": "gemini",
            "model": "gemini-3.5-flash-lite",
            "temperature": 0.1,
            "enabled": True,
            "fallbacks": [
                {"provider": "gemini", "model": "gemini-3.1-flash-lite", "temperature": 0.1},
            ],
        },
        "summarization": {
            "stage_id": "summarization",
            "provider": "groq",
            "model": "openai/gpt-oss-120b",
            "temperature": 0.2,
            "enabled": True,
            "fallbacks": [
                {"provider": "groq", "model": "qwen/qwen3.8-27b", "temperature": 0.2},
                {"provider": "gemini", "model": "gemini-3.5-flash-lite", "temperature": 0.2},
                {"provider": "gemini", "model": "gemini-3.1-flash-lite", "temperature": 0.2},
            ],
        },
        "visual_generation": {
            "stage_id": "visual_generation",
            "provider": "groq",
            "model": "openai/gpt-oss-20b",
            "temperature": 0.2,
            "enabled": True,
            "fallbacks": [
                {"provider": "groq", "model": "qwen/qwen3.8-27b", "temperature": 0.2},
                {"provider": "gemini", "model": "gemini-3.5-flash-lite", "temperature": 0.2},
                {"provider": "gemini", "model": "gemini-3.1-flash-lite", "temperature": 0.2},
            ],
        },
    },
    "notification_settings": {
        "default_alert_threshold": 7,
        "cooldown_seconds": 30,
        "channel": "telegram",
        "bot_enabled": True,
    },
    "system_general": {
        "active_theme": "US-Iran conflict",
        "theme_switch_mode": "zero_code",
        "memory_budget_mb": 900,
    },
    "max_lookback_hours": {
        "rss": 1.0,
        "telegram": 1.0,
        "newsapi": 1.0,
        "reddit": 1.0,
        "ais": 1.0,
    },
    "polling_intervals": {
        "rss": 300,
        "telegram": 300,
        "newsapi": 600,
        "reddit": 600,
        "ais": 900,
    },
}


async def seed_initial_data(session: AsyncSession) -> Dict[str, Any]:
    """
    Seed initial pre-configured theme, sources, and system settings.
    Idempotent: skips existing records to prevent overwrites.
    """
    logger.info("Starting initial database seed...")
    seeded_counts = {"themes": 0, "sources": 0, "settings": 0}

    # 1. Seed or retrieve "US-Iran conflict" theme
    query = select(Theme).where(
        (Theme.name == DEFAULT_THEME_DATA["name"]) | (Theme.slug == "us-iran-conflict")
    )
    result = await session.execute(query)
    theme = result.scalars().first()

    if not theme:
        theme = Theme(
            slug=DEFAULT_THEME_DATA.get("slug", "us-iran-conflict"),
            name=DEFAULT_THEME_DATA["name"],
            description=DEFAULT_THEME_DATA["description"],
            keywords=DEFAULT_THEME_DATA["keywords"],
            entities=DEFAULT_THEME_DATA["entities"],
            scoring_prompt=DEFAULT_THEME_DATA["scoring_prompt"],
            is_active=DEFAULT_THEME_DATA["is_active"],
            alert_threshold=DEFAULT_THEME_DATA["alert_threshold"],
        )
        session.add(theme)
        await session.flush()
        seeded_counts["themes"] += 1
        logger.info(f"Seeded theme: {theme.name} (id={theme.id}, slug={theme.slug})")
    else:
        if not theme.slug:
            theme.slug = "us-iran-conflict"
            session.add(theme)
            await session.flush()
            logger.info(f"Auto-repaired missing slug for existing theme: {theme.name} -> {theme.slug}")
        logger.debug(f"Theme '{theme.name}' already exists (id={theme.id}), skipping creation.")

    # 2. Seed sources for the active theme
    for src_data in DEFAULT_SOURCES_DATA:
        src_query = select(Source).where(
            Source.theme_id == theme.id, Source.name == src_data["name"]
        )
        src_res = await session.execute(src_query)
        existing_src = src_res.scalars().first()

        if not existing_src:
            src_cfg = dict(src_data.get("config_json") or {})
            src_cfg.setdefault("max_lookback_hours", 1.0)
            new_src = Source(
                theme_id=theme.id,
                name=src_data["name"],
                source_type=src_data["source_type"],
                config_json=src_cfg,
                polling_interval_sec=src_data["polling_interval_sec"],
                is_enabled=src_data["is_enabled"],
            )
            session.add(new_src)
            seeded_counts["sources"] += 1
            logger.info(f"Seeded source '{new_src.name}' ({new_src.source_type})")

    # 3. Seed default system settings
    for key, val in DEFAULT_SETTINGS_DATA.items():
        set_query = select(SystemSettings).where(SystemSettings.key == key)
        set_res = await session.execute(set_query)
        existing_setting = set_res.scalars().first()

        if not existing_setting:
            new_setting = SystemSettings(key=key, value_json=val)
            session.add(new_setting)
            seeded_counts["settings"] += 1
            logger.info(f"Seeded system setting: {key}")

    await session.commit()
    logger.info(f"Database seed complete: {seeded_counts}")
    return seeded_counts
