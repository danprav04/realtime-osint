'use client';

import React, { useState, useEffect, useRef, useCallback } from 'react';
import {
  Flame,
  Clock,
  ChevronLeft,
  ChevronRight,
  Pause,
  Play,
  X,
  ExternalLink,
  Layers,
} from 'lucide-react';
import { AlertItem } from '@/lib/types';
import { formatAlertTime, cn, getEventAIProvenance } from '@/lib/utils';
import { AIModelBadge } from '@/components/common/AIModelBadge';

export interface AlertWheelProps {
  alerts: AlertItem[];
  onDismissAlert?: (alertId: string) => void;
  onDismissAll?: () => void;
  onOpenDetails?: (alert: AlertItem) => void;
  autoRotateIntervalMs?: number;
  className?: string;
}

export function AlertWheel({
  alerts,
  onDismissAlert,
  onDismissAll,
  onOpenDetails,
  autoRotateIntervalMs = 7000,
  className,
}: AlertWheelProps) {
  const [currentIndex, setCurrentIndex] = useState(0);
  const [isPaused, setIsPaused] = useState(false);
  const [isHovered, setIsHovered] = useState(false);
  const [progress, setProgress] = useState(0);
  const [tick, setTick] = useState(0); // re-renders relative time every few seconds

  const lastScrollTime = useRef<number>(0);
  const totalAlerts = alerts.length;

  // Clamp current index when alerts change
  useEffect(() => {
    if (currentIndex >= totalAlerts && totalAlerts > 0) {
      setCurrentIndex(totalAlerts - 1);
      setProgress(0);
    }
  }, [totalAlerts, currentIndex]);

  // Periodic tick to refresh relative time string (e.g. "Just now" -> "20s ago")
  useEffect(() => {
    const timer = setInterval(() => {
      setTick((t) => t + 1);
    }, 5000);
    return () => clearInterval(timer);
  }, []);

  // Safe navigation handlers
  const nextAlert = useCallback(() => {
    if (totalAlerts <= 1) return;
    setCurrentIndex((prev) => (prev + 1) % totalAlerts);
    setProgress(0);
  }, [totalAlerts]);

  const prevAlert = useCallback(() => {
    if (totalAlerts <= 1) return;
    setCurrentIndex((prev) => (prev - 1 + totalAlerts) % totalAlerts);
    setProgress(0);
  }, [totalAlerts]);

  const goToAlert = useCallback((index: number) => {
    setCurrentIndex(index);
    setProgress(0);
  }, []);

  // Wheel Auto-rotation timer loop
  useEffect(() => {
    if (totalAlerts <= 1 || isPaused || isHovered) {
      return;
    }

    const stepMs = 100;
    const progressPerStep = (stepMs / autoRotateIntervalMs) * 100;

    const interval = setInterval(() => {
      setProgress((prev) => {
        if (prev + progressPerStep >= 100) {
          nextAlert();
          return 0;
        }
        return prev + progressPerStep;
      });
    }, stepMs);

    return () => clearInterval(interval);
  }, [totalAlerts, isPaused, isHovered, autoRotateIntervalMs, nextAlert]);

  // Mouse wheel listener: rolling mouse wheel rotates through alerts
  const handleWheel = (e: React.WheelEvent<HTMLDivElement>) => {
    if (totalAlerts <= 1) return;
    const now = Date.now();
    // Throttle wheel triggers to at most once per 350ms
    if (now - lastScrollTime.current < 350) return;

    if (e.deltaY > 15) {
      lastScrollTime.current = now;
      nextAlert();
    } else if (e.deltaY < -15) {
      lastScrollTime.current = now;
      prevAlert();
    }
  };

  if (!alerts || alerts.length === 0) {
    return null;
  }

  const currentAlert = alerts[currentIndex] || alerts[0];
  const rawTime =
    currentAlert.published_at ||
    currentAlert.timestamp ||
    currentAlert.created_at ||
    currentAlert.received_at;
  const timeInfo = formatAlertTime(rawTime);
  const scoringProv = getEventAIProvenance(currentAlert as any, 'scoring');
  const summarizationProv = getEventAIProvenance(currentAlert as any, 'summarization');

  // SVG circular progress ring calculations
  // radius 15, circumference = 2 * PI * 15 ≈ 94.25
  const radius = 15;
  const circumference = 2 * Math.PI * radius;
  const strokeDashoffset = circumference - (progress / 100) * circumference;

  return (
    <div
      onMouseEnter={() => setIsHovered(true)}
      onMouseLeave={() => setIsHovered(false)}
      onWheel={handleWheel}
      className={cn(
        'relative p-4 rounded-xl bg-rose-500/15 border border-rose-500/40 shadow-alert transition-all duration-300',
        'group select-none',
        className
      )}
    >
      <div className="flex items-start space-x-3.5">
        {/* Circular Countdown Progress Wheel & Flame Icon */}
        <div
          className="relative flex-shrink-0 flex items-center justify-center w-10 h-10 rounded-lg bg-rose-500/20 text-rose-300"
          title={
            totalAlerts > 1
              ? isHovered
                ? 'Rotation paused (Hovering)'
                : isPaused
                ? 'Rotation paused (Click Play to resume)'
                : `Rotating every ${Math.round(autoRotateIntervalMs / 1000)}s`
              : 'Critical Priority Alert'
          }
        >
          {totalAlerts > 1 ? (
            <svg
              className="absolute inset-0 w-10 h-10 -rotate-90 pointer-events-none"
              viewBox="0 0 36 36"
            >
              {/* Background circular track */}
              <circle
                cx="18"
                cy="18"
                r={radius}
                className="stroke-rose-950/50"
                strokeWidth="2.5"
                fill="none"
              />
              {/* Progress ring countdown */}
              <circle
                cx="18"
                cy="18"
                r={radius}
                className="stroke-rose-500 transition-[stroke-dashoffset] duration-100 ease-linear"
                strokeWidth="2.5"
                strokeDasharray={circumference}
                strokeDashoffset={isHovered || isPaused ? strokeDashoffset : strokeDashoffset}
                strokeLinecap="round"
                fill="none"
                style={{
                  filter: 'drop-shadow(0 0 3px rgba(239, 68, 68, 0.8))',
                }}
              />
            </svg>
          ) : null}

          {/* Flame Icon */}
          <Flame className="w-5 h-5 text-rose-300 animate-pulse relative z-10" />
        </div>

        {/* Alert Information Body */}
        <div className="flex-1 min-w-0 space-y-1.5">
          {/* Header row: SCORE, SOURCE, TIME */}
          <div className="flex flex-wrap items-center gap-2 text-xs">
            <span className="font-bold font-mono tracking-wider text-rose-400 uppercase">
              CRITICAL FLASH ALERT • SCORE {currentAlert.importance_score || 9}/10
            </span>
            <AIModelBadge provenance={scoringProv} size="xs" linkToSettings />

            <span className="text-zinc-500 hidden sm:inline">•</span>

            <span className="text-zinc-400 font-mono truncate max-w-[200px] sm:max-w-none">
              {currentAlert.source_name || 'REAL-TIME OSINT'}
            </span>

            <span className="text-zinc-500 hidden sm:inline">•</span>

            {/* Time Badge with Clock icon, military time & relative interval */}
            <span
              className="inline-flex items-center gap-1.5 px-2 py-0.5 rounded text-[11px] font-mono bg-rose-500/10 border border-rose-500/30 text-rose-300 shadow-sm transition-colors hover:bg-rose-500/20"
              title={timeInfo.fullStr}
            >
              <Clock className="w-3 h-3 text-rose-400 flex-shrink-0" />
              <span className="font-medium">{timeInfo.timeStr}</span>
              {timeInfo.relativeStr && (
                <span className="text-rose-400/80">({timeInfo.relativeStr})</span>
              )}
            </span>
          </div>

          {/* Headline / Title */}
          <div
            onClick={() => onOpenDetails?.(currentAlert)}
            className="group/title flex items-center gap-1.5 cursor-pointer flex-wrap"
          >
            <p className="text-sm font-semibold text-zinc-100 group-hover/title:text-white transition-colors">
              {currentAlert.headline || currentAlert.title}
            </p>
            <AIModelBadge provenance={summarizationProv} size="xs" linkToSettings />
            {onOpenDetails && (
              <ExternalLink className="w-3.5 h-3.5 text-zinc-400 opacity-0 group-hover/title:opacity-100 transition-opacity flex-shrink-0" />
            )}
          </div>

          {/* Summary Text */}
          <p className="text-xs text-zinc-300 leading-relaxed break-words">
            {currentAlert.summary}
          </p>

          {/* Wheel Segments / Navigation Bar (when multiple alerts exist) */}
          {totalAlerts > 1 && (
            <div className="pt-2 flex flex-wrap items-center justify-between gap-2 border-t border-rose-500/20 text-[11px]">
              <div className="flex items-center space-x-1.5">
                <span className="text-rose-400/80 font-mono font-medium text-[10px] tracking-wider uppercase mr-1">
                  Wheel:
                </span>
                {alerts.map((alt, idx) => {
                  const isActive = idx === currentIndex;
                  return (
                    <button
                      key={alt.id || idx}
                      onClick={() => goToAlert(idx)}
                      title={`Go to Alert ${idx + 1}: ${alt.headline || alt.title}`}
                      className={cn(
                        'h-1.5 rounded-full transition-all duration-200 pressable',
                        isActive
                          ? 'w-6 bg-rose-400 shadow-[0_0_8px_rgba(244,63,94,0.9)]'
                          : 'w-2 bg-rose-500/30 hover:bg-rose-400/60'
                      )}
                    />
                  );
                })}
              </div>

              <div className="flex items-center space-x-2 text-zinc-400 font-mono">
                {isHovered && (
                  <span className="text-[10px] text-rose-300/70 uppercase tracking-widest animate-pulse">
                    Paused on Hover
                  </span>
                )}
                {onDismissAll && (
                  <button
                    onClick={onDismissAll}
                    className="text-[10px] text-zinc-400 hover:text-rose-300 transition-colors uppercase tracking-wider"
                  >
                    Clear All ({totalAlerts})
                  </button>
                )}
              </div>
            </div>
          )}
        </div>

        {/* Right Action & Wheel Rotation Controls */}
        <div className="flex items-center space-x-1 pl-1 flex-shrink-0">
          {totalAlerts > 1 && (
            <>
              {/* Alert counter pill */}
              <div className="hidden sm:flex items-center px-1.5 py-0.5 rounded bg-rose-950/40 border border-rose-500/20 text-[11px] font-mono text-rose-300 mr-1 tabular">
                <span>{String(currentIndex + 1).padStart(2, '0')}</span>
                <span className="text-rose-500/60 mx-0.5">/</span>
                <span>{String(totalAlerts).padStart(2, '0')}</span>
              </div>

              {/* Prev Button */}
              <button
                onClick={prevAlert}
                className="p-1 rounded-md text-zinc-400 hover:text-zinc-100 hover:bg-rose-500/20 transition-colors pressable"
                title="Previous alert (Mouse Wheel Up)"
              >
                <ChevronLeft className="w-4 h-4" />
              </button>

              {/* Play / Pause Toggle */}
              <button
                onClick={() => setIsPaused((prev) => !prev)}
                className="p-1 rounded-md text-zinc-400 hover:text-zinc-100 hover:bg-rose-500/20 transition-colors pressable"
                title={isPaused ? 'Resume auto-rotation' : 'Pause auto-rotation'}
              >
                {isPaused ? <Play className="w-3.5 h-3.5" /> : <Pause className="w-3.5 h-3.5" />}
              </button>

              {/* Next Button */}
              <button
                onClick={nextAlert}
                className="p-1 rounded-md text-zinc-400 hover:text-zinc-100 hover:bg-rose-500/20 transition-colors pressable"
                title="Next alert (Mouse Wheel Down)"
              >
                <ChevronRight className="w-4 h-4" />
              </button>
            </>
          )}

          {/* Dismiss Alert Button */}
          {onDismissAlert && (
            <button
              onClick={() => onDismissAlert(currentAlert.id)}
              className="p-1 rounded-md text-zinc-400 hover:text-zinc-100 hover:bg-rose-500/20 transition-colors pressable ml-1"
              title="Dismiss alert"
            >
              <X className="w-4 h-4" />
            </button>
          )}
        </div>
      </div>
    </div>
  );
}
