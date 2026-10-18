"""
backend/app/services/world_theater_service.py - Continuous Live World Theater Context Engine
=============================================================================================
Maintains an authoritative, continuously synced geopolitical baseline of the global theater:
  - Temporal anchors and current operational year (e.g. 2026).
  - Incumbent world leaders, defense officials, and command structures.
  - Active conflict theaters, maritime chokepoints, and kinetic flashpoints.
  - Continuous live synchronization with the Wikidata Entity REST API and Wikipedia endpoints.
  - Dynamic prompt block generation for injection across all AI pipeline stages and SitReps.
  - Defensive text normalization guardrails.
"""

import asyncio
from datetime import datetime, timezone
import json
import logging
import os
import re
from typing import Any, Dict, List, Optional
import httpx
from sqlalchemy import select
from sqlalchemy.orm.attributes import flag_modified

logger = logging.getLogger("osint.world_theater")

SETTINGS_KEY_WORLD_THEATER = "world_theater_context"
WIKIDATA_ENTITIES_URL = "https://www.wikidata.org/w/api.php"

# Key Wikidata item IDs for global leadership
WIKIDATA_LEADER_IDS = {
    "Q22686": {"name": "Donald Trump", "country": "United States", "default_role": "President of the United States"},
    "Q380711": {"name": "JD Vance", "country": "United States", "default_role": "Vice President of the United States"},
    "Q30873": {"name": "Benjamin Netanyahu", "country": "Israel", "default_role": "Prime Minister of Israel"},
    "Q57689": {"name": "Isaac Herzog", "country": "Israel", "default_role": "President of Israel"},
    "Q1488": {"name": "Ali Khamenei", "country": "Iran", "default_role": "Supreme Leader of Iran"},
    "Q371160": {"name": "Masoud Pezeshkian", "country": "Iran", "default_role": "President of Iran"},
    "Q7747": {"name": "Vladimir Putin", "country": "Russia", "default_role": "President of Russia"},
    "Q57689_UA": {"name": "Volodymyr Zelenskyy", "id": "Q57689", "country": "Ukraine", "default_role": "President of Ukraine"},
    "Q15031": {"name": "Xi Jinping", "country": "China", "default_role": "President of China"},
    "Q964": {"name": "Keir Starmer", "country": "United Kingdom", "default_role": "Prime Minister of the United Kingdom"},
    "Q305277": {"name": "Emmanuel Macron", "country": "France", "default_role": "President of France"},
    "Q15039": {"name": "Olaf Scholz", "country": "Germany", "default_role": "Chancellor of Germany"},
}


