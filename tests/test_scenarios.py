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
             "products": "1 jack daniels", "price": 45, "requested_time": "vers 23h"}
MONTREUIL = {"address": "12 rue de Paris, Montreuil", "address_detail": "code 1234B",
             "products": "2 rosé + glace", "price": 38, "requested_time": None}

EXTRACTIONS = {
    "rivoli": [RIVOLI],
    "oberkampf": [OBERKAMPF],
    "deux": [OBERKAMPF, MONTREUIL],
    "sans prix": [{**RIVOLI, "price": None}],
    "lyon": [{**RIVOLI, "address": "3 rue de la République, Lyon"}],
    "charenton": [{**RIVOLI, "address": "rue de Charenton, 75012 Paris"}],
    "introuvable": [{**RIVOLI, "address": "99 rue qui n'existe pas"}],
    "cher": [{**RIVOLI, "price": 2500}],
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
    assert h.tg.last(F1).text == texts.WELCOME_FRANCHISE
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
    assert [d.split(":")[0] for _, d in fmsg.buttons] == ["relay_start", "course_withdraw"]
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
    assert "Livreur 1 — 2 courses — 105 €" in recap
    assert "Total : 2 courses — 105 €" in recap
    assert "En attente : 1 · En cours : 0" in recap

    await h.text(DISPATCH, "/journal")
    journal = h.tg.find(DISPATCH, "📋 Journal nuit du").text
    assert "2 courses livrées" in journal
    assert f"#{c1['id']} · Franchisé 1 → Livreur 1 · 12 Rue de Rivoli 75004 Paris · 60 €" in journal
    assert "Total : 105 €" in journal
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
    assert "⏳ En attente (1)" in enc.text and f"#{c2['id']} — Franchisé 2 — Paris 11e — 45 € — vague 1" in enc.text
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
