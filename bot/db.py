"""Accès à Supabase : une fonction par requête.

Le client est asynchrone (supabase-py `acreate_client`) pour ne jamais
bloquer la boucle d'événements du bot. Toute fonction renvoie des dict
bruts tels que renvoyés par PostgREST.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any, Iterable

from bot.timeutil import iso, now_utc

_client: Any = None

OPEN_STATUSES = ("pending", "assigned")
CANCELLED_STATUSES = ("cancelled", "cancelled_on_site")


async def connect(url: str, key: str) -> None:
    from supabase import acreate_client

    global _client
    _client = await acreate_client(url, key)


def init(client: Any) -> None:
    """Injecte un client déjà construit (tests : client PostgREST local)."""
    global _client
    _client = client


def _t(name: str):
    if _client is None:
        raise RuntimeError("Base de données non initialisée")
    return _client.table(name)


def _first(res) -> dict | None:
    return res.data[0] if res.data else None


# ---------------------------------------------------------------- users

async def get_user_by_tg(telegram_id: int) -> dict | None:
    return _first(await _t("users").select("*").eq("telegram_id", telegram_id).limit(1).execute())


async def get_user(user_id: str) -> dict | None:
    return _first(await _t("users").select("*").eq("id", user_id).limit(1).execute())


async def get_users(user_ids: Iterable[str]) -> dict[str, dict]:
    ids = list({u for u in user_ids if u})
    if not ids:
        return {}
    res = await _t("users").select("*").in_("id", ids).execute()
    return {u["id"]: u for u in res.data}


async def create_user(fields: dict) -> dict:
    return _first(await _t("users").insert(fields).execute())


async def update_user(user_id: str, fields: dict) -> dict | None:
    return _first(await _t("users").update(fields).eq("id", user_id).execute())


async def delete_user(user_id: str) -> None:
    await _t("events").delete().eq("user_id", user_id).execute()
    await _t("users").delete().eq("id", user_id).execute()


async def count_display_names(role: str) -> int:
    res = await (
        _t("users").select("id", count="exact").eq("role", role).not_.is_("display_name", "null").execute()
    )
    return res.count or 0


async def list_users(role: str | None = None, status: str | None = None) -> list[dict]:
    """Sans statut demandé : tous les comptes sauf les comptes supprimés."""
    q = _t("users").select("*")
    if role:
        q = q.eq("role", role)
    q = q.eq("status", status) if status else q.neq("status", "deleted")
    return (await q.order("created_at").execute()).data


async def list_on_duty_livreurs() -> list[dict]:
    res = await (
        _t("users").select("*").eq("role", "livreur").eq("status", "active").eq("on_duty", True).execute()
    )
    return res.data


async def set_state(user_id: str, state: str | None, payload: dict | None, expires_at: datetime | None) -> None:
    await _t("users").update(
        {
            "conversation_state": state,
            "state_payload": payload,
            "state_expires_at": iso(expires_at) if expires_at else None,
        }
    ).eq("id", user_id).execute()


async def clear_state(user_id: str) -> None:
    await set_state(user_id, None, None, None)


async def clear_expired_states() -> int:
    res = await (
        _t("users")
        .update({"conversation_state": None, "state_payload": None, "state_expires_at": None})
        .lt("state_expires_at", iso(now_utc()))
        .execute()
    )
    return len(res.data)


async def list_users_in_state(state: str) -> list[dict]:
    return (await _t("users").select("*").eq("conversation_state", state).execute()).data


# ---------------------------------------------------------------- drafts

async def create_draft(franchise_id: str, raw_message: str, status: str = "awaiting") -> dict:
    return _first(
        await _t("drafts").insert({"franchise_id": franchise_id, "raw_message": raw_message, "status": status}).execute()
    )


async def get_draft(draft_id: str) -> dict | None:
    return _first(await _t("drafts").select("*").eq("id", draft_id).limit(1).execute())


async def update_draft(draft_id: str, fields: dict) -> dict | None:
    return _first(await _t("drafts").update(fields).eq("id", draft_id).execute())


async def update_draft_if_status(draft_id: str, statuses: Iterable[str], fields: dict) -> dict | None:
    """Mise à jour conditionnelle : ne touche le draft que s'il est dans l'un des statuts."""
    res = await _t("drafts").update(fields).eq("id", draft_id).in_("status", list(statuses)).execute()
    return _first(res)


async def list_expirable_drafts(before: datetime) -> list[dict]:
    res = await (
        _t("drafts").select("*").in_("status", ["awaiting", "correcting"]).lt("created_at", iso(before)).execute()
    )
    return res.data


# ---------------------------------------------------------------- courses

async def create_course(fields: dict) -> dict:
    return _first(await _t("courses").insert(fields).execute())


async def get_course(course_id: int) -> dict | None:
    return _first(await _t("courses").select("*").eq("id", course_id).limit(1).execute())


async def get_course_by_draft(draft_id: str) -> dict | None:
    return _first(await _t("courses").select("*").eq("draft_id", draft_id).limit(1).execute())


async def update_course(course_id: int, fields: dict) -> dict | None:
    return _first(await _t("courses").update(fields).eq("id", course_id).execute())


async def update_course_if_status(
    course_id: int, statuses: Iterable[str], fields: dict, livreur_id: str | None = None
) -> dict | None:
    """Mise à jour conditionnelle au statut (et éventuellement au livreur) : la ligne
    n'est modifiée que si elle est encore dans l'état attendu. Renvoie None sinon."""
    q = _t("courses").update(fields).eq("id", course_id).in_("status", list(statuses))
    if livreur_id:
        q = q.eq("livreur_id", livreur_id)
    return _first(await q.execute())


async def take_course(course_id: int, livreur_id: str) -> dict | None:
    """LE verrou de prise de course (§9.3) :

        UPDATE courses SET status='assigned', livreur_id=:l, assigned_at=now()
        WHERE id=:c AND status='pending' RETURNING *;

    Renvoie la course si ce livreur l'a obtenue, None si quelqu'un a été plus rapide.
    """
    res = await (
        _t("courses")
        .update({"status": "assigned", "livreur_id": livreur_id, "assigned_at": iso(now_utc())})
        .eq("id", course_id)
        .eq("status", "pending")
        .execute()
    )
    return _first(res)


async def list_pending_edits() -> list[dict]:
    """Courses dont la modification du livreur attend la décision du franchisé."""
    return (await _t("courses").select("*").filter("pending_edit", "not.is", "null").order("id").execute()).data


async def list_courses_by_status(status: str) -> list[dict]:
    return (await _t("courses").select("*").eq("status", status).order("id").execute()).data


async def list_assigned_for_livreur(livreur_id: str) -> list[dict]:
    res = await (
        _t("courses").select("*").eq("status", "assigned").eq("livreur_id", livreur_id).order("assigned_at").execute()
    )
    return res.data


async def list_assigned_for_livreurs(livreur_ids: Iterable[str]) -> list[dict]:
    ids = list(livreur_ids)
    if not ids:
        return []
    return (await _t("courses").select("*").eq("status", "assigned").in_("livreur_id", ids).execute()).data


async def list_open_for_franchise(franchise_id: str) -> list[dict]:
    res = await (
        _t("courses").select("*").eq("franchise_id", franchise_id).in_("status", list(OPEN_STATUSES)).execute()
    )
    return res.data


async def list_franchise_courses_since(franchise_id: str, since: datetime) -> list[dict]:
    res = await (
        _t("courses").select("*").eq("franchise_id", franchise_id).gte("created_at", iso(since)).order("id").execute()
    )
    return res.data


async def list_delivered_between(start: datetime, end: datetime) -> list[dict]:
    res = await (
        _t("courses")
        .select("*")
        .eq("status", "delivered")
        .gte("delivered_at", iso(start))
        .lt("delivered_at", iso(end))
        .order("delivered_at")
        .execute()
    )
    return res.data


async def list_closed_between(status: str, start: datetime, end: datetime) -> list[dict]:
    res = await (
        _t("courses").select("*").eq("status", status).gte("closed_at", iso(start)).lt("closed_at", iso(end)).execute()
    )
    return res.data


async def list_franchise_cancelled_on_site() -> list[dict]:
    return (await _t("courses").select("id,franchise_id").eq("status", "cancelled_on_site").execute()).data


async def find_course_by_message(message_id: int, user: dict) -> dict | None:
    """Course dont la fiche (côté livreur) ou le suivi (côté franchisé) est ce message."""
    if user["role"] == "livreur":
        q = _t("courses").select("*").eq("livreur_message_id", message_id).eq("livreur_id", user["id"])
    else:
        q = _t("courses").select("*").eq("franchise_message_id", message_id).eq("franchise_id", user["id"])
    return _first(await q.order("id", desc=True).limit(1).execute())


async def scrub_old_courses(before: datetime) -> list[int]:
    res = await (
        _t("courses")
        .update({"address_detail": None, "raw_message": "[effacé]"})
        .lt("closed_at", iso(before))
        .neq("raw_message", "[effacé]")
        .execute()
    )
    return [c["id"] for c in res.data]


async def list_course_ids_closed_before(before: datetime) -> list[int]:
    res = await _t("courses").select("id").lt("closed_at", iso(before)).execute()
    return [c["id"] for c in res.data]


async def scrub_old_drafts(before: datetime) -> int:
    res = await (
        _t("drafts")
        .update({"raw_message": "[effacé]", "extracted": None})
        .lt("created_at", iso(before))
        .neq("raw_message", "[effacé]")
        .execute()
    )
    return len(res.data)


# ---------------------------------------------------------------- positions

async def upsert_position(livreur_id: str, lat: float, lon: float, **extra) -> None:
    """extra : live (bool) et live_until (texte ISO ou None) ; absents = inchangés."""
    await _t("livreur_positions").upsert(
        {"livreur_id": livreur_id, "lat": lat, "lon": lon, "updated_at": iso(now_utc()), **extra},
        on_conflict="livreur_id",
    ).execute()


async def get_position(livreur_id: str) -> dict | None:
    return _first(await _t("livreur_positions").select("*").eq("livreur_id", livreur_id).limit(1).execute())


async def get_positions(livreur_ids: Iterable[str]) -> dict[str, dict]:
    ids = list(livreur_ids)
    if not ids:
        return {}
    res = await _t("livreur_positions").select("*").in_("livreur_id", ids).execute()
    return {p["livreur_id"]: p for p in res.data}


# ---------------------------------------------------------------- broadcasts

async def reserve_broadcast(course_id: int, livreur_id: str, round_: int) -> bool:
    """Inscrit un livreur comme sollicité. False s'il l'était déjà (clé primaire)."""
    res = await _t("broadcasts").upsert(
        {"course_id": course_id, "livreur_id": livreur_id, "round": round_, "sent_at": iso(now_utc())},
        on_conflict="course_id,livreur_id",
        ignore_duplicates=True,
    ).execute()
    return bool(res.data)


async def set_broadcast_message(course_id: int, livreur_id: str, message_id: int | None) -> None:
    await _t("broadcasts").update({"telegram_message_id": message_id}).eq("course_id", course_id).eq(
        "livreur_id", livreur_id
    ).execute()


async def list_broadcasts(course_id: int) -> list[dict]:
    return (await _t("broadcasts").select("*").eq("course_id", course_id).execute()).data


async def get_broadcast(course_id: int, livreur_id: str) -> dict | None:
    res = await _t("broadcasts").select("*").eq("course_id", course_id).eq("livreur_id", livreur_id).limit(1).execute()
    return _first(res)


async def reset_broadcasts_except(course_id: int, keep_livreur_id: str | None) -> None:
    """Remise en diffusion : les autres livreurs redeviennent sollicitables ;
    celui qu'on garde (qui a annulé ou a été retiré) reste exclu, en vague 0."""
    q = _t("broadcasts").delete().eq("course_id", course_id)
    if keep_livreur_id:
        q = q.neq("livreur_id", keep_livreur_id)
    await q.execute()
    if keep_livreur_id:
        await _t("broadcasts").update({"round": 0}).eq("course_id", course_id).eq(
            "livreur_id", keep_livreur_id
        ).execute()


