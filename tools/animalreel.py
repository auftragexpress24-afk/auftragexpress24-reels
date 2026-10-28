"""Tier-Reel mit Sprecherstimme: Tiere (Emoji) zeigen die Leistungen, Untertitel im Karaoke-Stil.

Aufruf: python3 animalreel.py spec.json out.mp4 --voice /pfad/zum/vits-ordner
visual: {"type":"animal","animal":"🐻","prop":"🖌️","title":"Kurzer Titel"}
        {"type":"headline","lines":[...]}  |  {"type":"cta",...}  (wie voicereel)
"""
import argparse, json, math, os, sys
from PIL import Image, ImageDraw, ImageFont

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import voicereel
from voicereel import VoiceReel
from reelgen import F, fit, back_out, W, H

EMOJI = "/usr/share/fonts/truetype/noto/NotoColorEmoji.ttf"
_ef = None


def emoji(ch, size):
    """Farbiges Emoji als RGBA-Bild (NotoColorEmoji hat nur die Größe 109)."""
    global _ef
    if _ef is None:
        _ef = ImageFont.truetype(EMOJI, 109)
    im = Image.new("RGBA", (136, 128), (0, 0, 0, 0))
    ImageDraw.Draw(im).text((0, 0), ch, font=_ef, embedded_color=True)
    bb = im.getbbox()
    if bb: im = im.crop(bb)
    return im.resize((size, int(size * im.height / im.width)), Image.LANCZOS)


class AnimalReel(VoiceReel):
    def v_animal(self, c, t, v, bg):
        dark = bg != self.LIGHT
        ink = self.LIGHT if dark else self.D1
        if v.get("title"):
            self.pop(c, v["title"], fit(v["title"], "Black", 110), self.ACC, W / 2, 330, t, 0.0)
        # Tier hüpft rein, danach sanftes Wippen
        p = back_out(t / 0.45)
        bob = math.sin(max(0.0, t - 0.45) * 2 * math.pi / 1.4) * 22 if t > 0.45 else 0
        size = int(560 * max(0.05, p))
        if size > 0:
            im = emoji(v["animal"], size)
            c.alpha_composite(im, (int(W / 2 - im.width / 2), int(720 - im.height / 2 + bob)))
        # Requisit wackelt daneben
        if v.get("prop") and t > 0.6:
            q = back_out((t - 0.6) / 0.4)
            ps = int(230 * max(0.05, q))
            wob = math.sin(t * 6) * 12
            pi = emoji(v["prop"], ps).rotate(wob, expand=True, resample=Image.BICUBIC)
            c.alpha_composite(pi, (int(W / 2 + 200 - pi.width / 2), int(980 - pi.height / 2 - bob * 0.5)))
        if v.get("caption"):
            self.pop(c, v["caption"], fit(v["caption"], "Bold", 62), ink, W / 2, 1290, t, 1.0)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("spec"); ap.add_argument("out")
    ap.add_argument("--voice", required=True); ap.add_argument("--speed", type=float, default=0.95)
    a = ap.parse_args()
    spec = json.load(open(a.spec, encoding="utf-8"))
    AnimalReel(spec, a.voice, a.speed).make(a.out)


if __name__ == "__main__":
    main()
