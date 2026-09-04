import maplibregl from "maplibre-gl";
import { useEffect, useRef } from "react";
import type { Frame, TrailPoint } from "../api";
import { fmtShort } from "../format";

// Key-free vector tiles. Offline the base map is blank but the pin, trail and clusters still draw.
const STYLE = "https://tiles.openfreemap.org/styles/liberty";

interface Props {
  frame: Frame;
  trail: TrailPoint[];
  busy: boolean;
  onPlace: (lat: number, lon: number, radius_m?: number, label?: string) => void;
}

/** The frame's pin (draggable), the trail inside its interval, and the clusters offered when the place is ambiguous. */
export function MapPane({ frame, trail, busy, onPlace }: Props) {
  const el = useRef<HTMLDivElement>(null);
  const map = useRef<maplibregl.Map | null>(null);
  const pin = useRef<maplibregl.Marker | null>(null);
  const clusterMarkers = useRef<maplibregl.Marker[]>([]);
  const onPlaceRef = useRef(onPlace);
  onPlaceRef.current = onPlace;

  const loaded = useRef(false);

  useEffect(() => {
    if (!el.current || map.current) return;
    const m = new maplibregl.Map({ container: el.current, style: STYLE, center: [0, 20], zoom: 1, attributionControl: false });
    m.addControl(new maplibregl.NavigationControl({ showCompass: false }), "top-right");
    m.addControl(new maplibregl.AttributionControl({ compact: true }));
    const addTrailLayer = () => {
      if (m.getSource("trail")) return;
      m.addSource("trail", { type: "geojson", data: { type: "FeatureCollection", features: [] } });
      m.addLayer({
        id: "trail-dots",
        type: "circle",
        source: "trail",
        paint: { "circle-radius": 4, "circle-color": "#f0a63a", "circle-opacity": 0.75, "circle-stroke-color": "#0f0d0b", "circle-stroke-width": 1 },
      });
      m.on("click", "trail-dots", (e) => {
        const f = e.features?.[0];
        if (!f) return;
        const p = f.properties as { time: string; source: string; label?: string };
        new maplibregl.Popup({ closeButton: false, offset: 8 }).setLngLat(e.lngLat).setHTML(`<span class="mono">${p.time}</span> ${p.source}${p.label ? ` · ${p.label}` : ""}`).addTo(m);
      });
      m.on("mouseenter", "trail-dots", () => (m.getCanvas().style.cursor = "pointer"));
      m.on("mouseleave", "trail-dots", () => (m.getCanvas().style.cursor = ""));
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
    return () => {
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
      elPin.title = frame.locked ? "your pin — drag to move" : `${frame.location_source} — drag to set the place yourself`;
      const mk = new maplibregl.Marker({ element: elPin, draggable: !busy, anchor: "bottom" }).setLngLat([frame.lon, frame.lat]).addTo(m);
      mk.on("dragend", () => {
        const { lat, lng } = mk.getLngLat();
        onPlaceRef.current(lat, lng);
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
    if (any) m.fitBounds(bounds, { padding: 48, maxZoom: 15, duration: 400 });

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

  return (
    <div className="map">
      <div className="map__head">
        <span className="eyebrow">Place</span>
        <span className="muted">
          {frame.location === "ok" && `${frame.location_source} · drag the pin to override`}
          {frame.location === "ambiguous" && `${frame.clusters.length} places in the interval — pick one`}
          {frame.location === "none" && "nothing in the trail places this frame"}
          {trail.length > 0 && ` · ${trail.length} trail points`}
        </span>
      </div>
      <div ref={el} className="map__canvas" />
    </div>
  );
}
