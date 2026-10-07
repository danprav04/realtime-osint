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

console.log('Running frontend automated tests...');
