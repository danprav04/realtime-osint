"""
backend/tests/test_parser.py - Unit tests for 5-tier resilient JSON parser and envelope unwrapping
"""

import pytest
from pydantic import BaseModel
from backend.app.pipeline.parser import extract_and_parse_json


class SampleStage2Result(BaseModel):
    military_importance: int
    urgency: int
    credibility: int
    civil_impact: int
    importance_score: int
    reasoning: str


def fallback_stage2() -> SampleStage2Result:
    return SampleStage2Result(
        military_importance=1,
        urgency=1,
        credibility=1,
        civil_impact=1,
        importance_score=1,
        reasoning="fallback",
    )


def test_parser_direct_json():
    raw = '{"military_importance": 4, "urgency": 3, "credibility": 4, "civil_impact": 2, "importance_score": 3, "reasoning": "Standard"}'
    parsed = extract_and_parse_json(raw, SampleStage2Result, fallback_stage2)
    assert parsed.military_importance == 4
    assert parsed.reasoning == "Standard"


def test_parser_unwraps_class_name_root_envelope():
    # Mistral or similar models outputting {"SampleStage2Result": {...}}
    raw = '''{
        "SampleStage2Result": {
            "military_importance": 5,
            "urgency": 4,
            "credibility": 5,
            "civil_impact": 3,
            "importance_score": 5,
            "reasoning": "Inside class envelope"
        }
    }'''
    parsed = extract_and_parse_json(raw, SampleStage2Result, fallback_stage2)
    assert parsed.military_importance == 5
    assert parsed.reasoning == "Inside class envelope"


def test_parser_unwraps_generic_result_envelope():
    raw = '''{
        "result": {
            "military_importance": 7,
            "urgency": 6,
            "credibility": 8,
            "civil_impact": 5,
            "importance_score": 7,
            "reasoning": "Inside result envelope"
        }
    }'''
    parsed = extract_and_parse_json(raw, SampleStage2Result, fallback_stage2)
    assert parsed.military_importance == 7
    assert parsed.reasoning == "Inside result envelope"


def test_parser_unwraps_markdown_code_fence_with_envelope():
    raw = '''Here is the analysis:
```json
{
    "data": {
        "military_importance": 8,
        "urgency": 9,
        "credibility": 9,
        "civil_impact": 8,
        "importance_score": 9,
        "reasoning": "High critical event"
    }
}
```
Done.'''
    parsed = extract_and_parse_json(raw, SampleStage2Result, fallback_stage2)
    assert parsed.military_importance == 8
    assert parsed.importance_score == 9


def test_parser_executes_fallback_on_garbage():
    raw = "Not a json at all, completely malformed garbage."
    parsed = extract_and_parse_json(raw, SampleStage2Result, fallback_stage2)
    assert parsed.reasoning == "fallback"
    assert parsed.importance_score == 1
