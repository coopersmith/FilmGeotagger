import { useState } from "react";
import type { Candidate, Frame } from "../api";
import { fmtClock, fmtDate, fmtDelta, fmtShort } from "../format";

interface Props {
  frame: Frame;
  frames: Frame[];
  busy: boolean;
  error: string | null;
  onPick: (uuid: string) => void;
  onReject: (uuid: string) => void;
}

/** Two lists with different jobs. "Possible photos" is what the frame can be, given its
 *  neighbours — the thing to act on. "What Claude saw" is the month-wide shortlist and its
 *  verdicts, kept for when you want to know why a frame is red; folded by default. */
export function CandidateStrip({ frame, frames, busy, error, onPick, onReject }: Props) {
  const [showSeen, setShowSeen] = useState(false);
  const i = frames.findIndex((f) => f.number === frame.number);
  const prevAnchor = [...frames.slice(0, i)].reverse().find((f) => f.source === "anchored" || f.source === "locked");
  const nextAnchor = frames.slice(i + 1).find((f) => f.source === "anchored" || f.source === "locked");
  const anchored = frame.source === "anchored" || frame.source === "locked";
  const bounds =
    prevAnchor && nextAnchor
      ? `between frame ${prevAnchor.number} (${fmtShort(prevAnchor.time, prevAnchor.tzoffset)}) and frame ${nextAnchor.number} (${fmtShort(nextAnchor.time, nextAnchor.tzoffset)})`
      : prevAnchor
        ? `after frame ${prevAnchor.number} (${fmtShort(prevAnchor.time, prevAnchor.tzoffset)})`
        : nextAnchor
          ? `before frame ${nextAnchor.number} (${fmtShort(nextAnchor.time, nextAnchor.tzoffset)})`
          : "anywhere in the window";
  const possible = frame.possible;
  const seenOutside = frame.candidates.filter((c) => !(frame.t_lo <= c.time && c.time <= frame.t_hi)).length;

  return (
    <div className="cands">
      <div className="cands__head">
        <span className="eyebrow">{anchored ? "This occasion" : "Possible photos"}</span>
        <span className="muted">
          {anchored
            ? `the chosen photo and the others from the same occasion, ${fmtShort(frame.t_lo, frame.tzoffset)} – ${fmtClock(frame.t_hi, frame.tzoffset)}`
            : `${possible.length ? possible.length : "no"} phone photos ${bounds}, one per occasion, most similar first`}
        </span>
        {busy && <span className="muted">re-solving…</span>}
        {error && <span className="error">{error}</span>}
      </div>
      {possible.length > 0 ? (
        <ol className="cands__strip">{possible.map((c, k) => card(c, k, frame, busy, onPick, onReject))}</ol>
      ) : (
        <p className="muted cands__empty">
          No phone photos in this stretch. Say it is the same day as a neighbour, type a time, or mark it "no reference".
        </p>
      )}

      <button className="cands__toggle link" onClick={() => setShowSeen((s) => !s)}>
        {showSeen ? "▾" : "▸"} what Claude saw
        <span className="muted">
          {" "}
          — {frame.verdict ? `${frame.verdict.shown.length} photos from the whole window` : "not verified yet"}
          {seenOutside > 0 ? `, ${seenOutside} of them outside this stretch` : ""}
          {frame.verdict ? `; it said ${frame.verdict.match ? `match, ${frame.verdict.confidence.toFixed(2)}` : `none, ${frame.verdict.confidence.toFixed(2)}`}` : ""}
        </span>
      </button>
      {showSeen && (
        <ol className="cands__strip cands__strip--seen">
          {frame.candidates.map((c, k) => card(c, k, frame, busy, onPick, onReject, !(frame.t_lo <= c.time && c.time <= frame.t_hi)))}
        </ol>
      )}
    </div>
  );
}

function card(c: Candidate, k: number, frame: Frame, busy: boolean, onPick: (u: string) => void, onReject: (u: string) => void, outside = false) {
  const chosen = frame.anchor_uuid === c.uuid;
  const cls = ["cand", chosen ? "is-chosen" : "", c.verdict === "match" ? "is-match" : "", c.rejected ? "is-rejected" : "", outside ? "is-outside" : ""].join(" ");
  return (
    <li key={c.uuid} className={cls}>
      <img src={`${c.image}?size=small`} alt="" loading="lazy" />
      <div className="cand__meta">
        <span className="mono">
          <b>{k + 1}</b> · {c.score.toFixed(3)}
        </span>
        <span className="mono">
          {fmtDate(c.time, c.tzoffset).slice(0, 10)} {fmtClock(c.time, c.tzoffset)}
        </span>
        <span className="muted mono">{fmtDelta(frame.time, c.time)}</span>
        <span className="cand__flags">
          {chosen && <span className="badge badge--anchor">chosen</span>}
          {c.verdict === "match" && !chosen && <span className="badge">Claude's pick</span>}
          {c.verdict === "no" && <span className="badge badge--dim">Claude: no</span>}
          {outside && <span className="badge badge--dim">outside this stretch</span>}
          {c.rejected && <span className="badge badge--warn">rejected</span>}
        </span>
      </div>
      <div className="cand__actions">
        <button className="btn btn--use" disabled={busy || chosen} onClick={() => onPick(c.uuid)} title={`${k < 9 && !outside ? `${k + 1} — ` : ""}lock this frame to this photo's time and GPS; neighbours re-solve`}>
          {chosen ? "in use" : "use this photo"}
        </button>
        {(chosen || c.verdict === "match") && !c.rejected && (
          <button className="btn btn--ghost btn--use" disabled={busy} onClick={() => onReject(c.uuid)} title="not a match: the solver will not anchor this frame to this photo">
            not a match
          </button>
        )}
      </div>
    </li>
  );
}
