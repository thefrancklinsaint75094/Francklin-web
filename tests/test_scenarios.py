"""Scénarios de bout en bout : vrais handlers + vraie base + faux Telegram.

Tourne contre la base de test (voir conftest.py)."""
import asyncio
import csv
import io
from datetime import timedelta

import pytest

from bot import db, texts
from bot.handlers import dispatch as dispatch_h
from bot.handlers import franchise as franchise_h
from bot.services import broadcast, geocoding, lifecycle
from bot.services.extraction import to_order
from bot.services.geocoding import GeocodeResult
from bot.timeutil import iso, now_utc
from tests.harness import Harness

DISPATCH = 1000
F1, F2 = 2001, 2002
L1, L2, L3 = 3001, 3002, 3003

ADDR = {
    "12 rue de Rivoli, 75004 Paris": GeocodeResult("12 Rue de Rivoli 75004 Paris", "75004", "Paris", 48.8556, 2.3577, 0.95, "housenumber"),
    "8 rue Oberkampf, 75011 Paris": GeocodeResult("8 Rue Oberkampf 75011 Paris", "75011", "Paris", 48.8645, 2.3700, 0.93, "housenumber"),
    "12 rue de Paris, Montreuil": GeocodeResult("12 Rue de Paris 93100 Montreuil", "93100", "Montreuil", 48.8606, 2.4390, 0.9, "housenumber"),
    "rue de Charenton, 75012 Paris": GeocodeResult("Rue de Charenton 75012 Paris", "75012", "Paris", 48.8448, 2.3790, 0.8, "street"),
    "3 rue de la République, Lyon": GeocodeResult("3 Rue de la République 69002 Lyon", "69002", "Lyon", 45.76, 4.83, 0.9, "housenumber"),
}
BASTILLE = (48.8532, 2.3691)       # ~1 km de Rivoli
REPUBLIQUE = (48.8671, 2.3636)     # ~1,5 km de Rivoli
FAR = (49.40, 2.35)                # > 25 km

RIVOLI = {"address": "12 rue de Rivoli, 75004 Paris", "address_detail": "digicode 45A32",
          "products": "2 vodka + coca", "price": 60, "requested_time": None}
OBERKAMPF = {"address": "8 rue Oberkampf, 75011 Paris", "address_detail": None,
             "products": "1 jack daniels", "price": 50, "requested_time": "vers 23h"}
MONTREUIL = {"address": "12 rue de Paris, Montreuil", "address_detail": "code 1234B",
             "products": "2 rosé + glace", "price": 40, "requested_time": None}

EXTRACTIONS = {
    "rivoli": [RIVOLI],
    "oberkampf": [OBERKAMPF],
    "deux": [OBERKAMPF, MONTREUIL],
    "sans prix": [{**RIVOLI, "price": None}],
    "lyon": [{**RIVOLI, "address": "3 rue de la République, Lyon"}],
    "charenton": [{**RIVOLI, "address": "rue de Charenton, 75012 Paris"}],
    "introuvable": [{**RIVOLI, "address": "99 rue qui n'existe pas"}],
    "cher": [{**RIVOLI, "price": 2500}],
    "pas rond": [{**RIVOLI, "price": 65}],
    "bonjour": [],
    "le digicode c'est 45B en fait": [{"address": None, "address_detail": "digicode 45B", "products": None,
                                       "price": None, "requested_time": None}],
}


class FakeExtractor:
    def __init__(self):
        self.calls = []

    async def extract_text(self, text):
        self.calls.append(text)
        return [to_order(dict(o)) for o in EXTRACTIONS[text]]

    async def extract_image(self, image, media_type, caption=None):
        self.calls.append(("image", media_type, caption))
        return [to_order(dict(RIVOLI))]


@pytest.fixture
async def h(database, monkeypatch):
    async def fake_geocode(address, client=None):
        return ADDR.get(address)

    monkeypatch.setattr(geocoding, "geocode", fake_geocode)
    franchise_h.set_extractor(FakeExtractor())
    lifecycle._no_livreur.clear()
    lifecycle._notice.clear()
    harness = await Harness.create()
    yield harness
    await harness.close()


async def register(h: Harness, tg_id: int, role: str, name: str, username: str | None = None) -> dict:
    await h.text(tg_id, "/start", username=username)
    await h.press(tg_id, h.tg.last(tg_id), f"role:{role}", username=username)
    await h.text(tg_id, name, username=username)
    card = h.tg.find(DISPATCH, f"Nom : {name}")
    await h.press(DISPATCH, card, "approve:")
    return await db.get_user_by_tg(tg_id)


async def setup_network(h: Harness, livreurs=(L1, L2)):
    await h.text(DISPATCH, "/start")
    f1 = await register(h, F1, "franchise", "Bar du Coin", "barducoin")
    f2 = await register(h, F2, "franchise", "Nassim")
    ls = [await register(h, tg, "livreur", f"Livreur-{tg}", f"lv{tg}") for tg in livreurs]
    return f1, f2, ls


async def order(h: Harness, tg_id: int, key: str):
    await h.text(tg_id, key)
    card = h.tg.last(tg_id)
    await h.press(tg_id, card, "draft_confirm:")
    course = (await db.list_franchise_courses_since((await db.get_user_by_tg(tg_id))["id"],
                                                    now_utc() - timedelta(hours=1)))[-1]
    return course


# ====================================================================== étape 1

async def test_onboarding_and_welcome(h):
    await h.text(DISPATCH, "/start")
    assert h.tg.last(DISPATCH).text.startswith("Dispatch actif.")
    await h.text(F1, "/start", username="barducoin")
    assert h.tg.last(F1).text == texts.ASK_ROLE
    await h.press(F1, h.tg.last(F1), "role:franchise", username="barducoin")
    assert h.tg.last(F1).text == texts.ASK_NAME
    await h.text(F1, "hello", username="barducoin")  # le nom
    assert h.tg.last(F1).text == texts.REGISTRATION_SENT
    card = h.tg.last(DISPATCH)
    assert "🆕 Nouvelle inscription" in card.text and "Rôle : Franchisé" in card.text and "@barducoin" in card.text
    # Un pending qui écrit reçoit le message d'attente.
    await h.text(F1, "12 rue de Rivoli", username="barducoin")
    assert h.tg.last(F1).text == texts.PENDING
    approve_data = card.data("approve:")
    await h.press(DISPATCH, card, "approve:")
    assert h.tg.last(F1).text == texts.welcome("franchise")
    assert "Pleins pouvoirs" in h.tg.last(F1).text
    u = await db.get_user_by_tg(F1)
    assert u["status"] == "active" and u["display_name"] == "Franchisé 1" and u["real_name"] == "hello"
    # Deuxième appui sur Valider : idempotent.
    await h.press_data(DISPATCH, card, approve_data)
    assert (await db.get_user_by_tg(F1))["display_name"] == "Franchisé 1"

    lv = await register(h, L1, "livreur", "Karim", "karim_75")
    assert lv["display_name"] == "Livreur 1"
    assert h.tg.last(L1).text == texts.WELCOME_LIVREUR
    f2 = await register(h, F2, "franchise", "Nassim")
    assert f2["display_name"] == "Franchisé 2"


async def test_reject_and_unknown(h):
    await h.text(DISPATCH, "/start")
    await h.text(4000, "coucou")
    assert h.tg.last(4000).text == texts.START_TO_REGISTER
    await h.text(4000, "/start")
    await h.press(4000, h.tg.last(4000), "role:livreur")
    await h.text(4000, "Sofiane")
    await h.press(DISPATCH, h.tg.last(DISPATCH), "reject:")
    assert h.tg.last(4000).text == texts.REJECTED
    assert await db.get_user_by_tg(4000) is None


async def test_banned_user_gets_silence(h):
    f1, f2, (l1, l2) = await setup_network(h)
    await h.text(DISPATCH, "/exclure")
    await h.press(DISPATCH, h.tg.last(DISPATCH), f"ban:{l2['id']}")
    assert "Exclure Livreur 2 (Livreur-3002) ?" in h.tg.last(DISPATCH).text
    await h.press(DISPATCH, h.tg.last(DISPATCH), "ban_yes:")
    assert h.tg.last(L2).text == texts.BANNED_NOTICE
    n = len(h.tg.inbox(L2))
    await h.text(L2, "/start")
    await h.text(L2, "/dispo")
    await h.location(L2, *BASTILLE)
    assert len(h.tg.inbox(L2)) == n
    # Réactivation
    await h.text(DISPATCH, "/reactiver")
    await h.press(DISPATCH, h.tg.last(DISPATCH), f"unban:{l2['id']}")
    assert (await db.get_user_by_tg(L2))["status"] == "active"
    assert texts.UNBANNED_NOTICE in h.tg.last(L2).text


