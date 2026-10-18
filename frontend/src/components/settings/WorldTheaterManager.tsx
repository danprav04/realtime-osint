'use client';

import React, { useState, useEffect, useCallback } from 'react';
import {
  Globe,
  RefreshCw,
  Check,
  AlertCircle,
  Shield,
  Clock,
  Landmark,
  Compass,
  FileText,
  Save,
} from 'lucide-react';
import { WorldTheaterContext } from '@/lib/types';
import { getWorldTheaterContext, updateWorldTheaterContext, refreshWorldTheaterFromApi } from '@/lib/api';
import { cn } from '@/lib/utils';

export function WorldTheaterManager() {
  const [context, setContext] = useState<WorldTheaterContext | null>(null);
  const [loading, setLoading] = useState(true);
  const [syncing, setSyncing] = useState(false);
  const [saving, setSaving] = useState(false);
  const [feedbackMsg, setFeedbackMsg] = useState<{ type: 'success' | 'error'; text: string } | null>(null);

  const fetchContext = useCallback(async () => {
    try {
      setLoading(true);
      const data = await getWorldTheaterContext();
      setContext(data);
    } catch (err: any) {
      console.error('Failed to load world theater context:', err);
      setFeedbackMsg({ type: 'error', text: 'Failed to load World Theater context.' });
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    fetchContext();
  }, [fetchContext]);

  const handleLiveSync = async () => {
    try {
      setSyncing(true);
      setFeedbackMsg(null);
      const res = await refreshWorldTheaterFromApi();
      if (res && res.context) {
        setContext(res.context);
        setFeedbackMsg({
          type: 'success',
          text: `Synchronized with live Wikidata APIs (${res.sync_source || 'live'})`,
        });
      }
    } catch (err: any) {
      console.error('Failed to sync with live APIs:', err);
      setFeedbackMsg({ type: 'error', text: 'Live API sync failed. Preserved authoritative baseline.' });
    } finally {
      setSyncing(false);
    }
  };

  if (loading && !context) {
    return (
      <div className="flex items-center justify-center p-12 text-zinc-400">
        <RefreshCw className="w-5 h-5 animate-spin mr-3 text-cyan-400" />
        Loading World Theater context...
      </div>
    );
  }

  const anchor = context?.temporal_anchor;
  const leaders = context?.global_leadership || {};
  const theaters = context?.active_operational_theaters || [];
  const rules = context?.geopolitical_rules || [];

  return (
    <div className="space-y-6">
      {/* Header Card */}
      <div className="bg-zinc-900/60 border border-zinc-800 rounded-xl p-5 shadow-sm">
        <div className="flex flex-col md:flex-row md:items-center justify-between gap-4">
          <div className="flex items-start space-x-3">
            <div className="p-2.5 bg-cyan-500/10 border border-cyan-500/20 rounded-lg text-cyan-400 shrink-0">
              <Globe className="w-6 h-6" />
            </div>
            <div>
              <h3 className="text-base font-semibold text-zinc-100 flex items-center gap-2">
                Global World Theater & Geopolitical Grounding
                <span className="text-xs px-2 py-0.5 rounded-full bg-cyan-500/10 text-cyan-300 border border-cyan-500/20 font-mono">
                  Live Context Engine
                </span>
              </h3>
              <p className="text-xs text-zinc-400 mt-1 max-w-2xl leading-relaxed">
                Supplies all AI pipeline stages (Gatekeeper, Translation, Scoring, Summaries, SitReps) with an
                authoritative, real-time understanding of global governance, sovereign leadership, active flashpoints,
                and timeline anchors.
              </p>
            </div>
          </div>

          <div className="flex items-center gap-3">
            <button
              onClick={handleLiveSync}
              disabled={syncing}
              className={cn(
                'inline-flex items-center px-3.5 py-2 rounded-lg text-xs font-medium border transition-colors shadow-sm',
                syncing
                  ? 'bg-zinc-800 border-zinc-700 text-zinc-400 cursor-not-allowed'
                  : 'bg-cyan-600 hover:bg-cyan-500 text-white border-cyan-500/30'
              )}
            >
              <RefreshCw className={cn('w-3.5 h-3.5 mr-2', syncing && 'animate-spin')} />
              {syncing ? 'Syncing Wikidata...' : 'Sync with Live APIs'}
            </button>
          </div>
        </div>

        {/* Sync Status Banner */}
        <div className="mt-4 pt-4 border-t border-zinc-800/80 flex flex-wrap items-center justify-between text-xs text-zinc-400 gap-3">
          <div className="flex items-center gap-4">
            <span className="flex items-center gap-1.5">
              <Clock className="w-3.5 h-3.5 text-zinc-500" />
              <span>
                Temporal Anchor: <strong className="text-zinc-200">{anchor?.current_year || 2026}</strong> (UTC Date:{' '}
                {anchor?.current_date_utc || 'Current'})
              </span>
            </span>
            <span className="text-zinc-700">|</span>
            <span className="flex items-center gap-1.5">
              <span
                className={cn(
                  'w-2 h-2 rounded-full',
                  context?.live_api_status === 'synced' ? 'bg-emerald-400' : 'bg-cyan-400'
                )}
              />
              <span>
                Source:{' '}
                <strong className="text-zinc-300">
                  {context?.sync_source === 'wikidata_live_api' ? 'Wikidata REST API (Live)' : 'Authoritative Baseline'}
                </strong>
              </span>
            </span>
          </div>

          {context?.last_synced_at && (
            <span className="text-zinc-500">
              Last Synced: {new Date(context.last_synced_at).toLocaleString()}
            </span>
          )}
        </div>

        {feedbackMsg && (
          <div
            className={cn(
              'mt-3 px-3 py-2 rounded-lg text-xs flex items-center space-x-2',
              feedbackMsg.type === 'success'
                ? 'bg-emerald-950/40 border border-emerald-800 text-emerald-300'
                : 'bg-rose-950/40 border border-rose-800 text-rose-300'
            )}
          >
            {feedbackMsg.type === 'success' ? (
              <Check className="w-4 h-4 shrink-0 text-emerald-400" />
            ) : (
              <AlertCircle className="w-4 h-4 shrink-0 text-rose-400" />
            )}
            <span>{feedbackMsg.text}</span>
          </div>
        )}
      </div>

      {/* Global Leadership Roster */}
      <div className="bg-zinc-900/60 border border-zinc-800 rounded-xl p-5 shadow-sm space-y-4">
        <div className="flex items-center justify-between">
          <h4 className="text-sm font-semibold text-zinc-200 flex items-center gap-2">
            <Landmark className="w-4 h-4 text-cyan-400" />
            Synchronized Global Leadership & Sovereign Authority
          </h4>
          <span className="text-xs text-zinc-500">Injected into Entity & Translation Models</span>
        </div>

        <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-3">
          {/* United States */}
          <div className="bg-zinc-950/50 border border-zinc-800/80 rounded-lg p-3.5 space-y-1.5">
            <div className="flex items-center justify-between">
              <span className="text-xs font-semibold text-cyan-400 uppercase tracking-wider">United States</span>
              <span className="text-[10px] px-1.5 py-0.5 rounded bg-zinc-800 text-zinc-300 border border-zinc-700">
                In Office (47th POTUS)
              </span>
            </div>
            <p className="text-sm font-medium text-zinc-100">
              {leaders.united_states?.head_of_state || 'Donald J. Trump'}
            </p>
            <p className="text-xs text-zinc-400 line-clamp-2">
              {leaders.united_states?.title || '47th President of the United States'}
            </p>
            {leaders.united_states?.vice_president && (
              <p className="text-[11px] text-zinc-500 pt-1 border-t border-zinc-800/60">
                VP: {leaders.united_states.vice_president}
              </p>
            )}
          </div>

          {/* Israel */}
          <div className="bg-zinc-950/50 border border-zinc-800/80 rounded-lg p-3.5 space-y-1.5">
            <div className="flex items-center justify-between">
              <span className="text-xs font-semibold text-cyan-400 uppercase tracking-wider">Israel</span>
              <span className="text-[10px] px-1.5 py-0.5 rounded bg-zinc-800 text-zinc-300 border border-zinc-700">
                Prime Minister
              </span>
            </div>
            <p className="text-sm font-medium text-zinc-100">
              {leaders.israel?.head_of_government || 'Benjamin Netanyahu'}
            </p>
            <p className="text-xs text-zinc-400 line-clamp-2">
              {leaders.israel?.title || 'Prime Minister of Israel'}
            </p>
            {leaders.israel?.head_of_state && (
              <p className="text-[11px] text-zinc-500 pt-1 border-t border-zinc-800/60">
                President: {leaders.israel.head_of_state}
              </p>
            )}
          </div>

          {/* Iran */}
          <div className="bg-zinc-950/50 border border-zinc-800/80 rounded-lg p-3.5 space-y-1.5">
            <div className="flex items-center justify-between">
              <span className="text-xs font-semibold text-cyan-400 uppercase tracking-wider">Iran</span>
              <span className="text-[10px] px-1.5 py-0.5 rounded bg-zinc-800 text-zinc-300 border border-zinc-700">
                Presidency & Supreme
              </span>
            </div>
            <p className="text-sm font-medium text-zinc-100">
              {leaders.iran?.president || 'Masoud Pezeshkian'}
            </p>
            <p className="text-xs text-zinc-400 line-clamp-2">
              {leaders.iran?.title || 'President of the Islamic Republic of Iran'}
            </p>
            {leaders.iran?.supreme_leader && (
              <p className="text-[11px] text-zinc-500 pt-1 border-t border-zinc-800/60">
                Supreme Leader: {leaders.iran.supreme_leader}
              </p>
            )}
          </div>

          {/* Russia */}
          <div className="bg-zinc-950/50 border border-zinc-800/80 rounded-lg p-3.5 space-y-1.5">
            <div className="flex items-center justify-between">
              <span className="text-xs font-semibold text-zinc-400 uppercase tracking-wider">Russia</span>
              <span className="text-[10px] px-1.5 py-0.5 rounded bg-zinc-800 text-zinc-300 border border-zinc-700">
                President
              </span>
            </div>
            <p className="text-sm font-medium text-zinc-100">
              {leaders.russia?.head_of_state || 'Vladimir Putin'}
            </p>
            <p className="text-xs text-zinc-400 line-clamp-2">
              {leaders.russia?.title || 'President of the Russian Federation'}
            </p>
          </div>

          {/* Ukraine */}
          <div className="bg-zinc-950/50 border border-zinc-800/80 rounded-lg p-3.5 space-y-1.5">
            <div className="flex items-center justify-between">
              <span className="text-xs font-semibold text-zinc-400 uppercase tracking-wider">Ukraine</span>
              <span className="text-[10px] px-1.5 py-0.5 rounded bg-zinc-800 text-zinc-300 border border-zinc-700">
                President
              </span>
            </div>
            <p className="text-sm font-medium text-zinc-100">
              {leaders.ukraine?.head_of_state || 'Volodymyr Zelenskyy'}
            </p>
            <p className="text-xs text-zinc-400 line-clamp-2">
              {leaders.ukraine?.title || 'President of Ukraine'}
            </p>
          </div>

          {/* Regional Proxies & Actors */}
          <div className="bg-zinc-950/50 border border-zinc-800/80 rounded-lg p-3.5 space-y-1.5">
            <div className="flex items-center justify-between">
              <span className="text-xs font-semibold text-amber-400 uppercase tracking-wider">Regional Actors</span>
              <span className="text-[10px] px-1.5 py-0.5 rounded bg-zinc-800 text-zinc-300 border border-zinc-700">
                Axis of Resistance
              </span>
            </div>
            <p className="text-sm font-medium text-zinc-100">Houthis / Ansar Allah</p>
            <p className="text-xs text-zinc-400 line-clamp-2">
              {leaders.regional_non_state_actors?.houthis_ansar_allah || 'Abdul-Malik al-Houthi, Yahya Saree'}
            </p>
            <p className="text-[11px] text-zinc-500 pt-1 border-t border-zinc-800/60">
              Hezbollah & Iraqi Paramilitaries
            </p>
          </div>
        </div>
      </div>

      {/* Active Operational Theaters & Flashpoints */}
      <div className="bg-zinc-900/60 border border-zinc-800 rounded-xl p-5 shadow-sm space-y-4">
        <div className="flex items-center justify-between">
          <h4 className="text-sm font-semibold text-zinc-200 flex items-center gap-2">
            <Compass className="w-4 h-4 text-cyan-400" />
            Active Operational Theaters & Strategic Flashpoints
          </h4>
          <span className="text-xs text-zinc-500">Injected into Scoring & SitRep Calibration</span>
        </div>

        <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
          {theaters.map((t, idx) => (
            <div key={idx} className="bg-zinc-950/50 border border-zinc-800/80 rounded-lg p-3.5 space-y-1.5">
              <div className="flex items-center justify-between">
                <span className="text-xs font-semibold text-zinc-200">{t.theater}</span>
                <span className="text-[10px] px-2 py-0.5 rounded bg-cyan-950/50 text-cyan-400 border border-cyan-800/40">
                  {t.status}
                </span>
              </div>
              <p className="text-xs text-zinc-400 leading-relaxed">{t.scope}</p>
            </div>
          ))}
        </div>
      </div>

      {/* Geopolitical Grounding Rules */}
      <div className="bg-zinc-900/60 border border-zinc-800 rounded-xl p-5 shadow-sm space-y-3">
        <h4 className="text-sm font-semibold text-zinc-200 flex items-center gap-2">
          <Shield className="w-4 h-4 text-cyan-400" />
          Mandatory Geopolitical Grounding Rules & Constraints
        </h4>
        <div className="space-y-2">
          {rules.map((rule, idx) => (
            <div key={idx} className="flex items-start gap-2.5 text-xs text-zinc-300 bg-zinc-950/40 p-2.5 rounded-lg border border-zinc-800/60">
              <span className="text-cyan-400 font-mono font-bold shrink-0">{idx + 1}.</span>
              <span className="leading-relaxed">{rule}</span>
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}
