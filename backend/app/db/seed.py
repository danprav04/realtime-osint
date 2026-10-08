import logging
from typing import Dict, Any, List
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from backend.app.db.models import Theme, Source, SystemSettings

logger = logging.getLogger("osint.seed")

DEFAULT_THEME_DATA: Dict[str, Any] = {
    "name": "US-Iran conflict",
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
    {
        "name": "Reuters World News",
        "source_type": "rss",
        "config_json": {"url": "https://feeds.reuters.com/reuters/worldNews"},
        "polling_interval_sec": 300,
        "is_enabled": True,
    },
    {
        "name": "BBC World News",
        "source_type": "rss",
        "config_json": {"url": "http://feeds.bbci.co.uk/news/world/rss.xml"},
        "polling_interval_sec": 300,
        "is_enabled": True,
    },
    {
        "name": "Al Jazeera English",
        "source_type": "rss",
        "config_json": {"url": "https://www.aljazeera.com/xml/rss/all.xml"},
        "polling_interval_sec": 300,
        "is_enabled": True,
    },
    {
        "name": "IRNA English",
        "source_type": "rss",
        "config_json": {"url": "https://en.irna.ir/rss"},
        "polling_interval_sec": 600,
        "is_enabled": True,
    },
    {
        "name": "Suggested Telegram Channels",
        "source_type": "telegram",
        "config_json": {"channels": ["@OSINTdefender", "@MiddleEastSpectator", "@iraborna"]},
        "polling_interval_sec": 300,
        "is_enabled": True,
    },
    {
        "name": "Reddit Geopolitics & WorldNews",
        "source_type": "reddit",
        "config_json": {"subreddits": ["worldnews", "geopolitics"], "min_upvotes": 50},
        "polling_interval_sec": 600,
        "is_enabled": True,
    },
    {
        "name": "Maritime Strait of Hormuz",
        "source_type": "maritime_ais",
        "config_json": {
            "bbox": [25.0, 54.0, 27.5, 57.5],
            "zone_name": "Strait of Hormuz",
            "provider": "aisstream",
        },
        "polling_interval_sec": 900,
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
        },
        "importance_scoring": {
            "stage_id": "importance_scoring",
            "provider": "gemini",
            "model": "gemini-3.5-flash-lite",
            "temperature": 0.1,
            "enabled": True,
        },
        "summarization": {
            "stage_id": "summarization",
            "provider": "groq",
            "model": "openai/gpt-oss-120b",
            "temperature": 0.2,
            "enabled": True,
        },
        "visual_generation": {
            "stage_id": "visual_generation",
            "provider": "groq",
            "model": "openai/gpt-oss-20b",
            "temperature": 0.2,
            "enabled": True,
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
}


async def seed_initial_data(session: AsyncSession) -> Dict[str, Any]:
    """
    Seed initial pre-configured theme, sources, and system settings.
    Idempotent: skips existing records to prevent overwrites.
    """
    logger.info("Starting initial database seed...")
    seeded_counts = {"themes": 0, "sources": 0, "settings": 0}

    # 1. Seed or retrieve "US-Iran conflict" theme
    query = select(Theme).where(Theme.name == DEFAULT_THEME_DATA["name"])
    result = await session.execute(query)
    theme = result.scalars().first()

    if not theme:
        theme = Theme(
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
        logger.info(f"Seeded theme: {theme.name} (id={theme.id})")
    else:
        logger.debug(f"Theme '{theme.name}' already exists (id={theme.id}), skipping creation.")

    # 2. Seed sources for the active theme
    for src_data in DEFAULT_SOURCES_DATA:
        src_query = select(Source).where(
            Source.theme_id == theme.id, Source.name == src_data["name"]
        )
        src_res = await session.execute(src_query)
        existing_src = src_res.scalars().first()

        if not existing_src:
            new_src = Source(
                theme_id=theme.id,
                name=src_data["name"],
                source_type=src_data["source_type"],
                config_json=src_data["config_json"],
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
