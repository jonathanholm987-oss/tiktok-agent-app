"""Erzeugt Texte für Slideshows und erzwingt die inhaltlichen Regeln."""
from __future__ import annotations

import json
import os
import re

import requests

from .core import AgentError, Product, Settings

CLAUDE_URL = "https://api.anthropic.com/v1/messages"
DISCLOSURE = "Als Amazon-Partner verdiene ich an qualifizierten Verkäufen."

FORMATS: dict[str, dict[str, str]] = {
    "problem_loesung": {
        "brief": "Beginne mit dem Alltagsproblem als direkte Frage und zeige dann die Lösung.",
        "hook": "Schluss mit {problem}",
    },
    "fuer_wen": {
        "brief": "Sprich gezielt eine Zielgruppe an und erkläre, warum es für sie passt.",
        "hook": "Für alle, die {problem} kennen",
    },
    "gruende": {
        "brief": "Nenne die Gründe als knappe, nummerierte Punkte.",
        "hook": "{count} Gründe für {short_lower}",
    },
    "kurz_erklaert": {
        "brief": "Erkläre nüchtern und schnell, was das Produkt macht.",
        "hook": "{short} kurz erklärt",
    },
    "kleine_loesung": {
        "brief": "Stelle die kleine, unspektakuläre Lösung für ein nerviges Problem heraus.",
        "hook": "Kleine Lösung gegen {problem}",
    },
}

FORBIDDEN: list[tuple[str, str]] = [
    (r"€|\beuro\b|\bpreis|\bgünstig|\brabatt|\bangebot|%", "Preis oder Rabatt"),
    (r"\bgetestet\b|\bich (habe|hab|nutze|benutze|liebe|verwende)\b|\bmein(e|en|em)? (erfahrung|favorit|test)", "erfundene eigene Erfahrung"),
    (r"\bbewertung|\bsterne\b|\bkunden sagen|\bbestseller|\btausende\b|\bviral", "erfundener Beleg"),
    (r"\bgarantiert\b|\b100 ?prozent\b|\bheilt\b|\bklinisch\b|\bwissenschaftlich\b", "unbelegtes Versprechen"),
    (r"\bnur (noch )?heute\b|\bbegrenzt\b|\bsolange\b|\bausverkauf", "künstliche Dringlichkeit"),
    (r"\bdas beste\b|\bder beste\b|\bdie beste\b|\bbesser als\b", "unbelegter Vergleich"),
]


def _digits(text: str) -> set[str]:
    return set(re.findall(r"\d+", text))


def has_forbidden(text: str) -> bool:
    lowered = text.lower()
    return any(re.search(pattern, lowered) for pattern, _ in FORBIDDEN)


def shorten(fact: str, limit: int = 70) -> str | None:
    """Macht aus einem Stichpunkt eine Slide-Zeile, ohne Inhalte zu erfinden."""
    text = re.sub(r"^\s*[【\[][^】\]]*[】\]]\s*[:：\-–]?\s*", "", fact)
    text = re.sub(r"^[A-ZÄÖÜ0-9 &/\-]{4,}\s*[:：\-–]\s*", "", text).strip()
    if len(text) <= limit:
        return text or None
    head = re.split(r"(?<=[.!?;:])\s|\s[–—-]\s|,\s", text, maxsplit=1)[0].strip(" .;:")
    return head if 12 <= len(head) <= limit else None


def validate(draft: dict, product: Product, settings: Settings) -> list[str]:
    errors: list[str] = []
    slides = draft.get("slides")
    max_slides = int(settings.section("publish").get("max_slides", 7))
    max_content = min(max_slides - 2, 8)
    if not isinstance(draft.get("hook"), str) or not draft["hook"].strip():
        errors.append("hook fehlt")
    if not isinstance(slides, list) or not 2 <= len(slides) <= max_content:
        errors.append(f"slides muss 2 bis {max_content} Einträge haben")
        return errors
    for index, slide in enumerate(slides, 1):
        if not isinstance(slide, dict) or not str(slide.get("headline", "")).strip():
            errors.append(f"Slide {index}: headline fehlt")
            continue
        if len(str(slide["headline"])) > 70:
            errors.append(f"Slide {index}: headline länger als 70 Zeichen")
        if len(str(slide.get("body") or "")) > 140:
            errors.append(f"Slide {index}: body länger als 140 Zeichen")
    if len(str(draft.get("hook", ""))) > 70:
        errors.append("hook länger als 70 Zeichen")
    if not str(draft.get("caption") or "").strip():
        errors.append("caption fehlt")

    all_text = " ".join(
        [str(draft.get("hook", "")), str(draft.get("caption", "")), str(draft.get("title", ""))]
        + [f"{s.get('headline', '')} {s.get('body', '')}" for s in slides if isinstance(s, dict)]
    )
    # Was wörtlich in den geprüften Fakten steht, ist immer erlaubt.
    lowered = all_text.lower()
    for fact in product.facts:
        lowered = lowered.replace(fact.lower(), " ")
    for pattern, label in FORBIDDEN:
        match = re.search(pattern, lowered)
        if match:
            errors.append(f"Verboten ({label}): '{match.group(0)}'")
    allowed = _digits(" ".join(product.facts) + " " + product.name) | {str(len(slides))}
    invented = _digits(all_text) - allowed
    if invented:
        errors.append(f"Zahlen ohne Beleg in den Fakten: {sorted(invented)}")
    return errors


