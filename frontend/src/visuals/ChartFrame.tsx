import { useEffect, useRef, useState, type ReactNode } from "react";

/**
 * Recharts ResponsiveContainer reports width/height 0 inside flex columns,
 * which paints an empty SVG. Measure the host and fall back to a usable size.
 */
export function ChartFrame({ children }: { children: (size: { width: number; height: number }) => ReactNode }) {
  const ref = useRef<HTMLDivElement>(null);
  const [size, setSize] = useState({ width: 640, height: 320 });

  useEffect(() => {
    const node = ref.current;
    if (!node) return;
    const apply = (width: number, height: number) => {
      setSize({
        width: Math.max(320, Math.floor(width || 640)),
        height: Math.max(240, Math.floor(height || 320)),
      });
    };
    apply(node.clientWidth, node.clientHeight);
    if (typeof ResizeObserver === "undefined") return;
    const observer = new ResizeObserver((entries) => {
      const box = entries[0]?.contentRect;
      if (box) apply(box.width, box.height);
    });
    observer.observe(node);
    return () => observer.disconnect();
  }, []);

  return (
    <div ref={ref} className="h-80 w-full min-h-[20rem] min-w-0" data-testid="chart-frame">
      {children(size)}
    </div>
  );
}
