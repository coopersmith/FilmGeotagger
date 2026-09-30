import { useEffect, useState } from "react";
import type { AssignBody, Frame } from "../api";
import { parseCoords } from "../coords";
import { fmtOffset, fmtShort, fromDatetimeLocal, toDatetimeLocal } from "../format";

const OFFSETS = [-43200, -39600, -36000, -32400, -28800, -25200, -21600, -18000, -14400, -10800, -7200, -3600, 0, 3600, 7200, 10800, 14400, 19800, 28800, 32400, 36000, 39600, 43200];

interface Props {
  frame: Frame;
  prev?: Frame;
  next?: Frame;
  busy: boolean;
  error: string | null;
  onSave: (body: AssignBody) => void;
  onClose: () => void;
}

/** The two things this tool exists to produce, asked for plainly: when was this frame shot, and where.
 *  Either half may be left blank — a date alone locks the time and lets the trail place it, coordinates alone
 *  pin the place and let the neighbours date it. Saving locks the frame; the roll re-solves around it. */
export function SetDatePlace({ frame, prev, next, busy, error, onSave, onClose }: Props) {
  const offset0 = frame.tzoffset ?? 0;
  const [dated, setDated] = useState(!!frame.fact?.when || frame.source === "anchored" || frame.source === "locked");
  const [value, setValue] = useState(toDatetimeLocal(frame.time, offset0));
  const [offset, setOffset] = useState(offset0);
  const [coords, setCoords] = useState(frame.location === "ok" && frame.locked ? `${frame.lat!.toFixed(5)}, ${frame.lon!.toFixed(5)}` : "");
  const [place, setPlace] = useState(frame.fact?.place_name ?? "");
  const parsed = parseCoords(coords);
  const coordsBad = coords.trim() !== "" && !parsed;
  const offsets = OFFSETS.includes(offset0) ? OFFSETS : [...OFFSETS, offset0].sort((a, b) => a - b);
  const canSave = !busy && !coordsBad && (dated || !!parsed || place.trim() !== "");

  useEffect(() => {
    const on = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    addEventListener("keydown", on);
    return () => removeEventListener("keydown", on);
  }, [onClose]);

  const submit = (e: React.FormEvent) => {
    e.preventDefault();
    if (!canSave) return;
    const body: AssignBody = {};
    if (dated) body.when = fromDatetimeLocal(value, offset);
    if (parsed) {
      body.lat = parsed.lat;
      body.lon = parsed.lon;
    }
    if (place.trim()) body.place_name = place.trim();
    onSave(body);
  };

  return (
    <div className="sheet" onClick={onClose}>
      <form className="sheet__card sdp" onClick={(e) => e.stopPropagation()} onSubmit={submit}>
        <div className="sheet__head">
          <div>
            <span className="eyebrow">Frame {String(frame.number).padStart(2, "0")}</span>
            <h2 className="sheet__title">Set the date and place</h2>
          </div>
          <button type="button" className="link" onClick={onClose}>
            close (esc)
          </button>
        </div>
        <p className="muted sdp__hint">
          Fill in what you know; leave the rest blank and the roll works it out from the neighbours.
          {prev || next ? (
            <>
              {" "}
              It sits {prev ? `after frame ${prev.number} (${fmtShort(prev.time, prev.tzoffset)})` : ""}
              {prev && next ? " and " : ""}
              {next ? `before frame ${next.number} (${fmtShort(next.time, next.tzoffset)})` : ""}.
            </>
          ) : null}
        </p>

        <fieldset className="sdp__block">
          <legend>
            <label className="sdp__check">
              <input type="checkbox" checked={dated} onChange={(e) => setDated(e.target.checked)} /> <span className="eyebrow">When</span>
            </label>
            <span className="muted">{dated ? "this exact local time" : "unknown — placed between its neighbours"}</span>
          </legend>
          <div className={`sdp__row ${dated ? "" : "is-off"}`}>
            <label className="sdp__field">
              <span className="muted">local date and time</span>
              <input type="datetime-local" value={value} step={60} disabled={!dated} onChange={(e) => setValue(e.target.value)} autoFocus />
            </label>
            <label className="sdp__field">
              <span className="muted">time zone</span>
              <select value={offset} disabled={!dated} onChange={(e) => setOffset(Number(e.target.value))}>
                {offsets.map((o) => (
                  <option key={o} value={o}>
                    UTC{fmtOffset(o)}
                  </option>
                ))}
              </select>
            </label>
          </div>
          {frame.offset_disputed && frame.offsets && frame.offsets.length > 1 && (
            <p className="muted sdp__note">The roll crosses a zone change here: {frame.offsets.map((o) => `UTC${fmtOffset(o)}`).join(" or ")}.</p>
          )}
        </fieldset>

        <fieldset className="sdp__block">
          <legend>
            <span className="eyebrow">Where</span> <span className="muted">coordinates, or a pasted Google / Apple Maps link</span>
          </legend>
          <label className="sdp__field">
            <span className="muted">latitude, longitude</span>
            <input type="text" value={coords} placeholder="41.5209, -71.1921  ·  43°46'10.6&quot;N 11°15'20.9&quot;E  ·  https://maps…/@41.52,-71.19" onChange={(e) => setCoords(e.target.value)} className={coordsBad ? "is-bad" : ""} />
            {parsed && (
              <span className="mono muted">
                → {parsed.lat.toFixed(5)}, {parsed.lon.toFixed(5)}
              </span>
            )}
            {coordsBad && <span className="error">could not read a latitude and longitude from that</span>}
          </label>
          <label className="sdp__field">
            <span className="muted">place name (optional — a note for you; becomes the frame's place fact)</span>
            <input type="text" value={place} placeholder="Young Family Farm" onChange={(e) => setPlace(e.target.value)} />
          </label>
        </fieldset>

        <div className="sheet__actions">
          <button className="btn btn--write" type="submit" disabled={!canSave}>
            {busy ? "solving…" : "save and lock this frame"}
          </button>
          <span className="muted">
            {dated && parsed ? "date and place" : dated ? "date only — the trail places it" : parsed ? "place only — the neighbours date it" : place.trim() ? "name only" : "nothing to save yet"}
          </span>
          {error && <span className="error">{error}</span>}
        </div>
      </form>
    </div>
  );
}