# ====================================================================== étape 2

async def test_order_card_and_confirmation_without_livreur(h):
    f1, f2, _ = await setup_network(h, livreurs=())
    await h.text(F1, "rivoli")
    card = h.tg.last(F1)
    assert "📍 12 Rue de Rivoli 75004 Paris" in card.text
    assert "🔑 Digicode 45A32" in card.text and "🍾 2 vodka + coca" in card.text and "💶 60 €" in card.text
    assert card.text.endswith("C'est correct ?")
    assert [d.split(":")[0] for _, d in card.buttons] == ["draft_confirm", "draft_edit", "draft_cancel"]
    confirm_data = card.data("draft_confirm:")
    await h.press(F1, card, "draft_confirm:")
    course = (await db.list_courses_by_status("pending"))[0]
    msg = h.tg.messages[(F1, card.message_id)]
    assert "enregistrée — ⚠️ aucun livreur en service" in msg.text
    assert msg.data("course_withdraw:") == f"course_withdraw:{course['id']}"
    assert any(t.startswith(f"🆕 #{course['id']} — Franchisé 1 — Paris 4e — 60") for t in h.tg.texts(DISPATCH))
    assert f"🔴 #{course['id']} — aucun livreur disponible" in h.tg.texts(DISPATCH)
    # Double confirmation : rien de créé.
    await h.press_data(F1, card, confirm_data)
    assert len(await db.list_courses_by_status("pending")) == 1
    assert h.tg.answers()[-1]["text"] == f"Déjà confirmée (course #{course['id']})."


async def test_multi_orders_headers(h):
    await setup_network(h, livreurs=())
    await h.text(F1, "deux")
    cards = h.tg.inbox(F1)[-2:]
    assert cards[0].text.startswith("<b>Commande 1 sur 2</b>")
    assert cards[1].text.startswith("<b>Commande 2 sur 2</b>")
    assert "🕐 vers 23h" in cards[0].text
    assert "Montreuil" in cards[1].text


async def test_invalid_orders(h):
    await setup_network(h, livreurs=())
    await h.text(F1, "sans prix")
    assert h.tg.last(F1).text == "Il me manque le prix pour : 12 rue de Rivoli, 75004 Paris."
    await h.text(F1, "lyon")
    assert h.tg.last(F1).text == "Cette adresse est hors zone (Lyon 69002). Je ne peux pas la prendre."
    await h.text(F1, "introuvable")
    assert "Je n'arrive pas à localiser cette adresse" in h.tg.last(F1).text
    await h.text(F1, "bonjour")
    assert h.tg.last(F1).text == texts.NO_ORDER_FOUND
    await h.text(F1, "charenton")
    assert "⚠️ Pas de numéro de rue trouvé — vérifie l'adresse" in h.tg.last(F1).text
    await h.text(F1, "cher")
    assert "⚠️ Prix élevé, vérifie" in h.tg.last(F1).text
    await h.sticker(F1)
    assert h.tg.last(F1).text == texts.ONLY_TEXT_OR_VOICE
    await h.voice(F1)
    assert h.tg.last(F1).text == texts.VOICE_UNSUPPORTED


async def test_duplicate_and_complement(h):
    await setup_network(h, livreurs=())
    course = await order(h, F1, "rivoli")
    await h.text(F1, "rivoli")
    assert f"⚠️ Ressemble à la course #{course['id']} envoyée il y a 0 min" in h.tg.last(F1).text
    await h.press(F1, h.tg.last(F1), "draft_confirm:")
    dup = (await db.list_courses_by_status("pending"))[-1]
    assert dup["possible_duplicate_of"] == course["id"]
    await h.text(F1, "le digicode c'est 45B en fait")
    assert h.tg.last(F1).text == texts.complement_hint(dup["id"])


async def test_correction_flow(h):
    await setup_network(h, livreurs=())
    await h.text(F1, "rivoli")
    card = h.tg.last(F1)
    confirm_data = card.data("draft_confirm:")
    await h.press(F1, card, "draft_edit:")
    assert h.tg.messages[(F1, card.message_id)].text == texts.CORRECTION_PROMPT
    await h.text(F1, "oberkampf")
    new_card = h.tg.last(F1)
    assert "Oberkampf" in new_card.text
    assert h.tg.messages[(F1, card.message_id)].text == texts.DRAFT_REPLACED
    assert new_card.data("draft_confirm:") == confirm_data  # même draft réutilisé
    assert (await db.get_user_by_tg(F1))["conversation_state"] is None
    await h.press(F1, new_card, "draft_confirm:")
    assert (await db.list_courses_by_status("pending"))[0]["address"] == "8 Rue Oberkampf 75011 Paris"


async def test_draft_cancel_and_expiry(h):
    from bot import jobs
    await setup_network(h, livreurs=())
    await h.text(F1, "rivoli")
    card = h.tg.last(F1)
    await h.press(F1, card, "draft_cancel:")
    assert h.tg.messages[(F1, card.message_id)].text == texts.DRAFT_CANCELLED
    await h.text(F1, "oberkampf")
    card2 = h.tg.last(F1)
    draft_id = card2.data("draft_confirm:").split(":")[1]
    await db.update_draft(draft_id, {"created_at": iso(now_utc() - timedelta(minutes=16))})
    await jobs.expire_drafts(h.context)
    assert h.tg.messages[(F1, card2.message_id)].text == texts.DRAFT_EXPIRED
    assert h.tg.messages[(F1, card2.message_id)].buttons == []
    await h.press_data(F1, card2, f"draft_confirm:{draft_id}")
    assert await db.list_courses_by_status("pending") == []


async def test_photo_order(h):
    await setup_network(h, livreurs=())
    h.tg.files["photo1"] = b"\xff\xd8jpeg"
    await h.photo(F1, caption="de la part de Paul")
    assert "📍 12 Rue de Rivoli 75004 Paris" in h.tg.last(F1).text
    assert franchise_h.extractor().calls[-1] == ("image", "image/jpeg", "de la part de Paul")


# ====================================================================== étape 3

async def go_on_duty(h, tg_id, pos):
    await h.text(tg_id, "/dispo")
    await h.location(tg_id, *pos, message_id=tg_id)


async def test_dispo_broadcast_take_and_deliver(h):
    f1, f2, (l1, l2) = await setup_network(h)
    await h.text(L1, "/dispo")
    assert h.tg.last(L1).text == texts.DISPO_PROMPT
    await h.location(L1, *BASTILLE, message_id=L1)
    assert h.tg.last(L1).text == texts.ON_DUTY
    await go_on_duty(h, L2, REPUBLIQUE)

    course = await order(h, F1, "rivoli")
    prop1, prop2 = h.tg.last(L1), h.tg.last(L2)
    for p in (prop1, prop2):
        assert p.text.startswith(f"🆕 Course #{course['id']}")
        assert "Paris 4e · à ~" in p.text
        assert "Rivoli" not in p.text and "45A32" not in p.text  # jamais l'adresse avant la prise
    assert "à ~900 m de toi" in prop1.text

    await h.press(L1, prop1, "course_take:")
    fiche = h.tg.messages[(L1, prop1.message_id)]
    assert fiche.text.startswith(f"🚴 Course #{course['id']} — c'est pour toi")
    assert "📍 12 Rue de Rivoli 75004 Paris" in fiche.text and "🔑 Digicode 45A32" in fiche.text
    assert "💶 60 € à encaisser" in fiche.text
    assert "Franchisé 1 — @barducoin" in fiche.text
    assert h.tg.messages[(L2, prop2.message_id)].text == texts.proposal_taken(course["id"])
    fmsg = h.tg.find(F1, f"Course #{course['id']} — prise par Livreur 1")
    assert "lv3001" not in fmsg.text
    assert [d.split(":")[0] for _, d in fmsg.buttons] == ["force_deliver", "order_edit", "relay_start",
                                                          "course_withdraw"]
    assert f"🚴 #{course['id']} — Franchisé 1 → Livreur 1 — Paris 4e — 60 €" in h.tg.texts(DISPATCH)

    # L2 appuie trop tard.
    await h.press_data(L2, prop2, f"course_take:{course['id']}")
    assert h.tg.answers()[-1]["text"] == texts.TOO_LATE

    await h.text(L1, "/macourse")
    assert h.tg.last(L1).text.startswith(f"🚴 Course #{course['id']}")

    await h.press(L1, fiche, "course_deliver:")
    delivered = await db.get_course(course["id"])
    assert delivered["status"] == "delivered" and delivered["delivered_distance_m"] is not None
    assert "✅ Course" in h.tg.last(L1).text or "livrée" in h.tg.messages[(L1, fiche.message_id)].text
    assert "— livrée à" in h.tg.find(F1, f"Course #{course['id']} — livrée").text
    assert f"✅ #{course['id']} — livrée — Livreur 1 — 60 €" in h.tg.texts(DISPATCH)
    # Double « Livré » : sans effet.
    await h.press_data(L1, fiche, f"course_deliver:{course['id']}")
    assert h.tg.answers()[-1]["text"] == "Déjà livrée."

    await h.text(F1, "/mescourses")
    assert f"#{course['id']} — Paris 4e — 60 € — ✅ livrée" in h.tg.last(F1).text


