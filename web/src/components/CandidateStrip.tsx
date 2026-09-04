import type { Frame } from "../api";
import { fmtClock, fmtDate, fmtDelta } from "../format";

/** The shortlist retrieval produced: similarity, whether Claude saw it, what it said. */
export function CandidateStrip({ frame, busy, error, onPick }: { frame: Frame; busy: boolean; error: string | null; onPick: (uuid: string) => void }) {
  return (
    <div className="cands">
      <div className="cands__head">
        <span className="eyebrow">Candidates</span>
        <span className="muted">
          {frame.candidates.length} by similarity, one per event{frame.verdict ? ` · ${frame.verdict.shown.length} shown to Claude` : ""}
        </span>
        {busy && <span className="muted">re-solving…</span>}
        {error && <span className="error">{error}</span>}
      </div>
      <ol className="cands__strip">
        {frame.candidates.map((c, k) => {
          const chosen = frame.anchor_uuid === c.uuid;
          const cls = ["cand", chosen ? "is-chosen" : "", c.verdict === "match" ? "is-match" : "", c.rejected ? "is-rejected" : "", c.shown ? "" : "is-unseen"].join(" ");
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
                  {c.verdict === "no" && <span className="badge badge--dim">seen · no</span>}
                  {!c.shown && <span className="badge badge--dim">not shown</span>}
                  {c.rejected && <span className="badge badge--warn">rejected</span>}
                </span>
              </div>
              <button className="btn btn--use" disabled={busy || chosen} onClick={() => onPick(c.uuid)} title="lock this frame to this photo's time and GPS; neighbours re-solve">
                {chosen ? "in use" : "Use this photo's time and GPS"}
              </button>
            </li>
          );
        })}
      </ol>
    </div>
  );
}
