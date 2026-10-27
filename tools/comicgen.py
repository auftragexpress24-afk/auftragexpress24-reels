"""Comic-Reel-Generator: animierte Cartoon-Figur zeigt das Gewerk bei der Arbeit (9:16, 30 fps, mit Beat).

Aufruf:  python3 comicgen.py spec.json ausgabe.mp4

Spec (JSON):
  trade        maler | abbruch | bodenleger | reinigung | umzug | lieferung | sicherheitsdienst | entruempelung | verkehrssicherung
  bpm (105-128), progression (0-5), seed, drop_scene (Index)
  theme        {"accent": "#..", "caption": "#fde047", "paint": "#.." (Maler: neue Wandfarbe), "wood": "#.." (Boden)}
  env          day | autumn | night | rain | snow | indoor   (Standard je Gewerk; Sicherheitsdienst immer night)
  look         Figur: {"skin","hair","shirt","pants","overalls","hat": cap|helmet|beanie|painter|guard|none,
                       "hat_color","vest": none|orange|yellow, "chest": "Text auf Shirt", "beard": bool, "gloves": "#.."}
  scenes       Liste, jede Szene: {"beats": n, "action": ..., "caption": [1-3 Zeilen], "bubble": "Sprechblase",
                                    "fx": "ZACK!", "flip": bool}
     action:   work (Figur arbeitet, Fortschritt läuft weiter) | walk_in | wave | thumbs | think | shock | celebrate | point | idle
               cta  (letzte Szene): {"lines":[..], "button","sub","phone","foot"}
  start_prog   0..1: Arbeitsfortschritt zu Beginn (z. B. 0 = vorher, Ende = nachher)
Der Arbeitsfortschritt (gestrichene Wand, verlegte Dielen, geladene Kartons ...) wächst nur in "work"-Szenen
und bleibt in den anderen Szenen stehen – so entsteht ein Vorher/Nachher.
"""
import json, math, os, random, shutil, subprocess, sys
import cairo

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import audio

W, H, FPS = 1080, 1920, 30
FY = 1640          # Boden-Linie (Füße)
OL = 7             # Konturstärke
INK = (0.07, 0.07, 0.09)
PI = math.pi
STAGE = 1.05      # Bühne etwas größer zeigen


def ensure_fonts():
    dst = os.path.expanduser("~/.local/share/fonts")
    need = False
    for f in ("Bangers-Regular.ttf", "LuckiestGuy-Regular.ttf"):
        if not os.path.exists(os.path.join(dst, f)):
            os.makedirs(dst, exist_ok=True)
            shutil.copy(os.path.join(HERE, "fonts", f), dst)
            need = True
    if need:
        subprocess.run(["fc-cache", "-f"], check=False, stdout=subprocess.DEVNULL)


def hx(h, a=1.0):
    h = h.lstrip("#")
    return tuple(int(h[i:i + 2], 16) / 255 for i in (0, 2, 4)) + (a,)


def mix(c1, c2, k):
    return tuple(c1[i] * (1 - k) + c2[i] * k for i in range(3)) + (1.0,)


def clamp(x, a=0.0, b=1.0): return max(a, min(b, x))
def ease_out(x): x = clamp(x); return 1 - (1 - x) ** 3
def ease_io(x): x = clamp(x); return x * x * (3 - 2 * x)
def back_out(x): x = clamp(x); c = 1.9; return max(0.01, 1 + (c + 1) * (x - 1) ** 3 + c * (x - 1) ** 2)
def lerp(a, b, k): return a + (b - a) * k


# ------------------------------------------------------------------ Zeichen-Helfer
def src(c, col):
    c.set_source_rgba(*col) if len(col) == 4 else c.set_source_rgb(*col)


def fill_ol(c, col, lw=OL, ink=INK):
    src(c, col); c.fill_preserve(); src(c, ink); c.set_line_width(lw); c.stroke()


def rr(c, x, y, w, h, r):
    r = min(r, w / 2, h / 2)
    c.new_sub_path()
    c.arc(x + w - r, y + r, r, -PI / 2, 0); c.arc(x + w - r, y + h - r, r, 0, PI / 2)
    c.arc(x + r, y + h - r, r, PI / 2, PI); c.arc(x + r, y + r, r, PI, 1.5 * PI); c.close_path()


def circle(c, x, y, r):
    c.new_sub_path(); c.arc(x, y, r, 0, 2 * PI)


def ellipse(c, x, y, rx, ry):
    c.save(); c.translate(x, y); c.scale(rx, max(ry, 0.01)); c.new_sub_path(); c.arc(0, 0, 1, 0, 2 * PI); c.restore()


def poly(c, pts):
    c.move_to(*pts[0])
    for p in pts[1:]: c.line_to(*p)
    c.close_path()


def chain(c, pts, w, cols, ink=INK):
    """Gliedmaße: Kontur einmal komplett, dann Segmentfarben."""
    c.set_line_cap(cairo.LINE_CAP_ROUND); c.set_line_join(cairo.LINE_JOIN_ROUND)
    c.move_to(*pts[0])
    for p in pts[1:]: c.line_to(*p)
    src(c, ink); c.set_line_width(w + 2 * OL); c.stroke()
    for i in range(len(pts) - 1):
        c.move_to(*pts[i]); c.line_to(*pts[i + 1]); src(c, cols[i]); c.set_line_width(w); c.stroke()


def text(c, s, x, y, size, fill, font="Bangers", stroke=None, sw=0, maxw=None, rot=0.0, sc=1.0, shadow=0, align="c"):
    c.save()
    c.select_font_face(font, cairo.FONT_SLANT_NORMAL, cairo.FONT_WEIGHT_NORMAL)
    c.set_font_size(size)
    e = c.text_extents(s)
    if maxw and e.x_advance > maxw:
        size *= maxw / e.x_advance; c.set_font_size(size); e = c.text_extents(s)
    c.translate(x, y); c.rotate(rot); c.scale(sc, sc)
    ox = -e.x_advance / 2 if align == "c" else (0 if align == "l" else -e.x_advance)
    oy = -(e.y_bearing + e.height / 2)
    c.set_line_join(cairo.LINE_JOIN_ROUND)
    if shadow:
        c.move_to(ox + shadow, oy + shadow); c.text_path(s)
        if stroke: c.set_line_width(sw * 2); src(c, INK); c.stroke_preserve()
        src(c, INK); c.fill()
    c.move_to(ox, oy); c.text_path(s)
    if stroke:
        src(c, stroke); c.set_line_width(sw * 2); c.stroke_preserve()
    src(c, fill); c.fill()
    c.restore()
    return e.x_advance, size


def text_w(c, s, size, font="Bangers"):
    c.select_font_face(font, cairo.FONT_SLANT_NORMAL, cairo.FONT_WEIGHT_NORMAL)
    c.set_font_size(size); return c.text_extents(s).x_advance


def burst(c, x, y, r1, r2, n, rot, col, lw=OL):
    pts = []
    for i in range(2 * n):
        a = rot + i * PI / n
        r = r1 if i % 2 == 0 else r2
        pts.append((x + r * math.cos(a), y + r * math.sin(a)))
    poly(c, pts); fill_ol(c, col, lw)


def ik(S, T, l1, l2, bend=None):
    dx, dy = T[0] - S[0], T[1] - S[1]
    d = math.hypot(dx, dy)
    d = max(abs(l1 - l2) + 1, min(d, (l1 + l2) * 0.999))
    a = math.atan2(dy, dx)
    if bend is None: bend = 1 if T[0] >= S[0] else -1
    B = math.acos(clamp((l1 * l1 + d * d - l2 * l2) / (2 * l1 * d), -1, 1))
    ang = a + bend * B
    E = (S[0] + l1 * math.cos(ang), S[1] + l1 * math.sin(ang))
    Hn = (S[0] + d * math.cos(a), S[1] + d * math.sin(a))
    return E, Hn


def seg(P, a, l):
    """a: 0 = nach unten, +PI/2 = nach rechts, PI = nach oben."""
    return (P[0] + l * math.sin(a), P[1] + l * math.cos(a))


def rot(P, O, a):
    s, co = math.sin(a), math.cos(a)
    x, y = P[0] - O[0], P[1] - O[1]
    return (O[0] + x * co - y * s, O[1] + x * s + y * co)


# ------------------------------------------------------------------ Figur
DEFAULT_POSE = dict(la=(-0.18, -0.08), ra=(0.18, 0.08), ll=(-0.06, -0.02), rl=(0.06, 0.02), bob=0.0, lean=0.0,
                    mouth="smile", eyes="open", brows="happy", tilt=0.0)


