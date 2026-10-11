"""
backend/app/pipeline/arabic_normalizer.py

Algorithmic text normalization, linguistic signal triangulation,
and Jaccard/MinHash echo deduplication for frontline Arabic and regional OSINT feeds.
Based on tactical OSINT signal architecture recommendations.
"""

import re
import time
import unicodedata
from collections import deque
from typing import Any, Deque, Dict, List, Optional, Set, Tuple


# Regex for Tashkeel (Arabic diacritics / short vowel marks: Fathah, Dammah, Kasrah, Sukun, Shaddah, Tanwin)
# and Hebrew Niqqud (\u0591-\u05C7)
TASHKEEL_REGEX = re.compile(r"[\u064B-\u065F\u0670\u0591-\u05C7]")

# Tatweel / Kashida elongation mark
TATWEEL_CHAR = "\u0640"

# Persian and Arabic-Indic numerals translation table
NUMERAL_TRANSLATION = str.maketrans({
    "۰": "0", "۱": "1", "۲": "2", "۳": "3", "۴": "4",
    "۵": "5", "۶": "6", "۷": "7", "۸": "8", "۹": "9",
    "٠": "0", "١": "1", "٢": "2", "٣": "3", "٤": "4",
    "٥": "5", "٦": "6", "٧": "7", "٨": "8", "٩": "9",
})

# --- Ground-Truth Vernacular Keyword Matrix (Arabic, Farsi, Hebrew, English) ---

# 1. Explosion / Detonation (Audible / Visceral Ground Reports)
EXPLOSION_VERNACULAR: List[str] = [
    "انفجار قوي",
    "صوت قوي يهز",
    "دبت ضربه",
    "دوي انفجار",
    "دوي انفجارات",
    "سماع دوي",
    "سماع صوت",
    "اصوات انفجارات",
    "انفجار مجهول",
    "صدای مهیب",
    "صداي مهيب",
    "صدای انفجار مهیب اومد",
    "صداي انفجار مهيب اومد",
    "صدای انفجار",
    "صداي انفجار",
    "פיצוץ מטורף",
    "בום חזק מאוד",
    "בום חזק",
    "loud boom",
    "huge explosion heard",
    "massive blast heard",
]

# 2. Sonic Boom / Jets (De-escalation Indicators when no smoke/impact present)
SONIC_BOOM_TRIGGERS: List[str] = [
    "خرق جدار الصوت",
    "جدار صوت",
    "جدار الصوت",
    "شکستن دیوار صوتی",
    "شكستن ديوار صوتي",
    "دیوار صوتی",
    "ديوار صوتي",
    "בום על-קולי",
    "בום על קולי",
    "sonic boom",
    "sound barrier",
]

# 3. Military Aircraft Activity
AIRCRAFT_VERNACULAR: List[str] = [
    "طيران حربي منخفض",
    "طيران استطلاع",
    "تحليق مكثف",
    "طيران حربي في الاجواء",
    "جنگنده",
    "جنگنده",
    "هواپیمای جنگی ارتفاع پایین",
    "هواپيماي جنگي ارتفاع پايين",
    "מטוסי קרב בגובה נמוך",
    "מטוסי קרב",
    "low flying fighter",
    "jets overhead",
]

# 4. Drones / Loitering Munitions
DRONE_VERNACULAR: List[str] = [
    "مسيره",
    "مسيره انقضاضيه",
    "ام كا",
    "ام كه",
    "طنين قوي بالسما",
    "پهپاد انتحاری",
    "پهپاد انتحاري",
    "صدای موتور گازی",
    "صداي موتور گازي",
    "پهپاد",
    "כטב\"ם מתאבד",
    "כטבם מתאבד",
    "כטב\"ם",
    "זמזום מעל הראש",
    "loitering munition",
    "kamikaze drone",
    "shahed overhead",
]

