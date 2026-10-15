'use client';

import { useEffect } from 'react';
import { registerServiceWorker } from '@/lib/usePwaInstall';

export function PwaRegistration() {
  useEffect(() => {
    registerServiceWorker();
  }, []);

  return null;
}
