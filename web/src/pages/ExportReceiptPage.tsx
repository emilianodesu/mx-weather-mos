import { useState } from 'react';
import { Link, useLocation } from 'react-router-dom';
import { ArrowLeft, CheckCircle2, Copy, Home, ShieldCheck } from 'lucide-react';
import type { ExportReceipt } from '../types/api';

interface LocationState {
  receipt?: ExportReceipt;
}

function loadStoredReceipt(): ExportReceipt | null {
  try {
    const raw = sessionStorage.getItem('mxweather_last_export_receipt');
    if (!raw) return null;
    return JSON.parse(raw) as ExportReceipt;
  } catch {
    return null;
  }
}

export function ExportReceiptPage() {
  const location = useLocation();
  const state = (location.state as LocationState | null) ?? null;
  const receipt = state?.receipt ?? loadStoredReceipt();

  const [copied, setCopied] = useState(false);

  const handleCopySha = async () => {
    if (!receipt) return;
    try {
      await navigator.clipboard.writeText(receipt.sha256);
      setCopied(true);
      setTimeout(() => setCopied(false), 2500);
    } catch {
      // Clipboard API may be unavailable in non-HTTPS contexts
    }
  };

  if (!receipt) {
    return (
      <section
        aria-label="No recent export receipt"
        className="max-w-2xl mx-auto rounded-card bg-surface border border-surface-border p-8 shadow-sm text-center space-y-4"
      >
        <h1 className="text-headline text-navy">No Verified Export Receipt Yet</h1>
        <p className="text-guidance text-slate-700">
          Generate a SHA-256 verified CSV or Parquet dataset export from the Trends &amp; ML
          Explorer to view its cryptographic receipt here.
        </p>
        <div className="flex flex-wrap justify-center gap-3 pt-2">
          <Link
            to="/explorer"
            className="inline-flex items-center justify-center gap-2 px-5 min-h-[48px] rounded-control bg-navy text-white font-semibold text-[15px] hover:bg-navy-hover transition-colors"
          >
            <span>Go to Trends &amp; ML Explorer</span>
          </Link>
          <Link
            to="/"
            className="inline-flex items-center justify-center gap-2 px-5 min-h-[48px] rounded-control border-2 border-navy bg-surface text-navy font-semibold text-[15px] hover:bg-navy-light transition-colors"
          >
            <Home className="w-4 h-4" aria-hidden="true" />
            <span>Return to Portal Home</span>
          </Link>
        </div>
      </section>
    );
  }

  return (
    <section
      aria-labelledby="receipt-heading"
      data-testid="export-receipt-card"
      className="max-w-3xl mx-auto rounded-card bg-surface border-2 border-safe-border p-6 sm:p-8 shadow-md space-y-6"
    >
      <div className="flex items-start gap-4">
        <span
          className="inline-flex items-center justify-center w-14 h-14 rounded-full bg-safe-bg text-safe-text shrink-0"
          aria-hidden="true"
        >
          <CheckCircle2 className="w-8 h-8" />
        </span>
        <div className="space-y-1">
          <span className="inline-flex items-center gap-1.5 px-3 py-0.5 rounded-pill bg-safe-bg text-safe-text text-[12px] font-bold uppercase tracking-wide">
            ✓ Verified Export Complete
          </span>
          <h1 id="receipt-heading" className="text-headline text-navy">
            Dataset Download &amp; Checksum Receipt
          </h1>
          <p className="text-guidance text-slate-700">
            Your {receipt.format.toUpperCase()} dataset has been streamed with a server-computed
            SHA-256 digest for reproducible research.
          </p>
        </div>
      </div>

      {/* Metadata Grid */}
      <dl className="grid grid-cols-1 sm:grid-cols-2 gap-4 rounded-card bg-surface-muted border border-surface-border p-5">
        <div>
          <dt className="text-timestamp text-slate-600">Filename</dt>
          <dd className="text-[15px] font-bold text-navy break-all">{receipt.filename}</dd>
        </div>
        <div>
          <dt className="text-timestamp text-slate-600">Verified Row Count</dt>
          <dd className="text-metric text-navy">{receipt.rowCount.toLocaleString()} rows</dd>
        </div>
        <div>
          <dt className="text-timestamp text-slate-600">Municipality / Scope</dt>
          <dd className="text-[15px] font-semibold text-navy">{receipt.municipalityLabel}</dd>
        </div>
        <div>
          <dt className="text-timestamp text-slate-600">Date Window</dt>
          <dd className="text-[15px] font-semibold text-navy">{receipt.dateRange}</dd>
        </div>
        <div className="sm:col-span-2">
          <dt className="text-timestamp text-slate-600">Provenance &amp; Model Version</dt>
          <dd className="text-[14px] text-slate-800">
            {receipt.sourceProvenance} • Model: <strong>{receipt.modelVersion}</strong> • Exported:{' '}
            {receipt.downloadedAt.slice(0, 19).replace('T', ' ')} UTC
          </dd>
        </div>
      </dl>

      {/* SHA-256 Digest Box */}
      <div className="rounded-card bg-navy-light border border-surface-border p-4 space-y-2">
        <div className="flex items-center justify-between gap-2">
          <span className="inline-flex items-center gap-1.5 text-[13px] font-bold text-navy">
            <ShieldCheck className="w-4 h-4" aria-hidden="true" />
            SHA-256 Digest (<code className="font-mono">X-Content-SHA256</code>)
          </span>
          <button
            type="button"
            onClick={handleCopySha}
            className="inline-flex items-center gap-1.5 px-3 min-h-[48px] min-w-[48px] rounded-control bg-surface border border-navy text-navy text-[13px] font-semibold hover:bg-navy hover:text-white transition-colors"
          >
            <Copy className="w-4 h-4" aria-hidden="true" />
            <span>{copied ? 'Copied!' : 'Copy SHA-256'}</span>
          </button>
        </div>
        <code
          data-testid="receipt-sha256"
          className="block font-mono text-[13px] text-navy bg-surface px-3 py-2 rounded-control border border-surface-border break-all"
        >
          {receipt.sha256}
        </code>
        <p className="text-timestamp text-slate-700">
          Verify locally with: <code className="font-mono">shasum -a 256 {receipt.filename}</code>
        </p>
      </div>

      {/* Navigation CTAs */}
      <div className="flex flex-wrap items-center justify-between gap-3 pt-2">
        <Link
          to="/explorer"
          className="inline-flex items-center justify-center gap-2 px-5 min-h-[48px] rounded-control border-2 border-navy bg-surface text-navy font-semibold text-[15px] hover:bg-navy-light transition-colors"
        >
          <ArrowLeft className="w-4 h-4" aria-hidden="true" />
          <span>Back to Trends Explorer</span>
        </Link>
        <Link
          to="/"
          className="inline-flex items-center justify-center gap-2 px-6 min-h-[48px] rounded-control bg-navy text-white font-semibold text-[15px] hover:bg-navy-hover transition-colors shadow-sm"
        >
          <Home className="w-4 h-4" aria-hidden="true" />
          <span>Return to Portal Home</span>
        </Link>
      </div>
    </section>
  );
}
