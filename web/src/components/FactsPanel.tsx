import { useEffect, useState } from "react";
import type { Roll } from "../api";
import { useRealign, useSaveFacts } from "../api";

const CAMERAS = ["Contax T2", "Leica M7", "Mamiya 7II"];

/** What the user knows about the roll: the window, camera, film, lab, notes, reverse. Saved to the facts file; re-solves. */
export function FactsPanel({ rollKey, roll, onClose }: { rollKey: string; roll: Roll; onClose: () => void }) {
  const save = useSaveFacts(rollKey);
  const realign = useRealign(rollKey);
  const f = roll.facts;
  const [form, setForm] = useState({
    window_from: f.window_from ?? "",
    window_to: f.window_to ?? "",
    tz: f.tz ?? "",
    camera: f.camera ?? "",
    film: f.film ?? "",
    lab: f.lab ?? "",
    notes: f.notes ?? "",
    reverse: f.reverse,
  });
  useEffect(() => {
    setForm({ window_from: f.window_from ?? "", window_to: f.window_to ?? "", tz: f.tz ?? "", camera: f.camera ?? "", film: f.film ?? "", lab: f.lab ?? "", notes: f.notes ?? "", reverse: f.reverse });
  }, [f]);
  const set = (k: keyof typeof form, v: string | boolean) => setForm((s) => ({ ...s, [k]: v }));
  const windowMoved = form.window_from !== (f.window_from ?? "") || form.window_to !== (f.window_to ?? "") || form.tz !== (f.tz ?? "");
  const dirty = windowMoved || form.camera !== (f.camera ?? "") || form.film !== (f.film ?? "") || form.lab !== (f.lab ?? "") || form.notes !== (f.notes ?? "") || form.reverse !== f.reverse;
  const busy = save.isPending || realign.isPending;
  const submit = () => {
    const nz = (s: string) => (s.trim() === "" ? null : s.trim());
    save.mutate({
      window_from: nz(form.window_from),
      window_to: nz(form.window_to),
      tz: nz(form.tz),
      camera: nz(form.camera),
      film: nz(form.film),
      lab: nz(form.lab),
      notes: nz(form.notes),
      reverse: form.reverse,
      frames: f.frames,
    });
  };
  const result = save.data;

  return (
    <form
      className="facts"
      onSubmit={(e) => {
        e.preventDefault();
        if (dirty) submit();
      }}
    >
      <div className="facts__head">
        <span className="eyebrow">Roll facts</span>
        <span className="muted">what you know: the cheapest evidence there is. The window is a period — 2026-04, 2026-04-12, or "2026-04-12 14:05" — in the roll's zone.</span>
        <button type="button" className="link" onClick={onClose}>
          close
        </button>
      </div>
      <div className="facts__grid">
        <label>
          <span className="eyebrow">from</span>
          <input value={form.window_from} placeholder="2026-04" onChange={(e) => set("window_from", e.target.value)} />
        </label>
        <label>
          <span className="eyebrow">to</span>
          <input value={form.window_to} placeholder="2026-04" onChange={(e) => set("window_to", e.target.value)} />
        </label>
        <label>
          <span className="eyebrow">zone</span>
          <input value={form.tz} placeholder="this Mac's (e.g. Europe/Lisbon)" onChange={(e) => set("tz", e.target.value)} />
        </label>
        <label>
          <span className="eyebrow">camera</span>
          <input list="cameras" value={form.camera} placeholder="Mamiya 7II" onChange={(e) => set("camera", e.target.value)} />
          <datalist id="cameras">
            {CAMERAS.map((c) => (
              <option key={c} value={c} />
            ))}
          </datalist>
        </label>
        <label>
          <span className="eyebrow">film</span>
          <input value={form.film} placeholder="Kodak Portra 400" onChange={(e) => set("film", e.target.value)} />
        </label>
        <label>
          <span className="eyebrow">lab</span>
          <input value={form.lab} placeholder="Richard Photo Lab" onChange={(e) => set("lab", e.target.value)} />
        </label>
        <label className="facts__notes">
          <span className="eyebrow">notes</span>
          <textarea rows={2} value={form.notes} placeholder="the Portugal trip; frames 1–8 are the wedding" onChange={(e) => set("notes", e.target.value)} />
        </label>
        <label className="facts__check">
          <input type="checkbox" checked={form.reverse} onChange={(e) => set("reverse", e.target.checked)} />
          <span>scanned in reverse order</span>
        </label>
      </div>
      <div className="facts__actions">
        <button className="btn" type="submit" disabled={!dirty || busy}>
          {save.isPending ? (windowMoved ? "rebuilding the pool…" : "solving…") : windowMoved ? "save and rebuild" : "save"}
        </button>
        <button className="btn btn--ghost" type="button" disabled={busy} onClick={() => realign.mutate(true)} title="a month more on each side, written into the window, the pool rebuilt; then `filmgeo verify --widen --only-new` verifies what it surfaced">
          {realign.isPending ? "widening…" : "widen ±1 month and re-run"}
        </button>
        {save.error && <span className="error">{(save.error as Error).message}</span>}
        {realign.error && <span className="error">{(realign.error as Error).message}</span>}
        {result && !result.solved && <span className="error">saved, but not solved: {result.error}</span>}
        {result && result.solved && !save.isPending && <span className="muted">saved</span>}
      </div>
      <div className="facts__status">
        {roll.window_check.doubtful ? (
          <span className="pill pill--warn" title={roll.window_check.reason}>
            window doubtful — {roll.window_check.reason}
          </span>
        ) : (
          <span className="muted">window check: {roll.window_check.reason}</span>
        )}
        <span className="muted">
          best days by posterior mass: {roll.window_check.best_days.map(([d, m]) => `${d.slice(5)} (${m.toFixed(1)})`).join(", ")}
        </span>
        <span className="muted" title={`${roll.cost.verified_frames} frames × K ${roll.cost.k}${roll.cost.outing_usd ? " + outing pass" : ""}${roll.cost.model ? ` on ${roll.cost.model}` : ""}`}>
          Claude so far ≈ ${roll.cost.usd.toFixed(2)}
        </span>
      </div>
    </form>
  );
}
