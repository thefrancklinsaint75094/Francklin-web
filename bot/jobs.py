"""Tâches planifiées (job_queue de python-telegram-bot)."""
from __future__ import annotations

import logging
from datetime import time, timedelta

from bot import config, db, keyboards, messaging, texts
from bot.config import PARIS
from bot.handlers import cloture, dispatch
from bot.services import broadcast, names
from bot.timeutil import minutes_since, night_start_date, now_utc, parse_ts

log = logging.getLogger(__name__)


async def expire_drafts(context) -> None:
    cfg = config.get()
    for draft in await db.list_expirable_drafts(now_utc() - timedelta(minutes=cfg.draft_expiry_minutes)):
        expired = await db.update_draft_if_status(draft["id"], ["awaiting", "correcting"], {"status": "expired"})
        if expired is None:
            continue
        franchise = await db.get_user(draft["franchise_id"])
        if franchise is None:
            continue
        if franchise.get("conversation_state") == "correcting" and \
                (franchise.get("state_payload") or {}).get("draft_id") == draft["id"]:
            await db.clear_state(franchise["id"])
        if draft.get("telegram_message_id") and draft.get("extracted"):
            await messaging.edit(context.bot, franchise["telegram_id"], draft["telegram_message_id"],
                                 texts.DRAFT_EXPIRED, resend_if_old=False)


_silence_alerted: set[tuple[str, str]] = set()   # (livreur, heure de sa dernière position) déjà signalés


async def stale_positions(context) -> None:
    """Livreur en service sans nouvelle position : au bout de POSITION_ALERT_MINUTES, le dispatch
    (et le livreur) sont prévenus ; au bout de POSITION_STALE_MINUTES, il sort du service."""
    from bot.services import geocoding

    cfg = config.get()
    now = now_utc()
    limit = now - timedelta(minutes=cfg.position_stale_minutes)
    alert = now - timedelta(minutes=cfg.position_alert_minutes)
    livreurs = await db.list_on_duty_livreurs()
    positions = await db.get_positions(lv["id"] for lv in livreurs)
    for lv in livreurs:
        if lv.get("duty_forced"):
            continue  # mis en service par un admin : pas besoin de position
        pos = positions.get(lv["id"])
        at = parse_ts(pos["updated_at"]) if pos else None
        if at is None or at < limit:
            await db.update_user(lv["id"], {"on_duty": False, "soon_free": False})
            await db.log_event("position_stale", user_id=lv["id"])
            await messaging.send(context.bot, lv, texts.POSITION_LOST)
            await messaging.notify_dispatch(context.bot, texts.d_position_lost(lv, cfg.position_stale_minutes))
        elif 0 < cfg.position_alert_minutes < cfg.position_stale_minutes and at < alert:
            key = (lv["id"], pos["updated_at"])
            if key in _silence_alerted:
                continue
            _silence_alerted.add(key)
            address = await geocoding.reverse(pos["lat"], pos["lon"])
            minutes = (now - at).total_seconds() / 60
            await db.log_event("position_silent", user_id=lv["id"], payload={"minutes": int(minutes)})
            await messaging.notify_dispatch(context.bot, texts.d_position_silent(
                lv, minutes, address, cfg.position_stale_minutes))
            await messaging.send(context.bot, lv, texts.POSITION_SILENT)


async def stuck_courses(context) -> None:
    cfg = config.get()
    assigned = await db.list_courses_by_status("assigned")
    first: dict[str, tuple] = {}            # livreur → sa course la plus ancienne (celle en cours)
    for c in assigned:
        k = (c.get("assigned_at") or "", c["id"])
        if c.get("livreur_id") and (c["livreur_id"] not in first or k < first[c["livreur_id"]]):
            first[c["livreur_id"]] = k
    for course in assigned:
        if first.get(course.get("livreur_id")) != (course.get("assigned_at") or "", course["id"]):
            continue                        # en file derrière sa livraison en cours : rappelée à la fin
        assigned_at = parse_ts(course.get("assigned_at"))
        minutes = minutes_since(assigned_at)
        if assigned_at is None or minutes < cfg.stuck_course_minutes:
            continue
        alerts = await db.list_events(course["id"], "stuck_alert")
        if any((e.get("payload") or {}).get("assigned_at") == course["assigned_at"] for e in alerts):
            continue
        livreur = await db.get_user(course["livreur_id"])
        if livreur is None:
            continue
        await db.log_event("stuck_alert", course["id"], livreur["id"], {"assigned_at": course["assigned_at"]})
        await messaging.send(context.bot, livreur, texts.stuck_for_livreur(course["id"]))
        await messaging.notify_dispatch(context.bot, texts.d_stuck(course["id"], minutes, livreur))


async def clear_states(context) -> None:
    await db.clear_expired_states()


async def daily_journal(context) -> None:
    """À 6h : journal de la nuit qui vient de se terminer."""
    cfg = config.get()
    night = night_start_date(now_utc() - timedelta(minutes=5), cfg.night_end_hour)
    await dispatch.send_journal(context.bot, night)


