import { useEffect, useState } from "react";
import type { ChronoPhoto, Frame } from "../api";
import { useFramePhotos } from "../api";
import { fmtClock, fmtDate, fmtDelta, fmtOffset, fmtShort } from "../format";

interface Props {
  rollKey: string;
  frame: Frame;
  prev?: Frame;
  next?: Frame;
  busy: boolean;
  error: string | null;
  onPick: (uuid: string) => void;
  onClose: () => void;
}

const DAY_FMT = new Intl.DateTimeFormat(undefined, { weekday: "long", day: "numeric", month: "long", year: "numeric", timeZone: "UTC" });

/** Every phone photo between the frames either side of this one, in the order they were taken, by day and
 *  then by outing, with bursts folded. Nothing here needs to *look like* the frame: the point is to find the
 *  photo taken a moment before or after it — facing the other way, of the same table, from the same car —
 *  and say "this frame was shot here", which gives the frame that photo's time and GPS. */
export function PhotoChronology({ rollKey, frame, prev, next, busy, error, onPick, onClose }: Props) {
  const [allDays, setAllDays] = useState(false);
  const [openBursts, setOpenBursts] = useState<Set<string>>(new Set());
  const [pending, setPending] = useState<ChronoPhoto | null>(null);
  const q = useFramePhotos(rollKey, frame.number, allDays);
  useEffect(() => {
    const on = (e: KeyboardEvent) => {
      if (e.key === "Escape") (pending ? setPending(null) : onClose());
    };
    addEventListener("keydown", on);
    return () => removeEventListener("keydown", on);
  }, [onClose, pending]);

  const d = q.data;
  const bounds = prev && next ? `between frame ${prev.number} and frame ${next.number}` : prev ? `after frame ${prev.number}` : next ? `before frame ${next.number}` : "in the roll's window";

  return (
    <div className="sheet" onClick={onClose}>
      <div className="sheet__card chrono" onClick={(e) => e.stopPropagation()}>
        <div className="sheet__head">
          <div>
            <span className="eyebrow">Frame {String(frame.number).padStart(2, "0")}</span>
            <h2 className="sheet__title">Which phone photo was taken right next to it?</h2>
          </div>
          <button type="button" className="link" onClick={onClose}>
            close (esc)
          </button>
        </div>
        <p className="muted chrono__hint">
          {d ? `${d.total} photos` : "Photos"} {allDays ? "in the roll's window" : bounds}
          {d ? ` — ${fmtShort(d.from, frame.tzoffset)} to ${fmtShort(d.to, frame.tzoffset)}` : ""}, oldest first, grouped by day and outing; shots within a minute of each other are folded behind the first.
          {" "}It need not look like the frame. Click the photo you took just before or after it.
          {!allDays && (
            <>
              {" "}
              <button type="button" className="link" onClick={() => setAllDays(true)}>
                the frame might be outside these bounds — show the whole window
              </button>
            </>
          )}
          {allDays && (
            <>
              {" "}
              <button type="button" className="link" onClick={() => setAllDays(false)}>
                back to {bounds}
              </button>
            </>
          )}
        </p>

        <div className="chrono__body">
          <figure className="chrono__frame">
            <img src={`${frame.image}?size=large`} alt={`frame ${frame.number}`} />
            <figcaption className="muted">the frame, for comparison</figcaption>
          </figure>

          <div className="chrono__list">
            {q.isLoading && <p className="muted">loading…</p>}
            {q.error && <p className="error">{(q.error as Error).message}</p>}
            {d && d.total === 0 && <p className="muted">No phone photos in this stretch. Set the date and place by hand instead.</p>}
            {d?.days.map((day) => (
              <section key={day.day} className="chrono__day">
                <h3 className="chrono__dayhead">
                  {DAY_FMT.format(new Date(`${day.day}T00:00:00Z`))} <span className="muted">· {day.count} photos</span>
                </h3>
                {day.events.map((ev) => (
                  <div key={ev.index} className="chrono__event">
                    <div className="chrono__evhead mono muted">
                      {fmtClock(ev.start, frame.tzoffset)} – {fmtClock(ev.end, frame.tzoffset)} {fmtOffset(frame.tzoffset)} · {ev.count} {ev.count === 1 ? "photo" : "photos"}
                      {ev.lat != null && ev.lon != null ? ` · ${ev.lat.toFixed(3)}, ${ev.lon.toFixed(3)}` : ""}
                    </div>
                    <ol className="chrono__grid">
                      {ev.photos.map((p) => {
                        const burstOpen = openBursts.has(p.uuid);
                        const shots = burstOpen ? [p, ...p.more] : [p];
                        return shots.map((s, k) => (
                          <li key={s.uuid} className={`photo ${frame.anchor_uuid === s.uuid ? "is-chosen" : ""} ${k > 0 ? "photo--burst" : ""}`}>
                            <button type="button" disabled={busy} onClick={() => setPending(s)} title={`${s.filename} — this frame was shot here`}>
                              <img src={`${s.image}?size=small`} alt="" loading="lazy" />
                              <span className="mono">
                                {fmtClock(s.time, s.tzoffset, true)} <span className="muted">{fmtDelta(frame.time, s.time)}</span>
                              </span>
                            </button>
                            {k === 0 && p.more.length > 0 && (
                              <button
                                type="button"
                                className="photo__more link"
                                onClick={() =>
                                  setOpenBursts((o) => {
                                    const n = new Set(o);
                                    if (n.has(p.uuid)) n.delete(p.uuid);
                                    else n.add(p.uuid);
                                    return n;
                                  })
                                }
                              >
                                {burstOpen ? "fold" : `+${p.more.length} within a minute`}
                              </button>
                            )}
                          </li>
                        ));
                      })}
                    </ol>
                  </div>
                ))}
              </section>
            ))}
          </div>
        </div>

        {pending && (
          <div className="chrono__confirm">
            <img src={`${pending.image}?size=small`} alt="" />
            <div className="chrono__confirmtext">
              <strong>This frame was shot here?</strong>
              <span className="muted">
                Frame {frame.number} takes this photo's time — {fmtDate(pending.time, pending.tzoffset)} {fmtClock(pending.time, pending.tzoffset, true)} {fmtOffset(pending.tzoffset)} — and its GPS
                {pending.lat != null && pending.lon != null ? ` (${pending.lat.toFixed(5)}, ${pending.lon.toFixed(5)})` : " (this photo has none; the trail places it)"}. The roll re-solves around it.
              </span>
              {error && <span className="error">{error}</span>}
            </div>
            <button type="button" className="btn btn--write" disabled={busy} onClick={() => onPick(pending.uuid)}>
              {busy ? "solving…" : "yes, shot here"}
            </button>
            <button type="button" className="btn btn--ghost" disabled={busy} onClick={() => setPending(null)}>
              no
            </button>
          </div>
        )}
      </div>
    </div>
  );
}
