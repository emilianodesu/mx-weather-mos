export type AlertLevel = 'NORMAL' | 'ADVISORY' | 'WARNING_P0';

export interface HealthResponse {
  status: string;
  database: string;
  postgis: string;
  active_model_version: string | null;
  latest_ingestion_run?: {
    run_id?: string;
    source_provider?: string;
    status?: string;
    completed_at?: string | null;
    records_loaded?: number;
  } | null;
}

export interface Municipality {
  state_id: number;
  state_name: string;
  municipality_id: number;
  municipality_name: string;
  lat: number;
  lon: number;
  elevation_m: number | null;
  utc_offset_hours?: number;
  is_ml_cohort: boolean;
  distance_km?: number | null;
}

export interface ForecastDay {
  valid_date: string;
  lead_day: number;
  issued_at: string;
  model_version: string | null;
  raw_tmax_c: number;
  pred_tmax_p10: number | null;
  pred_tmax_p50: number | null;
  pred_tmax_p90: number | null;
  raw_tmin_c: number;
  pred_tmin_p10: number | null;
  pred_tmin_p50: number | null;
  pred_tmin_p90: number | null;
  precip_mm: number | null;
  raw_precip_prob_pct: number | null;
  calibrated_rain_prob: number | null;
  wind_speed_kmh?: number | null;
  wind_gust_kmh?: number | null;
  sky_desc?: string | null;
  alert_level: AlertLevel;
}

export interface MunicipalityForecastResponse {
  municipality: Municipality;
  issued_at: string | null;
  model_version: string | null;
  days: ForecastDay[];
}

export interface AlertItem {
  state_id: number;
  state_name: string;
  municipality_id: number;
  municipality_name: string;
  valid_date: string;
  issued_at: string;
  alert_level: AlertLevel;
  reasons: string[];
  tmax_c: number | null;
  tmin_c: number | null;
  precip_mm: number | null;
  calibrated_rain_prob: number | null;
  wind_gust_kmh: number | null;
  scope_label?: string;
}

export interface MapForecastPoint {
  state_id: number;
  state_name: string;
  municipality_id: number;
  municipality_name: string;
  lat: number;
  lon: number;
  elevation_m: number | null;
  valid_date: string;
  raw_tmax_c: number;
  pred_tmax_p50: number;
  raw_tmin_c: number;
  pred_tmin_p50: number;
  precip_mm: number | null;
  calibrated_rain_prob: number | null;
  bias_delta_tmax_c: number;
  alert_level: AlertLevel;
}

export interface TrendPoint {
  date: string;
  obs_tmax_c: number | null;
  raw_tmax_c: number | null;
  ml_tmax_p50: number | null;
  obs_tmin_c: number | null;
  raw_tmin_c: number | null;
  ml_tmin_p50: number | null;
  obs_precip_mm: number | null;
  raw_precip_mm: number | null;
}

export interface TrendsResponse {
  municipality: Municipality;
  start_date: string | null;
  end_date: string | null;
  series: TrendPoint[];
}

export interface MlSkillMetricRow {
  model_version: string;
  split_name: string;
  segment_key: string;
  metric_name: string;
  raw_baseline_val: number | null;
  ml_model_val: number;
  persistence_val: number | null;
  climatology_val: number | null;
  sample_count: number;
}

export interface CalibrationBin {
  bin_center: number;
  mean_predicted_prob: number;
  observed_freq: number;
  raw_observed_freq?: number | null;
  count: number;
}

export interface MlSkillResponse {
  model_version: string | null;
  metrics: MlSkillMetricRow[];
  calibration_bins: CalibrationBin[];
}

export interface ExportReceipt {
  filename: string;
  format: 'csv' | 'parquet';
  rowCount: number;
  sha256: string;
  downloadedAt: string;
  municipalityLabel: string;
  dateRange: string;
  sourceProvenance: string;
  modelVersion: string;
}
