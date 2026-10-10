"""Côté franchisé : réception des commandes, fiche de confirmation, retrait (§7, §11.2)."""
from __future__ import annotations

import logging
from datetime import timedelta

from telegram import Update

from bot import config, db, keyboards, messaging, texts
from bot.handlers import common, relay
from bot.services import broadcast, geocoding, lifecycle, pick, transcription
from bot.services import catalog, order_edit, rules_extraction
from bot.services.rules_extraction import RuleExtractor
from bot.services.extraction import (
    ExtractionParseError, ExtractionUnavailable, Extractor, looks_like_complement, to_order,
)
from bot.timeutil import iso, minutes_since, night_bounds, night_start_date, now_utc, parse_ts

log = logging.getLogger(__name__)

COMPLEMENT_WINDOW = timedelta(minutes=30)
DUPLICATE_WINDOW = timedelta(minutes=15)

_extractor: Extractor | None = None


def extractor():
    """Extracteur selon EXTRACTION_MODE : règles fixes (sans IA) ou Anthropic."""
    global _extractor
    if _extractor is None:
        cfg = config.get()
        if cfg.uses_ai:
            _extractor = Extractor(cfg.anthropic_api_key, cfg.anthropic_model)
        else:
            _extractor = RuleExtractor()
    return _extractor


def set_extractor(ex) -> None:
    global _extractor
    _extractor = ex


def _norm(address: str) -> str:
    return " ".join((address or "").lower().replace(",", " ").split())


# ================================================================ réception

async def on_message(update: Update, context, user: dict, state: str | None, payload: dict) -> None:
    msg = update.message
    if state == "relaying":
        if msg.text:
            await relay.relay_text(update, context, user, payload.get("course_id"), msg.text)
        else:
            await messaging.reply(update, texts.WRITE_TEXT)
        return
    if msg.text and msg.reply_to_message:
        course_id = await relay.course_for_reply(user, msg.reply_to_message.message_id)
        if course_id:
            await relay.relay_text(update, context, user, course_id, msg.text)
            return

    correcting = payload.get("draft_id") if state == "correcting" else None
    if msg.text:
        await process(update, context, user, raw=msg.text, correcting_id=correcting)
    elif msg.voice:
        await process_voice(update, context, user, correcting)
    elif msg.photo or (msg.document and (msg.document.mime_type or "").startswith("image/")):
        await process_photo(update, context, user, correcting)
    else:
        await messaging.reply(update, texts.ONLY_TEXT_OR_VOICE)


async def process_voice(update: Update, context, user: dict, correcting_id: str | None) -> None:
    cfg = config.get()
    if not cfg.uses_ai or not cfg.openai_api_key:  # la transcription est aussi de l'IA
        await messaging.reply(update, texts.VOICE_UNSUPPORTED)
        return
    voice = update.message.voice
    # Aucun message perdu : le draft est écrit avant tout appel réseau.
    draft = await _start_draft(user, f"[vocal] (transcription en cours — file_id {voice.file_id})", correcting_id)
    try:
        f = await context.bot.get_file(voice.file_id)
        audio = bytes(await f.download_as_bytearray())
        text = await transcription.transcribe(audio, cfg.openai_api_key)
    except Exception as exc:  # noqa: BLE001
        log.warning("Vocal non transcrit : %s", exc)
        await db.update_draft(draft["id"], {"status": "error" if not correcting_id else "correcting",
                                            "raw_message": f"[vocal] (échec — file_id {voice.file_id})"})
        await messaging.reply(update, texts.VOICE_FAILED)
        return
    if not text:
        await db.update_draft(draft["id"], {"status": "expired" if not correcting_id else "correcting",
                                            "raw_message": "[vocal] (vide)"})
        await messaging.reply(update, texts.VOICE_FAILED)
        return
    raw = f"[vocal] {text}"
    draft = await db.update_draft(draft["id"], {"raw_message": raw}) or draft
    await process(update, context, user, raw=raw, correcting_id=correcting_id, draft=draft, extract_input=text)


