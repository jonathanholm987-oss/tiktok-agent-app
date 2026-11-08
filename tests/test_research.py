import json
import random
from datetime import datetime, timezone

import yaml

from agent import research
from agent.__main__ import main
from agent.buffer import BufferClient
from agent.core import Settings, State, iso_z
from tests.test_agent import FakeResponse, project  # noqa: F401


def item(asin="B0TESTASIN", title="ACME Kabelhalter 5er Set", bullets=None, rank=None):
    data = {
        "asin": asin,
        "detailPageURL": f"https://www.amazon.de/dp/{asin}?tag=test-21&linkCode=ogi",
        "itemInfo": {
            "title": {"displayValue": title},
            "features": {
                "displayValues": bullets
                or [
                    "【ORDNUNG】Hält fünf Kabel an der Tischkante",
                    "Klebt sicher, ganz ohne Bohren",
                    "Aus weichem Silikon, lässt sich abwischen",
                ]
            },
        },
    }
    if rank:
        data["browseNodeInfo"] = {"websiteSalesRank": {"salesRank": rank}}
    return data


class Router:
    def __init__(self, items=None, status=200):
        self.items, self.status, self.calls = items or [], status, []

    def post(self, url, headers, json, timeout):
        self.calls.append((url, headers, json))
        if "auth/o2/token" in url:
            return FakeResponse({"access_token": "tok", "expires_in": 3600})
        if self.status != 200:
            return FakeResponse({"reason": "AssociateNotEligible"}, self.status)
        if url.endswith("searchItems"):
            return FakeResponse({"searchResult": {"items": self.items}})
        if url.endswith("getItems"):
            return FakeResponse({"itemResults": {"items": [i for i in self.items if i["asin"] in json["itemIds"]]}})
        if "buffer.com" in url:
            return FakeResponse({"data": {"createPost": {"post": {"id": "buf"}}}})
        raise AssertionError(url)


def enable(project, monkeypatch, router, live=False, real_list=False):
    config = yaml.safe_load((project / "config.yaml").read_text(encoding="utf-8"))
    config["amazon"]["associate_tag"] = "test-21"
    config["legal"]["impressum"] = "Max Muster"
    (project / "config.yaml").write_text(yaml.safe_dump(config, allow_unicode=True), encoding="utf-8")
    if not real_list:
        (project / "data" / "products.yaml").write_text("products: []\n", encoding="utf-8")
    monkeypatch.setenv("AMAZON_CREDENTIAL_ID", "id")
    monkeypatch.setenv("AMAZON_CREDENTIAL_SECRET", "secret")
    monkeypatch.setattr(research.requests, "Session", lambda: router)
    if live:
        monkeypatch.setenv("LIVE_POSTING", "true")
        monkeypatch.setenv("BUFFER_API_KEY", "key")
        monkeypatch.setenv("BUFFER_CHANNEL_ID", "chan")
        monkeypatch.setenv("SITE_BASE_URL", "https://me.github.io/repo")
        monkeypatch.setattr("agent.__main__._client", lambda: BufferClient("key", router, pause=lambda s: None))
        monkeypatch.setattr("agent.__main__._wait_until_online", lambda url: None)


def posts(project):
    return json.loads((project / "state" / "posts.json").read_text(encoding="utf-8"))


def test_request_matches_documented_api(project, monkeypatch):
    router = Router([item()])
    enable(project, monkeypatch, router)
    assert main(["create", "--root", str(project)]) == 0
    token_url, _, token_body = router.calls[0]
    assert token_url == "https://api.amazon.co.uk/auth/o2/token"
    assert token_body["scope"] == "creatorsapi::default"
    url, headers, body = router.calls[1]
    assert url == "https://creatorsapi.amazon/catalog/v1/searchItems"
    assert headers["x-marketplace"] == "www.amazon.de" and headers["Authorization"] == "Bearer tok"
    assert body["partnerTag"] == "test-21" and body["itemCount"] == 10


def test_full_automatic_flow_without_product_list(project, monkeypatch):
    router = Router([item("B0TESTASI1"), item("B0TESTASI2", title="ACME Halter")])
    enable(project, monkeypatch, router, live=True)
    assert main(["create", "--root", str(project)]) == 0
    assert main(["site", "--root", str(project)]) == 0
    assert main(["publish", "--root", str(project)]) == 0
    saved = posts(project)
    assert len(saved) == 2 and all(p["status"] == "scheduled" for p in saved)
    assert saved[0]["product_id"] != saved[1]["product_id"]
    assert all(p["research_query"] for p in saved)
    page = (project / "site" / "index.html").read_text(encoding="utf-8")
    assert "linkCode=ogi" in page and "ACME Kabelhalter 5er Set" in page


def test_amazon_text_is_not_stored_permanently(project, monkeypatch):
    router = Router([item()])
    enable(project, monkeypatch, router, live=True)
    assert main(["create", "--root", str(project)]) == 0
    for name in ("posts.json", "catalog.json"):
        text = (project / "state" / name).read_text(encoding="utf-8")
        assert "ACME" not in text and "Silikon, lässt" not in text, name


def test_restricted_and_promotional_items_are_skipped():
    seed = {"query": "q", "problem": "p", "short": "s", "category": "c"}
    assert research.to_product(item(title="Vitamin D3 Kapseln"), seed, 1) is None
    bullets = ["Bestseller 2026", "Jetzt 20 % Rabatt", "Hält gut", "Aus Metall"]
    assert research.to_product(item(bullets=bullets), seed, 1) is None
    assert research.to_product(item(), seed, 1).facts[0].startswith("【ORDNUNG】")


def test_recently_used_product_is_skipped(project, monkeypatch):
    now = datetime(2026, 11, 8, tzinfo=timezone.utc)
    router = Router([item("B0TESTASI1", rank=5), item("B0TESTASI2", rank=50)])
    enable(project, monkeypatch, router)
    settings = Settings.load(project)
    finder = research.Finder.from_env(settings)
    state = State(path=project / "s.json")
    first = next(finder.candidates(state, now, random.Random(1), 1))
    assert first.asin == "B0TESTASI1"
    state.posts.append({"status": "scheduled", "due_at": iso_z(now), "product_id": "B0TESTASI1"})
    assert next(finder.candidates(state, now, random.Random(1), 1)).asin == "B0TESTASI2"


def test_no_eligibility_falls_back_to_list(project, monkeypatch, capsys):
    router = Router(status=403)
    enable(project, monkeypatch, router, real_list=True)
    assert main(["create", "--root", str(project)]) == 0
    assert all(p["product_id"].startswith("DEMO") for p in posts(project))
    assert "Automatische Produktsuche nicht verfügbar" in capsys.readouterr().out


def test_no_source_at_all_fails_visibly(project, monkeypatch, capsys):
    router = Router(status=403)
    enable(project, monkeypatch, router)
    assert main(["create", "--root", str(project)]) == 1
    assert "keine Slideshow" in capsys.readouterr().err


def test_shortened_amazon_bullets_become_valid_fallback(project, monkeypatch):
    router = Router([item(bullets=[
        "ROBUST: Das stabile Gehäuse aus Metall hält auch schweren Kabeln stand, weil die Klebefläche besonders groß ausfällt und lange hält",
        "Einfache Montage ohne Werkzeug",
        "Passt an Tischkanten bis 3 cm",
    ])])
    enable(project, monkeypatch, router)
    assert main(["create", "--root", str(project)]) == 0
    assert posts(project)[0]["generator"] == "fallback"
