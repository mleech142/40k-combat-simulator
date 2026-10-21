"""
Write src/combat_sim/play/layouts.py from Games Workshop's Warhammer Event Companion PDF: its terrain
layouts (three per pair of force dispositions), read from the PDF's drawing data, not traced by hand.

    pip install pymupdf numpy        (only for this script; the app doesn't need them)
    python scripts/extract_event_layouts.py EVENT_COMPANION.pdf

The PDF is free on warhammer-community.com (version 1.2, August 2026, was used); never commit it.

Each layout page draws the battlefield as a portrait card (44" across, 60" down, the Attacker's edge at
the top). A card point (across, down) is board point (down, across): the card turned a quarter turn,
as for the deployment maps. On the card:
- Terrain areas are grey shapes. Their straight edges are the footprint's edges; rubble is drawn round
  them in curves. Each is fitted to one of the five footprints (geometry.FOOTPRINTS), turned and, for
  the polygon, turned over, whichever way its straight edges fit best.
- Terrain features are pictures: green ones are dense, yellow ones light. An area holding a dense
  feature is a dense area, else one holding a light feature is light.
- Objectives are icons: home (red or blue circles), central (teal circle) and expansion (teal diamond).
- Deployment zones are red and blue shapes; each layout uses one of the six deployment maps
  (battlefields.BUILTIN), whose zones and dividing line it takes.
The script stops if any area fits badly or a layout's zones match no deployment map.
"""

import math
import pprint
import re
import sys
from pathlib import Path

import fitz  # PyMuPDF
import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
from combat_sim.play.battlefields import MAPS, inside  # noqa: E402
from combat_sim.play.geometry import FOOTPRINTS, footprint_corners  # noqa: E402

OUT = ROOT / 'src' / 'combat_sim' / 'play' / 'layouts.py'
AREA_FILL = (0.82, 0.83, 0.83)
RED, BLUE = (0.62, 0.04, 0.06), (0.0, 0.24, 0.41)
CENTRAL, EXPANSION = (0.0, 0.45, 0.38), (0.13, 0.57, 0.52)
DISPOSITIONS = {'TAKE AND HOLD': 'take-and-hold', 'PURGE THE FOE': 'purge-the-foe', 'PRIORITY ASSETS': 'priority-assets',
                'RECONNAISSANCE': 'reconnaissance', 'DISRUPTION': 'disruption'}
WORST_FIT = 0.3  # Inches between an area's drawn straight edges and its fitted footprint


def close(colour, target, tol=0.03):
    return colour is not None and all(abs(a - b) <= tol for a, b in zip(colour, target))


def rotate(p, degrees):
    c, s = math.cos(math.radians(degrees)), math.sin(math.radians(degrees))
    return (p[0] * c - p[1] * s, p[0] * s + p[1] * c)


def segment_distance(p, a, b):
    ax, ay, bx, by = *a, *b
    length2 = (bx - ax) ** 2 + (by - ay) ** 2
    t = 0 if length2 == 0 else max(0, min(1, ((p[0] - ax) * (bx - ax) + (p[1] - ay) * (by - ay)) / length2))
    return math.dist(p, (ax + t * (bx - ax), ay + t * (by - ay)))


def edge_distance(p, shape):
    return min(segment_distance(p, a, b) for a, b in zip(shape, shape[1:] + shape[:1]))


def bezier(p0, p1, p2, p3, steps=6):
    return [((1 - t) ** 3 * p0.x + 3 * (1 - t) ** 2 * t * p1.x + 3 * (1 - t) * t * t * p2.x + t ** 3 * p3.x,
             (1 - t) ** 3 * p0.y + 3 * (1 - t) ** 2 * t * p1.y + 3 * (1 - t) * t * t * p2.y + t ** 3 * p3.y)
            for t in (i / steps for i in range(1, steps + 1))]


def outline(drawing):
    """A drawing's points in order (curves sampled) and its straight segments [(a, b)], in PDF points."""
    points, lines = [], []
    for item in drawing['items']:
        if item[0] == 'l':
            a, b = item[1], item[2]
            if not points:
                points.append((a.x, a.y))
            points.append((b.x, b.y))
            lines.append(((a.x, a.y), (b.x, b.y)))
        elif item[0] == 'c':
            if not points:
                points.append((item[1].x, item[1].y))
            points += bezier(*item[1:5])
        elif item[0] == 're':
            r = item[1]
            points += [(r.x0, r.y0), (r.x1, r.y0), (r.x1, r.y1), (r.x0, r.y1)]
    return points, lines


