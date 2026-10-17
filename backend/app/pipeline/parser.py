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


def _try_validate(data: Any, target_model: Type[T]) -> Optional[T]:
    """
    Attempt to validate data into target_model with resilient envelope unwrapping.
    Handles responses wrapped in:
      - Class name, e.g. {"Stage2Result": {...}}
      - Generic wrappers, e.g. {"result": {...}}, {"data": {...}}, {"output": {...}}
      - Single-key root objects or nested dictionaries matching target model fields.
    """
    if not isinstance(data, (dict, list)):
        return None

    # Direct validation attempt
    try:
        return target_model.model_validate(data)
    except Exception:
        pass

    if isinstance(data, dict):
        model_name_lower = target_model.__name__.lower()
        wrapper_candidates = [
            model_name_lower,
            "result",
            "data",
            "output",
            "response",
            "stage",
            "payload",
            "analysis",
        ]

        # 1. Check known wrapper keys (case-insensitive)
        for k, v in data.items():
            if str(k).lower() in wrapper_candidates and isinstance(v, (dict, list)):
                try:
                    return target_model.model_validate(v)
                except Exception:
                    pass

        # 2. Check if single-key dict where value is dict or list
        if len(data) == 1:
            only_val = next(iter(data.values()))
            if isinstance(only_val, (dict, list)):
                try:
                    return target_model.model_validate(only_val)
                except Exception:
                    pass

        # 3. Check any nested dict value that shares fields with target_model
        target_fields = getattr(target_model, "model_fields", None)
        if target_fields and isinstance(target_fields, dict):
            expected_keys = set(target_fields.keys())
            for v in data.values():
                if isinstance(v, dict) and (set(v.keys()) & expected_keys):
                    try:
                        return target_model.model_validate(v)
                    except Exception:
                        pass

    return None


def extract_and_parse_json(
    raw_text: str,
    target_model: Type[T],
    fallback_factory: Optional[Callable[[], T]] = None,
) -> T:
    """
    Resiliently parse LLM output into a validated Pydantic model.
    Applies 5 tiers of parsing, stripping, sanitization, and fallback:
      Tier 1: Direct JSON parse + envelope unwrapping
      Tier 2: Markdown code fence stripping (```json ... ```)
      Tier 3: Outermost bracket extraction ({ ... })
      Tier 3b: Candidate JSON blocks search
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
        validated = _try_validate(data, target_model)
        if validated is not None:
            return validated
    except Exception:
        pass

    # Tier 2: Extract from markdown code fences (check in reverse to prioritize final answer over thinking examples)
    fences = re.findall(r"```(?:json)?\s*([\s\S]*?)\s*```", raw_text, re.IGNORECASE)
    for fence in reversed(fences):
        try:
            data = json.loads(fence.strip())
            validated = _try_validate(data, target_model)
            if validated is not None:
                return validated
        except Exception:
            continue

    # Tier 3: Extract outermost curly brackets
    bracket_match = re.search(r"(\{[\s\S]*\})", text)
    if bracket_match:
        bracketed_content = bracket_match.group(1).strip()
        try:
            data = json.loads(bracketed_content)
            validated = _try_validate(data, target_model)
            if validated is not None:
                return validated
        except Exception:
            text = bracketed_content

    # Tier 3b: Search individual candidate JSON blocks (handles Gemma thinking chains) from last to first
    cand_blocks = re.findall(r"\{[^{}]*\}|\[[^\[\]]*\]", raw_text)
    for cand in reversed(cand_blocks):
        try:
            cand_data = json.loads(cand)
            validated = _try_validate(cand_data, target_model)
            if validated is not None:
                return validated
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
        validated = _try_validate(data, target_model)
        if validated is not None:
            return validated
    except Exception:
        pass

    # Tier 5: Fallback factory execution
    if fallback_factory is not None:
        logger.warning(
            f"All JSON extraction tiers failed for {target_model.__name__}. Executing fallback factory. Raw text snippet: {raw_text[:250]}"
        )
        try:
            return fallback_factory()
        except Exception as fb_exc:
            logger.error(f"Fallback factory error for {target_model.__name__}: {fb_exc}")

    raise ValueError(
        f"Failed to parse valid {target_model.__name__} from LLM output: {raw_text[:200]}..."
    )
