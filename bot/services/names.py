"""Noms des livreurs et ravitailleurs = leurs noms dans les feuilles (« Livreur A », « Ravitailleur 1 »…).

Le nom visible dans le bot (users.display_name) est exactement celui écrit dans les feuilles Dispatch
et Rechargement : plus de table de correspondance à tenir, plus d'incohérence possible.
Un nom n'est porté que par un seul utilisateur (hors exclus).
"""
from __future__ import annotations

from bot import config, db


def names_for(role: str) -> tuple[str, ...]:
    cfg = config.get()
    return {"livreur": cfg.livreur_names, "ravitailleur": cfg.ravitailleur_names}.get(role, ())


def _key(name: str | None) -> str:
    return " ".join(str(name or "").lower().split())


async def holders(role: str) -> dict[str, dict]:
    """{nom normalisé: utilisateur} pour les noms déjà portés (utilisateurs non exclus)."""
    out = {}
    for u in await db.list_users(role=role):
        if u["status"] != "banned" and u.get("display_name"):
            out[_key(u["display_name"])] = u
    return out


async def first_free(role: str, exclude_user_id: str | None = None) -> str | None:
    taken = await holders(role)
    for name in names_for(role):
        holder = taken.get(_key(name))
        if holder is None or holder["id"] == exclude_user_id:
            return name
    return None


async def holder_of(role: str, name: str, exclude_user_id: str | None = None) -> dict | None:
    holder = (await holders(role)).get(_key(name))
    return holder if holder and holder["id"] != exclude_user_id else None
