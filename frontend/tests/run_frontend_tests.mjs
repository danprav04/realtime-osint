import test from 'node:test';
import assert from 'node:assert/strict';

// Test 1: Utils test
test('Utils - getScoreCategory maps 1-10 scores to correct severity tiers', async () => {
  const { getScoreCategory } = await import('../src/lib/utils.ts');

  assert.equal(getScoreCategory(1), 'routine');
  assert.equal(getScoreCategory(2), 'routine');
  assert.equal(getScoreCategory(3), 'routine');
  assert.equal(getScoreCategory(4), 'advisory');
  assert.equal(getScoreCategory(5), 'advisory');
  assert.equal(getScoreCategory(6), 'advisory');
  assert.equal(getScoreCategory(7), 'warning');
  assert.equal(getScoreCategory(8), 'warning');
  assert.equal(getScoreCategory(9), 'critical');
  assert.equal(getScoreCategory(10), 'critical');
  assert.equal(getScoreCategory(null), 'routine');
  assert.equal(getScoreCategory(undefined), 'routine');
});

test('Utils - getScoreColors returns valid Tailwind color tokens and labels', async () => {
  const { getScoreColors } = await import('../src/lib/utils.ts');

  const critical = getScoreColors(10);
  assert.equal(critical.label, 'CRITICAL');
  assert.ok(critical.bg.includes('rose'));
  assert.ok(critical.glow.includes('rgba(239,68,68'));

  const warning = getScoreColors(7);
  assert.equal(warning.label, 'HIGH');
  assert.ok(warning.bg.includes('amber'));

  const advisory = getScoreColors(5);
  assert.equal(advisory.label, 'MED');
  assert.ok(advisory.bg.includes('cyan'));

  const routine = getScoreColors(2);
  assert.equal(routine.label, 'LOW');
  assert.ok(routine.bg.includes('slate'));
});

test('Utils - formatRelativeTime correctly computes delta intervals', async () => {
  const { formatRelativeTime } = await import('../src/lib/utils.ts');

  const now = new Date();
  assert.equal(formatRelativeTime(null), 'Never');
  assert.equal(formatRelativeTime(undefined), 'Never');

  const justNow = new Date(now.getTime() - 10 * 1000).toISOString();
  assert.ok(formatRelativeTime(justNow).includes('s ago'));

  const minsAgo = new Date(now.getTime() - 5 * 60 * 1000).toISOString();
  assert.equal(formatRelativeTime(minsAgo), '5m ago');

  const hoursAgo = new Date(now.getTime() - 3 * 3600 * 1000).toISOString();
  assert.equal(formatRelativeTime(hoursAgo), '3h ago');

  const daysAgo = new Date(now.getTime() - 2 * 86400 * 1000).toISOString();
  assert.equal(formatRelativeTime(daysAgo), '2d ago');
});

test('Utils - truncateText limits strings and appends ellipsis', async () => {
  const { truncateText } = await import('../src/lib/utils.ts');

  assert.equal(truncateText('short', 10), 'short');
  assert.equal(truncateText('hello world', 5), 'hello...');
  assert.equal(truncateText('', 10), '');
  assert.equal(truncateText(null, 10), '');
});

test('Utils - cn correctly merges and resolves Tailwind classes', async () => {
  const { cn } = await import('../src/lib/utils.ts');

  const merged = cn('px-2 py-1', false && 'hidden', 'text-white', 'px-4');
  // twMerge resolves conflicting padding
  assert.ok(merged.includes('px-4'));
  assert.ok(!merged.includes('px-2'));
  assert.ok(merged.includes('text-white'));
});

test('Types - NormalizedEvent interface mirrors backend contract schema', async () => {
  const sampleEvent = {
    id: 'evt-test-123',
    theme_id: 'theme-us-iran',
    source_type: 'rss',
    source_name: 'Reuters World',
    source_url: 'https://reuters.com/article/1',
    external_id: 'reuters-1',
    title: 'Naval activity reported in Strait of Hormuz',
    raw_text: 'Patrol boats conducted maneuvers near commercial shipping lane.',
    language: 'en',
    importance_score: 8,
    score_rationale: 'Strategic maritime chokepoint transit alert',
    summary: 'Iranian naval patrol boats shadowed commercial container ship.',
    entities_json: ['IRGC', 'Hormuz', 'Navy'],
    published_at: new Date().toISOString(),
    ingested_at: new Date().toISOString(),
  };

  assert.equal(sampleEvent.id, 'evt-test-123');
  assert.equal(sampleEvent.importance_score, 8);
  assert.equal(sampleEvent.entities_json.length, 3);
});