def person(c, x, fy, s, look, pose, face=1, t=0.0):
    p = dict(DEFAULT_POSE); p.update(pose)
    skin = hx(look.get("skin", "#f1c27d")); shirt = hx(look.get("shirt", "#3b82f6"))
    pants = hx(look.get("pants", "#1f2937")); hair = hx(look.get("hair", "#3b2416"))
    glove = hx(look["gloves"]) if look.get("gloves") else skin
    L1, L2 = 118 * s, 118 * s
    Hc = (x, fy - (L1 + L2) + p["bob"])
    lean = p["lean"]
    J = {}
    # Beine
    for side, key in ((-1, "ll"), (1, "rl")):
        hip = (Hc[0] + side * 36 * s, Hc[1] - 4 * s)
        a1, a2 = p[key]
        K = seg(hip, a1, L1); Fp = seg(K, a2, L2)
        if "feet" in p and p["feet"]:
            # Fuß-Ziel per IK (Knie nach vorne)
            T = p["feet"][0 if side < 0 else 1]
            K, Fp = ik(hip, T, L1, L2, bend=-face if face else -1)
        chain(c, [hip, K, Fp], 46 * s, [pants, pants])
        ellipse(c, Fp[0] + face * 14 * s, Fp[1] + 4 * s, 36 * s, 19 * s); fill_ol(c, hx("#1f2937"), OL * 0.9)
        J["lf" if side < 0 else "rf"] = Fp
    # Rumpf
    c.save(); c.translate(*Hc); c.rotate(lean)
    rr(c, -24 * s, -232 * s, 48 * s, 40 * s, 10 * s); fill_ol(c, skin)          # Hals
    rr(c, -80 * s, -208 * s, 160 * s, 214 * s, 44 * s); fill_ol(c, shirt)
    if look.get("overalls"):
        ov = hx(look["overalls"])
        rr(c, -62 * s, -120 * s, 124 * s, 126 * s, 26 * s); fill_ol(c, ov, OL * 0.8)
        for sx in (-1, 1):
            c.move_to(sx * 50 * s, -118 * s); c.line_to(sx * 56 * s, -200 * s)
            src(c, INK); c.set_line_width(22 * s); c.stroke()
            c.move_to(sx * 50 * s, -118 * s); c.line_to(sx * 56 * s, -200 * s)
            src(c, ov); c.set_line_width(12 * s); c.stroke()
        rr(c, -26 * s, -100 * s, 52 * s, 40 * s, 6 * s); fill_ol(c, mix(ov, (0, 0, 0), 0.12), OL * 0.6)
    vest = look.get("vest", "none")
    if vest != "none":
        vc = hx("#f97316") if vest == "orange" else hx("#d9f99d")
        c.move_to(-80 * s, -170 * s); c.line_to(-30 * s, -206 * s); c.line_to(-8 * s, -110 * s)
        c.line_to(8 * s, -110 * s); c.line_to(30 * s, -206 * s); c.line_to(80 * s, -170 * s)
        c.line_to(80 * s, 0); c.line_to(-80 * s, 0); c.close_path(); fill_ol(c, vc, OL * 0.8)
        for yy in (-78, -34):
            c.rectangle(-80 * s, yy * s, 160 * s, 16 * s); fill_ol(c, hx("#e5e7eb"), OL * 0.5)
    if look.get("chest"):
        text(c, look["chest"], 0, -150 * s, 34 * s, hx("#facc15"), stroke=INK, sw=3, maxw=130 * s)
    c.restore()
    belt_y = Hc[1] - 8 * s
    # Schultern / Kopf (gedreht)
    S_l = rot((Hc[0] - 68 * s, Hc[1] - 186 * s), Hc, lean)
    S_r = rot((Hc[0] + 68 * s, Hc[1] - 186 * s), Hc, lean)
    Hd = rot((Hc[0], Hc[1] - 300 * s), Hc, lean)
    J["head"] = Hd; J["sl"] = S_l; J["sr"] = S_r; J["hip"] = Hc
    A1, A2 = 106 * s, 100 * s
    arms = {}
    for key, S in (("la", S_l), ("ra", S_r)):
        tk = key + "_t"
        if p.get(tk) is not None:
            E, Hn = ik(S, p[tk], A1, A2, p.get(key + "_bend"))
        else:
            a1, a2 = p[key]
            E = seg(S, a1, A1); Hn = seg(E, a2, A2)
        arms[key] = (S, E, Hn)
    J["lh"] = arms["la"][2]; J["rh"] = arms["ra"][2]
    J["le"] = arms["la"][1]; J["re"] = arms["ra"][1]

    def draw_arm(key):
        S, E, Hn = arms[key]
        chain(c, [S, E, Hn], 34 * s, [shirt, skin if not look.get("long_sleeves") else shirt])
        circle(c, Hn[0], Hn[1], 22 * s); fill_ol(c, glove, OL * 0.9)
        if p.get("thumb") == key:
            rr(c, Hn[0] - 9 * s, Hn[1] - 52 * s, 18 * s, 36 * s, 9 * s); fill_ol(c, glove, OL * 0.8)
        if p.get("finger") == key:
            ang = math.atan2(Hn[1] - E[1], Hn[0] - E[0])
            c.move_to(*Hn); c.line_to(Hn[0] + 40 * s * math.cos(ang), Hn[1] + 40 * s * math.sin(ang))
            c.set_line_cap(cairo.LINE_CAP_ROUND); src(c, INK); c.set_line_width(14 * s + 2 * OL * 0.8); c.stroke()
            c.move_to(*Hn); c.line_to(Hn[0] + 40 * s * math.cos(ang), Hn[1] + 40 * s * math.sin(ang))
            src(c, glove); c.set_line_width(14 * s); c.stroke()

    J["draw_hand"] = lambda key: (circle(c, arms[key][2][0], arms[key][2][1], 22 * s), fill_ol(c, glove, OL * 0.9))
    back_arm = p.get("back_arm")
    if back_arm: draw_arm(back_arm)
    # Kopf
    head(c, Hd, s, look, p, face, t, skin, hair)
    for k in ("la", "ra"):
        if k != back_arm and not p.get("arm_late") == k: draw_arm(k)
    J["draw_arm_late"] = (lambda: draw_arm(p["arm_late"])) if p.get("arm_late") else None
    J["s"] = s
    return J


def head(c, Hd, s, look, p, face, t, skin, hair):
    x, y = Hd; r = 82 * s
    c.save(); c.translate(x, y); c.rotate(p["tilt"]); c.translate(-x, -y)
    hat = look.get("hat", "cap")
    # Ohren
    for sx in (-1, 1):
        circle(c, x + sx * r * 0.98, y + 8 * s, 18 * s); fill_ol(c, skin, OL * 0.8)
    if hat == "none":
        circle(c, x, y - 6 * s, r * 1.02); fill_ol(c, hair)
    circle(c, x, y, r); fill_ol(c, skin)
    if hat == "none":
        c.new_sub_path(); c.arc(x, y - 4 * s, r * 0.98, PI * 1.05, PI * 1.95); c.close_path(); src(c, hair); c.fill()
    if look.get("beard"):
        c.new_sub_path(); c.arc(x, y + 6 * s, r * 0.96, PI * 0.08, PI * 0.92); c.close_path(); fill_ol(c, hair, OL * 0.7)
    lx = face * 8 * s
    # Augen
    blink = (t % 2.9) < 0.11 or p["eyes"] == "closed"
    for sx in (-1, 1):
        ex, ey = x + sx * 30 * s + lx, y - 8 * s
        if blink:
            c.move_to(ex - 15 * s, ey); c.curve_to(ex - 5 * s, ey + 8 * s, ex + 5 * s, ey + 8 * s, ex + 15 * s, ey)
            src(c, INK); c.set_line_width(6 * s); c.stroke()
        else:
            big = 1.25 if p["eyes"] == "wide" else 1.0
            ellipse(c, ex, ey, 17 * s * big, 21 * s * big); fill_ol(c, (1, 1, 1), 5 * s)
            px = ex + face * 6 * s; py = ey + (-6 * s if p["eyes"] == "up" else 2 * s)
            circle(c, px, py, 8.5 * s); src(c, INK); c.fill()
            circle(c, px + 3 * s, py - 3 * s, 2.6 * s); src(c, (1, 1, 1)); c.fill()
        # Brauen
        by = ey - 34 * s
        tilt = {"happy": -0.15, "shock": -0.25, "focus": 0.35, "think": 0.2 * sx}.get(p["brows"], 0)
        dy = -14 * s if p["brows"] == "shock" else 0
        c.move_to(ex - 16 * s, by + dy + sx * tilt * 14 * s); c.line_to(ex + 16 * s, by + dy - sx * tilt * 14 * s)
        c.set_line_cap(cairo.LINE_CAP_ROUND); src(c, hair if hat != "none" else mix(hair, (0, 0, 0), 0.3))
        c.set_line_width(9 * s); c.stroke()
    # Nase, Wangen
    ellipse(c, x + lx + face * 6 * s, y + 18 * s, 13 * s, 11 * s); fill_ol(c, mix(skin, (0.8, 0.4, 0.3), 0.18), 4 * s)
    for sx in (-1, 1):
        circle(c, x + sx * 50 * s + lx, y + 26 * s, 13 * s); src(c, (1, 0.45, 0.45, 0.35)); c.fill()
    # Mund
    mx, my = x + lx, y + 46 * s
    m = p["mouth"]
    if m == "open":
        c.move_to(mx - 28 * s, my - 6 * s); c.curve_to(mx - 22 * s, my + 34 * s, mx + 22 * s, my + 34 * s, mx + 28 * s, my - 6 * s)
        c.close_path(); fill_ol(c, hx("#7f1d1d"), 5 * s)
        ellipse(c, mx, my + 16 * s, 12 * s, 7 * s); src(c, hx("#f87171")); c.fill()
    elif m == "o":
        ellipse(c, mx, my + 4 * s, 11 * s, 14 * s); fill_ol(c, hx("#7f1d1d"), 5 * s)
    elif m == "grin":
        c.move_to(mx - 34 * s, my - 8 * s); c.curve_to(mx - 20 * s, my + 26 * s, mx + 20 * s, my + 26 * s, mx + 34 * s, my - 8 * s)
        c.close_path(); fill_ol(c, (1, 1, 1), 5 * s)
    elif m == "flat":
        c.move_to(mx - 18 * s, my + 4 * s); c.line_to(mx + 18 * s, my + 2 * s); src(c, INK); c.set_line_width(6 * s); c.stroke()
    else:
        c.move_to(mx - 24 * s, my - 2 * s); c.curve_to(mx - 12 * s, my + 18 * s, mx + 12 * s, my + 18 * s, mx + 24 * s, my - 2 * s)
        src(c, INK); c.set_line_width(6 * s); c.set_line_cap(cairo.LINE_CAP_ROUND); c.stroke()
    # Hüte
    hc = hx(look.get("hat_color", "#ef4444"))
    if hat == "cap":
        c.new_sub_path(); c.arc(x, y - 20 * s, r * 0.98, PI, 2 * PI); c.close_path(); fill_ol(c, hc)
        ellipse(c, x + face * 70 * s, y - 22 * s, 62 * s, 15 * s); fill_ol(c, mix(hc, (0, 0, 0), 0.2))
        circle(c, x, y - 20 * s - r * 0.98, 9 * s); fill_ol(c, hc, 4 * s)
    elif hat == "helmet":
        c.new_sub_path(); c.arc(x, y - 22 * s, r * 1.08, PI, 2 * PI); c.close_path(); fill_ol(c, hc)
        rr(c, x - 6 * s, y - 22 * s - r * 1.08, 12 * s, r * 1.08, 6 * s); fill_ol(c, mix(hc, (1, 1, 1), 0.25), 4 * s)
        rr(c, x - r * 1.32, y - 34 * s, r * 2.64, 22 * s, 11 * s); fill_ol(c, mix(hc, (0, 0, 0), 0.12))
    elif hat == "beanie":
        c.new_sub_path(); c.arc(x, y - 18 * s, r * 1.0, PI, 2 * PI); c.close_path(); fill_ol(c, hc)
        rr(c, x - r * 1.04, y - 40 * s, r * 2.08, 34 * s, 14 * s); fill_ol(c, mix(hc, (0, 0, 0), 0.18))
        circle(c, x, y - 18 * s - r, 22 * s); fill_ol(c, mix(hc, (1, 1, 1), 0.6))
    elif hat == "painter":
        poly(c, [(x - r * 1.12, y - 30 * s), (x + r * 1.12, y - 30 * s), (x + r * 0.5, y - r * 1.25), (x - r * 0.6, y - r * 1.15)])
        fill_ol(c, hx(look.get("hat_color", "#ffffff")))
        rr(c, x - r * 1.14, y - 46 * s, r * 2.28, 22 * s, 6 * s); fill_ol(c, hx(look.get("hat_color", "#ffffff")), OL * 0.8)
    elif hat == "guard":
        rr(c, x - r * 0.98, y - 92 * s, r * 1.96, 64 * s, 18 * s); fill_ol(c, hc)
        ellipse(c, x + face * 26 * s, y - 28 * s, 92 * s, 15 * s); fill_ol(c, INK)
        burst(c, x, y - 62 * s, 17 * s, 10 * s, 5, -PI / 2, hx("#facc15"), 3 * s)
    c.restore()


# ------------------------------------------------------------------ Hintergründe
ENV_SKY = {
    "day": ("#7dd3fc", "#e0f2fe"), "autumn": ("#fb923c", "#fef3c7"), "night": ("#0b1033", "#1e3a8a"),
    "rain": ("#64748b", "#cbd5e1"), "snow": ("#93c5fd", "#f1f5f9"),
}