# ---------------------------------------------------------------- messages

async def add_message(course_id: int, sender_id: str, content: str) -> dict:
    return _first(
        await _t("messages").insert({"course_id": course_id, "sender_id": sender_id, "content": content}).execute()
    )


async def set_message_relayed_id(message_id: int, relayed_id: int) -> None:
    await _t("messages").update({"relayed_telegram_message_id": relayed_id}).eq("id", message_id).execute()


async def find_messages_by_relayed_id(relayed_id: int) -> list[dict]:
    return (await _t("messages").select("*").eq("relayed_telegram_message_id", relayed_id).execute()).data


async def scrub_messages(course_ids: list[int]) -> int:
    if not course_ids:
        return 0
    res = await (
        _t("messages").update({"content": "[effacé]"}).in_("course_id", course_ids).neq("content", "[effacé]").execute()
    )
    return len(res.data)


# ---------------------------------------------------------------- produits

async def list_products() -> list[dict]:
    return (await _t("products").select("*").order("name").execute()).data


async def get_product(product_id: int) -> dict | None:
    return _first(await _t("products").select("*").eq("id", product_id).limit(1).execute())


async def create_product(name: str, name_key: str, aliases: list[str]) -> dict:
    return _first(await _t("products").insert({"name": name, "name_key": name_key, "aliases": aliases}).execute())


