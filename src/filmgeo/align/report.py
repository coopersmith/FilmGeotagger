"""Static HTML report for one aligned roll: the window timeline and every frame's verdict."""

from __future__ import annotations

import html
from datetime import datetime, timedelta
from pathlib import Path

from filmgeo.align.pipeline import RollRun
from filmgeo.report import CSS as BASE_CSS, thumb

CSS = BASE_CSS + """
.timeline { width: 100%; height: 90px; margin: 8px 0 18px; }
.row { display: grid; grid-template-columns: 130px 1fr 150px; gap: 14px; padding: 12px 0; border-top: 1px solid var(--line); }
.bar { height: 6px; background: #8883; border-radius: 3px; overflow: hidden; margin: 4px 0; }
.bar > i { display: block; height: 100%; }
.hi { background: var(--ok); } .mid { background: #b7791f; } .lo { background: var(--bad); } .locked { background: #888; }
.badge { display:inline-block; padding:1px 6px; border-radius:4px; background:#8882; margin-right:4px; font-size:11px; }
.warn { color: var(--bad); font-weight: 600; }
.truth { opacity: .75; font-size: 12px; }
small { opacity: .7; }
"""


def _conf_class(a) -> str:
    if a.source == "locked":
        return "locked"
    return "hi" if a.confidence >= 0.8 else "mid" if a.confidence >= 0.5 else "lo"


def _fmt(t: datetime) -> str:
    return t.strftime("%a %-d %b %H:%M")


def interval_text(a) -> str:
    lo, hi = a.t_lo, a.t_hi
    if a.source in ("anchored", "locked"):
        if lo.date() == hi.date():
            return f"this occasion, {lo:%a %-d %b %H:%M}–{hi:%H:%M}"
        return f"this occasion, {_fmt(lo)} – {_fmt(hi)}"
    if lo.date() == hi.date():
        return f"between {lo:%a %-d %b %H:%M} and {hi:%H:%M}"
    return f"between {_fmt(lo)} and {_fmt(hi)}"


def timeline_svg(r: RollRun, width: int = 1100) -> str:
    span = (r.window.end - r.window.start).total_seconds() or 1.0
    x = lambda t: 40 + (width - 60) * (t - r.window.start).total_seconds() / span
    parts = [f"<svg class=timeline viewBox='0 0 {width} 90' xmlns='http://www.w3.org/2000/svg'>"]
    # day ticks
    day = r.window.start.replace(hour=0, minute=0, second=0, microsecond=0)
    while day <= r.window.end:
        if day >= r.window.start:
            parts.append(f"<line x1='{x(day):.1f}' y1='58' x2='{x(day):.1f}' y2='70' stroke='#8886'/>")
            if day.day == 1 or (r.window.end - r.window.start) < timedelta(days=15):
                parts.append(f"<text x='{x(day):.1f}' y='84' font-size='9' text-anchor='middle' fill='#888'>{day:%-d %b}</text>")
        day += timedelta(days=1)
    # events
    for e in r.events:
        w = max(1.5, x(e.end) - x(e.start))
        parts.append(f"<rect x='{x(e.start):.1f}' y='40' width='{w:.1f}' height='16' fill='#4a90d9' opacity='.55'><title>event {e.index}: {e.count} photos</title></rect>")
    # frames: interval line + tick
    for f, a in zip(r.frames, r.solution.assignments):
        col = {"hi": "#1a7f37", "mid": "#b7791f", "lo": "#b42318", "locked": "#888"}[_conf_class(a)]
        parts.append(f"<line x1='{x(a.t_lo):.1f}' y1='24' x2='{x(a.t_hi):.1f}' y2='24' stroke='{col}' stroke-width='2' opacity='.5'/>")
        parts.append(f"<line x1='{x(a.time):.1f}' y1='14' x2='{x(a.time):.1f}' y2='34' stroke='{col}' stroke-width='2'><title>frame {f.number}</title></line>")
        if f.truth and r.window.contains(f.truth):
            parts.append(f"<circle cx='{x(f.truth):.1f}' cy='8' r='2.5' fill='#000' opacity='.6'><title>truth frame {f.number}</title></circle>")
    parts.append("</svg>")
    return "".join(parts)


