"""Noms des livreurs et ravitailleurs = leurs noms dans les feuilles (« Livreur A », « Ravitailleur 1 »…).

Le nom visible dans le bot (users.display_name) est exactement celui écrit dans les feuilles Dispatch
et Rechargement : plus de table de correspondance à tenir, plus d'incohérence possible.
Un nom n'est porté que par un seul utilisateur (hors exclus).
"""
from __future__ import annotations

import html
import logging
import re

from bot import config, db

log = logging.getLogger(__name__)


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


# ---------------------------------------------------------------- prénoms des livreurs

# « Livreur A » → « Ketur » : prénom donné à l'inscription (users.real_name), sauf s'il est fixé par
# /prenom (table prenoms) — seul moyen pour un livreur des feuilles qui n'a pas de compte bot.
# Gardé en mémoire (refresh() au démarrage, toutes les 2 min et après /prenom) : chaque message du
# bot affiche « Livreur A (Ketur) » sans requête à la base.
_prenoms: dict[str, str] = {}
_pattern: re.Pattern | None = None
CODE_RE = re.compile(r"(<code>.*?</code>|<pre>.*?</pre>)", re.S)


async def load_prenoms() -> dict[str, str]:
    """{nom dans les feuilles: prénom} des livreurs (comptes actifs ou en pause + table prenoms)."""
    out: dict[str, str] = {}
    for u in await db.list_users(role="livreur"):
        if u["status"] not in ("banned", "deleted") and u.get("display_name") and (u.get("real_name") or "").strip():
            out[u["display_name"]] = " ".join(u["real_name"].split())
    for row in await db.list_prenoms():
        out[row["name"]] = row["prenom"]
    return out


def set_cache(prenoms: dict[str, str]) -> None:
    global _prenoms, _pattern
    _prenoms = {n: p for n, p in prenoms.items() if n.strip() and p.strip() and _key(n) != _key(p)}
    names = sorted(_prenoms, key=len, reverse=True)
    _pattern = re.compile(r"(?<![\w])(" + "|".join(re.escape(n) for n in names) + r")(?![\w])(</b>)?") \
        if names else None


async def refresh() -> None:
    try:
        set_cache(await load_prenoms())
    except Exception as exc:  # noqa: BLE001 — les prénoms ne doivent jamais bloquer le bot
        log.warning("Prénoms des livreurs non chargés : %s", exc)


def prenom(name: str | None) -> str | None:
    return _prenoms.get(name or "")


def label(name: str | None) -> str:
    """« Livreur A (Ketur) », ou le nom seul si le prénom est inconnu."""
    p = prenom(name)
    return f"{name} ({p})" if p else (name or "")


def by_prenom(text: str) -> str | None:
    """« ketur » → « Livreur A » (si un seul livreur porte ce prénom)."""
    wanted = _key(text)
    found = [n for n, p in _prenoms.items() if _key(p) == wanted]
    return found[0] if len(found) == 1 else None


def strip_prenom(text: str) -> str:
    """« Livreur A (Ketur) » → « Livreur A » : on peut recopier un nom tel que le bot l'affiche."""
    return re.sub(r"\s*\([^()]*\)\s*$", "", text or "").strip() or (text or "")


def _decorate(text: str, escape) -> str:
    if not text or _pattern is None:
        return text

    def one(m: re.Match) -> str:
        p = _prenoms[m.group(1)]
        after = m.string[m.end():m.end() + len(p) + 3]
        if after.lstrip().startswith(f"({escape(p)}") or after.lstrip().startswith(f"({p}"):
            return m.group(0)                       # déjà précisé
        return f"{m.group(0)} ({escape(p)})"

    parts = CODE_RE.split(text)
    return "".join(part if i % 2 else _pattern.sub(one, part) for i, part in enumerate(parts))


def decorate(text: str) -> str:
    """Texte HTML d'un message : chaque « Livreur A » devient « Livreur A (Ketur) » (hors exemples <code>)."""
    return _decorate(text, html.escape)


def decorate_plain(text: str) -> str:
    """Texte sans HTML (boutons, notifications) : même chose, sans échappement."""
    return _decorate(text, lambda s: s)


def decorate_markup(markup):
    """Boutons d'un message : les noms de livreurs prennent aussi leur prénom."""
    if markup is None or _pattern is None or not hasattr(markup, "inline_keyboard"):
        return markup
    from telegram import InlineKeyboardButton, InlineKeyboardMarkup

    rows = []
    for row in markup.inline_keyboard:
        out = []
        for b in row:
            data = b.to_dict()
            data["text"] = decorate_plain(b.text)[:64]
            out.append(InlineKeyboardButton.de_json(data, None))
        rows.append(out)
    return InlineKeyboardMarkup(rows)
