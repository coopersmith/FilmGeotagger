"""Static HTML contact sheet: every frame beside its top candidates.

M1's deliverable is a thing you can *look at*. Numbers say retrieval works; only the contact
sheet says whether the near-misses are near-misses for a reason — same place another day, a
similar wall, a person in similar clothing — which is what decides how much work Claude
verification has to do in the next stage.
"""

from __future__ import annotations

import html
from datetime import datetime
from pathlib import Path

from PIL import Image

Image.MAX_IMAGE_PIXELS = None

THUMB = 220


def thumb(src: str | None, dst: Path) -> str | None:
    if not src:
        return None
    if not dst.exists():
        try:
            with Image.open(src) as im:
                im = im.convert("RGB")
                im.thumbnail((THUMB, THUMB))
                dst.parent.mkdir(parents=True, exist_ok=True)
                im.save(dst, quality=82)
        except (OSError, ValueError):
            return None
    return dst.name


CSS = """
:root { color-scheme: light dark; --ok:#1a7f37; --bad:#b42318; --line:#8883; }
body { font: 13px/1.45 -apple-system, system-ui, sans-serif; margin: 0; padding: 24px; }
h1 { font-size: 18px; margin: 0 0 4px; }
.meta { opacity: .7; margin-bottom: 20px; }
.frame { display: flex; gap: 14px; padding: 14px 0; border-top: 1px solid var(--line); align-items: flex-start; }
.frame > .self { flex: 0 0 auto; }
.cands { display: flex; gap: 10px; overflow-x: auto; padding-bottom: 4px; }
figure { margin: 0; flex: 0 0 auto; width: 150px; }
img { width: 150px; height: auto; border-radius: 5px; display: block; }
.self img { width: 190px; }
figcaption { font-size: 11px; margin-top: 4px; opacity: .85; }
.hit figcaption { color: var(--ok); font-weight: 600; }
.miss figcaption { color: var(--bad); }
.tag { display:inline-block; padding:1px 5px; border-radius:4px; background:#8882; margin-right:4px; }
"""


def write(
    path: Path,
    roll_key: str,
    rows: list[dict],
    subtitle: str = "",
) -> Path:
    """`rows`: one dict per frame — number, path, date, and a list of candidate dicts."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tdir = path.parent / f"thumbs_{roll_key}"

    parts = [
        "<!doctype html><meta charset=utf-8>",
        f"<title>filmgeo roll {html.escape(roll_key)}</title>",
        f"<style>{CSS}</style>",
        f"<h1>Roll {html.escape(roll_key)}</h1>",
        f"<div class=meta>{html.escape(subtitle)}</div>",
    ]

    for row in rows:
        t = thumb(row["path"], tdir / f"f{row['number']:04d}.jpg")
        parts.append("<div class=frame><div class=self>")
        if t:
            parts.append(f"<figure><img src='{tdir.name}/{t}'>")
        else:
            parts.append("<figure>")
        parts.append(
            f"<figcaption><b>frame {row['number']}</b><br>"
            f"{row['date']:%Y-%m-%d %H:%M}</figcaption></figure></div><div class=cands>"
        )
        for i, c in enumerate(row["candidates"], 1):
            ct = thumb(c["path"], tdir / f"f{row['number']:04d}_c{i}.jpg")
            cls = "hit" if c["correct"] else "miss"
            parts.append(f"<figure class={cls}>")
            if ct:
                parts.append(f"<img src='{tdir.name}/{ct}'>")
            delta = c["date"] - row["date"]
            mins = delta.total_seconds() / 60
            near = f"{mins:+.0f} min" if abs(mins) < 600 else f"{mins/1440:+.1f} d"
            parts.append(
                f"<figcaption>#{i} · {c['score']:+.2f}<br>{c['date']:%m-%d %H:%M}<br>{near}</figcaption></figure>"
            )
        parts.append("</div></div>")

    path.write_text("\n".join(parts))
    return path
