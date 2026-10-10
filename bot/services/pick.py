"""Le franchisé choisit son livreur (FRANCHISE_PICKS_LIVREUR, activé par défaut).

À la confirmation, la course n'est pas diffusée : sa fiche propose les livreurs en service (🟢 libres,
du plus proche au plus loin, puis 🛵 déjà en livraison) et « 📣 Au plus proche ». Le livreur choisi
reçoit la course directement ; s'il est déjà en livraison, il la reçoit quand même et elle lui est
rappelée quand il valide celle en cours. Sans choix au bout de PICK_TIMEOUT_MINUTES, la course part
au plus proche comme avant.

L'attente est gardée en mémoire : après un redémarrage, la course repart en diffusion normale.
"""
from __future__ import annotations

from collections import defaultdict
from datetime import timedelta

from bot import config, db, keyboards
from bot.services import catalog as catalog_svc
from bot.services import transport, variants
from bot.services.distance import haversine_m
from bot.timeutil import now_utc, parse_ts

_awaiting: dict[int, object] = {}       # course → boutons de choix (InlineKeyboardMarkup)


def is_awaiting(course_id: int) -> bool:
    return course_id in _awaiting


def markup(course_id: int):
    return _awaiting.get(course_id)


def remember(course_id: int, buttons) -> None:
    _awaiting[course_id] = buttons


def _job_name(course_id: int) -> str:
    return f"pick:{course_id}"


def forget(job_queue, course_id: int) -> None:
    _awaiting.pop(course_id, None)
    if job_queue is not None:
        for job in job_queue.get_jobs_by_name(_job_name(course_id)):
            job.schedule_removal()


def schedule_timeout(job_queue, course_id: int, callback) -> None:
    minutes = config.get().pick_timeout_minutes
    if job_queue is not None and minutes > 0:
        job_queue.run_once(callback, when=minutes * 60, data=course_id, name=_job_name(course_id))


async def options(course: dict) -> list[dict]:
    """Livreurs en service, à proposer au franchisé : {"user", "km", "busy"} ; libres d'abord (temps de
    trajet), puis ceux déjà en livraison."""
    cfg = config.get()
    livreurs = [lv for lv in await db.list_on_duty_livreurs() if lv.get("status") == "active"]
    if not livreurs:
        return []
    ids = [lv["id"] for lv in livreurs]
    positions = await db.get_positions(ids)
    busy: dict[str, int] = defaultdict(int)
    for c in await db.list_assigned_for_livreurs(ids):
        busy[c["livreur_id"]] += 1
    fresh = now_utc() - timedelta(minutes=cfg.position_stale_minutes)
    out = []
    for lv in livreurs:
        pos = positions.get(lv["id"])
        dist = None
        if pos and parse_ts(pos["updated_at"]) >= fresh and course.get("lat") is not None:
            dist = haversine_m(pos["lat"], pos["lon"], course["lat"], course["lon"])
        out.append({"user": lv, "m": dist, "busy": busy.get(lv["id"], 0), "v": []})
    # Goûts demandés (« 1 MSX banane ») : qui en a encore.
    wanted = variants.requested(course.get("products") or "", await catalog_svc.load())
    if wanted:
        flavors = variants.by_livreur(await db.list_livreur_variants())
        for o in out:
            mine = flavors.get(o["user"].get("display_name") or "", {})
            o["v"] = [(v, v in mine.get(p, set())) for p, v in wanted]

    def order(o):
        minutes = transport.travel_minutes(o["m"], o["user"].get("transport_mode")) if o["m"] is not None else 1e9
        missing = sum(1 for _, ok in o["v"] if not ok)
        return (o["busy"] > 0, o["busy"], missing, minutes, o["user"].get("display_name") or "")

    return sorted(out, key=order)


def build(course: dict, opts: list[dict]):
    return keyboards.franchise_pick(course["id"], opts)