def halftone(c, x0, y0, x1, y1, col, step=30, rmax=7, fade="down"):
    src(c, col)
    j = 0
    yy = y0
    while yy < y1:
        k = (yy - y0) / max(1, (y1 - y0))
        r = rmax * (1 - k if fade == "down" else k) + 1.2
        xx = x0 + (step / 2 if j % 2 else 0)
        while xx < x1:
            c.new_sub_path(); c.arc(xx, yy, r, 0, 2 * PI)
            xx += step
        c.fill(); yy += step * 0.87; j += 1


def make_bg(env, theme, trade, rnd):
    surf = cairo.ImageSurface(cairo.FORMAT_ARGB32, W, H)
    c = cairo.Context(surf)
    if env == "indoor":
        wall = hx(theme.get("wall", "#fde68a"))
        src(c, wall); c.paint()
        halftone(c, 0, 0, W, FY, mix(wall, (0, 0, 0), 0.07), 34, 6)
        # Fenster/Bild an der Wand (links oben, hinter der Bildunterschrift-Zone vorbei)
        if trade not in ("reinigung",):
            rr(c, 70, 1000, 190, 150, 8); fill_ol(c, hx("#ffffff"))
            rr(c, 88, 1018, 154, 114, 4); src(c, hx(theme.get("accent", "#38bdf8"))); c.fill()
            poly(c, [(88, 1132), (150, 1060), (195, 1100), (242, 1050), (242, 1132)]); src(c, (1, 1, 1, 0.5)); c.fill()
        c.rectangle(0, FY - 40, W, 40); fill_ol(c, hx("#ffffff"))
        floor = hx(theme.get("floor", "#b45309"))
        c.rectangle(0, FY, W, H - FY); src(c, floor); c.fill()
        for i in range(0, W, 180):
            c.move_to(i, FY); c.line_to(i - 120, H); src(c, mix(floor, (0, 0, 0), 0.25)); c.set_line_width(4); c.stroke()
    else:
        top, bot = ENV_SKY.get(env, ENV_SKY["day"])
        g = cairo.LinearGradient(0, 0, 0, FY); g.add_color_stop_rgb(0, *hx(top)[:3]); g.add_color_stop_rgb(1, *hx(bot)[:3])
        c.set_source(g); c.paint()
        halftone(c, 0, 0, W, FY * 0.7, (1, 1, 1, 0.16) if env != "night" else (1, 1, 1, 0.06), 30, 7)
        if env == "night":
            for i in range(60):
                circle(c, rnd.uniform(0, W), rnd.uniform(0, 1150), rnd.uniform(1.5, 4)); src(c, (1, 1, 1, rnd.uniform(.4, .9))); c.fill()
            circle(c, 880, 700, 80); fill_ol(c, hx("#fef9c3"))
            circle(c, 850, 680, 16); src(c, hx("#e5e7a0")); c.fill()
        elif env in ("day", "autumn"):
            burst(c, 900, 690, 120, 92, 12, 0, hx("#fde047"))
            circle(c, 900, 690, 78); fill_ol(c, hx("#facc15"))
        for cx, cy, k in ((180, 700, 1.0), (620, 640, 0.8)):
            col = hx("#ffffff") if env in ("day", "autumn", "snow") else (hx("#475569") if env == "rain" else hx("#1e293b"))
            c.new_sub_path()
            for dx, dy, r in ((-60, 10, 50), (0, -20, 66), (64, 6, 52), (110, 20, 40)):
                c.arc(cx + dx * k, cy + dy * k, r * k, 0, 2 * PI); c.new_sub_path()
            src(c, col); c.fill()
        # Stadt-Silhouette
        sky_b = hx(bot)
        bx = 0
        while bx < W:
            bw = rnd.randint(90, 170); bh = rnd.randint(140, 360)
            c.rectangle(bx, FY - 60 - bh, bw - 8, bh + 60)
            src(c, mix(sky_b, hx("#1e293b"), 0.35 if env != "night" else 0.7)); c.fill()
            for wy in range(int(FY - 40 - bh), FY - 80, 46):
                for wx in range(bx + 16, bx + bw - 30, 36):
                    c.rectangle(wx, wy, 16, 22)
                    src(c, hx("#fde68a", 0.9) if env == "night" and rnd.random() < 0.4 else mix(sky_b, (1, 1, 1), 0.35))
                    c.fill()
            bx += bw
        # Gehweg
        c.rectangle(0, FY - 70, W, H - FY + 70)
        src(c, hx("#cbd5e1") if env != "night" else hx("#334155")); c.fill()
        for i in range(-200, W + 200, 150):
            c.move_to(i, FY - 70); c.line_to(i - 90, H); src(c, (0, 0, 0, 0.18)); c.set_line_width(5); c.stroke()
        c.move_to(0, FY - 70); c.line_to(W, FY - 70); src(c, INK); c.set_line_width(OL); c.stroke()
        if env == "snow":
            c.rectangle(0, FY - 80, W, 30); src(c, (1, 1, 1)); c.fill()
    return surf


def weather(c, env, t, rnd_seed):
    r = random.Random(rnd_seed)
    if env == "rain":
        src(c, (0.8, 0.88, 1, 0.55)); c.set_line_width(4)
        for i in range(70):
            x0 = r.uniform(0, W + 300); sp = r.uniform(1500, 2200); y0 = r.uniform(0, H)
            y = (y0 + sp * t) % (H + 100) - 50; x = (x0 - 0.3 * sp * t) % (W + 300) - 150
            c.move_to(x, y); c.line_to(x - 12, y + 40); c.stroke()
    elif env == "snow":
        for i in range(60):
            x0 = r.uniform(0, W); sp = r.uniform(60, 160); y0 = r.uniform(0, H); ph = r.uniform(0, 6)
            y = (y0 + sp * t) % H; x = x0 + 30 * math.sin(t * 1.3 + ph)
            circle(c, x, y, r.uniform(4, 9)); src(c, (1, 1, 1, 0.9)); c.fill()
    elif env == "autumn":
        cols = [hx("#ea580c"), hx("#dc2626"), hx("#ca8a04"), hx("#92400e")]
        for i in range(16):
            x0 = r.uniform(0, W); sp = r.uniform(90, 200); y0 = r.uniform(0, H); ph = r.uniform(0, 6)
            y = (y0 + sp * t) % H; x = x0 + 60 * math.sin(t * 1.1 + ph)
            c.save(); c.translate(x, y); c.rotate(t * 2 + ph)
            ellipse(c, 0, 0, 20, 10); fill_ol(c, cols[i % 4], 3); c.restore()


# ------------------------------------------------------------------ Requisiten
def van(c, x, y_floor, wdt, col, face=1, open_back=False, label=None, wheel_t=0.0):
    """Transporter Seitenansicht; x = linke Kante, face = Fahrtrichtung."""
    hgt = 330
    body_y = y_floor - 70 - hgt
    c.save()
    if face < 0:
        c.translate(2 * x + wdt, 0); c.scale(-1, 1)
    rr(c, x, body_y, wdt * 0.74, hgt, 22); fill_ol(c, col)
    cab_x = x + wdt * 0.72
    c.move_to(cab_x, body_y + 70); c.line_to(cab_x + wdt * 0.16, body_y + 70)
    c.line_to(x + wdt, body_y + 190); c.line_to(x + wdt, body_y + hgt); c.line_to(cab_x, body_y + hgt); c.close_path()
    fill_ol(c, col)
    poly(c, [(cab_x + 22, body_y + 92), (cab_x + wdt * 0.15, body_y + 92), (x + wdt - 22, body_y + 186), (cab_x + 22, body_y + 186)])
    fill_ol(c, hx("#bae6fd"), OL * 0.8)
    rr(c, x + wdt - 34, body_y + hgt - 70, 30, 30, 8); fill_ol(c, hx("#fde047"), 4)
    c.rectangle(x - 4, body_y + hgt - 20, wdt + 8, 26); fill_ol(c, hx("#334155"))
    for wx in (x + wdt * 0.2, x + wdt * 0.82):
        circle(c, wx, y_floor - 62, 62); fill_ol(c, INK)
        circle(c, wx, y_floor - 62, 28); fill_ol(c, hx("#cbd5e1"), 4)
        a = wheel_t * 6
        c.move_to(wx + 26 * math.cos(a), y_floor - 62 + 26 * math.sin(a)); c.line_to(wx - 26 * math.cos(a), y_floor - 62 - 26 * math.sin(a))
        src(c, INK); c.set_line_width(4); c.stroke()
    c.restore()
    if label:
        lx = x + wdt * 0.37 if face > 0 else x + wdt * 0.63
        text(c, label, lx, body_y + hgt * 0.42, 70, (1, 1, 1), stroke=INK, sw=5, maxw=wdt * 0.6)
    return body_y, hgt


def box(c, x, y, w, h, col=None, label=None, rotv=0.0):
    col = col or hx("#d39b5e")
    c.save(); c.translate(x, y); c.rotate(rotv)
    rr(c, -w / 2, -h / 2, w, h, 6); fill_ol(c, col)
    c.rectangle(-w * 0.12, -h / 2, w * 0.24, h); src(c, mix(col, (1, 1, 1), 0.35)); c.fill()
    c.move_to(-w / 2, -h / 2 + h * 0.18); c.line_to(w / 2, -h / 2 + h * 0.18); src(c, (0, 0, 0, 0.25)); c.set_line_width(3); c.stroke()
    if label:
        text(c, label, 0, h * 0.15, h * 0.32, INK, maxw=w * 0.8)
    c.restore()


def cone(c, x, y, k=1.0):
    poly(c, [(x - 52 * k, y), (x + 52 * k, y), (x + 16 * k, y - 150 * k), (x - 16 * k, y - 150 * k)])
    fill_ol(c, hx("#f97316"))
    for yy, ww in ((-62, 36), (-108, 24)):
        poly(c, [(x - ww * k, y + yy * k), (x + ww * k, y + yy * k), (x + (ww - 6) * k, y + (yy - 18) * k), (x - (ww - 6) * k, y + (yy - 18) * k)])
        src(c, (1, 1, 1)); c.fill()
    rr(c, x - 66 * k, y - 10 * k, 132 * k, 20 * k, 6 * k); fill_ol(c, hx("#ea580c"))


def sparkle(c, x, y, r, col=(1, 1, 1)):
    c.move_to(x, y - r); c.curve_to(x, y, x, y, x + r, y); c.curve_to(x, y, x, y, x, y + r)
    c.curve_to(x, y, x, y, x - r, y); c.curve_to(x, y, x, y, x, y - r); c.close_path(); fill_ol(c, col, 3)


def walk_pose(t, B, speed=1.0, amp=0.42):
    ph = math.sin(t * 2 * PI / B * speed)
    return dict(ll=(-amp * ph, -amp * ph - 0.25 * max(0, ph)), rl=(amp * ph, amp * ph - 0.25 * max(0, -ph)),
                la=(0.35 * ph - 0.1, 0.35 * ph - 0.25), ra=(-0.35 * ph + 0.1, -0.35 * ph + 0.25), bob=-10 * abs(ph))