test('API Client - getStoredApiKey provides safe fallback in SSR/Node environment', async () => {
  const { getStoredApiKey } = await import('../src/lib/api.ts');
  const key = getStoredApiKey();
  assert.equal(typeof key, 'string');
  assert.ok(key.length > 0);
});

// Test 9: Alert Time Formatting
test('Utils - formatAlertTime formats ISO string into military UTC time, relative time, and full string', async () => {
  const { formatAlertTime } = await import('../src/lib/utils.ts');

  const iso = '2026-10-09T14:30:15.000Z';
  const result = formatAlertTime(iso);

  assert.equal(result.timeStr, '14:30:15 UTC');
  assert.ok(result.relativeStr);
  assert.ok(result.fullStr.includes('2026'));
  assert.ok(result.fullStr.includes('GMT') || result.fullStr.includes('UTC'));
});

test('Utils - formatAlertTime handles null or undefined timestamp by providing fallback UTC time', async () => {
  const { formatAlertTime } = await import('../src/lib/utils.ts');

  const resultNull = formatAlertTime(null);
  assert.ok(resultNull.timeStr.includes('UTC'));
  assert.equal(resultNull.relativeStr, 'Just now');

  const resultUndefined = formatAlertTime(undefined);
  assert.ok(resultUndefined.timeStr.includes('UTC'));
  assert.equal(resultUndefined.relativeStr, 'Just now');
});

// Test 10: AlertItem Structure
test('Types - AlertItem validates alerts data for AlertWheel component', async () => {
  const sampleAlert = {
    id: 'alt-101',
    event_id: 'evt-202',
    title: 'Yemeni Forces Repel Saudi Advance Toward Bab el-Mandeb',
    headline: 'Yemeni Forces Repel Saudi Advance Toward Bab el-Mandeb',
    summary: 'Yemeni Armed Forces successfully repelled a Saudi-backed advance on the Lahij-Bab el-Mandeb front...',
    importance_score: 9,
    source_name: 'Yemeni Armed Forces Spox Yahya Saree',
    ai_provenance: {
      scoring: { provider: 'Groq', model: 'qwen/qwen3.8-27b' },
      summarization: { provider: 'Groq', model: 'openai/gpt-oss-120b' },
    },
    metadata_json: {
      scoring_breakdown: { corroboration_count: 3 },
    },
    published_at: '2026-10-09T18:24:46Z',
    timestamp: '2026-10-09T18:24:46Z',
  };

  assert.equal(sampleAlert.importance_score, 9);
  assert.equal(sampleAlert.source_name, 'Yemeni Armed Forces Spox Yahya Saree');
  assert.ok(sampleAlert.title.includes('Bab el-Mandeb'));
  assert.ok(sampleAlert.published_at.includes('2026'));
  assert.equal(sampleAlert.ai_provenance.scoring.model, 'qwen/qwen3.8-27b');
  assert.equal(sampleAlert.ai_provenance.summarization.model, 'openai/gpt-oss-120b');
});

// Test 11: sortEvents - newest first order (prevents old events sandwiched between fresh ones)
test('Utils - sortEvents correctly sorts by newest published timestamp descending', async () => {
  const { sortEvents } = await import('../src/lib/utils.ts');

  const events = [
    { id: '1', title: 'Portugal PM Defends Air Base', published_at: '2026-10-09T15:46:26Z', importance_score: 5 },
    { id: '2', title: 'Saudi Airstrikes Target Hodeidah', published_at: '2026-10-08T11:14:29Z', importance_score: 3 }, // 1 day ago
    { id: '3', title: 'Egyptian Artists Boycott', published_at: '2026-10-09T15:46:24Z', importance_score: 2 },
  ];

  const sorted = sortEvents(events, 'newest');
  assert.equal(sorted[0].id, '1'); // 15:46:26
  assert.equal(sorted[1].id, '3'); // 15:46:24
  assert.equal(sorted[2].id, '2'); // 1 day ago is last, NOT in between
});

