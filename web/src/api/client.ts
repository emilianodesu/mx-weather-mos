import type {
  AlertItem,
  AlertLevel,
  CalibrationBin,
  ExportReceipt,
  ForecastDay,
  HealthResponse,
  MapForecastPoint,
  MlSkillMetricRow,
  MlSkillResponse,
  Municipality,
  MunicipalityForecastResponse,
  TrendPoint,
  TrendsResponse,
} from '../types/api';

const API_BASE = (import.meta.env.VITE_API_BASE_URL as string | undefined) ?? '/api';
const IS_DEMO_MODE = import.meta.env.VITE_DEMO_MODE === 'true';
const DEMO_BASE = `${import.meta.env.BASE_URL}demo-data`;

const demoCache = new Map<string, Promise<unknown>>();

async function fetchDemoJson<T>(filename: string): Promise<T> {
  if (!demoCache.has(filename)) {
    demoCache.set(
      filename,
      fetch(`${DEMO_BASE}/${filename}`, { headers: { Accept: 'application/json' } }).then(
        async (res) => {
          if (!res.ok) throw new Error(`Snapshot ${res.status}: ${filename}`);
          return res.json();
        },
      ),
    );
  }
  return (await demoCache.get(filename)) as T;
}

async function computeSha256Hex(bytes: Uint8Array): Promise<string> {
  if (typeof crypto !== 'undefined' && crypto.subtle) {
    const buf = await crypto.subtle.digest('SHA-256', bytes as BufferSource);
    return Array.from(new Uint8Array(buf))
      .map((b) => b.toString(16).padStart(2, '0'))
      .join('');
  }
  return 'sha256-requires-secure-context';
}

async function fetchJson<T>(path: string, signal?: AbortSignal): Promise<T> {
  if (IS_DEMO_MODE) {
    if (path === '/health') return fetchDemoJson<T>('health.json');
    if (path.startsWith('/v1/municipalities')) return fetchDemoJson<T>('municipalities.json');
    if (path === '/v1/alerts') return fetchDemoJson<T>('alerts.json');
    if (path === '/v1/forecasts/map') return fetchDemoJson<T>('map.json');
    if (path === '/v1/ml/skill') return fetchDemoJson<T>('ml_skill.json');
    const fcstMatch = /^\/v1\/forecasts\/(\d+)\/(\d+)/.exec(path);
    if (fcstMatch) {
      const all = await fetchDemoJson<Record<string, T>>('forecasts.json');
      const hit = all[`${fcstMatch[1]}-${fcstMatch[2]}`];
      if (!hit) throw new Error(`Forecast not found in snapshot for ${fcstMatch[1]}-${fcstMatch[2]}`);
      return hit;
    }
    const trendMatch = /^\/v1\/trends\/(\d+)\/(\d+)/.exec(path);
    if (trendMatch) {
      const all = await fetchDemoJson<Record<string, Record<string, unknown>>>('trends.json');
      const hit = all[`${trendMatch[1]}-${trendMatch[2]}`];
      if (!hit) throw new Error(`Trends not found in snapshot for ${trendMatch[1]}-${trendMatch[2]}`);
      const qs = path.includes('?') ? new URLSearchParams(path.split('?')[1]) : null;
      const start = qs?.get('start_date') || '';
      const end = qs?.get('end_date') || '';
      if ((start || end) && Array.isArray(hit.series)) {
        const filtered = (hit.series as Array<{ date: string }>).filter(
          (row) => (!start || row.date >= start) && (!end || row.date <= end),
        );
        return { ...hit, series: filtered, count: filtered.length } as T;
      }
      return hit as T;
    }
  }
  const res = await fetch(`${API_BASE}${path}`, {
    headers: { Accept: 'application/json' },
    signal,
  });
  if (!res.ok) {
    const detail = await res.text().catch(() => res.statusText);
    throw new Error(`API ${res.status}: ${detail || res.statusText}`);
  }
  return (await res.json()) as T;
}

