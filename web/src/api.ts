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
  candidates: Candidate[];   // the month-wide shortlist verification saw
  possible: Candidate[];     // inside the frame's interval: what it can actually be
  possible_variant: "siglip" | "siglip_gray";   // anchored frames: the occasion ranked in grayscale for the exact shot, when cached
  anchor: Photo | null;
  verdict: Verdict | null;
  override: Override | null;
  fact: FrameFact | null;
  outing: number | null;
  written: { at: string | null; local: string | null; offset: string | null; lat: number | null; lon: number | null; verified: boolean | null; changed: boolean } | null;
}

export interface WritePlan {
  roll: string;
  folder: string;
  frames: { number: number; file: string; current: string | null; local: string; offset: string; lat: number | null; lon: number | null; source: string; confidence: number; keywords: string[]; provenance: string[]; stale: string[] }[];
  skipped: { number: number; file: string | null; why: string }[];
}

export interface WriteResult {
  plan: WritePlan;
  ok: boolean;
  warnings: string[];
  checks: { number: number; file: string; ok: boolean; problems: string[] }[];
  backed_up: number;
  record: string;
  sidecar: string | null;
  frames: Frame[];
}

export interface TrailPoint {
  time: string;
  lat: number;
  lon: number;
  source: string;
  tzoffset: number | null;
  label: string | null;
  ref: string | null;
  camera: string | null;
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
  cost: { verified_frames: number; k: number; verify_usd: number; outing_usd: number; usd: number; model: string | null };
  writable: boolean;
  written: { at: string | null; frames: number } | null;
}

export type FactsBody = Omit<Facts, "roll" | "frames"> & { frames: Record<string, Partial<FrameFact>> };

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

/** Older servers (before COO-149) send no `possible`; fill it so the page renders and says so. */
export function normaliseFrame(f: Frame): Frame {
  return { ...f, possible: f.possible ?? [], candidates: f.candidates ?? [], clusters: f.clusters ?? [] };
}

export const serverIsOld = (frames: Frame[] | undefined) => !!frames?.length && frames.every((f) => (f as Partial<Frame>).possible === undefined);

export const useFrames = (key: string | null) =>
  useQuery({
    queryKey: ["frames", key],
    queryFn: async () => (await request<Frame[]>(`/api/rolls/${encodeURIComponent(key!)}/frames`)).map(normaliseFrame),
    enabled: !!key,
  });

/** The pool's photos of one event, for picking any phone photo as the anchor. */
export const usePhotos = (key: string, event: number | null) =>
  useQuery({
    queryKey: ["photos", key, event],
    queryFn: () => request<(Photo & { event: number })[]>(`/api/rolls/${encodeURIComponent(key)}/photos?event=${event}`),
    enabled: event !== null,
  });

/** Trail points with GPS inside a frame's interval, padded so the map has context. */
export const useTrail = (key: string, number: number, padMinutes = 0) =>
  useQuery({
    queryKey: ["trail", key, number, padMinutes],
    queryFn: () => request<TrailPoint[]>(`/api/rolls/${encodeURIComponent(key)}/frames/${number}/trail?pad_minutes=${padMinutes}`),
  });

/** One user decision about a frame. The API re-solves and returns every frame, because neighbours move. */
export function useAssign(key: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ number, body }: { number: number; body: AssignBody }) =>
      request<Frame[]>(`/api/rolls/${encodeURIComponent(key)}/frames/${number}/assign`, { method: "PUT", body: JSON.stringify(body) }),
    onSuccess: (frames) => {
      qc.setQueryData(["frames", key], frames.map(normaliseFrame));
      qc.invalidateQueries({ queryKey: ["roll", key] });
      qc.invalidateQueries({ queryKey: ["rolls"] });
      qc.invalidateQueries({ queryKey: ["trail", key] });
    },
  });
}

export const useWritePlan = (key: string, force: boolean, enabled: boolean) =>
  useQuery({
    queryKey: ["write-plan", key, force],
    queryFn: () => request<WritePlan>(`/api/rolls/${encodeURIComponent(key)}/write?force=${force}`),
    enabled,
    staleTime: 0,
  });

/** Backup, write, read back, record, sidecar — the same chain as `filmgeo write --write`. */
export function useWrite(key: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (force: boolean) => request<WriteResult>(`/api/rolls/${encodeURIComponent(key)}/write?force=${force}`, { method: "POST" }),
    onSuccess: ({ frames }) => {
      qc.setQueryData(["frames", key], frames.map(normaliseFrame));
      qc.invalidateQueries({ queryKey: ["roll", key] });
      qc.invalidateQueries({ queryKey: ["write-plan", key] });
      qc.invalidateQueries({ queryKey: ["rolls"] });
    },
  });
}

export function useRestore(key: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: () => request<{ restored: { file: string; how: string }[]; frames: Frame[] }>(`/api/rolls/${encodeURIComponent(key)}/restore`, { method: "POST" }),
    onSuccess: ({ frames }) => {
      qc.setQueryData(["frames", key], frames.map(normaliseFrame));
      qc.invalidateQueries({ queryKey: ["roll", key] });
      qc.invalidateQueries({ queryKey: ["write-plan", key] });
    },
  });
}

/** Batch confirm or unconfirm: every frame, those at or above a confidence, or a list. Skipped frames are never confirmed. */
export function useConfirm(key: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: { confirmed: boolean; frames?: number[]; min_confidence?: number }) =>
      request<Frame[]>(`/api/rolls/${encodeURIComponent(key)}/confirm`, { method: "POST", body: JSON.stringify(body) }),
    onSuccess: (frames) => {
      qc.setQueryData(["frames", key], frames.map(normaliseFrame));
      qc.invalidateQueries({ queryKey: ["roll", key] });
      qc.invalidateQueries({ queryKey: ["rolls"] });
    },
  });
}

/** The whole facts file. A moved window rebuilds the pool (seconds); anything else re-solves in place. */
export function useSaveFacts(key: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: FactsBody) =>
      request<{ facts: Facts; solved: boolean; error: string | null; roll: Roll | null }>(`/api/rolls/${encodeURIComponent(key)}/facts`, { method: "PUT", body: JSON.stringify(body) }),
    onSuccess: ({ roll }) => {
      if (roll) qc.setQueryData(["roll", key], roll);
      qc.invalidateQueries({ queryKey: ["roll", key] });
      qc.invalidateQueries({ queryKey: ["frames", key] });
      qc.invalidateQueries({ queryKey: ["trail", key] });
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
      qc.setQueryData(["frames", key], frames.map(normaliseFrame));
      qc.setQueryData(["roll", key], roll);
      qc.invalidateQueries({ queryKey: ["trail", key] });
      qc.invalidateQueries({ queryKey: ["photos", key] });
      qc.invalidateQueries({ queryKey: ["rolls"] });
    },
  });
}
