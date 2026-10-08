"""Point d'entrée : construction de l'Application, enregistrement des handlers, polling."""
from __future__ import annotations

import logging
import sys
import time
import traceback

from telegram import BotCommand, LinkPreviewOptions, Update
from telegram.constants import ParseMode
from telegram.error import Conflict, NetworkError
from telegram.ext import (
    AIORateLimiter, Application, CallbackQueryHandler, CommandHandler, Defaults, MessageHandler, filters,
)

from bot import config, db, jobs, messaging, texts
from bot.config import PARIS
from bot.handlers import (
    cash, close_day, cloture, common, restock_text, sales, swipe, dispatch, franchise, livreur, location, messages, onboarding, relay, restock, stock_view,
)

log = logging.getLogger("bot")


def setup_logging() -> None:
    # stdout (et non stderr) : Railway classe sinon chaque ligne comme une erreur.
    logging.basicConfig(format="%(asctime)s %(levelname)s %(name)s %(message)s", level=logging.INFO,
                        stream=sys.stdout)
    logging.getLogger("apscheduler").setLevel(logging.WARNING)
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpx2").setLevel(logging.WARNING)


CONFLICT_ALERT_SECONDS = 120   # conflit continu au-delà : deux instances tournent vraiment
CONFLICT_STREAK_GAP = 30       # silence plus long : nouvel épisode
_conflict = {"first": None, "last": None, "notified": False}


async def polling_error(context, err: Exception) -> None:
    """Erreurs de polling passagères (réseau, relève d'instance lors d'un déploiement) :
    un simple avertissement dans les logs, sans événement ni message au dispatch.

    Un conflit qui dure plus de 2 minutes signifie que deux instances tournent
    vraiment : le dispatch est alors prévenu, une fois par épisode."""
    log.warning("Erreur de polling passagère : %s", err)
    if not isinstance(err, Conflict):
        return
    now = time.monotonic()
    if _conflict["last"] is None or now - _conflict["last"] > CONFLICT_STREAK_GAP:
        _conflict.update(first=now, notified=False)
    _conflict["last"] = now
    if now - _conflict["first"] > CONFLICT_ALERT_SECONDS and not _conflict["notified"]:
        _conflict["notified"] = True
        await messaging.notify_dispatch(context.bot, texts.D_TWO_INSTANCES)
        await db.log_event("error", payload={"type": "Conflict", "message": "deux instances en polling"})


async def error_handler(update: object, context) -> None:
    """Toute exception : log, événement `error`, dispatch prévenu, message générique."""
    err = context.error
    if update is None and isinstance(err, (Conflict, NetworkError)):
        await polling_error(context, err)
        return
    log.error("Exception dans un handler", exc_info=err)
    kind = type(err).__name__ if err else "Inconnue"
    user = None
    try:
        if isinstance(update, Update) and update.effective_user:
            user = await db.get_user_by_tg(update.effective_user.id)
        await db.log_event("error", user_id=user["id"] if user else None, payload={
            "type": kind, "message": str(err)[:500],
            "trace": "".join(traceback.format_exception(err))[-2000:] if err else None,
        })
    except Exception:  # noqa: BLE001
        log.exception("Impossible d'écrire l'événement d'erreur")
    await messaging.notify_dispatch(context.bot, texts.d_error(kind))
    if isinstance(update, Update):
        if update.callback_query:
            try:
                await update.callback_query.answer()
            except Exception:  # noqa: BLE001
                pass
        if user is not None and user["status"] == "banned":
            return
        if update.effective_chat and update.effective_user and \
                update.effective_user.id != config.get().dispatch_telegram_id:
            await messaging.reply(update, texts.GENERIC_ERROR)


async def post_init(app: Application) -> None:
    cfg = config.get()
    await db.connect(cfg.supabase_url, cfg.supabase_service_key)
    await app.bot.set_my_commands([BotCommand(c, d) for c, d in common.COMMON_COMMANDS])
    jobs.register(app.job_queue)
    log.info("Bot prêt (dispatch : %s)", cfg.dispatch_telegram_id)


def build_application(cfg: config.Config) -> Application:
    defaults = Defaults(parse_mode=ParseMode.HTML, tzinfo=PARIS,
                        link_preview_options=LinkPreviewOptions(is_disabled=True))
    app = (
        Application.builder()
        .token(cfg.telegram_bot_token)
        .defaults(defaults)
        .rate_limiter(AIORateLimiter(max_retries=3))
        .concurrent_updates(True)
        .post_init(post_init)
        .build()
    )
    register_handlers(app)
    return app


