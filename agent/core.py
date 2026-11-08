"""Konfiguration, Produktliste und gespeicherter Zustand."""
from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import yaml

ASIN_RE = re.compile(r"^[A-Z0-9]{10}$")


class AgentError(Exception):
    """Fehler, der dem Nutzer verständlich angezeigt wird."""


@dataclass
class Settings:
    root: Path
    raw: dict
    live: bool
    base_url: str

    @classmethod
    def load(cls, root: Path | None = None) -> "Settings":
        root = Path(root or os.environ.get("AGENT_ROOT") or Path.cwd())
        raw = yaml.safe_load((root / "config.yaml").read_text(encoding="utf-8"))
        return cls(
            root=root,
            raw=raw,
            live=os.environ.get("LIVE_POSTING", "").strip().lower() == "true",
            base_url=os.environ.get("SITE_BASE_URL", "").rstrip("/"),
        )

    @property
    def tz(self) -> ZoneInfo:
        return ZoneInfo(self.raw["timezone"])

    def section(self, name: str) -> dict:
        return self.raw.get(name) or {}


@dataclass
class Product:
    nr: int
    asin: str
    name: str
    short: str
    category: str
    problem: str
    audience: str
    facts: list[str]
    image: str | None = None
    demo: bool = False
    active: bool = True
    auto: bool = False
    query: str | None = None
    url: str | None = None

    @property
    def id(self) -> str:
        return self.asin

    def link(self, domain: str, tag: str) -> str:
        return self.url or f"https://www.{domain}/dp/{self.asin}?tag={tag}"


def load_products(settings: Settings) -> list[Product]:
    data = yaml.safe_load(
        (settings.root / "data" / "products.yaml").read_text(encoding="utf-8")
    )
    products: list[Product] = []
    seen_nr: set[int] = set()
    seen_asin: set[str] = set()
    for row in data.get("products") or []:
        label = row.get("name") or row.get("asin") or "Unbekannt"
        try:
            product = Product(
                nr=int(row["nr"]),
                asin=str(row["asin"]).strip(),
                name=str(row["name"]).strip(),
                short=str(row.get("short") or row["name"]).strip(),
                category=str(row["category"]).strip(),
                problem=str(row["problem"]).strip(),
                audience=str(row.get("audience") or "").strip(),
                facts=[str(f).strip() for f in row["facts"]],
                image=row.get("image"),
                demo=bool(row.get("demo", False)),
                active=bool(row.get("active", True)),
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise AgentError(f"Produkt '{label}' ist unvollständig: {exc}") from exc
        if not ASIN_RE.match(product.asin):
            raise AgentError(f"Produkt '{label}': ungültige ASIN {product.asin!r}")
        if len(product.facts) < 3:
            raise AgentError(f"Produkt '{label}': mindestens 3 Fakten nötig")
        if product.nr in seen_nr or product.asin in seen_asin:
            raise AgentError(f"Produkt '{label}': Nummer oder ASIN doppelt")
        seen_nr.add(product.nr)
        seen_asin.add(product.asin)
        products.append(product)
    return products


def amazon_api_configured() -> bool:
    return bool(os.environ.get("AMAZON_CREDENTIAL_ID") and os.environ.get("AMAZON_CREDENTIAL_SECRET"))


def usable_products(products: list[Product], live: bool) -> list[Product]:
    return [p for p in products if p.active and not (live and p.demo)]


def preflight(settings: Settings, products: list[Product]) -> list[str]:
    """Gibt alles zurück, was Live-Posting verhindern muss."""
    problems: list[str] = []
    if not (settings.section("amazon").get("associate_tag") or "").strip():
        problems.append("amazon.associate_tag fehlt in config.yaml")
    if not (settings.section("legal").get("impressum") or "").strip():
        problems.append("legal.impressum fehlt in config.yaml")
    if not usable_products(products, live=True) and not amazon_api_configured():
        problems.append(
            "Keine Produktquelle: Amazon-API-Zugang fehlt "
            "(AMAZON_CREDENTIAL_ID, AMAZON_CREDENTIAL_SECRET) "
            "und data/products.yaml enthält kein echtes Produkt"
        )
    if not settings.base_url.startswith("https://"):
        problems.append("SITE_BASE_URL fehlt (GitHub Pages ist nicht eingerichtet)")
    if not os.environ.get("BUFFER_API_KEY"):
        problems.append("Secret BUFFER_API_KEY fehlt")
    return problems


@dataclass
class State:
    path: Path
    posts: list[dict] = field(default_factory=list)

    @classmethod
    def load(cls, settings: Settings) -> "State":
        path = settings.root / "state" / "posts.json"
        posts = json.loads(path.read_text(encoding="utf-8")) if path.exists() else []
        return cls(path=path, posts=posts)

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(
            json.dumps(self.posts, ensure_ascii=False, indent=1), encoding="utf-8"
        )
        tmp.replace(self.path)


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def parse_time(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def iso_z(value: datetime) -> str:
    return value.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")


def upcoming_slots(settings: Settings, state: State, now: datetime) -> list[datetime]:
    """Freie Posting-Zeiten in den nächsten 24 Stunden, in UTC."""
    publish = settings.section("publish")
    earliest = now + timedelta(minutes=int(publish.get("min_lead_minutes", 30)))
    latest = now + timedelta(hours=24)
    taken = {
        p["due_at"] for p in state.posts if p.get("status") in ("created", "scheduled")
    }
    local_today = now.astimezone(settings.tz).date()
    slots: list[datetime] = []
    for offset in (0, 1):
        day = local_today + timedelta(days=offset)
        for text in publish.get("posting_times", []):
            hour, minute = (int(x) for x in str(text).split(":"))
            slot = datetime(
                day.year, day.month, day.day, hour, minute, tzinfo=settings.tz
            ).astimezone(timezone.utc)
            if earliest <= slot <= latest and iso_z(slot) not in taken:
                slots.append(slot)
    return sorted(slots)
