"""
backend/app/pipeline/providers/factory.py - Provider Factory & Multi-Key Resolution Engine
==========================================================================================
Supports 3-tier API key resolution hierarchy, Groq multi-key load balancing pool,
and cross-stage provider pooling.
"""

import os
import logging
from typing import Dict, List, Optional, Union
from backend.app.config import Settings, get_settings
from backend.app.pipeline.models import AIStageConfig
from backend.app.pipeline.providers.base import BaseAIProvider
from backend.app.pipeline.providers.gemini import GeminiProvider
from backend.app.pipeline.providers.groq import GroqProvider
from backend.app.pipeline.providers.aihubmix import AIHubMixProvider
from backend.app.pipeline.providers.mistral import MistralProvider
from backend.app.pipeline.providers.cloudflare import CloudflareProvider
from backend.app.pipeline.providers.zai import ZaiProvider
from backend.app.pipeline.providers.siliconflow import SiliconFlowProvider
from backend.app.pipeline.providers.mock import MockAIProvider
from backend.app.pipeline.providers.key_pool import GroqKeyPool, parse_key_list

logger = logging.getLogger("osint.ai.factory")

_shared_groq_pool: Optional[GroqKeyPool] = None


def get_shared_groq_pool(keys: Optional[List[str]] = None) -> GroqKeyPool:
    """
    Get or initialize the process-wide GroqKeyPool singleton.
    Ensures all Groq models across stages (extraction, summarization, visuals)
    share synchronized rotation, pacing, and 429 cooldown status.
    """
    global _shared_groq_pool
    if _shared_groq_pool is None:
        init_keys = keys if keys is not None else resolve_api_keys("groq")
        _shared_groq_pool = GroqKeyPool(keys=init_keys)
    elif keys:
        _shared_groq_pool.set_keys(keys)
    elif not _shared_groq_pool.total_keys:
        fallback_keys = resolve_api_keys("groq")
        if fallback_keys:
            _shared_groq_pool.set_keys(fallback_keys)
    return _shared_groq_pool


def resolve_api_keys(
    provider_name: str,
    stage_override: Optional[str] = None,
    db_keys: Optional[Dict[str, str]] = None,
    settings: Optional[Settings] = None,
) -> List[str]:
    """
    3-Tier Multi-Key Resolution Hierarchy:
      Tier 1 (Highest): Per-stage explicit override (comma-separated or single)
      Tier 2 (Medium):  Dynamic DB SystemSettings key ("api_keys" table row)
      Tier 3 (Default): Global environment variables (.env)
    Returns an ordered, deduplicated list of non-empty API keys.
    """
    p_norm = provider_name.lower().strip()

    # Tier 1: Stage-specific override
    if stage_override and stage_override.strip() and not stage_override.startswith("***"):
        parsed = parse_key_list(stage_override)
        if parsed:
            return parsed

    # Tier 2: Dynamic DB SystemSettings
    db_k = db_keys or {}
    for k_name in (f"{p_norm}_api_keys", f"{p_norm}_api_key", f"{p_norm}_api_token"):
        val = db_k.get(k_name)
        if val and not str(val).startswith("***"):
            parsed = parse_key_list(val)
            if parsed:
                return parsed

    # Tier 3: Environment variables
    st = settings or get_settings()
    if p_norm == "groq":
        if hasattr(st, "get_groq_api_keys"):
            parsed_st = st.get_groq_api_keys()
            if parsed_st:
                return parsed_st
        env_val = (
            getattr(st, "GROQ_API_KEYS", None)
            or os.environ.get("GROQ_API_KEYS")
            or getattr(st, "GROQ_API_KEY", None)
            or os.environ.get("GROQ_API_KEY")
        )
        return parse_key_list(env_val)

    elif p_norm == "gemini":
        env_val = getattr(st, "GEMINI_API_KEY", None) or os.environ.get("GEMINI_API_KEY")
        return parse_key_list(env_val)

    elif p_norm in ("aihubmix", "aihub", "inferera"):
        env_val = getattr(st, "AIHUBMIX_API_KEY", None) or os.environ.get("AIHUBMIX_API_KEY")
        return parse_key_list(env_val)

    elif p_norm in ("mistral", "mistralai"):
        env_val = getattr(st, "MISTRAL_API_KEY", None) or os.environ.get("MISTRAL_API_KEY")
        return parse_key_list(env_val)

    elif p_norm in ("cloudflare", "cf", "workers_ai", "workers-ai"):
        env_val = (
            getattr(st, "CLOUDFLARE_API_TOKEN", None)
            or os.environ.get("CLOUDFLARE_API_TOKEN")
            or getattr(st, "CLOUDFLARE_API_KEY", None)
            or os.environ.get("CLOUDFLARE_API_KEY")
        )
        return parse_key_list(env_val)

    elif p_norm in ("zai", "zhipu", "z.ai", "bigmodel"):
        env_val = getattr(st, "ZAI_API_KEY", None) or os.environ.get("ZAI_API_KEY")
        return parse_key_list(env_val)

    elif p_norm in ("siliconflow", "silicon", "siliconcloud"):
        env_val = getattr(st, "SILICONFLOW_API_KEY", None) or os.environ.get("SILICONFLOW_API_KEY")
        return parse_key_list(env_val)

    return []


