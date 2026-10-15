'use client';

import React, { useState } from 'react';
import { Download, Check, Sparkles } from 'lucide-react';
import { usePwaInstall } from '@/lib/usePwaInstall';
import { PwaInstallModal } from './PwaInstallModal';
import { cn } from '@/lib/utils';

interface InstallPwaButtonProps {
  label?: string;
  variant?: 'compact' | 'prominent' | 'pill' | 'header';
  appName?: string;
  className?: string;
  showWhenStandalone?: boolean;
  hideLabelOnMobile?: boolean;
}

export function InstallPwaButton({
  label = 'Install App',
  variant = 'compact',
  appName = 'OSINT Realtime',
  className,
  showWhenStandalone = false,
  hideLabelOnMobile = false,
}: InstallPwaButtonProps) {
  const {
    canInstall,
    isStandalone,
    isIOS,
    isModalOpen,
    setIsModalOpen,
    promptInstall,
  } = usePwaInstall();

  const [installSuccess, setInstallSuccess] = useState(false);

  // If already running standalone and showWhenStandalone is false, do not render
  if (isStandalone && !showWhenStandalone) {
    return null;
  }

  const handleClick = async () => {
    if (isStandalone) {
      setIsModalOpen(true);
      return;
    }
    const outcome = await promptInstall();
    if (outcome === 'accepted') {
      setInstallSuccess(true);
      setTimeout(() => setInstallSuccess(false), 4000);
    }
  };

  // Render variant styles
  let buttonClasses = '';
  if (variant === 'prominent') {
    buttonClasses =
      'w-full flex items-center justify-center space-x-2 py-2.5 px-4 rounded-xl bg-accent-cyan/15 hover:bg-accent-cyan/25 border border-accent-cyan/40 text-xs font-semibold text-accent-cyan transition-all shadow-sm pressable';
  } else if (variant === 'pill') {
    buttonClasses =
      'inline-flex items-center space-x-1.5 px-3 py-1.5 rounded-full bg-accent-cyan/10 hover:bg-accent-cyan/20 border border-accent-cyan/30 text-xs font-medium text-accent-cyan transition-colors pressable';
  } else if (variant === 'header') {
    buttonClasses =
      'flex items-center space-x-1.5 px-2.5 py-1 rounded-md bg-accent-cyan/10 hover:bg-accent-cyan/20 hairline-border border-accent-cyan/30 text-[11px] font-medium text-accent-cyan transition-colors pressable';
  } else {
    // Compact default
    buttonClasses =
      'flex items-center space-x-1.5 px-2.5 py-1 rounded-md bg-surface-card hover:bg-surface-raised hairline-border text-[11px] text-zinc-300 hover:text-zinc-100 transition-colors pressable';
  }

  return (
    <>
      <button
        type="button"
        onClick={handleClick}
        title={isStandalone ? `${appName} is installed` : `Download & Install ${appName} (PWA)`}
        className={cn(buttonClasses, className)}
      >
        {installSuccess ? (
          <>
            <Check className="w-3.5 h-3.5 text-emerald-400" />
            <span className={cn("text-emerald-400 font-medium", hideLabelOnMobile && "hidden sm:inline")}>Installed!</span>
          </>
        ) : isStandalone ? (
          <>
            <Check className="w-3.5 h-3.5 text-accent-cyan" />
            <span className={cn(hideLabelOnMobile && "hidden sm:inline")}>PWA Active</span>
          </>
        ) : (
          <>
            <Download className="w-3.5 h-3.5 text-accent-cyan" />
            <span className={cn(hideLabelOnMobile && "hidden sm:inline")}>{label}</span>
          </>
        )}
      </button>

      <PwaInstallModal
        isOpen={isModalOpen}
        onClose={() => setIsModalOpen(false)}
        isIOS={isIOS}
        appName={appName}
      />
    </>
  );
}
