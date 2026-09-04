/** Times are ISO-8601 with an offset; the UI shows the frame's own local wall clock. */

const DAYS = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"];
const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];

/** Wall-clock parts of an instant at a UTC offset (seconds east). Falls back to the ISO string's own offset. */
export function localParts(iso: string, tzoffset: number | null | undefined) {
  const offset = tzoffset ?? isoOffsetSeconds(iso);
  const d = new Date(new Date(iso).getTime() + offset * 1000);
  return {
    weekday: DAYS[d.getUTCDay()],
    day: d.getUTCDate(),
    month: MONTHS[d.getUTCMonth()],
    year: d.getUTCFullYear(),
    hh: String(d.getUTCHours()).padStart(2, "0"),
    mm: String(d.getUTCMinutes()).padStart(2, "0"),
    ss: String(d.getUTCSeconds()).padStart(2, "0"),
    offset,
  };
}

export function isoOffsetSeconds(iso: string): number {
  const m = iso.match(/([+-])(\d{2}):(\d{2})$/);
  if (!m) return 0;
  const s = (Number(m[2]) * 60 + Number(m[3])) * 60;
  return m[1] === "-" ? -s : s;
}

export function fmtOffset(seconds: number | null | undefined): string {
  if (seconds == null) return "—";
  const sign = seconds < 0 ? "-" : "+";
  const a = Math.abs(seconds);
  return `${sign}${String(Math.floor(a / 3600)).padStart(2, "0")}:${String(Math.floor((a % 3600) / 60)).padStart(2, "0")}`;
}

export function fmtDate(iso: string, tzoffset?: number | null): string {
  const p = localParts(iso, tzoffset);
  return `${p.weekday} ${p.day} ${p.month} ${p.year}`;
}

export function fmtClock(iso: string, tzoffset?: number | null, seconds = false): string {
  const p = localParts(iso, tzoffset);
  return seconds ? `${p.hh}:${p.mm}:${p.ss}` : `${p.hh}:${p.mm}`;
}

export function fmtShort(iso: string, tzoffset?: number | null): string {
  const p = localParts(iso, tzoffset);
  return `${p.day} ${p.month} ${p.hh}:${p.mm}`;
}

/** Value for an <input type="datetime-local">: local wall clock, minute precision. */
export function toDatetimeLocal(iso: string, tzoffset?: number | null): string {
  const p = localParts(iso, tzoffset);
  return `${p.year}-${String(MONTHS.indexOf(p.month) + 1).padStart(2, "0")}-${String(p.day).padStart(2, "0")}T${p.hh}:${p.mm}`;
}

/** A datetime-local value plus an offset back to ISO-8601 with that offset. */
export function fromDatetimeLocal(value: string, tzoffset: number): string {
  return `${value}:00${fmtOffset(tzoffset)}`;
}

export function hoursBetween(a: string, b: string): number {
  return (new Date(b).getTime() - new Date(a).getTime()) / 3_600_000;
}

/** "+6 min", "-2.3 h", "+3 d": how far a candidate sits from the frame's assigned time. */
export function fmtDelta(fromIso: string, toIso: string): string {
  const h = hoursBetween(fromIso, toIso);
  const sign = h >= 0 ? "+" : "−";
  const a = Math.abs(h);
  if (a < 1) return `${sign}${Math.round(a * 60)} min`;
  if (a < 48) return `${sign}${a.toFixed(1)} h`;
  return `${sign}${(a / 24).toFixed(1)} d`;
}

export function confidenceBand(c: number, locked: boolean): "locked" | "high" | "mid" | "low" {
  if (locked) return "locked";
  if (c >= 0.8) return "high";
  if (c >= 0.5) return "mid";
  return "low";
}

/** Mirrors `align.report.interval_text`, but in the frame's own offset rather than the photo's zone. */
export function intervalText(f: { source: string; t_lo: string; t_hi: string; tzoffset: number | null }): string {
  const lo = localParts(f.t_lo, f.tzoffset);
  const hi = localParts(f.t_hi, f.tzoffset);
  const sameDay = lo.year === hi.year && lo.month === hi.month && lo.day === hi.day;
  const loText = `${lo.weekday} ${lo.day} ${lo.month} ${lo.hh}:${lo.mm}`;
  const hiText = sameDay ? `${hi.hh}:${hi.mm}` : `${hi.weekday} ${hi.day} ${hi.month} ${hi.hh}:${hi.mm}`;
  if (f.source === "anchored" || f.source === "locked") return `this occasion, ${loText}–${hiText}`;
  return `between ${loText} and ${hiText}`;
}
