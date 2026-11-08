import json
import random
import shutil
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
import yaml
from PIL import Image

from agent import analysis, content
from agent.__main__ import main
from agent.buffer import BufferClient
from agent.core import AgentError, Product, Settings, State, iso_z, upcoming_slots

REPO = Path(__file__).resolve().parent.parent


def product(nr=1, asin="B000000001"):
    return Product(
        nr=nr,
        asin=asin,
        name=f"Produkt {chr(64 + nr)}",
        short="Der Halter",
        category="Zuhause",
        problem="Kabelsalat",
        audience="",
        facts=["Hält 5 Kabel", "Klebt ohne Bohren", "Aus Silikon"],
    )


@pytest.fixture
def project(tmp_path, monkeypatch):
    for name in ("config.yaml", "data"):
        source = REPO / name
        if source.is_dir():
            shutil.copytree(source, tmp_path / name)
        else:
            shutil.copy(source, tmp_path / name)
    for var in (
        "BUFFER_API_KEY", "ANTHROPIC_API_KEY", "LIVE_POSTING", "SITE_BASE_URL", "BUFFER_CHANNEL_ID",
        "AMAZON_CREDENTIAL_ID", "AMAZON_CREDENTIAL_SECRET",
    ):
        monkeypatch.delenv(var, raising=False)
    return tmp_path


def make_real(project):
    config = yaml.safe_load((project / "config.yaml").read_text(encoding="utf-8"))
    config["amazon"]["associate_tag"] = "test-21"
    config["legal"]["impressum"] = "Max Muster\nMusterweg 1"
    (project / "config.yaml").write_text(yaml.safe_dump(config), encoding="utf-8")
    (project / "data" / "products.yaml").write_text(
        yaml.safe_dump(
            {
                "products": [
                    {
                        "nr": 1,
                        "asin": "B000000001",
                        "name": "Halter",
                        "category": "Zuhause",
                        "problem": "Kabelsalat",
                        "facts": ["Hält 5 Kabel", "Klebt ohne Bohren", "Aus Silikon"],
                    }
                ]
            }
        ),
        encoding="utf-8",
    )


class FakeResponse:
    def __init__(self, payload, status=200):
        self.payload, self.status_code, self.text = payload, status, json.dumps(payload)

    def json(self):
        return self.payload


class FakeSession:
    def __init__(self, responses):
        self.responses, self.calls = list(responses), []

    def post(self, url, headers, json, timeout):
        self.calls.append({"headers": headers, "body": json})
        return self.responses.pop(0)


def settings_for(project):
    return Settings.load(project)


def good_draft(**changes):
    draft = {
        "hook": "Nervt dich Kabelsalat?",
        "slides": [{"headline": "Hält 5 Kabel", "body": ""}, {"headline": "Klebt ohne Bohren", "body": ""}],
        "caption": "Ordnung an der Tischkante.",
        "title": "Kabelsalat lösen",
        "hashtags": ["ordnung"],
    }
    draft.update(changes)
    return draft


def test_valid_draft_passes(project):
    assert content.validate(good_draft(), product(), settings_for(project)) == []


@pytest.mark.parametrize(
    "changes",
    [
        {"caption": "Jetzt nur 9,99 €"},
        {"hook": "Ich habe es getestet"},
        {"caption": "Tausende Kunden sind begeistert"},
        {"hook": "Hält 50 Kabel"},
        {"caption": "Nur heute verfügbar"},
        {"slides": [{"headline": "x", "body": ""}]},
    ],
)
def test_bad_drafts_are_rejected(project, changes):
    assert content.validate(good_draft(**changes), product(), settings_for(project))


def test_every_format_fallback_is_valid(project):
    settings = settings_for(project)
    for fmt in content.FORMATS:
        draft = content.fallback_draft(product(), fmt, settings)
        assert content.validate(draft, product(), settings) == [], fmt


def test_caption_starts_with_disclosure(project):
    text = content.build_caption(good_draft(), product(), settings_for(project))
    assert text.startswith("Werbung | ")
    assert content.DISCLOSURE in text
    assert "#werbung" in text


def test_preview_flow_renders_tiktok_slides_without_posting(project, capsys):
    assert main(["create", "--root", str(project)]) == 0
    assert main(["site", "--root", str(project)]) == 0
    assert main(["publish", "--root", str(project)]) == 0
    posts = json.loads((project / "state" / "posts.json").read_text(encoding="utf-8"))
    assert posts and all(p["status"] == "preview" for p in posts)
    assert all("buffer_id" not in p for p in posts)
    folder = project / "site" / "slides" / posts[0]["id"]
    files = sorted(folder.glob("*.jpg"))
    assert 4 <= len(files) <= 10
    with Image.open(files[0]) as image:
        assert image.size == (1080, 1920) and image.format == "JPEG"
    assert "Noch keine Produkte" in (project / "site" / "index.html").read_text(encoding="utf-8")
    assert "nichts wurde gepostet" in capsys.readouterr().out


def test_second_preview_run_does_not_duplicate_slots(project):
    main(["create", "--root", str(project)])
    first = json.loads((project / "state" / "posts.json").read_text(encoding="utf-8"))
    main(["create", "--root", str(project)])
    second = json.loads((project / "state" / "posts.json").read_text(encoding="utf-8"))
    assert len(first) == len(second)


def test_live_is_blocked_with_only_demo_products(project, monkeypatch, capsys):
    monkeypatch.setenv("LIVE_POSTING", "true")
    assert main(["create", "--root", str(project)]) == 1
    error = capsys.readouterr().err
    assert "associate_tag" in error and "impressum" in error and "Amazon-API" in error


