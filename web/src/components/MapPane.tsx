import maplibregl from "maplibre-gl";
import { useEffect, useRef, useState } from "react";
import type { Frame, TrailPoint } from "../api";
import { useGeocode, usePlaceSearch } from "../api";
import { parseCoords } from "../coords";
import { fmtShort } from "../format";

// Key-free vector tiles. Offline the base map is blank but the pin, trail and clusters still draw.
const STYLE = "https://tiles.openfreemap.org/styles/liberty";

interface Props {
  rollKey: string;
  frame: Frame;
  trail: TrailPoint[];
  busy: boolean;
  onPlace: (lat: number, lon: number, radius_m?: number, label?: string) => void;
}

/** The frame's pin (draggable), the trail inside its interval, and the clusters offered when the place is ambiguous.
 *  Three ways to set the place: click a spot (a mode, so an ordinary click never moves anything), search a name,
 *  or paste coordinates. Each marker kind has its own colour and shape, named in the legend. */
export function MapPane({ rollKey, frame, trail, busy, onPlace }: Props) {
  const el = useRef<HTMLDivElement>(null);
  const map = useRef<maplibregl.Map | null>(null);
  const pin = useRef<maplibregl.Marker | null>(null);
  const clusterMarkers = useRef<maplibregl.Marker[]>([]);
  const hitMarkers = useRef<maplibregl.Marker[]>([]);
  const onPlaceRef = useRef(onPlace);
  onPlaceRef.current = onPlace;
  const [placing, setPlacing] = useState(false);
  const placingRef = useRef(false);
  placingRef.current = placing;

  const loaded = useRef(false);

  useEffect(() => {
    if (!el.current || map.current) return;
    const m = new maplibregl.Map({ container: el.current, style: STYLE, center: [0, 20], zoom: 1, attributionControl: false });
    m.addControl(new maplibregl.NavigationControl({ showCompass: false }), "top-right");
    m.addControl(new maplibregl.AttributionControl({ compact: true }));
    const addTrailLayer = () => {
      if (m.getSource("trail")) return;
      m.addSource("trail", { type: "geojson", data: { type: "FeatureCollection", features: [] } });
      // Phone and route points: small, grey, background. Check-ins: larger, green, with their name.
      m.addLayer({
        id: "trail-dots",
        type: "circle",
        source: "trail",
        filter: ["!=", ["get", "source"], "swarm"],
        paint: { "circle-radius": 3.5, "circle-color": "#4f4a43", "circle-opacity": 0.8, "circle-stroke-color": "#ffffff", "circle-stroke-width": 1 },
      });
      m.addLayer({
        id: "trail-checkins",
        type: "circle",
        source: "trail",
        filter: ["==", ["get", "source"], "swarm"],
        paint: { "circle-radius": 6, "circle-color": "#3e9a48", "circle-stroke-color": "#ffffff", "circle-stroke-width": 1.5 },
      });
      m.addLayer({
        id: "trail-checkin-labels",
        type: "symbol",
        source: "trail",
        filter: ["==", ["get", "source"], "swarm"],
        layout: { "text-field": ["get", "label"], "text-size": 11, "text-offset": [0, 1.1], "text-anchor": "top", "text-optional": true },
        paint: { "text-color": "#1f6b2a", "text-halo-color": "#ffffff", "text-halo-width": 1.4 },
      });
      m.on("click", (e) => {
        if (!placingRef.current) return;
        placingRef.current = false;
        setPlacing(false);
        onPlaceRef.current(Number(e.lngLat.lat.toFixed(6)), Number(e.lngLat.lng.toFixed(6)), undefined, "");   // a clicked spot has no name: drop a stale one
      });
      m.on("click", "trail-dots", (e) => {
        if (placingRef.current) return;
        const f = e.features?.[0];
        if (!f) return;
        const p = f.properties as { time: string; source: string; label?: string };
        new maplibregl.Popup({ closeButton: false, offset: 8 }).setLngLat(e.lngLat).setHTML(`<span class="mono">${p.time}</span> ${p.source}${p.label ? ` · ${p.label}` : ""}`).addTo(m);
      });
      m.on("mouseenter", "trail-dots", () => !placingRef.current && (m.getCanvas().style.cursor = "pointer"));
      m.on("mouseleave", "trail-dots", () => !placingRef.current && (m.getCanvas().style.cursor = ""));
      m.on("click", "trail-checkins", (e) => {
        if (placingRef.current) return;
        const f = e.features?.[0];
        if (!f) return;
        const p = f.properties as { time: string; label?: string };
        new maplibregl.Popup({ closeButton: false, offset: 8 }).setLngLat(e.lngLat).setHTML(`<b>${p.label ?? "check-in"}</b> <span class="mono">${p.time}</span>`).addTo(m);
      });
    };
    m.on("load", () => {
      loaded.current = true;
      addTrailLayer();
      m.fire("filmgeo:ready");
    });
    // No network, or the tile host is down: fall back to a blank ground so the pin, trail and
    // clusters still draw. `load` fires for the fallback style like any other.
    m.on("error", (e) => {
      if (!loaded.current && /style/i.test(String((e as { error?: Error }).error?.message ?? ""))) {
        m.setStyle({ version: 8, sources: {}, layers: [{ id: "bg", type: "background", paint: { "background-color": "#171411" } }] });
      }
    });
    map.current = m;
    (window as unknown as { __filmgeoMap?: maplibregl.Map }).__filmgeoMap = m;   // for poking at it from the console
    const ro = new ResizeObserver(() => m.resize());
    ro.observe(el.current);
    return () => {
      ro.disconnect();
      m.remove();
      map.current = null;
      loaded.current = false;
    };
  }, []);

  // Everything that depends on the frame: pin, clusters, trail data, viewport. Markers are DOM
  // and need no style; only the trail layer waits for the map to have loaded.
  useEffect(() => {
    const m = map.current;
    if (!m) return;
    pin.current?.remove();
    pin.current = null;
    clusterMarkers.current.forEach((c) => c.remove());
    clusterMarkers.current = [];
    const bounds = new maplibregl.LngLatBounds();
    let any = false;
    if (frame.lat != null && frame.lon != null) {
      const elPin = document.createElement("div");
      elPin.className = `pin ${frame.locked ? "pin--locked" : ""}`;
      elPin.innerHTML = `<span class="pin__dot"></span><span class="pin__label">frame ${String(frame.number).padStart(2, "0")}${frame.locked ? " · yours" : ""}</span>`;
      elPin.title = frame.locked ? "your pin — drag to move" : `${frame.location_source} — drag to set the place yourself`;
      const mk = new maplibregl.Marker({ element: elPin, draggable: !busy, anchor: "center" }).setLngLat([frame.lon, frame.lat]).addTo(m);
      mk.on("dragend", () => {
        const { lat, lng } = mk.getLngLat();
        onPlaceRef.current(lat, lng, undefined, "");
      });
      pin.current = mk;
      bounds.extend([frame.lon, frame.lat]);
      any = true;
    }
    if (frame.location === "ambiguous") {
      frame.clusters.forEach((c, k) => {
        const elC = document.createElement("button");
        elC.className = "cluster";
        elC.innerHTML = `<b>${k + 1}</b><span>×${c.count}</span>`;
        elC.title = `${c.label ? c.label + " · " : ""}${c.count} trail points within ${Math.round(c.spread_m)} m, ${fmtShort(c.first, frame.tzoffset)} – ${fmtShort(c.last, frame.tzoffset)}. Click to place the frame here.`;
        elC.disabled = busy;
        elC.onclick = () => onPlaceRef.current(c.lat, c.lon, Math.max(300, c.spread_m), c.label ?? undefined);
        clusterMarkers.current.push(new maplibregl.Marker({ element: elC, anchor: "center" }).setLngLat([c.lon, c.lat]).addTo(m));
        bounds.extend([c.lon, c.lat]);
        any = true;
      });
    }
    trail.forEach((p) => {
      bounds.extend([p.lon, p.lat]);
      any = true;
    });
    if (any) m.fitBounds(bounds, { padding: 48, maxZoom: 15, duration: document.hidden ? 0 : 400 });

    const data: GeoJSON.FeatureCollection = {
      type: "FeatureCollection",
      features: trail.map((p) => ({
        type: "Feature",
        geometry: { type: "Point", coordinates: [p.lon, p.lat] },
        properties: { time: fmtShort(p.time, p.tzoffset), source: p.source, label: p.label ?? undefined },
      })),
    };
    const setData = () => (m.getSource("trail") as maplibregl.GeoJSONSource | undefined)?.setData(data);
    if (loaded.current) setData();
    else m.once("filmgeo:ready", setData);
  }, [frame, trail, busy]);

  useEffect(() => {
    const m = map.current;
    if (m) m.getCanvas().style.cursor = placing ? "crosshair" : "";
  }, [placing]);
  useEffect(() => setPlacing(false), [frame.number]);
  useEffect(() => {
    if (!placing) return;
    const on = (e: KeyboardEvent) => e.key === "Escape" && setPlacing(false);
    addEventListener("keydown", on);
    return () => removeEventListener("keydown", on);
  }, [placing]);

  const [text, setText] = useState("");
  useEffect(() => setText(""), [frame.number]);
  const parsed = parseCoords(text);

  // Search a place by name: the hits are shown as numbered green rings; clicking one places the frame there.
  const [query, setQuery] = useState("");
  const [asked, setAsked] = useState("");
  useEffect(() => {
    setQuery("");
    setAsked("");
  }, [frame.number]);
  // Two sources: the user's own check-ins and visits (small businesses OpenStreetMap has never heard of,
  // and they carry a date), then the world. One place per name from the first; the rest from OSM.
  const mine = usePlaceSearch(rollKey, asked);
  const world = useGeocode(asked);
  const hits: { name: string; lat: number; lon: number; note: string; radius_m?: number }[] = [];
  const seen = new Set<string>();
  for (const p of mine.data ?? []) {
    if (seen.has(p.name)) continue;
    seen.add(p.name);
    hits.push({ name: p.name, lat: p.lat, lon: p.lon, note: `your ${p.kind}, ${fmtShort(p.when, p.tzoffset)}`, radius_m: 300 });
  }
  for (const h of world.data ?? []) hits.push({ name: h.name.split(",")[0], lat: h.lat, lon: h.lon, note: h.name.split(",").slice(1, 3).join(",").trim() });
  const searching = mine.isFetching || world.isFetching;
  const hitsKey = hits.map((h) => `${h.lat},${h.lon}`).join(";");
  useEffect(() => {
    const m = map.current;
    hitMarkers.current.forEach((h) => h.remove());
    hitMarkers.current = [];
    if (!m || hits.length === 0) return;
    const bounds = new maplibregl.LngLatBounds();
    hits.forEach((h, k) => {
      const elH = document.createElement("button");
      elH.className = "hit";
      elH.innerHTML = `<b>${k + 1}</b>`;
      elH.title = `${h.name} — click to place the frame here`;
      elH.onclick = () => onPlaceRef.current(h.lat, h.lon, h.radius_m, h.name);
      hitMarkers.current.push(new maplibregl.Marker({ element: elH, anchor: "center" }).setLngLat([h.lon, h.lat]).addTo(m));
      bounds.extend([h.lon, h.lat]);
    });
    if (frame.lat != null && frame.lon != null) bounds.extend([frame.lon, frame.lat]);
    m.fitBounds(bounds, { padding: 48, maxZoom: 14, duration: document.hidden ? 0 : 400 });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [hitsKey, frame.lat, frame.lon]);

  return (
    <div className="map">
      <div className="map__head">
        <span className="eyebrow">Place</span>
        <span className="muted">
          {frame.location === "ok" && (frame.locked ? "set by you" : `from ${frame.location_source}`)}
          {frame.location === "ambiguous" && `${frame.clusters.length} places in the interval — pick a numbered ring`}
          {frame.location === "none" && "nothing in the trail places this frame"}
        </span>
      </div>
      <div className="map__tools">
        <button className={`btn ${placing ? "btn--on" : ""}`} type="button" disabled={busy} onClick={() => setPlacing((p) => !p)} title="then click the spot on the map where the frame was shot">
          {placing ? "click the map… (esc to cancel)" : "click a spot on the map"}
        </button>
        <form
          className="map__search"
          onSubmit={(e) => {
            e.preventDefault();
            setAsked(query.trim());
          }}
        >
          <input value={query} onChange={(e) => setQuery(e.target.value)} placeholder="search a place by name — your check-ins, then the world" aria-label="search a place by name" />
          <button className="btn btn--ghost" type="submit" disabled={query.trim().length < 3 || searching}>
            {searching ? "…" : "search"}
          </button>
        </form>
      </div>
      {asked && !searching && (
        <ol className="map__hits">
          {hits.length === 0 && <li className="muted">nothing found for “{asked}” — click the spot on the map instead</li>}
          {hits.map((h, k) => (
            <li key={`${h.lat},${h.lon}`}>
              <button className="link" disabled={busy} onClick={() => onPlaceRef.current(h.lat, h.lon, h.radius_m, h.name)} title="place the frame here">
                <b className="mono">{k + 1}</b> {h.name} <span className="muted">{h.note}</span>
              </button>
            </li>
          ))}
        </ol>
      )}
      {asked && world.error && <span className="error">{(world.error as Error).message}</span>}
      <form
        className="map__coords"
        onSubmit={(e) => {
          e.preventDefault();
          if (parsed && !busy) {
            onPlaceRef.current(Number(parsed.lat.toFixed(6)), Number(parsed.lon.toFixed(6)), undefined, "");
            setText("");
          }
        }}
      >
        <input
          value={text}
          onChange={(e) => setText(e.target.value)}
          placeholder={frame.lat != null ? `${frame.lat.toFixed(5)}, ${frame.lon!.toFixed(5)} — paste coordinates or a map link` : "paste coordinates or a map link: 43.7696, 11.2558"}
          aria-label="latitude and longitude"
        />
        <button className="btn" type="submit" disabled={!parsed || busy}>
          set pin
        </button>
        {text.trim() !== "" && !parsed && <span className="error">not a latitude and longitude</span>}
        {parsed && (
          <span className="muted mono">
            {parsed.lat.toFixed(5)}, {parsed.lon.toFixed(5)}
          </span>
        )}
      </form>
      <div ref={el} className={`map__canvas ${placing ? "is-placing" : ""}`} />
      <div className="map__legend muted">
        <span><i className="lg lg--pin" /> the frame{frame.locked ? " (yours)" : ""}</span>
        <span><i className="lg lg--dot" /> phone photos and routes{trail.length ? ` (${trail.length})` : ""}</span>
        <span><i className="lg lg--checkin" /> check-ins</span>
        {frame.location === "ambiguous" && <span><i className="lg lg--ring" /> places to pick</span>}
        {asked && hits.length > 0 && <span><i className="lg lg--hit" /> search results</span>}
      </div>
    </div>
  );
}
