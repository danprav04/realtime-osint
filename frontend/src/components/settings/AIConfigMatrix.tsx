'use client';

import React, { useState } from 'react';
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
} from 'lucide-react';
import { AIPipelineSettings, AIStageConfig, AIFallbackConfig } from '@/lib/types';
import { updateAIPipelineSettings } from '@/lib/api';

interface AIConfigMatrixProps {
  initialSettings: AIPipelineSettings | null;
  onRefresh: () => void;
}

export function AIConfigMatrix({ initialSettings, onRefresh }: AIConfigMatrixProps) {
  const defaultStage = (
    provider: 'gemini' | 'groq',
    model: string,
    fallbacks: AIFallbackConfig[] = []
  ): AIStageConfig => ({
    provider,
    model_name: model,
    temperature: provider === 'gemini' ? 0.1 : 0.2,
    fallbacks,
  });

  const [settings, setSettings] = useState<AIPipelineSettings>(
    initialSettings || {
      extraction: defaultStage('groq', 'qwen/qwen3.8-27b', [
        { provider: 'gemini', model_name: 'gemini-3.5-flash-lite', temperature: 0.2 },
        { provider: 'gemini', model_name: 'gemini-3.1-flash-lite', temperature: 0.2 },
        { provider: 'groq', model_name: 'openai/gpt-oss-120b', temperature: 0.2 },
      ]),
      scoring: defaultStage('gemini', 'gemini-3.5-flash-lite', [
        { provider: 'gemini', model_name: 'gemini-3.1-flash-lite', temperature: 0.1 },
        { provider: 'groq', model_name: 'openai/gpt-oss-120b', temperature: 0.1 },
        { provider: 'groq', model_name: 'qwen/qwen3.8-27b', temperature: 0.1 },
      ]),
      summarization: defaultStage('groq', 'openai/gpt-oss-120b', [
        { provider: 'groq', model_name: 'qwen/qwen3.8-27b', temperature: 0.2 },
        { provider: 'gemini', model_name: 'gemini-3.5-flash-lite', temperature: 0.2 },
        { provider: 'gemini', model_name: 'gemini-3.1-flash-lite', temperature: 0.2 },
        { provider: 'groq', model_name: 'openai/gpt-oss-20b', temperature: 0.2 },
      ]),
      visuals: defaultStage('groq', 'openai/gpt-oss-20b', [
        { provider: 'groq', model_name: 'qwen/qwen3.8-27b', temperature: 0.2 },
        { provider: 'gemini', model_name: 'gemini-3.5-flash-lite', temperature: 0.2 },
        { provider: 'gemini', model_name: 'gemini-3.1-flash-lite', temperature: 0.2 },
      ]),
    }
  );

  const [loading, setLoading] = useState(false);
  const [feedback, setFeedback] = useState<{ type: 'success' | 'error'; message: string } | null>(null);

  const stages = [
    {
      key: 'extraction' as const,
      name: 'Stage 1: Parsing, Extraction & Translation',
      desc: 'Normalizes entity facts and translates non-English (Arabic/Farsi) to English',
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
      name: 'Stage 3: Intelligence Summarization',
      desc: 'Generates concise tactical summaries and periodic situational digests',
      badge: 'Executive Briefings',
    },
    {
      key: 'visuals' as const,
      name: 'Stage 4: Visuals, Trends & Narrative',
      desc: 'Detects velocity clusters, source hotspots, and tactical trend narratives',
      badge: 'Visual Analytics',
    },
  ];

  const handleProviderChange = (stageKey: keyof AIPipelineSettings, provider: 'gemini' | 'groq') => {
    const defaultModel = provider === 'gemini' ? 'gemini-3.5-flash-lite' : 'qwen/qwen3.8-27b';
    setSettings((prev) => ({
      ...prev,
      [stageKey]: {
        ...prev[stageKey],
        provider,
        model_name: defaultModel,
      },
    }));
  };

  const handleModelChange = (stageKey: keyof AIPipelineSettings, model_name: string) => {
    setSettings((prev) => ({
      ...prev,
      [stageKey]: {
        ...prev[stageKey],
        model_name,
      },
    }));
  };

  const handleTemperatureChange = (stageKey: keyof AIPipelineSettings, temperature: number) => {
    setSettings((prev) => ({
      ...prev,
      [stageKey]: {
        ...prev[stageKey],
        temperature,
      },
    }));
  };

  // Fallback management handlers
  const handleAddFallback = (
    stageKey: keyof AIPipelineSettings,
    suggestedProvider?: 'gemini' | 'groq',
    suggestedModel?: string
  ) => {
    setSettings((prev) => {
      const stage = prev[stageKey];
      const existing = stage.fallbacks || [];
      const currentProvider = stage.provider;
      const nextProvider = suggestedProvider || (currentProvider === 'gemini' ? 'gemini' : 'groq');
      const nextModel = suggestedModel || (nextProvider === 'gemini' ? 'gemini-3.1-flash-lite' : 'qwen/qwen3.8-27b');

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
      const existing = [...(stage.fallbacks || [])];
      if (existing[index]) {
        const updated = {
          ...existing[index],
          [field]: value,
        };
        if (field === 'provider') {
          updated.model_name = value === 'gemini' ? 'gemini-3.1-flash-lite' : 'qwen/qwen3.8-27b';
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
              Configure primary models and unlimited automatic fallback chains across Google Gemini and Groq Cloud
            </p>
          </div>
        </div>

        <button
          onClick={handleSave}
          disabled={loading}
          className="inline-flex items-center space-x-1.5 px-3.5 py-1.5 text-xs font-semibold text-white bg-accent-indigo hover:bg-accent-indigo/90 rounded-lg pressable shadow-sm"
        >
          <Save className="w-4 h-4" />
          <span>{loading ? 'Saving...' : 'Save AI Matrix'}</span>
        </button>
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
            const currentStageConfig = settings[stage.key];
            const isGemini = currentStageConfig.provider === 'gemini';
            const fallbacks = currentStageConfig.fallbacks || [];

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
                          handleProviderChange(stage.key, e.target.value as 'gemini' | 'groq')
                        }
                        className="w-full rounded-lg bg-surface-card px-3 py-2 text-zinc-100 hairline-border focus:outline-none focus:border-accent-indigo"
                      >
                        <option value="gemini">Google Gemini API</option>
                        <option value="groq">Groq Cloud (LPU Inference)</option>
                      </select>
                    </div>

                    {/* Model name input */}
                    <div>
                      <div className="flex justify-between items-center mb-1">
                        <label className="text-zinc-400 font-medium">Model Name</label>
                        <span className="text-[10px] text-zinc-500 font-mono">
                          {isGemini ? 'Flash: 500 RPD' : 'Free: 30 RPM / 1K RPD'}
                        </span>
                      </div>
                      <input
                        type="text"
                        list={`models-${stage.key}`}
                        value={currentStageConfig.model_name}
                        onChange={(e) => handleModelChange(stage.key, e.target.value)}
                        placeholder={isGemini ? 'gemini-3.5-flash-lite' : 'qwen/qwen3.8-27b'}
                        className="w-full rounded-lg bg-surface-card px-3 py-2 text-zinc-100 font-mono text-xs hairline-border focus:outline-none focus:border-accent-indigo"
                      />
                      <datalist id={`models-${stage.key}`}>
                        {isGemini ? (
                          <>
                            <option value="gemini-3.5-flash-lite">gemini-3.5-flash-lite (Fastest ~0.4s | 500 RPD)</option>
                            <option value="gemini-3.1-flash-lite">gemini-3.1-flash-lite (Secondary Quota)</option>
                            <option value="gemini-2.5-flash">gemini-2.5-flash (Balanced)</option>
                            <option value="gemma-4-26b-a4b-it">gemma-4-26b-a4b-it (High Quota | 14,400 RPD)</option>
                          </>
                        ) : (
                          <>
                            <option value="qwen/qwen3.8-27b">qwen/qwen3.8-27b (Fastest Multilingual | 1K RPD)</option>
                            <option value="openai/gpt-oss-120b">openai/gpt-oss-120b (Deep Reasoning | 1K RPD)</option>
                            <option value="openai/gpt-oss-20b">openai/gpt-oss-20b (Lightweight | 1K RPD)</option>
                          </>
                        )}
                      </datalist>
                      <div className="flex flex-wrap gap-1 mt-1.5">
                        {isGemini ? (
                          <>
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
                        ) : (
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
                                  handleUpdateFallback(stage.key, idx, 'provider', e.target.value as 'gemini' | 'groq')
                                }
                                className="w-full rounded-md bg-canvas-recessed px-2.5 py-1.5 text-zinc-100 hairline-border focus:outline-none focus:border-accent-indigo text-xs"
                              >
                                <option value="gemini">Gemini API</option>
                                <option value="groq">Groq Cloud</option>
                              </select>
                            </div>

                            {/* Model name */}
                            <div className="flex-1">
                              <input
                                type="text"
                                value={fb.model_name}
                                onChange={(e) => handleUpdateFallback(stage.key, idx, 'model_name', e.target.value)}
                                placeholder={fbIsGemini ? 'gemini-3.1-flash-lite' : 'qwen/qwen3.8-27b'}
                                className="w-full rounded-md bg-canvas-recessed px-2.5 py-1.5 text-zinc-100 font-mono text-xs hairline-border focus:outline-none focus:border-accent-indigo"
                              />
                              <div className="flex flex-wrap gap-1 mt-1">
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
                                      className="text-[9px] px-1.5 py-0.5 rounded bg-white/5 hover:bg-white/10 text-zinc-400 hover:text-zinc-200"
                                    >
                                      gemma-4-26b
                                    </button>
                                  </>
                                ) : (
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
                                )}
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
          <button
            type="submit"
            disabled={loading}
            className="inline-flex items-center space-x-1.5 px-4 py-2 text-xs font-semibold text-white bg-accent-indigo hover:bg-accent-indigo/90 rounded-lg pressable shadow-sm"
          >
            <Check className="w-4 h-4" />
            <span>{loading ? 'Updating...' : 'Save AI Configuration'}</span>
          </button>
        </div>
      </form>
    </div>
  );
}
