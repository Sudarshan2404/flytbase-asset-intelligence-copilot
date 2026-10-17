import { describe, expect, it } from "vitest";
import { parseChatResponse, parseVisuals, userFacingAnswer, visualizationSchema } from "./schema";

describe("visualization schema", () => {
  it("accepts a grounded status card", () => {
    const parsed = visualizationSchema.parse({
      type: "status_card",
      title: "WELL-0001",
      cards: [{ label: "Production", value: "not producing", missing: false }],
    });
    expect(parsed.type).toBe("status_card");
  });

  it("drops unknown visualization types", () => {
    expect(parseVisuals([{ type: "hologram", title: "nope" }])).toEqual([]);
  });

  it("keeps missing heatmap cells as missing", () => {
    const parsed = visualizationSchema.parse({
      type: "heatmap",
      rows_axis: ["WELL-0001"],
      cols_axis: ["2026-09"],
      cells: [{ row: "WELL-0001", col: "2026-09", value: null, missing: true }],
    });
    expect(parsed.cells[0].missing).toBe(true);
    expect(parsed.cells[0].value).toBeNull();
  });

  it("coerces numeric strings on chart points", () => {
    const parsed = visualizationSchema.parse({
      type: "bar_chart",
      points: [{ x: "WELL-0001", values: { days: "49" } }],
      series: [{ key: "days", label: "Days" }],
    });
    expect(parsed.points[0].values.days).toBe(49);
  });

  it("recovers visuals when the surrounding chat payload is messy", () => {
    const parsed = parseChatResponse({
      answer: "Downtime comparison",
      visuals: [
        {
          type: "bar_chart",
          title: "Downtime",
          series: [{ key: "days", label: "Days" }],
          points: [{ x: "WELL-0121", values: { days: 49 } }],
        },
        { type: "hologram", title: "drop me" },
      ],
      tool_trace: [{ name: "get_top_downtime_wells", arguments: { start: "2026-06-16" }, ok: true }],
      extra_unknown: true,
    });
    expect(parsed.visuals).toHaveLength(1);
    expect(parsed.visuals[0].type).toBe("bar_chart");
    expect(parsed.visuals[0].points[0].values.days).toBe(49);
  });

  it("strips chain-of-thought from a chat answer", () => {
    const parsed = parseChatResponse({
      answer:
        "Let me re-examine the tool definitions.\nget_well_status, get_downtime\nWELL-0001 is not producing oil.",
      visuals: [],
      tool_trace: [{ name: "get_well_status", ok: true }],
    });
    expect(parsed.answer).toBe("WELL-0001 is not producing oil.");
    expect(parsed.answer).not.toMatch(/re-examine|tool definitions/i);
  });
});

describe("userFacingAnswer", () => {
  it("drops think tags and private deliberation", () => {
    const text = userFacingAnswer(
      "<think>I will scan every tool</think>Let me re-examine the tool definitions.\nWELL-0001 is not producing.",
    );
    expect(text).toBe("WELL-0001 is not producing.");
    expect(text).not.toMatch(/think|re-examine|tool definitions/i);
  });

  it("does not invent an oil ranking from leftover reasoning", () => {
    const text = userFacingAnswer(
      "Let me look at temperatures and inspection counts to guess which well produced the most oil.",
    );
    expect(text.toLowerCase()).not.toContain("guess");
    expect(text).toMatch(/could not produce a concise answer/i);
  });
});
