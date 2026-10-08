"""
backend/app/pipeline/stage1_extract.py - Stage 1: Extraction & Translation
"""

import logging
import re
from typing import Any, Dict, List, Optional, Union

from backend.app.pipeline.models import Stage1Result
from backend.app.pipeline.parser import extract_and_parse_json
from backend.app.pipeline.providers.base import BaseAIProvider
from backend.app.pipeline.arabic_normalizer import normalize_arabic_text, classify_kinetic_signal

logger = logging.getLogger("osint.pipeline.stage1")

STAGE1_SYSTEM_PROMPT = """You are a senior multilingual Open Source Intelligence (OSINT) signals and parsing analyst.
Your mission is to extract structured intelligence entities, detect source language, and produce accurate English translations.

Guidelines:
1. Language Detection: Detect the primary language (ISO 639-1 code: "fa" for Persian/Farsi, "ar" for Arabic, "he" for Hebrew, "en" for English, or other standard code).
2. Translation:
   - If the text is in Persian/Farsi, Arabic, Hebrew, or another non-English language, translate it into fluent, objective English intelligence text.
   - Begin the translated text with "English translation: " followed by the fluent translation.
   - Accurately translate tactical military terms: "مسيرة" -> drone/UAV, "رشقة صاروخية" -> rocket barrage, "صفارات الإنذار" -> air raid sirens, "استهداف" -> targeting/strike, "اعتراض" -> air defense interception, "צבע אדوم" -> Red Alert siren.
   - Cleanly handle Persian Zero-Width Non-Joiners (\u200c), Persian numerals, Arabic diacritics, and alert emojis.
   - Strictly preserve all defense and geopolitical acronyms (e.g., IRGC, CENTCOM, IAEA, UNIFIL, IDF, NATO).
   - If the source is already English, provide the clean text without adding "English translation: ".
3. Entity Extraction:
   - Identify named entities across 5 intelligence dimensions:
     * People: Political and military leaders, commanders, spokespersons.
     * Organizations: Military branches, militant proxies, government bodies, international agencies.
     * Locations: Strategic waterways, straits, military facilities, cities, provinces.
     * Military Assets: Specific weapon systems, vessel classes, missile types, drones, nuclear sites.
     * Geopolitical Events: Tactical actions, strikes, intercepts, sanctions, diplomatic meetings.
   - Return a flat deduplicated list in `extracted_entities` and grouped lists in `entity_breakdown`.
4. Key Facts: Extract 2-5 concise, atomic bullet points summarizing the core factual claims.

You MUST respond strictly with a valid JSON object matching the requested schema. No markdown backticks, no preamble."""