function toNum(v: unknown, fallback = 0): number {
  if (typeof v === 'number' && !Number.isNaN(v)) return v;
  if (typeof v === 'string' && v.trim() !== '') {
    const parsed = Number(v);
    if (!Number.isNaN(parsed)) return parsed;
  }
  return fallback;
}

function toNullableNum(v: unknown): number | null {
  if (v === null || v === undefined || v === '') return null;
  const parsed = Number(v);
  return Number.isNaN(parsed) ? null : parsed;
}

function normalizeAlertLevel(v: unknown): AlertLevel {
  if (v === 'WARNING_P0' || v === 'ADVISORY' || v === 'NORMAL') return v;
  return 'NORMAL';
}

function normalizeMunicipality(raw: Record<string, unknown>): Municipality {
  return {
    state_id: toNum(raw.state_id),
    state_name: String(raw.state_name ?? ''),
    municipality_id: toNum(raw.municipality_id),
    municipality_name: String(raw.municipality_name ?? ''),
    lat: toNum(raw.lat),
    lon: toNum(raw.lon),
    elevation_m: toNullableNum(raw.elevation_m),
    utc_offset_hours: raw.utc_offset_hours !== undefined ? toNum(raw.utc_offset_hours, -6) : undefined,
    is_ml_cohort: Boolean(raw.is_ml_cohort),
    distance_km: toNullableNum(raw.distance_km),
  };
}

export async function getHealth(signal?: AbortSignal): Promise<HealthResponse> {
  const raw = await fetchJson<Record<string, unknown>>('/health', signal);
  const latestRun =
    (raw.latest_ingestion_run as Record<string, unknown> | undefined) ??
    (raw.latest_run as Record<string, unknown> | undefined) ??
    null;

  return {
    status: String(raw.status ?? 'ok'),
    database:
      raw.database !== undefined
        ? String(raw.database)
        : raw.database_ok !== undefined
          ? raw.database_ok
            ? 'connected'
            : 'disconnected'
          : String(raw.db_status ?? 'connected'),
    postgis: String(raw.postgis ?? raw.postgis_version ?? 'enabled'),
    active_model_version: raw.active_model_version
      ? String(raw.active_model_version)
      : raw.model_version
        ? String(raw.model_version)
        : null,
    latest_ingestion_run: latestRun
      ? {
          run_id: latestRun.run_id ? String(latestRun.run_id) : undefined,
          source_provider: latestRun.source_provider ? String(latestRun.source_provider) : undefined,
          status: latestRun.status ? String(latestRun.status) : undefined,
          completed_at: latestRun.completed_at ? String(latestRun.completed_at) : null,
          records_loaded:
            latestRun.records_loaded !== undefined ? toNum(latestRun.records_loaded) : undefined,
        }
      : null,
  };
}

export async function getMunicipalities(
  params: { cohort_only?: boolean; search?: string; state_id?: number } = {},
  signal?: AbortSignal,
): Promise<Municipality[]> {
  const qs = new URLSearchParams();
  if (params.cohort_only !== undefined) qs.set('cohort_only', String(params.cohort_only));
  if (params.search) qs.set('search', params.search);
  if (params.state_id !== undefined) qs.set('state_id', String(params.state_id));
  const query = qs.toString() ? `?${qs.toString()}` : '';
  const raw = await fetchJson<unknown>(`/v1/municipalities${query}`, signal);
  const list = Array.isArray(raw)
    ? raw
    : Array.isArray((raw as Record<string, unknown>)?.items)
      ? ((raw as Record<string, unknown>).items as unknown[])
      : Array.isArray((raw as Record<string, unknown>)?.municipalities)
        ? ((raw as Record<string, unknown>).municipalities as unknown[])
        : [];
  return list.map((item) => normalizeMunicipality(item as Record<string, unknown>));
}

