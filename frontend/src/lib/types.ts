export interface RelatedReportItem {
  event_id: string;
  headline: string;
  source_name: string;
  source_type?: string;
  importance_score: number;
  similarity: number;
  published_at?: string | null;
}

export interface ScoringBreakdown {
  final_score: number;
  priority_level: 'low' | 'medium' | 'high' | 'critical' | string;
  severity_score: number;
  novelty_score: number;
  verification_confidence: 'low' | 'medium' | 'high' | string;
  confidence_score: number;
  corroboration_status: string;
  corroboration_count: number;
  corroborating_sources: string[];
  same_source_echo_count?: number;
  is_same_source_echo?: boolean;
  max_similarity?: number;
  source_reliability_tier?: string;
  source_reliability_label?: string;
  signal_type?: string;
  signal_verification?: string;
  velocity_status?: string;
  calibration_notes?: string[];
  related_reports?: RelatedReportItem[];
  retroactively_corroborated?: boolean;
  is_contradiction?: boolean;
  contradicting_sources?: string[];
  contradiction_details?: string[];
  recency_weighted_corroboration?: number;
  historical_precedents?: any[];
  entity_baselines?: Record<string, any>;
}

export interface AIModelInfo {
  provider: 'gemini' | 'groq' | 'aihubmix' | 'mistral' | 'cloudflare' | 'zai' | 'siliconflow' | 'huggingface' | 'heuristic' | 'system' | string;
  model: string;
}

export interface AIProvenance {
  extraction?: AIModelInfo;
  scoring?: AIModelInfo;
  summarization?: AIModelInfo;
  visuals?: AIModelInfo;
  embedding?: AIModelInfo;
  synthesizer?: AIModelInfo;
}

export interface NormalizedEvent {
  id: string;
  theme_id: string;
  source_id?: number | string | null;
  source_type: 'rss' | 'telegram' | 'newsapi' | 'reddit' | 'ais' | 'bluesky' | 'mastodon' | 'seismic' | 'ukmto' | 'oref' | string;
  source_name: string;
  source_url?: string | null;
  external_id: string;
  title: string;
  raw_text: string;
  translated_text?: string | null;
  language?: string;
  author?: string | null;
  location_json?: { lat?: number; lon?: number; name?: string } | null;
  metadata_json?: {
    scoring_breakdown?: ScoringBreakdown;
    trend_category?: string;
    ai_provenance?: AIProvenance;
    [key: string]: any;
  };
  ai_provenance?: AIProvenance;
  importance_score?: number | null;
  score_rationale?: string | null;
  summary?: string | null;
  headline?: string | null;
  entities_json?: string[];
  is_analyzed?: boolean;
  published_at: string;
  ingested_at: string;
}

export interface AnalyzedEvent extends NormalizedEvent {
  translated_text?: string | null;
  importance_score: number;
  score_rationale: string;
  summary: string;
  headline: string;
  entities: string[];
}

export interface AlertItem {
  id: string;
  event_id?: string;
  title: string;
  headline?: string | null;
  summary?: string | null;
  importance_score?: number | null;
  source_name?: string | null;
  source_url?: string | null;
  ai_provenance?: AIProvenance;
  metadata_json?: Record<string, any>;
  published_at?: string | null;
  timestamp?: string | null;
  created_at?: string | null;
  received_at?: string | null;
}

export interface Theme {
  id: string;
  slug: string;
  db_id?: number;
  name: string;
  description?: string;
  keywords: string[];
  entities: string[];
  scoring_prompt: string;
  is_active: boolean;
  alert_threshold: number;
  importance_threshold?: number;
  notification_frequency_limit_minutes?: number;
  source_presets?: Record<string, any>;
  created_at?: string;
  updated_at?: string;
}