# 5. Air Defense / Interceptions
AIR_DEFENSE_VERNACULAR: List[str] = [
    "صواريخ اعتراضيه",
    "تصدي الدفاعات الجويه",
    "اعتراض",
    "شلیک پدافند",
    "شليك پدافند",
    "پدافند هوایی",
    "پدافند هوايي",
    "موشک رهگیر",
    "موشك رهگير",
    "יירוט מוצלח",
    "שיגור מיירט",
    "יירוט",
    "air defense active",
    "iron dome intercept",
    "sam launch",
]

# 6. Sirens / Civil Alarms
SIREN_VERNACULAR: List[str] = [
    "صفارات الانذار تدوي",
    "صافرات الانذار",
    "صفارات الانذار",
    "آژیر خطر",
    "اژير خطر",
    "آژیر قرمز",
    "צבע אדום",
    "אזעקה עולה ויורדת",
    "אזעקות",
    "red alert siren",
    "air raid siren",
]

# 7. Smoke / Impact Evidence (High-Confidence Kinetic Confirmation)
SMOKE_IMPACT_TRIGGERS: List[str] = [
    "عمود دخان اسود",
    "سحابه دخان كثيفه",
    "تصاعد اعمده الدخان",
    "اندلاع حريق",
    "اصابه مباشره",
    "ستون دود غلیظ",
    "ستون دود غليظ",
    "ستون دود",
    "פטריית עשן",
    "נפילה ישירה",
    "עמוד עשן",
    "plume of black smoke",
    "thick smoke column",
    "direct hit confirmed",
]

# Combined Acoustic Triggers (backward-compatible superset)
ACOUSTIC_TRIGGERS: List[str] = list(dict.fromkeys([
    "دوي انفجار",
    "دوي انفجارات",
    "سماع دوي",
    "سماع صوت",
    "اصوات انفجارات",
    "صافرات الانذار",
    "صفارات الانذار",
    "صفارات الانذار تدوي",
    "تحليق مكثف",
    "طيران حربي في الاجواء",
    "طيران حربي منخفض",
    "طيران استطلاع",
    "جدار صوت",
    "خرق جدار الصوت",
    "انفجار مجهول",
    "انفجار قوي",
    "صوت قوي يهز",
    "طنين قوي بالسما",
    "صدای مهیب",
    "صداي مهيب",
    "صدای انفجار مهیب اومد",
    "صداي انفجار مهيب اومد",
    "شکستن دیوار صوتی",
    "شكستن ديوار صوتي",
    "صدای موتور گازی",
    "صداي موتور گازي",
    "آژیر خطر",
    "اژير خطر",
    "פיצוץ מטורף",
    "בום חזק מאוד",
    "בום על-קולי",
    "בום על קולי",
    "מטוסי קרב בגובה נמוך",
    "זמזום מעל הראש",
    "צבע אדום",
    "אזעקה עולה ויורדת",
]))

# Combined Kinetic Impact Triggers (backward-compatible superset)
KINETIC_TRIGGERS: List[str] = list(dict.fromkeys([
    "استهداف",
    "غاره",
    "غارات",
    "قصف",
    "سقوط صاروخ",
    "اعتراض",
    "صواريخ اعتراضيه",
    "تصدي الدفاعات الجويه",
    "مسيره انقضاضيه",
    "صاروخ باليستي",
    "اصابه مباشره",
    "اندلاع حريق",
    "تصاعد اعمده الدخان",
    "عمود دخان اسود",
    "سحابه دخان كثيفه",
    "دبت ضربه",
    "اشتباكات بالاسلحه",
    "استشهد",
    "شهداء وجرحى",
    "اضرار ماديه",
    "پهپاد انتحاری",
    "پهپاد انتحاري",
    "شلیک پدافند",
    "شليك پدافند",
    "موشک رهگیر",
    "موشك رهگير",
    "ستون دود غلیظ",
    "ستون دود غليظ",
    "כטב\"ם מתאבד",
    "כטבם מתאבד",
    "יירוט מוצלח",
    "שיגור מיירט",
    "פטריית עשן",
    "נפילה ישירה",
]))