def _heuristic_stage1_fallback(raw_text: str, title: str = "") -> Stage1Result:
    """
    Deterministic offline heuristic fallback for Stage 1.
    Handles Persian/Arabic/Hebrew detection, ZWNJ stripping, number normalization,
    and defense acronym preservation.
    """
    combined = f"{title}\n{raw_text}".strip()
    
    # Check for Arabic / Persian / Hebrew Unicode blocks
    arabic_chars = len(re.findall(r"[\u0600-\u06FF\u0750-\u077F\uFB50-\uFDFF\uFE70-\uFEFF]", combined))
    has_arabic_script = arabic_chars > 0
    hebrew_chars = len(re.findall(r"[\u0590-\u05FF]", combined))
    is_persian = has_arabic_script and bool(re.search(r"[پچگژ\u200c]|ایران|سپاه|تهران|خلیج", combined))

    # Extract defense acronyms
    acronym_pattern = r"\b(IRGC|CENTCOM|IAEA|IDF|NATO|UNIFIL|UAV|IRNA|IRIB)\b"
    acronyms = list(set(re.findall(acronym_pattern, combined, re.IGNORECASE)))
    acronyms = [a.upper() for a in acronyms]

    # Additional standard entities
    common_entities = ["Iran", "Israel", "United States", "US Navy", "Strait of Hormuz", "Persian Gulf", "Houthis", "Hezbollah", "Red Sea", "Bab el-Mandeb"]
    matched_entities = [e for e in common_entities if e.lower() in combined.lower()]
    all_entities = list(dict.fromkeys(acronyms + matched_entities))

    # Resolve common Arabic phrases to English entities
    arabic_entity_map = {
        "حزب الله": "Hezbollah",
        "أنصار الله": "Houthis",
        "الحرس الثوري": "IRGC",
        "البحر الأحمر": "Red Sea",
        "مضيق هرمز": "Strait of Hormuz",
        "باب المندب": "Bab el-Mandeb",
        "إسرائيل": "Israel",
        "لبنان": "Lebanon",
        "اليمن": "Yemen",
        "غزة": "Gaza",
    }
    for ar_term, en_ent in arabic_entity_map.items():
        if ar_term in combined and en_ent not in all_entities:
            all_entities.append(en_ent)

    if hebrew_chars > arabic_chars and hebrew_chars > 0:
        lang = "he"
        cleaned = re.sub(r"[🚨⚠️🔴⚡📢]", "", combined).strip()
        entity_str = ", ".join(all_entities) if all_entities else "Israel and regional sector"
        translated = (
            f"English translation: Real-time tactical alert regarding sirens or defense activity. "
            f"Entities involved include {entity_str}. Threat telemetry active."
        )
        if not all_entities:
            all_entities = ["Israel", "IDF"]
    elif arabic_chars > 0:
        lang = "fa" if is_persian else "ar"
        # Clean ZWNJ and Harakat
        cleaned = combined.replace("\u200c", " ")
        cleaned = re.sub(r"[\u064B-\u065F\u0670]", "", cleaned)  # Harakat
        # Convert Persian numerals
        persian_nums = str.maketrans("۰۱۲۳۴۵۶۷۸۹", "0123456789")
        cleaned = cleaned.translate(persian_nums)
        # Strip emojis
        cleaned = re.sub(r"[🚨⚠️🔴⚡📢]", "", cleaned).strip()

        entity_str = ", ".join(all_entities) if all_entities else "regional actors"
        translated = (
            f"English translation: Intelligence report regarding military and defense developments. "
            f"Entities involved include {entity_str}. Operational monitoring remains active."
        )
        if not all_entities:
            all_entities = ["Iran", "IRGC", "Strait of Hormuz"]
    else:
        lang = "en"
        translated = combined
        if not all_entities:
            caps = re.findall(r"\b[A-Z][a-z]+(?:\s+[A-Z][a-z]+)*\b", combined)
            all_entities = list(dict.fromkeys([c for c in caps if len(c) > 3][:5])) or ["Regional Command"]

    extra_facts = [
        "Intelligence monitoring event logged.",
        "Strategic forces and regional assets tracked.",
    ]
    if has_arabic_script and not is_persian:
        sig = classify_kinetic_signal(combined)
        if sig["is_kinetic"]:
            extra_facts.insert(0, f"Frontline kinetic indicator: {', '.join(sig['matched_kinetic_triggers'][:3])}.")
        elif sig["is_acoustic"]:
            extra_facts.insert(0, f"Acoustic observation detected (unverified anomaly): {', '.join(sig['matched_acoustic_triggers'][:3])}.")

    return Stage1Result(
        extracted_entities=all_entities,
        detected_language=lang,
        translated_text=translated,
        key_facts=extra_facts,
        entity_breakdown={
            "organizations": [e for e in all_entities if e in ("IRGC", "CENTCOM", "IAEA", "IDF", "NATO", "Houthis", "Hezbollah")],
            "locations": [e for e in all_entities if e in ("Strait of Hormuz", "Persian Gulf", "Iran", "Israel", "United States", "Red Sea", "Bab el-Mandeb", "Lebanon", "Yemen", "Gaza")],
            "people": [],
            "military_assets": [e for e in all_entities if "UAV" in e or "Navy" in e],
            "geopolitical_events": []
        }
    )


async def run_stage1_extract(
    event: Any,
    provider: BaseAIProvider,
    model: Optional[str] = None,
    temperature: Optional[float] = None,
) -> Stage1Result:
    """
    Executes Stage 1 extraction & translation using the configured AI provider.
    """
    raw_text = getattr(event, "raw_text", None) or (event.get("raw_text") if isinstance(event, dict) else "")
    title = getattr(event, "title", None) or (event.get("title") if isinstance(event, dict) else "")
    source_name = getattr(event, "source_name", None) or (event.get("source_name") if isinstance(event, dict) else "Unknown")
    source_type = getattr(event, "source_type", None) or (event.get("source_type") if isinstance(event, dict) else "rss")
    lang_hint = getattr(event, "language", None) or (event.get("language") if isinstance(event, dict) else "en")

    prompt = (
        f"Source Name: {source_name}\n"
        f"Source Type: {source_type}\n"
        f"Title: {title}\n"
        f"Reported Language Hint: {lang_hint}\n"
        f"Raw Content:\n{raw_text}\n\n"
        f"Provide valid JSON matching the Stage1Result schema."
    )

    def fallback():
        return _heuristic_stage1_fallback(raw_text, title)

    try:
        raw_response = await provider.generate_text(
            prompt=prompt,
            system_prompt=STAGE1_SYSTEM_PROMPT,
            model=model,
            temperature=temperature if temperature is not None else 0.2,
            json_mode=True,
        )
        result = extract_and_parse_json(
            raw_text=raw_response,
            target_model=Stage1Result,
            fallback_factory=fallback,
        )
        return result
    except PermissionError:
        raise
    except RuntimeError as r_err:
        if "429" in str(r_err):
            raise
        logger.warning(f"RuntimeError in Stage 1, falling back to heuristics: {r_err}")
        return fallback()
    except Exception as exc:
        logger.warning(f"Stage 1 execution exception, using fallback: {exc}")
        return fallback()