async def process_photo(update: Update, context, user: dict, correcting_id: str | None) -> None:
    msg = update.message
    if not config.get().uses_ai:
        await messaging.reply(update, texts.PHOTO_UNSUPPORTED)
        return
    if msg.photo:
        file_id, media_type = msg.photo[-1].file_id, "image/jpeg"
    else:
        file_id, media_type = msg.document.file_id, msg.document.mime_type
    if media_type not in ("image/jpeg", "image/png", "image/gif", "image/webp"):
        await messaging.reply(update, texts.ONLY_TEXT_OR_VOICE)
        return
    caption = msg.caption or ""
    draft = await _start_draft(user, f"[photo] {caption}".strip() + f" (file_id {file_id})", correcting_id)
    try:
        f = await context.bot.get_file(file_id)
        image = bytes(await f.download_as_bytearray())
    except Exception as exc:  # noqa: BLE001
        log.warning("Photo non téléchargée : %s", exc)
        await db.update_draft(draft["id"], {"status": "error" if not correcting_id else "correcting"})
        await messaging.reply(update, texts.GENERIC_ERROR)
        return
    await process(update, context, user, raw=draft["raw_message"], correcting_id=correcting_id, draft=draft,
                  image=(image, media_type, caption or None))


async def _start_draft(user: dict, raw: str, correcting_id: str | None) -> dict:
    if correcting_id:
        draft = await db.update_draft_if_status(correcting_id, ["correcting"], {"raw_message": raw})
        if draft:
            return draft
    return await db.create_draft(user["id"], raw)