export interface Source {
  id: string;
  theme_id: string;
  name: string;
  source_type: 'rss' | 'telegram' | 'newsapi' | 'reddit' | 'ais' | 'bluesky' | 'mastodon' | 'seismic' | 'ukmto' | 'oref' | string;
  target: string;
  config_json: Record<string, any>;
  is_enabled: boolean;
  enabled?: boolean;
  interval_seconds: number;
  polling_interval_sec?: number;
  max_lookback_hours?: number;
  status: 'active' | 'paused' | 'error';
  error_message?: string | null;
  last_polled_at?: string | null;
  created_at?: string | null;
  updated_at?: string | null;
}

export interface DashboardStats {
  total_events: number;
  high_priority_events: number;
  active_sources: number;
  last_updated: string;
  last_update_timestamp?: string;
  score_distribution: {
    '1-3': number;
    '4-6': number;
    '7-8': number;
    '9-10': number;
    [key: string]: number;
  };
  source_breakdown: {
    rss: number;
    telegram: number;
    newsapi: number;
    reddit: number;
    ais: number;
    [key: string]: number;
  };
  hourly_volume: Array<{ hour: string; count: number }>;
}

export interface AIFallbackConfig {
  provider: 'gemini' | 'groq' | 'aihubmix' | 'mistral' | 'cloudflare' | 'zai' | 'siliconflow' | string;
  model_name: string;
  temperature?: number;
  api_key_override?: string | null;
}

export interface AIStageConfig {
  stage_id?: string;
  provider: 'gemini' | 'groq' | 'aihubmix' | 'mistral' | 'cloudflare' | 'zai' | 'siliconflow' | string;
  model_name: string;
  temperature: number;
  api_key_override?: string | null;
  fallbacks?: AIFallbackConfig[];
}

export interface GatekeeperStats {
  evaluated_count: number;
  passed_count: number;
  fast_passed_count: number;
  dropped_deterministic_count: number;
  dropped_llm_count: number;
  total_dropped_count: number;
  drop_rate_pct: number;
  batch_calls_count: number;
  batch_size: number;
  last_model_used?: string | null;
  last_evaluated_at?: string | null;
}

export interface AIPipelineSettings {
  gatekeeper?: AIStageConfig;
  extraction: AIStageConfig;
  scoring: AIStageConfig;
  summarization: AIStageConfig;
  visuals: AIStageConfig;
  situation_summary?: AIStageConfig;
}

export interface SystemSettings {
  api_keys: Record<string, string>;
  notification_threshold: number;
  polling_intervals: Record<string, number>;
  max_lookback_hours?: Record<string, number>;
  gatekeeper_stats?: GatekeeperStats;
}

export interface MapTrustworthiness {
  score: number;
  label: 'Highly Corroborated' | 'Developing / Verified' | 'Single-Source Unverified' | 'Isolated Outlier' | string;
  verdict: string;
  corroboration_count: number;
  corroborating_sources: string[];
  independent_sources?: string[];
  same_source_updates?: number;
  source_diversity: number;
}

export interface MapNeighbor {
  event_id: string;
  title: string;
  source_name: string;
  similarity: number;
}

export interface MapNode {
  id: string;
  event_id: string;
  title: string;
  headline: string;
  summary: string;
  source_name: string;
  source_type: string;
  source_url?: string | null;
  published_at?: string | null;
  importance_score: number;
  location?: { name?: string; country?: string; lat?: number; lon?: number } | null;
  x: number;
  y: number;
  cluster_id?: number | null;
  cluster_label?: string | null;
  is_cluster_lead?: boolean;
  centrality?: number;
  trustworthiness: MapTrustworthiness;
  nearest_neighbors: MapNeighbor[];
  ai_provenance?: AIProvenance;
}

export interface MapEdge {
  source: string;
  target: string;
  similarity: number;
  intra_cluster?: boolean;
}

export interface MapCluster {
  id: number;
  label: string;
  size: number;
  center_x: number;
  center_y: number;
  radius?: number;
  avg_trust?: number;
  avg_importance?: number;
  lead_event_id?: string;
  is_perimeter?: boolean;
  event_ids?: string[];
}

export interface ModelQuotaUsage {
  requests_today: number;
  limit: number;
}

export interface QuotaTracker {
  date: string;
  models: Record<string, ModelQuotaUsage>;
  total_embeddings_generated: number;
}

