import { Fragment } from "react";
import type { Visualization } from "../schema";
import {
  MISSING_FILL,
  cellFill,
  cellTextColor,
  classifyHeatCell,
  legendStops,
  observedRange,
} from "./heatmapScale";

function formatTick(value: number): string {
  return Number.isInteger(value) ? String(value) : value.toFixed(1);
}

export function HeatmapGrid({ spec }: { spec: Visualization }) {
  const numeric = spec.cells
    .filter((cell) => !cell.missing && cell.value != null && !Number.isNaN(Number(cell.value)))
    .map((cell) => Number(cell.value));
  const { min, max } = observedRange(numeric);
  const lookup = new Map(spec.cells.map((cell) => [`${cell.row}:${cell.col}`, cell]));
  const rows = spec.rows_axis.length ? spec.rows_axis : [...new Set(spec.cells.map((cell) => cell.row))];
  const cols = spec.cols_axis.length ? spec.cols_axis : [...new Set(spec.cells.map((cell) => cell.col))];
  const meaning = spec.cell_meaning || "Observed count for that row and column.";
  const showCounts = cols.length <= 18;

  if (!rows.length || !cols.length) {
    return <p className="text-sm opacity-70">{spec.empty_reason || "No heatmap axes were returned."}</p>;
  }

  const ticks = legendStops(min, max);
  const includesZero = numeric.some((value) => value === 0);

  return (
    <div className="overflow-auto" data-testid="heatmap-grid" data-min={numeric.length ? min : ""} data-max={numeric.length ? max : ""}>
      <p className="muted mb-2 text-xs">{meaning}</p>
      <div className="min-w-max">
        <div
          className="grid gap-px"
          role="grid"
          aria-label={spec.title || "Heatmap"}
          style={{ gridTemplateColumns: `132px repeat(${cols.length}, minmax(44px, 1fr))` }}
        >
          <div role="presentation" />
          {cols.map((col) => (
            <div key={col} role="columnheader" className="muted px-1 text-center text-[10px] font-medium">
              {col}
            </div>
          ))}
          {rows.map((row) => (
            <Fragment key={row}>
              <div role="rowheader" className="truncate pr-2 text-xs text-[var(--text-primary)]">
                {row}
              </div>
              {cols.map((col) => {
                const cell = lookup.get(`${row}:${col}`);
                const missing = !cell || cell.missing || cell.value == null;
                const value = missing ? null : Number(cell.value);
                const tone = classifyHeatCell(value, missing, min, max);
                const label = missing
                  ? `${row}, ${col}: missing observation (not zero). ${meaning}`
                  : `${row}, ${col}: ${value}. ${meaning}`;
                return (
                  <div
                    key={`${row}-${col}`}
                    role="gridcell"
                    tabIndex={0}
                    data-tone={tone}
                    data-value={missing ? "" : String(value)}
                    aria-label={label}
                    title={label}
                    className="group relative flex h-8 items-center justify-center rounded-sm border text-[10px] font-semibold outline-none focus-visible:ring-2 focus-visible:ring-[var(--focus)]"
                    style={
                      missing
                        ? {
                            backgroundColor: MISSING_FILL,
                            backgroundImage:
                              "repeating-linear-gradient(45deg, transparent, transparent 3px, rgba(71,85,105,0.35) 3px, rgba(71,85,105,0.35) 5px)",
                            color: "#334155",
                            borderColor: "#94a3b8",
                          }
                        : {
                            backgroundColor: cellFill(value as number, min, max),
                            color: cellTextColor(value as number, min, max),
                            borderColor: "rgba(15, 23, 42, 0.12)",
                          }
                    }
                  >
                    {showCounts ? (missing ? "—" : String(value)) : null}
                    <span
                      role="tooltip"
                      className="pointer-events-none absolute bottom-full left-1/2 z-10 mb-1 hidden w-max max-w-[16rem] -translate-x-1/2 rounded-md bg-navy-900 px-2 py-1 text-left text-[11px] font-normal text-white shadow-lg group-hover:block group-focus:block"
                    >
                      <span className="block">{row}</span>
                      <span className="block">{col}</span>
                      <span className="block">{missing ? "Missing observation (not zero)" : `Count: ${value}`}</span>
                      <span className="block opacity-80">{meaning}</span>
                    </span>
                  </div>
                );
              })}
            </Fragment>
          ))}
        </div>
        <div className="mt-3 flex flex-wrap items-center gap-3 text-[11px]" data-testid="heatmap-legend">
          {numeric.length ? (
            <div className="flex items-center gap-2">
              {ticks.map((tick, index) => (
                <span key={`${tick.value}-${index}`} className="flex items-center gap-1">
                  <span className="h-3 w-4 rounded-sm border border-slate-400/40" style={{ backgroundColor: tick.color }} />
                  <span>{formatTick(tick.value)}</span>
                  {index < ticks.length - 1 ? <span className="opacity-40">→</span> : null}
                </span>
              ))}
            </div>
          ) : (
            <span>No observed values</span>
          )}
          {includesZero && (
            <span className="flex items-center gap-1">
              <span className="h-3 w-4 rounded-sm border" style={{ backgroundColor: cellFill(0, min, max) }} />
              0 is an observed count
            </span>
          )}
          <span className="flex items-center gap-1">
            <span
              className="h-3 w-4 rounded-sm border border-slate-400"
              style={{
                backgroundColor: MISSING_FILL,
                backgroundImage:
                  "repeating-linear-gradient(45deg, transparent, transparent 2px, rgba(71,85,105,0.4) 2px, rgba(71,85,105,0.4) 3px)",
              }}
            />
            Missing (not zero)
          </span>
        </div>
      </div>
    </div>
  );
}
