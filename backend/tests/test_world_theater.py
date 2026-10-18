"""
backend/tests/test_world_theater.py - Unit & Integration Tests for World Theater Context Engine
"""

import pytest
import asyncio
from unittest.mock import AsyncMock, patch, MagicMock
from backend.app.services.world_theater_service import (
    WorldTheaterService,
    get_default_world_theater_data,
    get_world_theater_service,
    sanitize_world_theater_text,
)


def test_default_world_theater_data_baseline():
    """Verify default baseline data contains current 2026 year, leaders, and theaters."""
    data = get_default_world_theater_data()
    assert "temporal_anchor" in data
    assert data["temporal_anchor"]["current_year"] >= 2025
    assert "global_leadership" in data
    
    us = data["global_leadership"]["united_states"]
    assert "Trump" in us["head_of_state"]
    assert "President" in us["title"]
    assert "47th President" in us["title"]
    
    isr = data["global_leadership"]["israel"]
    assert "Netanyahu" in isr["head_of_government"]
    
    irn = data["global_leadership"]["iran"]
    assert "Pezeshkian" in irn["president"]
    
    theaters = [t["theater"] for t in data["active_operational_theaters"]]
    assert any("Middle East" in t for t in theaters)
    assert any("Red Sea" in t for t in theaters)


def test_sanitize_world_theater_text():
    """Verify defensive text sanitizer normalizes outdated 'former president' phrases."""
    sample1 = "Former U.S. President Donald Trump has issued a total endorsement of Israeli Prime Minister Benjamin Netanyahu."
    fixed1 = sanitize_world_theater_text(sample1)
    assert "Former" not in fixed1
    assert "President Donald Trump has issued a total endorsement" in fixed1

    sample2 = "Former President Donald Trump warned Washington may be forced to take military action."
    fixed2 = sanitize_world_theater_text(sample2)
    assert "Former President" not in fixed2
    assert "President Donald Trump warned" in fixed2

    sample3 = "Former president trump stated that an agreement is near."
    fixed3 = sanitize_world_theater_text(sample3)
    assert "Former president" not in fixed3
    assert "President Trump stated" in fixed3

    sample4 = "Former prime minister Benjamin Netanyahu met with delegates."
    fixed4 = sanitize_world_theater_text(sample4)
    assert "Former prime minister" not in fixed4
    assert "Prime Minister Benjamin Netanyahu" in fixed4


def test_prompt_block_generation():
    """Verify get_prompt_block produces a rich, compact context block."""
    svc = WorldTheaterService()
    prompt_block = svc.get_prompt_block("scoring")
    
    assert "CURRENT WORLD THEATER & GEOPOLITICAL CONTEXT" in prompt_block
    assert "Donald J. Trump is the sitting 47th President of the United States" in prompt_block
    assert "Benjamin Netanyahu" in prompt_block
    assert "Masoud Pezeshkian" in prompt_block
    assert "NEVER refer to Donald J. Trump as 'former president'" in prompt_block
    assert "Active Theaters" in prompt_block


@pytest.mark.asyncio
async def test_sync_from_live_apis_mocked():
    """Verify live API sync extracts updated Wikidata descriptions."""
    svc = WorldTheaterService()
    
    mock_wikidata_resp = {
        "entities": {
            "Q22686": {
                "descriptions": {"en": {"value": "President of the United States (since 2025)"}}
            },
            "Q30873": {
                "descriptions": {"en": {"value": "Prime Minister of Israel"}}
            },
            "Q371160": {
                "descriptions": {"en": {"value": "President of Iran (since 2024)"}}
            }
        }
    }
    
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = mock_wikidata_resp
    
    with patch.object(svc.http_client, "get", new=AsyncMock(return_value=mock_response)):
        updated = await svc.sync_from_live_apis()
        assert updated["live_api_status"] == "synced"
        assert updated["sync_source"] == "wikidata_live_api"
        assert "President of the United States (since 2025)" in updated["global_leadership"]["united_states"]["title"]


@pytest.mark.asyncio
async def test_sync_from_live_apis_fallback():
    """Verify that network errors in live sync gracefully fall back to authoritative baseline."""
    svc = WorldTheaterService()
    
    with patch.object(svc.http_client, "get", new=AsyncMock(side_effect=Exception("Connection timeout"))):
        updated = await svc.sync_from_live_apis()
        assert updated["live_api_status"] == "fallback_active"
        assert "Trump" in updated["global_leadership"]["united_states"]["head_of_state"]


def test_telegram_bot_format_sanitizes_former_president():
    """Verify TelegramBotService format methods sanitize 'former president' before dispatch."""
    from datetime import datetime, timezone
    from backend.app.services.telegram_bot import TelegramAlertPayload, TelegramBotService

    bot = TelegramBotService(bot_token="test_tok", chat_id="12345", rate_limiting_enabled=False)
    payload = TelegramAlertPayload(
        event_id="evt_123",
        title="Former US President Donald Trump issues statement",
        headline="Former U.S. President Donald Trump warns of military strike",
        summary="Former US President Donald Trump stated that Washington may be forced to take military action against Iran.",
        importance_score=8,
        source_name="Clash Report",
        published_at=datetime.now(timezone.utc),
    )

    formatted_html = bot.format_html_message(payload)
    assert "Former" not in formatted_html
    assert "President Donald Trump warns" in formatted_html
    assert "President Donald Trump stated" in formatted_html


@pytest.mark.asyncio
async def test_stage3_summary_sanitizes_llm_hallucinations():
    """Verify Stage 3 summary cleanses any rogue 'former president' output from an LLM."""
    from backend.app.pipeline.stage3_summary import run_stage3_summary
    from backend.app.pipeline.providers.mock import MockAIProvider

    mock_prov = MockAIProvider()
    mock_prov.generate_text = AsyncMock(
        return_value='{"headline": "Trump Endorses Netanyahu", "summary": "Former U.S. President Donald Trump has issued a total endorsement of Israeli Prime Minister Benjamin Netanyahu."}'
    )
    event = {"title": "Trump endorsement wire", "raw_text": "Trump stated complete support for Netanyahu"}

    res = await run_stage3_summary(
        event=event,
        intelligence_text="Trump endorses Netanyahu",
        provider=mock_prov,
    )

    assert "Former" not in res.summary
    assert "President Donald Trump has issued a total endorsement" in res.summary