def write(path: Path, r: RollRun) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    tdir = path.parent / f"thumbs_{r.key}"
    by_uuid = {a.uuid: a for a in r.pool}
    sol = r.solution
    parts = [
        "<!doctype html><meta charset=utf-8>",
        f"<title>filmgeo align {html.escape(r.key)}</title><style>{CSS}</style>",
        f"<h1>Roll {html.escape(r.key)} — alignment</h1>",
        "<div class=meta>",
        f"window {r.window.start:%Y-%m-%d %H:%M} .. {r.window.end:%Y-%m-%d %H:%M} ({html.escape(r.window_source)}) · ",
        f"{len(r.pool)} phone photos in {len(r.events)} events · trail {html.escape(str(r.trail_counts))} · ",
        f"{r.n_frames} frames, {sol.anchored} anchored, {len(r.verdicts)} verified",
    ]
    if r.facts.camera or r.facts.film:
        parts.append(f" · {html.escape(' / '.join(x for x in (r.facts.camera, r.facts.film, r.facts.lab) if x))}")
    parts.append("<br>")
    if r.reverse.suspect:
        parts.append(f"<span class=warn>possibly reverse-wound</span>: reversed order holds {r.reverse.reverse_anchored} anchors vs {r.reverse.forward_anchored} · ")
    if r.check.doubtful:
        parts.append(f"<span class=warn>window doubtful</span>: {html.escape(r.check.reason)} · ")
    else:
        parts.append(f"window check: {html.escape(r.check.reason)} · ")
    days = ", ".join(f"{d:%a %-d %b} ({m:.1f})" for d, m in r.check.best_days[:5])
    parts.append(f"best days by posterior mass: {days}")
    if r.outings:
        groups = "; ".join(f"#{g['frames'][0]}–#{g['frames'][-1]} {html.escape(g['description'])} ({g['confidence']:.1f})"
                           if len(g["frames"]) > 1 else f"#{g['frames'][0]} {html.escape(g['description'])}" for g in r.outings.groups)
        parts.append(f"<br>outings: {groups}")
        if r.outings.out_of_sequence:
            parts.append(f" · <span class=warn>out of sequence: {', '.join('#' + str(n) for n in r.outings.out_of_sequence)}</span>")
    parts.append("</div>")
    parts.append(timeline_svg(r))

    for f, a in zip(r.frames, sol.assignments):
        t = thumb(f.path, tdir / f"f{f.number:04d}.jpg")
        parts.append("<div class=row><div>")
        if t:
            parts.append(f"<img src='{tdir.name}/{t}' style='width:130px'>")
        parts.append(f"<figcaption><b>frame {f.number}</b></figcaption></div><div>")
        off = "" if a.tzoffset is None else f" {'+' if a.tzoffset >= 0 else '-'}{abs(a.tzoffset)//3600:02d}:{abs(a.tzoffset)%3600//60:02d}"
        parts.append(f"<span class=badge>{a.source}</span>")
        if r.outings:
            g = next((k for k, g in enumerate(r.outings.groups, 1) if f.number in g["frames"]), None)
            if g:
                parts.append(f"<span class=badge>outing {g}</span>")
        if a.offset_disputed:
            parts.append("<span class='badge warn'>offset disputed</span>")
        parts.append(f"<b>{a.time:%a %-d %b %Y %H:%M:%S}</b>{off} <small>{html.escape(interval_text(a))}</small>")
        parts.append(f"<div class=bar><i class={_conf_class(a)} style='width:{100*a.confidence:.0f}%'></i></div>")
        parts.append(f"<small>confidence {a.confidence:.2f}" + (f" · outside {a.outside_mass:.2f}" if a.outside_mass > 0.05 else "") + "</small><br>")
        if a.location == "ok":
            parts.append(f"<span class=badge>location {a.location_source}</span> {a.lat:.5f}, {a.lon:.5f}")
        elif a.location == "ambiguous":
            opts = "; ".join(f"{html.escape(c.label) + ' ' if c.label else ''}{c.lat:.4f},{c.lon:.4f} ×{c.count}" for c in a.clusters[:4])
            parts.append(f"<span class='badge warn'>location ambiguous</span> {opts}")
        else:
            parts.append("<span class='badge warn'>location unknown</span>")
        v = r.verdicts.get(f.number)
        if v and v.evidence:
            parts.append(f"<br><small>{html.escape(v.evidence)}</small>")
        if f.truth:
            delta = (a.time - f.truth).total_seconds() / 3600
            inside = a.t_lo - timedelta(minutes=2) <= f.truth <= a.t_hi + timedelta(minutes=2)
            parts.append(f"<br><span class=truth>hand-tagged {f.truth:%a %-d %b %H:%M} · {delta:+.1f} h · "
                         f"{'inside interval' if inside else '<b class=warn>outside interval</b>'}</span>")
        parts.append("</div><div>")
        if a.anchor_uuid and a.anchor_uuid in by_uuid:
            ct = thumb(by_uuid[a.anchor_uuid].derivative, tdir / f"f{f.number:04d}_anchor.jpg")
            if ct:
                parts.append(f"<img src='{tdir.name}/{ct}' style='width:150px'><figcaption>anchor {by_uuid[a.anchor_uuid].date:%m-%d %H:%M}</figcaption>")
        else:
            top = r.candidates.get(f.number, [])[:1]
            if top:
                ct = thumb(top[0].asset.derivative, tdir / f"f{f.number:04d}_top.jpg")
                if ct:
                    parts.append(f"<img src='{tdir.name}/{ct}' style='width:150px;opacity:.6'><figcaption>top candidate {top[0].asset.date:%m-%d %H:%M} (unverified)</figcaption>")
        parts.append("</div></div>")
    path.write_text("\n".join(parts))
    return path
