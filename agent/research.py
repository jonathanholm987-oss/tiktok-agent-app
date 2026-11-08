"""Findet Produkte automatisch über Amazons Creators API.

Amazons Lizenz erlaubt das Speichern von Texten nur 24 Stunden und von Bildern gar
nicht. Deshalb bleiben Titel und Produktmerkmale im Arbeitsspeicher; dauerhaft
gespeichert werden nur ASIN und die eigenen Formulierungen des Agenten.
"""
from __future__ import annotations

import json
import os
import re
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Iterator

import requests

from . import analysis, content
from .core import AgentError, ASIN_RE, Product, Settings, State, amazon_api_configured, iso_z, parse_time

API_BASE = "https://creatorsapi.amazon/catalog/v1"
EU_TOKEN_URL = "https://api.amazon.co.uk/auth/o2/token"
RESOURCES = [
    "itemInfo.title",
    "itemInfo.features",
    "browseNodeInfo.websiteSalesRank",
    "offersV2.listings.availability",
]
MAX_CATALOG = 30
MAX_EXTRA_SEEDS = 40

# Produkte, für die TikTok oder Heilmittel- und Jugendschutzrecht besondere Regeln kennen.
RESTRICTED = re.compile(
    r"erotik|\bsex|kondom|gleitgel|vibrator|nahrungsergänz|vitamin|kapseln|abnehm|schlank"
    r"|medizin|arznei|heilt|blutdruck|messer|waffe|pfefferspray|alkohol|\bwein\b|\bbier\b"
    r"|tabak|zigarette|\bvape|shisha|\bcbd\b|hanf|baby|säugling|kleinkind|feuerwerk|lotto"
)


class CreatorsAccessError(AgentError):
    """Amazon verweigert den Zugang, meist wegen fehlender Verkäufe."""


class CreatorsClient:
    def __init__(self, credential_id, secret, tag, domain, token_url=EU_TOKEN_URL, session=None, pause=time.sleep):
        self.credential_id, self.secret, self.tag = credential_id, secret, tag
        self.marketplace = f"www.{domain}"
        self.token_url = token_url
        self.session = session or requests.Session()
        self.pause = pause
        self._token: str | None = None
        self._expires = 0.0

    def _access_token(self) -> str:
        if self._token and time.time() < self._expires:
            return self._token
        response = self.session.post(
            self.token_url,
            headers={"Content-Type": "application/json"},
            json={
                "grant_type": "client_credentials",
                "client_id": self.credential_id,
                "client_secret": self.secret,
                "scope": "creatorsapi::default",
            },
            timeout=30,
        )
        if response.status_code != 200:
            raise CreatorsAccessError(f"Amazon-Anmeldung abgelehnt (HTTP {response.status_code})")
        payload = response.json()
        self._token = payload["access_token"]
        self._expires = time.time() + int(payload.get("expires_in", 3600)) - 60
        return self._token

    def _call(self, operation: str, body: dict) -> dict:
        last_error = "unbekannt"
        for attempt in range(4):
            try:
                response = self.session.post(
                    f"{API_BASE}/{operation}",
                    headers={
                        "Authorization": f"Bearer {self._access_token()}",
                        "Content-Type": "application/json",
                        "x-marketplace": self.marketplace,
                    },
                    json={"partnerTag": self.tag, "marketplace": self.marketplace, **body},
                    timeout=30,
                )
            except requests.RequestException as exc:
                last_error = str(exc)
                self.pause(2**attempt)
                continue
            if response.status_code in (401, 403):
                raise CreatorsAccessError(f"Amazon verweigert den Zugang (HTTP {response.status_code})")
            if response.status_code == 429 or response.status_code >= 500:
                last_error = f"HTTP {response.status_code}"
                self.pause(2**attempt)
                continue
            if response.status_code >= 400:
                raise AgentError(f"Amazon: HTTP {response.status_code}: {response.text[:200]}")
            return response.json()
        raise AgentError(f"Amazon nicht erreichbar: {last_error}")

    def search(self, keywords: str) -> list[dict]:
        data = self._call(
            "searchItems",
            {"keywords": keywords, "itemCount": 10, "sortBy": "Featured", "resources": RESOURCES},
        )
        return (data.get("searchResult") or {}).get("items") or []

    def details(self, asins: list[str]) -> dict[str, dict]:
        found: dict[str, dict] = {}
        for start in range(0, len(asins), 10):
            data = self._call(
                "getItems",
                {"itemIds": asins[start : start + 10], "itemIdType": "ASIN", "resources": ["itemInfo.title"]},
            )
            for item in (data.get("itemResults") or {}).get("items") or []:
                found[item.get("asin")] = item
        return found