async def test_simultaneous_takes_through_handlers(h):
    f1, f2, (l1, l2) = await setup_network(h)
    await go_on_duty(h, L1, BASTILLE)
    await go_on_duty(h, L2, REPUBLIQUE)
    course = await order(h, F1, "rivoli")
    p1, p2 = h.tg.last(L1), h.tg.last(L2)
    await asyncio.gather(h.press(L1, p1, "course_take:"), h.press(L2, p2, "course_take:"))
    stored = await db.get_course(course["id"])
    assert stored["status"] == "assigned"
    winner, loser = (L1, L2) if stored["livreur_id"] == l1["id"] else (L2, L1)
    assert "c'est pour toi" in h.tg.last(winner).text or any(
        "c'est pour toi" in m.text for m in h.tg.inbox(winner))
    assert all("c'est pour toi" not in m.text for m in h.tg.inbox(loser))


async def test_far_livreur_never_solicited(h):
    f1, f2, (l1,) = await setup_network(h, livreurs=(L1,))
    await go_on_duty(h, L1, FAR)
    course = await order(h, F1, "rivoli")
    assert not any(f"Course #{course['id']}" in t for t in h.tg.texts(L1))
    assert "aucun livreur disponible pour l'instant" in h.tg.find(F1, f"Course #{course['id']}").text


# ====================================================================== étape 4

async def test_no_livreur_then_livreur_comes_online(h):
    f1, f2, (l1,) = await setup_network(h, livreurs=(L1,))
    course = await order(h, F1, "rivoli")
    assert f"🔴 #{course['id']} — aucun livreur disponible" in h.tg.texts(DISPATCH)
    await go_on_duty(h, L1, BASTILLE)
    assert h.tg.last(L1).text.startswith(f"🆕 Course #{course['id']}")
    assert h.tg.find(F1, f"Course #{course['id']}").text.startswith(f"✅ Course #{course['id']} envoyée aux livreurs.")


async def test_waves_widen(h, test_config):
    livreur_ids = (L1, L2, L3, 3004)
    f1, f2, ls = await setup_network(h, livreurs=livreur_ids)
    for i, tg in enumerate(livreur_ids):
        await go_on_duty(h, tg, (BASTILLE[0] + i * 0.003, BASTILLE[1]))
    course = await order(h, F1, "rivoli")
    solicited = {b["livreur_id"] for b in await db.list_broadcasts(course["id"])}
    assert len(solicited) == 3 and ls[3]["id"] not in solicited  # les 3 plus proches
    await broadcast.run_wave(h.context, course["id"], advance=True)
    bs = await db.list_broadcasts(course["id"])
    assert len(bs) == 4 and {b["round"] for b in bs} == {1, 2}
    assert (await db.get_course(course["id"]))["broadcast_round"] == 2
    # Un livreur de la vague 1 peut encore la prendre.
    first = h.tg.find(L1, f"🆕 Course #{course['id']}")
    await h.press(L1, first, "course_take:")
    assert (await db.get_course(course["id"]))["livreur_id"] == ls[0]["id"]


async def test_one_course_at_a_time_and_soon_free(h):
    f1, f2, (l1,) = await setup_network(h, livreurs=(L1,))
    await go_on_duty(h, L1, BASTILLE)
    c1 = await order(h, F1, "rivoli")
    await h.press(L1, h.tg.last(L1), "course_take:")
    c2 = await order(h, F2, "oberkampf")
    assert not any(f"Course #{c2['id']}" in t for t in h.tg.texts(L1))  # occupé
    # Il s'approche de l'adresse : « bientôt libre » automatique.
    await h.location(L1, 48.8557, 2.3578, edited=True, message_id=L1)
    assert (await db.get_user(l1["id"]))["soon_free"] is True
    assert texts.SOON_FREE_AUTO in h.tg.texts(L1)
    prop = h.tg.find(L1, f"🆕 Course #{c2['id']}")
    await h.press(L1, prop, "course_take:")
    assert len(await db.list_assigned_for_livreur(l1["id"])) == 2
    c3 = await order(h, F1, "deux")  # pas de 3e course
    assert all("🆕" not in t for t in h.tg.texts(L1)[-3:])
    # Livraison de la première : soon_free retombe.
    fiche1 = h.tg.find(L1, f"🚴 Course #{c1['id']}")
    await h.press(L1, fiche1, "course_deliver:")
    assert (await db.get_user(l1["id"]))["soon_free"] is False
    del c3


async def test_livreur_cancel_rebroadcast(h):
    f1, f2, (l1, l2) = await setup_network(h)
    await go_on_duty(h, L1, BASTILLE)
    course = await order(h, F1, "rivoli")
    await h.press(L1, h.tg.last(L1), "course_take:")
    await go_on_duty(h, L2, REPUBLIQUE)  # arrive après l'attribution
    fiche = h.tg.find(L1, f"🚴 Course #{course['id']}")
    await h.press(L1, fiche, "course_livreur_cancel:")
    assert texts.LIVREUR_CANCEL_CONFIRM in h.tg.messages[(L1, fiche.message_id)].text
    await h.press(L1, h.tg.messages[(L1, fiche.message_id)], "lc_no:")
    assert h.tg.messages[(L1, fiche.message_id)].text.startswith(f"🚴 Course #{course['id']}")
    await h.press(L1, fiche, "course_livreur_cancel:")
    await h.press(L1, h.tg.messages[(L1, fiche.message_id)], "lc_yes:")
    stored = await db.get_course(course["id"])
    assert stored["status"] == "pending" and stored["livreur_id"] is None and stored["broadcast_round"] == 1
    assert (await db.get_user(l1["id"]))["cancel_count"] == 1
    assert h.tg.messages[(L1, fiche.message_id)].text == texts.livreur_cancelled(course["id"])
    assert h.tg.last(L2).text.startswith(f"🆕 Course #{course['id']}")
    assert "Livreur 1 a annulé la course" in h.tg.find(F1, f"course #{course['id']}").text
    assert f"⚠️ #{course['id']} — annulée par Livreur 1 (1re annulation) — remise en diffusion" in h.tg.texts(DISPATCH)
    # Le livreur qui a annulé ne la reçoit plus.
    await broadcast.run_wave(h.context, course["id"], advance=True)
    assert sum(f"🆕 Course #{course['id']}" in t for t in h.tg.all_texts_ever(L1)) == 1


async def test_franchise_withdraw_pending_and_on_site(h):
    f1, f2, (l1, l2) = await setup_network(h)
    await go_on_duty(h, L1, BASTILLE)
    c1 = await order(h, F1, "rivoli")
    prop = h.tg.last(L1)
    fmsg = h.tg.find(F1, f"Course #{c1['id']}")
    await h.press(F1, fmsg, "course_withdraw:")
    assert h.tg.messages[(F1, fmsg.message_id)].text.startswith(f"Retirer la course #{c1['id']} ?")
    await h.press(F1, h.tg.messages[(F1, fmsg.message_id)], "fw_yes:")
    assert (await db.get_course(c1["id"]))["status"] == "cancelled"
    assert h.tg.messages[(L1, prop.message_id)].text == texts.proposal_withdrawn(c1["id"])
    assert f"🗑 #{c1['id']} — retirée par Franchisé 1" in h.tg.texts(DISPATCH)

    c2 = await order(h, F1, "oberkampf")
    await h.press(L1, h.tg.last(L1), "course_take:")
    fmsg2 = h.tg.find(F1, f"Course #{c2['id']}")
    await h.press(F1, fmsg2, "course_withdraw:")
    await h.press(F1, h.tg.messages[(F1, fmsg2.message_id)], "fw_yes:")
    stored = await db.get_course(c2["id"])
    assert stored["status"] == "cancelled_on_site"
    assert h.tg.last(L1).text == texts.cancelled_by_franchise(c2["id"])
    assert (await db.get_user(l1["id"]))["cancel_count"] == 0
    assert f"⚠️ #{c2['id']} — annulée sur place par Franchisé 1 (Livreur 1 déplacé pour rien)" in h.tg.texts(DISPATCH)
    await h.text(F1, "/mescourses")
    assert "⚠️ annulée sur place" in h.tg.last(F1).text


