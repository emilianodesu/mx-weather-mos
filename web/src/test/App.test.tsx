import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import { describe, expect, it, vi, beforeEach } from 'vitest';
import { Layout } from '../components/Layout';
import { AlertCard } from '../components/AlertCard';
import { AudioSummaryButton } from '../components/AudioSummaryButton';
import { ExportReceiptPage } from '../pages/ExportReceiptPage';
import type { AlertItem, ExportReceipt } from '../types/api';

describe('MX Weather Intel — GoogleUX WCAG 2.1 AAA UI', () => {
  beforeEach(() => {
    vi.restoreAllMocks();
    sessionStorage.clear();
  });

  it('renders semantic WCAG landmarks (<header>, <nav>, <main>, <footer>) and scope honesty notice', () => {
    render(
      <MemoryRouter initialEntries={['/']}>
        <Layout>
          <h1>Test Portal Content</h1>
        </Layout>
      </MemoryRouter>,
    );

    expect(screen.getByRole('banner')).toBeInTheDocument(); // <header>
    expect(screen.getByRole('navigation', { name: /primary navigation/i })).toBeInTheDocument(); // <nav>
    expect(screen.getByRole('main')).toBeInTheDocument(); // <main>
    const footer = screen.getByRole('contentinfo'); // <footer>
    expect(footer).toBeInTheDocument();
    expect(footer).toHaveTextContent(/Open-Meteo NWP/i);
    expect(footer).toHaveTextContent(/ERA5 reanalysis/i);
    expect(footer).toHaveTextContent(/Street-level flood sensors.*explicitly out of scope/i);
    expect(footer).toHaveTextContent(/live Doppler radar/i);
  });

  it('renders P0 Alert Card with Gestalt Common Region styling, metrics, and Audio Summary button', () => {
    const sampleAlerts: AlertItem[] = [
      {
        state_id: 14,
        state_name: 'Jalisco',
        municipality_id: 39,
        municipality_name: 'Guadalajara',
        valid_date: '2026-11-19',
        issued_at: '2026-11-19T00:00:00Z',
        alert_level: 'WARNING_P0',
        reasons: ['Heavy precipitation >= 25 mm (38.4 mm)', 'Wind gusts >= 50 km/h (58 km/h)'],
        tmax_c: 29.5,
        tmin_c: 14.2,
        precip_mm: 38.4,
        calibrated_rain_prob: 0.84,
        wind_gust_kmh: 58,
      },
    ];

    render(
      <MemoryRouter>
        <AlertCard alerts={sampleAlerts} />
      </MemoryRouter>,
    );

    const card = screen.getByTestId('p0-alert-card');
    expect(card).toHaveClass('bg-amberAlert-surface');
    expect(card).toHaveClass('border-amberAlert-border');
    expect(screen.getByText(/WARNING P0 — Forecast Threshold/i)).toBeInTheDocument();
    expect(screen.getByText(/Guadalajara, Jalisco/i)).toBeInTheDocument();
    expect(screen.getByText('38.4 mm')).toBeInTheDocument();
    expect(screen.getByText('84%')).toBeInTheDocument();

    const audioBtn = screen.getByRole('button', { name: /play audio summary/i });
    expect(audioBtn).toBeInTheDocument();
    expect(audioBtn).toHaveClass('min-h-[48px]');
    expect(audioBtn).toHaveClass('min-w-[48px]');
  });

  it('invokes Web Speech API when Audio Summary button is clicked', async () => {
    const user = userEvent.setup();
    const speakMock = vi.fn();
    const cancelMock = vi.fn();

    Object.defineProperty(window, 'speechSynthesis', {
      configurable: true,
      value: {
        speak: speakMock,
        cancel: cancelMock,
      },
    });

    render(<AudioSummaryButton summaryText="Forecast summary for Guadalajara." />);

    const btn = screen.getByRole('button', { name: /play audio summary/i });
    await user.click(btn);

    expect(cancelMock).toHaveBeenCalledTimes(1);
    expect(speakMock).toHaveBeenCalledTimes(1);
    expect(screen.getByRole('button', { name: /stop audio summary/i })).toHaveAttribute(
      'aria-pressed',
      'true',
    );
  });

  it('renders Verified Export Receipt with checkmark, filename, row count, SHA-256 digest, and home CTA', () => {
    const receipt: ExportReceipt = {
      filename: 'mx_weather_intel_14_39.csv',
      format: 'csv',
      rowCount: 730,
      sha256: 'e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855',
      downloadedAt: '2026-11-19T01:15:00Z',
      municipalityLabel: 'Guadalajara, Jalisco',
      dateRange: '2024-11-01 → 2026-11-01',
      sourceProvenance: 'Open-Meteo NWP + ERA5-Land + LightGBM MOS',
      modelVersion: 'mos_v1_20261119',
    };

    render(
      <MemoryRouter initialEntries={[{ pathname: '/export-receipt', state: { receipt } }]}>
        <Routes>
          <Route path="/export-receipt" element={<ExportReceiptPage />} />
        </Routes>
      </MemoryRouter>,
    );

    expect(screen.getByTestId('export-receipt-card')).toBeInTheDocument();
    expect(screen.getByText(/✓ Verified Export Complete/i)).toBeInTheDocument();
    expect(screen.getByText('mx_weather_intel_14_39.csv')).toBeInTheDocument();
    expect(screen.getByText(/730 rows/i)).toBeInTheDocument();
    expect(screen.getByTestId('receipt-sha256')).toHaveTextContent(receipt.sha256);

    const homeCta = screen.getByRole('link', { name: /return to portal home/i });
    expect(homeCta).toHaveAttribute('href', '/');
    expect(homeCta).toHaveClass('min-h-[48px]');
  });
});
