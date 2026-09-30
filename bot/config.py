"""Lecture et validation des variables d'environnement."""
from __future__ import annotations

import os
from dataclasses import dataclass
from zoneinfo import ZoneInfo

from dotenv import load_dotenv

PARIS = ZoneInfo("Europe/Paris")
ANTHROPIC_MODEL = "claude-haiku-4-5-20251001"


class ConfigError(RuntimeError):
    pass


def _raw(name: str) -> str | None:
    value = os.environ.get(name)
    if value is None:
        return None
    # Tolère les commentaires en fin de ligne recopiés depuis .env.example.
    value = value.split(" #", 1)[0].strip()
    return value or None


def _required(name: str) -> str:
    value = _raw(name)
    if not value:
        raise ConfigError(f"Variable d'environnement obligatoire manquante : {name}")
    return value


def _int(name: str, default: int) -> int:
    value = _raw(name)
    if value is None:
        return default
    try:
        return int(value)
    except ValueError as exc:
        raise ConfigError(f"{name} doit être un nombre entier (reçu : {value!r})") from exc


def _names(name: str, default: str) -> tuple[str, ...]:
    """Liste de noms séparés par des virgules ; « - » pour n'en mettre aucun."""
    value = _raw(name)
    if value is None:
        value = default
    return tuple(n.strip() for n in value.split(",") if n.strip() and n.strip() != "-")


def _mode() -> str:
    """EXTRACTION_MODE : « regles » (lecture par règles, sans IA) ou « ia ».
    Sans valeur : « ia » si une clé Anthropic est fournie, sinon « regles »."""
    value = (_raw("EXTRACTION_MODE") or "").lower().replace("è", "e")
    if not value:
        return "ia" if _raw("ANTHROPIC_API_KEY") else "regles"
    if value not in ("regles", "ia"):
        raise ConfigError(f"EXTRACTION_MODE doit valoir « regles » ou « ia » (reçu : {value!r})")
    return value


@dataclass(frozen=True)
class Config:
    telegram_bot_token: str
    supabase_url: str
    supabase_service_key: str
    anthropic_api_key: str | None
    dispatch_telegram_id: int
    openai_api_key: str | None = None
    extraction_mode: str = "regles"   # "regles" (sans IA) ou "ia" (Anthropic)
    departements_autorises: tuple[str, ...] = ("75", "77", "78", "91", "92", "93", "94", "95")
    broadcast_wave_size: int = 3
    broadcast_wave_seconds: int = 120
    position_stale_minutes: int = 30
    soon_free_radius_meters: int = 400
    draft_expiry_minutes: int = 15
    max_distance_km: int = 25
    stuck_course_minutes: int = 75
    price_max: int = 2000
    data_retention_days: int = 90
    night_start_hour: int = 18
    night_end_hour: int = 6
    anthropic_model: str = ANTHROPIC_MODEL
    boxes: tuple[str, ...] = ("Box 1", "Box 2", "Box 3")
    arrival_notify_meters: int = 500
    arrival_notify_minutes: int = 5
    stock_alerts: bool = True
    livreur_names: tuple[str, ...] = ()        # noms des livreurs dans les feuilles (vide : « Livreur 1 »…)
    ravitailleur_names: tuple[str, ...] = ()
    position_alert_minutes: int = 10           # dispatch prévenu quand un livreur en service n'envoie plus sa position
    transport_max_km: float = 5.0              # un livreur en transport / à pied ne reçoit pas de course plus loin
    stations_url: str | None = None            # liste des stations IDF (vide : adresse par défaut, « - » : aucune)

    @property
    def uses_ai(self) -> bool:
        return self.extraction_mode == "ia"

    @classmethod
    def from_env(cls) -> "Config":
        load_dotenv()
        try:
            dispatch_id = int(_required("DISPATCH_TELEGRAM_ID"))
        except ValueError as exc:
            raise ConfigError("DISPATCH_TELEGRAM_ID doit être un identifiant numérique") from exc
        deps = _raw("DEPARTEMENTS_AUTORISES") or "75,77,78,91,92,93,94,95"
        departements = tuple(d.strip() for d in deps.split(",") if d.strip())
        if not departements:
            raise ConfigError("DEPARTEMENTS_AUTORISES est vide")
        boxes = tuple(b.strip() for b in (_raw("BOXES") or "Box 1,Box 2,Box 3").split(",") if b.strip())
        cfg = cls(
            telegram_bot_token=_required("TELEGRAM_BOT_TOKEN"),
            supabase_url=_required("SUPABASE_URL"),
            supabase_service_key=_required("SUPABASE_SERVICE_KEY"),
            anthropic_api_key=_raw("ANTHROPIC_API_KEY"),
            dispatch_telegram_id=dispatch_id,
            openai_api_key=_raw("OPENAI_API_KEY"),
            departements_autorises=departements,
            broadcast_wave_size=_int("BROADCAST_WAVE_SIZE", 3),
            broadcast_wave_seconds=_int("BROADCAST_WAVE_SECONDS", 120),
            position_stale_minutes=_int("POSITION_STALE_MINUTES", 30),
            soon_free_radius_meters=_int("SOON_FREE_RADIUS_METERS", 400),
            draft_expiry_minutes=_int("DRAFT_EXPIRY_MINUTES", 15),
            max_distance_km=_int("MAX_DISTANCE_KM", 25),
            stuck_course_minutes=_int("STUCK_COURSE_MINUTES", 75),
            price_max=_int("PRICE_MAX", 2000),
            data_retention_days=_int("DATA_RETENTION_DAYS", 90),
            night_start_hour=_int("NIGHT_START_HOUR", 18),
            night_end_hour=_int("NIGHT_END_HOUR", 6),
            anthropic_model=_raw("ANTHROPIC_MODEL") or ANTHROPIC_MODEL,
            extraction_mode=_mode(),
            boxes=boxes or ("Box 1",),
            arrival_notify_meters=_int("ARRIVAL_NOTIFY_METERS", 500),
            arrival_notify_minutes=_int("ARRIVAL_NOTIFY_MINUTES", 5),
            stock_alerts=(_raw("STOCK_ALERTS") or "1").lower() not in ("0", "non", "false", "off"),
            livreur_names=_names("LIVREUR_NAMES", "Livreur A,Livreur B,Livreur C,Livreur D,Livreur R,Livreur X"),
            ravitailleur_names=_names("RAVITAILLEUR_NAMES", "Ravitailleur 1,Ravitailleur 2"),
            position_alert_minutes=_int("POSITION_ALERT_MINUTES", 10),
            transport_max_km=float(_raw("TRANSPORT_MAX_KM") or 5),
            stations_url=_raw("STATIONS_URL"),
        )
        if cfg.extraction_mode == "ia" and not cfg.anthropic_api_key:
            raise ConfigError("EXTRACTION_MODE=ia demande ANTHROPIC_API_KEY")
        if cfg.broadcast_wave_size < 1:
            raise ConfigError("BROADCAST_WAVE_SIZE doit être au moins 1")
        if not (0 <= cfg.night_end_hour <= 23 and 0 <= cfg.night_start_hour <= 23):
            raise ConfigError("NIGHT_START_HOUR et NIGHT_END_HOUR doivent être entre 0 et 23")
        return cfg


_config: Config | None = None


def get() -> Config:
    """Configuration courante (chargée depuis l'environnement au premier appel)."""
    global _config
    if _config is None:
        _config = Config.from_env()
    return _config


def set_config(cfg: Config) -> None:
    """Utilisé par les tests pour injecter une configuration."""
    global _config
    _config = cfg
