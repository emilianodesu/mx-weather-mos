import { useEffect, useMemo, useState } from 'react';
import { Link, useNavigate, useSearchParams } from 'react-router-dom';
import {
  CartesianGrid,
  Legend,
  Line,
  LineChart,
  ReferenceLine,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from 'recharts';
import {
  ArrowLeft,
  Download,
  SlidersHorizontal,
  X,
} from 'lucide-react';
import {
  downloadVerifiedExport,
  getMlSkill,
  getMunicipalities,
  getTrends,
} from '../api/client';
import type {
  MlSkillResponse,
  Municipality,
  TrendsResponse,
} from '../types/api';

export function ExplorerPage() {
  const [searchParams, setSearchParams] = useSearchParams();
  const navigate = useNavigate();

  const [municipalities, setMunicipalities] = useState<Municipality[]>([]);
  const [selectedMun, setSelectedMun] = useState<Municipality | null>(null);
  const [startDate, setStartDate] = useState<string>('');
  const [endDate, setEndDate] = useState<string>('');
  const [metricVar, setMetricVar] = useState<'tmax' | 'tmin'>('tmax');
  const [selectedSegment, setSelectedSegment] = useState<string>('overall');
  const [selectedSplit, setSelectedSplit] = useState<string>('holdout');

  const [trends, setTrends] = useState<TrendsResponse | null>(null);
  const [skill, setSkill] = useState<MlSkillResponse | null>(null);

  const [loadingTrends, setLoadingTrends] = useState(false);
  const [loadingSkill, setLoadingSkill] = useState(true);
  const [exportingFormat, setExportingFormat] = useState<'csv' | 'parquet' | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [mobileFiltersOpen, setMobileFiltersOpen] = useState(false);

  // Load municipalities & ML skill on mount
  useEffect(() => {
    const controller = new AbortController();
    setLoadingSkill(true);

    Promise.allSettled([
      getMunicipalities({ cohort_only: true }, controller.signal),
      getMlSkill(controller.signal),
    ]).then(([munsRes, skillRes]) => {
      if (controller.signal.aborted) return;

      if (munsRes.status === 'fulfilled') {
        const list = munsRes.value;
        setMunicipalities(list);
        const qpState = Number(searchParams.get('state_id'));
        const qpMun = Number(searchParams.get('mun_id'));
        const matched =
          list.find((m) => m.state_id === qpState && m.municipality_id === qpMun) ??
          list[0] ??
          null;
        setSelectedMun(matched);
      }

      if (skillRes.status === 'fulfilled') {
        setSkill(skillRes.value);
      }
      setLoadingSkill(false);
    });

    return () => controller.abort();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // Fetch trends whenever municipality or date filters change
  useEffect(() => {
    if (!selectedMun) return;
    const controller = new AbortController();
    setLoadingTrends(true);
    setError(null);

    getTrends(
      selectedMun.state_id,
      selectedMun.municipality_id,
      { start_date: startDate || undefined, end_date: endDate || undefined },
      controller.signal,
    )
      .then((res) => {
        if (!controller.signal.aborted) setTrends(res);
      })
      .catch((err: unknown) => {
        if (!controller.signal.aborted) {
          setError(err instanceof Error ? err.message : 'Failed to load trends.');
        }
      })
      .finally(() => {
        if (!controller.signal.aborted) setLoadingTrends(false);
      });

    return () => controller.abort();
  }, [selectedMun, startDate, endDate]);

  const segments = useMemo(() => {
    const keys = new Set<string>(['overall']);
    skill?.metrics.forEach((m) => keys.add(m.segment_key));
    return Array.from(keys);
  }, [skill]);

  const filteredMetrics = useMemo(() => {
    if (!skill) return [];
    return skill.metrics.filter(
      (m) =>
        m.segment_key === selectedSegment &&
        (selectedSplit === 'all' || m.split_name === selectedSplit),
    );
  }, [skill, selectedSegment, selectedSplit]);

  const chartSeries = useMemo(() => {
    if (!trends) return [];
    return trends.series.map((pt) => ({
      date: pt.date,
      observed: metricVar === 'tmax' ? pt.obs_tmax_c : pt.obs_tmin_c,
      raw_nwp: metricVar === 'tmax' ? pt.raw_tmax_c : pt.raw_tmin_c,
      ml_mos: metricVar === 'tmax' ? pt.ml_tmax_p50 : pt.ml_tmin_p50,
    }));
  }, [trends, metricVar]);

  const handleExport = async (format: 'csv' | 'parquet') => {
    if (!selectedMun) return;
    setExportingFormat(format);
    setError(null);
    try {
      const receipt = await downloadVerifiedExport({
        state_id: selectedMun.state_id,
        municipality_id: selectedMun.municipality_id,
        start_date: startDate || undefined,
        end_date: endDate || undefined,
        format,
        municipalityLabel: `${selectedMun.municipality_name}, ${selectedMun.state_name}`,
      });
      navigate('/export-receipt', { state: { receipt } });
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : 'Export failed.');
    } finally {
      setExportingFormat(null);
    }
  };

  const filterForm = (
    <div className="space-y-4">
      <div>
        <label htmlFor="explorer-mun-select" className="block text-[13px] font-bold text-navy mb-1">
          Municipality
        </label>
        <select
          id="explorer-mun-select"
          value={selectedMun ? `${selectedMun.state_id}-${selectedMun.municipality_id}` : ''}
          onChange={(e) => {
            const [sId, mId] = e.target.value.split('-').map(Number);
            const found = municipalities.find(
              (m) => m.state_id === sId && m.municipality_id === mId,
            );
            if (found) {
              setSelectedMun(found);
              setSearchParams({ state_id: String(found.state_id), mun_id: String(found.municipality_id) });
            }
          }}
          className="w-full min-h-[48px] px-3 rounded-control border-2 border-surface-border bg-surface text-[15px] font-medium text-navy"
        >
          {municipalities.map((m) => (
            <option
              key={`${m.state_id}-${m.municipality_id}`}
              value={`${m.state_id}-${m.municipality_id}`}
            >
              {m.municipality_name}, {m.state_name}
            </option>
          ))}
        </select>
      </div>

      <div>
        <span className="block text-[13px] font-bold text-navy mb-1">Temperature Series</span>
        <div className="grid grid-cols-2 gap-2">
          <button
            type="button"
            onClick={() => setMetricVar('tmax')}
            aria-pressed={metricVar === 'tmax'}
            className={`min-h-[48px] rounded-control text-[15px] font-semibold transition-colors ${
              metricVar === 'tmax'
                ? 'bg-navy text-white'
                : 'bg-surface-muted text-navy border border-surface-border hover:bg-navy-light'
            }`}
          >
            Tmax (°C)
          </button>
          <button
            type="button"
            onClick={() => setMetricVar('tmin')}
            aria-pressed={metricVar === 'tmin'}
            className={`min-h-[48px] rounded-control text-[15px] font-semibold transition-colors ${
              metricVar === 'tmin'
                ? 'bg-navy text-white'
                : 'bg-surface-muted text-navy border border-surface-border hover:bg-navy-light'
            }`}
          >
            Tmin (°C)
          </button>
        </div>
      </div>

      <div className="grid grid-cols-1 gap-3">
        <div>
          <label htmlFor="start-date" className="block text-[13px] font-bold text-navy mb-1">
            Start Date
          </label>
          <input
            id="start-date"
            type="date"
            value={startDate}
            onChange={(e) => setStartDate(e.target.value)}
            className="w-full min-h-[48px] px-3 rounded-control border-2 border-surface-border bg-surface text-[15px] text-navy"
          />
        </div>
        <div>
          <label htmlFor="end-date" className="block text-[13px] font-bold text-navy mb-1">
            End Date
          </label>
          <input
            id="end-date"
            type="date"
            value={endDate}
            onChange={(e) => setEndDate(e.target.value)}
            className="w-full min-h-[48px] px-3 rounded-control border-2 border-surface-border bg-surface text-[15px] text-navy"
          />
        </div>
      </div>

      <div>
        <label htmlFor="skill-segment" className="block text-[13px] font-bold text-navy mb-1">
          ML Evaluation Slice
        </label>
        <select
          id="skill-segment"
          value={selectedSegment}
          onChange={(e) => setSelectedSegment(e.target.value)}
          className="w-full min-h-[48px] px-3 rounded-control border-2 border-surface-border bg-surface text-[15px] font-medium text-navy"
        >
          {segments.map((seg) => (
            <option key={seg} value={seg}>
              {seg}
            </option>
          ))}
        </select>
      </div>

      {/* Verified Dataset Export Controls */}
      <div className="pt-3 border-t border-surface-border space-y-2">
        <p className="text-[13px] font-bold text-navy">Verified Dataset Export (SHA-256)</p>
        <button
          type="button"
          disabled={!selectedMun || exportingFormat !== null}
          onClick={() => handleExport('csv')}
          className="w-full inline-flex items-center justify-center gap-2 px-4 min-h-[48px] rounded-control bg-navy text-white font-semibold text-[15px] hover:bg-navy-hover disabled:opacity-50 transition-colors"
        >
          <Download className="w-4 h-4" aria-hidden="true" />
          <span>{exportingFormat === 'csv' ? 'Exporting CSV…' : 'Export Verified CSV'}</span>
        </button>
        <button
          type="button"
          disabled={!selectedMun || exportingFormat !== null}
          onClick={() => handleExport('parquet')}
          className="w-full inline-flex items-center justify-center gap-2 px-4 min-h-[48px] rounded-control border-2 border-navy bg-surface text-navy font-semibold text-[15px] hover:bg-navy-light disabled:opacity-50 transition-colors"
        >
          <Download className="w-4 h-4" aria-hidden="true" />
          <span>
            {exportingFormat === 'parquet' ? 'Exporting Parquet…' : 'Export Verified Parquet'}
          </span>
        </button>
      </div>
    </div>
  );

  return (
    <div className="space-y-6">
      <div>
        <Link
          to="/map"
          className="inline-flex items-center gap-1.5 text-[14px] font-semibold text-navy hover:underline mb-1"
        >
          <ArrowLeft className="w-4 h-4" aria-hidden="true" />
          Back to Forecast Map
        </Link>
        <h1 className="text-headline text-navy">Trends, ML Verification &amp; Data Explorer</h1>
        <p className="text-guidance text-slate-700">
          Researcher workspace comparing ERA5-Land ground truth, Raw Open-Meteo NWP, and LightGBM
          MOS bias-corrected forecasts with walk-forward skill metrics and SHA-256 verified exports.
        </p>
      </div>

      {/* Mobile Sticky Filter Drawer Trigger (390px Responsive Adaptation) */}
      <div className="lg:hidden sticky top-[72px] z-30">
        <button
          type="button"
          onClick={() => setMobileFiltersOpen(true)}
          aria-expanded={mobileFiltersOpen}
          aria-controls="mobile-filter-drawer"
          className="w-full inline-flex items-center justify-between px-4 min-h-[48px] rounded-control bg-navy text-white font-semibold text-[15px] shadow-md"
        >
          <span className="inline-flex items-center gap-2">
            <SlidersHorizontal className="w-4 h-4" aria-hidden="true" />
            Filters &amp; Export:{' '}
            {selectedMun ? selectedMun.municipality_name : 'Select municipality'}
          </span>
          <span aria-hidden="true">▾</span>
        </button>
      </div>

      {/* Mobile Filter Drawer Overlay */}
      {mobileFiltersOpen && (
        <div
          id="mobile-filter-drawer"
          role="dialog"
          aria-modal="true"
          aria-label="Researcher filter controls"
          className="fixed inset-0 z-50 bg-slate-900/50 flex justify-end lg:hidden"
        >
          <div className="bg-surface w-full max-w-sm h-full overflow-y-auto p-5 space-y-4 shadow-xl">
            <div className="flex items-center justify-between border-b border-surface-border pb-3">
              <h2 className="text-metric text-navy">Filters &amp; Export</h2>
              <button
                type="button"
                onClick={() => setMobileFiltersOpen(false)}
                aria-label="Close filter drawer"
                className="inline-flex items-center justify-center min-h-[48px] min-w-[48px] rounded-control border border-surface-border text-navy"
              >
                <X className="w-5 h-5" aria-hidden="true" />
              </button>
            </div>
            {filterForm}
          </div>
        </div>
      )}

      {error && (
        <div
          role="alert"
          className="rounded-card bg-danger-bg border border-danger-border px-4 py-3 text-[15px] font-medium text-danger-text"
        >
          {error}
        </div>
      )}

      <div className="grid grid-cols-1 lg:grid-cols-12 gap-6 items-start">
        {/* Persistent Desktop <aside> Filter Sidebar (1440px) */}
        <aside
          aria-label="Researcher filters and dataset export"
          className="hidden lg:block lg:col-span-3 rounded-card bg-surface border border-surface-border p-5 shadow-sm sticky top-24"
        >
          <h2 className="text-metric text-navy mb-4">Dataset Filters</h2>
          {filterForm}
        </aside>

        {/* Main Analytical Workspace */}
        <div className="lg:col-span-9 space-y-6">
          {/* Multi-series Recharts Chart */}
          <section
            aria-labelledby="trends-chart-heading"
            className="rounded-card bg-surface border border-surface-border p-5 shadow-sm space-y-3"
          >
            <div className="flex flex-wrap items-center justify-between gap-2">
              <h2 id="trends-chart-heading" className="text-metric text-navy">
                ERA5 Observed vs. Raw NWP vs. ML MOS ({metricVar === 'tmax' ? 'Tmax °C' : 'Tmin °C'})
              </h2>
              {selectedMun && (
                <span className="text-timestamp text-slate-600">
                  {selectedMun.municipality_name}, {selectedMun.state_name} ({chartSeries.length}{' '}
                  days)
                </span>
              )}
            </div>

            {loadingTrends ? (
              <div className="h-72 bg-slate-100 rounded-control animate-pulse" />
            ) : chartSeries.length > 0 ? (
              <div className="w-full h-80">
                <ResponsiveContainer width="100%" height="100%">
                  <LineChart data={chartSeries} margin={{ top: 10, right: 20, left: 0, bottom: 5 }}>
                    <CartesianGrid strokeDasharray="3 3" stroke="#E2E8F0" />
                    <XAxis dataKey="date" tick={{ fontSize: 12 }} minTickGap={24} />
                    <YAxis unit="°C" tick={{ fontSize: 12 }} />
                    <Tooltip />
                    <Legend />
                    <Line
                      type="monotone"
                      dataKey="observed"
                      name="ERA5 Observed"
                      stroke="#0B2545"
                      strokeWidth={2.5}
                      dot={false}
                    />
                    <Line
                      type="monotone"
                      dataKey="raw_nwp"
                      name="Raw NWP"
                      stroke="#D97706"
                      strokeWidth={2}
                      strokeDasharray="5 5"
                      dot={false}
                    />
                    <Line
                      type="monotone"
                      dataKey="ml_mos"
                      name="ML MOS (p50)"
                      stroke="#15803D"
                      strokeWidth={2.5}
                      dot={false}
                    />
                  </LineChart>
                </ResponsiveContainer>
              </div>
            ) : (
              <p className="text-guidance text-slate-700 py-8 text-center">
                No paired historical observations and forecasts found for this date window.
              </p>
            )}
          </section>

          {/* ML Skill & Baseline Comparison Table */}
          <section
            aria-labelledby="ml-skill-heading"
            className="rounded-card bg-surface border border-surface-border p-5 shadow-sm space-y-4"
          >
            <div className="flex flex-wrap items-center justify-between gap-2">
              <div>
                <h2 id="ml-skill-heading" className="text-metric text-navy">
                  Walk-Forward ML Skill vs. Baselines
                </h2>
                <p className="text-timestamp text-slate-600">
                  Slice: <strong>{selectedSegment}</strong>
                  {skill?.model_version ? ` • Model: ${skill.model_version}` : ''}
                </p>
              </div>
              <div className="inline-flex rounded-control border border-surface-border bg-surface-muted p-1 gap-1">
                {[
                  { key: 'holdout', label: 'Holdout' },
                  { key: 'walk_forward_cv', label: 'Walk-Forward CV' },
                  { key: 'all', label: 'All Splits' },
                ].map((opt) => (
                  <button
                    key={opt.key}
                    type="button"
                    onClick={() => setSelectedSplit(opt.key)}
                    className={`px-3 min-h-[40px] rounded-control text-[13px] font-semibold transition-colors ${
                      selectedSplit === opt.key
                        ? 'bg-navy text-white'
                        : 'text-navy hover:bg-white'
                    }`}
                  >
                    {opt.label}
                  </button>
                ))}
              </div>
            </div>

            {loadingSkill ? (
              <div className="h-40 bg-slate-100 rounded-control animate-pulse" />
            ) : filteredMetrics.length > 0 ? (
              <div className="overflow-x-auto">
                <table className="w-full text-left border-collapse text-[14px]">
                  <thead>
                    <tr className="border-b-2 border-surface-border text-navy">
                      <th className="py-2.5 pr-4 font-bold">Metric</th>
                      <th className="py-2.5 px-3 font-bold">Split</th>
                      <th className="py-2.5 px-3 font-bold">Raw NWP</th>
                      <th className="py-2.5 px-3 font-bold">ML MOS</th>
                      <th className="py-2.5 px-3 font-bold">Persistence</th>
                      <th className="py-2.5 px-3 font-bold">Climatology</th>
                      <th className="py-2.5 pl-3 font-bold text-right">N</th>
                    </tr>
                  </thead>
                  <tbody className="divide-y divide-surface-border">
                    {filteredMetrics.map((row, idx) => (
                      <tr key={`${row.metric_name}-${row.split_name}-${idx}`} className="hover:bg-surface-muted">
                        <td className="py-2.5 pr-4 font-semibold text-navy">{row.metric_name}</td>
                        <td className="py-2.5 px-3 text-slate-700">{row.split_name}</td>
                        <td className="py-2.5 px-3 text-slate-700">
                          {row.raw_baseline_val !== null ? row.raw_baseline_val.toFixed(3) : '—'}
                        </td>
                        <td className="py-2.5 px-3 font-bold text-safe-text">
                          {row.ml_model_val.toFixed(3)}
                        </td>
                        <td className="py-2.5 px-3 text-slate-700">
                          {row.persistence_val !== null ? row.persistence_val.toFixed(3) : '—'}
                        </td>
                        <td className="py-2.5 px-3 text-slate-700">
                          {row.climatology_val !== null ? row.climatology_val.toFixed(3) : '—'}
                        </td>
                        <td className="py-2.5 pl-3 text-right text-slate-700">
                          {row.sample_count.toLocaleString()}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            ) : (
              <p className="text-guidance text-slate-700">
                No ML evaluation records available for slice <strong>{selectedSegment}</strong>.
              </p>
            )}
          </section>

          {/* Reliability Diagram (Calibration Curve) */}
          <section
            aria-labelledby="calibration-heading"
            className="rounded-card bg-surface border border-surface-border p-5 shadow-sm space-y-3"
          >
            <h2 id="calibration-heading" className="text-metric text-navy">
              Rain Probability Reliability Diagram (Isotonic Calibration)
            </h2>
            <p className="text-timestamp text-slate-600">
              Closer alignment with the diagonal reference line indicates better probabilistic
              calibration for daily precipitation ≥ 1.0 mm.
            </p>

            {skill && skill.calibration_bins.length > 0 ? (
              <div className="w-full h-72">
                <ResponsiveContainer width="100%" height="100%">
                  <LineChart
                    data={skill.calibration_bins}
                    margin={{ top: 16, right: 20, left: 0, bottom: 24 }}
                  >
                    <CartesianGrid strokeDasharray="3 3" stroke="#E2E8F0" />
                    <XAxis
                      dataKey="mean_predicted_prob"
                      type="number"
                      domain={[0, 1]}
                      tick={{ fontSize: 12 }}
                      label={{ value: 'Predicted Rain Probability', position: 'insideBottom', offset: -14, fontSize: 12 }}
                    />
                    <YAxis
                      domain={[0, 1]}
                      tick={{ fontSize: 12 }}
                      label={{ value: 'Observed Frequency', angle: -90, position: 'insideLeft', fontSize: 12 }}
                    />
                    <Tooltip
                      formatter={(value: unknown) =>
                        typeof value === 'number' ? value.toFixed(3) : String(value ?? '')
                      }
                      labelFormatter={(label: unknown) =>
                        `Predicted Prob: ${typeof label === 'number' ? label.toFixed(3) : String(label ?? '')}`
                      }
                    />
                    <Legend verticalAlign="top" height={32} />
                    <ReferenceLine
                      segment={[
                        { x: 0, y: 0 },
                        { x: 1, y: 1 },
                      ]}
                      stroke="#94A3B8"
                      strokeDasharray="4 4"
                    />
                    <Line
                      type="monotone"
                      dataKey="observed_freq"
                      name="ML Isotonic Calibrated"
                      stroke="#0B2545"
                      strokeWidth={2.5}
                      dot={{ r: 4 }}
                    />
                    {skill.calibration_bins.some((b) => b.raw_observed_freq !== null) && (
                      <Line
                        type="monotone"
                        dataKey="raw_observed_freq"
                        name="Raw NWP Prob."
                        stroke="#D97706"
                        strokeWidth={2}
                        strokeDasharray="5 5"
                        dot={{ r: 3 }}
                      />
                    )}
                  </LineChart>
                </ResponsiveContainer>
              </div>
            ) : (
              <p className="text-guidance text-slate-700 py-4">
                Reliability curve bins will appear once the rain calibration model is evaluated.
              </p>
            )}
          </section>
        </div>
      </div>
    </div>
  );
}