def register_handlers(app: Application) -> None:
    new = filters.UpdateType.MESSAGE  # les messages édités sont ignorés (sauf positions)

    def cmd(name, fn):
        app.add_handler(CommandHandler(name, fn, filters=new))

    cmd("start", onboarding.start)
    cmd("aide", onboarding.aide)
    cmd("mescourses", franchise.mes_courses)
    cmd("dispo", livreur.dispo)
    cmd("pause", livreur.pause)
    cmd("macourse", livreur.ma_course)
    cmd("recap", dispatch.recap)
    cmd("journal", dispatch.journal)
    cmd("encours", dispatch.encours)
    cmd("users", dispatch.users)
    cmd("bannir", dispatch.exclure)
    cmd("exclure", dispatch.exclure)   # ancien nom, gardé
    cmd("reactiver", dispatch.reactiver)
    cmd("supprimer", dispatch.supprimer)
    cmd("produits", dispatch.produits)
    cmd("ajouter", dispatch.ajouter)
    cmd("modele", franchise.modele)
    cmd("synchro", dispatch.synchro)
    cmd("recharge", restock.recharge)
    cmd("livreurs", dispatch.livreurs)
    cmd("stock", stock_view.stock_cmd)
    cmd("depense", cash.depense)
    cmd("caisse", cash.caisse)
    cmd("macaisse", cash.macaisse)
    cmd("reset", cloture.cloture)
    cmd("close", close_day.close)
    cmd("ventes", sales.ventes)
    cmd("ravi", restock_text.ravi)
    cmd("swipe", swipe.swipe)
    cmd("cloture", cloture.cloture)   # ancien nom, gardé

    callbacks = [
        (r"^role:", onboarding.choose_role),
        (r"^approve:", onboarding.approve),
        (r"^reject:", onboarding.reject),
        (r"^draft_confirm:", franchise.confirm),
        (r"^draft_edit:", franchise.edit_draft),
        (r"^draft_edit_cancel:", franchise.cancel_edit),
        (r"^draft_cancel:", franchise.cancel_draft),
        (r"^course_withdraw:", franchise.withdraw),
        (r"^fw_yes:", franchise.withdraw_yes),
        (r"^fw_no:", franchise.withdraw_no),
        (r"^course_take:", livreur.take),
        (r"^course_deliver:", livreur.deliver),
        (r"^pay(_back)?:", livreur.pay),
        (r"^tmode:", livreur.transport_mode),
        (r"^livreur_soon_free$", livreur.soon_free),
        (r"^course_livreur_cancel:", livreur.cancel_ask),
        (r"^lc_yes:", livreur.cancel_yes),
        (r"^lc_no:", livreur.cancel_no),
        (r"^order_edit:", livreur.edit_start),
        (r"^oe_(q|s|p|add|pick):", livreur.edit_action),
        (r"^oe_(ok|x)$", livreur.edit_action),
        (r"^rs_", restock.action),
        (r"^duty_(on|off):", dispatch.duty),
        (r"^assign:", dispatch.assign_ask),
        (r"^assign_to:", dispatch.assign_do),
        (r"^sv:", stock_view.show),
        (r"^dp_", cash.action),
        (r"^cs_", cash.cash_action),
        (r"^cl_(go|x)$", cloture.action),
        (r"^sl_(ok|x)$", sales.action),
        (r"^rv_(ok|x)$", restock_text.action),
        (r"^sw_(ok|x)$", swipe.action),
        (r"^sname:", dispatch.name_ask),
        (r"^loc:", dispatch.show_position),
        (r"^loc_all$", dispatch.show_all_positions),
        (r"^sname_set:", dispatch.name_set),
        (r"^sname_done:", dispatch.name_done),
        (r"^relay_start:", relay.start),
        (r"^relay_cancel$", relay.cancel),
        (r"^recap_prev:", dispatch.recap_prev),
        (r"^journal_prev:", dispatch.journal_prev),
        (r"^force_(cancel|deliver|release):", dispatch.override_ask),
        (r"^fy:", dispatch.override_do),
        (r"^op_cancel$", dispatch.op_cancel),
        (r"^ban:", dispatch.ban_ask),
        (r"^ban_yes:", dispatch.ban_do),
        (r"^unban:", dispatch.unban),
        (r"^del:", dispatch.delete_ask),
        (r"^del_yes:", dispatch.delete_do),
        (r"^prod_add$", dispatch.catalog_add),
        (r"^prod_cancel$", dispatch.catalog_cancel),
        (r"^prod_del:", dispatch.catalog_delete),
    ]
    for pattern, fn in callbacks:
        app.add_handler(CallbackQueryHandler(fn, pattern=pattern))
    app.add_handler(CallbackQueryHandler(common.callback(_noop)))  # tout autre bouton : sablier retiré

    app.add_handler(MessageHandler(filters.LOCATION & filters.UpdateType.EDITED_MESSAGE, location.on_location))
    app.add_handler(MessageHandler(filters.LOCATION & new, location.on_location))
    app.add_handler(MessageHandler(new & ~filters.COMMAND & ~filters.StatusUpdate.ALL, messages.on_message))
    app.add_error_handler(error_handler)


async def _noop(update, context):
    return None


def main() -> None:
    setup_logging()
    cfg = config.get()
    app = build_application(cfg)
    log.info("Démarrage en polling")
    app.run_polling(allowed_updates=Update.ALL_TYPES, drop_pending_updates=False)


if __name__ == "__main__":
    main()