async def test_static_position_and_stale(h):
    from bot import jobs
    f1, f2, (l1,) = await setup_network(h, livreurs=(L1,))
    await h.location(L1, *BASTILLE, live=False)
    assert h.tg.texts(L1)[-2:] == [texts.ON_DUTY, texts.STATIC_POSITION_WARNING]
    await db._t("livreur_positions").update({"updated_at": iso(now_utc() - timedelta(minutes=31))}).eq(
        "livreur_id", l1["id"]).execute()
    await jobs.stale_positions(h.context)
    assert h.tg.last(L1).text == texts.POSITION_LOST
    assert (await db.get_user(l1["id"]))["on_duty"] is False
    await h.text(L1, "/pause")
    assert h.tg.last(L1).text == texts.PAUSED


async def test_stuck_alert_once(h):
    from bot import jobs
    f1, f2, (l1,) = await setup_network(h, livreurs=(L1,))
    await go_on_duty(h, L1, BASTILLE)
    course = await order(h, F1, "rivoli")
    await h.press(L1, h.tg.last(L1), "course_take:")
    stored = await db.update_course(course["id"], {"assigned_at": iso(now_utc() - timedelta(minutes=80))})
    await jobs.stuck_courses(h.context)
    await jobs.stuck_courses(h.context)
    assert h.tg.texts(L1).count(texts.stuck_for_livreur(course["id"])) == 1
    assert sum(t.startswith(f"⏰ #{course['id']} — en cours depuis") for t in h.tg.texts(DISPATCH)) == 1
    del stored


async def test_restart_resumes_pending(h):
    from bot import jobs
    f1, f2, (l1,) = await setup_network(h, livreurs=(L1,))
    course = await order(h, F1, "rivoli")
    await db.upsert_position(l1["id"], *BASTILLE)
    await db.update_user(l1["id"], {"on_duty": True})
    lifecycle._no_livreur.clear()
    await jobs.startup(h.context)
    assert texts.BOT_STARTED in h.tg.texts(DISPATCH)
    assert h.tg.last(L1).text.startswith(f"🆕 Course #{course['id']}")


async def test_blocked_livreur(h):
    f1, f2, (l1, l2) = await setup_network(h)
    await go_on_duty(h, L1, BASTILLE)
    h.tg.blocked.add(L1)
    await order(h, F1, "rivoli")
    assert (await db.get_user(l1["id"]))["on_duty"] is False
    assert "🚫 Livreur 1 a bloqué le bot" in h.tg.texts(DISPATCH)


# ====================================================================== étape 5

async def test_relay_both_ways(h):
    f1, f2, (l1,) = await setup_network(h, livreurs=(L1,))
    await go_on_duty(h, L1, BASTILLE)
    course = await order(h, F1, "rivoli")
    await h.press(L1, h.tg.last(L1), "course_take:")
    fiche = h.tg.find(L1, "c'est pour toi")

    await h.press(L1, fiche, "relay_start:")
    assert h.tg.last(L1).text == texts.relay_prompt(course["id"])
    await h.text(L1, "Le digicode ne passe pas")
    assert h.tg.last(L1).text == texts.RELAY_SENT
    relayed = h.tg.last(F1)
    assert relayed.text == f"💬 Course #{course['id']} — 12 Rue de Rivoli 75004 Paris\nLivreur 1 : « Le digicode ne passe pas »"
    assert "lv3001" not in relayed.text

    # Réponse native « Répondre » du franchisé.
    await h.text(F1, "Essaie 45B", reply_to=relayed.message_id)
    assert h.tg.last(L1).text.endswith("Franchisé 1 : « Essaie 45B »")
    # Le franchisé qui écrit hors relais : traité comme commande (pas relayé).
    await h.text(L1, "ok merci")
    assert h.tg.last(L1).text == texts.LIVREUR_TEXT_HINT
    # Réponse native du livreur au message relayé.
    await h.text(L1, "C'est bon", reply_to=h.tg.last(L1).message_id - 0 if False else h.tg.find(L1, "Essaie 45B").message_id)
    assert h.tg.last(F1).text.endswith("Livreur 1 : « C'est bon »")
    # Vocal pendant un relais.
    await h.press(F1, h.tg.find(F1, "prise par"), "relay_start:")
    await h.voice(F1)
    assert h.tg.last(F1).text == texts.WRITE_TEXT
    # Course livrée : relais fermé.
    await h.press(L1, fiche, "course_deliver:")
    await h.press_data(F1, relayed, f"relay_start:{course['id']}")
    assert h.tg.last(F1).text == texts.COURSE_FINISHED
    assert len(await db._t("messages").select("*").execute().__await__().__next__() if False else
               (await db._t("messages").select("*").eq("course_id", course["id"]).execute()).data) == 3


async def test_recap_journal_csv(h):
    f1, f2, (l1, l2) = await setup_network(h)
    await go_on_duty(h, L1, BASTILLE)
    c1 = await order(h, F1, "rivoli")
    await h.press(L1, h.tg.last(L1), "course_take:")
    await h.press(L1, h.tg.find(L1, "c'est pour toi"), "course_deliver:")
    c2 = await order(h, F2, "oberkampf")
    await h.press(L1, h.tg.last(L1), "course_take:")
    await h.press(L1, h.tg.find(L1, f"🚴 Course #{c2['id']}"), "course_deliver:")
    await go_on_duty(h, L2, REPUBLIQUE)
    c3 = await order(h, F1, "deux")

    await h.text(DISPATCH, "/recap")
    recap = h.tg.last(DISPATCH).text
    assert "📊 Récap nuit du" in recap
    assert "Livreur 1 — 2 courses — 110 €" in recap
    assert "Total : 2 courses — 110 €" in recap
    assert "En attente : 1 · En cours : 0" in recap

    await h.text(DISPATCH, "/journal")
    journal = h.tg.find(DISPATCH, "📋 Journal nuit du").text
    assert "2 courses livrées" in journal
    assert f"#{c1['id']} · Franchisé 1 → Livreur 1 · 12 Rue de Rivoli 75004 Paris · 60 €" in journal
    assert "Total : 110 €" in journal
    assert len(h.tg.documents(DISPATCH)) == 1

    csv_bytes = dispatch_h.build_csv(await db.list_delivered_between(now_utc() - timedelta(days=1), now_utc()),
                                     await db.get_users([l1["id"], f1["id"], f2["id"]]))
    assert csv_bytes.startswith("﻿".encode())
    rows = list(csv.reader(io.StringIO(csv_bytes.decode("utf-8-sig")), delimiter=";"))
    assert rows[0] == ["numero", "date_livraison", "heure_livraison", "franchise", "livreur", "adresse",
                       "complement", "produits", "prix"]
    assert rows[1][0] == str(c1["id"]) and rows[1][6] == "digicode 45A32" and rows[1][8] == "60,00"

    await h.press(DISPATCH, h.tg.find(DISPATCH, "📊 Récap"), "recap_prev:")
    assert "aucune course livrée" in h.tg.last(DISPATCH).text
    del c3


async def test_encours_and_overrides(h):
    f1, f2, (l1, l2) = await setup_network(h)
    await go_on_duty(h, L1, BASTILLE)
    c1 = await order(h, F1, "rivoli")
    await h.press(L1, h.tg.last(L1), "course_take:")
    c2 = await order(h, F2, "oberkampf")

    await h.text(DISPATCH, "/encours")
    enc = h.tg.last(DISPATCH)
    assert "⏳ En attente (1)" in enc.text and f"#{c2['id']} — Franchisé 2 — Paris 11e — 50 € — vague 1" in enc.text
    assert f"#{c1['id']} — Franchisé 1 → Livreur 1 — Paris 4e — 60 € — depuis 0 min" in enc.text
    assert "📍 Livreurs en service : 1 / 2" in enc.text

    await h.press_data(DISPATCH, enc, f"force_release:{c1['id']}")
    await h.press(DISPATCH, h.tg.last(DISPATCH), "fy:release:")
    stored = await db.get_course(c1["id"])
    assert stored["status"] == "pending" and stored["livreur_id"] is None
    assert (await db.get_user(l1["id"]))["cancel_count"] == 0
    assert texts.released_by_dispatch_for_livreur(c1["id"]) in h.tg.texts(L1)

    await h.press_data(DISPATCH, enc, f"force_cancel:{c2['id']}")
    await h.press(DISPATCH, h.tg.last(DISPATCH), "fy:cancel:")
    assert (await db.get_course(c2["id"]))["status"] == "cancelled"

    await go_on_duty(h, L2, REPUBLIQUE)
    await h.press(L2, h.tg.find(L2, f"🆕 Course #{c1['id']}"), "course_take:")
    await h.press_data(DISPATCH, enc, f"force_deliver:{c1['id']}")
    await h.press(DISPATCH, h.tg.last(DISPATCH), "fy:deliver:")
    assert (await db.get_course(c1["id"]))["status"] == "delivered"
    overrides = (await db._t("events").select("*").eq("type", "dispatch_override").execute()).data
    assert len(overrides) == 3