async def retention(context) -> None:
    cfg = config.get()
    before = now_utc() - timedelta(days=cfg.data_retention_days)
    ids = await db.list_course_ids_closed_before(before)
    await db.scrub_old_courses(before)
    n_msg = await db.scrub_messages(ids)
    n_drafts = await db.scrub_old_drafts(before)
    # Messages envoyés de plus de 48 h : Telegram ne laisse plus les effacer, inutile de les garder.
    await db.forget_messages(before=now_utc() - timedelta(hours=48))
    await db.log_event("retention", payload={"courses": len(ids), "messages": n_msg, "drafts": n_drafts})


async def refresh_commands(context) -> None:
    """Menus de commandes Telegram à jour pour tous les utilisateurs actifs
    (nouvelles commandes après une mise à jour du bot)."""
    from bot.handlers import common

    for user in await db.list_users(status="active"):
        await common.set_commands(context.bot, user)


async def startup(context) -> None:
    await messaging.notify_dispatch(context.bot, texts.BOT_STARTED)
    await db.clear_expired_states()
    try:
        await refresh_commands(context)
    except Exception:  # noqa: BLE001 — un menu non rafraîchi ne doit pas bloquer le démarrage
        log.exception("Rafraîchissement des menus impossible")
    await broadcast.resume_all(context)
    await db.log_event("bot_started")
    from bot.services import stations

    await stations.load()


EDIT_REMIND_FIRST = 10      # min : premier rappel au franchisé d'une « Modif » sans réponse
EDIT_REMIND_EVERY = 30      # min : puis toutes les 30 min
EDIT_REMIND_MAX = 3         # au 3e rappel, le dispatch est prévenu aussi ; ensuite on n'insiste plus


async def edit_reminders(context) -> None:
    """Modification du livreur toujours sans décision : le franchisé est relancé (10 min, 40 min, 1 h 10),
    avec les boutons ✅ / ❌ ; au dernier rappel, le dispatch est prévenu."""
    for course in await db.list_pending_edits():
        pending = course.get("pending_edit") or {}
        at = parse_ts(pending.get("at"))
        if at is None or course["status"] not in ("assigned", "delivered"):
            continue
        minutes = minutes_since(at)
        done = [e for e in await db.list_events(course["id"], "edit_reminder")
                if (e.get("payload") or {}).get("at") == pending.get("at")]
        n = len(done)
        if n >= EDIT_REMIND_MAX or minutes < EDIT_REMIND_FIRST + n * EDIT_REMIND_EVERY:
            continue
        franchise = await db.get_user(course["franchise_id"])
        livreur = await db.get_user(course["livreur_id"]) if course.get("livreur_id") else None
        await db.log_event("edit_reminder", course["id"], payload={"at": pending.get("at"), "n": n + 1})
        if franchise:
            await messaging.send(context.bot, franchise,
                                 texts.edit_reminder(course, livreur or {}, pending, int(minutes)),
                                 keyboards.edit_decision(course["id"]))
        if n + 1 == EDIT_REMIND_MAX:
            await messaging.notify_dispatch(context.bot, texts.d_edit_waiting(course, franchise or {}, int(minutes)))


async def refresh_menus(context) -> None:
    """Quelques secondes après le démarrage : le bouton « Menu » de chacun liste toutes ses commandes."""
    from bot.handlers import common

    await common.refresh_all_commands(context.bot)


async def refresh_prenoms(context) -> None:
    """Prénoms des livreurs (« Livreur A (Ketur) ») : nouvelles inscriptions et /prenom d'un autre admin."""
    await names.refresh()


def _safe(job):
    async def wrapper(context):
        try:
            await job(context)
        except Exception:  # noqa: BLE001 — un job en échec ne doit jamais arrêter le bot
            log.exception("Job %s en échec", job.__name__)
            try:
                await db.log_event("error", payload={"job": job.__name__})
            except Exception:  # noqa: BLE001
                pass
    wrapper.__name__ = job.__name__
    return wrapper


def register(job_queue) -> None:
    cfg = config.get()
    job_queue.run_once(_safe(startup), when=1, name="startup")
    job_queue.run_repeating(_safe(expire_drafts), interval=60, first=30, name="expire_drafts")
    job_queue.run_repeating(_safe(refresh_prenoms), interval=120, first=120, name="refresh_prenoms")
    job_queue.run_once(_safe(refresh_menus), when=10, name="refresh_menus")
    job_queue.run_repeating(_safe(edit_reminders), interval=120, first=150, name="edit_reminders")
    job_queue.run_repeating(_safe(stale_positions), interval=120, first=60, name="stale_positions")
    job_queue.run_repeating(_safe(stuck_courses), interval=300, first=90, name="stuck_courses")
    job_queue.run_repeating(_safe(clear_states), interval=300, first=120, name="clear_states")
    job_queue.run_daily(_safe(daily_journal), time=time(cfg.night_end_hour, 0, tzinfo=PARIS), name="daily_journal")
    job_queue.run_daily(_safe(retention), time=time(7, 0, tzinfo=PARIS), name="retention")
    # Lundi (0 = dimanche pour run_daily) à 6h05, juste après la nuit de dimanche.
    job_queue.run_daily(_safe(cloture.weekly_reminder), time=time(cfg.night_end_hour, 5, tzinfo=PARIS), days=(1,),
                        name="cloture_reminder")
