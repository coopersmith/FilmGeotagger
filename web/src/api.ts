/** Types and fetchers for the local review API (src/filmgeo/api/app.py). */

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

export type Source = "anchored" | "locked" | "interpolated" | "skipped";
export type Location = "ok" | "ambiguous" | "none";

export interface Photo {
  uuid: string;
  time: string;
  tzoffset: number | null;
  lat: number | null;
  lon: number | null;
  filename: string;
  image: string;
}

export interface Candidate extends Photo {
  score: number;
  event: number | null;
  shown: boolean;
  verdict: "match" | "no" | null;
  rejected: boolean;
}

export interface Cluster {
  lat: number;
  lon: number;
  count: number;
  spread_m: number;
  first: string;
  last: string;
  label: string | null;
}

export interface Verdict {
  match: string | null;
  confidence: number;
  evidence: string;
  clues: Record<string, unknown>;
  shown: string[];
}

export interface Override {
  number: number;
  anchor: string | null;
  rejected: string[];
  no_reference: boolean;
  confirmed: boolean;
}

export interface FrameFact {
  number: number;
  when: string | null;
  lat: number | null;
  lon: number | null;
  radius_m: number | null;
  place_name: string | null;
  same_day_as: number | null;
  skip: boolean;
  note: string | null;
}

export interface Frame {
  number: number;
  source: Source;
  time: string;
  tzoffset: number | null;
  offset_disputed: boolean;
  t_lo: string;
  t_hi: string;
  confidence: number;
  outside_mass: number;
  anchor_uuid: string | null;
  event: number | null;
  lat: number | null;
  lon: number | null;
  location: Location;
  location_source: string | null;
  clusters: Cluster[];
  truth: string | null;
  locked: boolean;
  status: "proposed" | "confirmed";
  image: string;
  interval_text: string;
  candidates: Candidate[];
  anchor: Photo | null;
  verdict: Verdict | null;
  override: Override | null;
  fact: FrameFact | null;
  outing: number | null;
}

export interface RollEvent {
  index: number;
  start: string;
  end: string;
  lat: number | null;
  lon: number | null;
  spread_m: number;
  count: number;
}

export interface Facts {
  roll: string;
  window_from: string | null;
  window_to: string | null;
  tz: string | null;
  camera: string | null;
  film: string | null;
  lab: string | null;
  notes: string | null;
  reverse: boolean;
  frames: Record<string, FrameFact>;
}

export interface Roll {
  roll: string;
  origin: string;
  window: { start: string; end: string; source: string };
  pool: number;
  events: RollEvent[];
  trail: Record<string, number>;
  verified_frames: number;
  outings: { groups: { frames: number[]; description: string; confidence: number }[]; out_of_sequence: number[]; notes: string } | null;
  anchored: number;
  reverse: { suspect: boolean; forward_anchored: number; reverse_anchored: number };
  window_check: { doubtful: boolean; reason: string; best_days: [string, number][] };
  n_frames: number;
  facts: Facts;
  confirmed: number;
}

export interface RollSummary {
  key: string;
  origin: string | null;
  loaded: boolean;
  aligned: boolean;
  n_frames?: number;
  window?: { start: string; end: string; source: string };
  anchored?: number;
  verified_frames?: number;
  confirmed?: number;
  doubtful?: boolean;
  facts: { window_from: string | null; window_to: string | null; camera: string | null; film: string | null; lab: string | null };
}

export interface AssignBody {
  anchor?: string | null;
  reject?: string[];
  no_reference?: boolean;
  when?: string;
  lat?: number;
  lon?: number;
  radius_m?: number;
  place_name?: string;
  same_day_as?: number;
  skip?: boolean;
  note?: string;
  confirmed?: boolean;
  unlock?: boolean;
}

export class ApiError extends Error {
  status: number;
  constructor(status: number, detail: string) {
    super(detail);
    this.status = status;
  }
}

async function request<T>(url: string, init?: RequestInit): Promise<T> {
  const r = await fetch(url, { headers: { "content-type": "application/json" }, ...init });
  if (!r.ok) {
    let detail = r.statusText;
    try {
      const body = await r.json();
      detail = typeof body.detail === "string" ? body.detail : JSON.stringify(body.detail);
    } catch {
      /* no body */
    }
    throw new ApiError(r.status, detail);
  }
  return r.json() as Promise<T>;
}

export const useRolls = () => useQuery({ queryKey: ["rolls"], queryFn: () => request<RollSummary[]>("/api/rolls") });

export const useRoll = (key: string | null) =>
  useQuery({ queryKey: ["roll", key], queryFn: () => request<Roll>(`/api/rolls/${encodeURIComponent(key!)}`), enabled: !!key });

export const useFrames = (key: string | null) =>
  useQuery({ queryKey: ["frames", key], queryFn: () => request<Frame[]>(`/api/rolls/${encodeURIComponent(key!)}/frames`), enabled: !!key });

/** One user decision about a frame. The API re-solves and returns every frame, because neighbours move. */
export function useAssign(key: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ number, body }: { number: number; body: AssignBody }) =>
      request<Frame[]>(`/api/rolls/${encodeURIComponent(key)}/frames/${number}/assign`, { method: "PUT", body: JSON.stringify(body) }),
    onSuccess: (frames) => {
      qc.setQueryData(["frames", key], frames);
      qc.invalidateQueries({ queryKey: ["roll", key] });
      qc.invalidateQueries({ queryKey: ["rolls"] });
    },
  });
}

export function useRealign(key: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (widen: boolean) =>
      request<Roll & { frames: Frame[] }>(`/api/rolls/${encodeURIComponent(key)}/realign`, { method: "POST", body: JSON.stringify({ widen }) }),
    onSuccess: ({ frames, ...roll }) => {
      qc.setQueryData(["frames", key], frames);
      qc.setQueryData(["roll", key], roll);
      qc.invalidateQueries({ queryKey: ["rolls"] });
    },
  });
}