async def test_users_list(h):
    f1, f2, (l1, l2) = await setup_network(h)
    await h.text(5000, "/start", username="sof")
    await h.press(5000, h.tg.last(5000), "role:livreur", username="sof")
    await h.text(5000, "Sofiane", username="sof")
    await h.text(DISPATCH, "/users")
    text = h.tg.last(DISPATCH).text
    assert "Franchisé 1 — Bar du Coin (@barducoin) — actif" in text
    assert "Livreur 1 — Livreur-3001 (@lv3001) — actif · pause — 0 annulation" in text
    assert "Livreur — Sofiane (@sof)" in text


async def test_ban_livreur_with_course_rebroadcasts(h):
    f1, f2, (l1, l2) = await setup_network(h)
    await go_on_duty(h, L1, BASTILLE)
    course = await order(h, F1, "rivoli")
    await h.press(L1, h.tg.last(L1), "course_take:")
    await go_on_duty(h, L2, REPUBLIQUE)
    await h.text(DISPATCH, "/exclure")
    await h.press_data(DISPATCH, h.tg.last(DISPATCH), f"ban:{l1['id']}")
    await h.press(DISPATCH, h.tg.last(DISPATCH), "ban_yes:")
    assert (await db.get_course(course["id"]))["status"] == "pending"
    assert h.tg.last(L1).text == texts.BANNED_NOTICE
    assert h.tg.last(L2).text.startswith(f"🆕 Course #{course['id']}")


async def test_error_handler_generic_message(h, monkeypatch):
    await setup_network(h, livreurs=())

    async def boom(*a, **k):
        raise RuntimeError("panne")

    monkeypatch.setattr(franchise_h, "process", boom)
    await h.text(F1, "rivoli")
    assert h.tg.last(F1).text == texts.GENERIC_ERROR
    assert "⚠️ Erreur technique : RuntimeError" in h.tg.texts(DISPATCH)
    errors = (await db._t("events").select("*").eq("type", "error").execute()).data
    assert errors


async def test_privacy_wall(h):
    """Le livreur ne voit jamais l'adresse avant la prise ; le franchisé ne voit jamais le @pseudo
    ni le vrai nom du livreur ; personne hors dispatch ne voit les vrais noms."""
    f1, f2, (l1, l2) = await setup_network(h)
    await go_on_duty(h, L1, BASTILLE)
    await go_on_duty(h, L2, REPUBLIQUE)
    course = await order(h, F1, "rivoli")
    await h.press(L1, h.tg.last(L1), "course_take:")
    for text in h.tg.all_texts_ever(L2):
        assert "Rivoli" not in text and "45A32" not in text and "barducoin" not in text
    for tg in (F1, F2):
        for text in h.tg.all_texts_ever(tg):
            assert "lv3001" not in text and "Livreur-3001" not in text and "Livreur-3002" not in text
    for tg in (L1, L2):
        for text in h.tg.all_texts_ever(tg):
            assert "Bar du Coin" not in text and "Nassim" not in text
    del course


async def test_voice_order(h, monkeypatch, test_config):
    import dataclasses

    from bot import config
    from bot.services import transcription

    config.set_config(dataclasses.replace(test_config, openai_api_key="sk-test"))
    await setup_network(h, livreurs=())
    h.tg.files["voice1"] = b"OggS..."
    seen = []

    async def fake_transcribe(audio, api_key, filename="voice.ogg"):
        seen.append(audio)
        return "rivoli"

    monkeypatch.setattr(transcription, "transcribe", fake_transcribe)
    await h.voice(F1, "voice1")
    assert seen == [b"OggS..."]
    card = h.tg.last(F1)
    assert "📍 12 Rue de Rivoli 75004 Paris" in card.text
    draft = await db.get_draft(card.data("draft_confirm:").split(":")[1])
    assert draft["raw_message"] == "[vocal] rivoli"

    async def broken(*a, **k):
        raise transcription.TranscriptionUnavailable("down")

    monkeypatch.setattr(transcription, "transcribe", broken)
    await h.voice(F1, "voice1")
    assert h.tg.last(F1).text == texts.VOICE_FAILED
    errored = (await db._t("drafts").select("*").eq("status", "error").execute()).data
    assert len(errored) == 1 and "[vocal]" in errored[0]["raw_message"]


async def test_cancel_correction_and_relay(h):
    f1, f2, (l1,) = await setup_network(h, livreurs=(L1,))
    await h.text(F1, "rivoli")
    card = h.tg.last(F1)
    await h.press(F1, card, "draft_edit:")
    await h.press(F1, h.tg.messages[(F1, card.message_id)], "draft_edit_cancel:")
    restored = h.tg.messages[(F1, card.message_id)]
    assert restored.text.endswith("C'est correct ?") and restored.data("draft_confirm:")
    assert (await db.get_user_by_tg(F1))["conversation_state"] is None

    await go_on_duty(h, L1, BASTILLE)
    await h.press(F1, restored, "draft_confirm:")
    await h.press(L1, h.tg.last(L1), "course_take:")
    await h.press(L1, h.tg.find(L1, "c'est pour toi"), "relay_start:")
    await h.press(L1, h.tg.last(L1), "relay_cancel")
    assert h.tg.last(L1).text == texts.RELAY_CANCELLED
    await h.text(L1, "message perdu ?")
    assert h.tg.last(L1).text == texts.LIVREUR_TEXT_HINT


def test_split_messages():
    lines = ["x" * 1500 for _ in range(6)]
    chunks = dispatch_h.split_messages(lines)
    assert len(chunks) == 3 and all(len(c) <= 4000 for c in chunks)
    assert "\n".join(chunks).count("x") == 9000


async def test_retention_scrubs_old_data(h):
    from bot import jobs
    f1, f2, (l1,) = await setup_network(h, livreurs=(L1,))
    await go_on_duty(h, L1, BASTILLE)
    course = await order(h, F1, "rivoli")
    await h.press(L1, h.tg.last(L1), "course_take:")
    await h.press(L1, h.tg.find(L1, "c'est pour toi"), "relay_start:")
    await h.text(L1, "je suis en bas")
    await h.press(L1, h.tg.find(L1, "c'est pour toi"), "course_deliver:")
    old = iso(now_utc() - timedelta(days=91))
    await db.update_course(course["id"], {"closed_at": old})
    await db._t("drafts").update({"created_at": old}).gte("created_at", "1970-01-01").execute()
    await jobs.retention(h.context)
    stored = await db.get_course(course["id"])
    assert stored["address_detail"] is None and stored["raw_message"] == "[effacé]"
    assert stored["address"] == "12 Rue de Rivoli 75004 Paris" and float(stored["price"]) == 60
    msgs = (await db._t("messages").select("*").eq("course_id", course["id"]).execute()).data
    assert msgs and all(m["content"] == "[effacé]" for m in msgs)
    drafts = (await db._t("drafts").select("*").execute()).data
    assert all(d["raw_message"] == "[effacé]" and d["extracted"] is None for d in drafts)


async def test_daily_journal_empty(h):
    from bot import jobs
    await h.text(DISPATCH, "/start")
    await jobs.daily_journal(h.context)
    assert "📋 Journal nuit du" in h.tg.last(DISPATCH).text
    assert h.tg.last(DISPATCH).text.endswith("— aucune course livrée.")
    assert h.tg.documents(DISPATCH) == []


