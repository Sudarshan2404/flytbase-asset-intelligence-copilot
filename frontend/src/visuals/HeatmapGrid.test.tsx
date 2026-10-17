import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { visualizationSchema } from "../schema";
import { cellFill } from "./heatmapScale";
import { HeatmapGrid } from "./HeatmapGrid";

describe("HeatmapGrid", () => {
  it("maps a narrow 3–5 range to distinct low, mid, and high colors and keeps missing hatched", () => {
    const spec = visualizationSchema.parse({
      type: "heatmap",
      title: "Inspection activity",
      cell_meaning: "Inspection count for that well and month. Missing is not zero.",
      rows_axis: ["WELL-0001"],
      cols_axis: ["2026-07", "2026-08", "2026-09", "2026-10"],
      cells: [
        { row: "WELL-0001", col: "2026-07", value: 3, missing: false },
        { row: "WELL-0001", col: "2026-08", value: 4, missing: false },
        { row: "WELL-0001", col: "2026-09", value: 5, missing: false },
        { row: "WELL-0001", col: "2026-10", value: null, missing: true },
      ],
    });
    render(<HeatmapGrid spec={spec} />);
    const low = screen.getByLabelText(/2026-07: 3/);
    const mid = screen.getByLabelText(/2026-08: 4/);
    const high = screen.getByLabelText(/2026-09: 5/);
    const missing = screen.getByLabelText(/2026-10: missing observation/);

    expect(low).toHaveAttribute("data-tone", "low");
    expect(mid).toHaveAttribute("data-tone", "mid");
    expect(high).toHaveAttribute("data-tone", "high");
    expect(missing).toHaveAttribute("data-tone", "missing");
    expect(low.style.backgroundColor).toBe(cellFill(3, 3, 5));
    expect(mid.style.backgroundColor).toBe(cellFill(4, 3, 5));
    expect(high.style.backgroundColor).toBe(cellFill(5, 3, 5));
    expect(new Set([low.style.backgroundColor, mid.style.backgroundColor, high.style.backgroundColor]).size).toBe(3);
    expect(missing.style.backgroundImage).toMatch(/repeating-linear-gradient/);
    expect(missing).toHaveTextContent("—");
    expect(low).toHaveTextContent("3");
    expect(screen.getByTestId("heatmap-legend")).toHaveTextContent("3");
    expect(screen.getByTestId("heatmap-legend")).toHaveTextContent("5");
    expect(screen.getByTestId("heatmap-legend")).toHaveTextContent("Missing (not zero)");
  });

  it("treats observed zero differently from a missing cell", () => {
    const spec = visualizationSchema.parse({
      type: "heatmap",
      cell_meaning: "Spill count by area and month.",
      rows_axis: ["Northeast Block 8"],
      cols_axis: ["2026-08", "2026-09"],
      cells: [
        { row: "Northeast Block 8", col: "2026-08", value: 0, missing: false },
        { row: "Northeast Block 8", col: "2026-09", value: null, missing: true },
      ],
    });
    render(<HeatmapGrid spec={spec} />);
    const zero = screen.getByLabelText(/2026-08: 0/);
    const missing = screen.getByLabelText(/2026-09: missing observation/);
    expect(zero).toHaveAttribute("data-tone", "zero");
    expect(missing).toHaveAttribute("data-tone", "missing");
    expect(zero).toHaveTextContent("0");
    expect(missing).toHaveTextContent("—");
    expect(zero.style.backgroundImage || "").not.toMatch(/repeating-linear-gradient/);
    expect(missing.style.backgroundImage).toMatch(/repeating-linear-gradient/);
    expect(screen.getByTestId("heatmap-legend")).toHaveTextContent("0 is an observed count");
  });
});
