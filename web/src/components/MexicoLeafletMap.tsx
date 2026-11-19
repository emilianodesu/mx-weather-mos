import { useEffect, useRef } from 'react';
import L from 'leaflet';
import 'leaflet/dist/leaflet.css';
import type { MapForecastPoint } from '../types/api';

export type MapLayerMode = 'tmax' | 'tmin' | 'precip' | 'bias_delta';

interface MexicoLeafletMapProps {
  points: MapForecastPoint[];
  layer: MapLayerMode;
  selectedKey: string | null;
  onSelectPoint: (point: MapForecastPoint) => void;
}

export function getMarkerColor(point: MapForecastPoint, layer: MapLayerMode): string {
  if (layer === 'tmax') {
    const v = point.pred_tmax_p50;
    if (v >= 36) return '#B91C1C';
    if (v >= 30) return '#D97706';
    if (v >= 22) return '#0284C7';
    return '#0B2545';
  }
  if (layer === 'tmin') {
    const v = point.pred_tmin_p50;
    if (v <= 5) return '#1D4ED8';
    if (v <= 14) return '#0284C7';
    if (v <= 22) return '#15803D';
    return '#D97706';
  }
  if (layer === 'precip') {
    const v = point.precip_mm ?? 0;
    if (v >= 25) return '#B91C1C';
    if (v >= 10) return '#D97706';
    if (v >= 1) return '#0284C7';
    return '#15803D';
  }
  // bias_delta: highlight magnitude of ML correction
  const absDelta = Math.abs(point.bias_delta_tmax_c);
  if (absDelta >= 2.5) return '#B91C1C';
  if (absDelta >= 1.2) return '#D97706';
  if (absDelta >= 0.5) return '#0284C7';
  return '#15803D';
}

export function MexicoLeafletMap({
  points,
  layer,
  selectedKey,
  onSelectPoint,
}: MexicoLeafletMapProps) {
  const containerRef = useRef<HTMLDivElement | null>(null);
  const mapRef = useRef<L.Map | null>(null);
  const markersLayerRef = useRef<L.LayerGroup | null>(null);

  useEffect(() => {
    if (!containerRef.current || mapRef.current) return;

    const map = L.map(containerRef.current, {
      center: [23.6345, -102.5528],
      zoom: 5,
      minZoom: 4,
      maxZoom: 12,
      scrollWheelZoom: true,
    });

    L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png', {
      attribution: '&copy; OpenStreetMap contributors',
    }).addTo(map);

    markersLayerRef.current = L.layerGroup().addTo(map);
    mapRef.current = map;

    return () => {
      map.remove();
      mapRef.current = null;
      markersLayerRef.current = null;
    };
  }, []);

  useEffect(() => {
    const layerGroup = markersLayerRef.current;
    if (!layerGroup) return;

    layerGroup.clearLayers();

    points.forEach((pt) => {
      const key = `${pt.state_id}-${pt.municipality_id}`;
      const isSelected = key === selectedKey;
      const color = getMarkerColor(pt, layer);
      const deltaSign = pt.bias_delta_tmax_c >= 0 ? '+' : '';

      const marker = L.circleMarker([pt.lat, pt.lon], {
        radius: isSelected ? 11 : 8,
        color: isSelected ? '#0B2545' : '#FFFFFF',
        weight: isSelected ? 3 : 2,
        fillColor: color,
        fillOpacity: 0.9,
      });

      const popupHtml = `
        <div style="font-family: Inter, sans-serif; min-width: 210px;">
          <strong style="color:#0B2545; font-size:14px;">${pt.municipality_name}, ${pt.state_name}</strong>
          <div style="font-size:12px; color:#475569; margin-bottom:6px;">Valid: ${pt.valid_date} • Alert: ${pt.alert_level}</div>
          <div style="font-size:13px; line-height:1.5;">
            <div><strong>ML Tmax:</strong> ${pt.pred_tmax_p50.toFixed(1)} °C <span style="color:#64748B;">(Raw: ${pt.raw_tmax_c.toFixed(1)} °C)</span></div>
            <div><strong>ML Tmin:</strong> ${pt.pred_tmin_p50.toFixed(1)} °C <span style="color:#64748B;">(Raw: ${pt.raw_tmin_c.toFixed(1)} °C)</span></div>
            <div><strong>ML Bias Delta:</strong> ${deltaSign}${pt.bias_delta_tmax_c.toFixed(2)} °C</div>
            <div><strong>Precip:</strong> ${pt.precip_mm !== null ? `${pt.precip_mm.toFixed(1)} mm` : '—'}</div>
          </div>
          <div style="margin-top:8px;">
            <a href="/explorer?state_id=${pt.state_id}&mun_id=${pt.municipality_id}" style="color:#0B2545; font-weight:600; text-decoration:underline;">Open in Trends Explorer →</a>
          </div>
        </div>
      `;

      marker.bindPopup(popupHtml);
      marker.on('click', () => onSelectPoint(pt));
      marker.addTo(layerGroup);
    });
  }, [points, layer, selectedKey, onSelectPoint]);

  return (
    <div
      ref={containerRef}
      role="region"
      aria-label="Interactive forecast map of Mexico"
      className="w-full h-[460px] lg:h-[580px] rounded-card border border-surface-border overflow-hidden shadow-sm"
    />
  );
}