async def test_rules_mode_end_to_end(h, monkeypatch, test_config):
    """Mode sans IA : lecture par règles, vocaux et photos refusés poliment."""
    import dataclasses

    from bot import config

    config.set_config(dataclasses.replace(test_config, extraction_mode="regles", anthropic_api_key=None,
                                          openai_api_key="sk-ignored"))
    franchise_h.set_extractor(None)  # l'extracteur par règles sera choisi

    async def geocode_contains(address, client=None):
        if "rivoli" in address.lower():
            return ADDR["12 rue de Rivoli, 75004 Paris"]
        return None

    monkeypatch.setattr(geocoding, "geocode", geocode_contains)
    await h.text(DISPATCH, "/start")
    await register(h, F1, "franchise", "Bar du Coin")
    assert h.tg.last(F1).text == texts.welcome("franchise")
    assert h.tg.last(F1).text.startswith(texts.WELCOME_FRANCHISE_RULES)

    await h.text(F1, "12 rue de rivoli paris 4, digicode 45A32, 2 vodka + coca, 60€")
    card = h.tg.last(F1)
    assert "📍 12 Rue de Rivoli 75004 Paris" in card.text and "🔑 Digicode 45A32" in card.text
    assert "🍾 2 vodka + coca" in card.text and "💶 60 €" in card.text
    await h.press(F1, card, "draft_confirm:")
    assert len(await db.list_courses_by_status("pending")) == 1

    await h.text(F1, "54 rue du point du jour Boulogne 50 mousseux")
    assert h.tg.last(F1).text == "Il me manque le prix pour : 54 rue du point du jour Boulogne."
    await h.voice(F1)
    assert h.tg.last(F1).text == texts.VOICE_UNSUPPORTED
    await h.photo(F1)
    assert h.tg.last(F1).text == texts.PHOTO_UNSUPPORTED
    franchise_h.set_extractor(None)


async def test_catalog_from_bot_and_model_order(h, monkeypatch, test_config):
    """Le dispatch saisit le catalogue depuis le bot ; le franchisé commande avec le modèle."""
    import dataclasses

    from bot import config

    config.set_config(dataclasses.replace(test_config, extraction_mode="regles", anthropic_api_key=None))
    franchise_h.set_extractor(None)

    async def geocode_contains(address, client=None):
        return ADDR["12 rue de Rivoli, 75004 Paris"] if "rivoli" in address.lower() else None

    monkeypatch.setattr(geocoding, "geocode", geocode_contains)
    await h.text(DISPATCH, "/start")
    await register(h, F1, "franchise", "Bar du Coin")

    # Le dispatch ouvre le catalogue et ajoute des produits.
    await h.text(DISPATCH, "/produits")
    assert "Catalogue vide" in h.tg.last(DISPATCH).text
    await h.press(DISPATCH, h.tg.last(DISPATCH), "prod_add")
    assert "un par ligne" in h.tg.last(DISPATCH).text
    await h.text(DISPATCH, "Vodka Absolut : absolut, abso\nCoca-Cola : coca\nJack Daniel's : jack, jd")
    summary = h.tg.inbox(DISPATCH)[-2].text
    assert "Ajoutés : Vodka Absolut, Coca-Cola, Jack Daniel's" in summary
    listing = h.tg.last(DISPATCH)
    assert "Produits connus</b> (3)" in listing.text
    # Compléter un produit existant : pas de doublon.
    await h.text(DISPATCH, "/ajouter vodka absolut : vodka")
    assert "Complété : Vodka Absolut" in h.tg.inbox(DISPATCH)[-2].text
    assert len(await db.list_products()) == 3

    # Le franchisé voit le modèle et les produits.
    await h.text(F1, "/modele")
    assert "Modèle de commande" in h.tg.last(F1).text
    await h.text(F1, "/produits")
    # Pleins pouvoirs : le franchisé gère aussi le catalogue.
    assert "Vodka Absolut" in h.tg.last(F1).text and "prod_add" in [d for _, d in h.tg.last(F1).buttons]

    # Commande au format du modèle.
    await h.text(F1, "12 rue de Rivoli 75004 Paris\n2 abso 60\n1 cocas 10\n1 ricard 20\n\nDigicode 45A32, 3e étage")
    card = h.tg.last(F1)
    assert "🍾 2 Vodka Absolut (60 €) + 1 Coca-Cola (10 €) + 1 ricard (20 €)" in card.text
    assert "💶 90 €" in card.text
    assert "🔑 Digicode 45A32, 3e étage" in card.text
    assert "⚠️ Produit pas dans le catalogue : « ricard »" in card.text
    await h.press(F1, card, "draft_confirm:")
    [course] = await db.list_courses_by_status("pending")
    assert float(course["price"]) == 90 and course["address_detail"] == "Digicode 45A32, 3e étage"

    # Suppression depuis la liste.
    await h.text(DISPATCH, "/produits")
    listing = h.tg.last(DISPATCH)
    await h.press(DISPATCH, listing, "prod_del:")
    assert len(await db.list_products()) == 2
    franchise_h.set_extractor(None)


async def test_delivery_pushes_row_to_google_sheets(h, monkeypatch):
    import asyncio

    from bot.services import sheets

    monkeypatch.setenv("GOOGLE_SHEETS_WEBHOOK_URL", "https://script.google.com/macros/s/x/exec")
    monkeypatch.setenv("GOOGLE_SHEETS_SECRET", "s")
    sent = []

    async def fake_send(rows, client=None):
        sent.extend(rows)
        return len(rows)

    monkeypatch.setattr(sheets, "send_rows", fake_send)
    f1, f2, (l1,) = await setup_network(h, livreurs=(L1,))
    await go_on_duty(h, L1, BASTILLE)
    course = await order(h, F1, "rivoli")
    await h.press(L1, h.tg.last(L1), "course_take:")
    await h.press(L1, h.tg.find(L1, "c'est pour toi"), "course_deliver:")
    await asyncio.gather(*list(sheets._tasks))
    assert [(r["numero"], r["vendeur"], r["livreur"], r["statut"], r["prix"]) for r in sent] == [
        (course["id"], "Franchisé 1", "Livreur 1", "OK", 60.0)]
    assert sum(line["prix"] or 0 for line in sent[0]["lignes"]) == 60.0

    sent.clear()
    await h.text(DISPATCH, "/synchro")
    assert "Google Sheets à jour" in h.tg.last(DISPATCH).text and len(sent) == 1


