import { requestJson } from "./http";
import { object } from "./value";
import type { StatusKind } from "../components/ui";

/**
 * The per-feature diagnosis the server builds from local state only
 * (`GET /api/diagnostics/features`, R18). The server decides what each feature
 * needs and whether it has it; this module only reads the answer and says how
 * to show it. It carries no credentials, paths or endpoints, and the screen
 * adds none.
 */
export type FeatureState = "available" | "not_configured" | "temporary_failure";
export interface Prerequisite {
  id: string;
  label: string;
  /** Only a required one decides whether the feature can be used. */
  required: boolean;
  status: string;
  recovery: string | null;
}
export interface Feature {
  id: string;
  label: string;
  description: string;
  /** True: the product's core works without it. */
  optional: boolean;
  status: FeatureState | string;
  recovery: string[];
  prerequisites: Prerequisite[];
}
export interface FeatureDiagnostics {
  features: Feature[];
}

const text = (v: unknown) => (typeof v === "string" ? v : "");
const texts = (v: unknown) =>
  Array.isArray(v) ? v.filter((x): x is string => typeof x === "string") : [];

/** Reads the response, keeping only what is shown. A bad shape is an error. */
export function decodeDiagnostics(body: unknown): FeatureDiagnostics {
  if (!object(body) || !Array.isArray(body.features))
    throw new Error("診断結果を読み込めませんでした。");
  return {
    features: body.features.map((f) => {
      if (
        !object(f) ||
        typeof f.id !== "string" ||
        typeof f.status !== "string"
      )
        throw new Error("診断結果を読み込めませんでした。");
      return {
        id: f.id,
        label: text(f.label) || f.id,
        description: text(f.description),
        optional: f.optional === true,
        status: f.status,
        recovery: texts(f.recovery),
        prerequisites: Array.isArray(f.prerequisites)
          ? f.prerequisites.filter(object).map((p) => ({
              id: text(p.id),
              label: text(p.label),
              required: p.required === true,
              status: text(p.status),
              recovery: text(p.recovery) || null,
            }))
          : [],
      };
    }),
  };
}

export const loadDiagnostics = (signal: AbortSignal) =>
  requestJson<FeatureDiagnostics>(
    "/api/diagnostics/features",
    { signal },
    decodeDiagnostics,
  );

/**
 * How one feature is shown, from its own `status` and `optional` alone.
 * An optional prerequisite that is missing (see `optionalGaps`) never changes
 * this: a feature the server calls available is shown as available.
 *
 * - available: no kind (an ordinary status line)
 * - not configured: `setup`, guidance and never an alert, optional or not
 * - temporary failure: `error`, the only alert
 * - anything else the server may add later: `partial`
 */
export function featureKind(feature: Feature): StatusKind | undefined {
  switch (feature.status) {
    case "available":
      return undefined;
    case "not_configured":
      return "setup";
    case "temporary_failure":
      return "error";
    default:
      return "partial";
  }
}

/** Whether the person must act for the feature to work (not mere guidance). */
export const needsRecovery = (feature: Feature) =>
  feature.status === "temporary_failure" ||
  (feature.status === "not_configured" && !feature.optional) ||
  (feature.status !== "available" &&
    feature.status !== "not_configured" &&
    !feature.optional);

/** Optional prerequisites that are not in place: a note, not a problem. */
export const optionalGaps = (feature: Feature) =>
  feature.prerequisites.filter(
    (p) => !p.required && p.status !== "ok" && p.status !== "disabled",
  );