async def update_product(product_id: int, fields: dict) -> dict | None:
    return _first(await _t("products").update(fields).eq("id", product_id).execute())


async def delete_product(product_id: int) -> None:
    await _t("products").delete().eq("id", product_id).execute()


# ---------------------------------------------------------------- events

# ---------------------------------------------------------------- rechargements

async def create_restock(fields: dict) -> dict | None:
    return _first(await _t("restocks").insert(fields).execute())


async def list_restocks_between(start: datetime, end: datetime) -> list[dict]:
    res = await (
        _t("restocks").select("*").gte("created_at", iso(start)).lt("created_at", iso(end))
        .order("created_at").execute()
    )
    return res.data


async def create_expense(fields: dict) -> dict | None:
    return _first(await _t("expenses").insert(fields).execute())


async def list_expenses_between(start: datetime, end: datetime) -> list[dict]:
    res = await (
        _t("expenses").select("*").gte("created_at", iso(start)).lt("created_at", iso(end))
        .order("created_at").execute()
    )
    return res.data


async def list_expenses_for_livreur(livreur_id: str, since: datetime) -> list[dict]:
    res = await (
        _t("expenses").select("*").eq("livreur_id", livreur_id).gte("created_at", iso(since))
        .order("created_at").execute()
    )
    return res.data