export interface EmbeddingsMapData {
  nodes: MapNode[];
  edges: MapEdge[];
  clusters: MapCluster[];
  stats: {
    total_nodes: number;
    total_edges: number;
    total_clusters: number;
    active_model: string;
    quota_tracker?: QuotaTracker;
  };
}

export interface EmbeddingStatus {
  total_events: number;
  embedded_events: number;
  pending_events: number;
  coverage_percentage: number;
  primary_model: string;
  fallback_model: string;
  hf_space_configured: boolean;
  hf_embedding_url?: string;
  quota_tracker?: QuotaTracker;
}

export interface SummaryCitationEvent {
  id: string;
  title: string;
  headline: string;
  summary: string;
  raw_text: string;
  translated_text?: string | null;
  source_name: string;
  source_type: string;
  source_url?: string | null;
  importance_score: number;
  score_rationale?: string | null;
  published_at: string;
  ingested_at: string;
  entities_json?: string[];
  location_json?: { name?: string; country?: string; lat?: number; lon?: number } | null;
  cluster_id?: number | null;
  cluster_label?: string | null;
  trustworthiness_score: number;
  trustworthiness_label: string;
  corroboration_count: number;
  corroborating_sources?: string[];
  ai_provenance?: AIProvenance;
}

export interface SummaryClusterBreakdown {
  cluster_id: number;
  label: string;
  title: string;
  executive_summary: string;
  operational_implication: string;
  threat_level: 'low' | 'medium' | 'high' | 'critical';
  report_count: number;
  avg_importance_score: number;
  max_importance_score: number;
  avg_trustworthiness_score: number;
  trustworthiness_label: string;
  independent_sources: string[];
  source_types: string[];
  top_entities: string[];
  center_x?: number;
  center_y?: number;
  is_fresh?: boolean;
  latest_event_age_hours?: number;
  cluster_recency_weight?: number;
  events: SummaryCitationEvent[];
}

export interface SummaryKeyDevelopment {
  bullet: string;
  event_id?: string | null;
  headline?: string | null;
  importance_score?: number | null;
  source_name?: string | null;
  trustworthiness_label?: string | null;
  trustworthiness_score?: number | null;
}

export interface HistoricalRollup {
  id: string;
  theme_id: number;
  period_start: string;
  period_end: string;
  epoch_key: string;
  total_events: number;
  critical_events_count: number;
  max_importance_score: number;
  avg_importance_score: number;
  executive_summary: string;
  entity_counts_json: Record<string, number>;
  location_counts_json: Record<string, number>;
  source_stats_json: Record<string, any>;
  notable_events_json: any[];
  is_compacted: boolean;
  created_at?: string;
}

export interface SourceCredibilityItem {
  source_name: string;
  source_type: string;
  credibility_score: number;
  tier: 'Tier 1 (High Reliability)' | 'Tier 2 (Established / Mainstream)' | 'Tier 3 (Aggregator / Secondary)' | 'Tier 4 (Unverified / Social / Bot)' | string;
  badge_color: string;
  total_reports: number;
  corroboration_rate: number;
  avg_importance_score: number;
  latest_activity?: string | null;
  recency_weight: number;
}

export interface SituationSummaryData {
  id: string;
  generated_at: string;
  cached: boolean;
  theme_id: string;
  theme_name: string;
  time_range: string;
  active_ai_model: string;
  active_embedding_model: string;
  ai_provenance?: AIProvenance;
  threat_trajectory: 'Escalating' | 'Elevated' | 'Stable' | 'De-escalating' | 'Volatile';
  threat_level_score: number;
  executive_bluf: string;
  strategic_assessment: string;
  key_developments: SummaryKeyDevelopment[];
  clusters: SummaryClusterBreakdown[];
  source_credibility_leaderboard?: SourceCredibilityItem[];
  historical_rollups?: HistoricalRollup[];
  unverified_outliers: {
    assessment: string;
    events: SummaryCitationEvent[];
  };
  watchlist_indicators: string[];
  telemetry: {
    total_events: number;
    embedded_events: number;
    embedding_coverage_pct: number;
    corroborated_reports_count: number;
    corroboration_rate_pct: number;
    cross_source_edges_count: number;
    total_clusters: number;
    isolated_outliers_count: number;
    critical_alerts_count: number;
    avg_importance_score: number;
    max_importance_score: number;
    velocity_status: 'Surge' | 'Elevated' | 'Normal' | 'Quiet';
    events_last_6h: number;
    events_prev_18h: number;
    source_breakdown: Record<string, number>;
    top_entities: Array<{ name: string; count: number }>;
    top_locations: Array<{ name: string; count: number }>;
    top_categories: Array<{ name: string; count: number }>;
  };
}

