import { useEffect, useState } from "react";
import type { Frame } from "../api";
import { useAssign } from "../api";
import { fmtOffset, fromDatetimeLocal, toDatetimeLocal } from "../format";

const OFFSETS = [-43200, -39600, -36000, -32400, -28800, -25200, -21600, -18000, -14400, -10800, -7200, -3600, 0, 3600, 7200, 10800, 14400, 19800, 28800, 32400, 36000, 39600, 43200];

/** The assigned local time and offset, editable. Committing sends a frame fact (`when`), which locks the frame. */
export function TimeEditor({ rollKey, frame }: { rollKey: string; frame: Frame }) {
  const assign = useAssign(rollKey);
  const offset0 = frame.tzoffset ?? 0;
  const [value, setValue] = useState(toDatetimeLocal(frame.time, offset0));
  const [offset, setOffset] = useState(offset0);
  useEffect(() => {
    setValue(toDatetimeLocal(frame.time, frame.tzoffset ?? 0));
    setOffset(frame.tzoffset ?? 0);
  }, [frame.number, frame.time, frame.tzoffset]);
  const dirty = value !== toDatetimeLocal(frame.time, offset0) || offset !== offset0;
  const offsets = OFFSETS.includes(offset0) ? OFFSETS : [...OFFSETS, offset0].sort((a, b) => a - b);

  return (
    <form
      className="time"
      onSubmit={(e) => {
        e.preventDefault();
        if (dirty) assign.mutate({ number: frame.number, body: { when: fromDatetimeLocal(value, offset) } });
      }}
    >
      <label className="time__field">
        <span className="eyebrow">local time</span>
        <input type="datetime-local" value={value} step={60} onChange={(e) => setValue(e.target.value)} />
      </label>
      <label className="time__field">
        <span className="eyebrow">offset</span>
        <select value={offset} onChange={(e) => setOffset(Number(e.target.value))}>
          {offsets.map((o) => (
            <option key={o} value={o}>
              UTC{fmtOffset(o)}
            </option>
          ))}
        </select>
      </label>
      <div className="time__actions">
        <button className="btn" type="submit" disabled={!dirty || assign.isPending}>
          {assign.isPending ? "solving…" : "set time"}
        </button>
        {frame.locked && (
          <button className="btn btn--ghost" type="button" disabled={assign.isPending} onClick={() => assign.mutate({ number: frame.number, body: { unlock: true } })} title="drop your decision and facts for this frame">
            unlock
          </button>
        )}
        {frame.fact?.when && <span className="muted mono">fact: {frame.fact.when}</span>}
        {assign.error && <span className="error">{(assign.error as Error).message}</span>}
      </div>
    </form>
  );
}
