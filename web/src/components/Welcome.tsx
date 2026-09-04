import { useEffect, useState } from "react";
import type { Frame, Roll } from "../api";
import { STEPS, stepsDone } from "../review";

const KEY = "filmgeo.welcomed";

/** The five steps, with the roll's own progress ticked. Shown until dismissed; back via "how it works". */
export function Welcome({ roll, frames, open, onClose }: { roll: Roll; frames: Frame[]; open: boolean; onClose: () => void }) {
  if (!open) return null;
  const done = stepsDone(roll, frames);
  return (
    <aside className="welcome">
      <div className="welcome__head">
        <span className="eyebrow">How this works</span>
        <span className="muted">Every roll goes through the same five steps. Ticks are this roll's progress.</span>
        <button className="link" onClick={onClose}>
          got it
        </button>
      </div>
      <ol className="welcome__steps">
        {STEPS.map((s, i) => (
          <li key={s.key} className={done[s.key] ? "is-done" : ""}>
            <span className="welcome__num mono">{done[s.key] ? "✓" : i + 1}</span>
            <span className="welcome__title">{s.title}</span>
            <span className="welcome__what muted">{s.what}</span>
          </li>
        ))}
      </ol>
    </aside>
  );
}

export function useWelcomed(): [boolean, (v: boolean) => void] {
  const [open, setOpen] = useState(() => {
    try {
      return localStorage.getItem(KEY) !== "1";
    } catch {
      return true;
    }
  });
  useEffect(() => {
    try {
      localStorage.setItem(KEY, open ? "0" : "1");
    } catch {
      /* no storage */
    }
  }, [open]);
  return [open, setOpen];
}