export async function getMunicipalityForecast(
  stateId: number,
  municipalityId: number,
  signal?: AbortSignal,
): Promise<MunicipalityForecastResponse> {
  const raw = await fetchJson<Record<string, unknown>>(
    `/v1/forecasts/${stateId}/${municipalityId}`,
    signal,
  );

  const rawDays = Array.isArray(raw.days)
    ? raw.days
    : Array.isArray(raw.forecasts)
      ? raw.forecasts
      : Array.isArray(raw.items)
        ? raw.items
        : [];

  const days: ForecastDay[] = rawDays.map((d) => {
    const item = d as Record<string, unknown>;
    return {
      valid_date: String(item.valid_date ?? ''),
      lead_day: toNum(item.lead_day),
      issued_at: String(item.issued_at ?? raw.issued_at ?? ''),
      model_version: item.model_version
        ? String(item.model_version)
        : raw.model_version
          ? String(raw.model_version)
          : null,
      raw_tmax_c: toNum(item.raw_tmax_c ?? item.tmax_c),
      pred_tmax_p10: toNullableNum(item.pred_tmax_p10),
      pred_tmax_p50: toNullableNum(item.pred_tmax_p50),
      pred_tmax_p90: toNullableNum(item.pred_tmax_p90),
      raw_tmin_c: toNum(item.raw_tmin_c ?? item.tmin_c),
      pred_tmin_p10: toNullableNum(item.pred_tmin_p10),
      pred_tmin_p50: toNullableNum(item.pred_tmin_p50),
      pred_tmin_p90: toNullableNum(item.pred_tmin_p90),
      precip_mm: toNullableNum(item.precip_mm),
      raw_precip_prob_pct: toNullableNum(item.raw_precip_prob_pct ?? item.precip_prob_pct),
      calibrated_rain_prob: toNullableNum(item.calibrated_rain_prob),
      wind_speed_kmh: toNullableNum(item.wind_speed_kmh),
      wind_gust_kmh: toNullableNum(item.wind_gust_kmh),
      sky_desc: item.sky_desc ? String(item.sky_desc) : null,
      alert_level: normalizeAlertLevel(item.alert_level),
    };
  });

  const munRaw = (raw.municipality as Record<string, unknown> | undefined) ?? raw;
  return {
    municipality: normalizeMunicipality(munRaw),
    issued_at: raw.issued_at ? String(raw.issued_at) : (days[0]?.issued_at ?? null),
    model_version: raw.model_version ? String(raw.model_version) : (days[0]?.model_version ?? null),
    days,
  };
}

export async function getAlerts(signal?: AbortSignal): Promise<AlertItem[]> {
  const raw = await fetchJson<unknown>('/v1/alerts', signal);
  const list = Array.isArray(raw)
    ? raw
    : Array.isArray((raw as Record<string, unknown>)?.alerts)
      ? ((raw as Record<string, unknown>).alerts as unknown[])
      : Array.isArray((raw as Record<string, unknown>)?.items)
        ? ((raw as Record<string, unknown>).items as unknown[])
        : [];

  return list.map((entry) => {
    const a = entry as Record<string, unknown>;
    const reasons = Array.isArray(a.reasons)
      ? a.reasons.map(String)
      : Array.isArray(a.trigger_reasons) && a.trigger_reasons.length > 0
        ? a.trigger_reasons.map(String)
        : a.reason
          ? [String(a.reason)]
          : ['Forecast threshold exceeded'];
    return {
      state_id: toNum(a.state_id),
      state_name: String(a.state_name ?? ''),
      municipality_id: toNum(a.municipality_id),
      municipality_name: String(a.municipality_name ?? ''),
      valid_date: String(a.valid_date ?? ''),
      issued_at: String(a.issued_at ?? ''),
      alert_level: normalizeAlertLevel(a.alert_level),
      reasons,
      tmax_c: toNullableNum(a.tmax_c ?? a.pred_tmax_p50 ?? a.raw_tmax_c),
      tmin_c: toNullableNum(a.tmin_c ?? a.pred_tmin_p50 ?? a.raw_tmin_c),
      precip_mm: toNullableNum(a.precip_mm),
      calibrated_rain_prob: toNullableNum(a.calibrated_rain_prob),
      wind_gust_kmh: toNullableNum(a.wind_gust_kmh),
      scope_label: a.scope_label ? String(a.scope_label) : 'Forecast-Threshold Alert',
    };
  });
}

