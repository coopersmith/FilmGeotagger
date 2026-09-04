import type { Frame, RollEvent } from "../api";
import { usePhotos } from "../api";
import { fmtClock, fmtDelta, fmtShort } from "../format";

interface Props {
  rollKey: string;
  frame: Frame;
  event: RollEvent;
  busy: boolean;
  onPick: (uuid: string) => void;
  onClose: () => void;
}

/** Every phone photo of one event, so the anchor can be any photo, not only a shortlisted one. */
export function PhotoBrowser({ rollKey, frame, event, busy, onPick, onClose }: Props) {
  const photos = usePhotos(rollKey, event.index);
  return (
    <div className="browser">
      <div className="browser__head">
        <span className="eyebrow">Event {event.index}</span>
        <span className="muted">
          {fmtShort(event.start, frame.tzoffset)} – {fmtClock(event.end, frame.tzoffset)} · {event.count} photos
          {photos.data && photos.data.length < event.count ? ` (${photos.data.length} with a local image)` : ""}
        </span>
        <button className="link" onClick={onClose}>
          close
        </button>
      </div>
      {photos.isLoading && <p className="muted">loading…</p>}
      <ol className="browser__grid">
        {(photos.data ?? []).map((p) => {
          const chosen = frame.anchor_uuid === p.uuid;
          return (
            <li key={p.uuid} className={`photo ${chosen ? "is-chosen" : ""}`}>
              <button disabled={busy || chosen} onClick={() => onPick(p.uuid)} title={`${p.filename} — use this photo's time and GPS`}>
                <img src={`${p.image}?size=small`} alt="" loading="lazy" />
                <span className="mono">
                  {fmtClock(p.time, p.tzoffset, true)} <span className="muted">{fmtDelta(frame.time, p.time)}</span>
                </span>
              </button>
            </li>
          );
        })}
      </ol>
    </div>
  );
}
