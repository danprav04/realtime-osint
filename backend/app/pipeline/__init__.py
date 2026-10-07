"""
backend/app/pipeline/__init__.py - Multi-Stage AI Pipeline Package
"""

from backend.app.pipeline.models import (
    AIStageConfig,
    AnalyzedEvent,
    DashboardTrendSynthesis,
    PeriodicDigestResult,
    Stage1Result,
    Stage2Result,
    Stage3Result,
    Stage4Result,
)
from backend.app.pipeline.orchestrator import (
    PipelineOrchestrator,
    get_pipeline_orchestrator,
)
from backend.app.pipeline.parser import extract_and_parse_json
from backend.app.pipeline.providers import (
    BaseAIProvider,
    GeminiProvider,
    GroqProvider,
    MockAIProvider,
    get_provider_registry,
    get_stage_provider,
    resolve_api_key,
)

__all__ = [
    "AIStageConfig",
    "AnalyzedEvent",
    "BaseAIProvider",
    "DashboardTrendSynthesis",
    "GeminiProvider",
    "GroqProvider",
    "MockAIProvider",
    "PeriodicDigestResult",
    "PipelineOrchestrator",
    "Stage1Result",
    "Stage2Result",
    "Stage3Result",
    "Stage4Result",
    "extract_and_parse_json",
    "get_pipeline_orchestrator",
    "get_provider_registry",
    "get_stage_provider",
    "resolve_api_key",
]