class Catalog:
    """Merkt sich, welche ASIN unter welcher Nummer auf der Link-Seite steht."""

    def __init__(self, path: Path, data: dict):
        self.path, self.data = path, data

    @classmethod
    def load(cls, settings: Settings) -> "Catalog":
        path = settings.root / "state" / "catalog.json"
        data = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
        data.setdefault("next_nr", 1)
        data.setdefault("items", [])
        return cls(path, data)

    def number_for(self, asin: str, minimum: int) -> int:
        for item in self.data["items"]:
            if item["asin"] == asin:
                return item["nr"]
        return max(self.data["next_nr"], minimum)

    def remember(self, product: Product, now: datetime) -> None:
        self.data["items"] = [i for i in self.data["items"] if i["asin"] != product.asin]
        self.data["items"].append(
            {
                "nr": product.nr,
                "asin": product.asin,
                "short": product.short,
                "category": product.category,
                "problem": product.problem,
                "last_used": iso_z(now),
            }
        )
        self.data["items"] = sorted(self.data["items"], key=lambda i: i["last_used"])[-MAX_CATALOG:]
        self.data["next_nr"] = max(self.data["next_nr"], product.nr + 1)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(self.data, ensure_ascii=False, indent=1), encoding="utf-8")


def clean_feature(text: str) -> str:
    return re.sub(r"\s+", " ", str(text)).strip()[:300]


def to_product(item: dict, seed: dict, nr: int) -> Product | None:
    asin = str(item.get("asin") or "")
    info = item.get("itemInfo") or {}
    title = clean_feature((info.get("title") or {}).get("displayValue") or "")
    bullets = (info.get("features") or {}).get("displayValues") or []
    if not ASIN_RE.match(asin) or not title:
        return None
    if RESTRICTED.search(f"{title} {' '.join(map(str, bullets))}".lower()):
        return None
    offers = json.dumps(item.get("offersV2") or {}).lower()
    if "out_of_stock" in offers or "unavailable" in offers:
        return None
    facts = [clean_feature(b) for b in bullets if b and not content.has_forbidden(str(b))][:6]
    if len(facts) < 3:
        return None
    return Product(
        nr=nr,
        asin=asin,
        name=title,
        short=seed["short"],
        category=seed["category"],
        problem=seed["problem"],
        audience=seed.get("audience", ""),
        facts=facts,
        auto=True,
        query=seed["query"],
        url=item.get("detailPageURL"),
    )


def _sales_rank(item: dict) -> float:
    rank = ((item.get("browseNodeInfo") or {}).get("websiteSalesRank") or {}).get("salesRank")
    return float(rank) if isinstance(rank, (int, float)) else float("inf")


def valid_seed(seed) -> bool:
    if not isinstance(seed, dict):
        return False
    if not all(isinstance(seed.get(k), str) and seed[k].strip() for k in ("query", "problem", "short", "category")):
        return False
    text = " ".join(str(v) for v in seed.values()).lower()
    return not RESTRICTED.search(text) and not content.has_forbidden(text)


