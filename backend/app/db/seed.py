import copy
import logging
from typing import Dict, Any, List
from sqlalchemy import select
from sqlalchemy.orm.attributes import flag_modified
from sqlalchemy.ext.asyncio import AsyncSession
from backend.app.api.settings import DEFAULT_AI_STAGES
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
        "UKMTO",
        "sonic boom",
        "انفجار",
        "غارة",
        "جدار الصوت",
        "مسيرات",
        "صافرات الانذار",
        "پدافند",
        "صدای مهیب",
        "פיצוץ",
        "אזעקה",
        "יירוט",
        "צבע אדום",
        "פיקוד העורף",
        "חדירת כלי טיס",
        "ירי רקטות",
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
    # --- Official Civil Defense & Siren Sensors (Israel Home Front Command / Pikud HaOref) ---
    {
        "name": "Israel Home Front Command Alerts (פיקוד העורף)",
        "source_type": "oref",
        "config_json": {
            "target": "https://alerts-history.oref.org.il/Shared/Ajax/GetAlarmsHistory.aspx?lang=he&mode=1",
            "url": "https://alerts-history.oref.org.il/Shared/Ajax/GetAlarmsHistory.aspx?lang=he&mode=1",
            "realtime_url": "https://www.oref.org.il/WarningMessages/alert/alerts.json",
            "lang": "he",
            "poll_realtime": True,
        },
        "polling_interval_sec": 30,
        "is_enabled": True,
    },
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

    # --- Additional Catalog Telegram Web Preview Channels (No Account Required) ---
    {
        "name": "Abu Ali Express (Arabic/Hebrew Tactical OSINT)",
        "source_type": "telegram",
        "config_json": {"target": "@abualiexpress"},
        "polling_interval_sec": 120,
        "is_enabled": True,
    },
    {
        "name": "Gaza Now Urgent (غزة الآن - عاجل)",
        "source_type": "telegram",
        "config_json": {"target": "@GazaNow"},
        "polling_interval_sec": 90,
        "is_enabled": True,
    },
    {
        "name": "VahidOnline (Iran Citizen Ground Videos)",
        "source_type": "telegram",
        "config_json": {"target": "@VahidOnline"},
        "polling_interval_sec": 120,
        "is_enabled": True,
    },
    {
        "name": "1500tasvir (Iran Domestic Field Reports)",
        "source_type": "telegram",
        "config_json": {"target": "@tasvir1500"},
        "polling_interval_sec": 120,
        "is_enabled": True,
    },
    {
        "name": "BBC Persian Live (بی‌بی‌سی فارسی)",
        "source_type": "telegram",
        "config_json": {"target": "@bbcpersian"},
        "polling_interval_sec": 180,
        "is_enabled": True,
    },
    {
        "name": "Israel Radar (Defense & Airspace Alerts)",
        "source_type": "telegram",
        "config_json": {"target": "@IsraelRadar_com"},
        "polling_interval_sec": 120,
        "is_enabled": True,
    },
    {
        "name": "Intel Slava (Conflict Aggregator)",
        "source_type": "telegram",
        "config_json": {"target": "@intelslava"},
        "polling_interval_sec": 180,
        "is_enabled": True,
    },
    # --- Bluesky / ATProto Unauthenticated Public XRPC Streams ---
    {
        "name": "Bluesky ELINT News (@elintnews.bsky.social)",
        "source_type": "bluesky",
        "config_json": {"target": "elintnews.bsky.social", "mode": "author"},
        "polling_interval_sec": 90,
        "is_enabled": True,
    },
    {
        "name": "Bluesky OSINT & Conflict Live Search",
        "source_type": "bluesky",
        "config_json": {
            "target": "search:Iran OR Israel OR Beirut OR Tehran OR Houthi OR Hormuz",
            "mode": "search",
        },
        "polling_interval_sec": 90,
        "is_enabled": True,
    },
    # --- Mastodon / Fediverse Unauthenticated Timelines ---
    {
        "name": "Mastodon #OSINT Global Stream",
        "source_type": "mastodon",
        "config_json": {
            "target": "https://mastodon.social/api/v1/timelines/tag/OSINT?limit=20",
            "instance": "mastodon.social",
        },
        "polling_interval_sec": 120,
        "is_enabled": True,
    },
    {
        "name": "Mastodon #Iran & #MiddleEast Stream",
        "source_type": "mastodon",
        "config_json": {
            "target": "https://mastodon.social/api/v1/timelines/tag/Iran?limit=20",
            "instance": "mastodon.social",
        },
        "polling_interval_sec": 180,
        "is_enabled": True,
    },
    # --- Specialist Aviation, Radio & Live Conflict RSS ---
    {
        "name": "Liveuamap Middle East Tactical Feed",
        "source_type": "rss",
        "config_json": {"url": "https://middleeast.liveuamap.com/rss"},
        "polling_interval_sec": 120,
        "is_enabled": True,
    },
    {
        "name": "PPRuNe Military Aviation Rumour Network",
        "source_type": "rss",
        "config_json": {"url": "https://www.pprune.org/external.php?type=RSS2&forumids=14"},
        "polling_interval_sec": 240,
        "is_enabled": True,
    },
    {
        "name": "RadioReference HF/MilAir Monitoring Forum",
        "source_type": "rss",
        "config_json": {"url": "https://forums.radioreference.com/forums/hf-mw-lw-general-discussion.32/index.rss"},
        "polling_interval_sec": 300,
        "is_enabled": True,
    },
    # --- Physical & Geophysical Proxy Sensors (EMSC Seismic / LastQuake & UKMTO) ---
    {
        "name": "EMSC Middle East Seismic & LastQuake Felt Reports",
        "source_type": "seismic",
        "config_json": {
            "target": "https://www.seismicportal.eu/fdsnws/event/1/query?format=json&minlat=12&maxlat=40&minlon=32&maxlon=64&limit=15",
            "sensor_type": "seismic",
        },
        "polling_interval_sec": 120,
        "is_enabled": True,
    },
    {
        "name": "UKMTO Red Sea & Persian Gulf Maritime Advisories",
        "source_type": "ukmto",
        "config_json": {
            "target": "https://www.ukmto.org/indian-ocean/recent-incidents",
            "sensor_type": "ukmto",
        },
        "polling_interval_sec": 180,
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

DEFAULT_SETTINGS_DATA: Dict[str, Any] = {
    "ai_pipeline_stages": {
        **DEFAULT_AI_STAGES,
        "extraction_translation": DEFAULT_AI_STAGES["extraction"],
        "importance_scoring": DEFAULT_AI_STAGES["scoring"],
        "visual_generation": DEFAULT_AI_STAGES["visuals"],
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
        "bluesky": 1.0,
        "mastodon": 1.0,
        "seismic": 2.0,
        "ukmto": 6.0,
    },
    "polling_intervals": {
        "rss": 300,
        "telegram": 300,
        "newsapi": 600,
        "reddit": 60,
        "ais": 900,
        "bluesky": 90,
        "mastodon": 120,
        "seismic": 120,
        "ukmto": 180,
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

    # 2b. Automatically purge deprecated Reddit sources from database
    reddit_query = select(Source).where(Source.source_type == "reddit")
    reddit_res = await session.execute(reddit_query)
    reddit_sources = reddit_res.scalars().all()
    if reddit_sources:
        for r_src in reddit_sources:
            await session.delete(r_src)
        await session.flush()
        logger.info(f"Purged {len(reddit_sources)} deprecated Reddit sources from database.")

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
        elif key == "ai_pipeline_stages" and existing_setting and isinstance(existing_setting.value_json, dict):
            # If not explicitly customized via UI, keep synchronized with DEFAULT_AI_STAGES
            if not existing_setting.value_json.get("_customized", False):
                existing_setting.value_json = copy.deepcopy(DEFAULT_AI_STAGES)
                flag_modified(existing_setting, "value_json")
                session.add(existing_setting)
                logger.info("Synchronized ai_pipeline_stages with code defaults (DEFAULT_AI_STAGES).")
            else:
                # Auto-repair existing customized setting if fallback chains are missing, empty, or use deprecated models
                cur_stages = copy.deepcopy(existing_setting.value_json)
                repaired = False
                for s_key, s_def in val.items():
                    matched_cur = None
                    for cur_k in cur_stages:
                        if (
                            s_key in cur_k
                            or cur_k in s_key
                            or ("score" in s_key and "score" in cur_k)
                            or ("extract" in s_key and "extract" in cur_k)
                            or ("summar" in s_key and "summar" in cur_k)
                            or ("vis" in s_key and "vis" in cur_k)
                        ):
                            matched_cur = cur_k
                            break
                    if matched_cur:
                        target_stage = cur_stages[matched_cur]
                        # Check and migrate primary model if deprecated
                        for m_field in ("model", "model_name"):
                            if target_stage.get(m_field) == "llama-3.3-70b-versatile":
                                target_stage[m_field] = "openai/gpt-oss-120b"
                                repaired = True
                        # Check fallbacks
                        if not target_stage.get("fallbacks"):
                            target_stage["fallbacks"] = list(s_def.get("fallbacks", []))
                            repaired = True
                        else:
                            for fb in target_stage["fallbacks"]:
                                for m_field in ("model", "model_name"):
                                    if fb.get(m_field) == "llama-3.3-70b-versatile":
                                        fb[m_field] = "openai/gpt-oss-120b"
                                        repaired = True
                            # Ensure Gemma 4 is present at every fallback chain at least somewhere (highest RPD: 14,400)
                            has_gemma = any(
                                "gemma" in str(fb.get("model_name") or fb.get("model", "")).lower()
                                for fb in target_stage["fallbacks"]
                            )
                            if not has_gemma:
                                target_stage["fallbacks"].append({
                                    "provider": "gemini",
                                    "model": "gemma-4-26b-a4b-it",
                                    "temperature": target_stage.get("temperature", 0.2),
                                })
                                repaired = True
                    else:
                        cur_stages[s_key] = s_def
                        repaired = True
                if repaired:
                    existing_setting.value_json = cur_stages
                    flag_modified(existing_setting, "value_json")
                    session.add(existing_setting)
                    logger.info("Auto-repaired AI pipeline fallback chains (including Gemma 4 presence) and replaced deprecated models in existing database settings.")

    # 4. Sync persisted UI api_keys into runtime environment if not already provided via .env
    import os
    api_keys_res = await session.execute(
        select(SystemSettings).where(SystemSettings.key == "api_keys")
    )
    api_keys_row = api_keys_res.scalars().first()
    if api_keys_row and isinstance(api_keys_row.value_json, dict):
        env_var_map = {
            "gemini_api_key": "GEMINI_API_KEY",
            "groq_api_key": "GROQ_API_KEY",
            "hf_embedding_url": "HF_EMBEDDING_URL",
            "hf_api_key": "HF_API_KEY",
            "telegram_bot_token": "TELEGRAM_BOT_TOKEN",
            "telegram_chat_id": "TELEGRAM_CHAT_ID",
            "telegram_api_id": "TELEGRAM_API_ID",
            "telegram_api_hash": "TELEGRAM_API_HASH",
            "newsapi_key": "NEWSAPI_KEY",
            "reddit_client_id": "REDDIT_CLIENT_ID",
            "reddit_client_secret": "REDDIT_CLIENT_SECRET",
        }
        for k, env_name in env_var_map.items():
            db_val = api_keys_row.value_json.get(k)
            if db_val and not os.environ.get(env_name):
                os.environ[env_name] = str(db_val).strip()

    await session.commit()
    logger.info(f"Database seed complete: {seeded_counts}")
    return seeded_counts