def resolve_api_key(
    provider_name: str,
    stage_override: Optional[str] = None,
    db_keys: Optional[Dict[str, str]] = None,
    settings: Optional[Settings] = None,
) -> Optional[str]:
    """
    Single-key resolution for backward compatibility. Returns primary key from resolve_api_keys.
    """
    keys = resolve_api_keys(
        provider_name=provider_name,
        stage_override=stage_override,
        db_keys=db_keys,
        settings=settings,
    )
    return keys[0] if keys else None


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
    keys = resolve_api_keys(
        provider_name=provider_name,
        stage_override=stage_config.api_key_override,
        db_keys=db_keys,
        settings=settings,
    )
    primary_key = keys[0] if keys else None

    if use_mock or os.environ.get("USE_MOCK_AI", "0") == "1":
        return MockAIProvider(
            provider=provider_name,
            api_key=primary_key or "mock-key",
            default_model=stage_config.model_name,
        )

    if provider_name == "gemini":
        return GeminiProvider(api_key=primary_key, default_model=stage_config.model_name)
    elif provider_name == "groq":
        pool = get_shared_groq_pool(keys=keys if keys else None)
        return GroqProvider(
            api_key=primary_key,
            key_pool=pool,
            default_model=stage_config.model_name,
        )
    elif provider_name in ("aihubmix", "aihub", "inferera"):
        return AIHubMixProvider(api_key=primary_key, default_model=stage_config.model_name)
    elif provider_name in ("mistral", "mistralai"):
        return MistralProvider(api_key=primary_key, default_model=stage_config.model_name)
    elif provider_name in ("cloudflare", "cf", "workers_ai", "workers-ai"):
        cf_account = (
            (db_keys.get("cloudflare_account_id") if db_keys else None)
            or (getattr(settings, "CLOUDFLARE_ACCOUNT_ID", None) if settings else None)
        )
        return CloudflareProvider(api_key=primary_key, account_id=cf_account, default_model=stage_config.model_name)
    elif provider_name in ("zai", "zhipu", "z.ai", "bigmodel"):
        return ZaiProvider(api_key=primary_key, default_model=stage_config.model_name)
    elif provider_name in ("siliconflow", "silicon", "siliconcloud"):
        return SiliconFlowProvider(api_key=primary_key, default_model=stage_config.model_name)
    else:
        raise ValueError(f"Unsupported AI provider: '{stage_config.provider}'")


class ProviderRegistry:
    """
    Registry for managing AI Provider instances across pipeline stages.
    Supports pooling, multi-key rotation pools, and dynamic key updates.
    """

    def __init__(self, use_mock: bool = False):
        self._providers: Dict[str, BaseAIProvider] = {}
        self.use_mock = use_mock

    def get_provider(
        self,
        provider: str,
        model_name: str,
        api_key: Optional[str] = None,
        account_id: Optional[str] = None,
        temperature: float = 0.2,
    ) -> BaseAIProvider:
        p_norm = provider.lower().strip()
        is_mock = self.use_mock or os.environ.get("USE_MOCK_AI", "0") == "1"
        key = f"{p_norm}:{model_name}:mock" if is_mock else f"{p_norm}:{model_name}:{api_key or ''}:{account_id or ''}"
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
                all_resolved_keys = resolve_api_keys("groq")
                parsed_keys = parse_key_list(api_key) if api_key else []
                if len(parsed_keys) > 1:
                    pool_keys = parsed_keys
                elif all_resolved_keys:
                    pool_keys = all_resolved_keys
                elif parsed_keys:
                    pool_keys = parsed_keys
                else:
                    pool_keys = None
                pool = get_shared_groq_pool(keys=pool_keys)
                self._providers[key] = GroqProvider(
                    api_key=api_key,
                    key_pool=pool,
                    default_model=model_name,
                )
            elif p_norm in ("aihubmix", "aihub", "inferera"):
                self._providers[key] = AIHubMixProvider(
                    api_key=api_key,
                    default_model=model_name,
                )
            elif p_norm in ("mistral", "mistralai"):
                self._providers[key] = MistralProvider(
                    api_key=api_key,
                    default_model=model_name,
                )
            elif p_norm in ("cloudflare", "cf", "workers_ai", "workers-ai"):
                self._providers[key] = CloudflareProvider(
                    api_key=api_key,
                    account_id=account_id,
                    default_model=model_name,
                )
            elif p_norm in ("zai", "zhipu", "z.ai", "bigmodel"):
                self._providers[key] = ZaiProvider(
                    api_key=api_key,
                    default_model=model_name,
                )
            elif p_norm in ("siliconflow", "silicon", "siliconcloud"):
                self._providers[key] = SiliconFlowProvider(
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
