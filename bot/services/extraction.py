"""Extraction des commandes depuis le texte libre (ou une image) via l'API Anthropic."""
from __future__ import annotations

import asyncio
import base64
import json
import logging
import re
from dataclasses import dataclass, field
from typing import Any

log = logging.getLogger(__name__)

TIMEOUT = 20.0
RETRY_DELAY = 2.0
MAX_TOKENS = 1024

SYSTEM_PROMPT = """Tu extrais des commandes de livraison depuis des messages en français écrits rapidement, souvent avec des fautes.
Un message peut contenir UNE ou PLUSIEURS commandes. Chaque commande a une adresse de livraison distincte.

Réponds UNIQUEMENT avec un tableau JSON, sans texte avant ou après, sans balises Markdown.
Chaque élément du tableau a exactement ces clés :
- "address" : l'adresse postale (numéro, rue, code postal et/ou ville). null si absente.
- "address_detail" : digicode, étage, porte, interphone, consignes d'accès. null si absent.
- "products" : les produits commandés, tels qu'écrits, nettoyés. null si absents.
- "price" : le prix total en euros, nombre décimal. null si absent.
- "requested_time" : heure souhaitée si mentionnée (ex : "vers 23h"), sinon null.

Règles :
- Un nombre suivi de €, euros, e, ou seul en fin de message est un prix.
- Un code alphanumérique court près des mots digicode/code/interphone est un address_detail.
- "Paris 4", "paris 4e", "75004" désignent tous le 4e arrondissement : normalise en "75004 Paris".
- Ne complète jamais une information absente. Si tu hésites, mets null.
- Si le message ne contient manifestement aucune commande (salutation, question), renvoie []."""

IMAGE_PROMPT = "Extrais les commandes visibles sur cette image."

KEYS = ("address", "address_detail", "products", "price", "requested_time")


class ExtractionUnavailable(Exception):
    """API Anthropic injoignable après deux tentatives."""


class ExtractionParseError(Exception):
    """Réponse illisible deux fois de suite."""


@dataclass
class Order:
    address: str | None
    address_detail: str | None
    products: str | None
    price: float | None
    requested_time: str | None
    missing: list[str] = field(default_factory=list)

    @property
    def valid(self) -> bool:
        return not self.missing


_FENCE_RE = re.compile(r"^\s*```(?:json|JSON)?\s*|\s*```\s*$")


def parse_response(text: str) -> list[dict]:
    """Retire d'éventuelles balises ``` puis parse le tableau JSON.
    Lève ValueError si la réponse n'est pas exploitable."""
    cleaned = _FENCE_RE.sub("", (text or "").strip()).strip()
    try:
        data = json.loads(cleaned)
    except json.JSONDecodeError:
        # Dernière chance : un tableau entouré de texte parasite.
        start, end = cleaned.find("["), cleaned.rfind("]")
        if start == -1 or end <= start:
            raise ValueError("réponse non JSON")
        data = json.loads(cleaned[start : end + 1])
    if isinstance(data, dict):
        data = [data]
    if not isinstance(data, list) or not all(isinstance(x, dict) for x in data):
        raise ValueError("la réponse n'est pas un tableau d'objets")
    return data


def _clean_str(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text or text.lower() in {"null", "none", "n/a"}:
        return None
    return text


def _clean_price(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        price = float(value)
    else:
        m = re.search(r"\d+(?:[.,]\d+)?", str(value).replace(" ", "").replace(" ", ""))
        if not m:
            return None
        price = float(m.group(0).replace(",", "."))
    if price <= 0:
        return None  # prix nul ou négatif = traité comme manquant
    return round(price, 2)


def to_order(item: dict) -> Order:
    order = Order(
        address=_clean_str(item.get("address")),
        address_detail=_clean_str(item.get("address_detail")),
        products=_clean_str(item.get("products")),
        price=_clean_price(item.get("price")),
        requested_time=_clean_str(item.get("requested_time")),
    )
    if order.address is None:
        order.missing.append("address")
    if order.products is None:
        order.missing.append("products")
    if order.price is None:
        order.missing.append("price")
    return order


def looks_like_complement(orders: list[Order]) -> bool:
    """Message sans adresse ni prix (ex : « le digicode c'est 45B en fait »)."""
    return all(o.address is None and o.price is None for o in orders)


class Extractor:
    def __init__(self, api_key: str, model: str, client: Any = None):
        if client is None:
            from anthropic import AsyncAnthropic

            client = AsyncAnthropic(api_key=api_key, timeout=TIMEOUT, max_retries=0)
        self.client = client
        self.model = model

    async def _call(self, content: Any) -> str:
        last_exc: Exception | None = None
        for attempt in range(2):
            try:
                # Le SDK anthropic 1.x n'accepte plus `temperature` en argument : il passe
                # par extra_body (Haiku 4.5 l'accepte toujours côté API).
                resp = await self.client.messages.create(
                    model=self.model,
                    max_tokens=MAX_TOKENS,
                    system=SYSTEM_PROMPT,
                    messages=[{"role": "user", "content": content}],
                    extra_body={"temperature": 0},
                )
                return "".join(getattr(b, "text", "") for b in resp.content if getattr(b, "type", "") == "text")
            except Exception as exc:  # noqa: BLE001 — réseau, 5xx, 429… : on réessaie une fois
                last_exc = exc
                log.warning("Anthropic tentative %d échouée : %s", attempt + 1, exc)
                if attempt == 0:
                    await asyncio.sleep(RETRY_DELAY)
        raise ExtractionUnavailable(str(last_exc))

    async def _extract(self, content: Any) -> list[Order]:
        for attempt in range(2):
            text = await self._call(content)
            try:
                return [to_order(item) for item in parse_response(text)]
            except ValueError:
                log.warning("Réponse d'extraction illisible (tentative %d) : %r", attempt + 1, text[:300])
        raise ExtractionParseError()

    async def extract_text(self, text: str) -> list[Order]:
        return await self._extract(text)

    async def extract_image(self, image: bytes, media_type: str, caption: str | None = None) -> list[Order]:
        prompt = IMAGE_PROMPT + (f"\n\nLégende : {caption}" if caption else "")
        content = [
            {
                "type": "image",
                "source": {
                    "type": "base64",
                    "media_type": media_type,
                    "data": base64.standard_b64encode(image).decode("ascii"),
                },
            },
            {"type": "text", "text": prompt},
        ]
        return await self._extract(content)