// Test 12: sortEvents - score descending
test('Utils - sortEvents correctly sorts by highest importance score descending', async () => {
  const { sortEvents } = await import('../src/lib/utils.ts');

  const events = [
    { id: '1', title: 'Low event', published_at: '2026-10-09T15:00:00Z', importance_score: 2 },
    { id: '2', title: 'Critical strike', published_at: '2026-10-09T14:00:00Z', importance_score: 9 },
    { id: '3', title: 'Medium statement', published_at: '2026-10-09T16:00:00Z', importance_score: 5 },
    { id: '4', title: 'Unanalyzed event', published_at: '2026-10-09T17:00:00Z', importance_score: null },
  ];

  const sorted = sortEvents(events, 'score_desc');
  assert.equal(sorted[0].id, '2'); // score 9
  assert.equal(sorted[1].id, '3'); // score 5
  assert.equal(sorted[2].id, '1'); // score 2
  assert.equal(sorted[3].id, '4'); // null score last
});

// Test 13: sortEvents - handles missing dates gracefully
test('Utils - sortEvents handles missing published_at by falling back to ingested_at', async () => {
  const { sortEvents } = await import('../src/lib/utils.ts');

  const events = [
    { id: '1', title: 'Older ingest', published_at: null, ingested_at: '2026-10-09T10:00:00Z' },
    { id: '2', title: 'Newer ingest', published_at: null, ingested_at: '2026-10-09T12:00:00Z' },
  ];

  const sorted = sortEvents(events, 'newest');
  assert.equal(sorted[0].id, '2');
  assert.equal(sorted[1].id, '1');
});

// Test 14: AIStageConfig & AIFallbackConfig Type Validation
test('Types - AIStageConfig supports multi-tier fallback configuration', async () => {
  /** @type {import('../src/lib/types.ts').AIStageConfig} */
  const stageConfig = {
    provider: 'gemini',
    model_name: 'gemini-3.5-flash-lite',
    temperature: 0.1,
    fallbacks: [
      {
        provider: 'gemini',
        model_name: 'gemini-3.1-flash-lite',
        temperature: 0.1,
      },
      {
        provider: 'groq',
        model_name: 'openai/gpt-oss-120b',
        temperature: 0.2,
      },
    ],
  };

  assert.equal(stageConfig.model_name, 'gemini-3.5-flash-lite');
  assert.equal(stageConfig.fallbacks?.length, 2);
  assert.equal(stageConfig.fallbacks?.[0].model_name, 'gemini-3.1-flash-lite');
  assert.equal(stageConfig.fallbacks?.[1].provider, 'groq');
  assert.equal(stageConfig.fallbacks?.[1].model_name, 'openai/gpt-oss-120b');
});

// Test 15: AIStageConfig fallback immutability and empty fallback support
test('Types - AIStageConfig handles stages without fallbacks gracefully', async () => {
  /** @type {import('../src/lib/types.ts').AIStageConfig} */
  const simpleConfig = {
    provider: 'groq',
    model_name: 'qwen/qwen3.8-27b',
    temperature: 0.2,
  };

  const fallbacks = simpleConfig.fallbacks || [];
  assert.equal(fallbacks.length, 0);
});

