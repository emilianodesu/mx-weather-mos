import { useEffect, useMemo, useState } from 'react';
import { Link } from 'react-router-dom';
import { ArrowLeft, ArrowRight, Layers, MapPin } from 'lucide-react';
import { getMapForecasts } from '../api/client';
import type { MapForecastPoint } from '../types/api';
import { AudioSummaryButton } from '../components/AudioSummaryButton';
import { MexicoLeafletMap, type MapLayerMode } from '../components/MexicoLeafletMap';

const LAYER_OPTIONS: { id: MapLayerMode; label: string; description: string }[] = [
  {
    id: 'tmax',
    label: 'ML Tmax (°C)',
    description: 'Bias-corrected LightGBM MOS median maximum temperature.',
  },
  {
    id: 'tmin',
    label: 'ML Tmin (°C)',
    description: 'Bias-corrected LightGBM MOS median minimum temperature.',
  },
  {
    id: 'precip',
    label: 'Precipitation (mm)',
    description: 'Forecast daily accumulated precipitation.',
  },
  {
    id: 'bias_delta',
    label: 'ML Bias Delta (°C)',
    description: 'Magnitude of ML correction (ML Tmax p50 minus Raw NWP Tmax).',
  },
];

export function ForecastMapPage() {
  const [points, setPoints] = useState<MapForecastPoint[]>([]);
  const [layer, setLayer] = useState<MapLayerMode>('tmax');
  const [selectedPoint, setSelectedPoint] = useState<MapForecastPoint | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    const controller = new AbortController();
    setLoading(true);
    setError(null);

    getMapForecasts(controller.signal)
      .then((items) => {
        if (controller.signal.aborted) return;
        setPoints(items);
        if (items.length > 0) setSelectedPoint(items[0]);
      })
      .catch((err: unknown) => {
        if (!controller.signal.aborted) {
          setError(err instanceof Error ? err.message : 'Failed to load geospatial forecasts.');
        }
      })
      .finally(() => {
        if (!controller.signal.aborted) setLoading(false);
      });

    return () => controller.abort();
  }, []);

  const activeLayerMeta = useMemo(
    () => LAYER_OPTIONS.find((opt) => opt.id === layer) ?? LAYER_OPTIONS[0],
    [layer],
  );

  const selectedKey = selectedPoint
    ? `${selectedPoint.state_id}-${selectedPoint.municipality_id}`
    : null;

  const audioSummary = selectedPoint
    ? `${selectedPoint.municipality_name}, ${selectedPoint.state_name} for ${selectedPoint.valid_date}. ML-calibrated maximum temperature is ${selectedPoint.pred_tmax_p50.toFixed(1)} degrees Celsius compared to raw NWP ${selectedPoint.raw_tmax_c.toFixed(1)} degrees, a bias adjustment of ${selectedPoint.bias_delta_tmax_c.toFixed(1)} degrees. Minimum temperature is ${selectedPoint.pred_tmin_p50.toFixed(1)} degrees. Alert level is ${selectedPoint.alert_level}.`
    : 'Geospatial forecast map of Mexico showing ML-calibrated municipal forecasts.';

  return (
    <div className="space-y-6">
      <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-3">
        <div>
          <Link
            to="/"
            className="inline-flex items-center gap-1.5 text-[14px] font-semibold text-navy hover:underline mb-1"
          >
            <ArrowLeft className="w-4 h-4" aria-hidden="true" />
            Back to Portal Home
          </Link>
          <h1 className="text-headline text-navy">Geospatial Forecast &amp; ML Bias Map</h1>
          <p className="text-guidance text-slate-700">{activeLayerMeta.description}</p>
        </div>

        <AudioSummaryButton summaryText={audioSummary} />
      </div>

      {/* Layer Toggle Bar (48x48px touch targets) */}
      <div
        role="group"
        aria-label="Map layer selector"
        className="flex flex-wrap items-center gap-2 bg-surface border border-surface-border rounded-card p-3 shadow-sm"
      >
        <span className="inline-flex items-center gap-1.5 text-[13px] font-bold text-navy px-2">
          <Layers className="w-4 h-4" aria-hidden="true" />
          Layer:
        </span>
        {LAYER_OPTIONS.map((opt) => {
          const active = opt.id === layer;
          return (
            <button
              key={opt.id}
              type="button"
              onClick={() => setLayer(opt.id)}
              aria-pressed={active}
              className={`px-4 min-h-[48px] min-w-[48px] rounded-control text-[15px] font-semibold transition-colors ${
                active
                  ? 'bg-navy text-white shadow-sm'
                  : 'bg-surface-muted text-navy border border-surface-border hover:bg-navy-light'
              }`}
            >
              {opt.label}
            </button>
          );
        })}
      </div>

      {error && (
        <div
          role="alert"
          className="rounded-card bg-danger-bg border border-danger-border px-4 py-3 text-[15px] font-medium text-danger-text"
        >
          {error}
        </div>
      )}

      <div className="grid grid-cols-1 lg:grid-cols-12 gap-6 items-start">
        <div className="lg:col-span-8 space-y-3">
          <MexicoLeafletMap
            points={points}
            layer={layer}
            selectedKey={selectedKey}
            onSelectPoint={setSelectedPoint}
          />
          <div className="flex flex-wrap items-center justify-between gap-2 text-timestamp text-slate-600 px-1">
            <span>
              Showing <strong>{points.length}</strong> scored municipalities (OpenStreetMap +
              PostGIS coordinates).
            </span>
            <span>Click any marker to inspect Raw NWP vs. ML MOS values.</span>
          </div>
        </div>

        {/* Accessible Inspector Panel */}
        <aside
          aria-label="Selected municipality forecast detail"
          className="lg:col-span-4 rounded-card bg-surface border border-surface-border p-5 shadow-sm space-y-4"
        >
          <div className="flex items-center gap-2 text-navy">
            <MapPin className="w-5 h-5 shrink-0" aria-hidden="true" />
            <h2 className="text-metric">Municipality Inspector</h2>
          </div>

          {loading ? (
            <div className="space-y-3 animate-pulse">
              <div className="h-5 w-48 bg-slate-200 rounded" />
              <div className="h-24 w-full bg-slate-100 rounded" />
            </div>
          ) : selectedPoint ? (
            <div className="space-y-4">
              <div>
                <p className="text-[18px] font-bold text-navy">
                  {selectedPoint.municipality_name}, {selectedPoint.state_name}
                </p>
                <p className="text-timestamp text-slate-600">
                  Valid: {selectedPoint.valid_date} • Lat {selectedPoint.lat.toFixed(3)}, Lon{' '}
                  {selectedPoint.lon.toFixed(3)}
                  {selectedPoint.elevation_m !== null
                    ? ` • ${Math.round(selectedPoint.elevation_m)} m`
                    : ''}
                </p>
              </div>

              <div className="grid grid-cols-2 gap-3">
                <div className="rounded-control bg-surface-muted border border-surface-border p-3">
                  <span className="block text-timestamp text-slate-600">ML Tmax (p50)</span>
                  <span className="block text-metric text-navy">
                    {selectedPoint.pred_tmax_p50.toFixed(1)} °C
                  </span>
                  <span className="block text-timestamp text-slate-600">
                    Raw: {selectedPoint.raw_tmax_c.toFixed(1)} °C
                  </span>
                </div>

                <div className="rounded-control bg-surface-muted border border-surface-border p-3">
                  <span className="block text-timestamp text-slate-600">ML Tmin (p50)</span>
                  <span className="block text-metric text-navy">
                    {selectedPoint.pred_tmin_p50.toFixed(1)} °C
                  </span>
                  <span className="block text-timestamp text-slate-600">
                    Raw: {selectedPoint.raw_tmin_c.toFixed(1)} °C
                  </span>
                </div>

                <div className="rounded-control bg-surface-muted border border-surface-border p-3">
                  <span className="block text-timestamp text-slate-600">ML Bias Delta</span>
                  <span className="block text-metric text-navy">
                    {selectedPoint.bias_delta_tmax_c >= 0 ? '+' : ''}
                    {selectedPoint.bias_delta_tmax_c.toFixed(2)} °C
                  </span>
                </div>

                <div className="rounded-control bg-surface-muted border border-surface-border p-3">
                  <span className="block text-timestamp text-slate-600">Precipitation</span>
                  <span className="block text-metric text-navy">
                    {selectedPoint.precip_mm !== null
                      ? `${selectedPoint.precip_mm.toFixed(1)} mm`
                      : '—'}
                  </span>
                </div>
              </div>

              <Link
                to={`/explorer?state_id=${selectedPoint.state_id}&mun_id=${selectedPoint.municipality_id}`}
                className="w-full inline-flex items-center justify-center gap-2 px-4 min-h-[48px] rounded-control bg-navy text-white font-semibold text-[15px] hover:bg-navy-hover transition-colors"
              >
                <span>Analyze Series &amp; Export</span>
                <ArrowRight className="w-4 h-4" aria-hidden="true" />
              </Link>
            </div>
          ) : (
            <p className="text-guidance text-slate-700">
              No scored municipalities found yet. Run the backfill, training, and scoring pipeline.
            </p>
          )}
        </aside>
      </div>
    </div>
  );
}
