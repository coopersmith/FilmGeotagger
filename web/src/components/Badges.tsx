import type { Frame } from "../api";

/** Source, lock, location and confirmation as small pills; `compact` shows glyphs only. */
export function Badges({ frame, compact = false }: { frame: Frame; compact?: boolean }) {
  const items: { key: string; label: string; glyph: string; cls?: string; title: string }[] = [];
  if (frame.locked) items.push({ key: "lock", label: "locked", glyph: "●", cls: "badge--lock", title: "you decided this frame" });
  else if (frame.source === "anchored") items.push({ key: "anchor", label: "anchored", glyph: "⚓", cls: "badge--anchor", title: "verified against a phone photo" });
  else if (frame.source === "skipped") items.push({ key: "skip", label: "skipped", glyph: "∅", title: "left unassigned" });
  else items.push({ key: "interp", label: "interpolated", glyph: "~", title: "placed between its neighbours" });
  if (frame.location === "ambiguous") items.push({ key: "amb", label: "ambiguous place", glyph: "?", cls: "badge--warn", title: `${frame.clusters.length} candidate places` });
  if (frame.location === "none") items.push({ key: "noloc", label: "no place", glyph: "·", cls: "badge--dim", title: "nothing in the trail places it" });
  if (frame.offset_disputed) items.push({ key: "off", label: "offset disputed", glyph: "±", cls: "badge--warn", title: "trail points disagree on the UTC offset" });
  if (frame.status === "confirmed") items.push({ key: "ok", label: "confirmed", glyph: "✓", cls: "badge--ok", title: "confirmed for writing" });
  return (
    <span className={`badges ${compact ? "badges--compact" : ""}`}>
      {items.map((b) => (
        <span key={b.key} className={`badge ${b.cls ?? ""}`} title={b.title}>
          {compact ? b.glyph : b.label}
        </span>
      ))}
    </span>
  );
}