async def test_livreur_modifies_order_with_buttons(h, monkeypatch):
    """Sur place, le livreur change les quantités, ajoute un produit du catalogue et
    règle les prix sans rien taper (sauf un prix, au choix)."""
    from bot.services import sheets

    monkeypatch.setenv("GOOGLE_SHEETS_WEBHOOK_URL", "https://script.google.com/macros/s/x/exec")
    monkeypatch.setenv("GOOGLE_SHEETS_SECRET", "s")
    sent = []

    async def fake_send(rows, client=None):
        sent.extend(rows)
        return len(rows)

    monkeypatch.setattr(sheets, "send_rows", fake_send)
    div = await db.create_product("DIV", "div", [])
    await db.create_product("KT", "kt", [])
    f1, f2, (l1,) = await setup_network(h, livreurs=(L1,))
    await go_on_duty(h, L1, BASTILLE)
    course = await order(h, F1, "rivoli")          # « 2 vodka + coca », 60 €
    prop = h.tg.last(L1)
    await h.press(L1, prop, "course_take:")
    card = h.tg.messages[(L1, prop.message_id)]
    assert "🍾 Produits :\n   • 2 vodka\n   • coca" in card.text

    def screen():
        return h.tg.messages[(L1, prop.message_id)]

    def txt(m):
        return m.text.replace("\xa0", " ")

    await h.press(L1, card, "order_edit:")
    assert "modifier la commande" in txt(screen())
    assert "1. 2 × vodka — 60 €" in txt(screen()) and "2. 1 × coca — ⚠️ prix ?" in txt(screen())

    # Valider sans rien changer : retour à la fiche.
    await h.press_data(L1, screen(), "oe_ok")
    assert h.tg.answers()[-1]["text"] == texts.ORDER_EDIT_UNCHANGED and "à encaisser" in txt(screen())
    await h.press(L1, screen(), "order_edit:")

    # Prix du coca : boutons de 10 en 10 €, puis 20 tapé au clavier (7 refusé).
    await h.press_data(L1, screen(), "oe_s:1")
    assert "◀️" in txt(screen())
    assert [b for b, _ in screen().buttons] == ["-50 €", "-20 €", "-10 €", "+10 €", "+20 €", "+50 €", "✅ OK"]
    await h.press_data(L1, screen(), "oe_p:1:10")
    assert "2. 1 × coca — 10 €" in txt(screen())
    await h.text(L1, "7")
    assert h.tg.last(L1).text == texts.ORDER_EDIT_PRICE_HINT
    await h.text(L1, "20")
    assert "2. 1 × coca — 20 €" in txt(screen())

    # Une vodka de plus : le prix suit (30 € l'unité).
    await h.press_data(L1, screen(), "oe_q:0:1")
    assert "1. 3 × vodka — 90 €" in txt(screen())

    # Ajout d'un produit du catalogue par le sélecteur, prix réglé par boutons.
    await h.press_data(L1, screen(), "oe_add:0")
    assert "choisis le produit" in txt(screen())
    await h.press_data(L1, screen(), f"oe_pick:{div['id']}")
    assert "3. 1 × DIV — ⚠️ prix ?  ◀️" in txt(screen())
    # Valider avec un produit sans prix : refusé.
    await h.press_data(L1, screen(), "oe_ok")
    assert h.tg.answers()[-1]["text"] == "Mets le prix de : DIV" and h.tg.answers()[-1]["show_alert"]
    await h.press_data(L1, screen(), "oe_p:2:10")
    await h.press_data(L1, screen(), "oe_p:2:10")
    await h.press_data(L1, screen(), "oe_s:-1")
    assert "💶 <b>Total : 130 €</b>" in txt(screen())

    await h.press_data(L1, screen(), "oe_ok")
    saved = await db.get_course(course["id"])
    assert saved["products"] == "3 vodka (90 €) + 1 coca (20 €) + 1 DIV (20 €)"
    assert float(saved["price"]) == 130
    assert "💶 130 € à encaisser" in txt(screen()) and "   • 1 DIV (20 €)" in txt(screen())
    assert "order_edit:" in [d.split(":")[0] + ":" for _, d in screen().buttons]
    assert f"✏️ Course #{course['id']} modifiée par Livreur 1 sur place" in txt(h.tg.find(F1, "modifiée par"))
    assert "(avant : 60 €)" in txt(h.tg.find(F1, "modifiée par"))
    assert "130 €" in txt(h.tg.find(F1, f"Course #{course['id']} — prise par"))
    assert f"✏️ #{course['id']} — modifiée par Livreur 1 — 60 € → 130 €" in txt(h.tg.find(DISPATCH, "modifiée par"))
    user = await db.get_user(l1["id"])
    assert user["conversation_state"] is None

    # Ouvrir puis annuler : rien ne change.
    await h.press(L1, screen(), "order_edit:")
    await h.press_data(L1, screen(), "oe_q:0:-1")
    await h.press_data(L1, screen(), "oe_x")
    assert float((await db.get_course(course["id"]))["price"]) == 130

    # Bouton d'un éditeur expiré : la fiche revient.
    await h.press(L1, screen(), "order_edit:")
    await db.clear_state(l1["id"])
    await h.press_data(L1, screen(), "oe_q:0:1")
    assert h.tg.answers()[-1]["text"] == texts.ORDER_EDIT_EXPIRED
    assert "à encaisser" in txt(screen())

    # Livraison pendant un réglage de prix : un nombre tapé ensuite ne touche plus la course.
    await h.press(L1, screen(), "order_edit:")
    await h.press_data(L1, screen(), "oe_s:0")
    await h.press_data(L1, card, f"course_deliver:{course['id']}")
    await h.text(L1, "5")
    assert h.tg.last(L1).text == texts.COURSE_FINISHED
    assert (await db.get_user(l1["id"]))["conversation_state"] is None
    assert float((await db.get_course(course["id"]))["price"]) == 130
    await asyncio.gather(*list(sheets._tasks))
    assert sent[0]["prix"] == 130.0
    assert sent[0]["lignes"] == [{"produit": "vodka", "qte": 3, "prix": 90.0},
                                 {"produit": "coca", "qte": 1, "prix": 20.0},
                                 {"produit": "DIV", "qte": 1, "prix": 20.0}]
    assert f"✅ #{course['id']} — livrée — Livreur 1 — 130 €" in [x.replace("\xa0", " ") for x in h.tg.texts(DISPATCH)]


async def test_franchise_order_price_must_be_multiple_of_ten(h):
    await h.text(DISPATCH, "/start")
    await register(h, F1, "franchise", "Bar du Coin")
    await h.text(F1, "pas rond")
    msg = h.tg.last(F1).text.replace("\xa0", " ")
    assert msg.startswith("⚠️ Les prix vont de 10 en 10 € : total 65 € — pas possible pour : 12 rue de Rivoli")
    assert not h.tg.last(F1).buttons  # aucune fiche à confirmer
    assert await db.list_courses_by_status("pending") == []
    # Le bon prix passe.
    await h.text(F1, "rivoli")
    assert "draft_confirm:" in h.tg.last(F1).buttons[0][1]


R1 = 4001


async def test_ravitailleur_restocks_livreur_with_buttons(h, monkeypatch):
    """Un ravitailleur charge un livreur par boutons ; le livreur et le dispatch sont
    prévenus ; la ligne part vers le tableau Rechargement ; /synchro la renvoie."""
    from bot.services import sheets

    monkeypatch.setenv("GOOGLE_SHEETS_WEBHOOK_URL", "https://script.google.com/macros/s/x/exec")
    monkeypatch.setenv("GOOGLE_SHEETS_SECRET", "s")
    sent = []

    async def fake_send(rows, client=None):
        sent.extend(rows)
        return len(rows)

    monkeypatch.setattr(sheets, "send_rows", fake_send)
    div = await db.create_product("DIV", "div", [])
    kt = await db.create_product("KT", "kt", [])
    f1, f2, (l1,) = await setup_network(h, livreurs=(L1,))
    r1 = await register(h, R1, "ravitailleur", "Sam")
    assert r1["display_name"] == "Ravitailleur 1" and r1["role"] == "ravitailleur"
    assert "/recharge" in h.tg.last(R1).text

    # Un livreur ou un franchisé n'y a pas accès.
    await h.text(L1, "/recharge")
    assert h.tg.last(L1).text == texts.NOT_FOR_YOU

    await h.text(R1, "/recharge")
    screen_id = h.tg.last(R1).message_id

    def screen():
        return h.tg.messages[(R1, screen_id)]

    def txt(m):
        return m.text.replace("\xa0", " ")

    assert screen().text == texts.RESTOCK_CHOOSE_LIVREUR
    await h.press(R1, screen(), "rs_l:")
    assert "Chargement, reprise ou cash" in txt(screen())
    await h.press_data(R1, screen(), "rs_k:load")
    assert "Quel box ?" in txt(screen())
    await h.press_data(R1, screen(), "rs_b:1")
    assert "📦 Chargement · Box 2" in txt(screen())

    # Valider vide : refusé.
    await h.press_data(R1, screen(), "rs_ok")
    assert h.tg.answers()[-1]["text"] == texts.RESTOCK_EMPTY

    await h.press_data(R1, screen(), "rs_add:0")
    await h.press_data(R1, screen(), f"rs_pick:{div['id']}")
    await h.press_data(R1, screen(), "rs_q:0:5")
    await h.press_data(R1, screen(), "rs_q:0:5")
    await h.press_data(R1, screen(), "rs_q:0:1")
    await h.press_data(R1, screen(), "rs_add:0")
    await h.press_data(R1, screen(), f"rs_pick:{kt['id']}")
    await h.press_data(R1, screen(), "rs_q:1:5")
    assert "+12 DIV" in txt(screen()) and "+6 KT" in txt(screen())

    # Cash : boutons, puis montant tapé.
    await h.press_data(R1, screen(), "rs_c")
    await h.press_data(R1, screen(), "rs_cp:100")
    assert "💶 Cash récupéré : 100 €" in txt(screen())
    await h.text(R1, "250")
    assert "💶 Cash récupéré : 250 €" in txt(screen())

    await h.press_data(R1, screen(), "rs_ok")
    [row] = await db.list_restocks_between(now_utc() - timedelta(hours=1), now_utc() + timedelta(minutes=1))
    assert (row["kind"], row["box"], row["items"], float(row["cash"])) == (
        "load", "Box 2", [{"p": "DIV", "q": 12}, {"p": "KT", "q": 6}], 250.0)
    assert row["livreur_id"] == l1["id"] and row["by_user_id"] == r1["id"]
    assert txt(screen()).startswith(f"✅ Rechargement #{row['id']} enregistré — Livreur 1")
    assert f"📦 Chargement reçu (#R{row['id']})\n+12 DIV, +6 KT" in txt(h.tg.last(L1))
    assert f"📦 R#{row['id']} — Ravitailleur 1 → Livreur 1 — 📦 Chargement · Box 2" in txt(h.tg.last(DISPATCH))
    assert (await db.get_user(r1["id"]))["conversation_state"] is None

    await asyncio.gather(*list(sheets._tasks))
    assert len(sent) == 1 and sent[0]["type"] == "recharge"
    assert sent[0]["produits"] == {"DIV": 12, "KT": 6} and sent[0]["cash"] == 250.0
    assert (sent[0]["livreur"], sent[0]["ravitailleur"], sent[0]["box"]) == ("Livreur 1", "Ravitailleur 1", "Box 2")

    # Reprise par le dispatch, puis cash seul.
    await h.text(DISPATCH, "/recharge")
    d_id = h.tg.last(DISPATCH).message_id

    def dscreen():
        return h.tg.messages[(DISPATCH, d_id)]

    await h.press(DISPATCH, dscreen(), "rs_l:")
    await h.press_data(DISPATCH, dscreen(), "rs_k:unload")
    await h.press_data(DISPATCH, dscreen(), "rs_b:0")
    await h.press_data(DISPATCH, dscreen(), f"rs_pick:{div['id']}")
    await h.press_data(DISPATCH, dscreen(), "rs_q:0:1")
    await h.press_data(DISPATCH, dscreen(), "rs_q:0:1")
    await h.press_data(DISPATCH, dscreen(), "rs_ok")
    assert "↩️ Stock repris" in h.tg.last(L1).text and "−3 DIV" in h.tg.last(L1).text

    await h.text(R1, "/recharge")
    c_id = h.tg.last(R1).message_id
    cmsg = h.tg.messages[(R1, c_id)]
    await h.press(R1, cmsg, "rs_l:")
    await h.press_data(R1, h.tg.messages[(R1, c_id)], "rs_k:cash")
    await h.text(R1, "300")
    await h.press_data(R1, h.tg.messages[(R1, c_id)], "rs_ok")
    assert "💶 Cash remis" in txt(h.tg.last(L1)) and "300 €" in txt(h.tg.last(L1))

    await asyncio.gather(*list(sheets._tasks))
    assert [s["produits"] for s in sent[1:]] == [{"DIV": -3}, {}]
    assert sent[2]["box"] == "" and sent[2]["cash"] == 300.0

    # Annuler ne crée rien ; /synchro renvoie les 3 rechargements de la nuit.
    await h.text(R1, "/recharge")
    x = h.tg.last(R1)
    await h.press_data(R1, x, "rs_x")
    assert h.tg.messages[(R1, x.message_id)].text == texts.RESTOCK_CANCELLED
    assert len(await db.list_restocks_between(now_utc() - timedelta(hours=1), now_utc() + timedelta(minutes=1))) == 3

    sent.clear()
    await h.text(DISPATCH, "/synchro")
    assert "3 rechargements" in h.tg.last(DISPATCH).text and len(sent) == 3

    await h.text(DISPATCH, "/users")
    assert "<b>Ravitailleurs</b>" in h.tg.last(DISPATCH).text and "Ravitailleur 1 — Sam" in h.tg.last(DISPATCH).text


