import { useState } from "react";
import type { Frame, Roll } from "../api";
import { useRestore, useWrite, useWritePlan } from "../api";
import { fmtShort } from "../format";

interface Props {
  rollKey: string;
  roll: Roll;
  frames: Frame[];
  onClose: () => void;
}

/** What will go into the files, the one button that does it, what came back, and the way back. */
export function WritePanel({ rollKey, roll, frames, onClose }: Props) {
  const [force, setForce] = useState(false);
  const [leaveRest, setLeaveRest] = useState(false);
  const [confirmRestore, setConfirmRestore] = useState(false);
  const plan = useWritePlan(rollKey, force, roll.writable);
  const write = useWrite(rollKey);
  const restore = useRestore(rollKey);
  const byNumber = new Map(frames.map((f) => [f.number, f]));
  const p = plan.data;
  const notConfirmed = p?.skipped.filter((s) => s.why === "not confirmed") ?? [];
  const unchanged = p?.skipped.filter((s) => s.why === "unchanged") ?? [];
  const canWrite = !!p && p.frames.length > 0 && (notConfirmed.length === 0 || leaveRest);
  const result = write.data;

  if (!roll.writable) {
    return (
      <section className="writep">
        <div className="writep__head">
          <span className="eyebrow">Write</span>
          <span className="muted">This roll was aligned from the Photos library, not a scan folder, so there are no files to write. Open the roll from its scan folder: `filmgeo serve ~/scans/&lt;roll&gt;`.</span>
          <button className="link" onClick={onClose}>
            close
          </button>
        </div>
      </section>
    );
  }

  return (
    <section className="writep">
      <div className="writep__head">
        <span className="eyebrow">Write</span>
        <span className="muted">
          into {p?.folder ?? "…"}
          {roll.written ? ` · last written ${roll.written.at?.slice(0, 16).replace("T", " ")}, ${roll.written.frames} frames` : " · never written"}
        </span>
        <button className="link" onClick={onClose}>
          close
        </button>
      </div>

      {plan.isLoading && <p className="muted">reading the files…</p>}
      {plan.error && <p className="error">{(plan.error as Error).message}</p>}

      {p && (
        <>
          <table className="wtable">
            <thead>
              <tr>
                <th></th>
                <th>#</th>
                <th>file</th>
                <th>in the file now</th>
                <th>new local time</th>
                <th>offset</th>
                <th>GPS</th>
                <th>provenance</th>
                <th></th>
              </tr>
            </thead>
            <tbody>
              {[...p.frames.map((f) => ({ kind: "write" as const, n: f.number, f })), ...p.skipped.map((s) => ({ kind: "skip" as const, n: s.number, s }))]
                .sort((a, b) => a.n - b.n)
                .map((row) => {
                  const fr = byNumber.get(row.n);
                  return (
                    <tr key={row.n} className={row.kind === "write" ? "is-write" : "is-skip"}>
                      <td>{fr && <img src={`${fr.image}?size=small`} alt="" />}</td>
                      <td className="mono">{String(row.n).padStart(2, "0")}</td>
                      {row.kind === "write" ? (
                        <>
                          <td className="mono">{row.f.file}</td>
                          <td className="mono muted">{row.f.current ?? "—"}</td>
                          <td className="mono">{row.f.local}</td>
                          <td className="mono">{row.f.offset}</td>
                          <td className="mono">{row.f.lat != null ? `${row.f.lat.toFixed(5)}, ${row.f.lon!.toFixed(5)}` : <span className="muted">none</span>}</td>
                          <td className="mono">{row.f.provenance.map((k) => k.replace("filmgeo:", "")).join(" ")}</td>
                          <td className="ok">write</td>
                        </>
                      ) : (
                        <>
                          <td className="mono muted">{row.s.file ?? "?"}</td>
                          <td colSpan={5} className="muted">{fr?.written ? `${fr.written.local} ${fr.written.offset}` : ""}</td>
                          <td className="muted">{row.s.why}</td>
                        </>
                      )}
                    </tr>
                  );
                })}
            </tbody>
          </table>

          <div className="writep__actions">
            {notConfirmed.length > 0 && (
              <label className="writep__toggle">
                <input type="checkbox" checked={leaveRest} onChange={(e) => setLeaveRest(e.target.checked)} />
                <span>
                  {notConfirmed.length} frame{notConfirmed.length > 1 ? "s are" : " is"} not confirmed. Write the confirmed ones now and leave {notConfirmed.length > 1 ? "them" : "it"} for later.
                </span>
              </label>
            )}
            {unchanged.length > 0 && (
              <label className="writep__toggle">
                <input type="checkbox" checked={force} onChange={(e) => setForce(e.target.checked)} />
                <span>{unchanged.length} already written as they stand. Write them again anyway.</span>
              </label>
            )}
            <button className="btn btn--write" disabled={!canWrite || write.isPending} onClick={() => write.mutate(force)}>
              {write.isPending ? `writing ${p.frames.length} files…` : p.frames.length ? `Write ${p.frames.length} file${p.frames.length > 1 ? "s" : ""}` : "nothing to write"}
            </button>
            <span className="muted">Originals are copied to .filmgeo_backup/ first; exiftool keeps &lt;name&gt;_original too.</span>
            {write.error && <span className="error">{(write.error as Error).message}</span>}
          </div>
        </>
      )}

      {result && (
        <div className={`report ${result.ok ? "report--ok" : "report--bad"}`}>
          <div className="report__head">
            <span className="eyebrow">Read back</span>
            <span>
              {result.checks.filter((c) => c.ok).length} of {result.checks.length} verified
              {result.backed_up ? ` · ${result.backed_up} originals backed up` : ""}
              {result.sidecar ? " · sidecar written" : ""}
            </span>
          </div>
          <ul className="report__list">
            {result.checks.map((c) => (
              <li key={c.number} className={c.ok ? "is-ok" : "is-bad"}>
                <span className="mono">{String(c.number).padStart(2, "0")}</span> <span className="mono">{c.file}</span>{" "}
                {c.ok ? <span className="ok">ok</span> : <span className="error">{c.problems.join("; ")}</span>}
              </li>
            ))}
          </ul>
          {result.warnings.length > 0 && (
            <ul className="report__list">
              {result.warnings.map((w, i) => (
                <li key={i} className="is-bad">
                  exiftool: {w}
                </li>
              ))}
            </ul>
          )}
        </div>
      )}

      <div className="writep__restore">
        {!confirmRestore ? (
          <button className="link" disabled={!roll.written && !result} onClick={() => setConfirmRestore(true)}>
            Restore originals…
          </button>
        ) : (
          <>
            <span>Put every scan in this folder back as it was before filmgeo wrote anything?</span>
            <button className="btn btn--ghost" disabled={restore.isPending} onClick={() => restore.mutate(undefined, { onSettled: () => setConfirmRestore(false) })}>
              {restore.isPending ? "restoring…" : "yes, restore"}
            </button>
            <button className="link" onClick={() => setConfirmRestore(false)}>
              no
            </button>
          </>
        )}
        {restore.data && (
          <span className="muted">
            restored {restore.data.restored.filter((r) => r.how !== "nothing to restore").length} of {restore.data.restored.length}
            {restore.data.restored.some((r) => r.how === "backup") ? " from the backup folder" : ""}
          </span>
        )}
        {restore.error && <span className="error">{(restore.error as Error).message}</span>}
        {frames.some((f) => f.written) && <span className="muted"> · last written {fmtShort(frames.find((f) => f.written)!.written!.at ?? roll.window.start)}</span>}
      </div>
    </section>
  );
}
