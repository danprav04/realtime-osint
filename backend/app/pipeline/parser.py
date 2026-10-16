"""
backend/app/pipeline/parser.py - 5-Tier Resilient JSON Parser for LLM Outputs
"""

import json
import logging
import re
from typing import Any, Callable, Dict, Optional, Type, TypeVar
from pydantic import BaseModel

logger = logging.getLogger("osint.pipeline.parser")

T = TypeVar("T", bound=BaseModel)


def extract_and_parse_json(
    raw_text: str,
    target_model: Type[T],
    fallback_factory: Optional[Callable[[], T]] = None,
) -> T:
    """
    Resiliently parse LLM output into a validated Pydantic model.
    Applies 5 tiers of parsing, stripping, sanitization, and fallback:
      Tier 1: Direct JSON parse
      Tier 2: Markdown code fence stripping (```json ... ```)
      Tier 3: Outermost bracket extraction ({ ... })
      Tier 4: Regex syntax sanitization & repair (trailing commas, quotes)
      Tier 5: Fallback factory execution
    """
    if not raw_text or not raw_text.strip():
        if fallback_factory:
            return fallback_factory()
        raise ValueError("Empty LLM response text")

    text = raw_text.strip()

    # Tier 1: Direct JSON parse
    try:
        data = json.loads(text)
        if isinstance(data, dict):
            return target_model.model_validate(data)
    except Exception:
        pass

    # Tier 2: Extract from markdown code fences
    fence_match = re.search(r"```(?:json)?\s*([\s\S]*?)\s*```", text, re.IGNORECASE)
    if fence_match:
        fenced_content = fence_match.group(1).strip()
        try:
            data = json.loads(fenced_content)
            if isinstance(data, dict):
                return target_model.model_validate(data)
        except Exception:
            text = fenced_content  # Continue to Tier 3 with un-fenced content

    # Tier 3: Extract outermost curly brackets
    bracket_match = re.search(r"(\{[\s\S]*\})", text)
    if bracket_match:
        bracketed_content = bracket_match.group(1).strip()
        try:
            data = json.loads(bracketed_content)
            if isinstance(data, dict):
                return target_model.model_validate(data)
        except Exception:
            text = bracketed_content

    # Tier 3b: Search individual candidate JSON blocks (handles Gemma thinking chains) from last to first
    cand_blocks = re.findall(r"\{[^{}]*\}", raw_text)
    for cand in reversed(cand_blocks):
        try:
            cand_data = json.loads(cand)
            if isinstance(cand_data, dict):
                return target_model.model_validate(cand_data)
        except Exception:
            continue

    # Tier 4: Regex sanitization & syntax repair
    try:
        cleaned = text
        # Remove trailing commas before closing braces/brackets: [1, 2,] -> [1, 2]
        cleaned = re.sub(r",\s*([\]}])", r"\1", cleaned)
        # Convert single-quoted property keys: 'key': -> "key":
        cleaned = re.sub(r"\'([a-zA-Z0-9_\-\.]+)\'\s*:", r'"\1":', cleaned)
        # Convert single-quoted string values: : 'value' -> : "value"
        cleaned = re.sub(r":\s*\'([^\']*?)\'", r': "\1"', cleaned)
        data = json.loads(cleaned)
        if isinstance(data, dict):
            return target_model.model_validate(data)
    except Exception:
        pass

    # Tier 5: Fallback factory execution
    if fallback_factory is not None:
        logger.warning(
            f"All JSON extraction tiers failed for {target_model.__name__}. Executing fallback factory."
        )
        try:
            return fallback_factory()
        except Exception as fb_exc:
            logger.error(f"Fallback factory error for {target_model.__name__}: {fb_exc}")

    raise ValueError(
        f"Failed to parse valid {target_model.__name__} from LLM output: {raw_text[:200]}..."
    )
