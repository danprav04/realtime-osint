export interface NormalizedEvent {
  id: string;
  theme_id: string;
  source_id?: number | string | null;
  source_type: 'rss' | 'telegram' | 'newsapi' | 'reddit' | 'ais' | string;
  source_name: string;
  source_url?: string | null;
  external_id: string;
  title: string;
  raw_text: string;
  translated_text?: string | null;
  language?: string;
  author?: string | null;
  location_json?: { lat?: number; lon?: number; name?: string } | null;
  metadata_json?: Record<string, any>;
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
  source_type: 'rss' | 'telegram' | 'newsapi' | 'reddit' | 'ais' | string;
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
  provider: 'gemini' | 'groq';
  model_name: string;
  temperature?: number;
  api_key_override?: string | null;
}

export interface AIStageConfig {
  stage_id?: string;
  provider: 'gemini' | 'groq';
  model_name: string;
  temperature: number;
  api_key_override?: string | null;
  fallbacks?: AIFallbackConfig[];
}

export interface AIPipelineSettings {
  extraction: AIStageConfig;
  scoring: AIStageConfig;
  summarization: AIStageConfig;
  visuals: AIStageConfig;
}

export interface SystemSettings {
  api_keys: Record<string, string>;
  notification_threshold: number;
  polling_intervals: Record<string, number>;
  max_lookback_hours?: Record<string, number>;
  pizza_meter_enabled?: boolean;
}

export interface PizzaLocation {
  name: string;
  address: string;
  busyness: number;
  normal_busyness?: number;
  status: 'Normal' | 'Elevated' | 'Spike';
  distance: string;
}

export interface PizzaMeterData {
  status: 'NOMINAL' | 'ELEVATED' | 'HIGH' | 'CRITICAL';
  level: number;
  score: number;
  trend: 'stable' | 'increasing' | 'decreasing';
  baseline_multiplier: number;
  description: string;
  monitored_region: string;
  last_updated: string;
  locations: PizzaLocation[];
  history: Array<{ timestamp: string; score: number }>;
  enabled?: boolean;
  hourly_calibrated?: boolean;
  arlington_hour?: number;
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

export interface SituationSummaryData {
  id: string;
  generated_at: string;
  cached: boolean;
  theme_id: string;
  theme_name: string;
  time_range: string;
  active_ai_model: string;
  active_embedding_model: string;
  threat_trajectory: 'Escalating' | 'Elevated' | 'Stable' | 'De-escalating' | 'Volatile';
  threat_level_score: number;
  executive_bluf: string;
  strategic_assessment: string;
  key_developments: SummaryKeyDevelopment[];
  clusters: SummaryClusterBreakdown[];
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
    pizza_meter?: PizzaMeterData | null;
  };
}

