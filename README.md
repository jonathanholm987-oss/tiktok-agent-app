# TikTok-Agent für Amazon-Slideshows

Der Agent läuft einmal täglich auf GitHub und macht Folgendes:

1. **Analysieren:** Holt Kennzahlen von Buffer und bewertet Formate, Produkte, Suchbegriffe und Uhrzeiten.
2. **Produkte finden:** Sucht passende Produkte über Amazons Creators API.
3. **Erstellen:** Wählt das Format, schreibt Texte mit Claude und rendert Slides als JPEG.
4. **Posten:** Plant die Slideshows über Buffer für die Zeiten in `config.yaml` ein.

Er startet im **Vorschau-Modus** und postet erst, wenn du `LIVE_POSTING` einschaltest.

## Einrichtung

### 1. Konten

- **GitHub:** Neues **öffentliches** Repository mit dem Hauptzweig `main` anlegen und diesen Ordner hochladen.
  Alles außer den Secrets ist dort sichtbar, auch Partner-ID und Impressum.
- **Buffer:** TikTok-Konto verbinden und unter API Settings einen Schlüssel erzeugen.
- **Amazon PartnerNet:** Anmelden und dein TikTok-Profil als Website eintragen.
- **Claude API (empfohlen):** Schlüssel in der Claude Console erzeugen.
  Ohne Schlüssel baut der Agent einfache Texte direkt aus deinen Fakten.

### 2. GitHub einstellen

- *Settings → Pages → Source:* **GitHub Actions**
- *Settings → Secrets and variables → Actions → Secrets:*
  - `BUFFER_API_KEY`
  - `ANTHROPIC_API_KEY`
  - `AMAZON_CREDENTIAL_ID` und `AMAZON_CREDENTIAL_SECRET`, sobald du sie hast (siehe unten)
- Sind mehrere TikTok-Konten in Buffer verbunden, zusätzlich die Variable `BUFFER_CHANNEL_ID` setzen.

### 3. Vorschau testen

Unter *Actions → TikTok Agent → Run workflow* starten. Die Slides erscheinen in der Zusammenfassung des Laufs.
Dort zeigt `Analysieren` auch die Auswertung, die außerdem in `reports/latest.md` liegt.

### 4. Daten eintragen

In `config.yaml` diese Felder ausfüllen:

- `amazon.associate_tag`
- `legal.impressum`
- `site.title`

**Automatische Produktsuche freischalten.** Amazon gibt den API-Zugang nur an Partner, die bereits qualifizierte Verkäufe vermittelt haben.

1. Am Anfang trägst du in `data/products.yaml` ein paar echte Produkte ein, damit erste Verkäufe entstehen können.
2. Sobald Amazon dich zulässt: PartnerNet → *Tools* → *Creators API* → *Create Application*.
3. Credential ID und Secret als GitHub-Secrets eintragen. Es werden Zugangsdaten der Version 3.2 (Europa) erwartet.

Ab dann sucht der Agent die Produkte selbst. Die Liste dient nur noch als Reserve.
Bleiben Verkäufe 30 Tage lang aus, kann Amazon den Zugang sperren. Der Agent nutzt dann die Liste und meldet das im Lauf.

### 5. Live schalten

*Settings → Secrets and variables → Actions → Variables:* `LIVE_POSTING` = `true`.
Fehlt noch etwas, bricht der Lauf ab und nennt die offenen Punkte.
Danach deine Link-Seite (die Pages-Adresse des Repositories) in die TikTok-Bio eintragen.

**Mit einem Produkt beginnen** und den ersten Post in TikTok prüfen.
Bilder-Posts über Buffers API konnten vor der Auslieferung nicht gegen ein echtes Konto getestet werden.

## Was du wissen musst

**Kennzeichnung.** Buffers API bietet kein Feld für TikToks Schalter „Kommerzielle Inhalte offenlegen“.
Der Agent kennzeichnet jede Slide und jede Caption sichtbar als Werbung, kann den Schalter aber nicht setzen.
Für Affiliate-Posts verlangt TikTok ihn. Du hast zwei Wege:

- `scheduling_type: notification`: Buffer erinnert dich, du postest in der TikTok-App und setzt den Schalter.
- `scheduling_type: automatic`: läuft ohne dich, der Schalter fehlt aber. Das kann zu Einschränkungen des Accounts führen.

**Produktbilder.** Amazons Lizenz verbietet, Bilder zu speichern oder zu verändern, und das gilt auch für Bilder aus der API.
Automatisch gefundene Produkte erscheinen deshalb als reine Text-Slides. Bei Einträgen in `products.yaml` kannst du über `image:` eigene Fotos einbinden.

**Amazon-Daten.** Titel und Produktmerkmale werden nicht dauerhaft gespeichert, nur ASIN und die eigenen Texte des Agenten.
Amazons Lizenz nennt das Verwenden von Programminhalten in Werbung außerhalb deiner eingetragenen Website als Grenze.
Trage dein TikTok-Profil deshalb im PartnerNet ein und nutze einen Claude-Schlüssel, damit die Texte neu formuliert werden.
Ohne Schlüssel erscheinen gekürzte Amazon-Stichpunkte auf den Slides.

**Inhalte.** Der Agent lehnt Texte ab, die Preise, erfundene Zahlen, Bewertungen oder eigene Testerfahrungen enthalten.
Gefundene Produkte werden nicht von einem Menschen geprüft. Gesundheits-, Baby-, Alkohol-, Waffen- und Erotikprodukte filtert er aus.
Die Fakten in `products.yaml` musst du selbst prüfen.

**Link in Bio.** Je nach Kontotyp und Followerzahl zeigt TikTok das Website-Feld nicht sofort an.

**Auswertung.** Gemessen wird, was Buffer von TikTok bekommt. Fehlende Kennzahlen zählen als 0.
Klicks und Verkäufe siehst du nur im Amazon PartnerNet.

## Anpassen

| Was | Wo |
|---|---|
| Posting-Zeiten | `publish.posting_times` in `config.yaml` |
| Startzeit des Laufs | `cron` in `.github/workflows/agent.yml` (UTC) |
| Claude-Modell | `content.model` in `config.yaml` |
| Suchideen | `research.seeds` in `config.yaml` |
| Ausgeschlossene Produkte | `RESTRICTED` in `agent/research.py` |
| Hook-Formate | `FORMATS` in `agent/content.py` |
| Farben | `PALETTES` in `agent/slides.py` |

Lokal prüfen: `pip install -r requirements.txt`, dann `python -m pytest -q` und `python -m agent doctor`.

Schrift: Inter, SIL Open Font License 1.1, https://github.com/rsms/inter