def get_default_world_theater_data() -> Dict[str, Any]:
    """
    Constructs the authoritative baseline World Theater context.
    Provides immediate operational grounding even before external API syncs complete.
    """
    now = datetime.now(timezone.utc)
    current_year = now.year

    return {
        "temporal_anchor": {
            "current_year": current_year,
            "current_date_utc": now.strftime("%Y-%m-%d"),
            "iso_timestamp": now.isoformat(),
        },
        "global_leadership": {
            "united_states": {
                "head_of_state": "Donald J. Trump",
                "title": "47th President of the United States (in office since Jan 20, 2025)",
                "vice_president": "JD Vance",
                "secretary_of_state": "Marco Rubio",
                "secretary_of_defense": "Pete Hegseth",
                "administration_stance": "Decisive deterrence, sovereign alignment with key allies, direct pressure on regional adversaries",
            },
            "israel": {
                "head_of_government": "Benjamin Netanyahu",
                "title": "Prime Minister of Israel",
                "head_of_state": "Isaac Herzog (President)",
                "defense": "Israel Defense Forces (IDF)",
                "operational_focus": "Multi-front defensive posture, pre-emptive strikes on proxy infrastructure, upcoming electoral and strategic alignments",
            },
            "iran": {
                "supreme_leader": "Ayatollah Ali Khamenei",
                "president": "Masoud Pezeshkian",
                "title": "President of the Islamic Republic of Iran",
                "military": "IRGC (Islamic Revolutionary Guard Corps) and Artesh",
                "operational_focus": "Coordination of the Axis of Resistance, ballistic missile deterrence, enrichment posture",
            },
            "russia": {
                "head_of_state": "Vladimir Putin",
                "title": "President of the Russian Federation",
                "operational_focus": "Military operations in Ukraine, strategic cooperation with Iran and North Korea",
            },
            "ukraine": {
                "head_of_state": "Volodymyr Zelenskyy",
                "title": "President of Ukraine",
                "operational_focus": "Frontline defense, deep drone strikes on Russian logistics, air defense coordination with NATO",
            },
            "china": {
                "head_of_state": "Xi Jinping",
                "title": "President of the People's Republic of China",
                "operational_focus": "Taiwan Strait military drills, South China Sea territorial enforcement, economic diplomacy",
            },
            "united_kingdom": {
                "head_of_government": "Keir Starmer",
                "title": "Prime Minister of the United Kingdom",
            },
            "france": {
                "head_of_state": "Emmanuel Macron",
                "title": "President of the French Republic",
            },
            "germany": {
                "head_of_government": "Olaf Scholz",
                "title": "Chancellor of Germany",
            },
            "regional_non_state_actors": {
                "houthis_ansar_allah": "Abdul-Malik al-Houthi (Supreme Leader), Mahdi al-Mashat (SPC President), Brig. Gen. Yahya Saree (Military Spokesperson)",
                "hezbollah": "Lebanese resistance movement, southern Lebanon / Litani sector operational posture",
                "iraqi_resistance": "Kata'ib Hezbollah, Harakat al-Nujaba, Islamic Resistance in Iraq",
                "syria": "Bashar al-Assad (President)",
            },
        },
        "active_operational_theaters": [
            {
                "theater": "Middle East & Levant",
                "scope": "Direct Israel-Iran military confrontation, Lebanon-Israel border kinetic clashes, Gaza operations, Syrian border monitoring, Israeli domestic election dynamics.",
                "status": "High Alert / Kinetic & Diplomatic Escalation",
            },
            {
                "theater": "Red Sea & Maritime Chokepoints",
                "scope": "Strait of Hormuz, Bab el-Mandeb, Gulf of Aden. Houthi ballistic missile and UAV interdiction against commercial shipping, US/coalition naval escort and strike operations.",
                "status": "Active Maritime Interdiction",
            },
            {
                "theater": "Eastern Europe",
                "scope": "Russia-Ukraine war, Black Sea maritime strikes, long-range missile and drone barrages against infrastructure, NATO eastern flank positioning.",
                "status": "Active Conventional Warfare",
            },
            {
                "theater": "Indo-Pacific",
                "scope": "Taiwan Strait military sorties, South China Sea freedom of navigation, North Korean ballistic missile and nuclear tests.",
                "status": "Strategic Deterrence & Power Projection",
            },
        ],
        "geopolitical_rules": [
            f"All temporal references, statements, and military/diplomatic developments MUST be analyzed under the current operational year ({current_year}).",
            "Donald Trump is the incumbent 47th President of the United States (in office since Jan 20, 2025). NEVER refer to him as 'former president' or 'former U.S. president'.",
            "Accurately reflect current sitting officeholders and administrations across all nations (e.g. Benjamin Netanyahu is Prime Minister of Israel; Masoud Pezeshkian is President of Iran; Vladimir Putin is President of Russia).",
            "Statements, warnings, and endorsements by sitting heads of state carry sovereign state authority and military implications, not private commentary.",
        ],
        "last_synced_at": now.isoformat(),
        "sync_source": "authoritative_baseline",
        "live_api_status": "initialized",
    }