# ------------------------------------------------------------------ Gewerke
class Trade:
    home = 330
    face = 1
    env = "day"
    fx_pos = (760, 980)
    look = {}

    def __init__(self, R): self.R = R

    def back(self, c, t, prog, st): pass           # Requisiten hinter der Figur
    def front(self, c, t, prog, st, J): pass       # Werkzeug / vorne
    def work(self, t, prog, st): return self.home, {}, self.face   # Figur-Position, Pose, Blickrichtung
    def events(self, t0, t1): return []            # Treffer-Zeitpunkte (für Ton/Wackeln)


class Maler(Trade):
    env = "indoor"; home = 360; fx_pos = (800, 900)
    look = dict(shirt="#38bdf8", overalls="#f8fafc", pants="#f1f5f9", hat="painter", hat_color="#ffffff")
    X0, X1, Y0 = 560, 1050, 760

    def roller(self, t, prog):
        B = self.R.B
        col = self.X0 + 40 + prog * (self.X1 - self.X0 - 80)
        ph = (t / B) % 2
        k = ph if ph < 1 else 2 - ph
        ry = lerp(FY - 150, self.Y0 + 90, ease_io(k))
        return col, ry

    def back(self, c, t, prog, st):
        old = hx("#d6cfc2"); new = hx(self.R.theme.get("paint", self.R.theme["accent"]))
        c.rectangle(self.X0, self.Y0, self.X1 - self.X0, FY - 40 - self.Y0); fill_ol(c, old)
        rnd = random.Random(5)
        for i in range(9):
            ellipse(c, rnd.uniform(self.X0 + 40, self.X1 - 40), rnd.uniform(self.Y0 + 60, FY - 120), rnd.uniform(20, 50), rnd.uniform(14, 36))
            src(c, (0.45, 0.4, 0.3, 0.35)); c.fill()
        c.move_to(self.X0 + 300, self.Y0 + 40); c.line_to(self.X0 + 330, self.Y0 + 140); c.line_to(self.X0 + 310, self.Y0 + 220)
        src(c, (0.3, 0.25, 0.2, 0.6)); c.set_line_width(4); c.stroke()
        col, ry = self.roller(t, prog) if st["action"] == "work" else (self.X0 + 40 + prog * (self.X1 - self.X0 - 80), FY)
        px = col + 46 if prog > 0 else self.X0
        if prog > 0:
            c.move_to(self.X0, self.Y0)
            n = 20
            for i in range(n + 1):
                yy = self.Y0 + (FY - 40 - self.Y0) * i / n
                c.line_to(px + 10 * math.sin(i * 1.7), yy)
            c.line_to(self.X0, FY - 40); c.close_path(); src(c, new); c.fill()
            for i in range(4):
                dx = self.X0 + 30 + i * (px - self.X0 - 40) / 4
                dl = 30 + 20 * ((i * 7) % 3)
                rr(c, dx, self.Y0, 14, dl, 7); src(c, new); c.fill()
        c.rectangle(self.X0, self.Y0, self.X1 - self.X0, FY - 40 - self.Y0); src(c, INK); c.set_line_width(OL); c.stroke()
        # Farbeimer
        bx = 520
        poly(c, [(bx - 60, FY - 110), (bx + 60, FY - 110), (bx + 50, FY), (bx - 50, FY)]); fill_ol(c, hx("#e2e8f0"))
        ellipse(c, bx, FY - 110, 60, 16); fill_ol(c, new)
        if st["action"] != "work":
            # Rolle lehnt an der Wand
            c.move_to(self.X1 - 40, FY - 20); c.line_to(self.X1 - 140, self.Y0 + 260); src(c, INK); c.set_line_width(16); c.stroke()
            c.move_to(self.X1 - 40, FY - 20); c.line_to(self.X1 - 140, self.Y0 + 260); src(c, hx("#a16207")); c.set_line_width(8); c.stroke()
            rr(c, self.X1 - 200, self.Y0 + 220, 120, 44, 14); fill_ol(c, new)

    def work(self, t, prog, st):
        col, ry = self.roller(t, prog)
        x = min(col - 230, 470)
        S = (x + 68, FY - 236 - 186)
        R_ = (col, ry + 40)
        d = math.hypot(R_[0] - S[0], R_[1] - S[1]); ux, uy = (R_[0] - S[0]) / d, (R_[1] - S[1]) / d
        hand = (S[0] + ux * 170, S[1] + uy * 170 + 30)
        hand2 = (hand[0] - ux * 95, hand[1] - uy * 95 + 10)
        self._hand = hand; self._roll = (col, ry)
        return x, dict(ra_t=hand, la_t=hand2, la_bend=1, mouth="smile", brows="focus", lean=0.05,
                       ll=(-0.12, -0.05), rl=(0.14, 0.05)), 1

    def front(self, c, t, prog, st, J):
        if st["action"] != "work": return
        col, ry = self._roll
        hx_, hy = J["rh"]
        ang = math.atan2(ry - hy, col - hx_)
        ex, ey = hx_ - 70 * math.cos(ang), hy - 70 * math.sin(ang)
        c.set_line_cap(cairo.LINE_CAP_ROUND)
        c.move_to(ex, ey); c.line_to(col - 10, ry + 30); src(c, INK); c.set_line_width(20); c.stroke()
        c.move_to(ex, ey); c.line_to(col - 10, ry + 30); src(c, hx("#a16207")); c.set_line_width(10); c.stroke()
        J["draw_hand"]("ra"); J["draw_hand"]("la")
        new = hx(self.R.theme.get("paint", self.R.theme["accent"]))
        rr(c, col - 22, ry - 70, 50, 140, 16); fill_ol(c, new)
        c.move_to(col - 10, ry + 30); c.line_to(col + 6, ry); src(c, INK); c.set_line_width(8); c.stroke()


class Abbruch(Trade):
    env = "day"; home = 380; fx_pos = (660, 1130)
    look = dict(shirt="#f97316", pants="#1f2937", hat="helmet", hat_color="#facc15", gloves="#78350f")
    X0, X1, Y0 = 610, 1060, 860
    P = (640, 1260)

    def bricks(self):
        if hasattr(self, "_b"): return self._b
        rnd = random.Random(3); out = []
        bw, bh = 90, 46
        y = FY - 40 - bh; row = 0
        while y >= self.Y0:
            x = self.X0 - (bw / 2 if row % 2 else 0)
            while x < self.X1:
                x0, x1 = max(x, self.X0), min(x + bw, self.X1)
                cx, cy = (x0 + x1) / 2, y + bh / 2
                d = math.hypot((cx - self.P[0]) * 0.8, cy - self.P[1]) + rnd.uniform(-40, 40)
                out.append((x0, y, x1 - x0, bh, d, rnd.uniform(-0.08, 0.08)))
                x += bw
            y -= bh; row += 1
        self._b = out; return out

    def back(self, c, t, prog, st):
        c.rectangle(self.X0, self.Y0, self.X1 - self.X0, FY - 40 - self.Y0); fill_ol(c, hx("#3f3f46"))
        rad = prog * 560
        base = hx("#b45309")
        for x, y, w, h, d, v in self.bricks():
            if d < rad: continue
            c.rectangle(x + 3, y + 3, w - 6, h - 6); src(c, mix(base, (0, 0, 0), 0.15 + v)); c.fill_preserve()
            src(c, hx("#78350f")); c.set_line_width(3); c.stroke()
        c.rectangle(self.X0, self.Y0, self.X1 - self.X0, FY - 40 - self.Y0); src(c, INK); c.set_line_width(OL); c.stroke()
        # Schuttberg
        if prog > 0:
            hgt = 40 + prog * 150
            c.move_to(560, FY + 10); c.curve_to(620, FY - hgt, 820, FY - hgt, 900, FY + 10); c.close_path(); fill_ol(c, hx("#a8a29e"))
            rnd = random.Random(9)
            for i in range(int(4 + prog * 10)):
                bx = rnd.uniform(600, 860); by = FY - rnd.uniform(0, hgt * 0.7)
                c.save(); c.translate(bx, by); c.rotate(rnd.uniform(-0.6, 0.6)); c.rectangle(-22, -11, 44, 22); fill_ol(c, base, 3); c.restore()

    def swing(self, t):
        B = self.R.B; p = (t / B) % 1
        if p < 0.55: a = lerp(1.6, 3.55, ease_io(p / 0.55))
        elif p < 0.68: a = lerp(3.55, 1.45, (p - 0.55) / 0.13)
        else: a = 1.45
        return a, p

    def work(self, t, prog, st):
        a, p = self.swing(t)
        self._a = a
        return self.home, dict(ra=(a, a + 0.25), la=(a - 0.35, a), mouth="open" if 0.55 < p < 0.8 else "flat",
                               brows="focus", lean=0.1 if p > 0.6 else -0.06, ll=(-0.25, -0.1), rl=(0.3, 0.1),
                               eyes="wide" if 0.6 < p < 0.8 else "open"), 1

    def events(self, t0, t1):
        B = self.R.B; ev = []
        k = math.floor(t0 / B)
        while k * B + 0.66 * B < t1:
            tt = k * B + 0.66 * B
            if tt >= t0: ev.append(tt)
            k += 1
        return ev

    def front(self, c, t, prog, st, J):
        if st["action"] != "work": return
        a = self._a
        hx_, hy = J["rh"]
        dx, dy = math.sin(a + 0.25), math.cos(a + 0.25)
        tip = (hx_ + dx * 210, hy + dy * 210)
        c.set_line_cap(cairo.LINE_CAP_ROUND)
        for col, w in ((INK, 24), (hx("#92400e"), 12)):
            c.move_to(hx_ - dx * 40, hy - dy * 40); c.line_to(*tip); src(c, col); c.set_line_width(w); c.stroke()
        c.save(); c.translate(*tip); c.rotate(-a - 0.25)
        rr(c, -52, -26, 104, 52, 8); fill_ol(c, hx("#52525b")); c.restore()
        J["draw_hand"]("ra"); J["draw_hand"]("la")
        # Splitter nach jedem Treffer
        B = self.R.B; p = (t / B) % 1; dt = (p - 0.66) * B
        if 0 <= dt < 0.6:
            rnd = random.Random(int(t / B))
            for i in range(9):
                vx = rnd.uniform(-350, 250); vy = rnd.uniform(-650, -200)
                x = self.P[0] + vx * dt; y = self.P[1] + vy * dt + 1400 * dt * dt
                c.save(); c.translate(x, y); c.rotate(dt * rnd.uniform(-8, 8))
                c.rectangle(-16, -9, 32, 18); fill_ol(c, hx("#b45309"), 3); c.restore()
            for i in range(5):
                circle(c, self.P[0] + 30 + i * 18 + dt * 120, self.P[1] - dt * 200 - i * 10, 20 + dt * 90)
                src(c, (0.85, 0.82, 0.78, max(0, 0.6 - dt))); c.fill()