export async function getMapForecasts(signal?: AbortSignal): Promise<MapForecastPoint[]> {
  const raw = await fetchJson<unknown>('/v1/forecasts/map', signal);
  const list = Array.isArray(raw)
    ? raw
    : Array.isArray((raw as Record<string, unknown>)?.items)
      ? ((raw as Record<string, unknown>).items as unknown[])
      : Array.isArray((raw as Record<string, unknown>)?.points)
        ? ((raw as Record<string, unknown>).points as unknown[])
        : [];

  return list.map((entry) => {
    const p = entry as Record<string, unknown>;
    const rawTmax = toNum(p.raw_tmax_c ?? p.tmax_c);
    const predTmax = toNum(p.pred_tmax_p50 ?? p.ml_tmax_p50, rawTmax);
    const rawTmin = toNum(p.raw_tmin_c ?? p.tmin_c);
    const predTmin = toNum(p.pred_tmin_p50 ?? p.ml_tmin_p50, rawTmin);
    const biasDelta =
      p.bias_delta_tmax_c !== undefined && p.bias_delta_tmax_c !== null
        ? toNum(p.bias_delta_tmax_c)
        : p.ml_bias_delta_c !== undefined && p.ml_bias_delta_c !== null
          ? toNum(p.ml_bias_delta_c)
          : Number((predTmax - rawTmax).toFixed(2));
    return {
      state_id: toNum(p.state_id),
      state_name: String(p.state_name ?? ''),
      municipality_id: toNum(p.municipality_id),
      municipality_name: String(p.municipality_name ?? ''),
      lat: toNum(p.lat),
      lon: toNum(p.lon),
      elevation_m: toNullableNum(p.elevation_m),
      valid_date: String(p.valid_date ?? ''),
      raw_tmax_c: rawTmax,
      pred_tmax_p50: predTmax,
      raw_tmin_c: rawTmin,
      pred_tmin_p50: predTmin,
      precip_mm: toNullableNum(p.precip_mm),
      calibrated_rain_prob: toNullableNum(p.calibrated_rain_prob),
      bias_delta_tmax_c: biasDelta,
      alert_level: normalizeAlertLevel(p.alert_level),
    };
  });
}

export async function getTrends(
  stateId: number,
  municipalityId: number,
  params: { start_date?: string; end_date?: string } = {},
  signal?: AbortSignal,
): Promise<TrendsResponse> {
  const qs = new URLSearchParams();
  if (params.start_date) qs.set('start_date', params.start_date);
  if (params.end_date) qs.set('end_date', params.end_date);
  const query = qs.toString() ? `?${qs.toString()}` : '';
  const raw = await fetchJson<Record<string, unknown>>(
    `/v1/trends/${stateId}/${municipalityId}${query}`,
    signal,
  );
  const rawSeries = Array.isArray(raw.series)
    ? raw.series
    : Array.isArray(raw.points)
      ? raw.points
      : Array.isArray(raw.items)
        ? raw.items
        : [];

  const series: TrendPoint[] = rawSeries.map((pt) => {
    const row = pt as Record<string, unknown>;
    return {
      date: String(row.date ?? row.valid_date ?? row.obs_date ?? ''),
      obs_tmax_c: toNullableNum(row.obs_tmax_c ?? row.observed_tmax_c),
      raw_tmax_c: toNullableNum(row.raw_tmax_c ?? row.forecast_tmax_c),
      ml_tmax_p50: toNullableNum(row.ml_tmax_p50 ?? row.pred_tmax_p50),
      obs_tmin_c: toNullableNum(row.obs_tmin_c ?? row.observed_tmin_c),
      raw_tmin_c: toNullableNum(row.raw_tmin_c ?? row.forecast_tmin_c),
      ml_tmin_p50: toNullableNum(row.ml_tmin_p50 ?? row.pred_tmin_p50),
      obs_precip_mm: toNullableNum(row.obs_precip_mm),
      raw_precip_mm: toNullableNum(row.raw_precip_mm ?? row.precip_mm),
    };
  });

  const munRaw = (raw.municipality as Record<string, unknown> | undefined) ?? raw;
  return {
    municipality: normalizeMunicipality(munRaw),
    start_date: raw.start_date ? String(raw.start_date) : (series[0]?.date ?? null),
    end_date: raw.end_date ? String(raw.end_date) : (series[series.length - 1]?.date ?? null),
    series,
  };
}

