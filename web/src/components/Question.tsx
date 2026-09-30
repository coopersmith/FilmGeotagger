import type { Frame } from "../api";
import { fmtShort } from "../format";
import { isPinned } from "../review";

interface Props {
  frame: Frame;
  frames: Frame[];
  busy: boolean;
  act: (body: Record<string, unknown>) => void;
  onOpenTime: () => void;
  onSetDatePlace: () => void;
  onBrowsePhotos: () => void;
}

/** The one question this frame asks, and the answers as buttons. Everything else is beneath it. */
export function Question({ frame, frames, busy, act, onOpenTime, onSetDatePlace, onBrowsePhotos }: Props) {
  const i = frames.findIndex((f) => f.number === frame.number);
  const prev = [...frames.slice(0, i)].reverse().find(isPinned);
  const next = frames.slice(i + 1).find(isPinned);
  const confirmed = frame.status === "confirmed";
  const confirmBtn = (
    <button className={`btn ${confirmed ? "btn--on" : ""}`} disabled={busy} onClick={() => act({ confirmed: !confirmed })} title="Enter">
      {confirmed ? "✓ confirmed — undo" : "yes, confirm"}
    </button>
  );

  if (frame.source === "skipped") {
    return (
      <div className="q q--skipped">
        <p className="q__text">Skipped. Nothing will be written for this frame.</p>
        <div className="q__answers">
          <button className="btn btn--ghost" disabled={busy} onClick={() => act({ unlock: true })}>
            un-skip
          </button>
        </div>
      </div>
    );
  }
  const handSet = frame.locked && !!frame.fact && (!!frame.fact.when || frame.fact.lat != null || !!frame.fact.same_time_as);
  if (frame.source === "locked" || handSet) {
    const what = frame.anchor
      ? ` to the photo at ${fmtShort(frame.anchor.time, frame.anchor.tzoffset)}`
      : frame.fact?.same_time_as
        ? ` moments ${frame.fact.same_time_as < frame.number ? "after" : "before"} frame ${frame.fact.same_time_as} — ${fmtShort(frame.time, frame.tzoffset)}${frame.fact.lat != null ? `, at ${frame.fact.place_name || `${frame.fact.lat.toFixed(4)}, ${frame.fact.lon!.toFixed(4)}`}` : ""}`
      : frame.fact?.when && frame.fact.lat != null
        ? ` to ${fmtShort(frame.time, frame.tzoffset)} at ${frame.fact.place_name || `${frame.fact.lat.toFixed(4)}, ${frame.fact.lon!.toFixed(4)}`}`
        : frame.fact?.when
          ? ` to ${fmtShort(frame.time, frame.tzoffset)}; the place comes from the trail`
          : frame.fact?.lat != null
            ? ` at ${frame.fact.place_name || `${frame.fact.lat.toFixed(4)}, ${frame.fact.lon!.toFixed(4)}`}; the time comes from its neighbours`
            : "";
    return (
      <div className="q q--locked">
        <p className="q__text">
          You set this one{what}. Keep it?
        </p>
        <div className="q__answers">
          {confirmBtn}
          <button className="btn btn--ghost" disabled={busy} onClick={onSetDatePlace} title="d">
            change the date or place…
          </button>
          <button className="btn btn--ghost" disabled={busy} onClick={onBrowsePhotos} title="p">
            a different photo, in time order…
          </button>
          <button className="btn btn--ghost" disabled={busy} onClick={() => act({ unlock: true })} title="u">
            undo my decision
          </button>
        </div>
      </div>
    );
  }
  if (frame.source === "anchored") {
    return (
      <div className="q q--anchored">
        <p className="q__text">
          Is this the right photo? <span className="muted">Claude says {frame.verdict ? `${frame.verdict.confidence.toFixed(2)}: ` : ""}{frame.verdict?.evidence || "—"}{frame.possible.length > 1 ? ` If a different shot of the same occasion is the exact one, it is below${frame.possible_variant === "siglip_gray" ? ", ranked in grayscale" : ""}.` : ""}</span>
        </p>
        <div className="q__answers">
          {confirmBtn}
          <button className="btn btn--ghost" disabled={busy || !frame.anchor_uuid} onClick={() => act({ reject: [frame.anchor_uuid] })} title="n — not this photo; Claude's next choice or the neighbours decide">
            no, not this photo
          </button>
          <button className="btn btn--ghost" disabled={busy} onClick={() => act({ no_reference: true })} title="N — no phone photo shows this frame at all">
            no photo shows this frame
          </button>
          <button className="btn btn--ghost" disabled={busy} onClick={onBrowsePhotos} title="p">
            a different photo, in time order…
          </button>
          <button className="btn btn--ghost" disabled={busy} onClick={onSetDatePlace} title="d">
            set the date and place myself…
          </button>
        </div>
      </div>
    );
  }
  const between =
    prev && next
      ? `between frame ${prev.number} (${fmtShort(prev.time, prev.tzoffset)}) and frame ${next.number} (${fmtShort(next.time, next.tzoffset)})`
      : prev
        ? `after frame ${prev.number} (${fmtShort(prev.time, prev.tzoffset)})`
        : next
          ? `before frame ${next.number} (${fmtShort(next.time, next.tzoffset)})`
          : "somewhere in the window";
  return (
    <div className="q q--open">
      <p className="q__text">
        No photo matched. It was shot {between}. <span className="muted">Two ways to place it:</span>
      </p>
      <div className="q__answers q__answers--primary">
        <button className="btn btn--primary" disabled={busy} onClick={onSetDatePlace} title="d — type the date and time, paste coordinates or a map link">
          <strong>Set the date and place</strong>
          <span className="muted">you know when or where — type it</span>
        </button>
        <button className="btn btn--primary" disabled={busy} onClick={onBrowsePhotos} title="p — every phone photo between the neighbouring frames, oldest first">
          <strong>Find the phone photo taken next to it</strong>
          <span className="muted">browse {between.replace(/ \([^)]*\)/g, "")} in time order</span>
        </button>
      </div>
      {(prev || next) && (
        <div className="q__answers q__answers--pair">
          <span className="muted">Or it was shot</span>
          {prev && (
            <button className="btn" disabled={busy} onClick={() => act({ same_time_as: prev.number })} title="within a few minutes after that frame, at its place; re-solves">
              moments after frame {prev.number}
            </button>
          )}
          {next && (
            <button className="btn" disabled={busy} onClick={() => act({ same_time_as: next.number })} title="within a few minutes before that frame, at its place; re-solves">
              moments before frame {next.number}
            </button>
          )}
          {prev && (
            <button className="btn btn--ghost" disabled={busy} onClick={() => act({ same_day_as: prev.number })} title="binds this frame to that day; re-solves">
              same day as frame {prev.number}
            </button>
          )}
          {next && (!prev || fmtShort(prev.time, prev.tzoffset).slice(0, 6) !== fmtShort(next.time, next.tzoffset).slice(0, 6)) && (
            <button className="btn btn--ghost" disabled={busy} onClick={() => act({ same_day_as: next.number })} title="binds this frame to that day; re-solves">
              same day as frame {next.number}
            </button>
          )}
        </div>
      )}
      <details className="q__more">
        <summary className="link">more ways</summary>
        <div className="q__answers">
          {frame.possible.length > 0 && (
            <button className="btn btn--ghost" disabled={busy} onClick={() => document.querySelector(".cands")?.scrollIntoView({ behavior: "smooth", block: "start" })}>
              pick a possible photo ↓
            </button>
          )}
          <button className="btn btn--ghost" disabled={busy} onClick={() => document.querySelector(".places")?.scrollIntoView({ behavior: "smooth", block: "start" })}>
            pick a place you were ↓
          </button>
          <button className="btn btn--ghost" onClick={onOpenTime}>
            nudge the time by hand
          </button>
          <button className={`btn btn--ghost ${frame.override?.no_reference ? "btn--on" : ""}`} disabled={busy} onClick={() => act({ no_reference: !frame.override?.no_reference })} title="N — leave it between its neighbours">
            no photo shows it — leave it here
          </button>
          {confirmBtn}
          <button className="btn btn--ghost" disabled={busy} onClick={() => act({ skip: true })} title="x">
            unknown, skip
          </button>
        </div>
      </details>
    </div>
  );
}