class Card:
    """A layout card's frame: PDF points to board inches."""

    def __init__(self, rect):
        self.rect = rect
        self.sx, self.sy = rect.width / 44, rect.height / 60

    def board(self, p):
        across, down = (p[0] - self.rect.x0) / self.sx, (p[1] - self.rect.y0) / self.sy
        return (down, across)


def polygon_area(points):
    return abs(sum(a[0] * b[1] - b[0] * a[1] for a, b in zip(points, points[1:] + points[:1]))) / 2


def fit_area(points, lines):
    """A terrain area (board inches) fitted to a footprint: {'footprint', 'x', 'y', 'angle', 'flip', 'fit'}."""
    # Its turn: the straight edges' direction (modulo 90), averaged by length round the circle
    vx = vy = 0.0
    for a, b in lines:
        theta = math.atan2(b[1] - a[1], b[0] - a[0]) * 4  # Edges 90 degrees apart count as one direction
        vx, vy = vx + math.dist(a, b) * math.cos(theta), vy + math.dist(a, b) * math.sin(theta)
    turn = math.degrees(math.atan2(vy, vx)) / 4 % 90 if (vx or vy) else 0
    flat = [rotate(p, -turn) for p in points]
    flat_lines = [(rotate(a, -turn), rotate(b, -turn)) for a, b in lines]
    xs, ys = [p[0] for p in flat], [p[1] for p in flat]
    box = (min(xs), min(ys), max(xs), max(ys))
    width_box, height_box = box[2] - box[0], box[3] - box[1]
    fill = polygon_area(flat) / (width_box * height_box)
    # Which footprint: its size against the box (rubble drawn round an area only makes the box bigger)
    best = None
    for name, (length, width) in FOOTPRINTS.items():
        if name == 'polygon' and fill > 0.85:
            continue
        for dx, dy in ((length, width), (width, length)):
            error = sum(3 * max(0, d - b) + max(0, b - d) for d, b in ((dx, width_box), (dy, height_box)))
            if best is None or error < best[0]:
                best = (error, name, dx, dy)
    _, name, dx, dy = best
    # Where: its straight edges along each axis, else the box's middle
    horizontal = [(a[1] + b[1]) / 2 for a, b in flat_lines if abs(a[1] - b[1]) < 0.15 and abs(a[0] - b[0]) > 0.25]
    vertical = [(a[0] + b[0]) / 2 for a, b in flat_lines if abs(a[0] - b[0]) < 0.15 and abs(a[1] - b[1]) > 0.25]

    def edges(found, low, high, size):
        lo = [v for v in found if v < (low + high) / 2]
        hi = [v for v in found if v >= (low + high) / 2]
        if lo and hi and abs((max(hi) - min(lo)) - size) < 0.4:
            return min(lo), max(hi)
        if lo:
            return min(lo), min(lo) + size
        if hi:
            return max(hi) - size, max(hi)
        return (low + high - size) / 2, (low + high + size) / 2

    x0, x1 = edges(vertical, box[0], box[2], dx)
    y0, y1 = edges(horizontal, box[1], box[3], dy)
    cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
    # Which way round: each quarter turn (and the polygon turned over) centred there, scored by how far
    # the drawn straight edges are from its edges
    best = None
    for quarter in range(4):
        for flip in ((False, True) if name == 'polygon' else (False,)):
            shape = [rotate((px, -py if flip else py), 90 * quarter) for px, py in footprint_corners(name)]
            sx, sy = [q[0] for q in shape], [q[1] for q in shape]
            if abs((max(sx) - min(sx)) - dx) > 0.01:
                continue
            ox, oy = cx - (max(sx) + min(sx)) / 2, cy - (max(sy) + min(sy)) / 2
            shape = [(q[0] + ox, q[1] + oy) for q in shape]
            score = sum(math.dist(a, b) * min(1.5, edge_distance(((a[0] + b[0]) / 2, (a[1] + b[1]) / 2), shape))
                        for a, b in flat_lines)
            if best is None or score < best[0]:
                best = (score, shape, (ox, oy), 90 * quarter, flip)
    _, shape, origin, quarter, flip = best
    residual = sorted(edge_distance(((a[0] + b[0]) / 2, (a[1] + b[1]) / 2), shape)
                      for a, b in flat_lines if math.dist(a, b) > 0.5)
    x, y = rotate(origin, turn)  # Where the footprint's own corner lands
    return {'footprint': name, 'x': snap(x, 0.05), 'y': snap(y, 0.05), 'angle': snap((quarter + turn) % 360, 0.5) % 360,
            'flip': flip, 'fit': residual[len(residual) // 2] if residual else 0}


def snap(value, step):
    return round(round(value / step) * step, 2)


def feature_kinds(doc):
    """Terrain feature pictures by xref: 'dense' (mostly green), 'light' (mostly yellow) or None."""
    cache = {}

    def kind(xref):
        if xref not in cache:
            pix = fitz.Pixmap(doc, xref)
            if pix.colorspace is None:
                cache[xref] = None
                return None
            if pix.colorspace.n != 3:
                pix = fitz.Pixmap(fitz.csRGB, pix)
            data = np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.height, pix.width, pix.n)
            rgb = data[..., :3].astype(float) / 255
            mask = data[..., 3] > 128 if pix.alpha else np.ones(rgb.shape[:2], bool)
            smask = doc.xref_get_key(xref, 'SMask')
            if smask[0] == 'xref':
                alpha = fitz.Pixmap(doc, int(smask[1].split()[0]))
                a = np.frombuffer(alpha.samples, dtype=np.uint8).reshape(alpha.height, alpha.width, alpha.n)[..., 0]
                if a.shape == mask.shape:
                    mask = a > 128
            total = max(1, mask.sum())
            green = ((rgb[..., 1] > rgb[..., 0] + 0.15) & (rgb[..., 1] > rgb[..., 2]) & mask).sum() / total
            yellow = ((rgb[..., 0] > 0.55) & (rgb[..., 1] > 0.4) & (rgb[..., 2] < 0.3) & mask).sum() / total
            cache[xref] = 'dense' if green > 0.15 else 'light' if yellow > 0.15 else None
        return cache[xref]
    return kind


def title_case(words):
    """'PURGE AND SECURE' -> 'Purge and Secure'."""
    small = {'and', 'the', 'of', 'to', 'a', 'in', 'on'}
    return ' '.join(w.lower() if i and w.lower() in small else w.capitalize() for i, w in enumerate(words.split()))


def header(page):
    """[(disposition, mission)] for the left and right cards at the top of a layout page, and its letter."""
    sides, letter = {0: [], 1: []}, None
    for block in page.get_text('dict')['blocks']:
        for line in block.get('lines', []):
            words = ' '.join(span['text'] for span in line['spans']).strip()
            x0, y0, x1, _ = line['bbox']
            found = re.fullmatch(r'LAYOUT ([ABC])', words)
            if found:
                letter = found.group(1)
            elif y0 < 250 and words and words not in ('FORCE DISPOSITION', 'MISSION', 'VS'):
                sides[0 if (x0 + x1) / 2 < page.rect.width / 2 else 1].append((y0, words))
    cards = []
    for side in (0, 1):
        lines = [w for _, w in sorted(sides[side])]
        disposition = next(DISPOSITIONS[w.upper()] for w in lines if w.upper() in DISPOSITIONS)
        mission = next(w for w in lines if w.upper() not in DISPOSITIONS)
        cards.append((disposition, title_case(mission)))
    return cards, letter


GRID = [(x + 0.25, y + 0.25) for x in range(60) for y in range(44)]


def deployment_map(zones):
    """The deployment map whose zones these are (each a list of polygons), or None."""
    def mismatch(polygons, zone):
        return sum(any(inside(p, poly) for poly in polygons) != inside(p, zone) for p in GRID) / len(GRID)
    scores = {key: mismatch(zones.get('attacker', []), m['zones']['attacker']) + mismatch(zones.get('defender', []), m['zones']['defender'])
              for key, m in MAPS.items()}
    key = min(scores, key=scores.get)
    return key if scores[key] < 0.01 else None


def read_layout(page, kind):
    cards, letter = header(page)
    drawings = page.get_drawings()
    frame = max((d['rect'] for d in drawings if d['type'] == 's' and d['rect'].width > 300 and d['rect'].height > 400),
                key=lambda r: r.width * r.height)
    card = Card(frame)

    def on_card(rect):
        return frame.x0 - 1 <= rect.x0 and rect.x1 <= frame.x1 + 1 and frame.y0 - 1 <= rect.y0 and rect.y1 <= frame.y1 + 1

    zones, objectives, areas = {}, [], []
    for d in drawings:
        fill, rect = d.get('fill'), d['rect']
        if not on_card(rect):
            continue
        icon = d['type'] == 'f' and 20 < rect.width < 40
        if d['type'] == 'f' and rect.width > 60 and rect.height > 60 and (close(fill, RED) or close(fill, BLUE)):
            zones.setdefault('attacker' if close(fill, RED) else 'defender', []).append([card.board(p) for p in outline(d)[0]])
        elif icon and close(fill, RED):
            objectives.append(('home', 'attacker', rect))
        elif icon and close(fill, BLUE):
            objectives.append(('home', 'defender', rect))
        elif icon and close(fill, CENTRAL):
            objectives.append(('central', None, rect))
        elif icon and close(fill, EXPANSION):
            objectives.append(('expansion', None, rect))
        elif d['type'] == 'fs' and close(fill, AREA_FILL):
            points, lines = outline(d)
            areas.append((rect, fit_area([card.board(p) for p in points], [(card.board(a), card.board(b)) for a, b in lines])))
    categories = [None] * len(areas)
    for info in page.get_image_info(xrefs=True):
        feature = kind(info['xref']) if info['xref'] else None
        if feature:
            cx, cy = (info['bbox'][0] + info['bbox'][2]) / 2, (info['bbox'][1] + info['bbox'][3]) / 2
            for i, (rect, _) in enumerate(areas):
                if rect.x0 <= cx <= rect.x1 and rect.y0 <= cy <= rect.y1 and categories[i] != 'dense':
                    categories[i] = feature
    where = f'page {page.number + 1}'
    map_key = deployment_map(zones)
    if map_key is None:
        raise SystemExit(f'{where}: its deployment zones match none of the six deployment maps')
    for _, fitted in areas:
        if fitted['fit'] > WORST_FIT:
            raise SystemExit(f'{where}: a {fitted["footprint"]} area fits its drawing badly ({fitted["fit"]:.2f}")')
    objectives.sort(key=lambda o: (['home', 'central', 'expansion'].index(o[0]), o[1] or '', o[2].y0, o[2].x0))
    (first, first_mission), (second, second_mission) = cards
    return {
        'id': f'{first}-vs-{second}-{letter.lower()}',
        'dispositions': (first, second), 'missions': (first_mission, second_mission), 'layout': letter, 'map': map_key,
        'objectives': [(k, role, *(snap(v, 0.05) for v in card.board(((r.x0 + r.x1) / 2, (r.y0 + r.y1) / 2))))
                       for k, role, r in objectives],
        'terrain': [(f['footprint'], f['x'], f['y'], f['angle'], f['flip'], categories[i] or 'exposed')
                    for i, (_, f) in enumerate(areas)],
    }


def main():
    doc = fitz.open(sys.argv[1])
    kind = feature_kinds(doc)
    layouts = [read_layout(page, kind) for page in doc if re.search(r'^LAYOUT [ABC]$', page.get_text(), re.M)
               and 'FORCE DISPOSITION' in page.get_text()]
    body = pprint.pformat(layouts, width=120, sort_dicts=False)
    OUT.write_text(f'''"""
The Warhammer Event Companion's terrain layouts (version 1.2, August 2026): three for each pair of force
dispositions, as measured from the PDF by scripts/extract_event_layouts.py (do not edit by hand; run it
again for a new version). battlefields.BUILTIN turns them into battlefields.

Each layout: 'id', 'dispositions' and 'missions' (the two cards at the top of its page), 'layout' (A, B
or C), 'map' (the deployment map whose zones and dividing line it uses), 'objectives' [(kind, role, x,
y)] and 'terrain' [(footprint, x, y, angle, flip, category)], in board inches as battlefields use them.
"""

LAYOUTS = {body}
''', encoding='utf-8')
    print(f'{len(layouts)} layouts written to {OUT.relative_to(ROOT)}')


if __name__ == '__main__':
    main()
