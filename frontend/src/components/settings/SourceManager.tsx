'use client';

import React, { useState } from 'react';
import {
  Radio,
  Plus,
  Play,
  Trash2,
  Check,
  AlertCircle,
  Clock,
  RotateCw,
  Power,
} from 'lucide-react';
import { Source } from '@/lib/types';
import { SourceBadge } from '../timeline/SourceBadge';
import { updateSource, createSource, deleteSource, triggerSource } from '@/lib/api';
import { formatRelativeTime } from '@/lib/utils';

interface SourceManagerProps {
  sources: Source[];
  activeThemeId?: string;
  onRefresh: () => void;
}

export function SourceManager({ sources, activeThemeId, onRefresh }: SourceManagerProps) {
  const [loadingId, setLoadingId] = useState<string | null>(null);
  const [isAddOpen, setIsAddOpen] = useState(false);
  const [feedback, setFeedback] = useState<{ type: 'success' | 'error'; message: string } | null>(null);

  // New source form state
  const [newName, setNewName] = useState('');
  const [newType, setNewType] = useState('rss');
  const [newTarget, setNewTarget] = useState('');
  const [newInterval, setNewInterval] = useState(300);
  const [newLookback, setNewLookback] = useState(1);

  const handleToggle = async (source: Source) => {
    const currentEnabled = source.is_enabled ?? source.enabled ?? true;
    const nextEnabled = !currentEnabled;
    setLoadingId(source.id);
    setFeedback(null);
    try {
      await updateSource(source.id, { is_enabled: nextEnabled, enabled: nextEnabled });
      setFeedback({
        type: 'success',
        message: `Source "${source.name}" ${nextEnabled ? 'enabled' : 'paused'}.`,
      });
      onRefresh();
    } catch (err: any) {
      setFeedback({ type: 'error', message: err.message || 'Failed to update source' });
    } finally {
      setLoadingId(null);
    }
  };

  const handleTrigger = async (source: Source) => {
    setLoadingId(`trigger-${source.id}`);
    setFeedback(null);
    try {
      await triggerSource(source.id);
      setFeedback({
        type: 'success',
        message: `Manual poll triggered for "${source.name}". Check live feed.`,
      });
      onRefresh();
    } catch (err: any) {
      setFeedback({ type: 'error', message: err.message || 'Failed to trigger source' });
    } finally {
      setLoadingId(null);
    }
  };

  const handleDelete = async (source: Source) => {
    if (!confirm(`Delete source "${source.name}"?`)) return;
    setLoadingId(source.id);
    setFeedback(null);
    try {
      await deleteSource(source.id);
      setFeedback({ type: 'success', message: `Source "${source.name}" deleted.` });
      onRefresh();
    } catch (err: any) {
      setFeedback({ type: 'error', message: err.message || 'Failed to delete source' });
    } finally {
      setLoadingId(null);
    }
  };

  const handleAddSource = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!activeThemeId) {
      setFeedback({ type: 'error', message: 'No active theme ID found' });
      return;
    }

    setFeedback(null);
    try {
      await createSource({
        theme_id: activeThemeId,
        name: newName,
        source_type: newType,
        target: newTarget,
        interval_seconds: Number(newInterval),
        max_lookback_hours: Number(newLookback),
        is_enabled: true,
        config_json: { target: newTarget, max_lookback_hours: Number(newLookback) },
      });
      setFeedback({ type: 'success', message: `Added new source "${newName}" successfully!` });
      setIsAddOpen(false);
      setNewName('');
      setNewTarget('');
      setNewLookback(1);
      onRefresh();
    } catch (err: any) {
      setFeedback({ type: 'error', message: err.message || 'Failed to add source' });
    }
  };

  return (
    <div className="card-slick rounded-xl p-6 space-y-6">
      {/* Header */}
      <div className="flex flex-wrap items-center justify-between gap-4 pb-4 hairline-b">
        <div className="flex items-center space-x-3">
          <div className="p-2.5 rounded-lg bg-accent-cyan/15 text-accent-cyan border border-accent-cyan/30">
            <Radio className="w-5 h-5" />
          </div>
          <div>
            <h3 className="text-base font-semibold text-zinc-100 tracking-tight">
              Data Source Management
            </h3>
            <p className="text-xs text-zinc-400">
              Configure independent ingestion engines (RSS, Telegram, NewsAPI, Reddit, Maritime AIS)
            </p>
          </div>
        </div>

        <button
          onClick={() => setIsAddOpen(true)}
          className="inline-flex items-center space-x-1.5 px-3 py-1.5 text-xs font-semibold text-zinc-950 bg-accent-cyan hover:bg-accent-cyan/90 rounded-lg pressable shadow-sm"
        >
          <Plus className="w-4 h-4" />
          <span>Add Ingestion Source</span>
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

      {/* Sources Table / List */}
      <div className="overflow-x-auto">
        <table className="w-full text-left text-xs">
          <thead>
            <tr className="border-b border-white/5 text-zinc-500 font-mono uppercase text-[11px]">
              <th className="pb-3 font-semibold">Engine / Source</th>
              <th className="pb-3 font-semibold">Target / Feed URL</th>
              <th className="pb-3 font-semibold">Interval / Lookback</th>
              <th className="pb-3 font-semibold">Status / Last Polled</th>
              <th className="pb-3 font-semibold text-right">Actions</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-white/5">
            {sources.map((source) => {
              const isEnabled = source.is_enabled ?? source.enabled ?? true;
              const isTriggering = loadingId === `trigger-${source.id}`;
              const interval = source.interval_seconds || source.polling_interval_sec || 300;
              const lookback = source.max_lookback_hours ?? source.config_json?.max_lookback_hours ?? 1;

              return (
                <tr key={source.id} className="hover:bg-surface-card/50 transition-colors">
                  <td className="py-3.5 pr-4">
                    <div className="flex flex-col space-y-1">
                      <span className="font-semibold text-zinc-200">{source.name}</span>
                      <SourceBadge sourceType={source.source_type} showName={false} />
                    </div>
                  </td>

                  <td className="py-3.5 pr-4 max-w-xs">
                    <div className="font-mono text-[11px] text-zinc-400 truncate" title={source.target}>
                      {source.target}
                    </div>
                  </td>

                  <td className="py-3.5 pr-4 font-mono tabular text-zinc-300">
                    <div className="flex flex-col space-y-0.5">
                      <div className="flex items-center space-x-1">
                        <Clock className="w-3 h-3 text-zinc-500" />
                        <span>{interval}s</span>
                      </div>
                      <span className="text-[10px] text-zinc-400">
                        {lookback}h lookback
                      </span>
                    </div>
                  </td>

                  <td className="py-3.5 pr-4">
                    <div className="flex flex-col space-y-1">
                      <div className="flex items-center space-x-1.5">
                        <span
                          className={`w-2 h-2 rounded-full ${
                            isEnabled ? 'bg-emerald-400 shadow-beacon' : 'bg-zinc-600'
                          }`}
                        />
                        <span
                          className={`font-semibold text-[11px] ${
                            isEnabled ? 'text-emerald-400' : 'text-zinc-500'
                          }`}
                        >
                          {isEnabled ? 'ACTIVE' : 'PAUSED'}
                        </span>
                      </div>
                      <span className="text-[10px] text-zinc-500 tabular">
                        {formatRelativeTime(source.last_polled_at)}
                      </span>
                    </div>
                  </td>

                  <td className="py-3.5 text-right space-x-2">
                    {/* Manual trigger button */}
                    <button
                      type="button"
                      disabled={isTriggering || !isEnabled}
                      onClick={() => handleTrigger(source)}
                      title="Trigger Immediate Polling Run"
                      className="inline-flex items-center space-x-1 px-2.5 py-1 rounded bg-surface-raised hover:bg-surface-overlay hairline-border text-zinc-300 hover:text-white transition-colors text-[11px] disabled:opacity-40"
                    >
                      <Play className={`w-3 h-3 ${isTriggering ? 'animate-spin' : ''}`} />
                      <span className="hidden sm:inline">Poll Now</span>
                    </button>

                    {/* Enable / Disable toggle */}
                    <button
                      type="button"
                      disabled={loadingId === source.id}
                      onClick={() => handleToggle(source)}
                      title={isEnabled ? 'Pause Source' : 'Enable Source'}
                      className={`inline-flex items-center p-1 rounded hairline-border transition-colors ${
                        isEnabled
                          ? 'bg-emerald-500/15 text-emerald-400 border-emerald-500/30 hover:bg-emerald-500/25'
                          : 'bg-zinc-800 text-zinc-500 border-zinc-700 hover:text-zinc-300'
                      }`}
                    >
                      <Power className="w-3.5 h-3.5" />
                    </button>

                    {/* Delete button */}
                    <button
                      type="button"
                      disabled={loadingId === source.id}
                      onClick={() => handleDelete(source)}
                      title="Delete Source"
                      className="p-1 rounded text-zinc-500 hover:text-rose-400 hover:bg-surface-raised transition-colors"
                    >
                      <Trash2 className="w-3.5 h-3.5" />
                    </button>
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>

      {/* Add Source Modal */}
      {isAddOpen && (
        <div className="fixed inset-0 z-50 flex items-center justify-center p-4 bg-black/75 backdrop-blur-sm">
          <div className="relative w-full max-w-md rounded-xl bg-surface-overlay p-6 hairline-border shadow-2xl">
            <h4 className="text-base font-semibold text-zinc-100 mb-4 pb-2 hairline-b">
              Add New Intelligence Source
            </h4>
            <form onSubmit={handleAddSource} className="space-y-4 text-xs">
              <div>
                <label className="block text-zinc-300 font-medium mb-1">Source Name / Identifier</label>
                <input
                  type="text"
                  required
                  placeholder="e.g. Reuters Middle East Feed"
                  value={newName}
                  onChange={(e) => setNewName(e.target.value)}
                  className="w-full rounded-lg bg-canvas-recessed px-3.5 py-2 text-zinc-100 hairline-border focus:outline-none focus:border-accent-cyan"
                />
              </div>

              <div>
                <label className="block text-zinc-300 font-medium mb-1">Engine Plugin Type</label>
                <select
                  value={newType}
                  onChange={(e) => setNewType(e.target.value)}
                  className="w-full rounded-lg bg-canvas-recessed px-3.5 py-2 text-zinc-100 hairline-border focus:outline-none focus:border-accent-cyan"
                >
                  <option value="rss">RSS / Atom Feed</option>
                  <option value="telegram">Telegram Channel</option>
                  <option value="newsapi">News API / GDELT</option>
                  <option value="reddit">Reddit Subreddit</option>
                  <option value="ais">Maritime AIS Vessel Stream</option>
                </select>
              </div>

              <div>
                <label className="block text-zinc-300 font-medium mb-1">
                  Target (Feed URL, Channel Username, Subreddit)
                </label>
                <input
                  type="text"
                  required
                  placeholder="https://... or @channel or r/worldnews"
                  value={newTarget}
                  onChange={(e) => setNewTarget(e.target.value)}
                  className="w-full rounded-lg bg-canvas-recessed px-3.5 py-2 text-zinc-100 hairline-border focus:outline-none focus:border-accent-cyan font-mono"
                />
              </div>

              <div className="grid grid-cols-2 gap-3">
                <div>
                  <label className="block text-zinc-300 font-medium mb-1">
                    Polling Interval (Sec)
                  </label>
                  <input
                    type="number"
                    min="30"
                    max="3600"
                    value={newInterval}
                    onChange={(e) => setNewInterval(Number(e.target.value))}
                    className="w-full rounded-lg bg-canvas-recessed px-3.5 py-2 text-zinc-100 hairline-border focus:outline-none focus:border-accent-cyan font-mono tabular"
                  />
                </div>
                <div>
                  <label className="block text-zinc-300 font-medium mb-1">
                    Lookback Max (Hours)
                  </label>
                  <input
                    type="number"
                    min="0.1"
                    max="72"
                    step="0.5"
                    value={newLookback}
                    onChange={(e) => setNewLookback(Number(e.target.value))}
                    className="w-full rounded-lg bg-canvas-recessed px-3.5 py-2 text-zinc-100 hairline-border focus:outline-none focus:border-accent-cyan font-mono tabular"
                  />
                </div>
              </div>

              <div className="flex items-center justify-end space-x-3 pt-3 hairline-t">
                <button
                  type="button"
                  onClick={() => setIsAddOpen(false)}
                  className="px-4 py-2 text-zinc-400 hover:text-zinc-200"
                >
                  Cancel
                </button>
                <button
                  type="submit"
                  className="px-4 py-2 rounded-lg bg-accent-cyan text-zinc-950 font-semibold hover:bg-accent-cyan/90 pressable shadow-sm"
                >
                  Create Source
                </button>
              </div>
            </form>
          </div>
        </div>
      )}
    </div>
  );
}