async def test_franchise_has_full_admin_powers(h):
    """Le franchisé (comme le dispatch) : met un livreur en service sans position,
    attribue sa course, la modifie, la marque livrée, et a les commandes d'admin."""
    from bot import jobs

    f1, f2, (l1,) = await setup_network(h, livreurs=(L1,))

    def txt(m):
        return m.text.replace("\xa0", " ")

    # Un livreur n'a pas les commandes d'admin.
    await h.text(L1, "/livreurs")
    assert h.tg.last(L1).text == texts.NOT_FOR_YOU

    # /livreurs : Livreur 1 en pause ; le franchisé le met en service (sans position).
    await h.text(F1, "/livreurs")
    listing = h.tg.last(F1)
    assert "Livreur 1 — ⏸ pause" in listing.text
    await h.press(F1, listing, "duty_on:")
    assert "Livreur 1 — 🟢 en service · sans position" in h.tg.messages[(F1, listing.message_id)].text
    assert "t'a mis en service" in h.tg.last(L1).text
    assert (await db.get_user(l1["id"]))["on_duty"] is True
    # Pas de position : le contrôle des positions ne le met pas hors service.
    await jobs.stale_positions(h.context)
    assert (await db.get_user(l1["id"]))["on_duty"] is True

    # Une course lui est proposée, distance inconnue.
    course = await order(h, F1, "rivoli")
    prop = h.tg.last(L1)
    assert prop.text.startswith(f"🆕 Course #{course['id']}") and "de toi" not in prop.text

    # Le franchisé l'attribue lui-même, sans attendre « Je prends ».
    fmsg = h.tg.find(F1, f"Course #{course['id']}")
    assert [d.split(":")[0] for _, d in fmsg.buttons] == ["assign", "order_edit", "course_withdraw"]
    await h.press(F1, fmsg, "assign:")
    picker = h.tg.last(F1)
    assert "À quel livreur attribuer" in picker.text and "🟢 Livreur 1" in picker.buttons[0][0]
    await h.press(F1, picker, "assign_to:")
    assert h.tg.messages[(F1, picker.message_id)].text == f"✅ Course #{course['id']} attribuée à Livreur 1."
    assigned = await db.get_course(course["id"])
    assert assigned["status"] == "assigned" and assigned["livreur_id"] == l1["id"]
    fiche = h.tg.find(L1, "c'est pour toi")
    assert f"🚴 Course #{course['id']}" in fiche.text
    assert f"🚴 #{course['id']} — Franchisé 1 → Livreur 1" in txt(h.tg.find(DISPATCH, f"🚴 #{course['id']}"))

    # Le franchisé modifie sa course : une vodka de plus.
    fmsg = h.tg.find(F1, f"Course #{course['id']} — prise par")
    await h.press(F1, fmsg, "order_edit:")
    editor = h.tg.messages[(F1, fmsg.message_id)]
    assert "modifier la commande" in editor.text
    await h.press_data(F1, editor, "oe_q:0:1")
    # Le coca n'avait pas de prix (texte libre) : 10 €.
    await h.press_data(F1, h.tg.messages[(F1, fmsg.message_id)], "oe_s:1")
    await h.press_data(F1, h.tg.messages[(F1, fmsg.message_id)], "oe_p:1:10")
    await h.press_data(F1, h.tg.messages[(F1, fmsg.message_id)], "oe_s:-1")
    await h.press_data(F1, h.tg.messages[(F1, fmsg.message_id)], "oe_ok")
    modified = await db.get_course(course["id"])
    assert float(modified["price"]) == 100 and modified["products"].startswith("3 vodka")
    assert "modifiée par Franchisé 1" in txt(h.tg.find(L1, "modifiée par"))
    assert "100 € à encaisser" in txt(h.tg.messages[(L1, fiche.message_id)])
    assert "modifiée par Franchisé 1" in txt(h.tg.find(DISPATCH, "modifiée par"))
    back = h.tg.messages[(F1, fmsg.message_id)]
    assert "prise par Livreur 1" in back.text and "force_deliver" in back.buttons[0][1]

    # Il la marque livrée (avec confirmation).
    await h.press(F1, back, "force_deliver:")
    await h.press(F1, h.tg.last(F1), "fy:deliver:")
    assert (await db.get_course(course["id"]))["status"] == "delivered"

    # Commandes d'admin : récap, journal (chez lui), utilisateurs ; pas d'auto-exclusion.
    await h.text(F1, "/recap")
    assert "📊 Récap nuit du" in h.tg.last(F1).text and "Livreur 1 — 1 course — 100 €" in txt(h.tg.last(F1))
    await h.text(F1, "/journal")
    assert "📋 Journal nuit du" in h.tg.find(F1, "📋 Journal").text
    assert len([p for m, p in h.tg.calls if m == "sendDocument" and int(p["chat_id"]) == F1]) == 1
    await h.text(F1, "/exclure")
    assert "Franchisé 1" not in " ".join(b for b, _ in h.tg.last(F1).buttons)
    await h.press_data(F1, h.tg.last(F1), f"ban:{f1['id']}")
    assert h.tg.answers()[-1]["text"] == texts.CANNOT_BAN_SELF

    # Pause par un admin : le livreur est prévenu.
    await h.text(DISPATCH, "/livreurs")
    await h.press(DISPATCH, h.tg.last(DISPATCH), "duty_off:")
    lv = await db.get_user(l1["id"])
    assert lv["on_duty"] is False and lv["duty_forced"] is False
    assert "t'a mis en pause" in h.tg.last(L1).text
