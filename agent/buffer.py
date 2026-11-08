"""Schmaler Client für Buffers GraphQL-API."""
from __future__ import annotations

import json
import time

import requests

from .core import AgentError

ENDPOINT = "https://api.buffer.com"

CREATE_POST = """
mutation CreatePost($input: CreatePostInput!) {
  createPost(input: $input) {
    ... on PostActionSuccess { post { id } }
    ... on MutationError { message }
  }
}
"""


class BufferClient:
    def __init__(self, api_key: str, session: requests.Session | None = None, pause=time.sleep):
        self.session = session or requests.Session()
        self.api_key = api_key
        self.pause = pause

    def graphql(self, query: str, variables: dict | None = None) -> dict:
        last_error = "unbekannt"
        for attempt in range(4):
            try:
                response = self.session.post(
                    ENDPOINT,
                    headers={
                        "Authorization": f"Bearer {self.api_key}",
                        "Content-Type": "application/json",
                    },
                    json={"query": query, "variables": variables or {}},
                    timeout=60,
                )
            except requests.RequestException as exc:
                last_error = str(exc)
                self.pause(2**attempt)
                continue
            if response.status_code == 429 or response.status_code >= 500:
                last_error = f"HTTP {response.status_code}"
                self.pause(2**attempt)
                continue
            if response.status_code >= 400:
                raise AgentError(f"Buffer: HTTP {response.status_code}: {response.text[:300]}")
            payload = response.json()
            if payload.get("errors"):
                messages = "; ".join(e.get("message", "?") for e in payload["errors"])
                raise AgentError(f"Buffer: {messages}")
            return payload["data"]
        raise AgentError(f"Buffer nicht erreichbar: {last_error}")

    def organization_id(self) -> str:
        data = self.graphql("query { account { organizations { id name } } }")
        organizations = data["account"]["organizations"]
        if not organizations:
            raise AgentError("Buffer: keine Organisation gefunden")
        return organizations[0]["id"]

    def tiktok_channel_id(self, configured: str | None = None) -> str:
        if configured:
            return configured
        organization = json.dumps(self.organization_id())
        data = self.graphql(
            f"query {{ channels(input: {{ organizationId: {organization} }}) "
            "{ id name service } }"
        )
        channels = [c for c in data["channels"] if c["service"].lower() == "tiktok"]
        if not channels:
            raise AgentError("Buffer: kein TikTok-Kanal verbunden")
        if len(channels) > 1:
            names = ", ".join(f"{c['name']} ({c['id']})" for c in channels)
            raise AgentError(f"Mehrere TikTok-Kanäle, setze BUFFER_CHANNEL_ID: {names}")
        return channels[0]["id"]

    def create_photo_post(
        self,
        channel_id: str,
        text: str,
        title: str,
        image_urls: list[str],
        due_at: str,
        scheduling_type: str = "automatic",
    ) -> str:
        data = self.graphql(
            CREATE_POST,
            {
                "input": {
                    "channelId": channel_id,
                    "text": text,
                    "schedulingType": scheduling_type,
                    "mode": "customScheduled",
                    "dueAt": due_at,
                    "assets": [{"image": {"url": url}} for url in image_urls],
                    "metadata": {"tiktok": {"title": title}},
                    "source": "tiktok-agent",
                }
            },
        )
        result = data["createPost"]
        if result.get("message"):
            raise AgentError(f"Buffer lehnt Post ab: {result['message']}")
        return result["post"]["id"]

    def post_metrics(self, post_id: str) -> tuple[dict[str, float], str | None]:
        identifier = json.dumps(post_id)
        data = self.graphql(
            f"query {{ post(input: {{ id: {identifier} }}) "
            "{ id metrics { type name value unit } metricsUpdatedAt } }"
        )
        post = data.get("post") or {}
        metrics = {m["type"]: float(m["value"]) for m in post.get("metrics") or []}
        return metrics, post.get("metricsUpdatedAt")
