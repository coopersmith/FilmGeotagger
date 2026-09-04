import { useEffect, useState } from "react";

export const KEYS: [string, string][] = [
  ["j / k  or  → / ←", "next / previous frame"],
  ["1 – 9", "use possible photo 1–9's time and GPS"],
  ["Enter", "confirm this frame (again to unconfirm)"],
  ["n", "not a match: reject the chosen photo"],
  ["N", "no reference: no phone photo shows this frame"],
  ["x", "unknown: skip this frame"],
  ["u", "unlock: drop your decisions for this frame"],
  ["?", "this list"],
];

/** The keyboard map, as a sheet toggled with `?`. */
export function Keys() {
  const [open, setOpen] = useState(false);
  useEffect(() => {
    const on = (e: KeyboardEvent) => {
      if (e.key === "?" && !isTyping(e)) setOpen((o) => !o);
      if (e.key === "Escape") setOpen(false);
    };
    addEventListener("keydown", on);
    return () => removeEventListener("keydown", on);
  }, []);
  return (
    <>
      <button className="keys__toggle mono" onClick={() => setOpen((o) => !o)} title="keyboard shortcuts">
        ?
      </button>
      {open && (
        <div className="keys" onClick={() => setOpen(false)}>
          <div className="keys__sheet" onClick={(e) => e.stopPropagation()}>
            <span className="eyebrow">Keys</span>
            <dl>
              {KEYS.map(([k, what]) => (
                <div key={k}>
                  <dt className="mono">{k}</dt>
                  <dd>{what}</dd>
                </div>
              ))}
            </dl>
          </div>
        </div>
      )}
    </>
  );
}

export function isTyping(e: KeyboardEvent): boolean {
  const t = e.target as HTMLElement | null;
  return !!t && (t.tagName === "INPUT" || t.tagName === "SELECT" || t.tagName === "TEXTAREA" || t.isContentEditable);
}