async def process(update: Update, context, user: dict, raw: str, correcting_id: str | None = None,
                  draft: dict | None = None, extract_input: str | None = None, image=None) -> None:
    """Pipeline complet : draft → extraction → validation → géocodage → fiches."""
    cfg = config.get()
    if draft is None:
        draft = await _start_draft(user, raw, correcting_id)
    is_correction = correcting_id is not None and draft["id"] == correcting_id
    fail_status = "correcting" if is_correction else "error"
    dead_status = "correcting" if is_correction else "expired"

    try:
        template = None
        if image is None:
            # Modèle de commande (quantité produit prix) : lu sans IA, quel que soit le mode.
            template = rules_extraction.parse_template(extract_input or raw, await catalog.load())
        if template is not None:
            orders = [to_order(d) for d in template]
        elif image is not None:
            orders = await extractor().extract_image(*image)
        else:
            orders = await extractor().extract_text(extract_input or raw)
    except ExtractionUnavailable:
        await db.update_draft(draft["id"], {"status": fail_status})
        await messaging.reply(update, texts.GENERIC_ERROR)
        await messaging.notify_dispatch(context.bot, texts.d_error("API Anthropic injoignable"))
        return
    except ExtractionParseError:
        await db.update_draft(draft["id"], {"status": fail_status})
        await messaging.reply(update, texts.EXTRACTION_PARSE_FAIL)
        return

    if not orders or looks_like_complement(orders):
        recent = [c for c in await db.list_franchise_courses_since(user["id"], now_utc() - COMPLEMENT_WINDOW)
                  if c["status"] in db.OPEN_STATUSES]
        if recent:
            await db.update_draft(draft["id"], {"status": dead_status})
            await messaging.reply(update, texts.complement_hint(recent[-1]["id"]))
            return
        if not orders:
            await db.update_draft(draft["id"], {"status": dead_status})
            await messaging.reply(update, texts.NO_ORDER_IN_IMAGE if image is not None else texts.NO_ORDER_FOUND)
            return

    total = len(orders)
    recent_courses = await db.list_franchise_courses_since(user["id"], now_utc() - DUPLICATE_WINDOW)
    reuse = draft
    produced = 0
    technical_error = False
    for index, order in enumerate(orders, start=1):
        if not order.valid:
            await messaging.reply(update, texts.missing_fields(order))
            continue
        problems = order_edit.price_problems(order.price, order.products)
        if problems:
            await messaging.reply(update, texts.price_not_round(order, problems))
            continue
        try:
            geo = await geocoding.geocode(order.address)
        except geocoding.GeocodingUnavailable:
            technical_error = True
            await messaging.reply(update, texts.GENERIC_ERROR)
            continue
        if geo is None:
            await messaging.reply(update, texts.geocode_not_found(order.address))
            continue
        if not geocoding.in_zone(geo.postcode, cfg.departements_autorises):
            await messaging.reply(update, texts.out_of_zone(f"{geo.city} {geo.postcode}".strip()))
            continue

        extracted = {
            "address": geo.label,
            "address_detail": order.address_detail,
            "products": order.products,
            "price": order.price,
            "requested_time": order.requested_time,
            "raw_address": order.address,
            "postal_code": geo.postcode,
            "city": geo.city,
            "district": geocoding.district(geo.postcode, geo.city),
            "lat": geo.lat,
            "lon": geo.lon,
            "has_housenumber": geo.has_housenumber,
            "header": f"Commande {index} sur {total}" if total > 1 else None,
            "warnings": order.warnings,
        }
        dup = next((c for c in reversed(recent_courses)
                    if c["status"] not in db.CANCELLED_STATUSES and _norm(c["address"]) == _norm(geo.label)), None)
        if dup:
            extracted["duplicate_of"] = dup["id"]
            extracted["duplicate_minutes"] = minutes_since(parse_ts(dup["created_at"]))

        if reuse is not None:
            old_message_id = reuse.get("telegram_message_id") if is_correction else None
            target = await db.update_draft(reuse["id"], {
                "extracted": extracted, "status": "awaiting", "created_at": iso(now_utc()),
            })
            if old_message_id:
                await messaging.edit(context.bot, update.effective_chat.id, old_message_id, texts.DRAFT_REPLACED)
            reuse = None
        else:
            target = await db.create_draft(user["id"], raw)
            target = await db.update_draft(target["id"], {"extracted": extracted})
        sent = await messaging.reply(update, texts.draft_card(extracted, cfg.price_max),
                                     keyboards.draft_card(target["id"]))
        if sent:
            await db.update_draft(target["id"], {"telegram_message_id": sent.message_id})
        produced += 1

    if produced:
        if is_correction:
            await db.clear_state(user["id"])
    elif reuse is not None:
        await db.update_draft(reuse["id"], {"status": fail_status if technical_error else dead_status})


# ================================================================ boutons de la fiche

async def _own_draft(update: Update) -> tuple[dict | None, dict | None]:
    user = await common.actor(update)
    if user is None or user["status"] != "active" or user["role"] != "franchise":
        return user, None
    draft = await db.get_draft(common.arg(update))
    if draft is None or draft["franchise_id"] != user["id"]:
        return user, None
    return user, draft


@common.callback
async def confirm(update: Update, context):
    user, draft = await _own_draft(update)
    if draft is None:
        return None
    locked = await db.update_draft_if_status(draft["id"], ["awaiting"], {"status": "confirmed"})
    if locked is None:
        current = await db.get_draft(draft["id"])
        if current and current["status"] == "confirmed":
            course = await db.get_course_by_draft(draft["id"])
            return texts.draft_already_confirmed(course["id"] if course else None), True
        return texts.DRAFT_NOT_ACTIVE, True
    ex = locked["extracted"]
    course = await db.create_course({
        "draft_id": locked["id"],
        "franchise_id": user["id"],
        "raw_message": locked["raw_message"],
        "address": ex["address"],
        "address_detail": ex.get("address_detail"),
        "postal_code": ex["postal_code"],
        "district": ex["district"],
        "lat": ex["lat"],
        "lon": ex["lon"],
        "products": ex["products"],
        "price": ex["price"],
        "requested_time": ex.get("requested_time"),
        "possible_duplicate_of": ex.get("duplicate_of"),
        "franchise_message_id": update.callback_query.message.message_id,
    })
    await db.log_event("course_created", course["id"], user["id"], {"draft_id": locked["id"]})
    # Le franchisé choisit son livreur parmi ceux en service ; personne en service : diffusion (attente).
    opts = await pick.options(course) if config.get().franchise_picks else []
    if opts:
        pick.remember(course["id"], pick.build(course, opts))
        pick.schedule_timeout(context.job_queue, course["id"], broadcast.pick_timeout)
    text, markup = lifecycle.franchise_view(course)
    await messaging.edit(context.bot, update.effective_chat.id, update.callback_query.message.message_id, text, markup)
    await messaging.notify_dispatch(context.bot, texts.d_created(course, user))
    if opts:
        return f"Course #{course['id']} enregistrée : choisis le livreur"
    await broadcast.run_wave(context, course["id"], advance=False)
    return f"Course #{course['id']} envoyée"


