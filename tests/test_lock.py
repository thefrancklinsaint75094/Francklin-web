"""Le verrou de prise de course (§9.3) : deux prises simultanées, un seul gagnant.

Tourne contre une vraie base (voir conftest.py) et passe par db.take_course,
c'est-à-dire exactement le code du bot."""
import asyncio

import pytest

from bot import db


async def _setup(n_livreurs: int):
    franchise = await db.create_user({"telegram_id": 1, "role": "franchise", "status": "active",
                                      "display_name": "Franchisé 1"})
    livreurs = [
        await db.create_user({"telegram_id": 100 + i, "role": "livreur", "status": "active",
                              "display_name": f"Livreur {i + 1}", "on_duty": True})
        for i in range(n_livreurs)
    ]
    return franchise, livreurs


async def _course(franchise):
    return await db.create_course({
        "franchise_id": franchise["id"], "raw_message": "x", "address": "12 Rue de Rivoli 75004 Paris",
        "postal_code": "75004", "district": "Paris 4e", "lat": 48.855, "lon": 2.357,
        "products": "vodka", "price": 60,
    })


async def test_two_simultaneous_takes_one_winner(database):
    franchise, (a, b) = await _setup(2)
    course = await _course(franchise)
    results = await asyncio.gather(db.take_course(course["id"], a["id"]), db.take_course(course["id"], b["id"]))
    winners = [r for r in results if r is not None]
    assert len(winners) == 1
    stored = await db.get_course(course["id"])
    assert stored["status"] == "assigned"
    assert stored["livreur_id"] == winners[0]["livreur_id"]
    assert stored["assigned_at"] is not None


@pytest.mark.parametrize("attempt", range(5))
async def test_many_simultaneous_takes(database, attempt):
    franchise, livreurs = await _setup(8)
    course = await _course(franchise)
    results = await asyncio.gather(*(db.take_course(course["id"], lv["id"]) for lv in livreurs))
    assert sum(r is not None for r in results) == 1


async def test_take_after_assignment_fails(database):
    franchise, (a, b) = await _setup(2)
    course = await _course(franchise)
    assert await db.take_course(course["id"], a["id"]) is not None
    assert await db.take_course(course["id"], b["id"]) is None
    assert (await db.get_course(course["id"]))["livreur_id"] == a["id"]


async def test_cannot_take_cancelled_course(database):
    franchise, (a,) = await _setup(1)
    course = await _course(franchise)
    await db.update_course(course["id"], {"status": "cancelled"})
    assert await db.take_course(course["id"], a["id"]) is None
