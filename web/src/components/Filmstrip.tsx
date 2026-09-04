import { useEffect, useRef } from "react";
import type { Frame } from "../api";
import { confidenceBand, fmtShort } from "../format";
import { Badges } from "./Badges";

export function Filmstrip({ frames, selected, onSelect }: { frames: Frame[]; selected: number | null; onSelect: (n: number) => void }) {
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => {
    ref.current?.querySelector<HTMLElement>(`[data-frame="${selected}"]`)?.scrollIntoView({ block: "nearest", inline: "nearest", behavior: "smooth" });
  }, [selected]);
  return (
    <div className="filmstrip" ref={ref} role="listbox" aria-label="frames in scan order">
      {frames.map((f) => {
        const band = confidenceBand(f.confidence, f.locked);
        return (
          <button
            key={f.number}
            data-frame={f.number}
            role="option"
            aria-selected={f.number === selected}
            className={`sprocket ${f.number === selected ? "is-selected" : ""} band-${band}`}
            onClick={() => onSelect(f.number)}
            title={`frame ${f.number} · ${f.source} · ${fmtShort(f.time, f.tzoffset)}`}
          >
            <span className="sprocket__num mono">{String(f.number).padStart(2, "0")}</span>
            <img src={`${f.image}?size=small`} alt="" loading="lazy" />
            <span className="sprocket__time mono">{fmtShort(f.time, f.tzoffset)}</span>
            <span className="conf" aria-label={`confidence ${f.confidence.toFixed(2)}`}>
              <i style={{ width: `${Math.round(100 * f.confidence)}%` }} />
            </span>
            <Badges frame={f} compact />
          </button>
        );
      })}
    </div>
  );
}
