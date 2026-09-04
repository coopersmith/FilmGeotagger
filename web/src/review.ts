/** What the roll needs next, derived from its frames. One place, so the strip, the detail and the checklist agree. */

import type { Frame, Roll } from "./api";

export const GREEN = 0.8;

export const isAnchored = (f: Frame) => f.source === "anchored" || f.source === "locked";
export const isResolved = (f: Frame) => f.status === "confirmed" || f.source === "skipped";
export const isGreen = (f: Frame) => f.confidence >= GREEN && f.source !== "skipped";

export interface Plan {
  n: number;
  anchored: number;
  confirmed: number;
  unresolved: Frame[];          // in scan order
  greensToConfirm: Frame[];
  next: Frame | null;           // the frame to look at
  headline: string;
  detail: string;
  action: "verify" | "facts" | "confirm-greens" | "review" | "inside" | "done";
}

export function plan(roll: Roll, frames: Frame[]): Plan {
  const n = frames.length;
  const anchored = frames.filter(isAnchored).length;
  const confirmed = frames.filter((f) => f.status === "confirmed").length;
  const unresolved = frames.filter((f) => !isResolved(f));
  const greens = unresolved.filter(isGreen);
  const reds = unresolved.filter((f) => !isGreen(f));
  const list = (fs: Frame[]) => fs.map((f) => f.number).join(", ");
  const base = { n, anchored, confirmed, unresolved, greensToConfirm: greens, next: greens[0] ?? reds[0] ?? null };

  if (unresolved.length === 0) {
    return { ...base, action: "done", headline: `All ${n} frames confirmed.`, detail: "Writing them into the scan files is the next milestone; nothing has been written yet." };
  }
  if (roll.verified_frames === 0 && anchored === 0) {
    return {
      ...base,
      action: "verify",
      headline: "Claude has not looked at this roll yet.",
      detail: `Run \`filmgeo verify ${roll.roll}\` in Terminal (about $${(0.035 * n * 2).toFixed(2)}), then "re-solve" — or match frames by hand below.`,
    };
  }
  if (roll.window_check.doubtful) {
    return { ...base, action: "facts", headline: "The window looks wrong.", detail: `${roll.window_check.reason}. Check the dates in roll facts, or widen it.` };
  }
  if (greens.length > 0) {
    return {
      ...base,
      action: "confirm-greens",
      headline: `${anchored} of ${n} matched by Claude, ${confirmed} confirmed.`,
      detail: `Confirm the ${greens.length} green frame${greens.length > 1 ? "s" : ""} if they look right, then look at ${reds.length ? `frame${reds.length > 1 ? "s" : ""} ${list(reds)}` : "nothing else — the rest is done"}.`,
    };
  }
  const canInside = reds.some((f) => !isAnchored(f)) && anchored > 0;
  return {
    ...base,
    action: canInside ? "inside" : "review",
    headline: `${confirmed} of ${n} confirmed, ${reds.length} to go: frame${reds.length > 1 ? "s" : ""} ${list(reds)}.`,
    detail: canInside
      ? `Each sits between matched neighbours now. Pick a possible photo, say it is the same day as a neighbour, or run \`filmgeo verify ${roll.roll} --inside\` (about $${(0.035 * 2 * reds.length).toFixed(2)}) to ask Claude about just these.`
      : "Pick a possible photo, type a time, or mark it no reference.",
  };
}

export function nextAfter(frames: Frame[], current: number | null): Frame | null {
  const order = current == null ? frames : [...frames.filter((f) => f.number > current), ...frames.filter((f) => f.number <= current)];
  return order.find((f) => !isResolved(f)) ?? null;
}

export const STEPS: { key: string; title: string; what: string }[] = [
  { key: "window", title: "Tell it when", what: "Set the roll's window in roll facts — the month, or the trip. The cheapest evidence there is." },
  { key: "verify", title: "Let Claude look", what: "`filmgeo verify <roll>` shows each frame its most similar phone photos and asks which is the same occasion." },
  { key: "greens", title: "Confirm the greens", what: "Frames with a green bar are matched and consistent with their neighbours. Check the photo, press Enter." },
  { key: "reds", title: "Resolve the rest", what: "Pick a possible photo, say it is the same day as a neighbour, type a time, or mark it no reference. Every choice re-solves the roll." },
  { key: "write", title: "Write", what: "Once every frame is confirmed, the dates, offsets and GPS go into the scan files (next milestone)." },
];

export function stepsDone(roll: Roll, frames: Frame[]): Record<string, boolean> {
  const p = plan(roll, frames);
  return {
    window: !!(roll.facts.window_from && roll.facts.window_to),
    verify: roll.verified_frames > 0,
    greens: frames.filter(isGreen).every((f) => f.status === "confirmed"),
    reds: p.unresolved.length === 0,
    write: false,
  };
}
