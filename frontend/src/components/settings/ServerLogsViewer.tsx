'use client';

import React, { useState, useEffect, useRef, useCallback } from 'react';
import {
  Terminal,
  RotateCw,
  Trash2,
  Download,
  Search,
  Filter,
  Check,
  AlertCircle,
  AlertTriangle,
  Info,
  Bug,
  ChevronDown,
  ChevronRight,
  Copy,
  ExternalLink,
  Play,
  ArrowDownToLine,
  Pause,
  Clock,
  Cpu,
  Layers,
  X,
} from 'lucide-react';
import { ServerLogEntry, ServerLogStats } from '@/lib/types';
import {
  getServerLogs,
  clearServerLogs,
  getServerLogStats,
  getServerLogsExportUrl,
  emitTestServerLog,
} from '@/lib/api';
import { cn } from '@/lib/utils';

export function ServerLogsViewer() {
  const [logs, setLogs] = useState<ServerLogEntry[]>([]);
  const [stats, setStats] = useState<ServerLogStats | null>(null);
  const [loading, setLoading] = useState(false);
  const [isRefreshing, setIsRefreshing] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // Filters & Controls
  const [selectedLevel, setSelectedLevel] = useState<string>('ALL');
  const [selectedLogger, setSelectedLogger] = useState<string>('all');
  const [searchQuery, setSearchQuery] = useState<string>('');
  const [limit, setLimit] = useState<number>(200);
  const [autoRefreshInterval, setAutoRefreshInterval] = useState<number>(5000); // ms, 0 = paused
  const [autoScroll, setAutoScroll] = useState<boolean>(false);
  const [expandedLogId, setExpandedLogId] = useState<number | null>(null);
  const [copiedId, setCopiedId] = useState<number | null>(null);
  const [actionFeedback, setActionFeedback] = useState<string | null>(null);

  const consoleEndRef = useRef<HTMLDivElement>(null);
  const consoleContainerRef = useRef<HTMLDivElement>(null);

  const fetchLogs = useCallback(
    async (showLoadingSpinner = false) => {
      if (showLoadingSpinner) setIsRefreshing(true);
      try {
        const response = await getServerLogs({
          limit,
          level: selectedLevel,
          logger: selectedLogger,
          search: searchQuery.trim() || undefined,
        });
        setLogs(response.logs || []);
        if (response.stats) {
          setStats(response.stats);
        }
        setError(null);
      } catch (err: any) {
        console.error('Failed to load server logs:', err);
        setError(err?.message || 'Failed to connect to backend server logs API');
      } finally {
        setLoading(false);
        setIsRefreshing(false);
      }
    },
    [limit, selectedLevel, selectedLogger, searchQuery]
  );

  // Initial load
  useEffect(() => {
    setLoading(true);
    fetchLogs(true);
  }, [fetchLogs]);

  // Auto-refresh interval timer
  useEffect(() => {
    if (autoRefreshInterval <= 0) return;
    const timer = setInterval(() => {
      fetchLogs(false);
    }, autoRefreshInterval);
    return () => clearInterval(timer);
  }, [autoRefreshInterval, fetchLogs]);

  // Auto-scroll to bottom when logs update if enabled
  useEffect(() => {
    if (autoScroll && consoleContainerRef.current) {
      consoleContainerRef.current.scrollTop = consoleContainerRef.current.scrollHeight;
    }
  }, [logs, autoScroll]);

  const handleClearLogs = async () => {
    if (!window.confirm('Are you sure you want to clear the in-memory server log buffer?')) {
      return;
    }
    try {
      await clearServerLogs();
      setLogs([]);
      setActionFeedback('Server log buffer cleared successfully');
      setTimeout(() => setActionFeedback(null), 3000);
      fetchLogs(false);
    } catch (err: any) {
      setError(err?.message || 'Failed to clear server logs');
    }
  };

  const handleEmitTest = async () => {
    try {
      const res = await emitTestServerLog(
        'INFO',
        `Operator diagnostic verification test at ${new Date().toLocaleTimeString()}`
      );
      setActionFeedback(`Test log emitted (#${res.entry?.id || 'new'})`);
      setTimeout(() => setActionFeedback(null), 3000);
      fetchLogs(false);
    } catch (err: any) {
      setError(err?.message || 'Failed to emit test log');
    }
  };

  const handleCopyLog = (entry: ServerLogEntry, e: React.MouseEvent) => {
    e.stopPropagation();
    const formatted = JSON.stringify(entry, null, 2);
    navigator.clipboard.writeText(formatted);
    setCopiedId(entry.id);
    setTimeout(() => setCopiedId(null), 2000);
  };

  const formatUptime = (seconds: number) => {
    if (!seconds || seconds <= 0) return '0s';
    const hrs = Math.floor(seconds / 3600);
    const mins = Math.floor((seconds % 3600) / 60);
    const secs = Math.floor(seconds % 60);
    if (hrs > 0) return `${hrs}h ${mins}m`;
    if (mins > 0) return `${mins}m ${secs}s`;
    return `${secs}s`;
  };

  const formatTimestamp = (iso: string) => {
    try {
      const d = new Date(iso);
      return d.toLocaleTimeString('en-US', {
        hour12: false,
        hour: '2-digit',
        minute: '2-digit',
        second: '2-digit',
        fractionalSecondDigits: 3,
      });
    } catch {
      return iso;
    }
  };

  const levelColorClass = (level: string) => {
    switch (level.toUpperCase()) {
      case 'CRITICAL':
      case 'ERROR':
        return 'text-rose-400 bg-rose-500/10 border-rose-500/25';
      case 'WARNING':
      case 'WARN':
        return 'text-amber-400 bg-amber-500/10 border-amber-500/25';
      case 'INFO':
        return 'text-accent-cyan bg-accent-cyan/10 border-accent-cyan/25';
      case 'DEBUG':
        return 'text-zinc-400 bg-zinc-800/60 border-zinc-700/40';
      default:
        return 'text-zinc-300 bg-surface-raised border-white/10';
    }
  };

  const counts = stats?.counts_by_level || {
    ERROR: 0,
    WARNING: 0,
    INFO: 0,
    DEBUG: 0,
  };

  const levelTabs = [
    { id: 'ALL', label: 'All Levels', count: stats?.total_buffered ?? logs.length },
    { id: 'ERROR', label: 'Errors', count: counts.ERROR || 0, color: 'text-rose-400' },
    { id: 'WARNING', label: 'Warnings', count: counts.WARNING || 0, color: 'text-amber-400' },
    { id: 'INFO', label: 'Info', count: counts.INFO || 0, color: 'text-accent-cyan' },
    { id: 'DEBUG', label: 'Debug', count: counts.DEBUG || 0, color: 'text-zinc-400' },
  ];

  const loggerPresets = [
    { id: 'all', label: 'All Components' },
    { id: 'pipeline', label: 'AI Pipeline' },
    { id: 'supervisor', label: 'Supervisor & Ingestion' },
    { id: 'telegram', label: 'Telegram Bot' },
    { id: 'socket', label: 'Socket.IO Gateway' },
    { id: 'api', label: 'REST API' },
  ];

  return (
    <div className="space-y-4">
      {/* Top Telemetry & Control Bar */}
      <div className="card-slick rounded-xl p-4 flex flex-col md:flex-row md:items-center justify-between gap-4">
        <div className="flex items-center space-x-3 min-w-0">
          <div className="p-2.5 rounded-lg bg-surface-raised border hairline-border text-accent-cyan shrink-0">
            <Terminal className="w-5 h-5" />
          </div>
          <div className="min-w-0">
            <div className="flex items-center space-x-2">
              <h3 className="text-sm font-semibold text-zinc-100 tracking-tight">
                Runtime Server Logs & Diagnostics
              </h3>
              <span
                className={cn(
                  'inline-flex items-center space-x-1 px-2 py-0.5 rounded-full text-[10px] font-mono border',
                  autoRefreshInterval > 0
                    ? 'bg-emerald-500/10 text-emerald-400 border-emerald-500/25'
                    : 'bg-zinc-800 text-zinc-400 border-zinc-700'
                )}
              >
                <span
                  className={cn(
                    'w-1.5 h-1.5 rounded-full',
                    autoRefreshInterval > 0 ? 'bg-emerald-400 animate-pulse' : 'bg-zinc-500'
                  )}
                />
                <span>{autoRefreshInterval > 0 ? `Streaming (${autoRefreshInterval / 1000}s)` : 'Paused'}</span>
              </span>
            </div>
            <p className="text-xs text-zinc-400 truncate mt-0.5">
              In-memory rotating circular buffer with zero overhead and automatic secret redaction
            </p>
          </div>
        </div>

        {/* Telemetry quick metrics */}
        <div className="flex flex-wrap items-center gap-2 text-xs font-mono">
          <div className="px-2.5 py-1 rounded-md bg-canvas-recessed border hairline-border text-zinc-300 flex items-center space-x-1.5">
            <Layers className="w-3.5 h-3.5 text-zinc-500" />
            <span className="text-zinc-500">Buffer:</span>
            <span className="text-zinc-200 tabular font-medium">
              {stats?.total_buffered ?? logs.length} / {stats?.max_capacity ?? 2000}
            </span>
          </div>

          <div className="px-2.5 py-1 rounded-md bg-canvas-recessed border hairline-border text-zinc-300 flex items-center space-x-1.5">
            <Clock className="w-3.5 h-3.5 text-zinc-500" />
            <span className="text-zinc-500">Uptime:</span>
            <span className="text-zinc-200 tabular font-medium">
              {stats ? formatUptime(stats.uptime_seconds) : '—'}
            </span>
          </div>

          {/* Action buttons */}
          <div className="flex items-center space-x-1.5 pl-2 border-l hairline-border">
            <button
              onClick={() => fetchLogs(true)}
              disabled={isRefreshing}
              title="Refresh Logs Now"
              className="p-1.5 rounded-lg bg-surface-raised hairline-border text-zinc-300 hover:text-white transition-colors pressable"
            >
              <RotateCw className={cn('w-3.5 h-3.5', isRefreshing && 'animate-spin')} />
            </button>

            <button
              onClick={handleEmitTest}
              title="Emit Test Log"
              className="inline-flex items-center space-x-1 px-2.5 py-1 rounded-lg bg-surface-raised hairline-border text-xs text-zinc-300 hover:text-accent-cyan transition-colors pressable"
            >
              <Play className="w-3 h-3" />
              <span>Test Log</span>
            </button>

            {/* Export Dropdown */}
            <div className="relative group">
              <a
                href={getServerLogsExportUrl('text', limit)}
                download
                className="inline-flex items-center space-x-1 px-2.5 py-1 rounded-lg bg-surface-raised hairline-border text-xs text-zinc-300 hover:text-white transition-colors pressable"
              >
                <Download className="w-3 h-3" />
                <span>Export</span>
              </a>
            </div>

            <button
              onClick={handleClearLogs}
              title="Clear Buffer"
              className="p-1.5 rounded-lg bg-surface-raised hairline-border text-zinc-400 hover:text-rose-400 transition-colors pressable"
            >
              <Trash2 className="w-3.5 h-3.5" />
            </button>
          </div>
        </div>
      </div>

      {/* Action feedback notification */}
      {actionFeedback && (
        <div className="px-3 py-2 rounded-lg bg-accent-cyan/10 border border-accent-cyan/30 text-xs text-accent-cyan flex items-center space-x-2 animate-fadeIn">
          <Check className="w-4 h-4 shrink-0" />
          <span>{actionFeedback}</span>
        </div>
      )}

      {/* Error banner */}
      {error && (
        <div className="px-3.5 py-2.5 rounded-lg bg-rose-500/10 border border-rose-500/30 text-xs text-rose-300 flex items-center justify-between">
          <div className="flex items-center space-x-2">
            <AlertCircle className="w-4 h-4 text-rose-400 shrink-0" />
            <span>{error}</span>
          </div>
          <button
            onClick={() => fetchLogs(true)}
            className="text-xs underline hover:text-rose-200 ml-4"
          >
            Retry Connection
          </button>
        </div>
      )}

      {/* Filter Bar */}
      <div className="card-slick rounded-xl p-3 space-y-3">
        {/* Top row: Level tabs & Auto-refresh selector */}
        <div className="flex flex-wrap items-center justify-between gap-2.5">
          {/* Level Tabs */}
          <div className="flex flex-wrap items-center gap-1.5">
            {levelTabs.map((tab) => {
              const isSelected = selectedLevel === tab.id;
              return (
                <button
                  key={tab.id}
                  onClick={() => setSelectedLevel(tab.id)}
                  className={cn(
                    'px-2.5 py-1 rounded-lg text-xs font-medium transition-all pressable flex items-center space-x-1.5 border',
                    isSelected
                      ? 'bg-surface-active text-zinc-100 border-white/20 shadow-sm'
                      : 'bg-canvas-recessed text-zinc-400 border-white/5 hover:border-white/10 hover:text-zinc-200'
                  )}
                >
                  <span className={tab.color}>{tab.label}</span>
                  <span
                    className={cn(
                      'text-[10px] px-1.5 py-0.2 rounded font-mono tabular',
                      isSelected ? 'bg-black/40 text-zinc-300' : 'text-zinc-500'
                    )}
                  >
                    {tab.count}
                  </span>
                </button>
              );
            })}
          </div>

          {/* Stream frequency selector */}
          <div className="flex items-center space-x-2 text-xs">
            <span className="text-zinc-500 text-[11px]">Stream:</span>
            <select
              value={autoRefreshInterval}
              onChange={(e) => setAutoRefreshInterval(Number(e.target.value))}
              className="bg-canvas-recessed border hairline-border rounded-lg px-2 py-1 text-xs text-zinc-300 focus:outline-none focus:border-accent-cyan/50"
            >
              <option value={2000}>2s (Realtime)</option>
              <option value={5000}>5s (Default)</option>
              <option value={10000}>10s</option>
              <option value={0}>Paused</option>
            </select>

            <button
              onClick={() => setAutoScroll(!autoScroll)}
              title={autoScroll ? 'Disable Auto-scroll' : 'Enable Auto-scroll to bottom'}
              className={cn(
                'inline-flex items-center space-x-1 px-2.5 py-1 rounded-lg border text-xs transition-colors pressable',
                autoScroll
                  ? 'bg-accent-cyan/15 text-accent-cyan border-accent-cyan/30'
                  : 'bg-canvas-recessed text-zinc-400 border-white/5 hover:text-zinc-200'
              )}
            >
              <ArrowDownToLine className="w-3 h-3" />
              <span>Stick to Bottom</span>
            </button>
          </div>
        </div>

        {/* Bottom row: Search query, Component filter, Limit */}
        <div className="grid grid-cols-1 sm:grid-cols-12 gap-2.5 pt-2 border-t hairline-border">
          {/* Search box */}
          <div className="sm:col-span-6 relative">
            <Search className="w-3.5 h-3.5 text-zinc-500 absolute left-3 top-1/2 -translate-y-1/2" />
            <input
              type="text"
              placeholder="Filter by keyword, error trace, or trace ID..."
              value={searchQuery}
              onChange={(e) => setSearchQuery(e.target.value)}
              className="w-full pl-8 pr-8 py-1.5 rounded-lg bg-canvas-recessed border hairline-border text-xs text-zinc-200 placeholder-zinc-500 focus:outline-none focus:border-accent-cyan/50"
            />
            {searchQuery && (
              <button
                onClick={() => setSearchQuery('')}
                className="absolute right-2.5 top-1/2 -translate-y-1/2 text-zinc-500 hover:text-zinc-300"
              >
                <X className="w-3.5 h-3.5" />
              </button>
            )}
          </div>

          {/* Component Filter */}
          <div className="sm:col-span-4">
            <select
              value={selectedLogger}
              onChange={(e) => setSelectedLogger(e.target.value)}
              className="w-full bg-canvas-recessed border hairline-border rounded-lg px-2.5 py-1.5 text-xs text-zinc-300 focus:outline-none focus:border-accent-cyan/50"
            >
              {loggerPresets.map((p) => (
                <option key={p.id} value={p.id}>
                  {p.label}
                </option>
              ))}
            </select>
          </div>

          {/* Line limit */}
          <div className="sm:col-span-2">
            <select
              value={limit}
              onChange={(e) => setLimit(Number(e.target.value))}
              className="w-full bg-canvas-recessed border hairline-border rounded-lg px-2.5 py-1.5 text-xs text-zinc-300 focus:outline-none focus:border-accent-cyan/50"
            >
              <option value={50}>50 lines</option>
              <option value={100}>100 lines</option>
              <option value={200}>200 lines</option>
              <option value={500}>500 lines</option>
              <option value={1000}>1000 lines</option>
            </select>
          </div>
        </div>
      </div>

      {/* Terminal Console Log View */}
      <div className="rounded-xl bg-canvas-recessed border hairline-border overflow-hidden shadow-2xl">
        {/* Terminal Title Bar */}
        <div className="px-4 py-2 bg-canvas-subtle/80 border-b hairline-border flex items-center justify-between text-xs font-mono text-zinc-400">
          <div className="flex items-center space-x-2">
            <div className="flex space-x-1.5">
              <span className="w-2.5 h-2.5 rounded-full bg-rose-500/70 inline-block" />
              <span className="w-2.5 h-2.5 rounded-full bg-amber-500/70 inline-block" />
              <span className="w-2.5 h-2.5 rounded-full bg-emerald-500/70 inline-block" />
            </div>
            <span className="text-zinc-500 text-[11px] pl-2">osint-backend@stdout</span>
          </div>

          <div className="flex items-center space-x-3 text-[11px]">
            <span>
              Showing <span className="text-zinc-200 tabular font-semibold">{logs.length}</span> entries
            </span>
            {searchQuery && (
              <span className="text-accent-cyan">matching &quot;{searchQuery}&quot;</span>
            )}
          </div>
        </div>

        {/* Scrollable log list */}
        <div
          ref={consoleContainerRef}
          className="p-3 max-h-[560px] min-h-[300px] overflow-y-auto font-mono text-xs space-y-1 select-text scroll-smooth"
        >
          {loading && logs.length === 0 ? (
            <div className="py-20 text-center text-zinc-500 space-y-2 font-sans">
              <RotateCw className="w-6 h-6 animate-spin mx-auto text-accent-cyan/70" />
              <p className="text-xs">Connecting to server log stream...</p>
            </div>
          ) : logs.length === 0 ? (
            <div className="py-20 text-center text-zinc-500 space-y-2 font-sans">
              <Terminal className="w-8 h-8 mx-auto text-zinc-600" />
              <p className="text-xs font-medium text-zinc-400">No logs found matching active filters</p>
              <p className="text-[11px] text-zinc-600 max-w-sm mx-auto">
                Try switching the level filter to &quot;All Levels&quot; or resetting search criteria
              </p>
              {(selectedLevel !== 'ALL' || selectedLogger !== 'all' || searchQuery) && (
                <button
                  onClick={() => {
                    setSelectedLevel('ALL');
                    setSelectedLogger('all');
                    setSearchQuery('');
                  }}
                  className="mt-2 text-xs text-accent-cyan hover:underline"
                >
                  Clear all filters
                </button>
              )}
            </div>
          ) : (
            logs.map((entry) => {
              const isExpanded = expandedLogId === entry.id;
              const hasDetails = Boolean(
                entry.exception || entry.source_file || entry.extra_data || entry.trace_id
              );

              return (
                <div
                  key={entry.id}
                  onClick={() => hasDetails && setExpandedLogId(isExpanded ? null : entry.id)}
                  className={cn(
                    'group rounded px-2.5 py-1.5 transition-colors border border-transparent leading-relaxed',
                    hasDetails ? 'cursor-pointer hover:bg-surface-raised/60 hover:border-white/5' : '',
                    isExpanded ? 'bg-surface-card border-white/10 shadow-sm' : ''
                  )}
                >
                  <div className="flex items-start space-x-2.5 min-w-0">
                    {/* Expand icon if details present */}
                    {hasDetails ? (
                      <span className="text-zinc-600 group-hover:text-zinc-400 mt-0.5 shrink-0 transition-transform">
                        {isExpanded ? (
                          <ChevronDown className="w-3.5 h-3.5" />
                        ) : (
                          <ChevronRight className="w-3.5 h-3.5" />
                        )}
                      </span>
                    ) : (
                      <span className="w-3.5 shrink-0" />
                    )}

                    {/* ID */}
                    <span className="text-zinc-600 tabular shrink-0 select-none">
                      #{entry.id}
                    </span>

                    {/* Timestamp */}
                    <span className="text-zinc-500 tabular shrink-0 select-none">
                      {formatTimestamp(entry.timestamp)}
                    </span>

                    {/* Level Pill */}
                    <span
                      className={cn(
                        'px-1.5 py-0.2 rounded text-[10px] font-semibold tracking-wider shrink-0 border uppercase',
                        levelColorClass(entry.level)
                      )}
                    >
                      {entry.level}
                    </span>

                    {/* Logger tag */}
                    <span className="text-indigo-400 shrink-0 font-medium truncate max-w-[150px] sm:max-w-[200px]">
                      [{entry.logger}]
                    </span>

                    {/* Trace ID indicator if available */}
                    {entry.trace_id && (
                      <span className="hidden md:inline-flex px-1.5 py-0.2 rounded bg-zinc-800 text-zinc-400 text-[10px] border hairline-border shrink-0">
                        trace:{entry.trace_id.slice(0, 8)}
                      </span>
                    )}

                    {/* Message */}
                    <span className="text-zinc-200 break-words flex-1 min-w-0">
                      {entry.message}
                    </span>

                    {/* Copy button */}
                    <button
                      onClick={(e) => handleCopyLog(entry, e)}
                      title="Copy log entry JSON"
                      className="opacity-0 group-hover:opacity-100 p-1 text-zinc-500 hover:text-zinc-200 transition-opacity shrink-0"
                    >
                      {copiedId === entry.id ? (
                        <Check className="w-3 h-3 text-emerald-400" />
                      ) : (
                        <Copy className="w-3 h-3" />
                      )}
                    </button>
                  </div>

                  {/* Expanded Detail View */}
                  {isExpanded && (
                    <div className="mt-2.5 pl-6 pr-2 py-2.5 space-y-2 border-t hairline-border bg-canvas-base/80 rounded-md text-[11px]">
                      <div className="grid grid-cols-1 sm:grid-cols-2 gap-2 text-zinc-400">
                        {entry.source_file && (
                          <div>
                            <span className="text-zinc-500">Source:</span>{' '}
                            <span className="text-zinc-300">
                              {entry.source_file}
                              {entry.line_number ? `:${entry.line_number}` : ''}
                            </span>
                          </div>
                        )}
                        {entry.function && (
                          <div>
                            <span className="text-zinc-500">Function:</span>{' '}
                            <span className="text-zinc-300">{entry.function}()</span>
                          </div>
                        )}
                        {entry.trace_id && (
                          <div className="sm:col-span-2">
                            <span className="text-zinc-500">Full Trace ID:</span>{' '}
                            <span className="text-accent-cyan select-all">{entry.trace_id}</span>
                          </div>
                        )}
                      </div>

                      {/* Extra Data */}
                      {entry.extra_data && Object.keys(entry.extra_data).length > 0 && (
                        <div className="space-y-1">
                          <span className="text-zinc-500 font-semibold">Extra Context:</span>
                          <pre className="p-2 rounded bg-surface-card border hairline-border overflow-x-auto text-[10px] text-zinc-300">
                            {JSON.stringify(entry.extra_data, null, 2)}
                          </pre>
                        </div>
                      )}

                      {/* Exception stack trace */}
                      {entry.exception && (
                        <div className="space-y-1">
                          <span className="text-rose-400 font-semibold flex items-center space-x-1">
                            <AlertCircle className="w-3 h-3" />
                            <span>Exception Traceback:</span>
                          </span>
                          <pre className="p-2.5 rounded bg-rose-950/30 border border-rose-500/20 text-rose-300 overflow-x-auto whitespace-pre-wrap text-[10px] leading-relaxed">
                            {entry.exception}
                          </pre>
                        </div>
                      )}
                    </div>
                  )}
                </div>
              );
            })
          )}
          <div ref={consoleEndRef} />
        </div>
      </div>
    </div>
  );
}
