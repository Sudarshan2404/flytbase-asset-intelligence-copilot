import { describe, expect, it } from "vitest";
import { cellFill, classifyHeatCell, normalizeObserved } from "./heatmapScale";

describe("heatmap scale", () => {
  it("stretches a narrow 3–5 range across the full palette", () => {
    expect(normalizeObserved(3, 3, 5)).toBe(0);
    expect(normalizeObserved(4, 3, 5)).toBe(0.5);
    expect(normalizeObserved(5, 3, 5)).toBe(1);
    expect(cellFill(3, 3, 5)).not.toBe(cellFill(4, 3, 5));
    expect(cellFill(4, 3, 5)).not.toBe(cellFill(5, 3, 5));
  });

  it("classifies low, mid, high, zero, and missing", () => {
    expect(classifyHeatCell(3, false, 3, 5)).toBe("low");
    expect(classifyHeatCell(4, false, 3, 5)).toBe("mid");
    expect(classifyHeatCell(5, false, 3, 5)).toBe("high");
    expect(classifyHeatCell(0, false, 0, 5)).toBe("zero");
    expect(classifyHeatCell(null, true, 3, 5)).toBe("missing");
  });
});