@common.callback
async def pick_livreur(update: Update, context):
    """fp:<course>:<livreur> : le franchisé envoie sa course à ce livreur, même s'il est déjà en livraison."""
    user = await common.actor(update)
    _, course_id, livreur_id = update.callback_query.data.split(":", 2)
    course = await db.get_course(int(course_id))
    if course is None or user is None or user["status"] != "active" or \
            (course["franchise_id"] != user["id"] and not common.is_admin(user)):
        return None
    if course["status"] != "pending":
        await lifecycle.refresh_franchise_message(context, course)
        return texts.ALREADY_CLOSED, True
    livreur = await db.get_user(livreur_id)
    if livreur is None or livreur["role"] != "livreur" or livreur["status"] != "active":
        return texts.ALREADY_HANDLED, True
    ongoing = await db.list_assigned_for_livreurs([livreur["id"]])
    won = await db.take_course(course["id"], livreur["id"])
    if won is None:
        await lifecycle.refresh_franchise_message(context, await db.get_course(course["id"]))
        return texts.ALREADY_CLOSED, True
    if won.get("franchise_message_id") != update.callback_query.message.message_id:
        await db.update_course(won["id"], {"franchise_message_id": update.callback_query.message.message_id})
        won["franchise_message_id"] = update.callback_query.message.message_id
    await lifecycle.after_assignment(context, won, livreur, None)
    await db.log_event("course_picked", won["id"], livreur["id"], {"by": user["id"], "busy": len(ongoing)})
    if ongoing:
        current = min(ongoing, key=lambda c: (c.get("assigned_at") or "", c["id"]))
        await messaging.send(context.bot, livreur, texts.queued_for_livreur(won, current))
        franchise = await db.get_user(won["franchise_id"])
        if franchise:
            await messaging.send(context.bot, franchise, texts.livreur_busy_for_franchise(won, livreur, current))
        return "Envoyée — il est en livraison, il la fera juste après"
    return "Envoyée ✅"


@common.callback
async def pick_auto(update: Update, context):
    """fp_auto:<course> : diffusion habituelle, au plus proche."""
    user = await common.actor(update)
    course = await db.get_course(common.arg(update, int))
    if course is None or user is None or user["status"] != "active" or \
            (course["franchise_id"] != user["id"] and not common.is_admin(user)):
        return None
    pick.forget(context.job_queue, course["id"])
    if course["status"] != "pending":
        await lifecycle.refresh_franchise_message(context, course)
        return texts.ALREADY_CLOSED, True
    await lifecycle.refresh_franchise_message(context, course, franchise=user if user["id"] == course["franchise_id"] else None)
    await broadcast.run_wave(context, course["id"], advance=False)
    return "Envoyée au plus proche"


