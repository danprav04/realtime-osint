'use client';

import React, { useState, useEffect } from 'react';
import {
  Cpu,
  Check,
  AlertCircle,
  Save,
  Plus,
  Trash2,
  ChevronUp,
  ChevronDown,
  CornerDownRight,
  ShieldAlert,
  ExternalLink,
  Gauge,
  RotateCcw,
} from 'lucide-react';
import { AIPipelineSettings, AIStageConfig, AIFallbackConfig, SystemSettings } from '@/lib/types';
import { updateAIPipelineSettings, resetAIPipelineSettings } from '@/lib/api';

type AIProvider = 'gemini' | 'groq' | 'aihubmix' | 'mistral' | 'cloudflare' | 'zai' | 'siliconflow';

interface AIConfigMatrixProps {
  initialSettings: AIPipelineSettings | null;
  systemSettings?: SystemSettings | null;
  onRefresh: () => void;
}

export function AIConfigMatrix({ initialSettings, systemSettings, onRefresh }: AIConfigMatrixProps) {
  const defaultStage = (
    provider: AIProvider,
    model: string,
    fallbacks: AIFallbackConfig[] = []
  ): AIStageConfig => ({
    provider,
    model_name: model,
    temperature: provider === 'gemini' ? 0.1 : 0.2,
    fallbacks,
  });

  const defaultGatekeeperStage = (): AIStageConfig => ({
    provider: 'mistral',
    model_name: 'ministral-3b-2512',
    temperature: 0.0,
    fallbacks: [
      { provider: 'groq', model_name: 'openai/gpt-oss-20b', temperature: 0.0 },
      { provider: 'gemini', model_name: 'gemma-4-26b-a4b-it', temperature: 0.0 },
      { provider: 'gemini', model_name: 'gemini-3.1-flash-lite', temperature: 0.0 },
      { provider: 'gemini', model_name: 'gemma-4-31b-it', temperature: 0.0 },
    ],
  });

  const defaultSituationSummaryStage = (): AIStageConfig => ({
    provider: 'groq',
    model_name: 'qwen/qwen3.8-27b',
    temperature: 0.2,
    fallbacks: [
      { provider: 'groq', model_name: 'openai/gpt-oss-120b', temperature: 0.2 },
      { provider: 'aihubmix', model_name: 'coding-glm-5.3-free', temperature: 0.2 },
      { provider: 'gemini', model_name: 'gemini-3.5-flash-lite', temperature: 0.2 },
      { provider: 'gemini', model_name: 'gemini-3.1-flash-lite', temperature: 0.2 },
    ],
  });

  const [settings, setSettings] = useState<AIPipelineSettings>(
    initialSettings
      ? {
          gatekeeper: initialSettings.gatekeeper || defaultGatekeeperStage(),
          situation_summary: initialSettings.situation_summary || defaultSituationSummaryStage(),
          ...initialSettings,
        }
      : {
          gatekeeper: defaultGatekeeperStage(),
          extraction: defaultStage('groq', 'qwen/qwen3.8-27b', [
            { provider: 'mistral', model_name: 'ministral-8b-2512', temperature: 0.2 },
            { provider: 'gemini', model_name: 'gemini-3.5-flash-lite', temperature: 0.2 },
            { provider: 'gemini', model_name: 'gemma-4-26b-a4b-it', temperature: 0.2 },
            { provider: 'gemini', model_name: 'gemma-4-31b-it', temperature: 0.2 },
          ]),
          scoring: defaultStage('mistral', 'codestral-2508', [
            { provider: 'mistral', model_name: 'ministral-14b-2512', temperature: 0.1 },
            { provider: 'gemini', model_name: 'gemini-3.1-flash-lite', temperature: 0.1 },
            { provider: 'gemini', model_name: 'gemma-4-26b-a4b-it', temperature: 0.1 },
            { provider: 'aihubmix', model_name: 'coding-glm-5.3-free', temperature: 0.1 },
          ]),
          summarization: defaultStage('groq', 'qwen/qwen3.8-27b', [
            { provider: 'groq', model_name: 'openai/gpt-oss-120b', temperature: 0.2 },
            { provider: 'gemini', model_name: 'gemini-3.5-flash-lite', temperature: 0.2 },
            { provider: 'gemini', model_name: 'gemini-3.1-flash-lite', temperature: 0.2 },
            { provider: 'aihubmix', model_name: 'coding-glm-5.3-free', temperature: 0.2 },
            { provider: 'groq', model_name: 'openai/gpt-oss-20b', temperature: 0.2 },
          ]),
          visuals: defaultStage('groq', 'openai/gpt-oss-20b', [
            { provider: 'mistral', model_name: 'ministral-8b-2512', temperature: 0.2 },
            { provider: 'gemini', model_name: 'gemma-4-26b-a4b-it', temperature: 0.2 },
            { provider: 'gemini', model_name: 'gemini-3.1-flash-lite', temperature: 0.2 },
          ]),
          situation_summary: defaultSituationSummaryStage(),
        }
  );

  useEffect(() => {
    if (initialSettings) {
      setSettings({
        gatekeeper: initialSettings.gatekeeper || defaultGatekeeperStage(),
        situation_summary: initialSettings.situation_summary || defaultSituationSummaryStage(),
        ...initialSettings,
      });
    }
  }, [initialSettings]);

  const [loading, setLoading] = useState(false);
  const [feedback, setFeedback] = useState<{ type: 'success' | 'error'; message: string } | null>(null);

  const stages = [
    {
      key: 'gatekeeper' as const,
      name: 'Stage 0: Pre-Pipeline Chatter & Noise Gatekeeper',
      desc: 'High-RPD micro-batched filter (up to 12 items/call) that discards unrelated social/comment noise BEFORE database storage & Stages 1–4',
      badge: 'High-RPD Micro-Batch | 14.4K RPD',
    },
    {
      key: 'extraction' as const,
      name: 'Stage 1: Parsing, Extraction & Translation',
      desc: 'Normalizes entity facts and translates non-English (Arabic/Farsi/Hebrew) to English',
      badge: 'Multilingual NLP',
    },
    {
      key: 'scoring' as const,
      name: 'Stage 2: Importance & Threat Scoring (1-10)',
      desc: 'Evaluates priority against dynamic theme prompt; triggers alerts if ≥ threshold',
      badge: 'Core Intelligence Scoring',
    },
    {
      key: 'summarization' as const,
      name: 'Stage 3: Event Intelligence Summarization',
      desc: 'Generates concise tactical summaries and periodic situational digests for incoming reports',
      badge: 'Feed Summaries',
    },
    {
      key: 'visuals' as const,
      name: 'Stage 4: Visuals, Trends & Narrative',
      desc: 'Detects velocity clusters, source hotspots, and tactical trend narratives',
      badge: 'Visual Analytics',
    },
    {
      key: 'situation_summary' as const,
      name: 'Stage 5: AI Situational Summary & Intelligence Fusion',
      desc: 'Full-spectrum executive SitRep synthesis across semantic storyline clusters, vector corroboration, and live OSINT telemetry',
      badge: 'Executive SitRep | Flagship Models',
    },
  ];

  const handleProviderChange = (stageKey: keyof AIPipelineSettings, provider: AIProvider) => {
    let defaultModel = 'gemini-3.5-flash-lite';
    if (provider === 'groq') {
      defaultModel = stageKey === 'situation_summary' ? 'openai/gpt-oss-120b' : 'qwen/qwen3.8-27b';
    } else if (provider === 'aihubmix') {
      defaultModel = 'coding-glm-5.3-free';
    } else if (provider === 'mistral') {
      defaultModel = stageKey === 'gatekeeper' ? 'ministral-3b-2512' : stageKey === 'situation_summary' ? 'mistral-large-2512' : 'ministral-8b-2512';
    } else if (provider === 'cloudflare') {
      defaultModel = stageKey === 'gatekeeper' ? '@cf/meta/llama-3.2-1b-instruct' : '@cf/meta/llama-3.1-8b-instruct-fp8-fast';
    } else if (provider === 'zai') {
      defaultModel = 'glm-4.7-flash';
    } else if (provider === 'siliconflow') {
      defaultModel = stageKey === 'situation_summary' ? 'Qwen/Qwen2.5-14B-Instruct' : 'Qwen/Qwen2.5-7B-Instruct';
    }
    setSettings((prev) => {
      const stage = prev[stageKey];
      if (!stage) return prev;
      // Strip any aihubmix from fallbacks if aihubmix is primary (shared quota across models)
      const cleanedFallbacks = provider === 'aihubmix'
        ? (stage.fallbacks || []).filter((f) => f.provider !== 'aihubmix')
        : (stage.fallbacks || []);
      return {
        ...prev,
        [stageKey]: {
          ...stage,
          provider,
          model_name: defaultModel,
          fallbacks: cleanedFallbacks,
        },
      };
    });
  };

  const handleModelChange = (stageKey: keyof AIPipelineSettings, model_name: string) => {
    setSettings((prev) => {
      const stage = prev[stageKey];
      if (!stage) return prev;
      return {
        ...prev,
        [stageKey]: {
          ...stage,
          model_name,
        },
      };
    });
  };

  const handleTemperatureChange = (stageKey: keyof AIPipelineSettings, temperature: number) => {
    setSettings((prev) => {
      const stage = prev[stageKey];
      if (!stage) return prev;
      return {
        ...prev,
        [stageKey]: {
          ...stage,
          temperature,
        },
      };
    });
  };

  // Fallback management handlers
  const handleAddFallback = (
    stageKey: keyof AIPipelineSettings,
    suggestedProvider?: AIProvider,
    suggestedModel?: string
  ) => {
    setSettings((prev) => {
      const stage = prev[stageKey];
      if (!stage) return prev;
      const existing = stage.fallbacks || [];
      const currentProvider = stage.provider;
      const providerCycle: AIProvider[] = ['mistral', 'cloudflare', 'groq', 'gemini', 'siliconflow', 'zai'];
      const nextProvider = suggestedProvider || providerCycle.find((p) => p !== currentProvider && !existing.some((f) => f.provider === p)) || 'mistral';
      let nextModel = suggestedModel;
      if (!nextModel) {
        if (nextProvider === 'gemini') nextModel = stageKey === 'situation_summary' ? 'gemini-3.5-flash-lite' : 'gemini-3.1-flash-lite';
        else if (nextProvider === 'groq') nextModel = stageKey === 'situation_summary' ? 'openai/gpt-oss-120b' : 'qwen/qwen3.8-27b';
        else if (nextProvider === 'mistral') nextModel = stageKey === 'gatekeeper' ? 'ministral-3b-2512' : stageKey === 'situation_summary' ? 'mistral-large-2512' : 'ministral-8b-2512';
        else if (nextProvider === 'cloudflare') nextModel = stageKey === 'gatekeeper' ? '@cf/meta/llama-3.2-1b-instruct' : '@cf/meta/llama-3.1-8b-instruct-fp8-fast';
        else if (nextProvider === 'zai') nextModel = 'glm-4.7-flash';
        else if (nextProvider === 'siliconflow') nextModel = stageKey === 'situation_summary' ? 'Qwen/Qwen2.5-14B-Instruct' : 'Qwen/Qwen2.5-7B-Instruct';
        else nextModel = 'coding-glm-5.3-free';
      }

      return {
        ...prev,
        [stageKey]: {
          ...stage,
          fallbacks: [
            ...existing,
            {
              provider: nextProvider,
              model_name: nextModel,
              temperature: stage.temperature,
            },
          ],
        },
      };
    });
  };

  const handleRemoveFallback = (stageKey: keyof AIPipelineSettings, index: number) => {
    setSettings((prev) => {
      const stage = prev[stageKey];
      if (!stage) return prev;
      const existing = [...(stage.fallbacks || [])];
      existing.splice(index, 1);
      return {
        ...prev,
        [stageKey]: {
          ...stage,
          fallbacks: existing,
        },
      };
    });
  };

  const handleUpdateFallback = (
    stageKey: keyof AIPipelineSettings,
    index: number,
    field: keyof AIFallbackConfig,
    value: any
  ) => {
    setSettings((prev) => {
      const stage = prev[stageKey];
      if (!stage) return prev;
      const existing = [...(stage.fallbacks || [])];
      if (existing[index]) {
        const updated = {
          ...existing[index],
          [field]: value,
        };
        if (field === 'provider') {
          if (value === 'gemini') {
            updated.model_name = stageKey === 'situation_summary' ? 'gemini-3.5-flash-lite' : 'gemini-3.1-flash-lite';
          } else if (value === 'groq') {
            updated.model_name = stageKey === 'situation_summary' ? 'openai/gpt-oss-120b' : 'qwen/qwen3.8-27b';
          } else if (value === 'mistral') {
            updated.model_name = stageKey === 'gatekeeper' ? 'ministral-3b-2512' : stageKey === 'situation_summary' ? 'mistral-large-2512' : 'ministral-8b-2512';
          } else if (value === 'cloudflare') {
            updated.model_name = stageKey === 'gatekeeper' ? '@cf/meta/llama-3.2-1b-instruct' : '@cf/meta/llama-3.1-8b-instruct-fp8-fast';
          } else if (value === 'zai') {
            updated.model_name = 'glm-4.7-flash';
          } else if (value === 'siliconflow') {
            updated.model_name = stageKey === 'situation_summary' ? 'Qwen/Qwen2.5-14B-Instruct' : 'Qwen/Qwen2.5-7B-Instruct';
          } else if (value === 'aihubmix') {
            updated.model_name = 'coding-glm-5.3-free';
          }
        }
        existing[index] = updated;
      }
      return {
        ...prev,
        [stageKey]: {
          ...stage,
          fallbacks: existing,
        },
      };
    });
  };

  const handleMoveFallback = (stageKey: keyof AIPipelineSettings, index: number, direction: 'up' | 'down') => {
    setSettings((prev) => {
      const stage = prev[stageKey];
      if (!stage) return prev;
      const existing = [...(stage.fallbacks || [])];
      const targetIndex = direction === 'up' ? index - 1 : index + 1;
      if (targetIndex >= 0 && targetIndex < existing.length) {
        const temp = existing[index];
        existing[index] = existing[targetIndex];
        existing[targetIndex] = temp;
      }
      return {
        ...prev,
        [stageKey]: {
          ...stage,
          fallbacks: existing,
        },
      };
    });
  };

  const handleSave = async (e: React.FormEvent) => {
    e.preventDefault();
    setLoading(true);
    setFeedback(null);
    try {
      await updateAIPipelineSettings(settings);
      setFeedback({
        type: 'success',
        message: 'AI Pipeline configuration updated! Hot-reloaded across background workers with fallback chains active.',
      });
      onRefresh();
    } catch (err: any) {
      setFeedback({ type: 'error', message: err.message || 'Failed to update AI settings' });
    } finally {
      setLoading(false);
    }
  };

  const handleReset = async () => {
    if (!window.confirm('Reset all AI Pipeline stage models and fallbacks to default system configurations?')) {
      return;
    }
    setLoading(true);
    setFeedback(null);
    try {
      const res = await resetAIPipelineSettings();
      setSettings(res);
      setFeedback({
        type: 'success',
        message: 'AI Pipeline configuration reset to code defaults and hot-reloaded!',
      });
      onRefresh();
    } catch (err: any) {
      setFeedback({ type: 'error', message: err.message || 'Failed to reset AI settings' });
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="card-slick rounded-xl p-6 space-y-6">
      {/* Header */}
      <div className="flex flex-wrap items-center justify-between gap-4 pb-4 hairline-b">
        <div className="flex items-center space-x-3">
          <div className="p-2.5 rounded-lg bg-accent-indigo/15 text-accent-indigo border border-accent-indigo/30">
            <Cpu className="w-5 h-5" />
          </div>
          <div>
            <h3 className="text-base font-semibold text-zinc-100 tracking-tight">
              AI Processing Pipeline & Multi-Tier Fallback Matrix
            </h3>
            <p className="text-xs text-zinc-400">
              Configure primary models and unlimited automatic fallback chains across Google Gemini, Groq Cloud, and AI Hub Mix (Free Flagships)
            </p>
          </div>
        </div>

        <div className="flex items-center space-x-2">
          <button
            type="button"
            onClick={handleReset}
            disabled={loading}
            className="inline-flex items-center space-x-1.5 px-3 py-1.5 text-xs font-medium text-zinc-300 hover:text-white bg-zinc-800 hover:bg-zinc-700 rounded-lg pressable border border-zinc-700 shadow-sm"
            title="Reset matrix to system code defaults"
          >
            <RotateCcw className="w-3.5 h-3.5" />
            <span>Reset Defaults</span>
          </button>
          <button
            onClick={handleSave}
            disabled={loading}
            className="inline-flex items-center space-x-1.5 px-3.5 py-1.5 text-xs font-semibold text-white bg-accent-indigo hover:bg-accent-indigo/90 rounded-lg pressable shadow-sm"
          >
            <Save className="w-4 h-4" />
            <span>{loading ? 'Saving...' : 'Save AI Matrix'}</span>
          </button>
        </div>
      </div>

      {/* Connected AI Provider Rate Limit Shortcuts */}
      <div className="p-3.5 rounded-xl bg-canvas-recessed hairline-border flex flex-wrap items-center justify-between gap-3 text-xs">
        <div className="flex items-center space-x-2">
          <Gauge className="w-4 h-4 text-accent-cyan shrink-0" />
          <div>
            <span className="font-semibold text-zinc-200">
              Connected AI Provider Rate Limits &amp; Quotas
            </span>
            <span className="hidden sm:inline text-[11px] text-zinc-400 ml-2">
              Monitor live RPM, TPM &amp; RPD thresholds to tune fallback chains
            </span>
          </div>
        </div>

        <div className="flex flex-wrap items-center gap-2">
          <a
            href="https://aistudio.google.com/rate-limit"
            target="_blank"
            rel="noopener noreferrer"
            title="Open Google AI Studio Rate Limits Dashboard (15 RPM / 500–14.4K RPD)"
            className="inline-flex items-center space-x-1.5 px-2.5 py-1.5 rounded-lg bg-surface-card hover:bg-surface-raised text-zinc-200 hover:text-accent-cyan hairline-border transition-colors pressable"
          >
            <span
              className={`w-1.5 h-1.5 rounded-full ${
                systemSettings?.api_keys?.gemini_api_key
                  ? 'bg-emerald-400 shadow-[0_0_6px_rgba(16,185,129,0.6)]'
                  : 'bg-accent-cyan'
              }`}
            />
            <span className="font-medium">Google Gemini Limits</span>
            <span className="text-[10px] font-mono text-zinc-400 hidden md:inline">
              (AI Studio)
            </span>
            <ExternalLink className="w-3 h-3 text-accent-cyan" />
          </a>

          <a
            href="https://console.groq.com/settings/limits"
            target="_blank"
            rel="noopener noreferrer"
            title="Open Groq Cloud Console Organization Rate Limits (30 RPM / 1K–14.4K RPD)"
            className="inline-flex items-center space-x-1.5 px-2.5 py-1.5 rounded-lg bg-surface-card hover:bg-surface-raised text-zinc-200 hover:text-accent-cyan hairline-border transition-colors pressable"
          >
            <span
              className={`w-1.5 h-1.5 rounded-full ${
                systemSettings?.api_keys?.groq_api_key
                  ? 'bg-emerald-400 shadow-[0_0_6px_rgba(16,185,129,0.6)]'
                  : 'bg-accent-indigo'
              }`}
            />
            <span className="font-medium">Groq Cloud Limits</span>
            <span className="text-[10px] font-mono text-zinc-400 hidden md:inline">
              (Groq Console)
            </span>
            <ExternalLink className="w-3 h-3 text-accent-cyan" />
          </a>

          <a
            href="https://console.mistral.ai/limits/"
            target="_blank"
            rel="noopener noreferrer"
            title="Open Mistral AI Limits (12.5 RPS / 1.3M TPM on Ministral-3B | $10/mo Free Tier)"
            className="inline-flex items-center space-x-1.5 px-2.5 py-1.5 rounded-lg bg-surface-card hover:bg-surface-raised text-zinc-200 hover:text-accent-cyan hairline-border transition-colors pressable"
          >
            <span
              className={`w-1.5 h-1.5 rounded-full ${
                systemSettings?.api_keys?.mistral_api_key
                  ? 'bg-emerald-400 shadow-[0_0_6px_rgba(16,185,129,0.6)]'
                  : 'bg-amber-400'
              }`}
            />
            <span className="font-medium">Mistral Limits</span>
            <span className="text-[10px] font-mono text-zinc-400 hidden md:inline">
              (12.5 RPS)
            </span>
            <ExternalLink className="w-3 h-3 text-accent-cyan" />
          </a>

          <a
            href="https://developers.cloudflare.com/workers-ai/models/"
            target="_blank"
            rel="noopener noreferrer"
            title="Open Cloudflare Workers AI Model Limits (10,000 Neurons/day | 300 RPM)"
            className="inline-flex items-center space-x-1.5 px-2.5 py-1.5 rounded-lg bg-surface-card hover:bg-surface-raised text-zinc-200 hover:text-accent-cyan hairline-border transition-colors pressable"
          >
            <span
              className={`w-1.5 h-1.5 rounded-full ${
                systemSettings?.api_keys?.cloudflare_api_token
                  ? 'bg-emerald-400 shadow-[0_0_6px_rgba(16,185,129,0.6)]'
                  : 'bg-orange-400'
              }`}
            />
            <span className="font-medium">Cloudflare AI</span>
            <span className="text-[10px] font-mono text-zinc-400 hidden md:inline">
              (10K Neurons/day)
            </span>
            <ExternalLink className="w-3 h-3 text-accent-cyan" />
          </a>

          <a
            href="https://open.bigmodel.cn/dev/howuse/rate-limits"
            target="_blank"
            rel="noopener noreferrer"
            title="Open Z.ai / Zhipu AI Console (Permanent $0 Free Flash Models)"
            className="inline-flex items-center space-x-1.5 px-2.5 py-1.5 rounded-lg bg-surface-card hover:bg-surface-raised text-zinc-200 hover:text-accent-cyan hairline-border transition-colors pressable"
          >
            <span
              className={`w-1.5 h-1.5 rounded-full ${
                systemSettings?.api_keys?.zai_api_key
                  ? 'bg-emerald-400 shadow-[0_0_6px_rgba(16,185,129,0.6)]'
                  : 'bg-sky-400'
              }`}
            />
            <span className="font-medium">Z.ai Flash</span>
            <span className="text-[10px] font-mono text-zinc-400 hidden md:inline">
              ($0 / Concurrency)
            </span>
            <ExternalLink className="w-3 h-3 text-accent-cyan" />
          </a>

          <a
            href="https://cloud.siliconflow.com/models"
            target="_blank"
            rel="noopener noreferrer"
            title="Open SiliconFlow Console (1,000 RPM on $0 Model Catalog)"
            className="inline-flex items-center space-x-1.5 px-2.5 py-1.5 rounded-lg bg-surface-card hover:bg-surface-raised text-zinc-200 hover:text-accent-cyan hairline-border transition-colors pressable"
          >
            <span
              className={`w-1.5 h-1.5 rounded-full ${
                systemSettings?.api_keys?.siliconflow_api_key
                  ? 'bg-emerald-400 shadow-[0_0_6px_rgba(16,185,129,0.6)]'
                  : 'bg-purple-400'
              }`}
            />
            <span className="font-medium">SiliconFlow</span>
            <span className="text-[10px] font-mono text-zinc-400 hidden md:inline">
              (1K RPM Free)
            </span>
            <ExternalLink className="w-3 h-3 text-accent-cyan" />
          </a>

          <a
            href={systemSettings?.api_keys?.hf_embedding_url || 'https://huggingface.co/spaces'}
            target="_blank"
            rel="noopener noreferrer"
            title="Open Hugging Face TEI Embeddings Space (Unlimited 24/7 Semantic Map Embeddings)"
            className="inline-flex items-center space-x-1.5 px-2.5 py-1.5 rounded-lg bg-surface-card hover:bg-surface-raised text-zinc-200 hover:text-accent-cyan hairline-border transition-colors pressable"
          >
            <span
              className={`w-1.5 h-1.5 rounded-full ${
                systemSettings?.api_keys?.hf_embedding_url
                  ? 'bg-emerald-400 shadow-[0_0_6px_rgba(16,185,129,0.6)]'
                  : 'bg-zinc-500'
              }`}
            />
            <span className="font-medium">HF Space TEI</span>
            <span className="text-[10px] font-mono text-zinc-400 hidden md:inline">
              (Unlimited Embeddings)
            </span>
            <ExternalLink className="w-3 h-3 text-accent-cyan" />
          </a>
        </div>
      </div>

      {feedback && (
        <div
          className={`p-3 rounded-lg text-xs flex items-center space-x-2 hairline-border ${
            feedback.type === 'success'
              ? 'bg-emerald-500/10 text-emerald-300 border-emerald-500/20'
              : 'bg-rose-500/10 text-rose-300 border-rose-500/20'
          }`}
        >
          {feedback.type === 'success' ? (
            <Check className="w-4 h-4 text-emerald-400" />
          ) : (
            <AlertCircle className="w-4 h-4 text-rose-400" />
          )}
          <span>{feedback.message}</span>
        </div>
      )}

      {/* 4 Stages Matrix */}
      <form onSubmit={handleSave} className="space-y-4">
        <div className="grid grid-cols-1 gap-4">
          {stages.map((stage) => {
            const currentStageConfig = settings[stage.key] || defaultGatekeeperStage();
            const isGemini = currentStageConfig.provider === 'gemini';
            const isGroq = currentStageConfig.provider === 'groq';
            const isMistral = currentStageConfig.provider === 'mistral';
            const isCloudflare = currentStageConfig.provider === 'cloudflare';
            const isZai = currentStageConfig.provider === 'zai';
            const isSiliconFlow = currentStageConfig.provider === 'siliconflow';
            const isAIHubMix = currentStageConfig.provider === 'aihubmix';
            const fallbacks = currentStageConfig.fallbacks || [];
            const gkStats = stage.key === 'gatekeeper' ? systemSettings?.gatekeeper_stats : null;

            return (
              <div
                key={stage.key}
                className="p-4 rounded-xl bg-canvas-recessed hairline-border space-y-4 hover:border-white/10 transition-colors"
              >
                <div className="flex flex-wrap items-center justify-between gap-2 pb-2 border-b border-white/5">
                  <div>
                    <h4 className="text-xs sm:text-sm font-semibold text-zinc-200">
                      {stage.name}
                    </h4>
                    <p className="text-[11px] text-zinc-400">{stage.desc}</p>
                  </div>
                  <div className="flex items-center space-x-2">
                    {fallbacks.length > 0 && (
                      <span className="px-2 py-0.5 rounded text-[10px] font-mono bg-accent-indigo/15 text-accent-indigo border border-accent-indigo/30">
                        {fallbacks.length} fallback{fallbacks.length > 1 ? 's' : ''} active
                      </span>
                    )}
                    <span className="px-2 py-0.5 rounded text-[10px] font-mono bg-surface-raised hairline-border text-zinc-400">
                      {stage.badge}
                    </span>
                  </div>
                </div>

                {gkStats && (
                  <div className="p-2.5 rounded-lg bg-accent-cyan/5 border border-accent-cyan/20 flex flex-wrap items-center justify-between gap-3 text-[11px] font-mono">
                    <div className="flex flex-wrap items-center gap-4">
                      <span className="text-zinc-300">
                        Evaluated: <strong className="text-white">{gkStats.evaluated_count}</strong>
                      </span>
                      <span className="text-emerald-400">
                        Kept: <strong>{gkStats.passed_count}</strong> ({gkStats.fast_passed_count} fast-pass)
                      </span>
                      <span className="text-amber-400">
                        Dropped Noise: <strong>{gkStats.total_dropped_count}</strong> ({gkStats.drop_rate_pct}%)
                      </span>
                      <span className="text-accent-cyan">
                        LLM Micro-Batches: <strong>{gkStats.batch_calls_count}</strong>
                      </span>
                    </div>
                    {gkStats.last_model_used && (
                      <span className="text-zinc-400 text-[10px]">
                        Active Engine: <span className="text-zinc-200">{gkStats.last_model_used}</span>
                      </span>
                    )}
                  </div>
                )}

                {/* Primary Model Section */}
                <div className="p-3 rounded-lg bg-surface-card/60 hairline-border space-y-3">
                  <div className="flex items-center justify-between">
                    <span className="text-[11px] font-semibold text-zinc-300 uppercase tracking-wider">
                      Primary Model (Tier 0)
                    </span>
                    <span className="text-[10px] text-emerald-400 font-mono flex items-center gap-1">
                      <span className="w-1.5 h-1.5 rounded-full bg-emerald-400 animate-pulse" />
                      Active Route
                    </span>
                  </div>

                  <div className="grid grid-cols-1 sm:grid-cols-3 gap-3 text-xs">
                    {/* Provider selector */}
                    <div>
                      <label className="block text-zinc-400 font-medium mb-1">Provider Engine</label>
                      <select
                        value={currentStageConfig.provider}
                        onChange={(e) =>
                          handleProviderChange(stage.key, e.target.value as AIProvider)
                        }
                        className="w-full rounded-lg bg-surface-card px-3 py-2 text-zinc-100 hairline-border focus:outline-none focus:border-accent-indigo"
                      >
                        <option value="groq">Groq Cloud (LPU Inference)</option>
                        <option value="gemini">Google Gemini API</option>
                        <option value="mistral">Mistral AI (La Plateforme)</option>
                        <option value="cloudflare">Cloudflare Workers AI (10K Neurons)</option>
                        <option value="zai">Z.ai / Zhipu ($0 Flash)</option>
                        <option value="siliconflow">SiliconFlow (Free Catalog)</option>
                        {(stage.key === 'scoring' || stage.key === 'summarization' || stage.key === 'situation_summary') && (
                          <option value="aihubmix">AI Hub Mix (GLM-5.3 Flagship)</option>
                        )}
                      </select>
                    </div>

                    {/* Model name input */}
                    <div>
                      <div className="flex justify-between items-center mb-1">
                        <label className="text-zinc-400 font-medium">Model Name</label>
                        <a
                          href={
                            isGemini
                              ? 'https://aistudio.google.com/rate-limit'
                              : isGroq
                              ? 'https://console.groq.com/settings/limits'
                              : isMistral
                              ? 'https://console.mistral.ai/limits/'
                              : isCloudflare
                              ? 'https://developers.cloudflare.com/workers-ai/models/'
                              : isZai
                              ? 'https://open.bigmodel.cn/dev/howuse/rate-limits'
                              : isSiliconFlow
                              ? 'https://cloud.siliconflow.com/models'
                              : 'https://aihubmix.com/models/free'
                          }
                          target="_blank"
                          rel="noopener noreferrer"
                          title="View provider rate limits"
                          className="inline-flex items-center space-x-1 text-[10px] text-accent-cyan hover:text-accent-cyan/80 font-mono hover:underline transition-colors"
                        >
                          <span>
                            {isGemini
                              ? currentStageConfig.model_name.includes('gemma')
                                ? 'Gemma 4: 30 RPM / 14.4K RPD'
                                : 'Flash: 500 RPD'
                              : isGroq
                              ? 'Free: 30 RPM / 1K RPD'
                              : isMistral
                              ? 'Mistral: Up to 12.5 RPS'
                              : isCloudflare
                              ? 'Cloudflare: 300 RPM / 10K Neurons'
                              : isZai
                              ? 'Z.ai: Unlimited RPD Flash'
                              : isSiliconFlow
                              ? 'SiliconFlow: 1,000 RPM Free'
                              : 'Free: 10 RPM / 100 RPD / 1M TPD'}
                          </span>
                          <ExternalLink className="w-2.5 h-2.5" />
                        </a>
                      </div>
                      <input
                        type="text"
                        list={`models-${stage.key}`}
                        value={currentStageConfig.model_name}
                        onChange={(e) => handleModelChange(stage.key, e.target.value)}
                        placeholder={
                          isGemini
                            ? 'gemma-4-26b-a4b-it'
                            : isGroq
                            ? 'qwen/qwen3.8-27b'
                            : isMistral
                            ? 'ministral-3b-2512'
                            : isCloudflare
                            ? '@cf/meta/llama-3.2-1b-instruct'
                            : isZai
                            ? 'glm-4.7-flash'
                            : isSiliconFlow
                            ? 'Qwen/Qwen2.5-7B-Instruct'
                            : 'coding-glm-5.3-free'
                        }
                        className="w-full rounded-lg bg-surface-card px-3 py-2 text-zinc-100 font-mono text-xs hairline-border focus:outline-none focus:border-accent-indigo"
                      />
                      <datalist id={`models-${stage.key}`}>
                        {isGemini ? (
                          <>
                            <option value="gemma-4-26b-a4b-it">gemma-4-26b-a4b-it (High Quota | 30 RPM / 14,400 RPD)</option>
                            <option value="gemma-4-31b-it">gemma-4-31b-it (High Quota | 30 RPM / 14,400 RPD)</option>
                            <option value="gemini-3.5-flash-lite">gemini-3.5-flash-lite (Fastest ~0.4s | 500 RPD)</option>
                            <option value="gemini-3.1-flash-lite">gemini-3.1-flash-lite (Secondary Quota)</option>
                            <option value="gemini-2.5-flash">gemini-2.5-flash (Balanced)</option>
                          </>
                        ) : isGroq ? (
                          <>
                            <option value="qwen/qwen3.8-27b">qwen/qwen3.8-27b (Fastest Multilingual | 1K RPD)</option>
                            <option value="openai/gpt-oss-120b">openai/gpt-oss-120b (Deep Reasoning | 1K RPD)</option>
                            <option value="openai/gpt-oss-20b">openai/gpt-oss-20b (Lightweight | 1K RPD)</option>
                          </>
                        ) : isMistral ? (
                          <>
                            <option value="ministral-3b-2512">ministral-3b-2512 (Ultra-fast Gatekeeper | 12.5 RPS / 1.3M TPM)</option>
                            <option value="ministral-8b-2512">ministral-8b-2512 (High Quality | 3.13 RPS / 625K TPM)</option>
                            <option value="mistral-small-2603">mistral-small-2603 (Reasoning | 1.0 RPS)</option>
                            <option value="mistral-large-2512">mistral-large-2512 (Flagship | 1.0 RPS)</option>
                          </>
                        ) : isCloudflare ? (
                          <>
                            <option value="@cf/meta/llama-3.2-1b-instruct">@cf/meta/llama-3.2-1b-instruct (~4.1K RPD / 300 RPM)</option>
                            <option value="@cf/meta/llama-3.2-3b-instruct">@cf/meta/llama-3.2-3b-instruct (~2.4K RPD / 300 RPM)</option>
                            <option value="@cf/meta/llama-3.1-8b-instruct-fp8-fast">@cf/meta/llama-3.1-8b-instruct-fp8-fast (~2.2K RPD / 300 RPM)</option>
                          </>
                        ) : isZai ? (
                          <>
                            <option value="glm-4.7-flash">glm-4.7-flash (Permanent $0 Free Flash | High Speed)</option>
                            <option value="glm-4.5-flash">glm-4.5-flash (Permanent $0 Free Flash)</option>
                          </>
                        ) : isSiliconFlow ? (
                          <>
                            <option value="Qwen/Qwen2.5-7B-Instruct">Qwen/Qwen2.5-7B-Instruct (Free Catalog | 1K RPM)</option>
                            <option value="THUDM/glm-4-9b-chat">THUDM/glm-4-9b-chat (Free Catalog | 1K RPM)</option>
                            <option value="Qwen/Qwen2.5-14B-Instruct">Qwen/Qwen2.5-14B-Instruct (Free Catalog)</option>
                          </>
                        ) : (
                          <>
                            <option value="coding-glm-5.3-free">coding-glm-5.3-free (Strongest Flagship Reasoning | 1.05M Context)</option>
                          </>
                        )}
                      </datalist>
                      <div className="flex flex-wrap gap-1 mt-1.5">
                        {isGemini ? (
                          <>
                            <button
                              type="button"
                              onClick={() => handleModelChange(stage.key, 'gemma-4-26b-a4b-it')}
                              className="text-[9px] px-1.5 py-0.5 rounded bg-accent-cyan/10 hover:bg-accent-cyan/20 text-accent-cyan transition-colors"
                            >
                              🔥 gemma-4-26b (14.4K RPD)
                            </button>
                            <button
                              type="button"
                              onClick={() => handleModelChange(stage.key, 'gemma-4-31b-it')}
                              className="text-[9px] px-1.5 py-0.5 rounded bg-accent-cyan/10 hover:bg-accent-cyan/20 text-accent-cyan transition-colors"
                            >
                              🔥 gemma-4-31b (14.4K RPD)
                            </button>
                            <button
                              type="button"
                              onClick={() => handleModelChange(stage.key, 'gemini-3.5-flash-lite')}
                              className="text-[9px] px-1.5 py-0.5 rounded bg-white/5 hover:bg-white/10 text-zinc-400 hover:text-zinc-200 transition-colors"
                            >
                              ⚡ 3.5-flash-lite
                            </button>
                            <button
                              type="button"
                              onClick={() => handleModelChange(stage.key, 'gemini-3.1-flash-lite')}
                              className="text-[9px] px-1.5 py-0.5 rounded bg-white/5 hover:bg-white/10 text-zinc-400 hover:text-zinc-200 transition-colors"
                            >
                              ⚡ 3.1-flash-lite
                            </button>
                          </>
                        ) : isGroq ? (
                          <>
                            <button
                              type="button"
                              onClick={() => handleModelChange(stage.key, 'qwen/qwen3.8-27b')}
                              className="text-[9px] px-1.5 py-0.5 rounded bg-white/5 hover:bg-white/10 text-zinc-400 hover:text-zinc-200 transition-colors"
                            >
                              ⚡ qwen3.8-27b
                            </button>
                            <button
                              type="button"
                              onClick={() => handleModelChange(stage.key, 'openai/gpt-oss-120b')}
                              className="text-[9px] px-1.5 py-0.5 rounded bg-white/5 hover:bg-white/10 text-zinc-400 hover:text-zinc-200 transition-colors"
                            >
                              🧠 gpt-oss-120b
                            </button>
                          </>
                        ) : isMistral ? (
                          <>
                            <button
                              type="button"
                              onClick={() => handleModelChange(stage.key, 'ministral-3b-2512')}
                              className="text-[9px] px-1.5 py-0.5 rounded bg-amber-500/10 hover:bg-amber-500/20 text-amber-300 transition-colors"
                            >
                              ⚡ ministral-3b (12.5 RPS)
                            </button>
                            <button
                              type="button"
                              onClick={() => handleModelChange(stage.key, 'ministral-8b-2512')}
                              className="text-[9px] px-1.5 py-0.5 rounded bg-amber-500/10 hover:bg-amber-500/20 text-amber-300 transition-colors"
                            >
                              🧠 ministral-8b (3.13 RPS)
                            </button>
                            <button
                              type="button"
                              onClick={() => handleModelChange(stage.key, 'mistral-large-2512')}
                              className="text-[9px] px-1.5 py-0.5 rounded bg-amber-500/10 hover:bg-amber-500/20 text-amber-300 transition-colors"
                            >
                              👑 mistral-large
                            </button>
                            <button
                              type="button"
                              onClick={() => handleModelChange(stage.key, 'mistral-small-2603')}
                              className="text-[9px] px-1.5 py-0.5 rounded bg-amber-500/10 hover:bg-amber-500/20 text-amber-300 transition-colors"
                            >
                              mistral-small
                            </button>
                          </>
                        ) : isCloudflare ? (
                          <>
                            <button
                              type="button"
                              onClick={() => handleModelChange(stage.key, '@cf/meta/llama-3.2-1b-instruct')}
                              className="text-[9px] px-1.5 py-0.5 rounded bg-orange-500/10 hover:bg-orange-500/20 text-orange-300 transition-colors"
                            >
                              ⚡ llama-3.2-1b (~4.1K RPD)
                            </button>
                            <button
                              type="button"
                              onClick={() => handleModelChange(stage.key, '@cf/meta/llama-3.2-3b-instruct')}
                              className="text-[9px] px-1.5 py-0.5 rounded bg-orange-500/10 hover:bg-orange-500/20 text-orange-300 transition-colors"
                            >
                              llama-3.2-3b
                            </button>
                            <button
                              type="button"
                              onClick={() => handleModelChange(stage.key, '@cf/meta/llama-3.1-8b-instruct-fp8-fast')}
                              className="text-[9px] px-1.5 py-0.5 rounded bg-orange-500/10 hover:bg-orange-500/20 text-orange-300 transition-colors"
                            >
                              llama-3.1-8b-fp8
                            </button>
                          </>
                        ) : isZai ? (
                          <>
                            <button
                              type="button"
                              onClick={() => handleModelChange(stage.key, 'glm-4.7-flash')}
                              className="text-[9px] px-1.5 py-0.5 rounded bg-sky-500/10 hover:bg-sky-500/20 text-sky-300 transition-colors"
                            >
                              ⚡ glm-4.7-flash ($0)
                            </button>
                            <button
                              type="button"
                              onClick={() => handleModelChange(stage.key, 'glm-4.5-flash')}
                              className="text-[9px] px-1.5 py-0.5 rounded bg-sky-500/10 hover:bg-sky-500/20 text-sky-300 transition-colors"
                            >
                              glm-4.5-flash
                            </button>
                          </>
                        ) : isSiliconFlow ? (
                          <>
                            <button
                              type="button"
                              onClick={() => handleModelChange(stage.key, 'Qwen/Qwen2.5-7B-Instruct')}
                              className="text-[9px] px-1.5 py-0.5 rounded bg-purple-500/10 hover:bg-purple-500/20 text-purple-300 transition-colors"
                            >
                              ⚡ Qwen2.5-7B (1K RPM)
                            </button>
                            <button
                              type="button"
                              onClick={() => handleModelChange(stage.key, 'THUDM/glm-4-9b-chat')}
                              className="text-[9px] px-1.5 py-0.5 rounded bg-purple-500/10 hover:bg-purple-500/20 text-purple-300 transition-colors"
                            >
                              glm-4-9b-chat
                            </button>
                          </>
                        ) : (
                          <button
                            type="button"
                            onClick={() => handleModelChange(stage.key, 'coding-glm-5.3-free')}
                            className="text-[9px] px-1.5 py-0.5 rounded bg-accent-cyan/15 hover:bg-accent-cyan/25 text-accent-cyan font-semibold transition-colors"
                          >
                            🧠 coding-glm-5.3-free (Strongest Flagship • 1.05M)
                          </button>
                        )}
                      </div>
                    </div>

                    {/* Temperature slider */}
                    <div>
                      <div className="flex justify-between items-center mb-1">
                        <label className="text-zinc-400 font-medium">Temperature</label>
                        <span className="text-zinc-300 font-mono tabular text-[11px]">
                          {currentStageConfig.temperature}
                        </span>
                      </div>
                      <input
                        type="range"
                        min="0.0"
                        max="1.0"
                        step="0.05"
                        value={currentStageConfig.temperature}
                        onChange={(e) => handleTemperatureChange(stage.key, parseFloat(e.target.value))}
                        className="w-full accent-accent-indigo cursor-pointer"
                      />
                    </div>
                  </div>
                </div>

                {/* Fallback Chain Section */}
                <div className="space-y-2 pt-1">
                  <div className="flex flex-wrap items-center justify-between gap-2">
                    <div className="flex items-center space-x-2">
                      <ShieldAlert className="w-3.5 h-3.5 text-accent-cyan" />
                      <span className="text-xs font-semibold text-zinc-300">
                        Automatic Fallback Chain (Rate Limit &amp; Quota Failover)
                      </span>
                    </div>

                    <div className="flex items-center space-x-1.5">
                      {/* Quick preset button for Gemma 4 (Highest RPD) */}
                      {!fallbacks.some((f) => f.model_name?.includes('gemma')) && (
                        <button
                          type="button"
                          onClick={() => handleAddFallback(stage.key, 'gemini', 'gemma-4-26b-a4b-it')}
                          className="inline-flex items-center space-x-1 px-2 py-1 text-[10px] font-semibold text-emerald-400 bg-emerald-500/10 hover:bg-emerald-500/20 border border-emerald-500/30 rounded-md transition-colors"
                          title="Gemma 4 has the largest quota (14,400 RPD / 30 RPM)"
                        >
                          <span>+ Add Gemma 4 (14.4K RPD)</span>
                        </button>
                      )}
                      {/* Quick preset button for Mistral */}
                      {!fallbacks.some((f) => f.provider === 'mistral') && (
                        <button
                          type="button"
                          onClick={() =>
                            handleAddFallback(
                              stage.key,
                              'mistral',
                              stage.key === 'gatekeeper' ? 'ministral-3b-2512' : 'ministral-8b-2512'
                            )
                          }
                          className="inline-flex items-center space-x-1 px-2 py-1 text-[10px] font-semibold text-amber-300 bg-amber-500/10 hover:bg-amber-500/20 border border-amber-500/30 rounded-md transition-colors"
                          title="Mistral Free Plan: 12.5 RPS / 1.3M TPM on Ministral-3B"
                        >
                          <span>+ Add Mistral ({stage.key === 'gatekeeper' ? '12.5 RPS' : '8B'})</span>
                        </button>
                      )}
                      {/* Quick preset button for Cloudflare */}
                      {!fallbacks.some((f) => f.provider === 'cloudflare') && (
                        <button
                          type="button"
                          onClick={() =>
                            handleAddFallback(
                              stage.key,
                              'cloudflare',
                              stage.key === 'gatekeeper' ? '@cf/meta/llama-3.2-1b-instruct' : '@cf/meta/llama-3.1-8b-instruct-fp8-fast'
                            )
                          }
                          className="inline-flex items-center space-x-1 px-2 py-1 text-[10px] font-semibold text-orange-300 bg-orange-500/10 hover:bg-orange-500/20 border border-orange-500/30 rounded-md transition-colors"
                          title="Cloudflare Workers AI: 10,000 Neurons/day (~4.1K RPD on 1B)"
                        >
                          <span>+ Add Cloudflare (~4K RPD)</span>
                        </button>
                      )}
                      {/* Quick preset button */}
                      {isGemini && !fallbacks.some((f) => f.model_name === 'gemini-3.1-flash-lite') && (
                        <button
                          type="button"
                          onClick={() => handleAddFallback(stage.key, 'gemini', 'gemini-3.1-flash-lite')}
                          className="inline-flex items-center space-x-1 px-2 py-1 text-[10px] font-semibold text-accent-cyan bg-accent-cyan/10 hover:bg-accent-cyan/20 border border-accent-cyan/20 rounded-md transition-colors"
                        >
                          <span>+ Add 3.1 Flash Lite</span>
                        </button>
                      )}
                      {/* Quick preset button for AI Hub Mix free flagship: on scoring, summarization, situation_summary, and ONLY if primary is not AI Hub Mix and no AI Hub Mix in fallbacks */}
                      {(stage.key === 'scoring' || stage.key === 'summarization' || stage.key === 'situation_summary') && currentStageConfig.provider !== 'aihubmix' && !fallbacks.some((f) => f.provider === 'aihubmix') && (
                        <button
                          type="button"
                          onClick={() => handleAddFallback(stage.key, 'aihubmix', 'coding-glm-5.3-free')}
                          className="inline-flex items-center space-x-1 px-2 py-1 text-[10px] font-semibold text-accent-cyan bg-accent-cyan/15 hover:bg-accent-cyan/25 border border-accent-cyan/30 rounded-md transition-colors"
                          title="AI Hub Mix GLM-5.3 Flagship: Strongest model with 1.05M context reasoning"
                        >
                          <span>+ Add GLM-5.3 (1.05M Free)</span>
                        </button>
                      )}
                      <button
                        type="button"
                        onClick={() => handleAddFallback(stage.key)}
                        className="inline-flex items-center space-x-1 px-2.5 py-1 text-[11px] font-semibold text-zinc-200 bg-surface-raised hover:bg-white/10 hairline-border rounded-lg transition-colors"
                      >
                        <Plus className="w-3 h-3 text-accent-indigo" />
                        <span>Add Fallback</span>
                      </button>
                    </div>
                  </div>

                  <p className="text-[11px] text-zinc-500">
                    If the primary model hits rate limits (HTTP 429, RPM 18/15 max, RPD quota) or timeouts,
                    requests automatically cascade sequentially through these configured fallback models:
                  </p>

                  {/* Fallback Items */}
                  {fallbacks.length === 0 ? (
                    <div className="p-3 rounded-lg bg-surface-card/30 border border-dashed border-white/10 text-center text-xs text-zinc-500">
                      No fallbacks configured. When primary reaches rate limits, stage will directly use deterministic offline heuristics.
                    </div>
                  ) : (
                    <div className="space-y-2">
                      {fallbacks.map((fb, idx) => {
                        const fbIsGemini = fb.provider === 'gemini';
                        const fbIsGroq = fb.provider === 'groq';
                        const fbIsMistral = fb.provider === 'mistral';
                        const fbIsCloudflare = fb.provider === 'cloudflare';
                        const fbIsZai = fb.provider === 'zai';
                        const fbIsSiliconFlow = fb.provider === 'siliconflow';
                        const fbIsAIHubMix = fb.provider === 'aihubmix';
                        return (
                          <div
                            key={idx}
                            className="p-3 rounded-lg bg-surface-card/40 hairline-border flex flex-col sm:flex-row items-stretch sm:items-center gap-3 text-xs"
                          >
                            <div className="flex items-center space-x-2 shrink-0">
                              <CornerDownRight className="w-3.5 h-3.5 text-zinc-500" />
                              <span className="px-2 py-0.5 rounded text-[10px] font-mono font-semibold bg-surface-raised hairline-border text-zinc-300">
                                Fallback #{idx + 1}
                              </span>
                            </div>

                            {/* Provider engine */}
                            <div className="sm:w-44 shrink-0">
                              <select
                                value={fb.provider}
                                onChange={(e) =>
                                  handleUpdateFallback(stage.key, idx, 'provider', e.target.value as AIProvider)
                                }
                                className="w-full rounded-md bg-canvas-recessed px-2.5 py-1.5 text-zinc-100 hairline-border focus:outline-none focus:border-accent-indigo text-xs"
                              >
                                <option value="groq">Groq Cloud</option>
                                <option value="gemini">Gemini API</option>
                                <option value="mistral">Mistral AI</option>
                                <option value="cloudflare">Cloudflare Workers AI</option>
                                <option value="zai">Z.ai (Zhipu)</option>
                                <option value="siliconflow">SiliconFlow</option>
                                {(stage.key === 'scoring' || stage.key === 'summarization' || stage.key === 'situation_summary') && currentStageConfig.provider !== 'aihubmix' && (
                                  <option value="aihubmix">AI Hub Mix (GLM-5.3 Flagship)</option>
                                )}
                              </select>
                            </div>

                            {/* Model name */}
                            <div className="flex-1">
                              <input
                                type="text"
                                value={fb.model_name}
                                onChange={(e) => handleUpdateFallback(stage.key, idx, 'model_name', e.target.value)}
                                placeholder={
                                  fbIsGemini
                                    ? 'gemma-4-26b-a4b-it'
                                    : fbIsGroq
                                    ? 'qwen/qwen3.8-27b'
                                    : fbIsMistral
                                    ? 'ministral-3b-2512'
                                    : fbIsCloudflare
                                    ? '@cf/meta/llama-3.2-1b-instruct'
                                    : fbIsZai
                                    ? 'glm-4.7-flash'
                                    : fbIsSiliconFlow
                                    ? 'Qwen/Qwen2.5-7B-Instruct'
                                    : 'coding-glm-5.3-free'
                                }
                                className="w-full rounded-md bg-canvas-recessed px-2.5 py-1.5 text-zinc-100 font-mono text-xs hairline-border focus:outline-none focus:border-accent-indigo"
                              />
                              <div className="flex flex-wrap items-center justify-between gap-1 mt-1">
                                <div className="flex flex-wrap gap-1">
                                  {fbIsGemini ? (
                                    <>
                                      <button
                                        type="button"
                                        onClick={() => handleUpdateFallback(stage.key, idx, 'model_name', 'gemini-3.1-flash-lite')}
                                        className="text-[9px] px-1.5 py-0.5 rounded bg-white/5 hover:bg-white/10 text-zinc-400 hover:text-zinc-200"
                                      >
                                        3.1-flash-lite
                                      </button>
                                      <button
                                        type="button"
                                        onClick={() => handleUpdateFallback(stage.key, idx, 'model_name', 'gemini-2.5-flash')}
                                        className="text-[9px] px-1.5 py-0.5 rounded bg-white/5 hover:bg-white/10 text-zinc-400 hover:text-zinc-200"
                                      >
                                        2.5-flash
                                      </button>
                                      <button
                                        type="button"
                                        onClick={() => handleUpdateFallback(stage.key, idx, 'model_name', 'gemma-4-26b-a4b-it')}
                                        className="text-[9px] px-1.5 py-0.5 rounded bg-emerald-500/10 hover:bg-emerald-500/20 text-emerald-400 hover:text-emerald-300 font-semibold"
                                        title="Google AI Studio Gemma 4: 14,400 RPD / 30 RPM"
                                      >
                                        🔥 gemma-4-26b
                                      </button>
                                      <button
                                        type="button"
                                        onClick={() => handleUpdateFallback(stage.key, idx, 'model_name', 'gemma-4-31b-it')}
                                        className="text-[9px] px-1.5 py-0.5 rounded bg-emerald-500/10 hover:bg-emerald-500/20 text-emerald-400 hover:text-emerald-300 font-semibold"
                                        title="Google AI Studio Gemma 4: 14,400 RPD / 30 RPM"
                                      >
                                        🔥 gemma-4-31b
                                      </button>
                                    </>
                                  ) : fbIsGroq ? (
                                    <>
                                      <button
                                        type="button"
                                        onClick={() => handleUpdateFallback(stage.key, idx, 'model_name', 'qwen/qwen3.8-27b')}
                                        className="text-[9px] px-1.5 py-0.5 rounded bg-white/5 hover:bg-white/10 text-zinc-400 hover:text-zinc-200"
                                      >
                                        qwen3.8-27b
                                      </button>
                                      <button
                                        type="button"
                                        onClick={() => handleUpdateFallback(stage.key, idx, 'model_name', 'openai/gpt-oss-120b')}
                                        className="text-[9px] px-1.5 py-0.5 rounded bg-white/5 hover:bg-white/10 text-zinc-400 hover:text-zinc-200"
                                      >
                                        gpt-oss-120b
                                      </button>
                                    </>
                                  ) : fbIsMistral ? (
                                    <>
                                      <button
                                        type="button"
                                        onClick={() => handleUpdateFallback(stage.key, idx, 'model_name', 'ministral-14b-2512')}
                                        className="text-[9px] px-1.5 py-0.5 rounded bg-amber-500/10 hover:bg-amber-500/20 text-amber-300 font-semibold"
                                      >
                                        🎯 ministral-14b (30 RPM)
                                      </button>
                                      <button
                                        type="button"
                                        onClick={() => handleUpdateFallback(stage.key, idx, 'model_name', 'codestral-2508')}
                                        className="text-[9px] px-1.5 py-0.5 rounded bg-amber-500/10 hover:bg-amber-500/20 text-amber-300 font-semibold"
                                      >
                                        💻 codestral (125 RPM)
                                      </button>
                                      <button
                                        type="button"
                                        onClick={() => handleUpdateFallback(stage.key, idx, 'model_name', 'ministral-3b-2512')}
                                        className="text-[9px] px-1.5 py-0.5 rounded bg-amber-500/10 hover:bg-amber-500/20 text-amber-300"
                                      >
                                        ⚡ 3b (12.5 RPS)
                                      </button>
                                      <button
                                        type="button"
                                        onClick={() => handleUpdateFallback(stage.key, idx, 'model_name', 'ministral-8b-2512')}
                                        className="text-[9px] px-1.5 py-0.5 rounded bg-amber-500/10 hover:bg-amber-500/20 text-amber-300"
                                      >
                                        🧠 8b (3.13 RPS)
                                      </button>
                                    </>
                                  ) : fbIsCloudflare ? (
                                    <>
                                      <button
                                        type="button"
                                        onClick={() => handleUpdateFallback(stage.key, idx, 'model_name', '@cf/meta/llama-3.2-1b-instruct')}
                                        className="text-[9px] px-1.5 py-0.5 rounded bg-orange-500/10 hover:bg-orange-500/20 text-orange-300"
                                      >
                                        ⚡ llama-1b (~4.1K RPD)
                                      </button>
                                      <button
                                        type="button"
                                        onClick={() => handleUpdateFallback(stage.key, idx, 'model_name', '@cf/meta/llama-3.2-3b-instruct')}
                                        className="text-[9px] px-1.5 py-0.5 rounded bg-orange-500/10 hover:bg-orange-500/20 text-orange-300"
                                      >
                                        llama-3b
                                      </button>
                                      <button
                                        type="button"
                                        onClick={() => handleUpdateFallback(stage.key, idx, 'model_name', '@cf/meta/llama-3.1-8b-instruct-fp8-fast')}
                                        className="text-[9px] px-1.5 py-0.5 rounded bg-orange-500/10 hover:bg-orange-500/20 text-orange-300"
                                      >
                                        llama-8b-fp8
                                      </button>
                                    </>
                                  ) : fbIsZai ? (
                                    <>
                                      <button
                                        type="button"
                                        onClick={() => handleUpdateFallback(stage.key, idx, 'model_name', 'glm-4.7-flash')}
                                        className="text-[9px] px-1.5 py-0.5 rounded bg-sky-500/10 hover:bg-sky-500/20 text-sky-300"
                                      >
                                        ⚡ glm-4.7-flash ($0)
                                      </button>
                                      <button
                                        type="button"
                                        onClick={() => handleUpdateFallback(stage.key, idx, 'model_name', 'glm-4.5-flash')}
                                        className="text-[9px] px-1.5 py-0.5 rounded bg-sky-500/10 hover:bg-sky-500/20 text-sky-300"
                                      >
                                        glm-4.5-flash
                                      </button>
                                    </>
                                  ) : fbIsSiliconFlow ? (
                                    <>
                                      <button
                                        type="button"
                                        onClick={() => handleUpdateFallback(stage.key, idx, 'model_name', 'Qwen/Qwen2.5-7B-Instruct')}
                                        className="text-[9px] px-1.5 py-0.5 rounded bg-purple-500/10 hover:bg-purple-500/20 text-purple-300"
                                      >
                                        ⚡ Qwen2.5-7B (1K RPM)
                                      </button>
                                      <button
                                        type="button"
                                        onClick={() => handleUpdateFallback(stage.key, idx, 'model_name', 'THUDM/glm-4-9b-chat')}
                                        className="text-[9px] px-1.5 py-0.5 rounded bg-purple-500/10 hover:bg-purple-500/20 text-purple-300"
                                      >
                                        glm-4-9b
                                      </button>
                                    </>
                                  ) : (
                                    <button
                                      type="button"
                                      onClick={() => handleUpdateFallback(stage.key, idx, 'model_name', 'coding-glm-5.3-free')}
                                      className="text-[9px] px-1.5 py-0.5 rounded bg-accent-cyan/15 hover:bg-accent-cyan/25 text-accent-cyan font-semibold"
                                    >
                                      🧠 coding-glm-5.3-free (1.05M)
                                    </button>
                                  )}
                                </div>
                                <a
                                  href={
                                    fbIsGemini
                                      ? 'https://aistudio.google.com/rate-limit'
                                      : fbIsGroq
                                      ? 'https://console.groq.com/settings/limits'
                                      : fbIsMistral
                                      ? 'https://console.mistral.ai/limits/'
                                      : fbIsCloudflare
                                      ? 'https://developers.cloudflare.com/workers-ai/models/'
                                      : fbIsZai
                                      ? 'https://open.bigmodel.cn/dev/howuse/rate-limits'
                                      : fbIsSiliconFlow
                                      ? 'https://cloud.siliconflow.com/models'
                                      : 'https://aihubmix.com/models/free'
                                  }
                                  target="_blank"
                                  rel="noopener noreferrer"
                                  title="Open provider rate limits"
                                  className="inline-flex items-center space-x-0.5 text-[9px] font-mono text-accent-cyan/80 hover:text-accent-cyan hover:underline"
                                >
                                  <span>
                                    {fbIsGemini
                                      ? 'Gemini Limits'
                                      : fbIsGroq
                                      ? 'Groq Limits'
                                      : fbIsMistral
                                      ? 'Mistral Limits'
                                      : fbIsCloudflare
                                      ? 'Cloudflare Limits'
                                      : fbIsZai
                                      ? 'Z.ai Limits'
                                      : fbIsSiliconFlow
                                      ? 'SiliconFlow Limits'
                                      : 'AIHubMix Limits'}
                                  </span>
                                  <ExternalLink className="w-2.5 h-2.5" />
                                </a>
                              </div>
                            </div>

                            {/* Actions (Move & Delete) */}
                            <div className="flex items-center space-x-1 shrink-0 self-end sm:self-center">
                              <button
                                type="button"
                                disabled={idx === 0}
                                onClick={() => handleMoveFallback(stage.key, idx, 'up')}
                                title="Move Priority Up"
                                className="p-1 rounded hover:bg-white/10 text-zinc-400 hover:text-zinc-200 disabled:opacity-30 disabled:pointer-events-none"
                              >
                                <ChevronUp className="w-3.5 h-3.5" />
                              </button>
                              <button
                                type="button"
                                disabled={idx === fallbacks.length - 1}
                                onClick={() => handleMoveFallback(stage.key, idx, 'down')}
                                title="Move Priority Down"
                                className="p-1 rounded hover:bg-white/10 text-zinc-400 hover:text-zinc-200 disabled:opacity-30 disabled:pointer-events-none"
                              >
                                <ChevronDown className="w-3.5 h-3.5" />
                              </button>
                              <button
                                type="button"
                                onClick={() => handleRemoveFallback(stage.key, idx)}
                                title="Remove Fallback"
                                className="p-1 rounded hover:bg-rose-500/20 text-zinc-400 hover:text-rose-300 ml-1"
                              >
                                <Trash2 className="w-3.5 h-3.5" />
                              </button>
                            </div>
                          </div>
                        );
                      })}
                    </div>
                  )}
                </div>
              </div>
            );
          })}
        </div>

        <div className="flex items-center justify-between pt-2 hairline-t text-xs">
          <span className="text-zinc-500 font-mono text-[11px]">
            Changes apply dynamically on the next processed event batch via Redis reload signal.
          </span>
          <div className="flex items-center space-x-2">
            <button
              type="button"
              onClick={handleReset}
              disabled={loading}
              className="inline-flex items-center space-x-1.5 px-3 py-2 text-xs font-medium text-zinc-300 hover:text-white bg-zinc-800 hover:bg-zinc-700 rounded-lg pressable border border-zinc-700 shadow-sm"
              title="Reset matrix to system code defaults"
            >
              <RotateCcw className="w-3.5 h-3.5" />
              <span>Reset Defaults</span>
            </button>
            <button
              type="submit"
              disabled={loading}
              className="inline-flex items-center space-x-1.5 px-4 py-2 text-xs font-semibold text-white bg-accent-indigo hover:bg-accent-indigo/90 rounded-lg pressable shadow-sm"
            >
              <Check className="w-4 h-4" />
              <span>{loading ? 'Updating...' : 'Save AI Configuration'}</span>
            </button>
          </div>
        </div>
      </form>
    </div>
  );
}
