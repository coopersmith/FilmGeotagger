import { useEffect, useMemo, useState } from "react";
import { useFrames, useRealign, useRoll } from "../api";
import { fmtDate } from "../format";
import { Filmstrip } from "./Filmstrip";
import { FrameDetail } from "./FrameDetail";

export function RollPage({ rollKey, onBack }: { rollKey: string; onBack: () => void }) {
  const roll = useRoll(rollKey);
  const frames = useFrames(rollKey);
  const realign = useRealign(rollKey);
  const [selected, setSelected] = useState<number | null>(null);
  const list = frames.data ?? [];
  const current = useMemo(() => list.find((f) => f.number === selected) ?? list[0] ?? null, [list, selected]);
  useEffect(() => {
    if (current && selected !== current.number) setSelected(current.number);
  }, [current, selected]);

  const r = roll.data;
  const loading = roll.isLoading || frames.isLoading;
  const error = roll.error ?? frames.error;

  return (
    <div className="page">
      <header className="roll-head">
        <button className="link" onClick={onBack}>
          ← rolls
        </button>
        <h1 className="roll-head__title">
          <span className="brand brand--small">filmgeo</span> {rollKey}
        </h1>
        {r && (
          <div className="roll-head__facts">
            <span className="mono">
              {fmtDate(r.window.start)} → {fmtDate(r.window.end)}
            </span>
            <span className="muted">({r.window.source})</span>
            {r.facts.camera && <span className="pill">{r.facts.camera}</span>}
            {r.facts.film && <span className="pill">{r.facts.film}</span>}
            <span className="muted">
              {r.n_frames} frames · {r.anchored} anchored · {r.verified_frames} verified · {r.confirmed} confirmed · {r.pool} photos in {r.events.length} events
            </span>
            {r.window_check.doubtful && (
              <span className="pill pill--warn" title={r.window_check.reason}>
                window doubtful
              </span>
            )}
            {r.reverse.suspect && <span className="pill pill--warn">possibly reverse-wound</span>}
            <button className="btn btn--ghost" disabled={realign.isPending} onClick={() => realign.mutate(false)} title="re-read verdicts and facts from disk and solve again">
              {realign.isPending ? "solving…" : "re-solve"}
            </button>
          </div>
        )}
      </header>
      {loading && <p className="muted pad">Building this roll — library, vectors, solve…</p>}
      {error && <p className="error pad">{String((error as Error).message ?? error)}</p>}
      {list.length > 0 && <Filmstrip frames={list} selected={current?.number ?? null} onSelect={setSelected} />}
      {current && r && <FrameDetail rollKey={rollKey} frame={current} frames={list} roll={r} />}
    </div>
  );
}