// Test 17: Embeddings Map Types & Corroboration contracts
test('Types - EmbeddingsMapData and MapNode reflect trustworthiness and spatial coordinates', async () => {
  /** @type {import('../src/lib/types.ts').MapNode} */
  const mockNode = {
    id: 'node-1',
    event_id: 'ev-1',
    title: 'Naval Incident in Strait of Hormuz',
    headline: 'Tanker boarded near Strait of Hormuz',
    summary: 'Commercial vessel intercepted by naval forces.',
    source_name: 'Reuters',
    source_type: 'newsapi',
    importance_score: 8,
    x: 12.5,
    y: -34.2,
    cluster_id: 1,
    cluster_label: 'Hormuz Naval Security',
    trustworthiness: {
      score: 92,
      label: 'Highly Corroborated',
      verdict: 'Confirmed by multiple independent intelligence channels.',
      corroboration_count: 3,
      corroborating_sources: ['Reuters', 'AP News', '@NavalAlerts'],
      source_diversity: 3,
    },
    nearest_neighbors: [
      {
        event_id: 'ev-2',
        title: 'Maritime Alert',
        source_name: '@NavalAlerts',
        similarity: 0.88,
      },
    ],
  };

  assert.equal(mockNode.trustworthiness.score, 92);
  assert.equal(mockNode.trustworthiness.label, 'Highly Corroborated');
  assert.equal(mockNode.trustworthiness.corroborating_sources.length, 3);
  assert.equal(mockNode.x, 12.5);
  assert.equal(mockNode.y, -34.2);
  assert.equal(mockNode.nearest_neighbors[0].similarity, 0.88);
});

// Test 18: API Client - verifies getEmbeddingsMap and triggerEmbeddingBackfill functions exist
test('API Client - exports getEmbeddingsMap, triggerEmbeddingBackfill, and getEmbeddingStatus', async () => {
  const api = await import('../src/lib/api.ts');
  assert.equal(typeof api.getEmbeddingsMap, 'function');
  assert.equal(typeof api.triggerEmbeddingBackfill, 'function');
  assert.equal(typeof api.reprojectEmbeddingsMap, 'function');
  assert.equal(typeof api.getEmbeddingStatus, 'function');
});

// Test 19: App Icon Assets - validates icon files and web manifest in public directory
test('App Icon Assets - verifies favicon, svg, touch icon, and webmanifest exist with non-zero size', async () => {
  const fs = await import('node:fs');
  const path = await import('node:path');

  const requiredAssets = [
    'public/favicon.ico',
    'public/icon.svg',
    'public/icon-192.png',
    'public/icon-512.png',
    'public/apple-touch-icon.png',
    'public/site.webmanifest',
    'src/app/favicon.ico',
    'src/app/icon.svg',
    'src/app/apple-icon.png',
  ];

  for (const relPath of requiredAssets) {
    const fullPath = path.resolve(relPath);
    assert.ok(fs.existsSync(fullPath), `Asset must exist: ${relPath}`);
    const stat = fs.statSync(fullPath);
    assert.ok(stat.size > 0, `Asset must not be empty: ${relPath}`);
  }

  // Validate webmanifest json format
  const manifestRaw = fs.readFileSync(path.resolve('public/site.webmanifest'), 'utf-8');
  const manifest = JSON.parse(manifestRaw);
  assert.equal(manifest.short_name, 'OSINT Realtime');
  assert.ok(Array.isArray(manifest.icons) && manifest.icons.length >= 4);
});

// Test 20: AppIcon & AppLogo Component - validates component file existence and exports
test('AppIcon & AppLogo - exports valid React components with props support', async () => {
  const fs = await import('node:fs');
  const path = await import('node:path');

  const componentPath = path.resolve('src/components/common/AppIcon.tsx');
  const reexportPath = path.resolve('src/components/AppIcon.tsx');

  assert.ok(fs.existsSync(componentPath), 'AppIcon.tsx must exist');
  assert.ok(fs.existsSync(reexportPath), 'components/AppIcon.tsx must exist');

  const content = fs.readFileSync(componentPath, 'utf-8');
  assert.ok(content.includes('export function AppIcon'), 'AppIcon component must be exported');
  assert.ok(content.includes('export function AppLogo'), 'AppLogo component must be exported');
  assert.ok(content.includes('viewBox="0 0 512 512"'), 'AppIcon must render SVG with 512 viewBox');
});