export async function getMlSkill(signal?: AbortSignal): Promise<MlSkillResponse> {
  const raw = await fetchJson<Record<string, unknown>>('/v1/ml/skill', signal);
  const rawMetrics = Array.isArray(raw.metrics)
    ? raw.metrics
    : raw.metrics && typeof raw.metrics === 'object'
      ? [
          ...((raw.metrics as Record<string, unknown>).overall as unknown[] ?? []),
          ...((raw.metrics as Record<string, unknown>).by_elevation_band as unknown[] ?? []),
          ...((raw.metrics as Record<string, unknown>).by_season as unknown[] ?? []),
          ...((raw.metrics as Record<string, unknown>).other as unknown[] ?? []),
        ]
      : Array.isArray(raw.evaluations)
        ? raw.evaluations
        : Array.isArray(raw.items)
          ? raw.items
          : [];
  const rawBins = Array.isArray(raw.calibration_bins)
    ? raw.calibration_bins
    : Array.isArray(raw.reliability_bins)
      ? raw.reliability_bins
      : Array.isArray(raw.calibration_curve)
        ? raw.calibration_curve
        : [];

  const metrics: MlSkillMetricRow[] = rawMetrics.map((m) => {
    const row = m as Record<string, unknown>;
    return {
      model_version: String(row.model_version ?? raw.model_version ?? 'unknown'),
      split_name: String(row.split_name ?? 'holdout'),
      segment_key: String(row.segment_key ?? 'overall'),
      metric_name: String(row.metric_name ?? ''),
      raw_baseline_val: toNullableNum(row.raw_baseline_val),
      ml_model_val: toNum(row.ml_model_val),
      persistence_val: toNullableNum(row.persistence_val),
      climatology_val: toNullableNum(row.climatology_val),
      sample_count: toNum(row.sample_count),
    };
  });

  const calibration_bins: CalibrationBin[] = rawBins.map((b) => {
    const row = b as Record<string, unknown>;
    const meanPred = toNum(row.mean_predicted_prob ?? row.bin_center ?? row.predicted_prob);
    return {
      bin_center: toNum(row.bin_center, meanPred),
      mean_predicted_prob: meanPred,
      observed_freq: toNum(row.observed_freq ?? row.fraction_of_positives ?? row.empirical_freq),
      raw_observed_freq: toNullableNum(row.raw_observed_freq),
      count: toNum(row.count ?? row.sample_count),
    };
  });

  return {
    model_version: raw.model_version ? String(raw.model_version) : (metrics[0]?.model_version ?? null),
    metrics,
    calibration_bins,
  };
}

