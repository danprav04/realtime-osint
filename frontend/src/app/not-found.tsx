import React from 'react';
import Link from 'next/link';
import { AppIcon } from '@/components/AppIcon';
import { ArrowLeft, Radio } from 'lucide-react';

export default function NotFound() {
  return (
    <div className="min-h-[60vh] flex flex-col items-center justify-center text-center px-4">
      <div className="card-slick rounded-2xl p-8 max-w-md w-full space-y-6">
        <div className="relative mx-auto flex items-center justify-center">
          <AppIcon size={72} animated withBeacon />
        </div>

        <div className="space-y-2">
          <span className="text-[11px] font-mono tracking-widest text-accent-cyan uppercase bg-accent-cyan/10 px-2.5 py-1 rounded border border-accent-cyan/20">
            SIGNAL LOST • 404 NOT FOUND
          </span>
          <h1 className="text-xl font-bold text-zinc-100 tracking-tight mt-3">
            Sector Out of Operational Range
          </h1>
          <p className="text-xs text-zinc-400 leading-relaxed">
            The requested intelligence vector or report coordinate does not exist in the active telemetry registry.
          </p>
        </div>

        <div className="pt-2">
          <Link
            href="/"
            className="inline-flex items-center space-x-2 px-4 py-2 rounded-lg bg-surface-raised hairline-border text-xs font-medium text-zinc-100 hover:border-accent-cyan/50 hover:bg-surface-overlay transition-colors pressable"
          >
            <Radio className="w-3.5 h-3.5 text-accent-cyan" />
            <span>Return to Live Intelligence Feed</span>
          </Link>
        </div>
      </div>
    </div>
  );
}