@common.callback
async def edit_draft(update: Update, context):
    user, draft = await _own_draft(update)
    if draft is None:
        return None
    cfg = config.get()
    locked = await db.update_draft_if_status(draft["id"], ["awaiting"], {"status": "correcting",
                                                                         "created_at": iso(now_utc())})
    if locked is None:
        return texts.DRAFT_NOT_ACTIVE, True
    state, payload = common.active_state(user)
    if state == "correcting" and payload.get("draft_id") not in (None, draft["id"]):
        await _restore_card(context, update.effective_chat.id, payload["draft_id"])
    await db.set_state(user["id"], "correcting", {"draft_id": draft["id"]},
                       now_utc() + timedelta(minutes=cfg.draft_expiry_minutes))
    await messaging.edit(context.bot, update.effective_chat.id, update.callback_query.message.message_id,
                         texts.CORRECTION_PROMPT, keyboards.cancel_correction(draft["id"]))
    return None


async def _restore_card(context, chat_id: int, draft_id: str) -> None:
    restored = await db.update_draft_if_status(draft_id, ["correcting"], {"status": "awaiting"})
    if restored and restored.get("extracted"):
        await messaging.edit(context.bot, chat_id, restored.get("telegram_message_id"),
                             texts.draft_card(restored["extracted"], config.get().price_max),
                             keyboards.draft_card(draft_id))


@common.callback
async def cancel_edit(update: Update, context):
    user, draft = await _own_draft(update)
    if draft is None:
        return None
    state, payload = common.active_state(user)
    if payload.get("draft_id") == draft["id"]:
        await db.clear_state(user["id"])
    if draft["status"] != "correcting":
        return texts.DRAFT_NOT_ACTIVE
    await _restore_card(context, update.effective_chat.id, draft["id"])
    return texts.CORRECTION_CANCELLED


@common.callback
async def cancel_draft(update: Update, context):
    user, draft = await _own_draft(update)
    if draft is None:
        return None
    locked = await db.update_draft_if_status(draft["id"], ["awaiting", "correcting"], {"status": "expired"})
    if locked is None:
        if draft["status"] == "confirmed":
            course = await db.get_course_by_draft(draft["id"])
            return texts.draft_already_confirmed(course["id"] if course else None), True
        return texts.DRAFT_NOT_ACTIVE
    state, payload = common.active_state(user)
    if payload.get("draft_id") == draft["id"]:
        await db.clear_state(user["id"])
    await messaging.edit(context.bot, update.effective_chat.id, update.callback_query.message.message_id,
                         texts.DRAFT_CANCELLED)
    return None


# ================================================================ retrait d'une course

async def _own_course(update: Update) -> tuple[dict | None, dict | None]:
    user = await common.actor(update)
    if user is None or user["status"] != "active" or user["role"] != "franchise":
        return user, None
    course = await db.get_course(common.arg(update, int))
    if course is None or course["franchise_id"] != user["id"]:
        return user, None
    return user, course


@common.callback
async def withdraw(update: Update, context):
    user, course = await _own_course(update)
    if course is None:
        return None
    if course["status"] not in db.OPEN_STATUSES:
        return texts.ALREADY_CLOSED, True
    await messaging.edit(context.bot, update.effective_chat.id, update.callback_query.message.message_id,
                         texts.withdraw_confirm(course["id"]) + "\n" + texts.course_summary(course),
                         keyboards.withdraw_confirm(course["id"]))
    return None


@common.callback
async def withdraw_no(update: Update, context):
    user, course = await _own_course(update)
    if course is None:
        return None
    if course.get("franchise_message_id") != update.callback_query.message.message_id:
        await db.update_course(course["id"], {"franchise_message_id": update.callback_query.message.message_id})
        course["franchise_message_id"] = update.callback_query.message.message_id
    await lifecycle.refresh_franchise_message(context, course, franchise=user)
    return None


@common.callback
async def withdraw_yes(update: Update, context):
    user, course = await _own_course(update)
    if course is None:
        return None
    if course["status"] not in db.OPEN_STATUSES:
        await lifecycle.refresh_franchise_message(context, course, franchise=user)
        return texts.ALREADY_CLOSED, True
    updated = await lifecycle.cancel(context, course, by="franchise")
    if updated is None:
        course = await db.get_course(course["id"])
        await lifecycle.refresh_franchise_message(context, course, franchise=user)
        return texts.ALREADY_CLOSED, True
    return "Course retirée"


