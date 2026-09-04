import { useMemo, useState, type MouseEvent } from "react";
import type { Frame, Roll, TrailPoint } from "../api";
import { confidenceBand, fmtClock, fmtOffset, fmtShort, localParts } from "../format";

interface Props {
  roll: Roll;
  frames: Frame[];
  selected: Frame;
  trail: TrailPoint[];
  busy: boolean;
  onSelect: (n: number) => void;
  onSetTime: (iso: string) => void;
  onEvent?: (index: number) => void;
  browsing?: number | null;
}

const W = 720;
const H_WINDOW = 74;
const H_ZOOM = 92;

const ms = (iso: string) => new Date(iso).getTime();

/** ISO-8601 at an instant with the frame's own offset, minute precision, for a `when` fact. */
function isoAt(t: number, tzoffset: number): string {
  const d = new Date(Math.round(t / 60_000) * 60_000 + tzoffset * 1000);
  const p = (n: number) => String(n).padStart(2, "0");
  return `${d.getUTCFullYear()}-${p(d.getUTCMonth() + 1)}-${p(d.getUTCDate())}T${p(d.getUTCHours())}:${p(d.getUTCMinutes())}:00${fmtOffset(tzoffset)}`;
}

/** Tick instants at a readable step for a span, in the frame's local wall clock. */
function ticks(lo: number, hi: number, tzoffset: number): { t: number; label: string; major: boolean }[] {
  const span = hi - lo;
  const steps = [15, 30, 60, 120, 180, 360, 720, 1440, 2880, 7 * 1440, 14 * 1440].map((m) => m * 60_000);
  const step = steps.find((s) => span / s <= 9) ?? steps[steps.length - 1];
  const out = [];
  const off = tzoffset * 1000;
  let t = Math.ceil((lo + off) / step) * step - off;
  for (; t <= hi; t += step) {
    const p = localParts(new Date(t).toISOString(), tzoffset);
    const midnight = p.hh === "00" && p.mm === "00";
    const label = step >= 1440 * 60_000 || midnight ? `${p.weekday} ${p.day} ${p.month}` : `${p.hh}:${p.mm}`;
    out.push({ t, label, major: midnight });
  }
  return out;
}

