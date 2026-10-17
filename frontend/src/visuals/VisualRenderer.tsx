import {
  Bar,
  BarChart,
  CartesianGrid,
  Legend,
  Line,
  LineChart,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import type { Visualization } from "../schema";
import { ChartFrame } from "./ChartFrame";
import { HeatmapGrid } from "./HeatmapGrid";
import { WellMap } from "./WellMap";

const tones: Record<string, string> = {
  ok: "border-[var(--success)] bg-[var(--success-soft)] text-[var(--success)]",
  warn: "border-[var(--warning)] bg-[var(--warning-soft)] text-[var(--warning)]",
  alert: "border-[var(--danger)] bg-[var(--danger-soft)] text-[var(--danger)]",
  unknown: "border-[var(--border)] bg-[var(--surface-subtle)] text-[var(--text-secondary)]",
  neutral: "border-[var(--border)] bg-[var(--surface)] text-[var(--text-primary)]",
};

const tooltipStyle = {
  backgroundColor: "var(--surface-raised)",
  border: "1px solid var(--border-strong)",
  borderRadius: "8px",
  color: "var(--text-primary)",
  boxShadow: "var(--shadow)",
};

const axisTick = { fontSize: 11, fill: "var(--text-secondary)" };

function formatValue(value: string | number | boolean | null | undefined, missing?: boolean) {
  if (missing || value == null || value === "") return "Not available";
  return String(value);
}

function seriesKeys(spec: Visualization): Array<{ key: string; label: string }> {
  if (spec.series.length) return spec.series;
  const keys = new Set<string>();
  for (const point of spec.points) {
    for (const key of Object.keys(point.values || {})) keys.add(key);
  }
  return [...keys].map((key) => ({ key, label: key }));
}

function chartRows(spec: Visualization) {
  const series = seriesKeys(spec);
  return spec.points.map((point) => {
    const row: Record<string, string | number | null> = { x: point.x };
    for (const item of series) {
      const raw = point.values?.[item.key];
      row[item.key] = raw == null || Number.isNaN(Number(raw)) ? null : Number(raw);
    }
    return row;
  });
}

function hasRenderableData(spec: Visualization): boolean {
  if (spec.type === "status_card") return spec.cards.length > 0;
  if (spec.type === "table") return spec.rows.length > 0;
  if (spec.type === "bar_chart" || spec.type === "line_chart") return spec.points.length > 0;
  if (spec.type === "heatmap") return spec.cells.length > 0 || (spec.rows_axis.length > 0 && spec.cols_axis.length > 0);
  if (spec.type === "map") return spec.markers.length > 0;
  return false;
}

export function VisualRenderer({ spec }: { spec: Visualization }) {
  if (spec.empty_reason && !hasRenderableData(spec)) {
    return (
      <section className="surface-card rounded-2xl p-4">
        <h3 className="text-sm font-semibold">{spec.title || spec.type}</h3>
        <p className="mt-2 text-sm opacity-70">{spec.empty_reason}</p>
      </section>
    );
  }

  const series = seriesKeys(spec);
  const rows = chartRows(spec);

  return (
    <section
      className="surface-card rounded-2xl p-4"
      data-visual-type={spec.type}
    >
      {spec.title && <h3 className="text-sm font-semibold">{spec.title}</h3>}
      {spec.description && <p className="mt-1 text-xs opacity-60">{spec.description}</p>}
      <div className="mt-3 min-w-0">
        {spec.type === "status_card" &&
          (spec.cards.length ? (
            <div className="grid gap-3 sm:grid-cols-2">
              {spec.cards.map((card) => (
                <article
                  key={card.label}
                  className={`rounded-xl border px-3 py-2 ${tones[card.tone || "neutral"]}`}
                >
                  <div className="text-[11px] uppercase tracking-wide opacity-70">{card.label}</div>
                  <div className="mt-1 text-lg font-semibold">
                    {formatValue(card.value, card.missing)}
                    {card.unit && !card.missing && card.value != null ? (
                      <span className="ml-1 text-xs font-normal">{card.unit}</span>
                    ) : null}
                  </div>
                  {card.record_id && <div className="mt-1 text-[11px] opacity-60">{card.record_id}</div>}
                </article>
              ))}
            </div>
          ) : (
            <p className="text-sm opacity-70">{spec.empty_reason || "No metrics were returned."}</p>
          ))}

        {spec.type === "table" &&
          (spec.rows.length ? (
            <div className="overflow-auto">
              <table className="min-w-full text-left text-sm">
                <thead>
                  <tr className="border-b border-[var(--border)] text-xs uppercase text-[var(--text-secondary)]">
                    {spec.columns.map((col) => (
                      <th key={col.key} className="px-2 py-2 font-medium">
                        {col.label}
                        {col.unit ? ` (${col.unit})` : ""}
                      </th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {spec.rows.map((row, index) => (
                    <tr key={index} className="border-b border-[var(--border)] hover:bg-[var(--surface-subtle)]">
                      {spec.columns.map((col) => (
                        <td key={col.key} className="px-2 py-2 align-top">
                          {row[col.key] == null || row[col.key] === "" ? "—" : String(row[col.key])}
                        </td>
                      ))}
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          ) : (
            <p className="text-sm opacity-70">{spec.empty_reason || "No rows were returned."}</p>
          ))}

        {(spec.type === "bar_chart" || spec.type === "line_chart") &&
          (spec.points.length ? (
            <ChartFrame>
              {({ width, height }) =>
                spec.type === "bar_chart" ? (
                  <BarChart width={width} height={height} data={rows}>
                    <CartesianGrid strokeDasharray="3 3" stroke="currentColor" opacity={0.15} />
                    <XAxis dataKey="x" tick={axisTick} label={spec.x_label ? { value: spec.x_label, position: "insideBottom", offset: -2, fill: "var(--text-secondary)" } : undefined} />
                    <YAxis tick={axisTick} label={spec.y_label ? { value: spec.y_label, angle: -90, position: "insideLeft", fill: "var(--text-secondary)" } : undefined} />
                    <Tooltip contentStyle={tooltipStyle} labelStyle={{ color: "var(--text-primary)" }} itemStyle={{ color: "var(--text-primary)" }} />
                    <Legend wrapperStyle={{ color: "var(--text-secondary)" }} />
                    {series.map((item) => (
                      <Bar key={item.key} dataKey={item.key} name={item.label} fill="#0d9488" />
                    ))}
                  </BarChart>
                ) : (
                  <LineChart width={width} height={height} data={rows}>
                    <CartesianGrid strokeDasharray="3 3" stroke="currentColor" opacity={0.15} />
                    <XAxis dataKey="x" tick={{ ...axisTick, fontSize: 10 }} />
                    <YAxis tick={axisTick} />
                    <Tooltip contentStyle={tooltipStyle} labelStyle={{ color: "var(--text-primary)" }} itemStyle={{ color: "var(--text-primary)" }} />
                    <Legend wrapperStyle={{ color: "var(--text-secondary)" }} />
                    {series.map((item, index) => (
                      <Line
                        key={item.key}
                        type="monotone"
                        dataKey={item.key}
                        name={item.label}
                        stroke={["#0d9488", "#93c5fd", "#f59e0b"][index % 3]}
                        connectNulls={false}
                        dot={{ r: 2 }}
                      />
                    ))}
                  </LineChart>
                )
              }
            </ChartFrame>
          ) : (
            <p className="text-sm opacity-70">{spec.empty_reason || "No chart points were returned."}</p>
          ))}

        {spec.type === "heatmap" && <HeatmapGrid spec={spec} />}
        {spec.type === "map" && <WellMap spec={spec} />}
      </div>
    </section>
  );
}

export function VisualStack({ visuals }: { visuals: Visualization[] }) {
  if (!visuals.length) return null;
  return (
    <div className="mt-4 grid gap-3">
      {visuals.map((visual, index) => (
        <VisualRenderer key={`${visual.type}-${visual.title}-${index}`} spec={visual} />
      ))}
    </div>
  );
}
