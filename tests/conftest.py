"""Fixtures communes.

Les tests qui ont besoin d'une base (test_lock, test_scenarios) tournent contre :
- une base Supabase de TEST : SUPABASE_URL_TEST + SUPABASE_SERVICE_KEY_TEST
  (⚠️ toutes les tables sont vidées : ne jamais pointer vers la production), ou
- un PostgREST local devant un PostgreSQL local : POSTGREST_URL_TEST
  (voir README, section « Tests »).
Sans ces variables, ces tests sont ignorés.
"""
import os

import pytest

from bot import config, db

TABLES = ["restocks", "events", "messages", "broadcasts", "livreur_positions", "courses", "drafts", "users", "products"]

TEST_CONFIG = config.Config(
    telegram_bot_token="123:test",
    supabase_url="http://test",
    supabase_service_key="test",
    anthropic_api_key="test",
    dispatch_telegram_id=1000,
    openai_api_key=None,
    extraction_mode="ia",  # les scénarios historiques simulent l'extracteur IA
)


@pytest.fixture(autouse=True)
def test_config():
    config.set_config(TEST_CONFIG)
    yield TEST_CONFIG


async def _client():
    if os.environ.get("SUPABASE_URL_TEST"):
        from supabase import acreate_client

        return await acreate_client(os.environ["SUPABASE_URL_TEST"], os.environ["SUPABASE_SERVICE_KEY_TEST"])
    if os.environ.get("POSTGREST_URL_TEST"):
        from postgrest import AsyncPostgrestClient

        return AsyncPostgrestClient(os.environ["POSTGREST_URL_TEST"])
    return None


# Colonne toujours renseignée, pour filtrer un DELETE « toutes les lignes ».
_ALL_ROWS = {"broadcasts": "sent_at", "livreur_positions": "updated_at"}


async def wipe() -> None:
    for table in TABLES:
        await db._t(table).delete().gte(_ALL_ROWS.get(table, "created_at"), "1970-01-01").execute()


@pytest.fixture
async def database():
    client = await _client()
    if client is None:
        pytest.skip("Aucune base de test (SUPABASE_URL_TEST ou POSTGREST_URL_TEST)")
    db.init(client)
    await wipe()
    yield client
    await wipe()
