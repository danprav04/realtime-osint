'use client';

import React, { useState } from 'react';
import { Cpu, Check, AlertCircle, Save, Sparkles, RefreshCw } from 'lucide-react';
import { AIPipelineSettings, AIStageConfig } from '@/lib/types';
import { updateAIPipelineSettings } from '@/lib/api';

interface AIConfigMatrixProps {
  initialSettings: AIPipelineSettings | null;
  onRefresh: () => void;
}

export function AIConfigMatrix({ initialSettings, onRefresh }: AIConfigMatrixProps) {
  const defaultStage = (provider: 'gemini' | 'groq', model: string): AIStageConfig => ({
    provider,
    model_name: model,
    temperature: 0.2,
  });

  const [settings, setSettings] = useState<AIPipelineSettings>(
    initialSettings || {
      extraction: defaultStage('groq', 'qwen/qwen3.8-27b'),
      scoring: defaultStage('gemini', 'gemini-3.5-flash-lite'),
      summarization: defaultStage('groq', 'openai/gpt-oss-120b'),
      visuals: defaultStage('groq', 'openai/gpt-oss-20b'),
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

  const handleSave = async (e: React.FormEvent) => {
    e.preventDefault();
    setLoading(true);
    setFeedback(null);
    try {
      await updateAIPipelineSettings(settings);
      setFeedback({
        type: 'success',
        message: 'AI Pipeline configuration updated! Hot-reloaded across background workers.',
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
              AI Processing Pipeline (Per-Stage Model Matrix)
            </h3>
            <p className="text-xs text-zinc-400">
              Independently configure model provider (Google Gemini vs Groq Cloud) for each of the 4 pipeline stages
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

            return (
              <div
                key={stage.key}
                className="p-4 rounded-xl bg-canvas-recessed hairline-border space-y-3 hover:border-white/10 transition-colors"
              >
                <div className="flex flex-wrap items-center justify-between gap-2 pb-2 border-b border-white/5">
                  <div>
                    <h4 className="text-xs sm:text-sm font-semibold text-zinc-200">
                      {stage.name}
                    </h4>
                    <p className="text-[11px] text-zinc-400">{stage.desc}</p>
                  </div>
                  <span className="px-2 py-0.5 rounded text-[10px] font-mono bg-surface-raised hairline-border text-zinc-400">
                    {stage.badge}
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
                        {isGemini ? 'Free: 15 RPM / 500 RPD' : 'Free: 30 RPM / 1K RPD'}
                      </span>
                    </div>
                    <input
                      type="text"
                      value={currentStageConfig.model_name}
                      onChange={(e) => handleModelChange(stage.key, e.target.value)}
                      placeholder={isGemini ? 'gemini-3.5-flash-lite' : 'qwen/qwen3.8-27b'}
                      className="w-full rounded-lg bg-surface-card px-3 py-2 text-zinc-100 font-mono text-xs hairline-border focus:outline-none focus:border-accent-indigo"
                    />
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
            );
          })}
        </div>

        <div className="flex items-center justify-between pt-2 hairline-t text-xs">
          <span className="text-zinc-500 font-mono text-[11px]">
            Changes apply dynamically on the next processed event batch.
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
