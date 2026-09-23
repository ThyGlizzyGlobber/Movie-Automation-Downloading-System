import re, sys
from PIL import Image, ImageDraw
src = open('brand_identity/obsidian-brand/icons/Obsidian-Logo-Icon-Only.svg').read()
paths = re.findall(r'<path d="([^"]+)" style="fill:rgb\((\d+),(\d+),(\d+)\);"/>', src)
assert len(paths) == 14, len(paths)
# Icon-Only.svg: translate(-425,-225) around matrix(1.006124,0,0,0.860155,-43.853893,151.026637), viewBox 1150x1550
def shard_pt(x, y):
    return (1.006124*x - 43.853893 - 425, 0.860155*y + 151.026637 - 225)
SCALE, T = 0.53, 1024
ox, oy = (T - 1150*SCALE)/2, (T - 1550*SCALE)/2
def tile_pt(x, y):
    sx, sy = shard_pt(x, y); return (ox + sx*SCALE, oy + sy*SCALE)
polys = []
for d, r, g, b in paths:
    pts = [tuple(map(float, p.split(','))) for p in re.findall(r'[ML]([\d.]+,[\d.]+)', d)]
    polys.append(([tile_pt(*p) for p in pts], (int(r), int(g), int(b))))
TOP, BOT, STOP = (5, 5, 8), (0x84, 0x45, 0xFF), 0.107
def raster(size, ss=4):
    N = size*ss
    im = Image.new('RGB', (N, N))
    px = ImageDraw.Draw(im)
    for y in range(N):
        t = max(0.0, (y/N - STOP)/(1 - STOP))
        px.line([(0, y), (N, y)], fill=tuple(round(a + (b-a)*t) for a, b in zip(TOP, BOT)))
    k = N / T
    for pts, col in polys:
        px.polygon([(x*k, y*k) for x, y in pts], fill=col)
    return im.resize((size, size), Image.LANCZOS)
out = 'frontend/public/'
raster(180).save(out + 'apple-touch-icon.png', optimize=True)
raster(192).save(out + 'icon-192.png', optimize=True)
raster(512).save(out + 'icon-512.png', optimize=True)
raster(32).save(out + 'favicon-32.png', optimize=True)
raster(1024).save('brand_identity/obsidian-brand/icons/icon-1024.png', optimize=True)
# Vector tile: same composition, rounded like the old mark, for favicon / login badge / manifest.
body = '\n'.join(f'<path d="{d}" fill="#{int(r):02x}{int(g):02x}{int(b):02x}"/>' for d, r, g, b in paths)
svg = f'''<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1024 1024" width="1024" height="1024">
<defs><linearGradient id="bg" x1="0" y1="0" x2="0" y2="1"><stop offset="{STOP}" stop-color="#050508"/><stop offset="1" stop-color="#8445ff"/></linearGradient>
<clipPath id="r"><rect width="1024" height="1024" rx="230"/></clipPath></defs>
<g clip-path="url(#r)"><rect width="1024" height="1024" fill="url(#bg)"/>
<g transform="translate({ox:.3f} {oy:.3f}) scale({SCALE}) translate(-425 -225) matrix(1.006124 0 0 0.860155 -43.853893 151.026637)">
{body}
</g></g></svg>
'''
open(out + 'brand-icon.svg', 'w').write(svg)
open('brand_identity/obsidian-brand/icons/icon-tile.svg', 'w').write(svg)
print('ok')
