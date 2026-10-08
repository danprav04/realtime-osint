"""
backend/app/pipeline/arabic_normalizer.py

Algorithmic text normalization, linguistic signal triangulation,
and Jaccard/MinHash echo deduplication for frontline Arabic and regional OSINT feeds.
Based on tactical OSINT signal architecture recommendations.
"""

import re
import unicodedata
from typing import Any, Dict, List, Set, Tuple


# Regex for Tashkeel (Arabic diacritics / short vowel marks: Fathah, Dammah, Kasrah, Sukun, Shaddah, Tanwin)
TASHKEEL_REGEX = re.compile(r"[\u064B-\u065F\u0670]")

# Tatweel / Kashida elongation mark
TATWEEL_CHAR = "\u0640"

# Persian and Arabic-Indic numerals translation table
NUMERAL_TRANSLATION = str.maketrans({
    "۰": "0", "۱": "1", "۲": "2", "۳": "3", "۴": "4",
    "۵": "5", "۶": "6", "۷": "7", "۸": "8", "۹": "9",
    "٠": "0", "١": "1", "٢": "2", "٣": "3", "٤": "4",
    "٥": "5", "٦": "6", "٧": "7", "٨": "8", "٩": "9",
})

# Triangulation keywords: Acoustic chatter vs. Verified kinetic impacts
ACOUSTIC_TRIGGERS: List[str] = [
    "دوي انفجار",
    "دوي انفجارات",
    "سماع دوي",
    "سماع صوت",
    "اصوات انفجارات",
    "صافرات الانذار",
    "صفارات الانذار",
    "تحليق مكثف",
    "طيران حربي في الاجواء",
    "جدار صوت",
    "خرق جدار الصوت",
    "انفجار مجهول",
]

KINETIC_TRIGGERS: List[str] = [
    "استهداف",
    "غاره",
    "غارات",
    "قصف",
    "سقوط صاروخ",
    "اعتراض",
    "مسيره انقضاضيه",
    "صاروخ باليستي",
    "اصابه مباشره",
    "اندلاع حريق",
    "تصاعد اعمده الدخان",
    "اشتباكات بالاسلحه",
    "استشهد",
    "شهداء وجرحى",
    "اضرار ماديه",
]


def normalize_arabic_text(text: str) -> str:
    """
    Apply linguistic normalization to raw Arabic text:
      1. Strip zero-width non-joiners (ZWNJ) and special formatting.
      2. Strip Tashkeel (short vowels, Tanwin, Shaddah).
      3. Strip Tatweel (Kashida elongation).
      4. Unify Alifs (أ, إ, آ, ٱ -> ا).
      5. Normalize Taa Marbuta (ة -> ه).
      6. Normalize Alif Maqsura (ى -> ي).
      7. Convert Arabic-Indic and Eastern Arabic numerals to standard digits.
      8. Strip sensationalist emojis and collapse redundant whitespace.
    """
    if not text:
        return ""

    # Remove zero-width characters (ZWNJ \u200c, ZWJ \u200d, LTR/RTL marks)
    text = re.sub(r"[\u200B-\u200F\u202A-\u202E\uFEFF]", "", text)

    # Strip Tashkeel
    text = TASHKEEL_REGEX.sub("", text)

    # Strip Tatweel
    text = text.replace(TATWEEL_CHAR, "")

    # Unify Alifs: bare, hamzated, and maddah
    text = re.sub(r"[أإآٱ]", "ا", text)

    # Normalize Taa Marbuta to Haa
    text = text.replace("ة", "ه")

    # Normalize Alif Maqsura to Yaa
    text = text.replace("ى", "ي")

    # Translate Eastern numerals to Western digits
    text = text.translate(NUMERAL_TRANSLATION)

    # Strip alert emojis often found in Telegram wires
    text = re.sub(r"[🚨⚠️🔴⚡📢💥🔊🔥⛔]+", " ", text)

    # Normalize whitespace
    text = re.sub(r"\s+", " ", text).strip()

    return text


def classify_kinetic_signal(raw_text: str) -> Dict[str, Any]:
    """
    Perform linguistic triangulation between acoustic observations
    ('explosions heard' / sirens) and verified kinetic strikes
    ('direct hit' / airstrike / casualties / missile impacts).
    """
    normalized = normalize_arabic_text(raw_text)

    matched_acoustic: List[str] = [
        t for t in ACOUSTIC_TRIGGERS if t in normalized
    ]
    matched_kinetic: List[str] = [
        t for t in KINETIC_TRIGGERS if t in normalized
    ]

    is_kinetic = len(matched_kinetic) > 0
    is_acoustic = len(matched_acoustic) > 0

    if is_kinetic:
        signal_type = "kinetic_impact"
        verification_status = "probable_kinetic_occurrence"
        confidence = "high" if len(matched_kinetic) >= 2 else "medium"
    elif is_acoustic:
        signal_type = "acoustic_chatter"
        verification_status = "unverified_anomaly"
        confidence = "low"
    else:
        signal_type = "general_intelligence"
        verification_status = "contextual_update"
        confidence = "standard"

    return {
        "signal_type": signal_type,
        "verification_status": verification_status,
        "confidence": confidence,
        "is_kinetic": is_kinetic,
        "is_acoustic": is_acoustic,
        "matched_kinetic_triggers": matched_kinetic,
        "matched_acoustic_triggers": matched_acoustic,
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
