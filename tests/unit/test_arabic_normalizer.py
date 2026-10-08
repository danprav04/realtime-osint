"""
tests/unit/test_arabic_normalizer.py - Unit tests for Arabic normalization & signal triangulation.
"""

import pytest
from backend.app.pipeline.arabic_normalizer import (
    normalize_arabic_text,
    classify_kinetic_signal,
    compute_jaccard_similarity,
    is_duplicate_echo,
)


def test_normalize_arabic_text_tashkeel_and_tatweel():
    # Text with Tashkeel (harakat), Tatweel (ـ), and emojis
    raw = "🚨 عاجِـــــلٌ: انْفِجَارٌ عَنِيفٌ فِي صَيْدَا! ⚡"
    cleaned = normalize_arabic_text(raw)
    assert "ـ" not in cleaned
    assert "🚨" not in cleaned
    assert "⚡" not in cleaned
    assert "عاجل" in cleaned
    assert "انفجار" in cleaned


def test_normalize_arabic_text_alifs_and_letters():
    raw = "أحمد وإبراهيم وآخرون في مدينة غزّة ومأوى"
    cleaned = normalize_arabic_text(raw)
    # Bare, hamzated, and maddah Alifs -> ا
    assert "احمد" in cleaned
    assert "ابراهيم" in cleaned
    # Taa Marbuta -> ه
    assert "مدينه" in cleaned
    assert "غزه" in cleaned
    # Alif Maqsura -> ي
    assert "ماوي" in cleaned


def test_normalize_arabic_numerals():
    raw = "سقوط ۳ مسيرات و ٤ صواريخ"
    cleaned = normalize_arabic_text(raw)
    assert "3" in cleaned
    assert "4" in cleaned


def test_classify_kinetic_signal_acoustic_chatter():
    text = "سماع دوي انفجارات في ضواحي المدينة وصافرات الانذار تدوي"
    sig = classify_kinetic_signal(text)
    assert sig["is_acoustic"] is True
    assert sig["is_kinetic"] is False
    assert sig["signal_type"] == "acoustic_chatter"
    assert sig["verification_status"] == "unverified_anomaly"


def test_classify_kinetic_signal_kinetic_impact():
    text = "غارة جوية واستهداف مباشر لمبنى سكني وتصاعد اعمدة الدخان وسقوط شهداء وجرحى"
    sig = classify_kinetic_signal(text)
    assert sig["is_kinetic"] is True
    assert sig["signal_type"] == "kinetic_impact"
    assert sig["verification_status"] == "probable_kinetic_occurrence"
    assert sig["confidence"] == "high"


def test_compute_jaccard_similarity_and_duplicate_echo():
    post1 = "عاجل: غارة إسرائيلية استهدفت بلدة كفركلا في جنوب لبنان"
    post2 = "🔴 عاجل: غارة اسرائيليه استهدفت بلده كفركلا في جنوب لبنان ⚡"
    post_unrelated = "بيان صادر عن وزارة الخارجية الإيرانية بشأن الاتفاق النووي"

    sim_same = compute_jaccard_similarity(post1, post2)
    assert sim_same >= 0.85

    sim_diff = compute_jaccard_similarity(post1, post_unrelated)
    assert sim_diff < 0.40

    is_dup, max_sim = is_duplicate_echo(post2, [post1], threshold=0.85)
    assert is_dup is True
    assert max_sim >= 0.85