class Bodenleger(Trade):
    env = "indoor"; home = 210; fx_pos = (760, 1160)
    look = dict(shirt="#ef4444", overalls="#1d4ed8", pants="#1d4ed8", hat="cap", hat_color="#1d4ed8")
    N = 9

    def quad(self, i):
        bk = (330, 1040, 1300); fr = (120, 1100, 1900)
        def at(edge, k): return edge[0] + (edge[1] - edge[0]) * k
        k0, k1 = i / self.N, (i + 1) / self.N
        return [(at(bk, k0), bk[2]), (at(bk, k1), bk[2]), (at(fr, k1), fr[2]), (at(fr, k0), fr[2])]

    def back(self, c, t, prog, st):
        poly(c, [(330, 1300), (1040, 1300), (1100, 1900), (120, 1900)]); fill_ol(c, hx("#9ca3af"))
        rnd = random.Random(2)
        for i in range(70):
            circle(c, rnd.uniform(260, 1060), rnd.uniform(1320, 1890), rnd.uniform(2, 5)); src(c, (0, 0, 0, 0.18)); c.fill()
        wood = hx(self.R.theme.get("wood", "#d4a373"))
        laid = prog * self.N
        for i in range(self.N):
            if i + 1 > laid + 1: break
            k = clamp(laid - i)
            if k <= 0: continue
            q = self.quad(i)
            dy = -260 * (1 - back_out(k)) if k < 1 else 0
            q = [(x, y + dy) for x, y in q]
            poly(c, q); fill_ol(c, mix(wood, (0, 0, 0), 0.08 * (i % 3)), 5)
            for g in (0.3, 0.65):
                c.move_to(lerp(q[0][0], q[1][0], g), q[0][1])
                c.curve_to(lerp(q[0][0], q[1][0], g) + 6, lerp(q[0][1], q[3][1], 0.3),
                           lerp(q[3][0], q[2][0], g) - 6, lerp(q[0][1], q[3][1], 0.6), lerp(q[3][0], q[2][0], g), q[3][1])
                src(c, mix(wood, (0, 0, 0), 0.3)); c.set_line_width(3); c.stroke()
        # Dielen-Stapel
        for j in range(max(0, 4 - int(laid / 2.3))):
            rr(c, 860, 1180 - j * 26, 200, 26, 6); fill_ol(c, wood, 4)

    def work(self, t, prog, st):
        B = self.R.B; p = (t / B) % 1
        i = min(self.N - 1, int(prog * self.N))
        q = self.quad(i)
        tx = (q[2][0] + q[3][0]) / 2 * 0.55 + (q[0][0] + q[1][0]) / 2 * 0.45
        tx = min(tx, self.home + 330)
        ty = 1560 - (90 if p < 0.5 else 90 * (1 - ease_out((p - 0.5) / 0.2)))
        self._tgt = (tx, ty)
        x = self.home + 40
        return x, dict(bob=110, ll=(1.25, 0.0), rl=(0.2, -1.4), ra_t=(tx, ty), la_t=(x + 120, 1560),
                       lean=0.22, mouth="smile", brows="focus"), 1

    def events(self, t0, t1):
        B = self.R.B; ev = []; k = math.floor(t0 / B)
        while k * B + 0.7 * B < t1:
            tt = k * B + 0.7 * B
            if tt >= t0: ev.append(tt)
            k += 1
        return ev

    def front(self, c, t, prog, st, J):
        if st["action"] != "work": return
        hx_, hy = J["rh"]
        ang = math.atan2(hy - J["re"][1], hx_ - J["re"][0])
        tip = (hx_ + 70 * math.cos(ang), hy + 70 * math.sin(ang))
        c.set_line_cap(cairo.LINE_CAP_ROUND)
        for col, w in ((INK, 22), (hx("#a16207"), 10)):
            c.move_to(hx_ - 30 * math.cos(ang), hy - 30 * math.sin(ang)); c.line_to(*tip); src(c, col); c.set_line_width(w); c.stroke()
        c.save(); c.translate(*tip); c.rotate(ang); rr(c, -26, -40, 52, 80, 14); fill_ol(c, hx("#111827")); c.restore()
        J["draw_hand"]("ra")


class Reinigung(Trade):
    env = "indoor"; home = 360; fx_pos = (790, 860)
    look = dict(shirt="#2dd4bf", pants="#334155", hat="cap", hat_color="#0f766e")
    X0, X1, Y0, Y1 = 560, 1030, 760, 1380

    def spots(self):
        rnd = random.Random(11)
        return [(rnd.uniform(self.X0 + 50, self.X1 - 50), rnd.uniform(self.Y0 + 50, self.Y1 - 50), rnd.uniform(22, 48)) for _ in range(16)]

    def sq(self, t, prog):
        B = self.R.B; ph = (t / B) % 2; k = ph if ph < 1 else 2 - ph
        x = self.X0 + 40 + prog * (self.X1 - self.X0 - 80)
        y = lerp(self.Y1 - 70, self.Y0 + 70, ease_io(k))
        return x, y

    def back(self, c, t, prog, st):
        rr(c, self.X0 - 30, self.Y0 - 30, self.X1 - self.X0 + 60, self.Y1 - self.Y0 + 60, 10); fill_ol(c, hx("#ffffff"))
        c.rectangle(self.X0, self.Y0, self.X1 - self.X0, self.Y1 - self.Y0); fill_ol(c, hx("#bae6fd"))
        for k in (0.2, 0.5):
            poly(c, [(self.X0 + (self.X1 - self.X0) * k, self.Y1), (self.X0 + (self.X1 - self.X0) * (k + 0.12), self.Y1),
                     (self.X0 + (self.X1 - self.X0) * (k + 0.35), self.Y0), (self.X0 + (self.X1 - self.X0) * (k + 0.23), self.Y0)])
            src(c, (1, 1, 1, 0.45)); c.fill()
        sx, _ = self.sq(t, prog)
        cleaned_x = sx if prog > 0 else self.X0
        if prog >= 0.999: cleaned_x = self.X1 + 100
        for x, y, r in self.spots():
            if x < cleaned_x - 20: continue
            ellipse(c, x, y, r, r * 0.8); src(c, (0.45, 0.33, 0.18, 0.55)); c.fill()
            circle(c, x + r * 0.6, y - r * 0.4, r * 0.35); src(c, (0.45, 0.33, 0.18, 0.4)); c.fill()
        mxx = (self.X0 + self.X1) / 2; myy = (self.Y0 + self.Y1) / 2
        c.rectangle(mxx - 9, self.Y0, 18, self.Y1 - self.Y0); fill_ol(c, hx("#ffffff"), 4)
        c.rectangle(self.X0, myy - 9, self.X1 - self.X0, 18); fill_ol(c, hx("#ffffff"), 4)
        c.rectangle(self.X0, self.Y0, self.X1 - self.X0, self.Y1 - self.Y0); src(c, INK); c.set_line_width(OL); c.stroke()
        rr(c, self.X0 - 50, self.Y1 + 30, self.X1 - self.X0 + 100, 30, 6); fill_ol(c, hx("#f1f5f9"))
        if prog > 0.2:
            n = int(prog * 6)
            for i in range(n):
                x = self.X0 + 60 + i * 70; y = self.Y0 + 90 + (i * 137) % 420
                s = 20 + 12 * math.sin(t * 8 + i)
                if x < cleaned_x - 30: sparkle(c, x, y, s)
        # Eimer mit Schaum
        bx = 500
        poly(c, [(bx - 62, FY - 120), (bx + 62, FY - 120), (bx + 52, FY), (bx - 52, FY)]); fill_ol(c, hx(self.R.theme["accent"]))
        for i in range(6):
            circle(c, bx - 50 + i * 20, FY - 126 - 10 * math.sin(i * 2 + t * 3), 18); fill_ol(c, (1, 1, 1), 4)

    def work(self, t, prog, st):
        sx, sy = self.sq(t, prog)
        x = min(sx - 260, 460)
        self._s = (sx, sy)
        return x, dict(ra_t=(sx - 90, sy + 50), la=(-0.6, 0.9), mouth="grin", brows="happy", lean=0.04), 1

    def front(self, c, t, prog, st, J):
        if st["action"] != "work": return
        sx, sy = self._s
        hx_, hy = J["rh"]
        c.set_line_cap(cairo.LINE_CAP_ROUND)
        for col, w in ((INK, 20), (hx("#0f766e"), 10)):
            c.move_to(hx_, hy); c.line_to(sx - 4, sy); src(c, col); c.set_line_width(w); c.stroke()
        rr(c, sx - 14, sy - 70, 28, 140, 10); fill_ol(c, hx("#1f2937"))
        J["draw_hand"]("ra")
        for i in range(3):
            yy = sy + (i - 1) * 50
            circle(c, sx + 26 + 8 * math.sin(t * 20 + i), yy, 7); src(c, (1, 1, 1, 0.8)); c.fill()