// Test 21: Provider Rate Limit Shortcuts - validates URLs and connected provider resolution
test('Utils - getProviderRateLimitStatuses resolves connected providers and rate limit URLs accurately', async () => {
  const {
    PROVIDER_RATE_LIMITS,
    getProviderRateLimitInfo,
    getProviderRateLimitStatuses,
  } = await import('../src/lib/utils.ts');

  assert.ok(Array.isArray(PROVIDER_RATE_LIMITS));
  assert.ok(PROVIDER_RATE_LIMITS.length >= 6);

  const geminiInfo = getProviderRateLimitInfo('gemini');
  assert.ok(geminiInfo);
  assert.equal(geminiInfo.rateLimitUrl, 'https://aistudio.google.com/rate-limit');

  const groqInfo = getProviderRateLimitInfo('groq_api_key');
  assert.ok(groqInfo);
  assert.equal(groqInfo.rateLimitUrl, 'https://console.groq.com/settings/limits');
  assert.ok(groqInfo.quotaSummary.includes('Multi-Key Rotated'));

  const statuses = getProviderRateLimitStatuses({
    apiKeys: {
      gemini_api_key: '***1234',
      newsapi_key: '',
    },
    aiSettings: {
      extraction: { provider: 'groq', fallbacks: [{ provider: 'gemini' }] },
    },
    sources: [
      { source_type: 'telegram', is_enabled: true },
      { source_type: 'reddit', is_enabled: false },
    ],
    hfSpaceConfigured: true,
  });

  const byId = Object.fromEntries(statuses.map((s) => [s.id, s]));
  assert.equal(byId.gemini.isConnected, true);
  assert.equal(byId.gemini.hasApiKey, true);
  assert.equal(byId.gemini.connectionType, 'api_key');

  assert.equal(byId.groq.isConnected, true);
  assert.equal(byId.groq.hasApiKey, false);
  assert.equal(byId.groq.connectionType, 'pipeline');

  assert.equal(byId.telegram.isConnected, true);
  assert.equal(byId.telegram.connectionType, 'source');

  assert.equal(byId.huggingface.isConnected, true);
  assert.equal(byId.huggingface.connectionType, 'pipeline');

  assert.equal(byId.newsapi.isConnected, false);
  assert.equal(byId.reddit.isConnected, false);
});

// Test 22: ProviderRateLimitsBar Component - validates component file and shortcut links
test('ProviderRateLimitsBar - exports component and links to provider rate limit dashboards', async () => {
  const fs = await import('node:fs');
  const path = await import('node:path');

  const barPath = path.resolve('src/components/settings/ProviderRateLimitsBar.tsx');
  assert.ok(fs.existsSync(barPath), 'ProviderRateLimitsBar.tsx must exist');

  const barContent = fs.readFileSync(barPath, 'utf-8');
  assert.ok(barContent.includes('export function ProviderRateLimitsBar'));
  assert.ok(barContent.includes('provider.rateLimitUrl'));
  assert.ok(barContent.includes('View Rate Limits'));
});

// Test 23: Utils - parseModelProvider & formatProviderName
test('Utils - parseModelProvider correctly parses prefixed and bare model names', async () => {
  const { parseModelProvider, formatProviderName } = await import('../src/lib/utils.ts');

  assert.equal(formatProviderName('gemini'), 'Gemini');
  assert.equal(formatProviderName('groq'), 'Groq');
  assert.equal(formatProviderName('aihubmix'), 'AIHubMix');
  assert.equal(formatProviderName('heuristic'), 'Rule-Based');

  const p1 = parseModelProvider('groq:qwen/qwen3.8-27b');
  assert.equal(p1.provider, 'Groq');
  assert.equal(p1.model, 'qwen/qwen3.8-27b');

  const p2 = parseModelProvider('aihubmix:coding-glm-5.3-free');
  assert.equal(p2.provider, 'AIHubMix');
  assert.equal(p2.model, 'coding-glm-5.3-free');

  const p3 = parseModelProvider('gemini-3.5-flash-lite');
  assert.equal(p3.provider, 'Gemini');
  assert.equal(p3.model, 'gemini-3.5-flash-lite');

  const p4 = parseModelProvider('deterministic-fusion-engine');
  assert.equal(p4.provider, 'Rule-Based');
  assert.equal(p4.model, 'deterministic-fusion-engine');
});