async def create_sales(rows: list[dict]) -> list[dict]:
    return (await _t("sales").insert(rows).execute()).data


async def list_sales_between(start: datetime, end: datetime) -> list[dict]:
    res = await (_t("sales").select("*").gte("created_at", iso(start)).lt("created_at", iso(end))
                 .order("id").execute())
    return res.data


async def list_prenoms() -> list[dict]:
    return (await _t("prenoms").select("*").execute()).data


async def set_prenom(name: str, prenom: str) -> None:
    await _t("prenoms").upsert({"name": name, "prenom": prenom, "updated_at": iso(now_utc())},
                               on_conflict="name").execute()


async def delete_prenom(name: str) -> None:
    await _t("prenoms").delete().eq("name", name).execute()


async def remember_message(chat_id: int, message_id: int) -> None:
    await _t("bot_messages").upsert({"chat_id": chat_id, "message_id": message_id},
                                    on_conflict="chat_id,message_id").execute()


async def recent_message_ids(chat_id: int, since: datetime) -> list[int]:
    res = await (_t("bot_messages").select("message_id").eq("chat_id", chat_id).gte("created_at", iso(since))
                 .order("message_id").execute())
    return [r["message_id"] for r in res.data]


async def forget_messages(chat_id: int | None = None, before: datetime | None = None) -> None:
    q = _t("bot_messages").delete()
    if chat_id is not None:
        q = q.eq("chat_id", chat_id)
    if before is not None:
        q = q.lt("created_at", iso(before))
    await q.execute()


async def delete_position(livreur_id: str) -> None:
    await _t("livreur_positions").delete().eq("livreur_id", livreur_id).execute()


async def log_event(type_: str, course_id: int | None = None, user_id: str | None = None, payload: dict | None = None) -> None:
    await _t("events").insert(
        {"type": type_, "course_id": course_id, "user_id": user_id, "payload": payload}
    ).execute()


async def last_user_events(type_: str, since: datetime) -> dict[str, str]:
    """{utilisateur: heure du dernier événement de ce type} depuis une date."""
    res = await (_t("events").select("user_id,created_at").eq("type", type_).gte("created_at", iso(since))
                 .order("created_at").execute())
    return {r["user_id"]: r["created_at"] for r in res.data if r.get("user_id")}


async def list_events(course_id: int, type_: str) -> list[dict]:
    return (await _t("events").select("*").eq("course_id", course_id).eq("type", type_).execute()).data
