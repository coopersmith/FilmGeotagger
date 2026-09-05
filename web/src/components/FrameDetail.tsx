import type { Frame, Roll } from "../api";
import { useAssign, useTrail } from "../api";
import { fmtClock, fmtDate, fmtOffset, intervalText } from "../format";
import { Badges } from "./Badges";
import { CandidateStrip } from "./CandidateStrip";
import { MapPane } from "./MapPane";
import { FrameFacts } from "./FrameFacts";
import { PhotoBrowser } from "./PhotoBrowser";
import { Question } from "./Question";
import { TimeEditor } from "./TimeEditor";
import { Timeline } from "./Timeline";
import { useState } from "react";

/** The selected frame beside the phone photo it was matched to, and everything known about it. */
export function FrameDetail({ rollKey, frame, frames, roll, onSelect }: { rollKey: string; frame: Frame; frames: Frame[]; roll: Roll; onSelect: (n: number) => void }) {
  const assign = useAssign(rollKey);
  const trail = useTrail(rollKey, frame.number, 30);
  const [browsing, setBrowsing] = useState<number | null>(null);
  const [timeOpen, setTimeOpen] = useState(false);
  const act = (body: Parameters<typeof assign.mutate>[0]["body"]) => assign.mutate({ number: frame.number, body });
  const browsingEvent = browsing === null ? null : (roll.events.find((e) => e.index === browsing) ?? null);
  const v = frame.verdict;
  const clues = v?.clues ?? {};
  const cluePairs = Object.entries(clues).filter(([, val]) => val !== null && val !== undefined && val !== "" && !(Array.isArray(val) && val.length === 0));
  const outing = frame.outing && roll.outings ? roll.outings.groups[frame.outing - 1] : null;
  const i = frames.findIndex((f) => f.number === frame.number);
  const prev = frames[i - 1];
  const next = frames[i + 1];

  return (
    <section className="detail">
      <div className="detail__pair">
        <figure className="plate plate--frame">
          <img src={`${frame.image}?size=large`} alt={`frame ${frame.number}`} />
          <figcaption>
            <span className="mono">frame {String(frame.number).padStart(2, "0")}</span>
            <Badges frame={frame} />
          </figcaption>
        </figure>
        <figure className={`plate plate--photo ${frame.anchor ? "" : "plate--empty"}`}>
          {frame.anchor ? (
            <>
              <img src={`${frame.anchor.image}?size=large`} alt="matched phone photo" />
              <figcaption>
                <span className="mono">
                  {fmtDate(frame.anchor.time, frame.anchor.tzoffset)} {fmtClock(frame.anchor.time, frame.anchor.tzoffset, true)} {fmtOffset(frame.anchor.tzoffset)}
                </span>
                <span className="muted">{frame.anchor.filename}</span>
              </figcaption>
            </>
          ) : (
            <div className="plate__void">
              <span>no phone photo chosen</span>
              <span className="muted">
                {frame.source === "skipped" ? "skipped by you" : "placed between its neighbours"}
                {prev && next ? ` — frames ${prev.number} and ${next.number}` : ""}
              </span>
            </div>
          )}
        </figure>
      </div>

      <div className="detail__facts">
        <Question frame={frame} frames={frames} busy={assign.isPending} act={(body) => act(body as Parameters<typeof act>[0])} onOpenTime={() => setTimeOpen(true)} />
        {assign.isPending && <span className="muted">re-solving…</span>}
        {assign.error && <span className="error">{(assign.error as Error).message}</span>}

        <details className="fold" open={timeOpen || !!frame.fact?.when || frame.offset_disputed} onToggle={(e) => setTimeOpen((e.target as HTMLDetailsElement).open)}>
          <summary>
            <span className="eyebrow">Time and offset</span>
            <span className="muted"> — edit by hand{frame.fact?.when ? ` · fact: ${frame.fact.when}` : ""}{frame.offset_disputed ? " · offset in dispute" : ""}</span>
          </summary>
          <TimeEditor rollKey={rollKey} frame={frame} />
        </details>
        <details className="fold" open={!!(frame.fact && (frame.fact.place_name || frame.fact.same_day_as || frame.fact.note))}>
          <summary>
            <span className="eyebrow">Facts about this frame</span>
            <span className="muted"> — a known day, a place name, same day as another frame</span>
          </summary>
          <FrameFacts rollKey={rollKey} frame={frame} nFrames={frames.length} />
        </details>

        <dl className="kv">
          <dt>interval</dt>
          <dd>{intervalText(frame)}</dd>
          <dt>confidence</dt>
          <dd>
            <span className="mono">{frame.confidence.toFixed(2)}</span>
            {frame.outside_mass > 0.05 && <span className="muted"> · outside the window {frame.outside_mass.toFixed(2)}</span>}
          </dd>
          <dt>place</dt>
          <dd>
            {frame.location === "ok" && (
              <span className="mono">
                {frame.lat!.toFixed(5)}, {frame.lon!.toFixed(5)} <span className="muted">({frame.location_source})</span>
              </span>
            )}
            {frame.location === "ambiguous" && (
              <span>
                ambiguous — {frame.clusters.length} places:{" "}
                {frame.clusters.slice(0, 4).map((c, k) => (
                  <span key={k} className="mono">
                    {k > 0 && "; "}
                    {c.label ? `${c.label} ` : ""}
                    {c.lat.toFixed(4)},{c.lon.toFixed(4)} ×{c.count}
                  </span>
                ))}
              </span>
            )}
            {frame.location === "none" && <span className="muted">unknown</span>}
          </dd>
          {outing && (
            <>
              <dt>outing</dt>
              <dd>
                <span className="muted mono">#{frame.outing}</span> {outing.description} <span className="muted">({outing.confidence.toFixed(1)})</span>
              </dd>
            </>
          )}
          {frame.truth && (
            <>
              <dt>hand-tagged</dt>
              <dd className="mono">
                {fmtDate(frame.truth, frame.tzoffset)} {fmtClock(frame.truth, frame.tzoffset)}
              </dd>
            </>
          )}
        </dl>

        {v && frame.source !== "anchored" && (
          <div className="verdict">
            <div className="verdict__head">
              <span className="eyebrow">Claude</span>
              <span className="mono">{v.match ? `match · ${v.confidence.toFixed(2)}` : `no match · ${v.confidence.toFixed(2)}`}</span>
              {frame.override?.no_reference && <span className="pill pill--lock">you: no reference</span>}
              {frame.override?.rejected.length ? <span className="pill pill--lock">you rejected {frame.override.rejected.length}</span> : null}
            </div>
            <p className="verdict__evidence">{v.evidence || "—"}</p>
            {cluePairs.length > 0 && (
              <ul className="clues">
                {cluePairs.map(([k, val]) => (
                  <li key={k}>
                    <span className="clues__k">{k.replace(/_/g, " ")}</span>
                    <span>{Array.isArray(val) ? val.join(", ") : String(val)}</span>
                  </li>
                ))}
              </ul>
            )}
          </div>
        )}
        {v && frame.source === "anchored" && cluePairs.length > 0 && (
          <ul className="clues clues--inline">
            {cluePairs.map(([k, val]) => (
              <li key={k}>
                <span className="clues__k">{k.replace(/_/g, " ")}</span>
                <span>{Array.isArray(val) ? val.join(", ") : String(val)}</span>
              </li>
            ))}
          </ul>
        )}
        {!v && <p className="muted">Not verified yet — `filmgeo verify {rollKey}` asks Claude about the shortlist.</p>}
      </div>

      <aside className="detail__side">
        <MapPane
          frame={frame}
          trail={trail.data ?? []}
          busy={assign.isPending}
          onPlace={(lat, lon, radius_m, label) => assign.mutate({ number: frame.number, body: { lat, lon, ...(radius_m ? { radius_m } : {}), ...(label ? { place_name: label } : {}) } })}
        />
        <Timeline
          roll={roll}
          frames={frames}
          selected={frame}
          trail={trail.data ?? []}
          busy={assign.isPending}
          onSelect={onSelect}
          onSetTime={(iso) => act({ when: iso })}
          onEvent={(i) => setBrowsing((b) => (b === i ? null : i))}
          browsing={browsing}
        />
      </aside>

      {browsingEvent && <PhotoBrowser rollKey={rollKey} frame={frame} event={browsingEvent} busy={assign.isPending} onPick={(uuid) => act({ anchor: uuid })} onClose={() => setBrowsing(null)} />}

      <CandidateStrip frame={frame} frames={frames} busy={assign.isPending} error={assign.error ? (assign.error as Error).message : null} onPick={(uuid) => act({ anchor: uuid })} onReject={(uuid) => act({ reject: [uuid] })} />
    </section>
  );
}