export interface KeyUsageTelemetry {
  key_hash: string;
  masked_key: string;
  key_field: string;
  key_index?: number;
  provider: string;
  status: 'active' | 'minute_cooldown' | 'daily_exhausted' | 'unauthorized' | 'unconfigured' | string;
  total_requests: number;
  successful_requests: number;
  failed_requests: number;
  rate_limited_count: number;
  success_rate_pct: number;
  total_tokens: number;
  prompt_tokens: number;
  completion_tokens: number;
  last_used_at?: string | null;
  remaining_requests?: number | null;
  remaining_tokens?: number | null;
  limit_requests?: number | null;
  limit_tokens?: number | null;
  reset_requests_sec?: number | null;
  reset_tokens_sec?: number | null;
  cooldown_remaining_sec: number;
  cooldown_reason?: string;
  throttled_models?: string[];
  model_cooldowns?: Record<string, { cooldown_remaining_sec: number; reason?: string }>;
}

export interface ModelUsageTelemetry {
  model_name: string;
  provider: string;
  stages: string[];
  is_primary: boolean;
  is_fallback: boolean;
  status: 'ready' | 'active' | 'cooldown' | 'exhausted' | string;
  cooldown_remaining_sec: number;
  total_requests: number;
  successful_requests: number;
  failed_requests: number;
  rate_limited_count: number;
  success_rate_pct: number;
  total_tokens: number;
  prompt_tokens: number;
  completion_tokens: number;
  avg_latency_ms: number;
  last_latency_ms: number;
  last_used_at?: string | null;
  last_error?: string | null;
}

export interface ProviderUsageTelemetry {
  id: string;
  name: string;
  is_connected: boolean;
  total_requests: number;
  successful_requests: number;
  failed_requests: number;
  rate_limited_count: number;
  total_tokens: number;
  prompt_tokens: number;
  completion_tokens: number;
  active_keys_count: number;
  cooldown_keys_count: number;
  cooldown_models_count: number;
  last_used_at?: string | null;
  keys: KeyUsageTelemetry[];
  models: ModelUsageTelemetry[];
}

export interface UsageTelemetryResponse {
  updated_at: string;
  summary: {
    total_requests: number;
    total_tokens: number;
    active_keys: number;
    keys_in_cooldown: number;
    total_models: number;
    models_in_cooldown: number;
  };
  providers: Record<string, ProviderUsageTelemetry>;
  all_models: ModelUsageTelemetry[];
  all_keys: KeyUsageTelemetry[];
}

export interface ServerLogEntry {
  id: number;
  timestamp: string;
  level: 'DEBUG' | 'INFO' | 'WARNING' | 'ERROR' | 'CRITICAL' | string;
  logger: string;
  message: string;
  trace_id?: string | null;
  source_file?: string | null;
  line_number?: number | null;
  function?: string | null;
  exception?: string | null;
  extra_data?: Record<string, any> | null;
}

export interface ServerLogStats {
  total_buffered: number;
  max_capacity: number;
  counts_by_level: Record<string, number>;
  active_loggers: string[];
  oldest_timestamp?: string | null;
  newest_timestamp?: string | null;
  uptime_seconds: number;
  created_at?: string;
}

export interface ServerLogsResponse {
  logs: ServerLogEntry[];
  total: number;
  returned: number;
  stats: ServerLogStats;
}