class Umzug(Trade):
    env = "day"; home = 300; fx_pos = (420, 1000)
    look = dict(shirt="#a78bfa", pants="#1e293b", hat="cap", hat_color="#1e293b")
    N = 8

    def cycle(self, t):
        B = self.R.B; p = (t / (2 * B)) % 1
        if p < 0.12: return 200, 0, p, "pick"
        if p < 0.5: return lerp(200, 560, ease_io((p - 0.12) / 0.38)), 1, p, "carry"
        if p < 0.6: return 560, 1, p, "put"
        return lerp(560, 200, ease_io((p - 0.6) / 0.4)), -1, p, "back"

    def back(self, c, t, prog, st):
        col = hx(self.R.theme.get("van", "#f8fafc"))
        by, hgt = van(c, 640, FY + 10, 430, col, face=1, label=None)
        rr(c, 650, by + 18, 260, hgt - 50, 12); fill_ol(c, hx("#334155"))
        loaded = int(prog * self.N + 1e-6)
        for i in range(loaded):
            bx = 700 + (i % 3) * 78; byy = by + hgt - 72 - (i // 3) * 64
            box(c, bx, byy, 72, 60)
        poly(c, [(560, FY + 10), (650, by + hgt - 30), (660, by + hgt - 4), (590, FY + 10)]); fill_ol(c, hx("#94a3b8"))
        left = self.N - loaded - (1 if st["action"] == "work" and self.cycle(t)[3] in ("carry", "put") else 0)
        for i in range(max(0, left)):
            box(c, 70 + (i % 2) * 90, FY - 40 - (i // 2) * 72, 84, 70, rotv=0.04 * ((i % 3) - 1))

    def work(self, t, prog, st):
        x, d, p, ph = self.cycle(t)
        B = self.R.B
        pose = walk_pose(t, B, 2.0, 0.35) if ph in ("carry", "back") else dict(bob=12 if ph in ("pick", "put") else 0)
        if ph in ("carry", "put"):
            self._box = (x + 30 * d, FY - 400)
            pose.update(ra_t=(x + 70, FY - 395), la_t=(x - 10, FY - 395), la_bend=1, ra_bend=1, mouth="open" if ph == "carry" else "smile")
        else:
            self._box = None
            pose.update(mouth="smile")
        face = 1 if d >= 0 else -1
        return x, pose, face

    def front(self, c, t, prog, st, J):
        if st["action"] == "work" and self._box:
            box(c, self._box[0], self._box[1], 140, 112, label=self.R.theme.get("box_label", "UMZUG"))
            J["draw_hand"]("ra"); J["draw_hand"]("la")


class Lieferung(Trade):
    env = "day"; home = 520; fx_pos = (860, 1000)
    look = dict(shirt="#facc15", pants="#374151", hat="cap", hat_color="#ca8a04")

    def cycle(self, t):
        B = self.R.B; p = (t / (2 * B)) % 1
        if p < 0.45: return lerp(470, 720, ease_io(p / 0.45)), 1, p, "carry"
        if p < 0.65: return 720, 1, p, "give"
        return lerp(720, 470, ease_io((p - 0.65) / 0.35)), -1, p, "back"

    def back(self, c, t, prog, st):
        # Haus mit Tür
        wallc = hx(self.R.theme.get("house", "#fca5a5"))
        c.rectangle(780, 940, 320, FY - 70 - 940 + 2); fill_ol(c, wallc)
        poly(c, [(760, 950), (1100, 830), (1100, 950)]); fill_ol(c, hx("#7f1d1d"))
        rr(c, 830, 1250, 170, FY - 70 - 1250, 10); fill_ol(c, hx("#78350f"))
        circle(c, 975, 1420, 9); fill_ol(c, hx("#facc15"), 3)
        rr(c, 1020, 1330, 34, 50, 6); fill_ol(c, (1, 1, 1), 4); circle(c, 1037, 1355, 8); src(c, INK); c.fill()
        rr(c, 860, 1020, 120, 120, 6); fill_ol(c, hx("#bae6fd"))
        n = int(prog * 4 + 1e-6)
        for i in range(n):
            box(c, 780 - (i % 2) * 70, FY - 100 - (i // 2) * 60, 66, 56, col=hx("#c08457"))
        # Lieferwagen
        vx = 20
        if st["index"] == 0:
            k = clamp(t / (1.2 * self.R.B))
            vx = lerp(-520, 20, ease_out(k))
            if k < 1:
                for i in range(6):
                    yy = 1260 + i * 50
                    c.move_to(vx - 40 - i * 30, yy); c.line_to(vx - 260 - i * 30, yy); src(c, INK); c.set_line_width(6); c.stroke()
        van(c, vx, FY + 10, 470, hx(self.R.theme.get("van", "#ffffff")), face=1,
            label=self.R.theme.get("van_label", "EXPRESS"), wheel_t=t if st["index"] == 0 else 0)

    def work(self, t, prog, st):
        x, d, p, ph = self.cycle(t)
        B = self.R.B
        pose = walk_pose(t, B, 2.0, 0.35) if ph != "give" else dict(mouth="grin")
        if ph in ("carry", "give"):
            reach = 70 if ph == "carry" else 150
            self._pkg = (x + reach + 10, FY - 430)
            pose.update(ra_t=(x + reach + 50, FY - 420), la_t=(x + reach - 30, FY - 420), la_bend=1, ra_bend=1, mouth="smile" if ph == "carry" else "grin")
        else:
            self._pkg = None
        return x, pose, 1 if d >= 0 else -1

    def front(self, c, t, prog, st, J):
        if st["action"] == "work" and self._pkg:
            box(c, self._pkg[0], self._pkg[1], 120, 90, col=hx("#c08457"))
            rr(c, self._pkg[0] - 24, self._pkg[1] - 20, 48, 30, 4); fill_ol(c, (1, 1, 1), 3)
            J["draw_hand"]("ra"); J["draw_hand"]("la")


class Sicherheit(Trade):
    env = "night"; home = 360; fx_pos = (780, 940)
    look = dict(shirt="#1e293b", pants="#0f172a", hat="guard", hat_color="#1e293b", chest="SECURITY", long_sleeves=True)

    def pos(self, t):
        B = self.R.B; k = math.sin(t * 2 * PI / (8 * B))
        return 360 + 150 * k, math.cos(t * 2 * PI / (8 * B))

    def back(self, c, t, prog, st):
        c.rectangle(600, 1000, 480, FY - 70 - 1000 + 2); fill_ol(c, hx("#334155"))
        for i in range(7):
            c.rectangle(660, 1160 + i * 52, 360, 44); fill_ol(c, hx("#475569"), 4)
        rr(c, 700, 1040, 280, 70, 8); fill_ol(c, hx("#1e293b"))
        text(c, self.R.theme.get("sign", "LAGER"), 840, 1076, 56, hx("#e2e8f0"), maxw=240)
        # Zaun
        for x in range(0, 620, 60):
            c.rectangle(x, FY - 330, 14, 270); src(c, hx("#64748b")); c.fill()
        c.rectangle(0, FY - 300, 600, 10); src(c, hx("#64748b")); c.fill(); c.rectangle(0, FY - 140, 600, 10); c.fill()
        # Laterne
        c.rectangle(560, 960, 18, FY - 70 - 960); fill_ol(c, hx("#1f2937"))
        rr(c, 540, 930, 58, 46, 10); fill_ol(c, hx("#fde68a"))
        poly(c, [(550, 976), (588, 976), (680, FY - 70), (458, FY - 70)]); src(c, (1, 0.95, 0.6, 0.12)); c.fill()

    def work(self, t, prog, st):
        x, v = self.pos(t)
        face = 1 if v >= 0 else -1
        B = self.R.B
        pose = walk_pose(t, B, 1.0, 0.25)
        sweep = math.sin(t * 2 * PI / (4 * B))
        self._beam = 0.25 + 0.25 * sweep
        pose.update(ra_t=(x + face * 150, FY - 400 + 30 * sweep), mouth="flat", brows="focus", eyes="open")
        if face < 0: pose.update(la_t=(x + face * 150, FY - 400 + 30 * sweep), ra_t=None, ra=(0.15, 0.05))
        self._face = face
        return x, pose, face

    def front(self, c, t, prog, st, J):
        if st["action"] != "work": return
        face = self._face
        key = "rh" if face > 0 else "lh"
        hx_, hy = J[key]
        a = self._beam
        L = 900
        a1 = (a - 0.18); a2 = (a + 0.18)
        p1 = (hx_ + face * L * math.cos(a1), hy + L * math.sin(a1)); p2 = (hx_ + face * L * math.cos(a2), hy + L * math.sin(a2))
        g = cairo.RadialGradient(hx_, hy, 10, hx_, hy, L)
        g.add_color_stop_rgba(0, 1, 0.96, 0.6, 0.65); g.add_color_stop_rgba(1, 1, 0.96, 0.6, 0.0)
        poly(c, [(hx_, hy), p1, p2]); c.set_source(g); c.fill()
        c.save(); c.translate(hx_, hy); c.scale(face, 1); c.rotate(a)
        rr(c, -10, -16, 70, 32, 8); fill_ol(c, hx("#111827")); c.rectangle(52, -20, 16, 40); fill_ol(c, hx("#fde68a"), 4)
        c.restore()
        J["draw_hand"]("ra" if face > 0 else "la")


class Entruempelung(Trade):
    env = "day"; home = 420; fx_pos = (820, 1150)
    look = dict(shirt="#15803d", pants="#422006", hat="beanie", hat_color="#b91c1c", gloves="#facc15")
    ITEMS = ["box", "chair", "lamp", "tire", "bag", "frame", "box", "plant"]

    def item(self, c, kind, x, y, r=0.0, k=1.0):
        c.save(); c.translate(x, y); c.rotate(r); c.scale(k, k)
        if kind == "box": box(c, 0, 0, 90, 70)
        elif kind == "chair":
            c.rectangle(-40, -10, 80, 16); fill_ol(c, hx("#92400e"), 5); c.rectangle(-40, -80, 14, 70); fill_ol(c, hx("#92400e"), 5)
            c.rectangle(-36, 6, 10, 50); fill_ol(c, hx("#92400e"), 5); c.rectangle(26, 6, 10, 50); fill_ol(c, hx("#92400e"), 5)
        elif kind == "lamp":
            c.rectangle(-5, -40, 10, 80); fill_ol(c, hx("#52525b"), 5)
            poly(c, [(-34, -40), (34, -40), (20, -80), (-20, -80)]); fill_ol(c, hx("#fde68a"), 5)
        elif kind == "tire":
            circle(c, 0, 0, 44); fill_ol(c, hx("#27272a"), 5); circle(c, 0, 0, 18); fill_ol(c, hx("#a1a1aa"), 4)
        elif kind == "bag":
            c.move_to(-40, 36); c.curve_to(-56, -20, -20, -50, 0, -46); c.curve_to(20, -50, 56, -20, 40, 36); c.close_path(); fill_ol(c, hx("#1f2937"), 5)
        elif kind == "frame":
            c.rectangle(-44, -34, 88, 68); fill_ol(c, hx("#a16207"), 5); c.rectangle(-30, -20, 60, 40); fill_ol(c, hx("#7dd3fc"), 4)
        else:
            poly(c, [(-26, 40), (26, 40), (20, 0), (-20, 0)]); fill_ol(c, hx("#c2410c"), 5)
            for a in (-0.6, 0, 0.6):
                ellipse(c, 28 * math.sin(a), -30, 14, 34); fill_ol(c, hx("#16a34a"), 4)
        c.restore()

    def back(self, c, t, prog, st):
        n = len(self.ITEMS)
        gone = int(prog * n + 1e-6)
        # Container (Mulde)
        cc = hx(self.R.theme.get("container", "#f97316"))
        for i in range(gone):
            self.item(c, self.ITEMS[i], 720 + (i % 4) * 80, 1360 - (i // 4) * 30, 0.3 * ((i % 3) - 1), 0.9)
        poly(c, [(650, 1360), (1070, 1360), (1020, FY + 5), (700, FY + 5)]); fill_ol(c, cc)
        for x in (740, 820, 900, 980):
            c.move_to(x, 1370); c.line_to(x - 6 if x < 860 else x + 6, FY); src(c, mix(cc, (0, 0, 0), 0.3)); c.set_line_width(8); c.stroke()
        # Haufen
        thrown_now = st["action"] == "work" and ((t / self.R.B) % 1) > 0.3
        for j, i in enumerate(range(n - 1, gone - 1 + (1 if thrown_now else 0), -1)):
            px = 90 + (i % 3) * 80; py = FY - 50 - (i // 3) * 70
            self.item(c, self.ITEMS[i], px, py, 0.15 * ((i % 3) - 1))

    def work(self, t, prog, st):
        B = self.R.B; p = (t / B) % 1
        n = len(self.ITEMS)
        self._p = p; self._i = min(n - 1, int(prog * n))
        if p < 0.3:
            return self.home, dict(ra_t=(self.home - 120, FY - 180), la_t=(self.home - 170, FY - 170), lean=-0.25, bob=30,
                                   mouth="flat", brows="focus"), -1
        k = ease_out((p - 0.3) / 0.2)
        return self.home, dict(ra=(lerp(0.2, 2.6, k), lerp(0.3, 2.8, k)), la=(lerp(-0.3, 2.2, k), lerp(-0.2, 2.6, k)),
                               lean=lerp(-0.1, 0.12, k), mouth="open", brows="happy"), 1

    def front(self, c, t, prog, st, J):
        if st["action"] != "work": return
        p = self._p; kind = self.ITEMS[self._i]
        if p < 0.3:
            self.item(c, kind, J["rh"][0] - 20, J["rh"][1] - 10, 0.0, 0.9)
        elif p < 0.95:
            k = (p - 0.3) / 0.65
            x = lerp(J["sr"][0] + 20, 860, k); y = lerp(FY - 560, 1330, k) - 420 * math.sin(PI * k)
            self.item(c, kind, x, y, k * 6, 0.9)
            for i in range(3):
                kk = max(0, k - 0.06 * (i + 1))
                xx = lerp(J["sr"][0] + 20, 860, kk); yy = lerp(FY - 560, 1330, kk) - 420 * math.sin(PI * kk)
                circle(c, xx, yy, 8 - 2 * i); src(c, (1, 1, 1, 0.6)); c.fill()


class Verkehr(Trade):
    env = "day"; home = 300; fx_pos = (700, 980)
    look = dict(shirt="#1f2937", pants="#1f2937", hat="helmet", hat_color="#ffffff", vest="orange", long_sleeves=True)
    CONES = [460, 585, 710, 835, 960]

    def placed(self, prog): return int(prog * (len(self.CONES) + 1) + 1e-6)

    def back(self, c, t, prog, st):
        c.rectangle(0, FY - 10, W, H - FY + 10); src(c, hx("#374151")); c.fill()
        c.rectangle(0, FY - 30, W, 30); fill_ol(c, hx("#9ca3af"))
        for x in range(-40, W, 200):
            c.rectangle(x + (0 if True else 0), 1790, 110, 18); src(c, (1, 1, 1)); c.fill()
        n = self.placed(prog)
        B = self.R.B
        if n >= 3:
            # Absperrschranke mit Warnleuchte
            bx = 760
            for lx in (bx - 150, bx + 150):
                c.rectangle(lx - 8, FY - 200, 16, 200); fill_ol(c, hx("#d1d5db"), 5)
            c.rectangle(bx - 190, FY - 240, 380, 56); fill_ol(c, (1, 1, 1))
            c.save(); c.rectangle(bx - 190, FY - 240, 380, 56); c.clip()
            for i in range(-2, 10):
                poly(c, [(bx - 190 + i * 60, FY - 184), (bx - 160 + i * 60, FY - 240), (bx - 130 + i * 60, FY - 240), (bx - 160 + i * 60, FY - 184)])
                src(c, hx("#dc2626")); c.fill()
            c.restore(); c.rectangle(bx - 190, FY - 240, 380, 56); src(c, INK); c.set_line_width(OL); c.stroke()
            on = (t / (B / 2)) % 2 < 1
            rr(c, bx - 26, FY - 300, 52, 60, 14); fill_ol(c, hx("#fbbf24") if on else hx("#92400e"))
            if on:
                burst(c, bx, FY - 270, 70, 44, 10, t, (1, 0.85, 0.2, 0.35), 0)
        if n >= len(self.CONES) + 1 or prog > 0.9:
            # Warnschild
            sx = 950
            c.rectangle(sx - 8, FY - 560, 16, 560); fill_ol(c, hx("#9ca3af"), 5)
            poly(c, [(sx, FY - 720), (sx + 110, FY - 530), (sx - 110, FY - 530)]); fill_ol(c, hx("#dc2626"))
            poly(c, [(sx, FY - 680), (sx + 74, FY - 552), (sx - 74, FY - 552)]); src(c, (1, 1, 1)); c.fill()
            text(c, "!", sx, FY - 590, 90, INK, font="Luckiest Guy")
        for i in range(min(n, len(self.CONES))):
            cone(c, self.CONES[i], FY + 30)

    def work(self, t, prog, st):
        n = self.placed(prog)
        B = self.R.B
        if n < len(self.CONES):
            target = self.CONES[n] - 130
            k = (prog * (len(self.CONES) + 1)) % 1
            x = lerp(self.CONES[n - 1] - 130 if n > 0 else self.home - 100, target, ease_io(min(1, k * 1.4)))
            walking = k < 0.7
            pose = walk_pose(t, B, 2.0, 0.35) if walking else dict(lean=0.25, bob=40)
            self._cone = (x + 120, FY - 330) if walking else (x + 130, FY - 180 + 150 * (k - 0.7) / 0.3)
            pose.update(ra_t=(self._cone[0] - 10, self._cone[1] - 140), mouth="smile", brows="focus")
            return x, pose, 1
        self._cone = None
        return 760 - 380, dict(ra=(2.5, 2.8), mouth="grin"), 1

    def front(self, c, t, prog, st, J):
        if st["action"] == "work" and getattr(self, "_cone", None):
            cone(c, self._cone[0], self._cone[1], 0.8)
            J["draw_hand"]("ra")


TRADES = {"maler": Maler, "abbruch": Abbruch, "bodenleger": Bodenleger, "reinigung": Reinigung, "umzug": Umzug,
          "lieferung": Lieferung, "sicherheitsdienst": Sicherheit, "entruempelung": Entruempelung, "verkehrssicherung": Verkehr}


# ------------------------------------------------------------------ Reel
class Reel:
    def __init__(self, spec):
        ensure_fonts()
        self.spec = spec
        self.theme = dict(accent="#f59e0b", caption="#fde047"); self.theme.update(spec.get("theme", {}))
        self.bpm = spec.get("bpm", 120); self.B = 60 / self.bpm
        self.trade = TRADES[spec["trade"]](self)
        self.look = dict(self.trade.look); self.look.update(spec.get("look", {}))
        self.env = "night" if spec["trade"] == "sicherheitsdienst" else spec.get("env", self.trade.env)
        rnd = random.Random(spec.get("seed", 0))
        self.bg = make_bg(self.env, self.theme, spec["trade"], rnd)
        self.scenes = []
        b = 0
        for i, s in enumerate(spec["scenes"]):
            self.scenes.append((b, b + s["beats"], s)); b += s["beats"]
        self.TOTAL = b * self.B
        work = sum(s["beats"] for s in spec["scenes"] if s.get("action") == "work") * self.B
        self.work_total = max(work, 1e-6); self.has_work = work > 0
        self.start_prog = spec.get("start_prog", 0.0)
        di = spec.get("drop_scene", 1)
        self.DROP = self.scenes[min(di, len(self.scenes) - 1)][0] * self.B
        self.hits = []
        for i, (sb, se, s) in enumerate(self.scenes):
            if s.get("action") == "work":
                self.hits += self.trade.events(sb * self.B, se * self.B) if True else []
        # Hits relativ zur Szene berechnen: events nutzen globale Zeit modulo Takt -> passt, da Szenen auf Takt starten

    def prog_at(self, t):
        if not self.has_work: return self.start_prog
        done = 0.0
        for sb, se, s in self.scenes:
            if s.get("action") != "work": continue
            a, b = sb * self.B, se * self.B
            if t >= b: done += b - a
            elif t > a: done += t - a
        return clamp(self.start_prog + (1 - self.start_prog) * done / self.work_total)

    # ---- Bausteine
    def caption(self, c, t, lines, idx, sub=None):
        if not lines: return
        B = self.B
        size = 128 if len(lines) <= 2 else 112
        widths = [text_w(c, ln, size) for ln in lines]
        bw = min(1000, max(widths) + 110); lh = size * 0.98
        bh = lh * len(lines) + 70 + (70 if sub else 0)
        cx, top = W / 2, 190
        k = back_out(t / 0.3)
        c.save(); c.translate(cx, top + bh / 2); c.rotate(-0.025 if idx % 2 == 0 else 0.025); c.scale(k, k)
        rr(c, -bw / 2 + 14, -bh / 2 + 14, bw, bh, 18); src(c, INK); c.fill()
        rr(c, -bw / 2, -bh / 2, bw, bh, 18); fill_ol(c, hx(self.theme["caption"]), OL + 2)
        for i, ln in enumerate(lines):
            t0 = i * B / 2
            if t < t0: continue
            kk = back_out((t - t0) / 0.22)
            y = -bh / 2 + 35 + lh * (i + 0.5)
            text(c, ln, 0, y, size, INK, maxw=bw - 70, sc=0.5 + 0.5 * kk)
        if sub and t >= len(lines) * B / 2:
            text(c, sub, 0, bh / 2 - 60, 60, hx(self.theme["accent"]) if self.theme["caption"] != self.theme["accent"] else INK,
                 stroke=INK, sw=4, maxw=bw - 80, sc=back_out((t - len(lines) * B / 2) / 0.25))
        c.restore()

    def bubble(self, c, t, txt, J, face):
        B = self.B
        if t < B: return
        k = back_out((t - B) / 0.3)
        lines = txt.split("\n")
        size = 70
        tw = max(text_w(c, ln, size) for ln in lines)
        bw = min(620, tw + 90); bh = 84 * len(lines) + 50
        hx_, hy = J["head"]
        bx = hx_ + (170 if hx_ < W / 2 else -170)
        bx = clamp(bx, bw / 2 + 40, W - bw / 2 - 40)
        by = hy - 210 - bh / 2
        by = max(by, 820 + bh / 2 * 0)
        c.save(); c.translate(bx, by); c.scale(k, k)
        tx = clamp(hx_ - bx, -bw / 2 + 60, bw / 2 - 60)
        poly(c, [(tx - 30, bh / 2 - 6), (tx + 30, bh / 2 - 6), (tx * 0.6 + (hx_ - bx) * 0.4, bh / 2 + 80)])
        fill_ol(c, (1, 1, 1))
        ellipse(c, 0, 0, bw / 2, bh / 2); fill_ol(c, (1, 1, 1))
        poly(c, [(tx - 27, bh / 2 - 14), (tx + 27, bh / 2 - 14), (tx * 0.6 + (hx_ - bx) * 0.4, bh / 2 + 70)]); src(c, (1, 1, 1)); c.fill()
        for i, ln in enumerate(lines):
            text(c, ln, 0, (i - (len(lines) - 1) / 2) * 84, size, INK, maxw=bw - 70)
        c.restore()

    def fx(self, c, t, word, pos, hits):
        """Comic-Effektwort: erscheint bei jedem Treffer (oder einmal nach 1,5 Takten) und verschwindet wieder."""
        B = self.B
        starts = hits if hits else [1.5 * B]
        dt = None
        for h in starts:
            if 0 <= t - h < 0.75: dt = t - h
        if dt is None: return
        k = back_out(dt / 0.2) * (1 - ease_io((dt - 0.55) / 0.2))
        if k <= 0.01: return
        size = 84
        tw = text_w(c, word, size, "Luckiest Guy")
        r1 = tw * 0.62 + 34
        x = clamp(pos[0], r1 + 10, W - r1 - 10)
        c.save(); c.translate(x, pos[1]); c.rotate(-0.12 + 0.05 * math.sin(dt * 9)); c.scale(k, k)
        burst(c, 0, 0, r1, r1 * 0.72, 14, dt, hx("#fde047"))
        text(c, word, 0, 5, size, hx("#ef4444"), font="Luckiest Guy", stroke=INK, sw=5)
        c.restore()

    def generic_pose(self, action, t, st):
        B = self.B; tr = self.trade
        x = tr.home; face = tr.face
        breath = 4 * math.sin(t * 3)
        p = dict(bob=breath)
        if action == "walk_in":
            k = clamp(t / (1.5 * B))
            x = lerp(-180, tr.home, ease_out(k))
            if k < 1: p.update(walk_pose(t, B, 2.0))
            else: p.update(ra=(2.4, 2.6 + 0.4 * math.sin(t * 2 * PI / (B / 2))), mouth="open")
        elif action == "wave":
            p.update(ra=(2.4, 2.6 + 0.45 * math.sin(t * 2 * PI / (B / 2))), mouth="open", brows="happy")
        elif action == "thumbs":
            p.update(ra=(1.3, 2.9), thumb="ra", mouth="grin", eyes="closed" if (t % 1.6) > 1.3 else "open")
        elif action == "think":
            S = (x - 68, FY - 236 - 186)
            p.update(la_t=(x - 20, FY - 236 - 300 + 70), la_bend=-1, ra=(-0.5, 0.9), mouth="o", eyes="up", brows="think",
                     tilt=0.12)
        elif action == "shock":
            p.update(la=(-2.5, -2.7), ra=(2.5, 2.7), mouth="open", eyes="wide", brows="shock",
                     bob=-30 * abs(math.sin(t * 2 * PI / B)))
        elif action == "celebrate":
            j = abs(math.sin(t * PI / B))
            p.update(bob=-90 * j, la=(-2.6, -2.9), ra=(2.6, 2.9), mouth="open", brows="happy",
                     ll=(-0.3 * j, 0.4 * j), rl=(0.3 * j, -0.4 * j))
        elif action == "point":
            p.update(ra=(2.75, 2.85), finger="ra", mouth="smile", eyes="up")
        return x, p, face

    def stage(self, c, t, idx, st, tl):
        tr = self.trade
        prog = self.prog_at(t)
        c.set_source_surface(self.bg, 0, 0); c.paint()
        stt = dict(action=st.get("action", "idle"), index=idx)
        tr.back(c, tl, prog, stt)
        if stt["action"] == "work":
            x, pose, face = tr.work(tl, prog, stt)
        else:
            x, pose, face = self.generic_pose(stt["action"], tl, st)
        if st.get("flip"): face = -face
        J = person(c, x, FY, 1.0, self.look, pose, face, t)
        tr.front(c, tl, prog, stt, J)
        weather(c, self.env, t, self.spec.get("seed", 0))
        return J, face, prog

    def cta(self, c, t, s):
        B = self.B
        acc = hx(self.theme["accent"])
        src(c, acc); c.paint()
        halftone(c, 0, 0, W, H, mix(acc, (0, 0, 0), 0.12), 32, 8, fade="up")
        burst(c, W / 2, 1560, 560, 420, 16, t * 0.3, mix(acc, (1, 1, 1), 0.45), OL)
        ls = s.get("lines", ["Jetzt anfragen!"])
        for i, ln in enumerate(ls):
            if t < i * B / 2: continue
            k = back_out((t - i * B / 2) / 0.25)
            text(c, ln, W / 2, 330 + i * 125, 118, (1, 1, 1), font="Luckiest Guy", stroke=INK, sw=8, maxw=960, sc=k, shadow=10)
        if t >= B:
            pulse = 1 + 0.05 * math.sin((t - B) * 2 * PI / B) if t > B + 0.3 else back_out((t - B) / 0.3)
            bw, bh = 900 * pulse, 170 * pulse
            c.save(); c.translate(W / 2, 700)
            rr(c, -bw / 2 + 14, -bh / 2 + 14, bw, bh, bh / 2); src(c, INK); c.fill()
            rr(c, -bw / 2, -bh / 2, bw, bh, bh / 2); fill_ol(c, (1, 1, 1), OL + 2)
            c.restore()
            text(c, s.get("button", "auftragexpress24.de"), W / 2, 704, 80 * pulse, INK, font="Luckiest Guy", maxw=bw - 90)
        if s.get("sub") and t >= 2 * B:
            text(c, s["sub"], W / 2, 870, 72, (1, 1, 1), stroke=INK, sw=5, maxw=960, sc=back_out((t - 2 * B) / 0.25))
        if s.get("phone") and t >= 3 * B:
            text(c, s["phone"], W / 2, 975, 82, INK, font="Luckiest Guy", stroke=(1, 1, 1), sw=5, maxw=960, sc=back_out((t - 3 * B) / 0.25))
        if s.get("foot") and t >= 4 * B:
            text(c, s["foot"], W / 2, 1085, 64, (1, 1, 1), stroke=INK, sw=5, maxw=960, sc=back_out((t - 4 * B) / 0.25))
        pose = dict(ra=(2.4, 2.6 + 0.45 * math.sin(t * 2 * PI / (B / 2))), la=(-1.3, -2.9), thumb="la",
                    mouth="open", brows="happy", bob=-14 * abs(math.sin(t * PI / B)))
        person(c, W / 2, 1890, 0.92, self.look, pose, 0, t)

    def render(self, t, c):
        B = self.B
        idx = max(i for i, s in enumerate(self.scenes) if s[0] * B <= t)
        sb, se, st = self.scenes[idx]
        tl = t - sb * B
        c.save()
        src(c, INK); c.paint()
        # Kamera: Wackeln bei Treffern, Puls nach dem Drop
        zoom, dx, dy = 1.0, 0.0, 0.0
        if t >= self.DROP: zoom = 1 + 0.018 * math.exp(-(t % B) / 0.09)
        for h in self.hits:
            if 0 <= t - h < 0.25:
                k = math.exp(-(t - h) / 0.06); dx += 14 * k * math.sin(t * 95); dy += 10 * k * math.cos(t * 80)
        c.translate(W / 2 + dx, H / 2 + dy); c.scale(zoom, zoom); c.translate(-W / 2, -H / 2)
        if st.get("action") == "cta":
            self.cta(c, tl, st)
        else:
            c.save(); c.translate(W / 2, 1720); c.scale(STAGE, STAGE); c.translate(-W / 2, -1720)
            J, face, prog = self.stage(c, t, idx, st, tl)
            if st.get("fx"):
                hit_local = [h - sb * B for h in self.hits if sb * B <= h < se * B]
                self.fx(c, tl, st["fx"], tuple(st.get("fx_pos", self.trade.fx_pos)), hit_local)
            if st.get("bubble"): self.bubble(c, tl, st["bubble"], J, face)
            c.restore()
            self.caption(c, tl, st.get("caption", []), idx, st.get("sub"))
        c.restore()
        # Übergang: Comic-Explosion
        if idx + 1 < len(self.scenes):
            nb = self.scenes[idx + 1][0] * B
            if t >= nb - 0.22:
                k = ease_out((t - (nb - 0.22)) / 0.22)
                nxt = self.scenes[idx + 1][2]
                col = hx(self.theme["accent"]) if nxt.get("action") == "cta" else hx(self.theme["caption"])
                burst(c, W / 2, H / 2, 1500 * k + 10, 1100 * k + 5, 14, t, col, OL + 4)
        if 0 < idx and tl < 0.12:
            k = tl / 0.12
            col = hx(self.theme["accent"]) if st.get("action") == "cta" else hx(self.theme["caption"])
            burst(c, W / 2, H / 2, 1500 * (1 - k) + 10, 1100 * (1 - k) + 5, 14, t, col, OL + 4)
        # Kopfzeile
        rr(c, 70, 44, W - 140, 16, 8); src(c, (0, 0, 0, 0.35)); c.fill()
        rr(c, 70, 44, max(16, (W - 140) * t / self.TOTAL), 16, 8); fill_ol(c, hx(self.theme["accent"]), 3)
        text(c, "AuftragExpress24", W / 2, 112, 58, (1, 1, 1), stroke=INK, sw=5)

    def build_events(self):
        B = self.B
        ev = [(self.DROP, "drop"), (self.DROP, "riser:%.2f" % min(2.0, self.DROP))]
        for sb, se, s in self.scenes[1:]:
            ev.append((sb * B, "whoosh"))
        for sb, se, s in self.scenes:
            st = sb * B
            if s.get("action") == "cta":
                ev += [(st + B, "pop"), (st + 3 * B, "pop"), (st + 4 * B, "pop")]
                continue
            ev += [(st + i * B / 2, "pop") for i in range(len(s.get("caption", [])))]
            if s.get("bubble"): ev.append((st + B, "pop"))
        ev += [(h, "pop") for h in self.hits]
        return [e for e in ev if e[0] < self.TOTAL]

    def frame(self, t):
        surf = cairo.ImageSurface(cairo.FORMAT_ARGB32, W, H)
        c = cairo.Context(surf)
        self.render(t, c)
        surf.flush()
        return surf

    def make(self, out):
        tmp = out + ".tmp"; os.makedirs(tmp, exist_ok=True)
        wav = os.path.join(tmp, "a.wav")
        audio.build(self.TOTAL, self.build_events(), wav, self.bpm, self.spec.get("progression", 0), self.spec.get("seed", 0))
        cmd = ["ffmpeg", "-y", "-loglevel", "error", "-f", "rawvideo", "-pix_fmt", "bgra", "-s", f"{W}x{H}", "-r", str(FPS),
               "-i", "-", "-i", wav, "-c:v", "libx264", "-preset", "veryfast", "-crf", "20", "-pix_fmt", "yuv420p",
               "-c:a", "aac", "-b:a", "160k", "-shortest", "-movflags", "+faststart", out]
        pr = subprocess.Popen(cmd, stdin=subprocess.PIPE)
        for f in range(int(self.TOTAL * FPS)):
            pr.stdin.write(bytes(self.frame(f / FPS).get_data()))
        pr.stdin.close(); pr.wait()
        shutil.rmtree(tmp, ignore_errors=True)
        if pr.returncode: raise SystemExit("ffmpeg fehlgeschlagen")
        self.preview(out.rsplit(".", 1)[0] + "_preview.png")
        print(f"ok {out} ({self.TOTAL:.1f}s)")

    def preview(self, path):
        from PIL import Image
        times = [(se * self.B - 0.4) for sb, se, _ in self.scenes]
        ims = []
        for tt in times:
            s = self.frame(min(tt, self.TOTAL - 0.05))
            im = Image.frombuffer("RGBA", (W, H), bytes(s.get_data()), "raw", "BGRA", 0, 1).convert("RGB")
            ims.append(im.resize((360, 640)))
        sheet = Image.new("RGB", (360 * len(ims), 640))
        for i, im in enumerate(ims): sheet.paste(im, (360 * i, 0))
        sheet.save(path)


if __name__ == "__main__":
    spec = json.load(open(sys.argv[1], encoding="utf-8"))
    r = Reel(spec)
    if len(sys.argv) > 3 and sys.argv[3] == "--still":
        # nur Einzelbilder zum Testen: ausgabe_<i>.png je Szene
        r.preview(sys.argv[2])
    else:
        r.make(sys.argv[2])
