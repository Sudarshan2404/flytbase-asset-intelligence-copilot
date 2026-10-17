import { z } from "zod";

export const visualTypeSchema = z.enum([
  "status_card",
  "table",
  "line_chart",
  "bar_chart",
  "map",
  "heatmap",
]);

const looseNumber = z.preprocess((value) => {
  if (value === "" || value === undefined) return null;
  if (typeof value === "string" && value.trim() !== "" && !Number.isNaN(Number(value))) {
    return Number(value);
  }
  return value;
}, z.number().nullable());

export const visualizationSchema = z.object({
  type: visualTypeSchema,
  title: z.string().nullish(),
  description: z.string().nullish(),
  cards: z
    .array(
      z.object({
        label: z.string(),
        value: z.union([z.string(), z.number(), z.boolean(), z.null()]).optional(),
        unit: z.string().nullish(),
        tone: z.enum(["neutral", "ok", "warn", "alert", "unknown"]).optional(),
        missing: z.boolean().optional(),
        record_id: z.string().nullish(),
      }),
    )
    .optional()
    .default([]),
  columns: z
    .array(z.object({ key: z.string(), label: z.string(), unit: z.string().nullish() }))
    .optional()
    .default([]),
  rows: z.array(z.record(z.any())).optional().default([]),
  x_key: z.string().nullish(),
  x_label: z.string().nullish(),
  y_label: z.string().nullish(),
  series: z
    .array(z.object({ key: z.string(), label: z.string(), unit: z.string().nullish() }))
    .optional()
    .default([]),
  points: z
    .array(
      z.object({
        x: z.preprocess((value) => (value == null ? "" : String(value)), z.string()),
        values: z.record(looseNumber).optional().default({}),
        record_id: z.string().nullish(),
      }),
    )
    .optional()
    .default([]),
  rows_axis: z.array(z.string()).optional().default([]),
  cols_axis: z.array(z.string()).optional().default([]),
  cells: z
    .array(
      z.object({
        row: z.preprocess((value) => String(value ?? ""), z.string()),
        col: z.preprocess((value) => String(value ?? ""), z.string()),
        value: looseNumber.optional(),
        missing: z.boolean().optional(),
        record_id: z.string().nullish(),
      }),
    )
    .optional()
    .default([]),
  cell_meaning: z.string().nullish(),
  color_scale: z.string().nullish(),
  markers: z
    .array(
      z.object({
        lat: z.number(),
        lon: z.number(),
        label: z.string(),
        record_id: z.string().nullish(),
        detail: z.string().nullish(),
      }),
    )
    .optional()
    .default([]),
  units: z.string().nullish(),
  empty_reason: z.string().nullish(),
});

export const chatResponseSchema = z.object({
  answer: z.string().default(""),
  citations: z
    .array(
      z.object({
        kind: z.string(),
        record_id: z.string().nullish(),
        path: z.string().nullish(),
        excerpt: z.string().nullish(),
      }),
    )
    .default([]),
  visual: z.object({ type: visualTypeSchema.nullish(), title: z.string().nullish() }).nullish(),
  visuals: z.array(visualizationSchema).default([]),
  conflicts: z
    .array(
      z.object({
        kind: z.string(),
        summary: z.string(),
        record_ids: z.array(z.string()).default([]),
      }),
    )
    .default([]),
  pending_confirmation: z
    .array(
      z.object({
        claim_id: z.string().nullish(),
        asset_id: z.string().nullish(),
        text: z.string().nullish(),
        message: z.string().nullish(),
      }),
    )
    .default([]),
  tool_trace: z
    .array(
      z.object({
        name: z.string(),
        arguments: z.record(z.any()).default({}),
        ok: z.boolean().default(true),
        error: z.string().nullish(),
      }),
    )
    .default([]),
  conversation_id: z.string().nullish(),
  user_id: z.string().nullish(),
  error: z.string().nullish(),
  identity_authenticated: z.boolean().default(false),
});

export type ChatResponse = z.infer<typeof chatResponseSchema>;
export type Visualization = z.infer<typeof visualizationSchema>;

const THINK_BLOCK =
  /<(?:think|thought|reasoning|analysis)>[\s\S]*?<\/(?:think|thought|reasoning|analysis)>/gi;
const DELIBERATION_LINE =
  /^(let me|let's|i will|i need to|looking at|re-examine|here are the tools|the tools (are|include)|tool (list|definitions?|catalog))/i;

function isToolInventoryLine(line: string): boolean {
  if (/tool definitions|available tools|function_declarations|"name"\s*:\s*"get_/i.test(line)) {
    return true;
  }
  const names = line.split(/[,;]\s*/).map((part) => part.trim()).filter(Boolean);
  return names.length >= 2 && names.every((name) => /^get_[a-z0-9_]+$/.test(name));
}

export function userFacingAnswer(text: string | null | undefined): string {
  const stripped = String(text || "").replace(THINK_BLOCK, "").trim();
  if (!stripped) {
    return "The copilot could not produce a concise answer from the model output.";
  }
  const kept = stripped
    .split(/\n+/)
    .map((line) => line.trim())
    .filter((line) => line && !DELIBERATION_LINE.test(line) && !isToolInventoryLine(line));
  const next = kept.join("\n").trim();
  if (!next || (/re-examine the tool|tool definitions|available tools/i.test(stripped) && next.length > 400)) {
    return "The copilot could not produce a concise answer from the model output.";
  }
  return next;
}

export function parseVisuals(input: unknown): Visualization[] {
  if (!Array.isArray(input)) return [];
  return input.flatMap((item) => {
    const parsed = visualizationSchema.safeParse(item);
    return parsed.success ? [parsed.data] : [];
  });
}

export function parseChatResponse(input: unknown): ChatResponse {
  const parsed = chatResponseSchema.safeParse(input);
  if (parsed.success) {
    return {
      ...parsed.data,
      answer: userFacingAnswer(parsed.data.answer),
      visuals: parseVisuals(parsed.data.visuals),
    };
  }
  const raw = input && typeof input === "object" ? (input as Record<string, unknown>) : {};
  const recovered = chatResponseSchema.parse({
    answer: userFacingAnswer(typeof raw.answer === "string" ? raw.answer : ""),
    citations: Array.isArray(raw.citations) ? raw.citations : [],
    visual: raw.visual && typeof raw.visual === "object" ? raw.visual : null,
    visuals: [],
    conflicts: Array.isArray(raw.conflicts) ? raw.conflicts : [],
    pending_confirmation: Array.isArray(raw.pending_confirmation) ? raw.pending_confirmation : [],
    tool_trace: Array.isArray(raw.tool_trace) ? raw.tool_trace : [],
    conversation_id: typeof raw.conversation_id === "string" ? raw.conversation_id : null,
    user_id: typeof raw.user_id === "string" ? raw.user_id : null,
    error:
      typeof raw.error === "string"
        ? raw.error
        : "Some response fields could not be validated. Valid visualizations were kept.",
    identity_authenticated: raw.identity_authenticated === true,
  });
  return { ...recovered, visuals: parseVisuals(raw.visuals) };
}