def fallback_draft(product: Product, fmt: str, settings: Settings) -> dict:
    """Regelbasierter Entwurf, nutzt ausschließlich die hinterlegten Fakten."""
    max_slides = int(settings.section("publish").get("max_slides", 7))
    facts = [line for line in map(shorten, product.facts) if line][: max(2, max_slides - 2)]
    short_lower = product.short[0].lower() + product.short[1:]
    hook = FORMATS[fmt]["hook"].format(
        problem=product.problem,
        short=product.short,
        short_lower=short_lower,
        count=len(facts),
    )
    return {
        "hook": hook,
        "slides": [{"headline": fact, "body": ""} for fact in facts],
        "caption": f"{product.short} gegen {product.problem}.",
        "title": hook,
        "hashtags": [re.sub(r"\W", "", product.category.lower())],
        "generator": "fallback",
    }


def _prompt(product: Product, fmt: str, learnings: str, settings: Settings) -> str:
    max_content = int(settings.section("publish").get("max_slides", 7)) - 2
    facts = "\n".join(f"- {fact}" for fact in product.facts)
    return f"""Schreibe eine deutsche TikTok-Fotoslideshow für dieses Produkt.

PRODUKT: {product.name}
KURZNAME: {product.short}
KATEGORIE: {product.category}
PROBLEM: {product.problem}
ZIELGRUPPE: {product.audience or "nicht angegeben"}
FAKTEN (einzige erlaubte Quelle):
{facts}

FORMAT: {FORMATS[fmt]["brief"]}

BISHERIGE ERGEBNISSE DES ACCOUNTS:
{learnings or "Noch keine Daten."}

REGELN:
- Behaupte nur, was in den Fakten steht. Erfinde keine Eigenschaften und keine Zahlen.
- Keine Preise, Rabatte, Bewertungen, Testsieger-Aussagen oder Verkaufszahlen.
- Schreibe nicht, dass jemand das Produkt getestet oder benutzt hat. Keine Ich-Erfahrungen.
- Keine Heilversprechen, keine Garantien, kein künstlicher Zeitdruck.
- hook höchstens 70 Zeichen. headline höchstens 70 Zeichen, body höchstens 140 Zeichen.
- 2 bis {max_content} Inhalts-Slides. Keine eigene Abschluss-Slide, die ergänzt das System.
- caption: ein bis zwei natürliche Sätze ohne Hashtags und ohne Kennzeichnung.
- title: höchstens 80 Zeichen, ohne Hashtags.
- hashtags: 2 bis 4 passende Begriffe ohne #-Zeichen.

Antworte nur mit JSON in dieser Form:
{{"hook":"","slides":[{{"headline":"","body":""}}],"caption":"","title":"","hashtags":[""]}}"""


def _ask_claude(prompt: str, model: str, api_key: str) -> dict:
    response = requests.post(
        CLAUDE_URL,
        headers={
            "x-api-key": api_key,
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
        },
        json={
            "model": model,
            "max_tokens": 1500,
            "messages": [{"role": "user", "content": prompt}],
        },
        timeout=120,
    )
    response.raise_for_status()
    text = "".join(
        block.get("text", "")
        for block in response.json().get("content", [])
        if block.get("type") == "text"
    )
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end <= start:
        raise ValueError("Antwort enthält kein JSON")
    return json.loads(text[start : end + 1])


def create_draft(
    product: Product, fmt: str, learnings: str, settings: Settings, attempts: int = 3
) -> dict:
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if api_key:
        model = settings.section("content").get("model", "claude-sonnet-5-5")
        prompt = _prompt(product, fmt, learnings, settings)
        feedback = ""
        for attempt in range(1, attempts + 1):
            try:
                draft = _ask_claude(prompt + feedback, model, api_key)
            except (requests.RequestException, ValueError) as exc:
                print(f"Textgenerierung Versuch {attempt} fehlgeschlagen: {exc}")
                continue
            errors = validate(draft, product, settings)
            if not errors:
                draft["generator"] = model
                return draft
            print(f"Entwurf {attempt} abgelehnt: {errors}")
            feedback = "\n\nDein letzter Entwurf wurde abgelehnt:\n- " + "\n- ".join(errors)
    draft = fallback_draft(product, fmt, settings)
    errors = validate(draft, product, settings)
    if errors:
        raise AgentError(f"Produkt {product.name}: Eintrag verletzt die Regeln: {errors}")
    return draft


def build_caption(draft: dict, product: Product, settings: Settings) -> str:
    tags = list(settings.section("content").get("base_hashtags") or [])
    for tag in draft.get("hashtags") or []:
        clean = re.sub(r"\W", "", str(tag))
        if clean and clean.lower() not in {t.lower() for t in tags}:
            tags.append(clean)
    body = str(draft["caption"]).strip()[:600]
    hashtags = " ".join(f"#{tag}" for tag in tags[:8])
    return (
        f"Werbung | {body}\n\n"
        f"Link in Bio, Produkt Nr. {product.nr}. {DISCLOSURE}\n\n{hashtags}"
    )


def build_title(draft: dict) -> str:
    source = str(draft.get("title") or draft["hook"])
    title = re.sub(r"#\w+", "", source).strip()
    return title[:90]