export async function downloadVerifiedExport(params: {
  state_id?: number;
  municipality_id?: number;
  start_date?: string;
  end_date?: string;
  format: 'csv' | 'parquet';
  municipalityLabel: string;
}): Promise<ExportReceipt> {
  const qs = new URLSearchParams();
  if (params.state_id !== undefined) qs.set('state_id', String(params.state_id));
  if (params.municipality_id !== undefined) qs.set('municipality_id', String(params.municipality_id));
  if (params.start_date) qs.set('start_date', params.start_date);
  if (params.end_date) qs.set('end_date', params.end_date);
  qs.set('format', params.format);

  if (IS_DEMO_MODE) {
    const allTrends = await fetchDemoJson<Record<string, { series?: TrendPoint[] }>>('trends.json');
    const key = `${params.state_id ?? 1}-${params.municipality_id ?? 1}`;
    const rawSeries = allTrends[key]?.series ?? [];
    const filtered = rawSeries.filter(
      (pt) =>
        (!params.start_date || pt.date >= params.start_date) &&
        (!params.end_date || pt.date <= params.end_date),
    );
    const header = 'date,obs_tmax_c,obs_tmin_c,obs_precip_mm,raw_tmax_c,raw_tmin_c,ml_tmax_p50,ml_tmin_p50';
    const lines = [
      header,
      ...filtered.map((pt) =>
        [
          pt.date,
          pt.obs_tmax_c ?? '',
          pt.obs_tmin_c ?? '',
          pt.obs_precip_mm ?? '',
          pt.raw_tmax_c ?? '',
          pt.raw_tmin_c ?? '',
          pt.ml_tmax_p50 ?? '',
          pt.ml_tmin_p50 ?? '',
        ].join(','),
      ),
    ];
    const csvText = `${lines.join('\n')}\n`;
    const bytes = new TextEncoder().encode(csvText);
    const sha256 = await computeSha256Hex(bytes);
    const filename = `mxweather_state${String(params.state_id ?? 1).padStart(2, '0')}_mun${String(params.municipality_id ?? 1).padStart(3, '0')}_snapshot.csv`;
    const blob = new Blob([bytes], { type: 'text/csv;charset=utf-8' });
    const objectUrl = URL.createObjectURL(blob);
    const anchor = document.createElement('a');
    anchor.href = objectUrl;
    anchor.download = filename;
    document.body.appendChild(anchor);
    anchor.click();
    anchor.remove();
    URL.revokeObjectURL(objectUrl);

    const receipt: ExportReceipt = {
      filename,
      format: 'csv',
      rowCount: filtered.length,
      sha256,
      downloadedAt: new Date().toISOString(),
      municipalityLabel: params.municipalityLabel,
      dateRange:
        params.start_date || params.end_date
          ? `${params.start_date || 'Earliest'} → ${params.end_date || 'Latest'}`
          : 'All available dates',
      sourceProvenance:
        'Verified Static Snapshot (2026-11-19) from Open-Meteo ERA5 + NWP + LightGBM MOS (run Docker locally for Parquet)',
      modelVersion: 'mos_lgbm_20261119T014120Z',
    };
    try {
      sessionStorage.setItem('mxweather_last_export_receipt', JSON.stringify(receipt));
    } catch {
      // Ignore storage quota errors
    }
    return receipt;
  }

  const res = await fetch(`${API_BASE}/v1/export?${qs.toString()}`);
  if (!res.ok) {
    const detail = await res.text().catch(() => res.statusText);
    throw new Error(`Export failed (${res.status}): ${detail || res.statusText}`);
  }

  const sha256 = res.headers.get('X-Content-SHA256') ?? 'unavailable';
  const rowCount = Number(res.headers.get('X-Row-Count') ?? '0');
  const sourceProvenance =
    res.headers.get('X-Data-Provenance') ??
    'Open-Meteo NWP Historical Forecast + ERA5-Land Archive + LightGBM MOS';
  const modelVersion = res.headers.get('X-Model-Version') ?? 'active';

  const contentDisposition = res.headers.get('Content-Disposition') ?? '';
  const match = /filename="?([^";]+)"?/i.exec(contentDisposition);
  const fallbackName = `mx_weather_intel_${params.state_id ?? 'all'}_${params.municipality_id ?? 'all'}.${params.format}`;
  const filename = match ? match[1] : fallbackName;

  const blob = await res.blob();
  const objectUrl = URL.createObjectURL(blob);
  const anchor = document.createElement('a');
  anchor.href = objectUrl;
  anchor.download = filename;
  document.body.appendChild(anchor);
  anchor.click();
  anchor.remove();
  URL.revokeObjectURL(objectUrl);

  const receipt: ExportReceipt = {
    filename,
    format: params.format,
    rowCount: Number.isNaN(rowCount) ? 0 : rowCount,
    sha256,
    downloadedAt: new Date().toISOString(),
    municipalityLabel: params.municipalityLabel,
    dateRange:
      params.start_date || params.end_date
        ? `${params.start_date || 'Earliest'} → ${params.end_date || 'Latest'}`
        : 'All available dates',
    sourceProvenance,
    modelVersion,
  };

  try {
    sessionStorage.setItem('mxweather_last_export_receipt', JSON.stringify(receipt));
  } catch {
    // Ignore storage quota errors
  }

  return receipt;
}
