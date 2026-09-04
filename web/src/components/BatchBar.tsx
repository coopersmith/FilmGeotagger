import type { Frame } from "../api";
import { useConfirm } from "../api";

interface Props {
  rollKey: string;
  frames: Frame[];
  range: [number, number] | null;
  onClearRange: () => void;
}

/** Confirm in bulk: everything at or above 0.8, the whole roll, or the shift-selected range; and undo any of it. */
export function BatchBar({ rollKey, frames, range, onClearRange }: Props) {
  const confirm = useConfirm(rollKey);
  const busy = confirm.isPending;
  const inRange = range ? frames.filter((f) => f.number >= range[0] && f.number <= range[1]) : [];
  const rangeNumbers = inRange.map((f) => f.number);
  const confirmed = frames.filter((f) => f.status === "confirmed").length;
  const high = frames.filter((f) => f.confidence >= 0.8 && f.source !== "skipped" && f.status !== "confirmed").length;
  const eligible = frames.filter((f) => f.source !== "skipped").length;
  return (
    <div className="batch">
      <span className="eyebrow">Confirm</span>
      <span className="muted">
        {confirmed} of {eligible} confirmed{range ? ` · frames ${range[0]}–${range[1]} selected` : " · shift-click the filmstrip to select a range"}
      </span>
      <button className="btn btn--ghost" disabled={busy || high === 0} onClick={() => confirm.mutate({ confirmed: true, min_confidence: 0.8 })} title="every frame whose confidence is 0.8 or more">
        all ≥ 0.8 {high > 0 && <span className="muted">({high})</span>}
      </button>
      <button className="btn btn--ghost" disabled={busy || confirmed === eligible} onClick={() => confirm.mutate({ confirmed: true })} title="every frame that is not skipped">
        whole roll
      </button>
      {range && (
        <>
          <button className="btn" disabled={busy} onClick={() => confirm.mutate({ confirmed: true, frames: rangeNumbers })}>
            frames {range[0]}–{range[1]}
          </button>
          <button className="btn btn--ghost" disabled={busy} onClick={() => confirm.mutate({ confirmed: false, frames: rangeNumbers })}>
            unconfirm {range[0]}–{range[1]}
          </button>
          <button className="link" onClick={onClearRange}>
            clear
          </button>
        </>
      )}
      <button className="btn btn--ghost" disabled={busy || confirmed === 0} onClick={() => confirm.mutate({ confirmed: false })} title="drop every confirmation on this roll">
        unconfirm all
      </button>
      {busy && <span className="muted">saving…</span>}
      {confirm.error && <span className="error">{(confirm.error as Error).message}</span>}
    </div>
  );
}