# Root tokens monitored for 30-60s Sliding-Window Term Burst Detection
BURST_ROOT_TOKENS: List[str] = [
    "انفجار",
    "صوت قوي",
    "دبت ضربه",
    "غاره",
    "قصف",
    "مسيره",
    "ام كا",
    "صفارات الانذار",
    "عمود دخان",
    "صداي مهيب",
    "صدای مهیب",
    "انفجار مهيب",
    "پدافند",
    "ستون دود",
    "פיצוץ",
    "בום חזק",
    "צבע אדום",
    "יירוט",
    "פטריית עשן",
    "explosion heard",
    "loud boom",
    "airstrike",
    "sirens sounding",
]

ALL_VERNACULAR_TRIGGERS: List[str] = list(
    dict.fromkeys(
        EXPLOSION_VERNACULAR
        + SONIC_BOOM_TRIGGERS
        + AIRCRAFT_VERNACULAR
        + DRONE_VERNACULAR
        + AIR_DEFENSE_VERNACULAR
        + SIREN_VERNACULAR
        + SMOKE_IMPACT_TRIGGERS
        + ACOUSTIC_TRIGGERS
        + KINETIC_TRIGGERS
    )
)


class SlidingWindowBurstDetector:
    """
    Tracks frequency surges of vernacular ground-truth tokens within a sliding time window
    (default 60 seconds). When >= burst_threshold posts match within the window,
    flags an active regional chatter burst to elevate Stage 2 confidence.
    """

    def __init__(self, window_sec: float = 60.0, burst_threshold: int = 3, max_events: int = 500):
        self.window_sec = window_sec
        self.burst_threshold = burst_threshold
        self._events: Deque[Tuple[float, str, List[str]]] = deque(maxlen=max_events)

    def record_and_check(
        self,
        normalized_text: str,
        source_hint: str = "global",
        now_ts: Optional[float] = None,
    ) -> Dict[str, Any]:
        now = now_ts if now_ts is not None else time.monotonic()
        cutoff = now - self.window_sec

        # Evict expired entries outside sliding window
        while self._events and self._events[0][0] < cutoff:
            self._events.popleft()

        text_lower = normalized_text.lower()
        matched_roots = [t for t in BURST_ROOT_TOKENS if t.lower() in text_lower]

        if matched_roots:
            self._events.append((now, source_hint, matched_roots))

        # Count matching events in window and distinct sources
        matching_events = [e for e in self._events if e[2]]
        burst_count = len(matching_events)
        distinct_sources = len({e[1] for e in matching_events if e[1]})
        active_tokens: List[str] = []
        for _, _, toks in matching_events:
            for tk in toks:
                if tk not in active_tokens:
                    active_tokens.append(tk)

        is_burst = burst_count >= self.burst_threshold
        return {
            "term_burst_active": is_burst,
            "burst_count": burst_count,
            "burst_distinct_sources": distinct_sources,
            "burst_tokens": active_tokens[:6],
            "window_sec": self.window_sec,
        }

    def reset(self) -> None:
        self._events.clear()


_global_burst_detector = SlidingWindowBurstDetector(window_sec=60.0, burst_threshold=3)


def get_burst_detector() -> SlidingWindowBurstDetector:
    return _global_burst_detector


