"""
backend/app/pipeline/providers/__init__.py - AI Provider Abstraction Package
"""

from backend.app.pipeline.providers.base import BaseAIProvider
from backend.app.pipeline.providers.gemini import GeminiProvider
from backend.app.pipeline.providers.groq import GroqProvider
from backend.app.pipeline.providers.aihubmix import AIHubMixProvider
from backend.app.pipeline.providers.mistral import MistralProvider
from backend.app.pipeline.providers.cloudflare import CloudflareProvider
from backend.app.pipeline.providers.zai import ZaiProvider
from backend.app.pipeline.providers.siliconflow import SiliconFlowProvider
from backend.app.pipeline.providers.mock import MockAIProvider
from backend.app.pipeline.providers.factory import (
    ProviderRegistry,
    get_provider_registry,
    get_stage_provider,
    resolve_api_key,
)

__all__ = [
    "BaseAIProvider",
    "GeminiProvider",
    "GroqProvider",
    "AIHubMixProvider",
    "MistralProvider",
    "CloudflareProvider",
    "ZaiProvider",
    "SiliconFlowProvider",
    "MockAIProvider",
    "ProviderRegistry",
    "get_provider_registry",
    "get_stage_provider",
    "resolve_api_key",
]
