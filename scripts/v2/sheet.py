import sys
from PIL import Image
from common import load_run
S = "/private/tmp/claude-501/-Users-coopersmithair-GitHub-FilmGeotagger/92c9675a-f6b1-462d-8851-02476cd46b4f/scratchpad/"
items = [a.split("#") for a in sys.argv[2:]]
ims = []
for key, n in items:
    r = load_run(key)
    im = Image.open(r.frames[int(n) - 1].path).convert("RGB"); im.thumbnail((520, 520)); ims.append(im)
W = sum(i.width for i in ims) + 10 * len(ims); H = max(i.height for i in ims)
sheet = Image.new("RGB", (W, H), "white"); x = 0
for im in ims:
    sheet.paste(im, (x, 0)); x += im.width + 10
sheet.save(S + sys.argv[1]); print(S + sys.argv[1])