def normalize_arabic_text(text: str) -> str:
    """
    Apply linguistic normalization to raw Arabic, Farsi, and Hebrew text:
      1. Strip zero-width non-joiners (ZWNJ) and special formatting.
      2. Strip Tashkeel (short vowels, Tanwin, Shaddah) and Hebrew Niqqud.
      3. Strip Tatweel (Kashida elongation).
      4. Unify Alifs (أ, إ, آ, ٱ -> ا).
      5. Normalize Taa Marbuta (ة -> ه) and Persian Kaf/Yeh (ک -> ك, ی -> ي).
      6. Normalize Alif Maqsura (ى -> ي).
      7. Convert Arabic-Indic and Eastern Persian numerals to standard digits.
      8. Strip sensationalist emojis and collapse redundant whitespace.
    """
    if not text:
        return ""

    # Remove zero-width characters (ZWNJ \u200c, ZWJ \u200d, LTR/RTL marks)
    text = re.sub(r"[\u200B-\u200F\u202A-\u202E\uFEFF]", "", text)

    # Strip Tashkeel and Hebrew Niqqud
    text = TASHKEEL_REGEX.sub("", text)

    # Strip Tatweel
    text = text.replace(TATWEEL_CHAR, "")

    # Unify Alifs: bare, hamzated, and maddah
    text = re.sub(r"[أإآٱ]", "ا", text)

    # Normalize Taa Marbuta to Haa
    text = text.replace("ة", "ه")

    # Normalize Alif Maqsura and Persian Yeh/Kaf for unified matching
    text = text.replace("ى", "ي").replace("ی", "ي").replace("ک", "ك")

    # Translate Eastern numerals to Western digits
    text = text.translate(NUMERAL_TRANSLATION)

    # Strip alert emojis often found in Telegram wires
    text = re.sub(r"[🚨⚠️🔴⚡📢💥🔊🔥⛔]+", " ", text)

    # Normalize whitespace
    text = re.sub(r"\s+", " ", text).strip()

    return text


def has_vernacular_signal(raw_text: str) -> bool:
    """Fast check returning True if the text contains any vernacular OSINT trigger across Ar/Fa/He/En."""
    if not raw_text:
        return False
    norm = normalize_arabic_text(raw_text).lower()
    for trig in ALL_VERNACULAR_TRIGGERS:
        if normalize_arabic_text(trig).lower() in norm:
            return True
    return False