/** The window as a strip: events as bars, frames as ticks at their assigned times, the selected frame's interval lit. */
export function Timeline({ roll, frames, selected, trail, busy, onSelect, onSetTime, onEvent, browsing = null }: Props) {
  const tz = selected.tzoffset ?? 0;
  const win = { lo: ms(roll.window.start), hi: ms(roll.window.end) };
  const ivl = { lo: ms(selected.t_lo), hi: ms(selected.t_hi) };
  const zoom = useMemo(() => {
    const span = Math.max(ivl.hi - ivl.lo, 2 * 3_600_000);
    const pad = Math.max(span * 0.35, 30 * 60_000);
    return { lo: Math.max(win.lo, ivl.lo - pad), hi: Math.min(win.hi, ivl.hi + pad) };
  }, [ivl.lo, ivl.hi, win.lo, win.hi]);
  const [hover, setHover] = useState<{ x: number; t: number } | null>(null);

  const bandProps = (range: { lo: number; hi: number }) => ({
    x: (t: number) => ((Math.min(Math.max(t, range.lo), range.hi) - range.lo) / (range.hi - range.lo)) * W,
    range,
  });
  const top = bandProps(win);
  const bot = bandProps(zoom);
  const maxCount = Math.max(1, ...roll.events.map((e) => e.count));

  const timeAt = (e: MouseEvent<SVGRectElement>, range: { lo: number; hi: number }) => {
    const r = e.currentTarget.getBoundingClientRect();
    const f = Math.min(1, Math.max(0, (e.clientX - r.left) / r.width));
    return range.lo + f * (range.hi - range.lo);
  };

  const renderBand = (b: ReturnType<typeof bandProps>, y0: number, h: number, zoomed: boolean) => {
    const evY = y0 + 14;
    const evH = h - 44;
    return (
      <g>
        <rect x={0} y={y0} width={W} height={h} className="tl__bg" />
        {/* click-to-set overlay, under the events and frame ticks so those stay clickable */}
        <rect
          x={0}
          y={y0}
          width={W}
          height={h}
          className="tl__hit"
          onMouseMove={(e) => setHover({ x: b.x(timeAt(e, b.range)), t: timeAt(e, b.range) })}
          onMouseLeave={() => setHover(null)}
          onClick={(e) => !busy && onSetTime(isoAt(timeAt(e, b.range), tz))}
        />
        {/* the selected frame's interval */}
        <rect x={b.x(ivl.lo)} y={y0} width={Math.max(2, b.x(ivl.hi) - b.x(ivl.lo))} height={h} className="tl__interval" />
        {/* day / hour ticks */}
        {ticks(b.range.lo, b.range.hi, tz).map((k) => (
          <g key={k.t} className={`tl__tick ${k.major ? "is-major" : ""}`}>
            <line x1={b.x(k.t)} x2={b.x(k.t)} y1={y0} y2={y0 + h} />
            <text x={b.x(k.t) + 3} y={y0 + 10}>
              {k.label}
            </text>
          </g>
        ))}
        {/* events as bars, height by photo count */}
        {roll.events
          .filter((e) => ms(e.end) >= b.range.lo && ms(e.start) <= b.range.hi)
          .map((e) => {
            const x = b.x(ms(e.start));
            const w = Math.max(2, b.x(ms(e.end)) - x);
            const bh = Math.max(3, (evH * Math.log1p(e.count)) / Math.log1p(maxCount));
            return (
              <rect
                key={e.index}
                x={x}
                y={evY + evH - bh}
                width={w}
                height={bh}
                className={`tl__event ${e.index === selected.event ? "is-current" : ""} ${e.index === browsing ? "is-browsing" : ""}`}
                onClick={(ev) => {
                  ev.stopPropagation();
                  onEvent?.(e.index);
                }}
              >
                <title>
                  event {e.index}: {e.count} photos, {fmtShort(e.start, tz)} – {fmtShort(e.end, tz)} — click to browse its photos
                </title>
              </rect>
            );
          })}
        {/* trail points, zoomed band only */}
        {zoomed &&
          trail.map((p, i) => (
            <circle key={i} cx={b.x(ms(p.time))} cy={evY + evH + 4} r={2} className="tl__trail">
              <title>
                {fmtShort(p.time, p.tzoffset)} {p.source}
              </title>
            </circle>
          ))}
        {/* frames as ticks */}
        {frames
          .filter((f) => ms(f.time) >= b.range.lo && ms(f.time) <= b.range.hi)
          .map((f) => (
            <g
              key={f.number}
              className={`tl__frame band-${confidenceBand(f.confidence, f.locked)} ${f.number === selected.number ? "is-selected" : ""}`}
              onClick={(ev) => {
                ev.stopPropagation();
                onSelect(f.number);
              }}
            >
              <line x1={b.x(ms(f.time))} x2={b.x(ms(f.time))} y1={y0 + h - 26} y2={y0 + h - 4} />
              {(zoomed || f.number === selected.number) && (
                <text x={b.x(ms(f.time))} y={y0 + h - 28} textAnchor="middle">
                  {f.number}
                </text>
              )}
              <title>
                frame {f.number} · {fmtShort(f.time, f.tzoffset)} · {f.source}
              </title>
            </g>
          ))}
        {hover && hover.t >= b.range.lo && hover.t <= b.range.hi && (
          <g className="tl__hover">
            <line x1={b.x(hover.t)} x2={b.x(hover.t)} y1={y0} y2={y0 + h} />
            <text x={b.x(hover.t) + 4} y={y0 + h - 6}>
              {fmtShort(new Date(hover.t).toISOString(), tz)} {fmtClock(new Date(hover.t).toISOString(), tz).slice(0, 0)}
            </text>
          </g>
        )}
      </g>
    );
  };

  return (
    <div className="tl">
      <div className="tl__head">
        <span className="eyebrow">Timeline</span>
        <span className="muted">events as bars, frames as ticks · click to set the time by hand{busy ? " · solving…" : ""}</span>
      </div>
      <svg viewBox={`0 0 ${W} ${H_WINDOW + 8 + H_ZOOM}`} className="tl__svg" preserveAspectRatio="none">
        {renderBand(top, 0, H_WINDOW, false)}
        {/* the zoomed span, drawn on the window band */}
        <rect x={top.x(zoom.lo)} y={H_WINDOW - 3} width={Math.max(2, top.x(zoom.hi) - top.x(zoom.lo))} height={3} className="tl__zoomspan" />
        {renderBand(bot, H_WINDOW + 8, H_ZOOM, true)}
      </svg>
      <div className="tl__foot mono muted">
        window {fmtShort(roll.window.start, tz)} → {fmtShort(roll.window.end, tz)} · below: {fmtShort(new Date(zoom.lo).toISOString(), tz)} → {fmtShort(new Date(zoom.hi).toISOString(), tz)} (UTC{fmtOffset(tz)})
      </div>
    </div>
  );
}
