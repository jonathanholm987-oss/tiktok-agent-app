"""Befehle: analyze, create, site, publish, doctor."""
from __future__ import annotations

import argparse
import os
import random
import shutil
import sys
import uuid
from datetime import timedelta
from pathlib import Path

import requests

from . import analysis, content, research, slides
from .buffer import BufferClient
from .core import (
    AgentError,
    Settings,
    State,
    iso_z,
    load_products,
    parse_time,
    preflight,
    upcoming_slots,
    usable_products,
    utcnow,
)
from .site import build_site


def _client() -> BufferClient:
    key = os.environ.get("BUFFER_API_KEY")
    if not key:
        raise AgentError("Secret BUFFER_API_KEY fehlt")
    return BufferClient(key)


def _summary(text: str) -> None:
    print(text)
    target = os.environ.get("GITHUB_STEP_SUMMARY")
    if target:
        with open(target, "a", encoding="utf-8") as handle:
            handle.write(text + "\n")


def cmd_analyze(settings: Settings, state: State) -> None:
    now = utcnow()
    if os.environ.get("BUFFER_API_KEY"):
        updated = analysis.refresh_metrics(state, _client(), settings, now)
        print(f"Kennzahlen aktualisiert: {updated}")
        state.save()
    text = analysis.report(state.posts, settings)
    target = settings.root / "reports" / "latest.md"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(text, encoding="utf-8")
    _summary(text)


def cmd_create(settings: Settings, state: State) -> None:
    products = load_products(settings)
    if settings.live:
        problems = preflight(settings, products)
        if problems:
            raise AgentError("Live-Posting nicht möglich:\n- " + "\n- ".join(problems))
    candidates = usable_products(products, settings.live)
    finder = research.Finder.from_env(settings)
    if not candidates and not finder:
        raise AgentError("Keine Produktquelle: Amazon-API-Zugang fehlt und keine Produkte hinterlegt")

    now = utcnow()
    _drop_old_previews(settings, state, now)
    rng = random.Random()
    insight = analysis.learnings(state.posts)
    slots = upcoming_slots(settings, state, now)
    if not settings.live:
        previews = {p["due_at"] for p in state.posts if p.get("status") == "preview"}
        slots = [s for s in slots if iso_z(s) not in previews]
    if finder and slots:
        finder.extend_seeds(state, now)
    minimum_nr = max([p.nr for p in products if not p.demo], default=0) + 1
    created = 0
    for slot in slots:
        selection = _select(finder, candidates, state, settings, now, rng, insight, minimum_nr)
        if selection is None:
            continue
        product, fmt, draft = selection
        post_id = f"{slot.strftime('%Y%m%d%H%M')}-{uuid.uuid4().hex[:6]}"
        names = slides.render(
            draft, product, settings.root / "site" / "slides" / post_id, settings.root, post_id
        )
        state.posts.append(
            {
                "id": post_id,
                "status": "created" if settings.live else "preview",
                "created_at": iso_z(now),
                "due_at": iso_z(slot),
                "product_id": product.id,
                # Amazon-Titel werden nicht dauerhaft gespeichert.
                "product_name": product.short if product.auto else product.name,
                "category": product.category,
                "research_query": product.query,
                "format": fmt,
                "hook": draft["hook"],
                "caption": content.build_caption(draft, product, settings),
                "title": content.build_title(draft),
                "generator": draft.get("generator"),
                "images": names,
            }
        )
        state.save()
        created += 1
        if finder and product.auto and settings.live:
            finder.catalog.remember(product, now)
        print(f"Erstellt: {post_id} | {fmt} | {product.short} | {draft['hook']}")
    if not slots:
        print("Keine freien Posting-Zeiten in den nächsten 24 Stunden.")
    elif not created:
        detail = f" Amazon: {finder.reason}" if finder and finder.reason else ""
        raise AgentError(f"Heute wurde keine Slideshow erstellt, kein passendes Produkt gefunden.{detail}")
    if finder and not finder.available:
        _summary(
            f"**Achtung:** Automatische Produktsuche nicht verfügbar ({finder.reason}). "
            "Amazon sperrt den Zugang, wenn 30 Tage lang keine Verkäufe vermittelt wurden."
        )


def _select(finder, candidates, state, settings, now, rng, insight, minimum_nr):
    """Sucht zuerst automatisch und nutzt sonst die hinterlegte Produktliste."""
    if finder and finder.available:
        for product in finder.candidates(state, now, rng, minimum_nr):
            fmt = analysis.pick_format(state, settings, rng)
            try:
                return product, fmt, content.create_draft(product, fmt, insight, settings)
            except AgentError as exc:
                print(f"Produkt {product.asin} übersprungen: {exc}")
    if not candidates:
        return None
    product, fmt = analysis.pick(candidates, state, settings, now, rng)
    return product, fmt, content.create_draft(product, fmt, insight, settings)