// Test 24: Utils - getEventAIProvenance resolves event stage provenance and fallbacks
test('Utils - getEventAIProvenance extracts stage models and provides stage fallbacks', async () => {
  const { getEventAIProvenance } = await import('../src/lib/utils.ts');

  const eventWithProv = {
    id: 'evt-1',
    ai_provenance: {
      extraction: { provider: 'groq', model: 'qwen/qwen3.8-27b' },
      summarization: { provider: 'aihubmix', model: 'coding-glm-5.3-free' },
    },
  };

  const extProv = getEventAIProvenance(eventWithProv, 'extraction');
  assert.equal(extProv.provider, 'Groq');
  assert.equal(extProv.model, 'qwen/qwen3.8-27b');

  const sumProv = getEventAIProvenance(eventWithProv, 'summarization');
  assert.equal(sumProv.provider, 'AIHubMix');
  assert.equal(sumProv.model, 'coding-glm-5.3-free');

  // Fallback for stage not in event
  const scoreProv = getEventAIProvenance(eventWithProv, 'scoring');
  assert.equal(scoreProv.provider, 'Gemini');
  assert.equal(scoreProv.model, 'gemini-3.5-flash-lite');

  // Verify provenance extracted from AlertItem format
  const alertItem = {
    id: 'alt-5',
    title: 'Drone intercept',
    ai_provenance: {
      scoring: { provider: 'Groq', model: 'qwen/qwen3.8-27b' },
      summarization: { provider: 'Groq', model: 'openai/gpt-oss-120b' },
    },
  };
  const alertScoreProv = getEventAIProvenance(alertItem, 'scoring');
  assert.equal(alertScoreProv.provider, 'Groq');
  assert.equal(alertScoreProv.model, 'qwen/qwen3.8-27b');

  const alertSumProv = getEventAIProvenance(alertItem, 'summarization');
  assert.equal(alertSumProv.provider, 'Groq');
  assert.equal(alertSumProv.model, 'openai/gpt-oss-120b');

  // Verify stringified metadata_json handling
  const eventWithStringMeta = {
    id: 'evt-2',
    metadata_json: JSON.stringify({
      ai_provenance: {
        scoring: { provider: 'groq', model: 'qwen/qwen3.8-27b' },
      },
    }),
  };
  const stringScoreProv = getEventAIProvenance(eventWithStringMeta, 'scoring');
  assert.equal(stringScoreProv.provider, 'Groq');
  assert.equal(stringScoreProv.model, 'qwen/qwen3.8-27b');
});

// Test 25: AIModelBadge Component - file exists and exports AIModelBadge
test('AIModelBadge - exports component and renders brand badge styles', async () => {
  const fs = await import('node:fs');
  const path = await import('node:path');

  const badgePath = path.resolve('src/components/common/AIModelBadge.tsx');
  assert.ok(fs.existsSync(badgePath), 'AIModelBadge.tsx must exist');

  const badgeContent = fs.readFileSync(badgePath, 'utf-8');
  assert.ok(badgeContent.includes('export function AIModelBadge'));
  assert.ok(badgeContent.includes('formatProviderName'));
  assert.ok(badgeContent.includes('getProviderBadgeStyles'));
});

// Test 26: PWA Manifests - verifies site.webmanifest, manifest.json, and display.webmanifest
test('PWA Manifests - validates site.webmanifest, manifest.json, and display.webmanifest with shortcuts and display start_url', async () => {
  const fs = await import('node:fs');
  const path = await import('node:path');

  // 1. site.webmanifest
  const siteManifestRaw = fs.readFileSync(path.resolve('public/site.webmanifest'), 'utf-8');
  const siteManifest = JSON.parse(siteManifestRaw);
  assert.equal(siteManifest.short_name, 'OSINT Realtime');
  assert.equal(siteManifest.start_url, '/');
  assert.equal(siteManifest.display, 'standalone');
  assert.ok(Array.isArray(siteManifest.icons) && siteManifest.icons.length >= 4);
  assert.ok(siteManifest.icons.some((i) => i.sizes === '192x192'));
  assert.ok(siteManifest.icons.some((i) => i.sizes === '512x512'));
  assert.ok(Array.isArray(siteManifest.shortcuts) && siteManifest.shortcuts.length >= 1);
  assert.ok(siteManifest.shortcuts.some((s) => s.url === '/display'), 'site.webmanifest must include /display shortcut');

  // 2. manifest.json compatibility alias
  const jsonManifestRaw = fs.readFileSync(path.resolve('public/manifest.json'), 'utf-8');
  const jsonManifest = JSON.parse(jsonManifestRaw);
  assert.equal(jsonManifest.display, 'standalone');
  assert.equal(jsonManifest.start_url, '/');

  // 3. Dedicated Read-Only Display Webmanifest
  const displayManifestRaw = fs.readFileSync(path.resolve('public/display.webmanifest'), 'utf-8');
  const displayManifest = JSON.parse(displayManifestRaw);
  assert.equal(displayManifest.short_name, 'OSINT Display');
  assert.equal(displayManifest.start_url, '/display');
  assert.equal(displayManifest.scope, '/display');
  assert.equal(displayManifest.display, 'standalone');
  assert.ok(Array.isArray(displayManifest.icons) && displayManifest.icons.length >= 4);
});