class Finder:
    def __init__(self, client: CreatorsClient, settings: Settings):
        self.client, self.settings = client, settings
        self.catalog = Catalog.load(settings)
        self.available = True
        self.reason = ""
        self.seeds_path = settings.root / "state" / "seeds.json"

    @classmethod
    def from_env(cls, settings: Settings) -> "Finder | None":
        if not amazon_api_configured():
            return None
        amazon = settings.section("amazon")
        tag = (amazon.get("associate_tag") or "").strip()
        if not tag:
            return None
        client = CreatorsClient(
            os.environ["AMAZON_CREDENTIAL_ID"],
            os.environ["AMAZON_CREDENTIAL_SECRET"],
            tag,
            amazon.get("domain", "amazon.de"),
            amazon.get("token_url", EU_TOKEN_URL),
        )
        return cls(client, settings)

    def _extra(self) -> dict:
        if self.seeds_path.exists():
            return json.loads(self.seeds_path.read_text(encoding="utf-8"))
        return {"updated_at": None, "seeds": []}

    def seeds(self) -> list[dict]:
        configured = self.settings.section("research").get("seeds") or []
        merged: dict[str, dict] = {}
        for seed in [*configured, *self._extra()["seeds"]]:
            if valid_seed(seed):
                merged[seed["query"]] = seed
        return list(merged.values())

    def extend_seeds(self, state: State, now: datetime) -> None:
        """Lässt Claude einmal pro Woche neue Suchideen ergänzen."""
        api_key = os.environ.get("ANTHROPIC_API_KEY")
        extra = self._extra()
        if not api_key or (extra["updated_at"] and now - parse_time(extra["updated_at"]) < timedelta(days=7)):
            return
        ranking = sorted(analysis.group_scores(state.posts, "research_query").items(), key=lambda x: -x[1][0])
        known = ", ".join(s["query"] for s in self.seeds())
        prompt = (
            "Schlage 5 neue Suchbegriffe für praktische, günstige Alltagsprodukte auf amazon.de vor, "
            "die in einer TikTok-Slideshow gut erklärbar sind. Keine Gesundheits-, Baby-, Alkohol-, "
            "Waffen- oder Erotikprodukte.\n"
            f"Bisher am besten: {', '.join(n for n, _ in ranking[:5]) or 'noch keine Daten'}.\n"
            f"Schon vorhanden: {known}.\n"
            'Antworte nur mit JSON: {"seeds":[{"query":"","problem":"","short":"","category":"","audience":""}]}\n'
            '"problem" muss in den Satz "Schluss mit ..." passen. "short" ist ein Name wie "Der Kabelhalter" '
            "ohne Marke."
        )
        model = self.settings.section("content").get("model", "claude-sonnet-5-5")
        try:
            proposed = content._ask_claude(prompt, model, api_key).get("seeds") or []
        except (requests.RequestException, ValueError) as exc:
            print(f"Keine neuen Suchideen: {exc}")
            return
        existing = {s["query"] for s in extra["seeds"]}
        fresh = [s for s in proposed if valid_seed(s) and s["query"] not in existing]
        extra = {"updated_at": iso_z(now), "seeds": (extra["seeds"] + fresh)[-MAX_EXTRA_SEEDS:]}
        self.seeds_path.parent.mkdir(parents=True, exist_ok=True)
        self.seeds_path.write_text(json.dumps(extra, ensure_ascii=False, indent=1), encoding="utf-8")

    def candidates(self, state: State, now: datetime, rng, minimum_nr: int) -> Iterator[Product]:
        research = self.settings.section("research")
        cooldown = timedelta(days=int(self.settings.section("content").get("product_cooldown_days", 7)))
        query_pause = timedelta(days=int(research.get("query_cooldown_days", 3)))
        recent_products, recent_queries = set(), set()
        for post in state.posts:
            if post.get("status") not in ("created", "scheduled", "preview"):
                continue
            age = now - parse_time(post["due_at"])
            if age < cooldown:
                recent_products.add(post["product_id"])
            if age < query_pause and post.get("research_query"):
                recent_queries.add(post["research_query"])

        seeds = {s["query"]: s for s in self.seeds()}
        queries = [q for q in seeds if q not in recent_queries] or list(seeds)
        scores = analysis.group_scores(state.posts, "research_query")
        exploration = float(self.settings.section("content").get("exploration", 0.25))
        for _ in range(min(int(research.get("max_searches_per_post", 4)), len(queries))):
            query = analysis._choose(queries, scores, exploration, rng)
            queries.remove(query)
            try:
                items = self.client.search(query)
            except CreatorsAccessError as exc:
                self.available = False
                self.reason = str(exc)
                return
            except AgentError as exc:
                print(f"Suche '{query}' fehlgeschlagen: {exc}")
                continue
            for item in sorted(items, key=_sales_rank):
                if item.get("asin") in recent_products:
                    continue
                nr = self.catalog.number_for(str(item.get("asin")), minimum_nr)
                product = to_product(item, seeds[query], nr)
                if product:
                    yield product

    def catalog_products(self) -> list[Product]:
        """Produkte der Link-Seite, Titel und Links frisch von Amazon."""
        items = self.catalog.data["items"]
        details: dict[str, dict] = {}
        if items:
            try:
                details = self.client.details([i["asin"] for i in items])
            except AgentError as exc:
                print(f"Produkttitel nicht aktualisiert: {exc}")
        products = []
        for item in items:
            live = details.get(item["asin"]) or {}
            title = ((live.get("itemInfo") or {}).get("title") or {}).get("displayValue")
            products.append(
                Product(
                    nr=item["nr"],
                    asin=item["asin"],
                    name=title or item["short"],
                    short=item["short"],
                    category=item["category"],
                    problem=item["problem"],
                    audience="",
                    facts=[],
                    auto=True,
                    url=live.get("detailPageURL"),
                )
            )
        return products
