import type { Frame, Roll } from "../api";
import { useConfirm } from "../api";
import { plan } from "../review";

interface Props {
  rollKey: string;
  roll: Roll;
  frames: Frame[];
  range: [number, number] | null;
  onClearRange: () => void;
  onGo: (n: number) => void;
  onOpenFacts: () => void;
}

/** The roll's condition in one sentence, the one thing to do next, and the bulk confirms. */
export function NextStrip({ rollKey, roll, frames, range, onClearRange, onGo, onOpenFacts }: Props) {
  const confirm = useConfirm(rollKey);
  const p = plan(roll, frames);
  const busy = confirm.isPending;
  const inRange = range ? frames.filter((f) => f.number >= range[0] && f.number <= range[1]).map((f) => f.number) : [];
  return (
    <div className={`next next--${p.action}`}>
      <div className="next__text">
        <span className="next__headline">{p.headline}</span>
        <span className="next__detail">{p.detail}</span>
      </div>
      <div className="next__actions">
        {p.action === "confirm-greens" && (
          <button className="btn" disabled={busy} onClick={() => confirm.mutate({ confirmed: true, frames: p.greensToConfirm.map((f) => f.number) })} title="every frame with a green bar that is not confirmed yet">
            confirm {p.greensToConfirm.length} green
          </button>
        )}
        {p.action === "facts" && (
          <button className="btn" onClick={onOpenFacts}>
            roll facts
          </button>
        )}
        {p.next && (
          <button className="btn btn--ghost" onClick={() => onGo(p.next!.number)} title=". — jump to the next frame that needs you">
            next: frame {String(p.next.number).padStart(2, "0")}
          </button>
        )}
        {range && (
          <>
            <button className="btn btn--ghost" disabled={busy} onClick={() => confirm.mutate({ confirmed: true, frames: inRange })}>
              confirm {range[0]}–{range[1]}
            </button>
            <button className="btn btn--ghost" disabled={busy} onClick={() => confirm.mutate({ confirmed: false, frames: inRange })}>
              unconfirm {range[0]}–{range[1]}
            </button>
            <button className="link" onClick={onClearRange}>
              clear
            </button>
          </>
        )}
        <details className="next__more">
          <summary className="link">more</summary>
          <div className="next__menu">
            <button className="link" disabled={busy || p.unresolved.length === 0} onClick={() => confirm.mutate({ confirmed: true })}>
              confirm the whole roll
            </button>
            <button className="link" disabled={busy || p.confirmed === 0} onClick={() => confirm.mutate({ confirmed: false })}>
              unconfirm everything
            </button>
            <span className="muted">shift-click two frames on the strip to work on a range</span>
          </div>
        </details>
        {busy && <span className="muted">saving…</span>}
        {confirm.error && <span className="error">{(confirm.error as Error).message}</span>}
      </div>
    </div>
  );
}