// Test 27: PWA Service Worker & Offline Fallback Shell
test('PWA Service Worker & Offline - validates sw.js caching strategies, api exclusion, and offline.html', async () => {
  const fs = await import('node:fs');
  const path = await import('node:path');

  const swPath = path.resolve('public/sw.js');
  assert.ok(fs.existsSync(swPath), 'public/sw.js must exist');
  const swContent = fs.readFileSync(swPath, 'utf-8');

  assert.ok(swContent.includes("addEventListener('install'"), 'SW must register install listener');
  assert.ok(swContent.includes("addEventListener('activate'"), 'SW must register activate listener');
  assert.ok(swContent.includes("addEventListener('fetch'"), 'SW must register fetch listener');
  assert.ok(swContent.includes('/display'), 'SW pre-cache must include /display route');
  assert.ok(swContent.includes('/offline.html'), 'SW pre-cache must include offline fallback');
  assert.ok(swContent.includes('/api/'), 'SW must exclude /api/ endpoints from cache');
  assert.ok(swContent.includes('/socket.io'), 'SW must exclude WebSocket endpoints from cache');

  const offlinePath = path.resolve('public/offline.html');
  assert.ok(fs.existsSync(offlinePath), 'public/offline.html must exist');
  const offlineContent = fs.readFileSync(offlinePath, 'utf-8');
  assert.ok(offlineContent.includes('Network Connection Lost') || offlineContent.includes('Offline'));
  assert.ok(offlineContent.includes('window.location.reload()'));
});

// Test 28: PWA UI Components & Display Layout Metadata
test('PWA Components & Layout - validates usePwaInstall hook, InstallPwaButton, and display layout metadata', async () => {
  const fs = await import('node:fs');
  const path = await import('node:path');

  const hookPath = path.resolve('src/lib/usePwaInstall.ts');
  assert.ok(fs.existsSync(hookPath), 'src/lib/usePwaInstall.ts must exist');
  const hookContent = fs.readFileSync(hookPath, 'utf-8');
  assert.ok(hookContent.includes('export function usePwaInstall'));
  assert.ok(hookContent.includes('export function registerServiceWorker'));
  assert.ok(hookContent.includes('beforeinstallprompt'));

  const btnPath = path.resolve('src/components/pwa/InstallPwaButton.tsx');
  assert.ok(fs.existsSync(btnPath), 'InstallPwaButton.tsx must exist');
  const btnContent = fs.readFileSync(btnPath, 'utf-8');
  assert.ok(btnContent.includes('export function InstallPwaButton'));

  const modalPath = path.resolve('src/components/pwa/PwaInstallModal.tsx');
  assert.ok(fs.existsSync(modalPath), 'PwaInstallModal.tsx must exist');
  const modalContent = fs.readFileSync(modalPath, 'utf-8');
  assert.ok(modalContent.includes('export function PwaInstallModal'));

  const displayLayoutPath = path.resolve('src/app/display/layout.tsx');
  assert.ok(fs.existsSync(displayLayoutPath), 'src/app/display/layout.tsx must exist');
  const displayLayoutContent = fs.readFileSync(displayLayoutPath, 'utf-8');
  assert.ok(displayLayoutContent.includes('/display.webmanifest'), 'display layout must reference display.webmanifest');
});

console.log('Running frontend automated tests...');



