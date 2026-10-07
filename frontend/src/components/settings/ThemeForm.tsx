'use client';

import React, { useState } from 'react';
import { Layers, Check, Plus, AlertCircle, Sparkles, Sliders } from 'lucide-react';
import { Theme } from '@/lib/types';
import { updateTheme, activateTheme, createTheme } from '@/lib/api';

interface ThemeFormProps {
  themes: Theme[];
  activeTheme: Theme | null;
  onRefresh: () => void;
}

export function ThemeForm({ themes, activeTheme, onRefresh }: ThemeFormProps) {
  const [selectedThemeId, setSelectedThemeId] = useState<string>(activeTheme?.id || '');
  const [isEditing, setIsEditing] = useState(false);
  const [isCreating, setIsCreating] = useState(false);
  const [loading, setLoading] = useState(false);
  const [feedback, setFeedback] = useState<{ type: 'success' | 'error'; message: string } | null>(null);

  // Form states
  const currentTheme = themes.find((t) => t.id === selectedThemeId) || activeTheme || themes[0];
  const [name, setName] = useState(currentTheme?.name || '');
  const [description, setDescription] = useState(currentTheme?.description || '');
  const [keywordsText, setKeywordsText] = useState((currentTheme?.keywords || []).join(', '));
  const [entitiesText, setEntitiesText] = useState((currentTheme?.entities || []).join(', '));
  const [scoringPrompt, setScoringPrompt] = useState(currentTheme?.scoring_prompt || '');
  const [alertThreshold, setAlertThreshold] = useState(currentTheme?.alert_threshold ?? 7);

  // Update form fields when selected theme changes
  const handleSelectTheme = (theme: Theme) => {
    setSelectedThemeId(theme.id);
    setName(theme.name);
    setDescription(theme.description || '');
    setKeywordsText((theme.keywords || []).join(', '));
    setEntitiesText((theme.entities || []).join(', '));
    setScoringPrompt(theme.scoring_prompt);
    setAlertThreshold(theme.alert_threshold ?? 7);
    setIsEditing(false);
    setIsCreating(false);
    setFeedback(null);
  };

  const handleActivateTheme = async (themeId: string) => {
    setLoading(true);
    setFeedback(null);
    try {
      await activateTheme(themeId);
      setFeedback({ type: 'success', message: 'Theme activated successfully! Pipeline reloaded.' });
      onRefresh();
    } catch (err: any) {
      setFeedback({ type: 'error', message: err.message || 'Failed to activate theme' });
    } finally {
      setLoading(false);
    }
  };

  const handleSaveTheme = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!currentTheme) return;

    setLoading(true);
    setFeedback(null);

    const keywords = keywordsText
      .split(',')
      .map((k) => k.trim())
      .filter(Boolean);
    const entities = entitiesText
      .split(',')
      .map((e) => e.trim())
      .filter(Boolean);

    try {
      if (isCreating) {
        const slug = name.toLowerCase().replace(/[^a-z0-9]+/g, '-');
        await createTheme({
          name,
          slug,
          description,
          keywords,
          entities,
          scoring_prompt: scoringPrompt,
          alert_threshold: alertThreshold,
          is_active: false,
        });
        setFeedback({ type: 'success', message: 'New theme created successfully!' });
        setIsCreating(false);
      } else {
        await updateTheme(currentTheme.id, {
          name,
          description,
          keywords,
          entities,
          scoring_prompt: scoringPrompt,
          alert_threshold: alertThreshold,
        });
        setFeedback({
          type: 'success',
          message: 'Theme settings updated! AI scoring prompt reloaded immediately.',
        });
        setIsEditing(false);
      }
      onRefresh();
    } catch (err: any) {
      setFeedback({ type: 'error', message: err.message || 'Failed to save theme' });
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="card-slick rounded-xl p-6 space-y-6">
      {/* Header */}
      <div className="flex flex-wrap items-center justify-between gap-4 pb-4 hairline-b">
        <div className="flex items-center space-x-3">
          <div className="p-2.5 rounded-lg bg-accent-amber/15 text-accent-amber border border-accent-amber/30">
            <Layers className="w-5 h-5" />
          </div>
          <div>
            <h3 className="text-base font-semibold text-zinc-100 tracking-tight">
              Theme System (Zero-Code Topic Configuration)
            </h3>
            <p className="text-xs text-zinc-400">
              Change monitoring focus & AI scoring prompts dynamically without server restarts
            </p>
          </div>
        </div>

        <button
          onClick={() => {
            setIsCreating(true);
            setIsEditing(true);
            setName('');
            setDescription('');
            setKeywordsText('');
            setEntitiesText('');
            setScoringPrompt(
              'Analyze the intelligence event for strategic impact, geopolitical escalation, military movements, and civilian infrastructure risk. Score 1-10.'
            );
            setAlertThreshold(7);
          }}
          className="inline-flex items-center space-x-1.5 px-3 py-1.5 text-xs font-semibold text-white bg-accent-indigo hover:bg-accent-indigo/90 rounded-lg pressable shadow-sm"
        >
          <Plus className="w-4 h-4" />
          <span>New Theme</span>
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

      {/* Theme selector tabs */}
      <div>
        <label className="block text-xs font-medium text-zinc-400 mb-2">Available Intelligence Themes</label>
        <div className="flex flex-wrap gap-2">
          {themes.map((theme) => {
            const isThemeActive = theme.is_active;
            const isSelected = (currentTheme?.id === theme.id) && !isCreating;

            return (
              <button
                key={theme.id}
                type="button"
                onClick={() => handleSelectTheme(theme)}
                className={`flex items-center space-x-2 px-3.5 py-2 rounded-lg text-xs font-medium transition-all hairline-border pressable ${
                  isSelected
                    ? 'bg-surface-raised text-zinc-100 border-accent-amber/50 shadow-sm'
                    : 'bg-canvas-recessed text-zinc-400 hover:text-zinc-200 hover:bg-surface-card'
                }`}
              >
                <span>{theme.name}</span>
                {isThemeActive && (
                  <span className="inline-flex items-center space-x-1 px-1.5 py-0.5 rounded text-[10px] bg-emerald-500/15 text-emerald-400 border border-emerald-500/30">
                    <span className="w-1.5 h-1.5 rounded-full bg-emerald-400 animate-pulse" />
                    <span>ACTIVE</span>
                  </span>
                )}
              </button>
            );
          })}
        </div>
      </div>

      {/* Theme Details / Edit Form */}
      {currentTheme && (
        <form onSubmit={handleSaveTheme} className="space-y-4 pt-2">
          <div className="flex items-center justify-between pb-2 border-b border-white/5">
            <div className="flex items-center space-x-2">
              <span className="text-xs font-semibold text-zinc-300">
                {isCreating ? 'Creating New Theme' : `Configuring Theme: ${currentTheme.name}`}
              </span>
              {currentTheme.is_active && !isCreating && (
                <span className="text-[11px] text-emerald-400 font-mono">(Live Active Ingestion Target)</span>
              )}
            </div>

            {!currentTheme.is_active && !isCreating && (
              <button
                type="button"
                disabled={loading}
                onClick={() => handleActivateTheme(currentTheme.id)}
                className="px-3 py-1 text-xs font-semibold text-emerald-300 bg-emerald-500/15 hover:bg-emerald-500/25 border border-emerald-500/30 rounded-md transition-colors"
              >
                {loading ? 'Activating...' : 'Set as Active Theme'}
              </button>
            )}
          </div>

          <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
            <div>
              <label className="block text-xs font-medium text-zinc-300 mb-1">Theme Display Name</label>
              <input
                type="text"
                value={name}
                onChange={(e) => setName(e.target.value)}
                required
                className="w-full rounded-lg bg-canvas-recessed px-3.5 py-2 text-xs text-zinc-100 hairline-border focus:outline-none focus:border-accent-cyan"
              />
            </div>

            <div>
              <label className="block text-xs font-medium text-zinc-300 mb-1">
                Alert Notification Threshold (1-10)
              </label>
              <div className="flex items-center space-x-3">
                <input
                  type="range"
                  min="1"
                  max="10"
                  value={alertThreshold}
                  onChange={(e) => setAlertThreshold(Number(e.target.value))}
                  className="w-full accent-accent-amber cursor-pointer"
                />
                <span className="px-2.5 py-1 rounded bg-surface-raised text-amber-300 font-mono font-bold text-xs hairline-border tabular">
                  ≥ {alertThreshold}
                </span>
              </div>
            </div>
          </div>

          <div>
            <label className="block text-xs font-medium text-zinc-300 mb-1">Description</label>
            <input
              type="text"
              value={description}
              onChange={(e) => setDescription(e.target.value)}
              placeholder="e.g. Real-time intelligence monitoring of Persian Gulf developments..."
              className="w-full rounded-lg bg-canvas-recessed px-3.5 py-2 text-xs text-zinc-100 hairline-border focus:outline-none focus:border-accent-cyan"
            />
          </div>

          <div>
            <label className="block text-xs font-medium text-zinc-300 mb-1">
              Tracked Keywords (comma-separated)
            </label>
            <input
              type="text"
              value={keywordsText}
              onChange={(e) => setKeywordsText(e.target.value)}
              placeholder="Iran, IRGC, Hormuz, sanctions, drone strike, uranium"
              className="w-full rounded-lg bg-canvas-recessed px-3.5 py-2 text-xs text-zinc-100 hairline-border focus:outline-none focus:border-accent-cyan font-mono"
            />
          </div>

          <div>
            <label className="block text-xs font-medium text-zinc-300 mb-1">
              Tracked Named Entities & Locations (comma-separated)
            </label>
            <input
              type="text"
              value={entitiesText}
              onChange={(e) => setEntitiesText(e.target.value)}
              placeholder="Tehran, Strait of Hormuz, CENTCOM, IRGC Navy, Natanz"
              className="w-full rounded-lg bg-canvas-recessed px-3.5 py-2 text-xs text-zinc-100 hairline-border focus:outline-none focus:border-accent-cyan font-mono"
            />
          </div>

          <div>
            <div className="flex items-center justify-between mb-1.5">
              <label className="flex items-center space-x-1.5 text-xs font-medium text-zinc-300">
                <Sparkles className="w-3.5 h-3.5 text-accent-cyan" />
                <span>AI Importance Scoring Prompt (Guiding AI Stage 2)</span>
              </label>
              <span className="text-[11px] text-zinc-500 font-mono">Dynamic zero-code prompt</span>
            </div>
            <textarea
              rows={4}
              value={scoringPrompt}
              onChange={(e) => setScoringPrompt(e.target.value)}
              required
              className="w-full rounded-lg bg-canvas-recessed p-3 text-xs text-zinc-100 hairline-border focus:outline-none focus:border-accent-cyan font-mono leading-relaxed"
            />
            <p className="text-[11px] text-zinc-500 mt-1">
              Modifying this prompt instantly re-calibrates how subsequent incoming intelligence events are scored across the 1-10 importance scale.
            </p>
          </div>

          <div className="flex items-center justify-end space-x-3 pt-3 hairline-t">
            {isCreating && (
              <button
                type="button"
                onClick={() => setIsCreating(false)}
                className="px-4 py-2 text-xs font-medium text-zinc-400 hover:text-zinc-200 rounded-lg"
              >
                Cancel
              </button>
            )}
            <button
              type="submit"
              disabled={loading}
              className="inline-flex items-center space-x-1.5 px-4 py-2 text-xs font-semibold text-white bg-accent-cyan hover:bg-accent-cyan/90 text-zinc-950 rounded-lg pressable shadow-sm"
            >
              <Check className="w-4 h-4" />
              <span>{loading ? 'Saving...' : isCreating ? 'Create Theme' : 'Save Theme Configuration'}</span>
            </button>
          </div>
        </form>
      )}
    </div>
  );
}
