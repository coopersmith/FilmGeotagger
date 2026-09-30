import { useEffect, useState } from "react";
import type { Frame } from "../api";
import { useAssign } from "../api";

/** Per-frame facts beyond the time editor: a known period, a place name, "same day as frame N", a note. */
export function FrameFacts({ rollKey, frame, nFrames }: { rollKey: string; frame: Frame; nFrames: number }) {
  const assign = useAssign(rollKey);
  const ff = frame.fact;
  const initial = () => ({ when: ff?.when ?? "", place_name: ff?.place_name ?? "", same_day_as: ff?.same_day_as ? String(ff.same_day_as) : "", same_time_as: ff?.same_time_as ? String(ff.same_time_as) : "", note: ff?.note ?? "" });
  const [form, setForm] = useState(initial);
  useEffect(() => {
    setForm(initial());
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [frame.number, ff]);
  const was = initial();
  const dirty = form.when !== was.when || form.place_name !== was.place_name || form.same_day_as !== was.same_day_as || form.same_time_as !== was.same_time_as || form.note !== was.note;
  const others = Array.from({ length: nFrames }, (_, i) => i + 1).filter((n) => n !== frame.number);

  return (
    <form
      className="ffacts"
      onSubmit={(e) => {
        e.preventDefault();
        if (!dirty) return;
        const body: Parameters<typeof assign.mutate>[0]["body"] = {};
        if (form.when !== (ff?.when ?? "") && form.when.trim()) body.when = form.when.trim();
        if (form.place_name !== (ff?.place_name ?? "")) body.place_name = form.place_name;
        if (form.same_day_as !== was.same_day_as && form.same_day_as) body.same_day_as = Number(form.same_day_as);
        if (form.same_time_as !== was.same_time_as && form.same_time_as) body.same_time_as = Number(form.same_time_as);
        if (form.note !== (ff?.note ?? "")) body.note = form.note;
        assign.mutate({ number: frame.number, body });
      }}
    >
      <div className="ffacts__head">
        <span className="eyebrow">Frame facts</span>
        <span className="muted">a day ("2026-04-12"), a month, or a minute; a place name for the pin; the frame it shares a day, or a moment, with</span>
      </div>
      <div className="ffacts__grid">
        <label>
          <span className="eyebrow">known period</span>
          <input value={form.when} placeholder="2026-04-12" onChange={(e) => setForm((s) => ({ ...s, when: e.target.value }))} />
        </label>
        <label>
          <span className="eyebrow">place name</span>
          <input value={form.place_name} placeholder="Montague Street" onChange={(e) => setForm((s) => ({ ...s, place_name: e.target.value }))} />
        </label>
        <label>
          <span className="eyebrow">same day as</span>
          <select value={form.same_day_as} onChange={(e) => setForm((s) => ({ ...s, same_day_as: e.target.value }))}>
            <option value="">—</option>
            {others.map((n) => (
              <option key={n} value={n}>
                frame {n}
              </option>
            ))}
          </select>
        </label>
        <label>
          <span className="eyebrow">same moment as</span>
          <select value={form.same_time_as} onChange={(e) => setForm((s) => ({ ...s, same_time_as: e.target.value }))}>
            <option value="">—</option>
            {others.map((n) => (
              <option key={n} value={n}>
                frame {n} ({n < frame.number ? "just after it" : "just before it"})
              </option>
            ))}
          </select>
        </label>
        <label className="ffacts__note">
          <span className="eyebrow">note</span>
          <input value={form.note} placeholder="the wedding" onChange={(e) => setForm((s) => ({ ...s, note: e.target.value }))} />
        </label>
      </div>
      <div className="ffacts__actions">
        <button className="btn" type="submit" disabled={!dirty || assign.isPending}>
          {assign.isPending ? "solving…" : "save facts"}
        </button>
        {ff?.lat != null && (
          <span className="muted mono">
            pin {ff.lat.toFixed(4)}, {ff.lon!.toFixed(4)}
            {ff.radius_m ? ` ±${Math.round(ff.radius_m)} m` : ""}
          </span>
        )}
        {ff?.skip && <span className="muted">skipped</span>}
        {assign.error && <span className="error">{(assign.error as Error).message}</span>}
      </div>
    </form>
  );
}
