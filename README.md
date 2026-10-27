# AuftragExpress24 Reels

Automatisch erstellte Instagram-Reels für @auftragexpress24.de.

- `tools/reelgen.py` – erzeugt ein Reel (9:16, mit Beat) aus einer JSON-Beschreibung, siehe Kopf der Datei.
- `tools/audio.py` – lizenzfreier, synthetisierter Beat + Soundeffekte.
- `specs/JJJJ-MM-TT/` – die Beschreibungen der Reels eines Tages.
- `posts.json` – Protokoll aller geposteten Reels (Thema, Hook, Idee), damit sich nichts wiederholt.
- Die Videos selbst liegen nur im Branch `media` (wird täglich ersetzt, damit das Repo klein bleibt).
- `tools/comicgen.py` – Comic-Reels: animierte Cartoon-Figur zeigt das Gewerk bei der Arbeit (seit 27.10.2026 Standard), Format siehe Kopf der Datei. Schriften in `tools/fonts/` (Bangers: OFL, Luckiest Guy: Apache 2.0).
- `tools/realreel.py` – Reels aus echten Stockvideos (Pixabay, gewerblich frei nutzbar) mit deutscher Sprecherstimme (Piper TTS), Karaoke-Untertiteln und Abspann. Braucht Netzwerkzugriff auf pixabay.com/huggingface.co und den Pixabay-Schlüssel in PIXABAY_KEY oder ~/.pixabay_key (nie ins Repo!). Beispiele: `specs/beispiele/real-*.json`.
