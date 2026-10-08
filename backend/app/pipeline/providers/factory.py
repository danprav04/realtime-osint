"""
backend/app/pipeline/providers/factory.py - Provider Factory & 3-Tier API Key Resolution
"""

import os
import logging
from typing import Dict, Optional, Union
from backend.app.config import Settings, get_settings
from backend.app.pipeline.models import AIStageConfig
from backend.app.pipeline.providers.base import BaseAIProvider
from backend.app.pipeline.providers.gemini import GeminiProvider
from backend.app.pipeline.providers.groq import GroqProvider
from backend.app.pipeline.providers.mock import MockAIProvider

logger = logging.getLogger("osint.ai.factory")


def resolve_api_key(
    provider_name: str,
    stage_override: Optional[str] = None,
    db_keys: Optional[Dict[str, str]] = None,
    settings: Optional[Settings] = None,
) -> Optional[str]:
    """
    3-Tier API Key Resolution Hierarchy:
      Tier 1 (Highest): Per-stage explicit override
      Tier 2 (Medium):  Dynamic DB SystemSettings key ("api_keys" table row)
      Tier 3 (Default): Global environment variable (.env)
    """
    p_norm = provider_name.lower().strip()

    # Tier 1: Stage-specific override
    if stage_override and stage_override.strip() and not stage_override.startswith("***"):
        return stage_override.strip()

    # Tier 2: Dynamic DB SystemSettings
    db_k = db_keys or {}
    key_name = f"{p_norm}_api_key"
    if db_k.get(key_name) and not str(db_k[key_name]).startswith("***"):
        return str(db_k[key_name]).strip()

    # Tier 3: Environment variables
    st = settings or get_settings()
    if p_norm == "gemini":
        env_val = getattr(st, "GEMINI_API_KEY", None) or os.environ.get("GEMINI_API_KEY")
        if env_val and not str(env_val).startswith("***"):
            return str(env_val).strip()
    elif p_norm == "groq":
        env_val = getattr(st, "GROQ_API_KEY", None) or os.environ.get("GROQ_API_KEY")
        if env_val and not str(env_val).startswith("***"):
            return str(env_val).strip()

    return None


def get_stage_provider(
    stage_config: AIStageConfig,
    db_keys: Optional[Dict[str, str]] = None,
    settings: Optional[Settings] = None,
    use_mock: bool = False,
) -> BaseAIProvider:
    """
    Instantiate appropriate AIProvider for a given stage configuration.
    """
    provider_name = stage_config.provider.lower().strip()
    api_key = resolve_api_key(
        provider_name=provider_name,
        stage_override=stage_config.api_key_override,
        db_keys=db_keys,
        settings=settings,
    )

    if use_mock or os.environ.get("USE_MOCK_AI", "0") == "1":
        return MockAIProvider(
            provider=provider_name,
            api_key=api_key or "mock-key",
            default_model=stage_config.model_name,
        )

    if provider_name == "gemini":
        return GeminiProvider(api_key=api_key, default_model=stage_config.model_name)
    elif provider_name == "groq":
        return GroqProvider(api_key=api_key, default_model=stage_config.model_name)
    else:
        raise ValueError(f"Unsupported AI provider: '{stage_config.provider}'")


class ProviderRegistry:
    """
    Registry for managing AI Provider instances across pipeline stages.
    Supports pooling and dynamic key updates.
    """

    def __init__(self, use_mock: bool = False):
        self._providers: Dict[str, BaseAIProvider] = {}
        self.use_mock = use_mock

    def get_provider(
        self,
        provider: str,
        model_name: str,
        api_key: Optional[str] = None,
        temperature: float = 0.2,
    ) -> BaseAIProvider:
        p_norm = provider.lower().strip()
        is_mock = self.use_mock or os.environ.get("USE_MOCK_AI", "0") == "1"
        key = f"{p_norm}:{model_name}:mock" if is_mock else f"{p_norm}:{model_name}:{api_key or ''}"
        if key not in self._providers:
            if is_mock:
                self._providers[key] = MockAIProvider(
                    provider=p_norm,
                    api_key=api_key or "mock-key",
                    default_model=model_name,
                )
            elif p_norm == "gemini":
                self._providers[key] = GeminiProvider(
                    api_key=api_key,
                    default_model=model_name,
                )
            elif p_norm == "groq":
                self._providers[key] = GroqProvider(
                    api_key=api_key,
                    default_model=model_name,
                )
            else:
                raise ValueError(f"Unknown provider '{provider}'")
        return self._providers[key]

    async def close_all(self) -> None:
        for p in self._providers.values():
            await p.close()
        self._providers.clear()


_registry_instance: Optional[ProviderRegistry] = None


def get_provider_registry(use_mock: bool = False) -> ProviderRegistry:
    global _registry_instance
    if _registry_instance is None or _registry_instance.use_mock != use_mock:
        _registry_instance = ProviderRegistry(use_mock=use_mock)
    return _registry_instance