def classify_kinetic_signal(
    raw_text: str,
    source_hint: str = "global",
    record_burst: bool = True,
) -> Dict[str, Any]:
    """
    Perform multilingual linguistic triangulation between:
      - Acoustic observations ('explosions heard', sirens, low-flying jets)
      - Sonic Boom overflights (sonic boom tokens WITHOUT smoke/impact tokens -> de-escalated)
      - Verified kinetic strikes ('direct hit', airstrike, smoke column, intercept, casualties)
      - Sliding-window Term Burst Detection (60s window surge across ground reports)
    """
    normalized = normalize_arabic_text(raw_text)
    norm_lower = normalized.lower()

    matched_acoustic: List[str] = [
        t for t in ACOUSTIC_TRIGGERS if normalize_arabic_text(t).lower() in norm_lower
    ]
    matched_kinetic: List[str] = [
        t for t in KINETIC_TRIGGERS if normalize_arabic_text(t).lower() in norm_lower
    ]
    matched_sonic: List[str] = [
        t for t in SONIC_BOOM_TRIGGERS if normalize_arabic_text(t).lower() in norm_lower
    ]
    matched_smoke: List[str] = [
        t for t in SMOKE_IMPACT_TRIGGERS if normalize_arabic_text(t).lower() in norm_lower
    ]

    is_kinetic = len(matched_kinetic) > 0
    is_acoustic = len(matched_acoustic) > 0
    has_sonic_boom = len(matched_sonic) > 0
    has_smoke_or_impact = len(matched_smoke) > 0 or is_kinetic

    # Sonic Boom Differentiation (Catalog Rule 3):
    # If sonic boom tokens appear and NO smoke plume or kinetic strike tokens appear,
    # flag as sonic boom overflight for Stage 2 de-escalation.
    is_sonic_boom_only = bool(has_sonic_boom and not has_smoke_or_impact)

    # Sliding-window Term Burst Detection (Catalog Rule 1)
    if record_burst and (is_kinetic or is_acoustic):
        burst_info = _global_burst_detector.record_and_check(normalized, source_hint=source_hint)
    else:
        burst_info = {
            "term_burst_active": False,
            "burst_count": 0,
            "burst_distinct_sources": 0,
            "burst_tokens": [],
            "window_sec": 60.0,
        }

    if is_kinetic:
        signal_type = "kinetic_impact"
        verification_status = "probable_kinetic_occurrence"
        confidence = "high" if (len(matched_kinetic) >= 2 or burst_info["term_burst_active"]) else "medium"
        sub_signal_type = "confirmed_smoke_or_strike" if len(matched_smoke) > 0 else "kinetic_engagement"
    elif is_sonic_boom_only:
        signal_type = "acoustic_chatter"
        sub_signal_type = "sonic_boom_overflight"
        verification_status = "de_escalated_sonic_boom"
        confidence = "low"
    elif is_acoustic:
        signal_type = "acoustic_chatter"
        sub_signal_type = "burst_corroborated_acoustic" if burst_info["term_burst_active"] else "unverified_acoustic"
        verification_status = "corroborated_acoustic_burst" if burst_info["term_burst_active"] else "unverified_anomaly"
        confidence = "medium" if burst_info["term_burst_active"] else "low"
    else:
        signal_type = "general_intelligence"
        sub_signal_type = "contextual"
        verification_status = "contextual_update"
        confidence = "standard"

    return {
        "signal_type": signal_type,
        "sub_signal_type": sub_signal_type,
        "verification_status": verification_status,
        "confidence": confidence,
        "is_kinetic": is_kinetic,
        "is_acoustic": is_acoustic,
        "is_sonic_boom_only": is_sonic_boom_only,
        "has_smoke_or_impact": has_smoke_or_impact,
        "matched_kinetic_triggers": matched_kinetic,
        "matched_acoustic_triggers": matched_acoustic,
        "matched_sonic_boom_triggers": matched_sonic,
        "matched_smoke_triggers": matched_smoke,
        "term_burst_active": burst_info["term_burst_active"],
        "burst_count": burst_info["burst_count"],
        "burst_distinct_sources": burst_info["burst_distinct_sources"],
        "burst_tokens": burst_info["burst_tokens"],
        "normalized_text": normalized,
    }


def get_character_ngrams(text: str, n: int = 3) -> Set[str]:
    """Extract character n-grams from normalized text for MinHash/Jaccard similarity."""
    cleaned = re.sub(r"\s+", "", text)
    if len(cleaned) < n:
        return {cleaned} if cleaned else set()
    return {cleaned[i:i + n] for i in range(len(cleaned) - n + 1)}


def compute_jaccard_similarity(text1: str, text2: str, n: int = 3) -> float:
    """
    Compute Jaccard similarity coefficient between two texts using character n-grams.
    Values >= 0.85 indicate near-duplicate cross-channel echoes.
    """
    norm1 = normalize_arabic_text(text1)
    norm2 = normalize_arabic_text(text2)

    grams1 = get_character_ngrams(norm1, n=n)
    grams2 = get_character_ngrams(norm2, n=n)

    if not grams1 or not grams2:
        return 0.0

    intersection = len(grams1.intersection(grams2))
    union = len(grams1.union(grams2))

    return float(intersection / union) if union > 0 else 0.0


def is_duplicate_echo(
    new_text: str,
    recent_texts: List[str],
    threshold: float = 0.85,
    n: int = 3,
) -> Tuple[bool, float]:
    """
    Check if a newly ingested post is an echo of an existing recent post.
    Returns (is_duplicate, max_similarity).
    """
    max_sim = 0.0
    for past in recent_texts:
        sim = compute_jaccard_similarity(new_text, past, n=n)
        if sim > max_sim:
            max_sim = sim
        if sim >= threshold:
            return True, sim
    return False, max_sim

