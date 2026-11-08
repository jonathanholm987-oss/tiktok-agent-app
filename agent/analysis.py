"""Holt Kennzahlen, bewertet Posts und wählt die nächsten Inhalte aus."""
from __future__ import annotations

import math
import random
from collections import defaultdict
from datetime import datetime, timedelta

from .buffer import BufferClient
from .content import FORMATS
from .core import Product, Settings, State, parse_time

SHRINK = 3.0


def score(metrics: dict[str, float]) -> float:
    views = metrics.get("views") or metrics.get("impressions") or 0
    weighted = (
        views
        + 5 * metrics.get("reactions", 0)
        + 10 * metrics.get("comments", 0)
        + 15 * metrics.get("shares", 0)
    )
    return round(math.log1p(weighted), 3)


def refresh_metrics(state: State, client: BufferClient, settings: Settings, now: datetime) -> int:
    analysis = settings.section("analysis")
    min_age = timedelta(hours=int(analysis.get("min_age_hours", 48)))
    max_age = timedelta(days=int(analysis.get("stop_after_days", 14)))
    updated = 0
    for post in state.posts:
        if post.get("status") != "scheduled" or not post.get("buffer_id"):
            continue
        age = now - parse_time(post["due_at"])
        if not min_age <= age <= max_age:
            continue
        metrics, updated_at = client.post_metrics(post["buffer_id"])
        if metrics:
            post.update(metrics=metrics, metrics_updated_at=updated_at, score=score(metrics))
            updated += 1
    return updated


def rated(posts: list[dict]) -> list[dict]:
    return [p for p in posts if p.get("score") is not None]


def group_scores(posts: list[dict], key: str) -> dict[str, tuple[float, int]]:
    items = rated(posts)
    if not items:
        return {}
    overall = sum(p["score"] for p in items) / len(items)
    groups: dict[str, list[float]] = defaultdict(list)
    for post in items:
        groups[str(post.get(key))].append(post["score"])
    return {
        name: ((sum(values) + SHRINK * overall) / (len(values) + SHRINK), len(values))
        for name, values in groups.items()
    }


def _choose(options: list[str], scores: dict, exploration: float, rng: random.Random) -> str:
    unseen = [o for o in options if o not in scores]
    if unseen and (rng.random() < exploration or not scores):
        return rng.choice(unseen)
    if rng.random() < exploration:
        return rng.choice(options)
    return max(options, key=lambda o: (scores.get(o, (0.0, 0))[0], rng.random()))


def pick(
    products: list[Product],
    state: State,
    settings: Settings,
    now: datetime,
    rng: random.Random,
) -> tuple[Product, str]:
    content = settings.section("content")
    exploration = float(content.get("exploration", 0.25))
    active_posts = [p for p in state.posts if p.get("status") in ("created", "scheduled")]
    cooldown = timedelta(days=int(content.get("product_cooldown_days", 7)))
    last_used: dict[str, datetime] = {}
    for post in active_posts:
        when = parse_time(post["due_at"])
        last_used[post["product_id"]] = max(when, last_used.get(post["product_id"], when))
    rested = [
        p for p in products if p.id not in last_used or now - last_used[p.id] >= cooldown
    ]
    if rested:
        product_scores = group_scores(state.posts, "product_id")
        chosen = _choose([p.id for p in rested], product_scores, exploration, rng)
        product = next(p for p in rested if p.id == chosen)
    else:
        product = min(products, key=lambda p: last_used[p.id])
    return product, pick_format(state, settings, rng)


def pick_format(state: State, settings: Settings, rng: random.Random) -> str:
    exploration = float(settings.section("content").get("exploration", 0.25))
    active_posts = [p for p in state.posts if p.get("status") in ("created", "scheduled")]
    last_format = None
    if active_posts:
        last_format = max(active_posts, key=lambda p: p["due_at"]).get("format")
    formats = [f for f in FORMATS if f != last_format] or list(FORMATS)
    return _choose(formats, group_scores(state.posts, "format"), exploration, rng)


def learnings(posts: list[dict]) -> str:
    items = rated(posts)
    if len(items) < 3:
        return ""
    lines = [f"Ausgewertete Posts: {len(items)}."]
    formats = sorted(group_scores(posts, "format").items(), key=lambda x: -x[1][0])
    lines.append(f"Stärkstes Format: {formats[0][0]}. Schwächstes Format: {formats[-1][0]}.")
    ordered = sorted(items, key=lambda p: -p["score"])
    lines.append("Beste Hooks: " + " | ".join(p["hook"] for p in ordered[:3]))
    lines.append("Schwächste Hooks: " + " | ".join(p["hook"] for p in ordered[-3:]))
    return "\n".join(lines)


def report(posts: list[dict], settings: Settings) -> str:
    items = rated(posts)
    scheduled = [p for p in posts if p.get("status") == "scheduled"]
    lines = [
        "# TikTok-Agent: Auswertung",
        "",
        f"Geplant oder veröffentlicht: {len(scheduled)}. Mit Kennzahlen: {len(items)}.",
        "",
    ]
    if not items:
        lines.append("Noch keine Kennzahlen. Buffer liefert sie frühestens etwa 24 Stunden nach dem Post.")
        return "\n".join(lines) + "\n"

    def table(title: str, key: str) -> None:
        lines.extend([f"## {title}", "", "| Wert | Score | Posts |", "|---|---:|---:|"])
        ranking = sorted(group_scores(posts, key).items(), key=lambda x: -x[1][0])
        for name, (value, count) in ranking:
            lines.append(f"| {name} | {value:.2f} | {count} |")
        lines.append("")

    for post in items:
        post["_hour"] = parse_time(post["due_at"]).astimezone(settings.tz).strftime("%H:%M")
    table("Formate", "format")
    table("Kategorien", "category")
    table("Produkte", "product_name")
    if any(p.get("research_query") for p in items):
        table("Suchbegriffe", "research_query")
    table("Uhrzeiten", "_hour")
    lines.extend(["## Beste Posts", "", "| Hook | Score | Kennzahlen |", "|---|---:|---|"])
    for post in sorted(items, key=lambda p: -p["score"])[:10]:
        metrics = ", ".join(f"{k}: {v:g}" for k, v in sorted(post["metrics"].items()))
        lines.append(f"| {post['hook']} | {post['score']:.2f} | {metrics} |")
    for post in items:
        post.pop("_hour", None)
    return "\n".join(lines) + "\n"
