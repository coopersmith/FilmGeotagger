import { useRolls } from "../api";

export function RollList({ onOpen }: { onOpen: (key: string) => void }) {
  const rolls = useRolls();
  const rows = rolls.data ?? [];
  const aligned = rows.filter((r) => r.aligned);
  const rest = rows.filter((r) => !r.aligned);
  return (
    <main className="rolls">
      <header className="rolls__head">
        <h1 className="brand">
          filmgeo <span className="brand__sub">light table</span>
        </h1>
        <p className="lede">
          Each roll aligned against the phone timeline. Pick one to review what the solver proposed, frame by frame.
        </p>
      </header>
      {rolls.isLoading && <p className="muted">Reading the library…</p>}
      {rolls.error && <p className="error">{String(rolls.error)}</p>}
      <section>
        <h2 className="section-title">Aligned</h2>
        <ul className="roll-grid">
          {aligned.map((r) => (
            <li key={r.key}>
              <button className="roll-card" onClick={() => onOpen(r.key)}>
                <span className="roll-card__key">{r.key}</span>
                <span className="roll-card__meta">
                  {r.n_frames} frames · {r.anchored} anchored · {r.confirmed ?? 0} confirmed
                </span>
                <span className="roll-card__meta muted">
                  {r.window ? `${r.window.start.slice(0, 10)} → ${r.window.end.slice(0, 10)}` : ""}
                  {r.facts.camera ? ` · ${r.facts.camera}` : ""}
                </span>
                {r.doubtful && <span className="pill pill--warn">window doubtful</span>}
              </button>
            </li>
          ))}
          {!rolls.isLoading && aligned.length === 0 && <li className="muted">Nothing aligned yet — run `filmgeo align &lt;roll&gt;` first.</li>}
        </ul>
      </section>
      {rest.length > 0 && (
        <section>
          <h2 className="section-title">Not yet aligned</h2>
          <ul className="roll-grid roll-grid--dense">
            {rest.map((r) => (
              <li key={r.key}>
                <button className="roll-card roll-card--dim" onClick={() => onOpen(r.key)}>
                  <span className="roll-card__key">{r.key}</span>
                  <span className="roll-card__meta muted">{r.facts.window_from ? `${r.facts.window_from} → ${r.facts.window_to}` : "no window yet"}</span>
                </button>
              </li>
            ))}
          </ul>
        </section>
      )}
    </main>
  );
}