def _drop_old_previews(settings: Settings, state: State, now) -> None:
    kept = []
    for post in state.posts:
        if post.get("status") == "preview" and parse_time(post["due_at"]) < now:
            shutil.rmtree(settings.root / "site" / "slides" / post["id"], ignore_errors=True)
            continue
        kept.append(post)
    state.posts[:] = kept


def _prune_slides(settings: Settings, state: State, now) -> None:
    days = int(settings.section("publish").get("keep_slides_days", 14))
    for post in state.posts:
        if post.get("status") == "scheduled" and now - parse_time(post["due_at"]) > timedelta(days=days):
            shutil.rmtree(settings.root / "site" / "slides" / post["id"], ignore_errors=True)


def cmd_site(settings: Settings, state: State) -> None:
    products = load_products(settings)
    finder = research.Finder.from_env(settings)
    if finder:
        products += finder.catalog_products()
    build_site(settings, products)
    _prune_slides(settings, state, utcnow())


def _image_urls(settings: Settings, post: dict) -> list[str]:
    return [f"{settings.base_url}/slides/{post['id']}/{name}" for name in post["images"]]


def _wait_until_online(url: str, attempts: int = 30, delay: int = 10) -> None:
    import time

    for _ in range(attempts):
        try:
            if requests.head(url, timeout=20, allow_redirects=False).status_code == 200:
                return
        except requests.RequestException:
            pass
        time.sleep(delay)
    raise AgentError(f"Bild ist nicht öffentlich erreichbar: {url}")


def cmd_publish(settings: Settings, state: State) -> None:
    if not settings.live:
        previews = [p for p in state.posts if p.get("status") == "preview"]
        lines = ["## Vorschau (nichts wurde gepostet)", ""]
        for post in previews:
            lines.append(f"**{post['hook']}** · {post['product_name']} · {post['format']}")
            lines.append("")
            lines.append(post["caption"].replace("\n", " "))
            lines.append("")
            if settings.base_url:
                lines.append(
                    "".join(f'<img src="{u}" width="150"> ' for u in _image_urls(settings, post))
                )
                lines.append("")
        _summary("\n".join(lines))
        return

    problems = preflight(settings, load_products(settings))
    if problems:
        raise AgentError("Live-Posting nicht möglich:\n- " + "\n- ".join(problems))
    client = _client()
    channel = client.tiktok_channel_id(os.environ.get("BUFFER_CHANNEL_ID") or None)
    scheduling = settings.section("publish").get("scheduling_type", "automatic")
    now = utcnow()
    failures: list[str] = []
    for post in state.posts:
        if post.get("status") != "created":
            continue
        if parse_time(post["due_at"]) <= now + timedelta(minutes=5):
            post["status"] = "missed"
            state.save()
            continue
        try:
            urls = _image_urls(settings, post)
            _wait_until_online(urls[0])
            post["buffer_id"] = client.create_photo_post(
                channel, post["caption"], post["title"], urls, post["due_at"], scheduling
            )
            post["status"] = "scheduled"
            print(f"Geplant: {post['due_at']} | {post['hook']}")
        except AgentError as exc:
            post["error"] = str(exc)
            failures.append(f"{post['id']}: {exc}")
        state.save()
    if failures:
        raise AgentError("Fehlgeschlagen:\n- " + "\n- ".join(failures))


def cmd_doctor(settings: Settings, state: State) -> None:
    products = load_products(settings)
    print(f"Produkte: {len(products)}, davon echt: {len(usable_products(products, True))}")
    problems = preflight(settings, products)
    print("Bereit für Live-Posting." if not problems else "Noch offen:\n- " + "\n- ".join(problems))
    finder = research.Finder.from_env(settings)
    print(f"Automatische Produktsuche: {'eingerichtet' if finder else 'noch kein Amazon-API-Zugang'}")
    if os.environ.get("BUFFER_API_KEY"):
        print("TikTok-Kanal:", _client().tiktok_channel_id(os.environ.get("BUFFER_CHANNEL_ID")))


COMMANDS = {
    "analyze": cmd_analyze,
    "create": cmd_create,
    "site": cmd_site,
    "publish": cmd_publish,
    "doctor": cmd_doctor,
}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="agent")
    parser.add_argument("command", choices=sorted(COMMANDS))
    parser.add_argument("--root", type=Path)
    args = parser.parse_args(argv)
    settings = Settings.load(args.root)
    state = State.load(settings)
    try:
        COMMANDS[args.command](settings, state)
    except AgentError as exc:
        print(f"FEHLER: {exc}", file=sys.stderr)
        return 1
    finally:
        state.save()
    return 0


if __name__ == "__main__":
    sys.exit(main())
