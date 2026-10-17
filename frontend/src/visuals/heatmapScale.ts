export type HeatTone = "low" | "mid" | "high" | "zero" | "missing";

/** Pale blue → medium blue → deep navy. Distinct stops, not a faint wash. */
export const HEATMAP_STOPS: Array<{ t: number; rgb: [number, number, number] }> = [
  { t: 0, rgb: [219, 234, 254] },
  { t: 0.35, rgb: [96, 165, 250] },
  { t: 0.65, rgb: [37, 99, 235] },
  { t: 1, rgb: [11, 31, 51] },
];

export const MISSING_FILL = "#cbd5e1";

export function observedRange(values: number[]): { min: number; max: number } {
  if (!values.length) return { min: 0, max: 0 };
  return { min: Math.min(...values), max: Math.max(...values) };
}

export function normalizeObserved(value: number, min: number, max: number): number {
  if (!Number.isFinite(value) || !Number.isFinite(min) || !Number.isFinite(max)) return 0;
  if (max === min) return 0.5;
  return Math.min(1, Math.max(0, (value - min) / (max - min)));
}

function mix(a: [number, number, number], b: [number, number, number], t: number): [number, number, number] {
  return [
    Math.round(a[0] + (b[0] - a[0]) * t),
    Math.round(a[1] + (b[1] - a[1]) * t),
    Math.round(a[2] + (b[2] - a[2]) * t),
  ];
}

export function sequentialFill(t: number): string {
  const clamped = Math.min(1, Math.max(0, t));
  let start = HEATMAP_STOPS[0];
  let end = HEATMAP_STOPS[HEATMAP_STOPS.length - 1];
  for (let i = 0; i < HEATMAP_STOPS.length - 1; i += 1) {
    if (clamped >= HEATMAP_STOPS[i].t && clamped <= HEATMAP_STOPS[i + 1].t) {
      start = HEATMAP_STOPS[i];
      end = HEATMAP_STOPS[i + 1];
      break;
    }
  }
  const span = end.t - start.t || 1;
  const [r, g, b] = mix(start.rgb, end.rgb, (clamped - start.t) / span);
  return `rgb(${r}, ${g}, ${b})`;
}

export function cellFill(value: number, min: number, max: number): string {
  return sequentialFill(normalizeObserved(value, min, max));
}

export function cellTextColor(value: number, min: number, max: number): string {
  const t = normalizeObserved(value, min, max);
  return t >= 0.55 ? "#f8fafc" : "#0b1f33";
}

export function classifyHeatCell(
  value: number | null | undefined,
  missing: boolean,
  min: number,
  max: number,
): HeatTone {
  if (missing || value == null || Number.isNaN(Number(value))) return "missing";
  if (value === 0) return "zero";
  const t = normalizeObserved(value, min, max);
  if (max !== min) {
    if (value === min || t <= 0.2) return "low";
    if (value === max || t >= 0.8) return "high";
    return "mid";
  }
  return "mid";
}

export function legendStops(min: number, max: number): Array<{ value: number; color: string }> {
  if (!Number.isFinite(min) || !Number.isFinite(max)) return [];
  if (min === max) return [{ value: min, color: cellFill(min, min, max) }];
  const mid = min + (max - min) / 2;
  return [
    { value: min, color: cellFill(min, min, max) },
    { value: mid, color: cellFill(mid, min, max) },
    { value: max, color: cellFill(max, min, max) },
  ];
}