# ================================================================ /modele, /produits

async def modele(update: Update, context) -> None:
    user = await common.actor(update)
    if not await common.require(update, user, role="franchise"):
        return
    await messaging.reply(update, texts.MODEL_HELP)


async def produits(update: Update, context) -> None:
    """Liste des produits connus, en lecture seule pour le franchisé."""
    user = await common.actor(update)
    if not await common.require(update, user, role="franchise"):
        return
    await messaging.reply(update, texts.catalog_list(await db.list_products()))


# ================================================================ /mescourses

async def mes_courses(update: Update, context) -> None:
    user = await common.actor(update)
    if not await common.require(update, user, role="franchise"):
        return
    cfg = config.get()
    start, _ = night_bounds(night_start_date(now_utc(), cfg.night_end_hour), cfg.night_end_hour)
    courses = {c["id"]: c for c in await db.list_franchise_courses_since(user["id"], start)}
    for c in await db.list_open_for_franchise(user["id"]):
        courses.setdefault(c["id"], c)
    ordered = [courses[k] for k in sorted(courses)]
    livreurs = await db.get_users(c["livreur_id"] for c in ordered if c.get("livreur_id"))
    await messaging.reply(update, texts.mes_courses(ordered, livreurs))


# ================================================================ modification du livreur : le franchisé décide

@common.callback
async def edit_decision(update: Update, context):
    """me:<course>:ok|no — le franchisé (ou un admin) valide ou refuse la « Modif » du livreur."""
    from bot.services import stock

    user = await common.actor(update)
    _, course_id, verdict = (update.callback_query.data or "::").split(":", 2)
    course = await db.get_course(int(course_id or 0))
    if course is None or user is None or user["status"] != "active" or \
            (course["franchise_id"] != user["id"] and not common.is_admin(user)):
        return None
    chat_id, msg_id = update.effective_chat.id, update.callback_query.message.message_id
    pending = course.get("pending_edit")
    if not pending:
        await messaging.edit_markup(context.bot, chat_id, msg_id, None)
        return texts.EDIT_ALREADY_DECIDED, True
    accepted = verdict == "ok"
    fields = {"pending_edit": None}
    if accepted:
        fields.update(products=pending["products"], price=pending["price"])
    updated = await db.update_course_if_status(course["id"], ["assigned", "delivered"], fields)
    if updated is None:                                 # course annulée entre-temps
        await db.update_course(course["id"], {"pending_edit": None})
        await messaging.edit_markup(context.bot, chat_id, msg_id, None)
        return texts.COURSE_FINISHED, True
    text = texts.edit_decided(course["id"], accepted, pending, user["display_name"])
    await messaging.edit(context.bot, chat_id, msg_id, text)
    livreur = await db.get_user(updated["livreur_id"]) if updated.get("livreur_id") else None
    if livreur:
        await messaging.send(context.bot, livreur, text)
        if accepted and updated["status"] == "assigned":
            franchise = await db.get_user(updated["franchise_id"]) or {}
            await messaging.edit(context.bot, livreur["telegram_id"], updated.get("livreur_message_id"),
                                 texts.full_fiche(updated, franchise), keyboards.livreur_course(updated["id"]),
                                 user=livreur, resend_if_old=False)
    await lifecycle.refresh_franchise_message(context, updated, livreur=livreur)
    if user["role"] != "dispatch":
        await messaging.notify_dispatch(context.bot, text)
    await db.log_event("course_edit_decided", course["id"], user["id"], {"accepted": accepted, **pending})
    if updated["status"] == "delivered":
        stock.after_delivery_later(context, updated, livreur)   # la feuille reçoit la version décidée
    return "Validée ✅" if accepted else "Refusée"

