import type { Frame } from "../api";
import { fmtShort } from "../format";
import { isAnchored } from "../review";

interface Props {
  frame: Frame;
  frames: Frame[];
  busy: boolean;
  act: (body: Record<string, unknown>) => void;
  onOpenTime: () => void;
}

/** The one question this frame asks, and the answers as buttons. Everything else is beneath it. */
export function Question({ frame, frames, busy, act, onOpenTime }: Props) {
  const i = frames.findIndex((f) => f.number === frame.number);
  const prev = [...frames.slice(0, i)].reverse().find(isAnchored);
  const next = frames.slice(i + 1).find(isAnchored);
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
  if (frame.source === "locked") {
    return (
      <div className="q q--locked">
        <p className="q__text">
          You set this one{frame.anchor ? ` to the photo at ${fmtShort(frame.anchor.time, frame.anchor.tzoffset)}` : ""}. Keep it?
        </p>
        <div className="q__answers">
          {confirmBtn}
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
          Is this the right photo? <span className="muted">Claude says {frame.verdict ? `${frame.verdict.confidence.toFixed(2)}: ` : ""}{frame.verdict?.evidence || "—"}</span>
        </p>
        <div className="q__answers">
          {confirmBtn}
          <button className="btn btn--ghost" disabled={busy || !frame.anchor_uuid} onClick={() => act({ reject: [frame.anchor_uuid] })} title="n — not this photo; Claude's next choice or the neighbours decide">
            no, not this photo
          </button>
          <button className="btn btn--ghost" disabled={busy} onClick={() => act({ no_reference: true })} title="N — no phone photo shows this frame at all">
            no photo shows this frame
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
        No photo matched. It was shot {between}. <span className="muted">What do you know?</span>
      </p>
      <div className="q__answers">
        <button className="btn" disabled={busy} onClick={() => document.querySelector(".cands")?.scrollIntoView({ behavior: "smooth", block: "start" })}>
          pick a possible photo ↓
        </button>
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
        <button className="btn btn--ghost" onClick={onOpenTime}>
          type a time
        </button>
        <button className={`btn btn--ghost ${frame.override?.no_reference ? "btn--on" : ""}`} disabled={busy} onClick={() => act({ no_reference: !frame.override?.no_reference })} title="N — leave it between its neighbours">
          no photo shows it — leave it here
        </button>
        {confirmBtn}
        <button className="btn btn--ghost" disabled={busy} onClick={() => act({ skip: true })} title="x">
          unknown, skip
        </button>
      </div>
    </div>
  );
}