def sanitize_world_theater_text(text: Optional[str]) -> str:
    """
    Defensive text sanitizer that corrects outdated leadership labels and temporal biases
    in generated headlines, summaries, and notifications.
    """
    if not text:
        return ""

    s = text

    # Correct 'former president donald trump' -> 'President Donald Trump' / 'U.S. President Donald Trump'
    s = re.sub(
        r"\bformer\s+(?:(?:u\.?s\.?|us|united\s+states)\s+)?president\s+donald\s+j\.?\s+trump\b",
        "President Donald J. Trump",
        s,
        flags=re.IGNORECASE,
    )
    s = re.sub(
        r"\bformer\s+(?:(?:u\.?s\.?|us|united\s+states)\s+)?president\s+donald\s+trump\b",
        "President Donald Trump",
        s,
        flags=re.IGNORECASE,
    )
    s = re.sub(
        r"\bformer\s+(?:(?:u\.?s\.?|us|united\s+states)\s+)?president\s+trump\b",
        "President Trump",
        s,
        flags=re.IGNORECASE,
    )
    s = re.sub(
        r"\bformer\s+president\s+trump\b",
        "President Trump",
        s,
        flags=re.IGNORECASE,
    )

    # Correct 'former prime minister netanyahu' (when referring to current events)
    s = re.sub(
        r"\bformer\s+(?:israeli\s+)?prime\s+minister\s+benjamin\s+netanyahu\b",
        "Prime Minister Benjamin Netanyahu",
        s,
        flags=re.IGNORECASE,
    )
    s = re.sub(
        r"\bformer\s+(?:israeli\s+)?prime\s+minister\s+netanyahu\b",
        "Prime Minister Netanyahu",
        s,
        flags=re.IGNORECASE,
    )

    # Correct 'former president pezeshkian'
    s = re.sub(
        r"\bformer\s+(?:iranian\s+)?president\s+masoud\s+pezeshkian\b",
        "President Masoud Pezeshkian",
        s,
        flags=re.IGNORECASE,
    )
    s = re.sub(
        r"\bformer\s+(?:iranian\s+)?president\s+pezeshkian\b",
        "President Pezeshkian",
        s,
        flags=re.IGNORECASE,
    )

    return s


