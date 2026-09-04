import { useEffect, useMemo, useState } from "react";
import { useAssign, useFrames, useRealign, useRoll } from "../api";
import { fmtDate } from "../format";
import { Filmstrip } from "./Filmstrip";
import { FrameDetail } from "./FrameDetail";
import { isTyping, Keys } from "./Keys";

export function RollPage({ rollKey, onBack }: { rollKey: string; onBack: () => void }) {
  const roll = useRoll(rollKey);
  const frames = useFrames(rollKey);
  const realign = useRealign(rollKey);
  const assign = useAssign(rollKey);
  const [selected, setSelected] = useState<number | null>(null);
  const list = frames.data ?? [];
  const current = useMemo(() => list.find((f) => f.number === selected) ?? list[0] ?? null, [list, selected]);
  useEffect(() => {
    if (current && selected !== current.number) setSelected(current.number);
  }, [current, selected]);

  // Keyboard: j/k move, 1-9 pick a candidate, Enter confirm, n not-a-match, N no-reference,
  // x unknown, u unlock. Every override locks the frame and re-solves the roll.
  useEffect(() => {
    const on = (e: KeyboardEvent) => {
      if (!current || isTyping(e) || e.metaKey || e.ctrlKey || e.altKey) return;
      const i = list.findIndex((f) => f.number === current.number);
      const act = (body: Parameters<typeof assign.mutate>[0]["body"]) => {
        if (!assign.isPending) assign.mutate({ number: current.number, body });
      };
      switch (e.key) {
        case "j":
        case "ArrowRight":
          if (i < list.length - 1) setSelected(list[i + 1].number);
          break;
        case "k":
        case "ArrowLeft":
          if (i > 0) setSelected(list[i - 1].number);
          break;
        case "Enter":
          act({ confirmed: current.status !== "confirmed" });
          break;
        case "n":
          if (current.anchor_uuid) act({ reject: [current.anchor_uuid] });
          break;
        case "N":
          act({ no_reference: !current.override?.no_reference });
          break;
        case "x":
          act({ skip: current.source !== "skipped" });
          break;
        case "u":
          if (current.locked || current.override || current.fact) act({ unlock: true });
          break;
        default: {
          const d = Number(e.key);
          if (d >= 1 && d <= 9) {
            const c = current.candidates[d - 1];
            if (c && c.uuid !== current.anchor_uuid) act({ anchor: c.uuid });
          } else return;
        }
      }
      e.preventDefault();
    };
    addEventListener("keydown", on);
    return () => removeEventListener("keydown", on);
  }, [current, list, assign]);

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
      {current && r && <FrameDetail rollKey={rollKey} frame={current} frames={list} roll={r} onSelect={setSelected} />}
      <Keys />
    </div>
  );
}