def test_live_publish_sends_expected_payload(project, monkeypatch):
    make_real(project)
    monkeypatch.setenv("LIVE_POSTING", "true")
    monkeypatch.setenv("BUFFER_API_KEY", "key")
    monkeypatch.setenv("BUFFER_CHANNEL_ID", "chan1")
    monkeypatch.setenv("SITE_BASE_URL", "https://me.github.io/repo/")
    session = FakeSession(
        [FakeResponse({"data": {"createPost": {"post": {"id": f"buf{i}"}}}}) for i in range(4)]
    )
    monkeypatch.setattr("agent.__main__._client", lambda: BufferClient("key", session, pause=lambda s: None))
    monkeypatch.setattr("agent.__main__._wait_until_online", lambda url: None)
    assert main(["create", "--root", str(project)]) == 0
    assert main(["publish", "--root", str(project)]) == 0
    posts = json.loads((project / "state" / "posts.json").read_text(encoding="utf-8"))
    assert all(p["status"] == "scheduled" and p["buffer_id"] for p in posts)
    body = session.calls[0]["body"]["variables"]["input"]
    assert body["channelId"] == "chan1"
    assert body["schedulingType"] == "automatic" and body["mode"] == "customScheduled"
    assert body["assets"][0]["image"]["url"].startswith("https://me.github.io/repo/slides/")
    assert body["text"].startswith("Werbung | ")
    assert len(body["metadata"]["tiktok"]["title"]) <= 90
    assert len(body["assets"]) <= 10
    assert main(["site", "--root", str(project)]) == 0
    page = (project / "site" / "index.html").read_text(encoding="utf-8")
    assert "amazon.de/dp/B000000001?tag=test-21" in page
    assert "Max Muster<br>Musterweg 1" in page
    assert content.DISCLOSURE in page


def test_facts_may_contain_words_that_are_otherwise_blocked(project):
    item = product()
    item.facts = ["Aus 100 % Baumwolle", "Spart Platz im Schrank", "Waschbar bei 40 Grad"]
    settings = settings_for(project)
    for fmt in content.FORMATS:
        assert content.validate(content.fallback_draft(item, fmt, settings), item, settings) == []
    assert content.validate(good_draft(caption="Heute 20 % sparen"), item, settings)


def test_buffer_rejection_is_reported():
    session = FakeSession([FakeResponse({"data": {"createPost": {"message": "bad url"}}})])
    client = BufferClient("k", session, pause=lambda s: None)
    with pytest.raises(AgentError, match="bad url"):
        client.create_photo_post("c", "t", "x", ["https://a/b.jpg"], "2026-01-01T00:00:00.000Z")


def test_buffer_retries_rate_limit():
    session = FakeSession(
        [FakeResponse({}, 429), FakeResponse({"data": {"post": {"metrics": [{"type": "views", "value": 3}], "metricsUpdatedAt": "x"}}})]
    )
    metrics, _ = BufferClient("k", session, pause=lambda s: None).post_metrics("p")
    assert metrics == {"views": 3.0}


def test_score_rewards_comments_and_handles_missing():
    assert analysis.score({}) == 0
    assert analysis.score({"views": 100, "comments": 5}) > analysis.score({"views": 100})


def test_metrics_only_for_old_enough_posts(project):
    now = datetime(2026, 11, 8, tzinfo=timezone.utc)
    state = State(path=project / "s.json")
    state.posts = [
        {"status": "scheduled", "buffer_id": "old", "due_at": iso_z(now - timedelta(days=3))},
        {"status": "scheduled", "buffer_id": "new", "due_at": iso_z(now - timedelta(hours=2))},
    ]
    session = FakeSession([FakeResponse({"data": {"post": {"metrics": [{"type": "views", "value": 9}]}}})])
    client = BufferClient("k", session, pause=lambda s: None)
    assert analysis.refresh_metrics(state, client, settings_for(project), now) == 1
    assert state.posts[0]["score"] > 0 and "score" not in state.posts[1]


def test_pick_prefers_winner_and_respects_cooldown(project):
    now = datetime(2026, 11, 8, tzinfo=timezone.utc)
    settings = settings_for(project)
    settings.raw["content"]["exploration"] = 0
    a, b, c = product(1, "B000000001"), product(2, "B000000002"), product(3, "B000000003")
    old = iso_z(now - timedelta(days=30))
    state = State(path=project / "s.json")
    state.posts = [
        {"status": "scheduled", "due_at": old, "product_id": a.id, "format": "gruende", "score": 9.0},
        {"status": "scheduled", "due_at": old, "product_id": b.id, "format": "fuer_wen", "score": 1.0},
    ]
    chosen, _ = analysis.pick([a, b], state, settings, now, random.Random(1))
    assert chosen.id == a.id
    state.posts.append({"status": "scheduled", "due_at": iso_z(now - timedelta(days=1)), "product_id": a.id, "format": "gruende"})
    chosen, fmt = analysis.pick([a, b, c], state, settings, now, random.Random(1))
    assert chosen.id != a.id and fmt != "gruende"


def test_slots_use_local_time_and_skip_taken(project):
    settings = settings_for(project)
    now = datetime(2026, 11, 8, 3, 41, tzinfo=timezone.utc)
    state = State(path=project / "s.json")
    slots = upcoming_slots(settings, state, now)
    assert [s.strftime("%H:%M") for s in slots] == ["11:30", "17:30"]
    state.posts.append({"status": "scheduled", "due_at": iso_z(slots[0])})
    assert upcoming_slots(settings, state, now) == [slots[1]]