class WorldTheaterService:
    """
    World Theater Service providing continuous synchronization, in-memory caching,
    database persistence, dynamic prompt injection, and defensive guardrails.
    """

    def __init__(self, session_factory: Optional[Any] = None, redis_bus: Optional[Any] = None):
        self._session_factory = session_factory
        self._redis_bus = redis_bus
        self._context_data: Dict[str, Any] = get_default_world_theater_data()
        self._lock = asyncio.Lock()
        self._sync_task: Optional[asyncio.Task] = None
        self._is_running: bool = False
        self._http_client: Optional[httpx.AsyncClient] = None
        self._sync_interval_seconds: float = 14400.0  # 4 hours
        self._last_sync_attempt: Optional[float] = None

    @property
    def http_client(self) -> httpx.AsyncClient:
        if self._http_client is None or self._http_client.is_closed:
            self._http_client = httpx.AsyncClient(
                timeout=10.0,
                headers={"User-Agent": "RealtimeOSINT/1.0 (Defense Intel Knowledge Engine)"},
            )
        return self._http_client

    def get_context(self) -> Dict[str, Any]:
        """Return the current in-memory World Theater context."""
        now = datetime.now(timezone.utc)
        # Ensure temporal anchor is always strictly up-to-date with current execution time
        if "temporal_anchor" in self._context_data:
            self._context_data["temporal_anchor"]["current_year"] = now.year
            self._context_data["temporal_anchor"]["current_date_utc"] = now.strftime("%Y-%m-%d")
            self._context_data["temporal_anchor"]["iso_timestamp"] = now.isoformat()
        return self._context_data

    def set_context(self, data: Dict[str, Any]) -> None:
        """Update in-memory context data."""
        self._context_data = data

    async def load_from_db(self) -> bool:
        """Load saved world theater context from SystemSettings if present."""
        if self._session_factory is None:
            from backend.app.db.session import get_session_factory
            self._session_factory = get_session_factory()

        try:
            from backend.app.db.models import SystemSettings
            async with self._session_factory() as session:
                res = await session.execute(
                    select(SystemSettings).where(SystemSettings.key == SETTINGS_KEY_WORLD_THEATER)
                )
                setting = res.scalars().first()
                if setting and isinstance(setting.value_json, dict) and setting.value_json:
                    merged = get_default_world_theater_data()
                    merged.update(setting.value_json)
                    self._context_data = merged
                    logger.info("Loaded World Theater context from database.")
                    return True
        except Exception as exc:
            logger.debug(f"Could not load World Theater context from DB: {exc}")
        return False

    async def persist_to_db(self) -> None:
        """Persist current World Theater context to SystemSettings table."""
        if self._session_factory is None:
            from backend.app.db.session import get_session_factory
            self._session_factory = get_session_factory()

        try:
            from backend.app.db.models import SystemSettings
            async with self._session_factory() as session:
                res = await session.execute(
                    select(SystemSettings).where(SystemSettings.key == SETTINGS_KEY_WORLD_THEATER)
                )
                existing = res.scalars().first()
                if existing:
                    existing.value_json = self._context_data
                    flag_modified(existing, "value_json")
                else:
                    session.add(
                        SystemSettings(
                            key=SETTINGS_KEY_WORLD_THEATER,
                            value_json=self._context_data,
                        )
                    )
                await session.commit()
                logger.info("Persisted updated World Theater context to DB.")
        except Exception as exc:
            logger.warning(f"Failed to persist World Theater context to DB: {exc}")

    async def sync_from_live_apis(self, timeout: float = 8.0) -> Dict[str, Any]:
        """
        Actively query Wikidata REST Entity API to pull live official officeholder titles
        and descriptions for key global leaders.
        """
        async with self._lock:
            now = datetime.now(timezone.utc)
            self._last_sync_attempt = asyncio.get_event_loop().time()
            query_ids = [
                "Q22686",   # Donald Trump
                "Q380711",  # JD Vance
                "Q30873",   # Benjamin Netanyahu
                "Q57689",   # Isaac Herzog
                "Q1488",    # Ali Khamenei
                "Q371160",  # Masoud Pezeshkian
                "Q7747",    # Vladimir Putin
                "Q964",     # Keir Starmer
                "Q305277",  # Emmanuel Macron
                "Q15039",   # Olaf Scholz
            ]

            url = (
                f"{WIKIDATA_ENTITIES_URL}?action=wbgetentities"
                f"&ids={'|'.join(query_ids)}&props=labels|descriptions&languages=en&format=json"
            )

            updated_leaders = dict(self._context_data.get("global_leadership", {}))
            api_success = False

            try:
                resp = await self.http_client.get(url, timeout=timeout)
                if resp.status_code == 200:
                    data = resp.json()
                    entities = data.get("entities", {})

                    # Parse Donald Trump
                    if "Q22686" in entities:
                        desc = entities["Q22686"].get("descriptions", {}).get("en", {}).get("value", "")
                        if desc and "united states" in desc.lower():
                            updated_leaders.setdefault("united_states", {})["title"] = f"Donald J. Trump ({desc})"
                            api_success = True

                    # Parse JD Vance
                    if "Q380711" in entities:
                        desc = entities["Q380711"].get("descriptions", {}).get("en", {}).get("value", "")
                        if desc:
                            updated_leaders.setdefault("united_states", {})["vice_president"] = f"JD Vance ({desc})"

                    # Parse Benjamin Netanyahu
                    if "Q30873" in entities:
                        desc = entities["Q30873"].get("descriptions", {}).get("en", {}).get("value", "")
                        if desc:
                            updated_leaders.setdefault("israel", {})["title"] = f"Benjamin Netanyahu ({desc})"
                            api_success = True

                    # Parse Masoud Pezeshkian
                    if "Q371160" in entities:
                        desc = entities["Q371160"].get("descriptions", {}).get("en", {}).get("value", "")
                        if desc:
                            updated_leaders.setdefault("iran", {})["title"] = f"Masoud Pezeshkian ({desc})"
                            api_success = True

                    # Parse Vladimir Putin
                    if "Q7747" in entities:
                        desc = entities["Q7747"].get("descriptions", {}).get("en", {}).get("value", "")
                        if desc:
                            updated_leaders.setdefault("russia", {})["title"] = f"Vladimir Putin ({desc})"

                    # Parse Keir Starmer
                    if "Q964" in entities:
                        desc = entities["Q964"].get("descriptions", {}).get("en", {}).get("value", "")
                        if desc:
                            updated_leaders.setdefault("united_kingdom", {})["title"] = f"Keir Starmer ({desc})"

                    # Parse Emmanuel Macron
                    if "Q305277" in entities:
                        desc = entities["Q305277"].get("descriptions", {}).get("en", {}).get("value", "")
                        if desc:
                            updated_leaders.setdefault("france", {})["title"] = f"Emmanuel Macron ({desc})"

                    # Parse Olaf Scholz
                    if "Q15039" in entities:
                        desc = entities["Q15039"].get("descriptions", {}).get("en", {}).get("value", "")
                        if desc:
                            updated_leaders.setdefault("germany", {})["title"] = f"Olaf Scholz ({desc})"

            except Exception as net_err:
                logger.warning(f"Wikidata live sync request warning: {net_err}. Retaining authoritative baseline.")

            # Update context metadata
            self._context_data["global_leadership"] = updated_leaders
            self._context_data["temporal_anchor"] = {
                "current_year": now.year,
                "current_date_utc": now.strftime("%Y-%m-%d"),
                "iso_timestamp": now.isoformat(),
            }
            self._context_data["last_synced_at"] = now.isoformat()
            self._context_data["sync_source"] = "wikidata_live_api" if api_success else "authoritative_baseline"
            self._context_data["live_api_status"] = "synced" if api_success else "fallback_active"

            # Persist to database
            await self.persist_to_db()

            # Broadcast reload to Redis if bus is available
            if self._redis_bus is not None:
                try:
                    from backend.app.services.redis_bus import CHANNEL_CONFIG_RELOAD
                    await self._redis_bus.publish(
                        CHANNEL_CONFIG_RELOAD,
                        {"type": "world_theater_updated", "timestamp": now.isoformat()},
                    )
                except Exception as b_err:
                    logger.debug(f"Could not publish world theater reload to Redis: {b_err}")

            logger.info(
                f"World Theater context synchronized. Status: {self._context_data['live_api_status']} "
                f"(Source: {self._context_data['sync_source']})"
            )
            return self._context_data

    async def start_continuous_sync(self, interval_seconds: float = 14400.0) -> None:
        """Start the background task that periodically resyncs with live APIs."""
        if self._is_running:
            return
        self._is_running = True
        self._sync_interval_seconds = interval_seconds

        # 1. Initial attempt to load from DB
        await self.load_from_db()

        # 2. Run initial live sync immediately in background
        asyncio.create_task(self.sync_from_live_apis())

        # 3. Start background periodic sync loop
        self._sync_task = asyncio.create_task(self._periodic_sync_loop())
        logger.info(f"World Theater continuous background sync started (interval: {interval_seconds}s).")

    async def stop(self) -> None:
        """Stop background sync loop and close HTTP client."""
        self._is_running = False
        if self._sync_task and not self._sync_task.done():
            self._sync_task.cancel()
            self._sync_task = None
        if self._http_client and not self._http_client.is_closed:
            await self._http_client.aclose()
            self._http_client = None
        logger.info("World Theater Service stopped.")

    async def _periodic_sync_loop(self) -> None:
        """Background loop executing live API synchronization periodically."""
        while self._is_running:
            try:
                await asyncio.sleep(self._sync_interval_seconds)
                if not self._is_running:
                    break
                await self.sync_from_live_apis()
            except asyncio.CancelledError:
                break
            except Exception as exc:
                logger.warning(f"Error in World Theater periodic sync: {exc}")
                await asyncio.sleep(60.0)

    def get_prompt_block(self, stage: str = "general") -> str:
        """
        Generates a compact, high-density, authoritative prompt block
        tailored for injection into AI system prompts and workflow contexts.
        """
        ctx = self.get_context()
        anchor = ctx.get("temporal_anchor", {})
        curr_year = anchor.get("current_year", datetime.now(timezone.utc).year)
        curr_date = anchor.get("current_date_utc", datetime.now(timezone.utc).strftime("%Y-%m-%d"))

        lead = ctx.get("global_leadership", {})
        us = lead.get("united_states", {})
        isr = lead.get("israel", {})
        irn = lead.get("iran", {})
        rus = lead.get("russia", {})
        ukr = lead.get("ukraine", {})
        chn = lead.get("china", {})
        prox = lead.get("regional_non_state_actors", {})

        us_head = us.get("head_of_state", "Donald J. Trump")
        us_title = us.get("title", "47th President of the United States (in office since Jan 20, 2025)")
        isr_pm = isr.get("head_of_government", "Benjamin Netanyahu")
        irn_sl = irn.get("supreme_leader", "Ayatollah Ali Khamenei")
        irn_pres = irn.get("president", "Masoud Pezeshkian")
        rus_pres = rus.get("head_of_state", "Vladimir Putin")
        ukr_pres = ukr.get("head_of_state", "Volodymyr Zelenskyy")
        chn_pres = chn.get("head_of_state", "Xi Jinping")

        houthis = prox.get("houthis_ansar_allah", "Abdul-Malik al-Houthi, Yahya Saree")

        return (
            f"CURRENT WORLD THEATER & GEOPOLITICAL CONTEXT (OPERATIONAL AS OF {curr_date}):\n"
            f"- Temporal Anchor: Today is {curr_date} (Year {curr_year}). All analysis MUST reflect current reality.\n"
            f"- Incumbent World Leaders & Sovereign Authority:\n"
            f"  * United States: {us_head} is the sitting {us_title} (VP: JD Vance). NEVER refer to {us_head} as 'former president'.\n"
            f"  * Israel: {isr_pm} is the Prime Minister of Israel; Isaac Herzog is President.\n"
            f"  * Iran: {irn_sl} is Supreme Leader; {irn_pres} is the sitting President of Iran.\n"
            f"  * Russia: {rus_pres} is President. Ukraine: {ukr_pres} is President.\n"
            f"  * China: {chn_pres} is President.\n"
            f"  * Regional Proxies/Actors: Houthis/Ansar Allah ({houthis}), Hezbollah, Iraqi Paramilitaries.\n"
            f"- Active Theaters & Flashpoints:\n"
            f"  * Middle East: Israel-Iran kinetic friction, Red Sea/Bab el-Mandeb Houthi maritime shipping strikes, Lebanon border operations, Israeli domestic election alignment.\n"
            f"  * Eastern Europe: Russia-Ukraine frontline operations and deep strategic strikes.\n"
            f"  * Indo-Pacific: Taiwan Strait and Korean Peninsula deterrence.\n"
            f"- Mandatory Intelligence Rule: All titles, leadership roles, and sovereign actions MUST accurately reflect this live world context."
        )


_global_world_theater_service: Optional[WorldTheaterService] = None


def get_world_theater_service(
    session_factory: Optional[Any] = None,
    redis_bus: Optional[Any] = None,
) -> WorldTheaterService:
    """Return the singleton instance of WorldTheaterService."""
    global _global_world_theater_service
    if _global_world_theater_service is None:
        _global_world_theater_service = WorldTheaterService(
            session_factory=session_factory,
            redis_bus=redis_bus,
        )
    else:
        if session_factory is not None and _global_world_theater_service._session_factory is None:
            _global_world_theater_service._session_factory = session_factory
        if redis_bus is not None and _global_world_theater_service._redis_bus is None:
            _global_world_theater_service._redis_bus = redis_bus
    return _global_world_theater_service
