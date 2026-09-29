import { useState } from "react";
import type { Frame, Place } from "../api";
import { usePlaces, usePlaceSearch } from "../api";
import { fmtClock, fmtShort } from "../format";

interface Props {
  rollKey: string;
  frame: Frame;
  busy: boolean;
  onUse: (p: Place) => void;
}

function span(p: Place, tz: number | null): string {
  const off = p.tzoffset ?? tz;
  if (p.start === p.end) return fmtShort(p.start, off);
  return `${fmtShort(p.start, off)} – ${fmtClock(p.end, off)}`;
}

function distance(m: number | null | undefined): string {
  if (m == null) return "";
  return m < 1000 ? `${m} m from the pin` : `${(m / 1000).toFixed(1)} km from the pin`;
}

/** Places you were, by name: check-ins, camera taps and visits. One click dates and places the frame. */
export function PlacesStrip({ rollKey, frame, busy, onUse }: Props) {
  const places = usePlaces(rollKey, frame.number, `${frame.time}|${frame.t_lo}|${frame.t_hi}`);
  const [routine, setRoutine] = useState(false);
  const [q, setQ] = useState("");
  const found = usePlaceSearch(rollKey, q);
  const all = places.data?.places ?? [];
  const shown = all.filter((p) => routine || !p.routine);
  const hidden = all.length - shown.length;
  const sources = places.data?.sources ?? {};
  const hasSignals = (sources.swarm ?? 0) + (sources.visit ?? 0) + (sources.nfc ?? 0) > 0;
  const tz = frame.tzoffset;

  const card = (p: Place, k: number) => {
    const current = frame.fact?.place_name === p.name && frame.locked;
    return (
      <li key={`${p.ref}-${k}`} className={`place ${current ? "is-chosen" : ""} ${p.routine ? "is-routine" : ""}`}>
        <span className="place__name">{p.name}</span>
        <span className="mono">{span(p, tz)}</span>
        <span className="muted place__meta">
          {p.kind === "check-in" ? "you checked in" : p.kind === "tap" ? "camera tag tapped" : "your phone was here"}
          {p.distance_m != null ? ` · ${distance(p.distance_m)}` : ""}
        </span>
        <button className="btn btn--use" disabled={busy || current} onClick={() => onUse(p)} title="set this frame's time and map pin from this place; neighbours re-solve">
          {current ? "in use" : p.kind === "check-in" ? "use this check-in" : "use this place"}
        </button>
      </li>
    );
  };

  return (
    <div className="places">
      <div className="places__head">
        <span className="eyebrow">Places you were</span>
        <span className="muted">
          {places.data
            ? `${shown.length || "none"} between ${fmtShort(places.data.from, tz)} and ${fmtShort(places.data.to, tz)}, where scan order allows this frame`
            : "looking…"}
        </span>
        {hidden > 0 && (
          <button className="link" onClick={() => setRoutine(true)}>
            show {hidden} at home or work
          </button>
        )}
        {routine && (
          <button className="link" onClick={() => setRoutine(false)}>
            hide home and work
          </button>
        )}
      </div>
      {places.data && !hasSignals && (
        <p className="muted places__empty">No check-ins or location history are loaded. Copy your Swarm export into `.filmgeo/signals/swarm/` and press re-solve.</p>
      )}
      {shown.length > 0 && <ol className="places__list">{shown.map(card)}</ol>}
      <div className="places__search">
        <input value={q} placeholder="know the place but not the day? search by name" onChange={(e) => setQ(e.target.value)} />
        {q.trim().length >= 2 && found.data && found.data.length === 0 && <span className="muted">nothing by that name in this roll's window</span>}
      </div>
      {q.trim().length >= 2 && found.data && found.data.length > 0 && <ol className="places__list">{found.data.map(card)}</ol>}
    </div>
  );
}
