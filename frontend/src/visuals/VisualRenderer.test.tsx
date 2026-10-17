import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { visualizationSchema } from "../schema";
import { VisualRenderer } from "./VisualRenderer";

describe("VisualRenderer", () => {
  it("renders empty heatmap honestly", () => {
    const spec = visualizationSchema.parse({
      type: "heatmap",
      title: "Inspections",
      empty_reason: "No heatmap cells were returned.",
      cells: [],
    });
    render(<VisualRenderer spec={spec} />);
    expect(screen.getByText(/No heatmap cells/)).toBeInTheDocument();
  });

  it("does not invent map markers", () => {
    const spec = visualizationSchema.parse({
      type: "map",
      title: "Locations",
      empty_reason: "Location data is unavailable for the requested wells.",
      markers: [],
    });
    render(<VisualRenderer spec={spec} />);
    expect(screen.getByText(/Location data is unavailable/)).toBeInTheDocument();
  });

  it("shows missing metric values", () => {
    const spec = visualizationSchema.parse({
      type: "status_card",
      title: "Status",
      cards: [{ label: "Delta", value: null, missing: true, unit: "°C" }],
    });
    render(<VisualRenderer spec={spec} />);
    expect(screen.getByText("Not available")).toBeInTheDocument();
  });

  it("renders a bar chart from backend-shaped points instead of hiding it", () => {
    const spec = visualizationSchema.parse({
      type: "bar_chart",
      title: "Downtime comparison",
      series: [{ key: "days", label: "Downtime days" }],
      points: [
        { x: "WELL-0121", values: { days: 49 } },
        { x: "WELL-0138", values: { days: 42 } },
      ],
    });
    const { container } = render(<VisualRenderer spec={spec} />);
    expect(container.querySelector("[data-visual-type='bar_chart']")).toBeTruthy();
    expect(container.querySelector("[data-testid='chart-frame']")).toBeTruthy();
    expect(container.querySelector("svg")).toBeTruthy();
  });

  it("renders heatmap cells when axes are present", () => {
    const spec = visualizationSchema.parse({
      type: "heatmap",
      title: "Inspection activity",
      rows_axis: ["WELL-0001"],
      cols_axis: ["2026-09", "2026-10"],
      cells: [
        { row: "WELL-0001", col: "2026-09", value: 4, missing: false },
        { row: "WELL-0001", col: "2026-10", value: null, missing: true },
      ],
    });
    render(<VisualRenderer spec={spec} />);
    expect(screen.getByTestId("heatmap-grid")).toBeInTheDocument();
    expect(screen.getByLabelText(/2026-09: 4/)).toBeInTheDocument();
    expect(screen.getByLabelText(/2026-10: missing observation/)).toBeInTheDocument();
  });
});
