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
    from bot.services import arrival

    arrival._notified.clear()
    from bot.services import stock as stock_service

    stock_service._inflight.clear()
    stock_service._sheet_cache = None
    from bot.services import transport as transport_service

    transport_service._stats.clear()
    from bot import jobs as jobs_module

    jobs_module._silence_alerted.clear()
    stock_service._dispatch_alerted.clear()
    from bot.handlers import cloture as cloture_h

    cloture_h._last_done = None
    from bot.services import names as names_service

    names_service.set_cache({})
    from bot.services import sheets

    async def no_script(action, client=None, **kw):   # jamais d'appel réseau : chaque test simule le script
        return {"ok": False, "error": "script non simulé"}

    monkeypatch.setattr(sheets, "fetch_action", no_script)

    async def fake_reverse(lat, lon, client=None):   # adresse la plus proche d'un point, sans réseau
        return "12 Rue de Rivoli 75004 Paris"

    monkeypatch.setattr(geocoding, "reverse", fake_reverse)
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
    assert h.tg.last(DISPATCH).text.startswith("Dispatch actif — tous les droits.")
    await h.text(F1, "/start", username="barducoin")
    assert h.tg.last(F1).text == texts.ASK_ROLE
    # Le dispatch sait qu'une nouvelle personne a ouvert le bot (une fois par heure).
    assert sum("👋 Nouvelle personne sur le bot" in t and "@barducoin" in t for t in h.tg.texts(DISPATCH)) == 1
    await h.text(F1, "/start", username="barducoin")
    assert sum("👋 Nouvelle personne sur le bot" in t for t in h.tg.texts(DISPATCH)) == 1
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
    await h.text(DISPATCH, "/bannir")
    await h.press(DISPATCH, h.tg.last(DISPATCH), f"ban:{l2['id']}")
    assert "Bannir Livreur 2 (Livreur-3002) ?" in h.tg.last(DISPATCH).text
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

async def deliver_course(h, tg_id, msg, pay="e"):
    """📦 Livré puis choix du paiement (e : espèces, v : virement)."""
    course_id = msg.data("course_deliver:").split(":")[1]
    await h.press(tg_id, msg, "course_deliver:")
    await h.press_data(tg_id, h.tg.messages[(tg_id, msg.message_id)], f"pay:{course_id}:{pay}")


async def go_on_duty(h, tg_id, pos):
    await h.text(tg_id, "/dispo")
    await h.location(tg_id, *pos, message_id=tg_id)


async def test_dispo_broadcast_take_and_deliver(h):
    f1, f2, (l1, l2) = await setup_network(h)
    await h.text(L1, "/dispo")
    assert h.tg.texts(L1)[-2:] == [texts.DISPO_PROMPT, texts.TRANSPORT_QUESTION]   # position, puis transport
    await h.location(L1, *BASTILLE, message_id=L1)
    assert h.tg.last(L1).text == texts.ON_DUTY
    await go_on_duty(h, L2, REPUBLIQUE)

    course = await order(h, F1, "rivoli")
    prop1, prop2 = h.tg.last(L1), h.tg.last(L2)
    for p in (prop1, prop2):
        assert p.text.endswith(f"\nCourse #{course['id']}")
        assert "· Paris 4e · " in p.text and "📍 à ~" in p.text
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
    assert [d.split(":")[0] for _, d in fmsg.buttons] == ["force_deliver", "order_edit", "addr", "ct", "assign",
                                                          "relay_start", "course_withdraw"]
    assert f"🚴 <b>Course #{course['id']} prise</b> par Livreur 1\nPour Franchisé 1 · 📍 12 Rue de Rivoli 75004 Paris · 60 €" in [
        x.replace("\xa0", " ") for x in h.tg.texts(DISPATCH)]
    # Le franchisé reçoit un vrai message (une fiche modifiée ne sonne pas sur le téléphone).
    assert any(t.startswith(f"🚴 <b>Ta course #{course['id']} est prise</b> par Livreur 1 — il est en route.")
               for t in h.tg.texts(F1))

    # L2 appuie trop tard.
    await h.press_data(L2, prop2, f"course_take:{course['id']}")
    assert h.tg.answers()[-1]["text"] == texts.TOO_LATE

    await h.text(L1, "/macourse")
    assert h.tg.last(L1).text.startswith(f"🚴 Course #{course['id']}")

    await deliver_course(h, L1, fiche)
    delivered = await db.get_course(course["id"])
    assert delivered["status"] == "delivered" and delivered["delivered_distance_m"] is not None
    assert "✅ Course" in h.tg.last(L1).text or "livrée" in h.tg.messages[(L1, fiche.message_id)].text
    assert "— livrée à" in h.tg.find(F1, f"Course #{course['id']} — livrée").text
    assert any(t.startswith(f"✅ <b>Ta course #{course['id']} est livrée</b> par Livreur 1.") for t in h.tg.texts(F1))
    assert f"✅ <b>Course #{course['id']} livrée</b> par Livreur 1\n📍 12 Rue de Rivoli 75004 Paris · 60 € · 💵 espèces" in [
        x.replace("\xa0", " ") for x in h.tg.texts(DISPATCH)]
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
    assert h.tg.last(L1).text.endswith(f"\nCourse #{course['id']}")
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
    first = h.tg.find(L1, f"\nCourse #{course['id']}")
    await h.press(L1, first, "course_take:")
    assert (await db.get_course(course["id"]))["livreur_id"] == ls[0]["id"]


async def test_livreur_chains_several_courses(h, test_config):
    """Un livreur qui a déjà une adresse reçoit et prend les suivantes lui-même (sans limite par défaut) ;
    MAX_COURSES_PER_LIVREUR fixe un maximum."""
    import dataclasses

    from bot import config

    f1, f2, (l1,) = await setup_network(h, livreurs=(L1,))
    await go_on_duty(h, L1, BASTILLE)
    c1 = await order(h, F1, "rivoli")
    await h.press(L1, h.tg.last(L1), "course_take:")
    # Il s'approche de l'adresse : « bientôt libre » automatique (retombe à la livraison).
    await h.location(L1, 48.8557, 2.3578, edited=True, message_id=L1)
    assert (await db.get_user(l1["id"]))["soon_free"] is True
    c2 = await order(h, F2, "oberkampf")
    await h.press(L1, h.tg.find(L1, f"\nCourse #{c2['id']}"), "course_take:")
    c3 = await order(h, F1, "rivoli")
    await h.press(L1, h.tg.find(L1, f"\nCourse #{c3['id']}"), "course_take:")
    assert len(await db.list_assigned_for_livreur(l1["id"])) == 3
    await deliver_course(h, L1, h.tg.find(L1, f"🚴 Course #{c1['id']}"))
    assert (await db.get_user(l1["id"]))["soon_free"] is False

    # Avec un maximum de 2 : plus de proposition, et « Je prends » est refusé.
    config.set_config(dataclasses.replace(test_config, max_courses_per_livreur=2))
    c4 = await order(h, F2, "oberkampf")
    assert not any(f"\nCourse #{c4['id']}" in t for t in h.tg.texts(L1))
    await h.press_data(L1, h.tg.last(L1), f"course_take:{c4['id']}")
    assert h.tg.answers()[-1].get("text") == texts.ALREADY_HAS_COURSE
    assert (await db.get_course(c4["id"]))["status"] == "pending"


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
    assert h.tg.last(L2).text.endswith(f"\nCourse #{course['id']}")
    assert "Livreur 1 a annulé la course" in h.tg.find(F1, "a annulé la course").text
    assert f"⚠️ #{course['id']} — annulée par Livreur 1 (1re annulation) — remise en diffusion" in h.tg.texts(DISPATCH)
    # Le livreur qui a annulé ne la reçoit plus.
    await broadcast.run_wave(h.context, course["id"], advance=True)
    assert sum(t.endswith(f"\nCourse #{course['id']}") for t in h.tg.all_texts_ever(L1)) == 1


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
    assert h.tg.last(L1).text.endswith(f"\nCourse #{course['id']}")


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
    # Le dispatch (grand admin) voit toutes les conversations.
    assert f"💬 #{course['id']} — Livreur 1 → Franchisé 1 :\n« Le digicode ne passe pas »" in h.tg.texts(DISPATCH)

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
    await deliver_course(h, L1, fiche)
    await h.press_data(F1, relayed, f"relay_start:{course['id']}")
    assert h.tg.last(F1).text == texts.COURSE_FINISHED
    assert len(await db._t("messages").select("*").execute().__await__().__next__() if False else
               (await db._t("messages").select("*").eq("course_id", course["id"]).execute()).data) == 3


async def test_recap_journal_csv(h):
    f1, f2, (l1, l2) = await setup_network(h)
    await go_on_duty(h, L1, BASTILLE)
    c1 = await order(h, F1, "rivoli")
    await h.press(L1, h.tg.last(L1), "course_take:")
    await deliver_course(h, L1, h.tg.find(L1, "c'est pour toi"))
    c2 = await order(h, F2, "oberkampf")
    await h.press(L1, h.tg.last(L1), "course_take:")
    await deliver_course(h, L1, h.tg.find(L1, f"🚴 Course #{c2['id']}"))
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
                       "complement", "produits", "prix", "paiement"]
    assert rows[1][0] == str(c1["id"]) and rows[1][6] == "digicode 45A32" and rows[1][8] == "60,00"
    assert rows[1][9] == "Espèces"

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
    assert "⏳ En attente (1)" in enc.text and f"#{c2['id']} — Paris 11e — {c2['products']} — 50 € — vague 1" in enc.text
    assert f"#{c1['id']} — Livreur 1 — Paris 4e — {c1['products']} — 60 € — depuis 0 min" in enc.text
    assert "Franchisé" not in enc.text.split("Livrées")[0]          # les produits, pas le nom des franchisés
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
    await h.press(L2, h.tg.find(L2, f"\nCourse #{c1['id']}"), "course_take:")
    await h.press_data(DISPATCH, enc, f"force_deliver:{c1['id']}")
    await h.press(DISPATCH, h.tg.last(DISPATCH), "fy:deliver:")
    assert (await db.get_course(c1["id"]))["status"] == "delivered"
    overrides = (await db._t("events").select("*").eq("type", "dispatch_override").execute()).data
    assert len(overrides) == 3

    # Combien de courses livrées cette nuit : dans /encours, avec le montant et le détail par livreur.
    await h.text(DISPATCH, "/encours")
    enc = h.tg.last(DISPATCH).text.replace("\xa0", " ")
    assert enc.startswith("🚦 <b>En ce moment</b> — nuit du")
    assert "✅ <b>Livrées (1)</b> — 60 € (💵 60 €)\n• Livreur 2 : 1 — 60 €" in enc and "❌ Annulées : 1" in enc


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
    assert h.tg.last(L2).text.endswith(f"\nCourse #{course['id']}")


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
    await deliver_course(h, L1, h.tg.find(L1, "c'est pour toi"))
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
    await deliver_course(h, L1, h.tg.find(L1, "c'est pour toi"))
    await asyncio.gather(*list(sheets._tasks))
    assert [(r["numero"], r["vendeur"], r["livreur"], r["statut"], r["prix"]) for r in sent] == [
        (course["id"], "Franchisé 1", "Livreur 1", "OK", 60.0)]
    assert sum(line["prix"] or 0 for line in sent[0]["lignes"]) == 60.0

    sent.clear()
    await h.text(DISPATCH, "/synchro")
    assert "Google Sheets à jour" in h.tg.last(DISPATCH).text and len(sent) == 1

    # Nuit passée de la semaine : /synchro hier, ou le bouton du jour sous le résultat.
    await db.update_course(course["id"], {"delivered_at": iso(now_utc() - timedelta(days=1))})
    sent.clear()
    await h.text(DISPATCH, "/synchro")
    assert "Aucune course livrée" in h.tg.last(DISPATCH).text and not sent
    yesterday = h.tg.last(DISPATCH).buttons[0][1]
    assert yesterday.startswith("sy:")
    await h.press_data(DISPATCH, h.tg.last(DISPATCH), yesterday)
    assert "de la nuit du" in h.tg.last(DISPATCH).text and len(sent) == 1
    sent.clear()
    await h.text(DISPATCH, "/synchro hier")
    assert len(sent) == 1
    await h.text(DISPATCH, "/synchro n'importe quoi")
    assert h.tg.last(DISPATCH).text == texts.SYNCHRO_HELP
    # Après un /reset, la semaine d'avant n'est plus renvoyable (elle écraserait l'onglet du même jour).
    await db.log_event("cloture")
    sent.clear()
    await h.text(DISPATCH, "/synchro hier")
    assert "pas dans la semaine en cours" in h.tg.last(DISPATCH).text and not sent


async def test_payment_mode_at_delivery(h, monkeypatch):
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
    fiche = h.tg.find(L1, "c'est pour toi")

    # 📦 Livré → choix du paiement ; ↩️ Retour rend la fiche intacte.
    await h.press(L1, fiche, "course_deliver:")
    assert h.tg.answers()[-1]["text"] == texts.PAYMENT_PROMPT
    ask = h.tg.messages[(L1, fiche.message_id)]
    assert [d for _, d in ask.buttons] == [f"pay:{course['id']}:e", f"pay:{course['id']}:v",
                                           f"pay_back:{course['id']}"]
    assert (await db.get_course(course["id"]))["status"] == "assigned"
    await h.press_data(L1, ask, f"pay_back:{course['id']}")
    assert any(d.startswith("course_deliver:") for _, d in h.tg.messages[(L1, fiche.message_id)].buttons)

    await deliver_course(h, L1, h.tg.messages[(L1, fiche.message_id)], pay="v")
    delivered = await db.get_course(course["id"])
    assert delivered["status"] == "delivered" and delivered["payment"] == "virement"
    assert "💳 virement" in h.tg.find(DISPATCH, f"✅ <b>Course #{course['id']} livrée").text
    await asyncio.gather(*list(sheets._tasks))
    assert [r["paiement"] for r in sent] == ["Virement"]

    # L'admin marque une course livrée en choisissant le mode de paiement.
    c2 = await order(h, F1, "rivoli")
    await h.press(L1, h.tg.find(L1, f"\nCourse #{c2['id']}"), "course_take:")
    await h.press_data(DISPATCH, h.tg.last(DISPATCH), f"force_deliver:{c2['id']}")
    confirm = h.tg.last(DISPATCH)
    assert f"fy:deliver:{c2['id']}:e" in [d for _, d in confirm.buttons]
    await h.press_data(DISPATCH, confirm, f"fy:deliver:{c2['id']}:v")
    assert (await db.get_course(c2["id"]))["payment"] == "virement"


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

    # Une vodka de plus : le prix ne bouge pas tout seul, le livreur le met lui-même (90 €).
    await h.press_data(L1, screen(), "oe_q:0:1")
    assert "1. 3 × vodka — 60 €" in txt(screen())
    await h.press_data(L1, screen(), "oe_s:0")
    await h.press_data(L1, screen(), "oe_p:0:20")
    await h.press_data(L1, screen(), "oe_p:0:10")
    await h.press_data(L1, screen(), "oe_s:-1")
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

    # Le franchisé a le dernier mot : la commande ne change qu'à sa validation.
    await h.press_data(L1, screen(), "oe_ok")
    saved = await db.get_course(course["id"])
    assert float(saved["price"]) == 60 and saved["pending_edit"]["price"] == 130
    assert "💶 60 € à encaisser" in txt(screen())                 # fiche inchangée en attendant
    assert h.tg.last(L1).text == texts.edit_sent(course["id"], "Franchisé 1")
    request = h.tg.find(F1, "a modifié la course")
    assert "Avant : 2 vodka + coca — 60 €" in txt(request)
    assert "Après : <b>3 vodka (90 €) + 1 coca (20 €) + 1 DIV (20 €) — 130 €</b>" in txt(request)
    assert [d for _, d in request.buttons] == [f"me:{course['id']}:ok", f"me:{course['id']}:no"]
    assert "En attente de la validation du franchisé." in txt(h.tg.find(DISPATCH, "a modifié la course"))
    await h.press_data(F1, request, f"me:{course['id']}:ok")
    saved = await db.get_course(course["id"])
    assert saved["products"] == "3 vodka (90 €) + 1 coca (20 €) + 1 DIV (20 €)"
    assert float(saved["price"]) == 130 and saved["pending_edit"] is None
    assert "validée par Franchisé 1" in txt(h.tg.messages[(F1, request.message_id)])
    assert "💶 130 € à encaisser" in txt(screen()) and "   • 1 DIV (20 €)" in txt(screen())
    assert "order_edit:" in [d.split(":")[0] + ":" for _, d in screen().buttons]
    assert "130 €" in txt(h.tg.find(F1, f"Course #{course['id']} — prise par"))
    await h.press_data(F1, request, f"me:{course['id']}:ok")         # déjà traitée
    assert h.tg.answers()[-1]["text"] == texts.EDIT_ALREADY_DECIDED
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
    await h.press_data(L1, card, f"pay:{course['id']}:e")
    await h.text(L1, "5")
    assert h.tg.last(L1).text == texts.COURSE_FINISHED
    assert (await db.get_user(l1["id"]))["conversation_state"] is None
    assert float((await db.get_course(course["id"]))["price"]) == 130
    await asyncio.gather(*list(sheets._tasks))
    assert sent[0]["prix"] == 130.0
    assert sent[0]["lignes"] == [{"produit": "vodka", "qte": 3, "prix": 90.0},
                                 {"produit": "coca", "qte": 1, "prix": 20.0},
                                 {"produit": "DIV", "qte": 1, "prix": 20.0}]
    assert any(t.startswith(f"✅ <b>Course #{course['id']} livrée</b> par Livreur 1") and "130 € · 💵 espèces" in t
               for t in [x.replace("\xa0", " ") for x in h.tg.texts(DISPATCH)])


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
    assert prop.text.endswith(f"\nCourse #{course['id']}") and "de toi" not in prop.text

    # Le franchisé l'attribue lui-même, sans attendre « Je prends ».
    fmsg = h.tg.find(F1, f"Course #{course['id']}")
    assert [d.split(":")[0] for _, d in fmsg.buttons] == ["assign", "order_edit", "addr", "ct", "course_withdraw"]
    await h.press(F1, fmsg, "assign:")
    picker = h.tg.last(F1)
    assert "À quel livreur attribuer" in picker.text and "🟢 Livreur 1" in picker.buttons[0][0]
    await h.press(F1, picker, "assign_to:")
    assert h.tg.messages[(F1, picker.message_id)].text == f"✅ Course #{course['id']} attribuée à Livreur 1."
    assigned = await db.get_course(course["id"])
    assert assigned["status"] == "assigned" and assigned["livreur_id"] == l1["id"]
    fiche = h.tg.find(L1, "c'est pour toi")
    assert f"🚴 Course #{course['id']}" in fiche.text
    assert f"Course #{course['id']} prise</b> par Livreur 1" in txt(h.tg.find(DISPATCH, f"🚴 <b>Course #{course['id']} prise"))

    # Le franchisé modifie sa course : une vodka de plus.
    fmsg = h.tg.find(F1, f"Course #{course['id']} — prise par")
    await h.press(F1, fmsg, "order_edit:")
    editor = h.tg.messages[(F1, fmsg.message_id)]
    assert "modifier la commande" in editor.text
    await h.press_data(F1, editor, "oe_q:0:1")
    # Prix libre : il met lui-même 90 € pour les 3 vodkas (60 → 90).
    await h.press_data(F1, h.tg.messages[(F1, fmsg.message_id)], "oe_s:0")
    await h.press_data(F1, h.tg.messages[(F1, fmsg.message_id)], "oe_p:0:20")
    await h.press_data(F1, h.tg.messages[(F1, fmsg.message_id)], "oe_p:0:10")
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
    # Accès des personnes et remise à zéro : réservés au dispatch.
    for cmd in ("/bannir", "/reactiver", "/supprimer", "/prenom", "/synchro", "/reset"):
        await h.text(F1, cmd)
        assert h.tg.last(F1).text == texts.DISPATCH_ONLY, cmd
    n = len(h.tg.inbox(F1))
    await h.press_data(F1, h.tg.last(F1), f"ban:{l1['id']}")
    assert len(h.tg.inbox(F1)) == n and (await db.get_user(l1["id"]))["status"] == "active"
    await h.text(F1, "/users")
    assert not any(d.startswith(("approve:", "reject:")) for _, d in h.tg.last(F1).buttons)
    await h.press_data(F1, h.tg.last(F1), f"uc:{l1['id']}")
    card = h.tg.last(F1)
    assert [d.split(":")[0] for _, d in card.buttons] == ["uc_duty", "loc", "uc_list"]
    await h.press_data(F1, card, f"uc_role:{l1['id']}")
    assert h.tg.answers()[-1].get("text") == texts.DISPATCH_ONLY

    # Pause par un admin : le livreur est prévenu.
    await h.text(DISPATCH, "/livreurs")
    await h.press(DISPATCH, h.tg.last(DISPATCH), "duty_off:")
    lv = await db.get_user(l1["id"])
    assert lv["on_duty"] is False and lv["duty_forced"] is False
    assert "t'a mis en pause" in h.tg.last(L1).text



async def test_franchise_and_dispatch_notified_when_livreur_arrives(h):
    """Le livreur approche : franchisé et dispatch prévenus une seule fois par course."""
    f1, f2, (l1,) = await setup_network(h, livreurs=(L1,))
    far = (48.8700, 2.3300)                     # ~2,3 km de Rivoli : ≈ 10 min à 15 km/h
    await go_on_duty(h, L1, far)
    course = await order(h, F1, "rivoli")
    await h.press(L1, h.tg.last(L1), "course_take:")
    n_f1 = len(h.tg.inbox(F1))
    await h.location(L1, *far, edited=True, message_id=L1)
    assert len(h.tg.inbox(F1)) == n_f1          # encore loin : rien

    near = (48.8560, 2.3600)                    # ~200 m de l'adresse
    await h.location(L1, *near, edited=True, message_id=L1)
    alert = h.tg.last(F1).text.replace("\xa0", " ")
    assert alert.startswith(f"📍 Livreur 1 arrive — course #{course['id']} : à ~200 m")
    assert "12 Rue de Rivoli" in alert
    assert any(t.startswith(f"📍 #{course['id']} — Livreur 1 arrive") for t in h.tg.texts(DISPATCH))
    [event] = await db.list_events(course["id"], "arrival_notified")
    assert event["payload"]["distance_m"] < 500

    # Positions suivantes : pas de nouvelle alerte.
    await h.location(L1, 48.8557, 2.3585, edited=True, message_id=L1)
    await h.location(L1, 48.8556, 2.3578, edited=True, message_id=L1)
    assert sum(1 for t in h.tg.texts(F1) if t.startswith("📍 Livreur 1 arrive")) == 1
    assert len(await db.list_events(course["id"], "arrival_notified")) == 1

    # À l'adresse (moins de 80 m) : « sur place », une seule fois aussi.
    c = await db.get_course(course["id"])
    await h.location(L1, c["lat"] + 0.0003, c["lon"], edited=True, message_id=L1)     # ~33 m
    await h.location(L1, c["lat"], c["lon"], edited=True, message_id=L1)
    onsite = [t for t in h.tg.texts(F1) if t.startswith("🏁 Livreur 1 est sur place")]
    assert len(onsite) == 1 and f"course #{course['id']}" in onsite[0] and "12 Rue de Rivoli" in onsite[0]
    assert sum(1 for t in h.tg.texts(DISPATCH) if t.startswith(f"🏁 #{course['id']} — Livreur 1 est <b>sur place</b>")) == 1
    assert len(await db.list_events(course["id"], "onsite_notified")) == 1


async def test_onsite_without_approach_alert(h):
    """Première position déjà à l'adresse : seulement « sur place », pas de « il arrive » ensuite."""
    f1, f2, (l1,) = await setup_network(h, livreurs=(L1,))
    far = (48.8700, 2.3300)
    await go_on_duty(h, L1, far)
    course = await order(h, F1, "rivoli")
    await h.press(L1, h.tg.last(L1), "course_take:")
    c = await db.get_course(course["id"])
    await h.location(L1, c["lat"], c["lon"], edited=True, message_id=L1)
    await h.location(L1, c["lat"] + 0.0002, c["lon"], edited=True, message_id=L1)
    assert any(t.startswith("🏁 Livreur 1 est sur place") for t in h.tg.texts(F1))
    assert not any(t.startswith("📍 Livreur 1 arrive") for t in h.tg.texts(F1))


async def test_stock_alerts_last_units_and_empty(h, monkeypatch, test_config):
    """Commande de 2 US au livreur qui en a 2 : alerte « les 2 derniers » ; une fois livrée :
    « n'a plus de US sur lui ». Stock lu dans le tableau Rechargement (simulé)."""
    import dataclasses

    from bot import config
    from bot.services import sheets

    config.set_config(dataclasses.replace(test_config, stock_alerts=True))
    monkeypatch.setenv("GOOGLE_SHEETS_WEBHOOK_URL", "https://script.google.com/macros/s/x/exec")
    monkeypatch.setenv("GOOGLE_SHEETS_SECRET", "s")
    order_of_calls = []

    async def fake_stock(livreur, client=None):
        order_of_calls.append("stock")
        return {"US": 2, "DIV": 7}, "Livreur A"

    async def fake_send(rows, client=None):
        order_of_calls.append("vente")
        return len(rows)

    monkeypatch.setattr(sheets, "fetch_stock", fake_stock)
    monkeypatch.setattr(sheets, "send_rows", fake_send)
    await db.create_product("US", "us", [])
    EXTRACTIONS["commande us"] = [{**RIVOLI, "products": "2 US", "price": 60}]
    f1, f2, (l1,) = await setup_network(h, livreurs=(L1,))
    r1 = await register(h, R1, "ravitailleur", "Sam")
    await go_on_duty(h, L1, BASTILLE)
    order_of_calls.clear()               # la question du stock de début de service lit aussi le tableau
    course = await order(h, F1, "commande us")
    await h.press(L1, h.tg.last(L1), "course_take:")
    await asyncio.gather(*list(sheets._tasks))

    last = f"• Ce sont les 2 derniers US de Livreur 1 (Livreur A)"
    for who in (F1, L1, R1, DISPATCH):
        assert last in h.tg.find(who, "⚠️ Stock").text, who
    assert h.tg.find(F1, "⚠️ Stock").text.startswith(f"⚠️ Stock — course #{course['id']}")

    await deliver_course(h, L1, h.tg.find(L1, "c'est pour toi"))
    await asyncio.gather(*list(sheets._tasks))
    empty = f"📭 Livreur 1 (Livreur A) n'a plus de US sur lui (course #{course['id']} livrée)."
    for who in (F1, L1, R1, DISPATCH):
        assert h.tg.find(who, "📭").text.startswith(empty), who
    # Stock lu avant l'envoi de la vente (sinon la vente pourrait déjà y être comptée).
    assert order_of_calls == ["stock", "stock", "vente"]
    assert [e["type"] for e in await db.list_events(course["id"], "stock_warning")] == ["stock_warning"]
    assert len(await db.list_events(course["id"], "stock_empty")) == 1
    del r1



async def test_stock_computed_in_parallel_of_the_sheet(h, monkeypatch, test_config):
    """La feuille a du retard : le bot compte lui-même les courses déjà attribuées et les ventes
    pas encore écrites (envoi en échec), pour ne jamais rater une alerte."""
    import dataclasses

    from bot import config
    from bot.services import sheets

    config.set_config(dataclasses.replace(test_config, stock_alerts=True))
    monkeypatch.setenv("GOOGLE_SHEETS_WEBHOOK_URL", "https://script.google.com/macros/s/x/exec")
    monkeypatch.setenv("GOOGLE_SHEETS_SECRET", "s")

    async def sheet_stock(livreur, client=None):
        return {"US": 3}, "Livreur A"            # la feuille ne bouge pas (retard)

    async def sheet_down(rows, client=None):
        raise RuntimeError("feuille injoignable")

    monkeypatch.setattr(sheets, "fetch_stock", sheet_stock)
    monkeypatch.setattr(sheets, "send_rows", sheet_down)
    await db.create_product("US", "us", [])
    EXTRACTIONS["deux us"] = [{**RIVOLI, "products": "2 US", "price": 60}]
    EXTRACTIONS["un us"] = [{**OBERKAMPF, "products": "1 US", "price": 30}]
    f1, f2, (l1,) = await setup_network(h, livreurs=(L1,))
    await go_on_duty(h, L1, BASTILLE)

    c1 = await order(h, F1, "deux us")
    await h.press(L1, h.tg.last(L1), "course_take:")
    await asyncio.gather(*list(sheets._tasks))
    assert not [t for t in h.tg.texts(F1) if t.startswith("⚠️ Stock")]      # 3 US, 2 commandés : rien

    # Deuxième course attribuée par un admin au même livreur : 3 − 2 déjà promis = 1 → le dernier.
    c2 = await order(h, F2, "un us")
    await h.press(F2, h.tg.find(F2, f"Course #{c2['id']}"), "assign:")
    await h.press(F2, h.tg.last(F2), "assign_to:")
    await asyncio.gather(*list(sheets._tasks))
    assert f"• C'est le dernier US de Livreur 1 (Livreur A)" in h.tg.find(F2, "⚠️ Stock").text

    # Livraison de la 1re : la feuille est injoignable, la vente reste comptée par le bot.
    await deliver_course(h, L1, h.tg.find(L1, f"🚴 Course #{c1['id']}"))
    await asyncio.gather(*list(sheets._tasks))
    assert not [t for t in h.tg.texts(F1) if t.startswith("📭")]            # 3 − 2 = 1 : il en reste
    # Livraison de la 2e : feuille toujours à 3, mais le bot sait qu'il n'en reste qu'1 → épuisé.
    await deliver_course(h, L1, h.tg.find(L1, f"🚴 Course #{c2['id']}"))
    await asyncio.gather(*list(sheets._tasks))
    assert h.tg.find(F2, "📭").text.startswith("📭 Livreur 1 (Livreur A) n'a plus de US sur lui")


async def test_livreur_gets_sheet_name_chosen_by_admin(h, monkeypatch, test_config):
    """À la validation, un livreur reçoit le premier nom libre de la feuille (« Livreur A ») ;
    l'admin peut en choisir un autre ; un nom n'est porté que par un seul livreur ; c'est ce nom
    qui part dans la feuille Dispatch."""
    import dataclasses

    from bot import config
    from bot.services import sheets

    config.set_config(dataclasses.replace(test_config, livreur_names=("Livreur A", "Livreur B", "Livreur X"),
                                          ravitailleur_names=("Ravitailleur 1", "Ravitailleur 2")))
    await h.text(DISPATCH, "/start")
    await register(h, F1, "franchise", "Bar du Coin")
    l1 = await register(h, L1, "livreur", "Karim")
    assert l1["display_name"] == "Livreur A"
    picker = h.tg.last(DISPATCH)
    assert picker.text.startswith("🏷 Nom dans les feuilles pour Karim")
    assert [b for b, _ in picker.buttons] == ["✅ Livreur A", "Livreur B", "Livreur X", "OK"]

    # L'admin choisit « Livreur X ».
    await h.press_data(DISPATCH, picker, f"sname_set:{l1['id']}:2")
    assert (await db.get_user(l1["id"]))["display_name"] == "Livreur X"
    assert h.tg.last(L1).text == "🏷 Ton nom est maintenant « Livreur X »."
    assert "Livreur A → <b>Livreur X</b> (Karim)" in h.tg.messages[(DISPATCH, picker.message_id)].text

    # Deuxième livreur : premier nom libre = Livreur A ; « Livreur X » est pris.
    l2 = await register(h, L2, "livreur", "Nassim")
    assert l2["display_name"] == "Livreur A"
    picker2 = h.tg.last(DISPATCH)
    assert [b for b, _ in picker2.buttons][:3] == ["✅ Livreur A", "Livreur B", "🔒 Livreur X"]
    await h.press_data(DISPATCH, picker2, f"sname_set:{l2['id']}:2")
    assert h.tg.answers()[-1]["text"] == "« Livreur X » est déjà pris par Karim." and h.tg.answers()[-1]["show_alert"]
    assert (await db.get_user(l2["id"]))["display_name"] == "Livreur A"
    await h.press_data(DISPATCH, picker2, f"sname_done:{l2['id']}")
    assert h.tg.messages[(DISPATCH, picker2.message_id)].text == "🏷 Livreur A (Nassim)"

    # Ravitailleur : même principe.
    r1 = await register(h, R1, "ravitailleur", "Sam")
    assert r1["display_name"] == "Ravitailleur 1"

    # Changer plus tard depuis /livreurs.
    await h.text(DISPATCH, "/livreurs")
    listing = h.tg.last(DISPATCH)
    assert "🏷 Nom" in [b for b, _ in listing.buttons]
    await h.press_data(DISPATCH, listing, f"sname:{l2['id']}")
    assert h.tg.last(DISPATCH).text.startswith("🏷 Nom dans les feuilles pour Nassim")

    # Le nom écrit dans la feuille est celui du bot.
    course = {"id": 9, "delivered_at": "2026-09-28T21:00:00+00:00", "franchise_id": (await db.get_user_by_tg(F1))["id"],
              "livreur_id": l1["id"], "address": "A", "products": "1 DIV", "price": 30}
    users = await db.get_users([course["franchise_id"], l1["id"]])
    assert sheets.row(course, users)["livreur"] == "Livreur X"


async def test_stock_command_boxes_and_livreurs(h, monkeypatch):
    """/stock : ce qui reste dans un box (feuille + rechargements pas encore écrits) et chez les livreurs."""
    from bot.services import sheets

    monkeypatch.setenv("GOOGLE_SHEETS_WEBHOOK_URL", "https://script.google.com/macros/s/x/exec")
    monkeypatch.setenv("GOOGLE_SHEETS_SECRET", "s")

    async def fake_action(action, client=None, **kw):
        if action == "stock_box":
            return {"ok": True, "boxes": {"Box 1": {"DIV": 28, "US": 115, "KT": 0}, "Box 2": {}, "Box 3": {}},
                    "totals": [{"produit": "KT", "total": 0, "seuil": 20, "statut": "RUPTURE"}]}
        return {"ok": True, "livreurs": {"Livreur 1": {"US": 2}, "Livreur B": {}}}

    async def sheet_down(rows, client=None):
        raise RuntimeError("feuille injoignable")

    monkeypatch.setattr(sheets, "fetch_action", fake_action)
    monkeypatch.setattr(sheets, "send_rows", sheet_down)
    div = await db.create_product("DIV", "div", [])
    f1, f2, (l1,) = await setup_network(h, livreurs=(L1,))
    r1 = await register(h, R1, "ravitailleur", "Sam")

    # Un livreur n'y a pas accès.
    await h.text(L1, "/stock")
    assert h.tg.last(L1).text == texts.NOT_FOR_YOU

    # Le ravitailleur charge 5 DIV depuis le Box 1 ; la feuille est injoignable (pas encore écrit).
    await h.text(R1, "/recharge")
    msg_id = h.tg.last(R1).message_id
    for data in [f"rs_l:{l1['id']}", "rs_k:load", "rs_b:0", f"rs_pick:{div['id']}", "rs_q:0:1", "rs_q:0:1",
                 "rs_q:0:1", "rs_q:0:1", "rs_ok"]:
        await h.press_data(R1, h.tg.messages[(R1, msg_id)], data)
    await asyncio.gather(*list(sheets._tasks))

    # /stock box 1 : 28 dans la feuille − 5 chargés à l'instant = 23.
    await h.text(R1, "/stock box 1")
    box = h.tg.last(R1).text
    assert "📦 <b>Box 1</b> — stock actuel" in box and "DIV <b>23</b>" in box and "US <b>115</b>" in box
    assert "KT" not in box                                   # les produits à 0 ne sont pas cités

    # Menu : tous les box, puis livreurs (le livreur a reçu les 5 DIV en plus de la feuille).
    await h.text(F1, "/stock")
    menu = h.tg.last(F1)
    assert menu.text == texts.STOCK_MENU
    await h.press_data(F1, menu, "sv:all")
    overview = h.tg.messages[(F1, menu.message_id)].text
    assert "<b>Box 1</b> : DIV <b>23</b> · US <b>115</b>" in overview and "KT" not in overview
    await h.press_data(F1, menu, "sv:liv")
    livs = h.tg.messages[(F1, menu.message_id)].text
    assert "<b>Livreur 1</b> : US <b>2</b> · DIV <b>5</b>" in livs and "Livreur B" not in livs
    await h.press_data(F1, menu, "sv:box:1")
    assert h.tg.messages[(F1, menu.message_id)].text.endswith("Vide.")
    del r1


async def test_expenses_and_cash_to_collect(h, monkeypatch):
    """/depense (livreur et admin), /caisse avec 💶 Récupérer (→ /recharge cash prérempli), /macaisse ;
    le cash d'une livraison en espèces pas encore écrite dans la feuille est compté par le bot."""
    from bot.services import sheets

    monkeypatch.setenv("GOOGLE_SHEETS_WEBHOOK_URL", "https://script.google.com/macros/s/x/exec")
    monkeypatch.setenv("GOOGLE_SHEETS_SECRET", "s")
    sent, sheet_up = [], {"ok": True}

    async def fake_send(rows, client=None):
        if not sheet_up["ok"]:
            raise RuntimeError("feuille injoignable")
        sent.extend(rows)
        return len(rows)

    async def fake_action(action, client=None, **kw):
        assert action == "cash_livreurs"
        return {"ok": True, "livreurs": {"Livreur 1": {"especes": 910, "virement": 60, "depenses": 200,
                                                       "recupere": 520, "cash": 190}}}

    monkeypatch.setattr(sheets, "send_rows", fake_send)
    monkeypatch.setattr(sheets, "fetch_action", fake_action)
    f1, f2, (l1,) = await setup_network(h, livreurs=(L1,))
    await register(h, R1, "ravitailleur", "Sam")

    def txt(m):
        return m.text.replace("\xa0", " ")

    # Le livreur note 25 € d'essence (boutons + motif tapé).
    await h.text(L1, "/depense")
    msg_id = h.tg.last(L1).message_id

    def screen(tg=L1):
        return h.tg.messages[(tg, msg_id)]

    assert "Quel type ?" in screen().text
    await h.press_data(L1, screen(), "dp_k:c")
    await h.press_data(L1, screen(), "dp_n")
    assert h.tg.answers()[-1]["text"] == texts.EXPENSE_ZERO
    for d in ("dp_a:10", "dp_a:10", "dp_a:5"):
        await h.press_data(L1, screen(), d)
    assert "Montant : <b>25 €</b>" in txt(screen())
    await h.press_data(L1, screen(), "dp_n")
    await h.text(L1, "Essence")
    assert "📝 Motif : Essence" in txt(screen())
    await h.press_data(L1, screen(), "dp_ok")
    assert "✅ Dépense #D" in screen().text
    await asyncio.gather(*list(sheets._tasks))
    assert [(r["type"], r["livreur"], r["depense"], r["montant"], r["motif"]) for r in sent] == [
        ("depense", "Livreur 1", "Charges", 25.0, "Essence")]
    assert "🧾 D#" in txt(h.tg.last(DISPATCH)) and "Charges (à ses frais) — 25 € — Essence" in txt(h.tg.last(DISPATCH))

    # Avance sur paye, montant tapé, sans motif.
    await h.text(L1, "/depense")
    msg_id = h.tg.last(L1).message_id
    await h.press_data(L1, screen(), "dp_k:p")
    await h.text(L1, "12,50")
    await h.press_data(L1, screen(), "dp_m:-1")
    await h.press_data(L1, screen(), "dp_ok")
    expenses = (await db._t("expenses").select("*").order("id").execute()).data
    assert [(e["kind"], float(e["amount"]), e["motif"]) for e in expenses] == [
        ("charges", 25.0, "Essence"), ("paye", 12.5, None)]

    # Un admin la note pour le livreur : le livreur est prévenu.
    await h.text(F1, "/depense")
    msg_id = h.tg.last(F1).message_id
    for d in (f"dp_l:{l1['id']}", "dp_k:c", "dp_a:50", "dp_n", "dp_m:1", "dp_ok"):
        await h.press_data(F1, screen(F1), d)
    assert "Dépense notée pour toi par Franchisé 1" in txt(h.tg.last(L1)) and "50 € — Parking" in txt(h.tg.last(L1))
    assert (await db._t("expenses").select("*").eq("by_user_id", f1["id"]).execute()).data[0]["amount"] == 50

    # Le ravitailleur ne note pas de dépense ; le livreur ne voit pas /caisse.
    await h.text(R1, "/depense")
    assert h.tg.last(R1).text == texts.NOT_FOR_YOU
    await h.text(L1, "/caisse")
    assert h.tg.last(L1).text == texts.NOT_FOR_YOU

    # Livraison en espèces pendant que la feuille est injoignable : +60 € comptés par le bot.
    await asyncio.gather(*list(sheets._tasks))
    sheet_up["ok"] = False
    await go_on_duty(h, L1, BASTILLE)
    await order(h, F1, "rivoli")
    await h.press(L1, h.tg.last(L1), "course_take:")
    await deliver_course(h, L1, h.tg.find(L1, "c'est pour toi"), pay="e")
    await asyncio.gather(*list(sheets._tasks))

    await h.text(L1, "/macaisse")
    mine = txt(h.tg.last(L1))
    # Compte du jour : reste d'avant + encaissé aujourd'hui − charges du jour (25 + 50 + 12,50) = reste.
    assert "📅 Reste d'avant (pas encore remis) : 277,50 €" in mine
    assert "💵 + Encaissé aujourd'hui : 60 €" in mine and "🧾 − Charges : 87,50 €" in mine
    assert "<b>= Il te reste : 250 €</b>" in mine and "Virements de la semaine (pour info) : 60 €" in mine

    await h.text(R1, "/caisse")
    caisse = h.tg.last(R1)
    assert "<b>Livreur 1</b> : 250 €" in txt(caisse) and "Total à récupérer : <b>250 €</b>" in txt(caisse)
    assert [d for _, d in caisse.buttons] == [f"cs_r:{l1['id']}", "cs_ref"]

    # 💶 Récupérer : /recharge « cash seulement » prérempli avec 250 €, validé tel quel.
    sheet_up["ok"] = True
    await h.press_data(R1, caisse, f"cs_r:{l1['id']}")
    editor = h.tg.messages[(R1, caisse.message_id)]
    assert "💶 Cash récupéré : 250 €" in txt(editor)
    await h.press_data(R1, editor, "rs_ok")
    restocks = (await db._t("restocks").select("*").execute()).data
    assert [(r["kind"], float(r["cash"])) for r in restocks] == [("cash", 250.0)]
    assert "💶 Cash remis" in txt(h.tg.last(L1))


async def test_dispatch_prefers_livreur_with_stock(h, monkeypatch, test_config):
    """Le plus proche n'a pas de US : la course part à celui qui en a (même s'il a déjà une course). Personne
    n'en a assez : la course attend, ravitailleurs et dispatch prévenus une fois ; « Je prends » est refusé
    au livreur sans le produit, un admin peut la lui attribuer."""
    import dataclasses

    from bot import config
    from bot.services import sheets

    config.set_config(dataclasses.replace(test_config, stock_alerts=True, broadcast_wave_size=1))
    monkeypatch.setenv("GOOGLE_SHEETS_WEBHOOK_URL", "https://script.google.com/macros/s/x/exec")
    monkeypatch.setenv("GOOGLE_SHEETS_SECRET", "s")
    reads = []

    async def fake_action(action, client=None, **kw):
        reads.append(action)
        return {"ok": True, "livreurs": {"Livreur 1": {"US": 0, "DIV": 4}, "Livreur 2": {"US": 5, "DIV": 0}}}

    async def fake_stock(livreur, client=None):
        return None, livreur["display_name"]

    monkeypatch.setattr(sheets, "fetch_action", fake_action)
    monkeypatch.setattr(sheets, "fetch_stock", fake_stock)
    await db.create_product("US", "us", [])
    EXTRACTIONS["deux us"] = [{**RIVOLI, "products": "2 US", "price": 60}]
    f1, f2, (l1, l2) = await setup_network(h)
    await register(h, R1, "ravitailleur", "Sam")
    await go_on_duty(h, L1, BASTILLE)       # le plus proche de Rivoli, mais 0 US
    await go_on_duty(h, L2, REPUBLIQUE)

    c1 = await order(h, F1, "deux us")
    assert h.tg.last(L2).text.endswith(f"\nCourse #{c1['id']}")
    assert not [t for t in h.tg.texts(L1) if t.endswith(f"\nCourse #{c1['id']}")]
    assert not [t for t in h.tg.texts(R1) if "aucun livreur en service n'a tout" in t]
    await h.press(L2, h.tg.last(L2), "course_take:")

    # Livreur 2 a déjà une course mais encore 3 US : il reçoit la suivante ; Livreur 1 (0 US) non.
    c2 = await order(h, F1, "deux us")
    assert h.tg.last(L2).text.endswith(f"\nCourse #{c2['id']}")
    assert not [t for t in h.tg.texts(L1) if t.endswith(f"\nCourse #{c2['id']}")]
    await h.press(L2, h.tg.last(L2), "course_take:")

    # Plus personne n'a 2 US : la course attend, ravitailleurs et dispatch prévenus une fois.
    c3 = await order(h, F1, "deux us")
    assert not [t for t in h.tg.texts(L1) + h.tg.texts(L2) if t.endswith(f"\nCourse #{c3['id']}")]
    alert = h.tg.find(R1, f"⚠️ Course #{c3['id']}").text.replace("\xa0", " ")
    assert "aucun livreur en service n'a tout en stock" in alert and "• Livreur 1 : 0/2 US" in alert
    assert "La course attend" in alert
    assert h.tg.find(DISPATCH, f"⚠️ Course #{c3['id']}")
    assert reads == ["stock_livreurs"]      # feuille lue une fois, gardée une minute
    await broadcast.run_wave(h.context, c3["id"], advance=True)
    assert len([t for t in h.tg.texts(R1) if t.startswith(f"⚠️ Course #{c3['id']}")]) == 1

    # « Je prends » sans le produit (d'après le tableau) : refusé, le dispatch est prévenu…
    await h.press_data(L1, h.tg.last(L1), f"course_take:{c3['id']}")
    assert h.tg.answers()[-1].get("text").startswith("⛔ D'après le tableau, il te manque : 0/2 US")
    assert (await db.get_course(c3["id"]))["status"] == "pending"
    assert "voulait la prendre, mais il lui manque" in h.tg.last(DISPATCH).text
    # … qui peut la lui attribuer quand même.
    await h.text(DISPATCH, "/encours")
    await h.press_data(DISPATCH, h.tg.last(DISPATCH), f"assign:{c3['id']}")
    await h.press_data(DISPATCH, h.tg.last(DISPATCH), f"assign_to:{c3['id']}:{l1['id']}")
    assert (await db.get_course(c3["id"]))["livreur_id"] == l1["id"]


async def test_weekly_cloture(h, monkeypatch):
    """/cloture : vérification (stock reporté, cash non récupéré, reste du ravitailleur), annulation,
    clôture par le script (archives), double appui refusé, échec expliqué, rappel du lundi."""
    from bot.handlers import cloture as cloture_h
    from bot.services import sheets
    from bot.services import stock as stock_service

    monkeypatch.setenv("GOOGLE_SHEETS_WEBHOOK_URL", "https://script.google.com/macros/s/x/exec")
    monkeypatch.setenv("GOOGLE_SHEETS_SECRET", "s")
    calls = []
    answer = {"cloture": {"ok": True, "archive": "https://drive.google.com/drive/folders/abc",
                          "dossier": "Archives bot / Clôture du 2026-09-28 06h10",
                          "initial": {"Box 1": {"DIV": 28, "US": 112}, "Box 2": {}},
                          "reports": {"Livreur 1": {"DIV": 4, "US": 4}}},
              "ravi_move": {"ok": True, "written": True}}

    async def fake_action(action, client=None, payload=None, timeout=None):
        calls.append((action, payload, timeout))
        if action == "stock_livreurs":
            return {"ok": True, "livreurs": {"Livreur 1": {"DIV": 4, "US": 4, "KT": 0}, "Livreur 2": {"US": 0}}}
        if action == "cash_livreurs":
            return {"ok": True, "livreurs": {"Livreur 1": {"especes": 350, "depenses": 200, "recupere": 100,
                                                           "cash": 50}}, "ravitailleur": 70}
        return answer[action]

    monkeypatch.setattr(sheets, "fetch_action", fake_action)
    f1, f2, (l1, l2) = await setup_network(h)
    r1 = await register(h, R1, "ravitailleur", "Sam")              # 70 € chez lui (registre de l'espace Ravi)
    await db.create_ravi_move({"ravitailleur": r1["display_name"], "kind": "depart", "amount": 70,
                               "by_user_id": (await db.get_user_by_tg(DISPATCH))["id"]})

    def txt(m):
        return m.text.replace("\xa0", " ")

    await h.text(L1, "/reset")
    assert h.tg.last(L1).text == texts.NOT_FOR_YOU

    await h.text(DISPATCH, "/reset")
    check = h.tg.last(DISPATCH)
    assert "🗓 <b>Reset de la semaine</b>" in check.text
    assert "• Livreur 1 : 4 DIV · 4 US" in txt(check) and "Livreur 2" not in txt(check).split("📦 Reporté")[1]
    assert "⚠️ Cash pas encore récupéré" in txt(check) and "• Livreur 1 : 50 €" in txt(check)
    assert "💼 Reste chez les ravitailleurs : 70 € (reporté" in txt(check)
    assert [d for _, d in check.buttons] == ["cl_go", "cl_x"]
    await h.press_data(DISPATCH, check, "cl_x")
    assert h.tg.messages[(DISPATCH, check.message_id)].text == texts.CLOTURE_CANCELLED
    assert [c[0] for c in calls] == ["stock_livreurs", "cash_livreurs"]

    # Réservé au dispatch : un franchisé ne remet pas la semaine à zéro.
    await h.text(F1, "/cloture")
    assert h.tg.last(F1).text == texts.DISPATCH_ONLY

    # Le dispatch clôture ; le bot oublie les mouvements de l'ancienne semaine.
    stock_service.add_inflight(l1["id"], "course:1", {"US": -1})
    await h.text(DISPATCH, "/cloture")
    check = h.tg.last(DISPATCH)
    await h.press_data(DISPATCH, check, "cl_go")
    done = h.tg.messages[(DISPATCH, check.message_id)]
    action, payload, timeout = next(c for c in reversed(calls) if c[0] == "cloture")
    assert action == "cloture" and payload["onglet"] == "Lundi" and payload["libelle"].startswith("Reset du ")
    assert timeout == cloture_h.TIMEOUT
    assert "✅ <b>Semaine remise à zéro</b>" in done.text and 'href="https://drive.google.com/drive/folders/abc"' in done.text
    assert "• Box 1 : 28 DIV · 112 US" in done.text and "• Box 2 : vide" in done.text
    assert f"💼 Reporté chez les ravitailleurs :\n• {r1['display_name']} : 70" in txt(done)
    assert any(a == "ravi_move" and p["motif"] == "Report semaine passée" and p["montant"] == 70 for a, p, _ in calls)
    assert "• Livreur 1 : 4 DIV · 4 US" in done.text
    assert stock_service.inflight_deltas(l1["id"]) == {}
    assert "✅ <b>Semaine remise à zéro</b>" in done.text
    assert (await db._t("events").select("*").eq("type", "cloture").execute()).data

    # Deuxième appui (ou deuxième admin) juste après : refusé.
    await h.text(DISPATCH, "/cloture")
    await h.press_data(DISPATCH, h.tg.last(DISPATCH), "cl_go")
    assert h.tg.answers()[-1]["text"] == texts.CLOTURE_ALREADY
    assert [c[0] for c in calls].count("cloture") == 1

    # Échec du script : expliqué, rien n'est marqué comme clôturé.
    cloture_h._last_done = None
    answer["cloture"] = {"ok": False, "error": "COMPTA_SPREADSHEET_ID vide dans le script"}
    await h.text(DISPATCH, "/cloture")
    check = h.tg.last(DISPATCH)
    await h.press_data(DISPATCH, check, "cl_go")
    assert "⚠️ Reset impossible : COMPTA_SPREADSHEET_ID vide" in h.tg.messages[(DISPATCH, check.message_id)].text
    assert cloture_h._last_done is None

    await cloture_h.weekly_reminder(h.context)
    assert h.tg.last(DISPATCH).text == texts.CLOTURE_REMINDER


async def test_close_day_debrief(h, monkeypatch):
    """/close : débrief de la journée, sans rien changer ; cash à récupérer et stock bas si la feuille est reliée."""
    from bot.services import sheets

    monkeypatch.setenv("GOOGLE_SHEETS_WEBHOOK_URL", "https://script.google.com/macros/s/x/exec")
    monkeypatch.setenv("GOOGLE_SHEETS_SECRET", "s")

    async def fake_send(rows, client=None):
        return len(rows)

    async def fake_action(action, client=None, **kw):
        if action == "cash_livreurs":
            return {"ok": True, "livreurs": {"Livreur 1": {"especes": 60, "cash": 60}, "Livreur 2": {"cash": 0}}}
        if action == "stock_box":
            return {"ok": True, "boxes": {}, "totals": [{"produit": "KT", "total": 0, "seuil": 20, "statut": "RUPTURE"},
                                                        {"produit": "DIV", "total": 50, "seuil": 20, "statut": "OK"}]}
        return {"ok": False, "error": "?"}

    monkeypatch.setattr(sheets, "send_rows", fake_send)
    monkeypatch.setattr(sheets, "fetch_action", fake_action)
    f1, f2, (l1, l2) = await setup_network(h)
    await go_on_duty(h, L1, BASTILLE)
    await go_on_duty(h, L2, REPUBLIQUE)
    c1 = await order(h, F1, "rivoli")
    await h.press(L1, h.tg.find(L1, f"\nCourse #{c1['id']}"), "course_take:")
    await deliver_course(h, L1, h.tg.find(L1, "c'est pour toi"), pay="e")
    c2 = await order(h, F2, "oberkampf")
    await h.press(L2, h.tg.find(L2, f"\nCourse #{c2['id']}"), "course_take:")
    await deliver_course(h, L2, h.tg.find(L2, "c'est pour toi"), pay="v")
    await order(h, F1, "rivoli")                         # reste en attente
    await h.text(L1, "/depense")
    msg_id = h.tg.last(L1).message_id
    for d in ("dp_k:c", "dp_a:10", "dp_n", "dp_m:0", "dp_ok"):
        await h.press_data(L1, h.tg.messages[(L1, msg_id)], d)
    await asyncio.gather(*list(sheets._tasks))

    await h.text(L1, "/close")                                    # un livreur : sa propre fin de service
    assert h.tg.last(L1).text.startswith("🏁 <b>Fin de service — Livreur 1</b>")

    await h.text(F1, "/close")
    text = h.tg.last(F1).text.replace("\xa0", " ")
    assert text.startswith("🔒 <b>Journée close — nuit du")
    assert "📦 <b>2 courses livrées — 110 €</b>" in text and "💵 espèces 60 € · 💳 virement 50 €" in text
    assert "⚠️ 1 course encore ouverte (/encours)" in text
    assert "• Livreur 1 — 1 course — 60 € (💵 60 €) — 🧾 10 €" in text           # pas de « 💳 0 € »
    assert "• Livreur 2 — 1 course — 50 € (💳 50 €)" in text
    assert "• Franchisé 1 — 1 course — 60 €" in text and "• Franchisé 2 — 1 course — 50 €" in text
    assert "🧾 Dépenses : 10 € (charges 10 €)" in text and "📦 Rechargements" not in text
    assert "💶 <b>Cash à récupérer</b> (semaine)\n• Livreur 1 : 60 €" in text and "Livreur 2 : 0" not in text
    assert "Stock sous le seuil" not in text                     # KT à 0 : pas cité ; DIV au-dessus du seuil
    assert "✅ Journée terminée" in text
    assert "(par Franchisé 1)" in h.tg.last(DISPATCH).text
    # Rien n'a changé : la course en attente l'est toujours.
    assert len(await db.list_courses_by_status("pending")) == 1
    assert (await db._t("events").select("*").eq("type", "day_closed").execute()).data


async def test_close_day_picks_the_night_that_just_ended(h, monkeypatch):
    """/close à 10h le matin : la nuit qui vient de finir (rien livré depuis 6h) ; à 3h : la nuit en cours."""
    from datetime import datetime

    from bot.config import PARIS
    from bot.handlers import close_day

    monkeypatch.setattr(close_day, "now_utc", lambda: datetime(2026, 9, 29, 10, 0, tzinfo=PARIS))
    assert (await close_day.closing_night()).isoformat() == "2026-09-28"
    monkeypatch.setattr(close_day, "now_utc", lambda: datetime(2026, 9, 29, 3, 0, tzinfo=PARIS))
    assert (await close_day.closing_night()).isoformat() == "2026-09-28"
    monkeypatch.setattr(close_day, "now_utc", lambda: datetime(2026, 9, 29, 22, 0, tzinfo=PARIS))
    assert (await close_day.closing_night()).isoformat() == "2026-09-29"


async def test_transport_mode_declared_and_metro_detected(h):
    """/dispo : le livreur choisit 🛵 ; pendant la course il disparaît près d'une station et réapparaît
    près d'une autre : « semble avoir pris le métro » au dispatch ; /close et /livreurs le montrent."""
    from datetime import timedelta

    from bot.services import stations
    from bot.timeutil import iso, now_utc

    stations.set_stations([(48.8532, 2.3691, "Bastille"), (48.8484, 2.3959, "Nation")])
    try:
        f1, f2, (l1,) = await setup_network(h, livreurs=(L1,))
        await h.text(L1, "/dispo")
        prompt = h.tg.last(L1)
        assert prompt.text == texts.TRANSPORT_QUESTION
        assert prompt.buttons == [("🚶 À pied ou en transports (métro, bus)", "tmode:t"),
                                  ("🛵 Deux-roues (scooter, moto, vélo)", "tmode:d"), ("🚗 Voiture", "tmode:v")]
        await h.press_data(L1, prompt, "tmode:d")
        assert h.tg.answers()[-1]["text"] == "🛵 Deux-roues noté."
        assert (await db.get_user(l1["id"]))["transport_mode"] == "deux_roues"
        chosen = h.tg.messages[(L1, prompt.message_id)]
        assert chosen.text.startswith("✅ <b>C'est noté :</b> 🛵 Deux-roues") and chosen.buttons[1][0].startswith("✅ 🛵")

        await h.location(L1, 48.8532, 2.3691, message_id=L1)        # à Bastille
        course = await order(h, F1, "rivoli")
        await h.press(L1, h.tg.last(L1), "course_take:")

        # Il a pris la course il y a 10 min, sa dernière position (Bastille) date de 7 min…
        await db._t("courses").update({"assigned_at": iso(now_utc() - timedelta(minutes=10))}).eq(
            "id", course["id"]).execute()
        await db._t("livreur_positions").update({"updated_at": iso(now_utc() - timedelta(minutes=7))}).eq(
            "livreur_id", l1["id"]).execute()
        # … et il réapparaît à Nation.
        await h.location(L1, 48.8484, 2.3959, edited=True, message_id=L1)
        alert = h.tg.find(DISPATCH, f"🚇 #{course['id']}").text.replace("\xa0", " ")
        assert "Livreur 1 semble avoir pris le métro (Bastille → Nation, 2 km en 7 min)" in alert
        assert "déclaré 🛵 deux-roues" in alert
        assert (await db.get_course(course["id"]))["detected_mode"] == "metro"

        await deliver_course(h, L1, h.tg.find(L1, "c'est pour toi"))
        assert (await db.get_course(course["id"]))["detected_mode"] == "metro"
        await h.text(DISPATCH, "/close")
        debrief = h.tg.last(DISPATCH).text
        assert "• Livreur 1 🛵 — 1 course" in debrief
        assert f"⚠️ #{course['id']} Livreur 1 : 🚇 métro · déclaré 🛵" in debrief
        await h.text(DISPATCH, "/livreurs")
        assert "Livreur 1 — 🟢 en service · 📍 à l'instant · en direct · 🛵" in h.tg.last(DISPATCH).text
    finally:
        stations.set_stations([])


async def test_transport_declared_but_vehicle_speed(h):
    """Déclaré 🚶 transport mais trois positions de suite à ≈ 36 km/h : « semble rouler »."""
    from datetime import timedelta

    from bot.timeutil import iso, now_utc

    f1, f2, (l1,) = await setup_network(h, livreurs=(L1,))
    await h.text(L1, "/dispo")
    await h.press_data(L1, h.tg.last(L1), "tmode:t")
    await h.location(L1, 48.8532, 2.3691, message_id=L1)
    course = await order(h, F1, "rivoli")
    await h.press(L1, h.tg.last(L1), "course_take:")
    await db._t("courses").update({"assigned_at": iso(now_utc() - timedelta(minutes=5))}).eq(
        "id", course["id"]).execute()
    lat = 48.8532
    for _ in range(3):
        # 30 s entre deux positions, 300 m parcourus : ≈ 36 km/h.
        await db._t("livreur_positions").update({"updated_at": iso(now_utc() - timedelta(seconds=30))}).eq(
            "livreur_id", l1["id"]).execute()
        lat += 0.0027
        await h.location(L1, lat, 2.3691, edited=True, message_id=L1)
    alert = h.tg.find(DISPATCH, f"🛵 #{course['id']}").text
    assert "Livreur 1 semble rouler (≈ 36 km/h) · déclaré 🚶 transport / à pied" in alert
    assert (await db.get_course(course["id"]))["detected_mode"] == "vehicule"


async def test_exclude_and_delete_remove_access_and_wipe_messages(h):
    """/bannir et /supprimer : la personne ne voit plus les messages récents du bot (effacés), ne
    reçoit plus rien, ses courses sont rendues ; supprimée, elle peut se réinscrire de zéro."""
    f1, f2, (l1, l2) = await setup_network(h)
    await go_on_duty(h, L1, BASTILLE)
    course = await order(h, F1, "rivoli")
    await h.press(L1, h.tg.find(L1, f"\nCourse #{course['id']}"), "course_take:")
    assert any("12 Rue de Rivoli" in t for t in h.tg.texts(L1))           # il a vu l'adresse

    # Exclusion du livreur : course rendue, messages du bot effacés chez lui, plus rien ensuite.
    await h.text(DISPATCH, "/bannir")
    await h.press_data(DISPATCH, h.tg.last(DISPATCH), f"ban:{l1['id']}")
    await h.press(DISPATCH, h.tg.last(DISPATCH), "ban_yes:")
    done = h.tg.find(DISPATCH, "⛔ Livreur 1").text
    assert "est banni" in done and "🧹" in done and "effacé" in done
    assert h.tg.texts(L1) == [texts.BANNED_NOTICE]                        # tout le reste a disparu
    assert (await db.get_course(course["id"]))["status"] == "pending"
    assert await db.get_position(l1["id"]) is None
    await h.text(L1, "/dispo")
    assert h.tg.texts(L1) == [texts.BANNED_NOTICE]                        # ignoré

    # Suppression d'un franchisé (actif) : sa course en attente est annulée, il peut se réinscrire.
    await h.text(DISPATCH, "/supprimer")
    listing = h.tg.last(DISPATCH)
    labels = [b for b, _ in listing.buttons]
    assert any(b.startswith("⛔ Livreur 1") for b in labels) and any(b.startswith("Franchisé 2") for b in labels)
    c2 = await order(h, F2, "oberkampf")
    await h.press_data(DISPATCH, listing, f"del:{f2['id']}")
    confirm = h.tg.last(DISPATCH)
    assert "Supprimer définitivement Franchisé 2" in confirm.text and "/start" in confirm.text
    await h.press(DISPATCH, confirm, "del_yes:")
    assert h.tg.find(DISPATCH, "🗑 Franchisé 2 est supprimé.")
    assert (await db.get_course(c2["id"]))["status"] == "cancelled"
    assert h.tg.texts(F2) == [texts.DELETED_NOTICE]
    gone = await db.get_user(f2["id"])
    assert gone["status"] == "deleted" and gone["telegram_id"] < 0 and gone["real_name"] is None
    assert gone["display_name"] == "Franchisé 2"                          # l'historique garde le nom
    assert f2["id"] not in [u["id"] for u in await db.list_users()]
    await h.text(DISPATCH, "/users")
    assert "Franchisé 2" not in h.tg.last(DISPATCH).text

    # Il revient avec le même compte Telegram : inscription de zéro, cette fois comme livreur.
    again = await register(h, F2, "livreur", "Paul")
    assert again["id"] != f2["id"] and again["role"] == "livreur" and again["status"] == "active"

    # Supprimer le livreur banni : son accès reste coupé, le compte disparaît des listes.
    await h.text(DISPATCH, "/supprimer")
    await h.press_data(DISPATCH, h.tg.last(DISPATCH), f"del:{l1['id']}")
    await h.press(DISPATCH, h.tg.last(DISPATCH), "del_yes:")
    assert (await db.get_user(l1["id"]))["status"] == "deleted"
    await h.text(DISPATCH, "/reactiver")
    assert h.tg.last(DISPATCH).text == texts.REACTIVER_EMPTY


async def test_dispatch_sees_livreur_positions_and_alerts(h):
    """/livreurs : âge et type de partage ; 📍 et 🗺 envoient un point sur une carte ; position fixe
    signalée ; silence de 10 min signalé une fois ; à 30 min, retrait du service signalé."""
    from datetime import timedelta

    from bot import jobs
    from bot.timeutil import iso, now_utc

    f1, f2, (l1, l2) = await setup_network(h)
    await h.text(L1, "/dispo")
    await h.press_data(L1, h.tg.last(L1), "tmode:d")
    await h.location(L1, *BASTILLE, message_id=L1)                  # en direct
    await h.text(L2, "/dispo")
    await h.location(L2, *REPUBLIQUE, live=False, message_id=L2)    # position fixe
    assert "Livreur 2 a envoyé une position fixe" in h.tg.last(DISPATCH).text

    await db._t("livreur_positions").update({"updated_at": iso(now_utc() - timedelta(minutes=5))}).eq(
        "livreur_id", l1["id"]).execute()
    await h.text(DISPATCH, "/livreurs")
    listing = h.tg.last(DISPATCH)
    assert "Livreur 1 — 🟢 en service · 📍 il y a 5 min · en direct · 🛵" in listing.text
    assert "Livreur 2 — 🟢 en service · ⚠️ position fixe (à l'instant)" in listing.text
    datas = [d for _, d in listing.buttons]
    assert f"loc:{l1['id']}" in datas and datas[-1] == "loc_all"

    # 📍 : un point sur la carte, avec l'adresse la plus proche.
    await h.press_data(DISPATCH, listing, f"loc:{l1['id']}")
    pin = h.tg.last(DISPATCH).text
    assert pin.startswith("[carte] 🛵 Livreur 1 — il y a 5 min | près de 12 Rue de Rivoli 75004 Paris · ✅ en direct")
    assert "jusqu'à" in pin and "🟢 en service" in pin and f"@ {BASTILLE[0]},{BASTILLE[1]}" in pin
    # 🗺 : tous les livreurs en service.
    await h.press_data(DISPATCH, listing, "loc_all")
    pins = [t for t in h.tg.texts(DISPATCH) if t.startswith("[carte]")]
    assert len(pins) == 3 and "Livreur 2" in pins[-1] and "⚠️ position fixe" in pins[-1]

    # Plus de position depuis 12 min : dispatch et livreur prévenus, une seule fois.
    await db._t("livreur_positions").update({"updated_at": iso(now_utc() - timedelta(minutes=12))}).eq(
        "livreur_id", l1["id"]).execute()
    await jobs.stale_positions(h.context)
    await jobs.stale_positions(h.context)
    silent = [t for t in h.tg.texts(DISPATCH) if t.startswith("⚠️ Livreur 1 : plus de position")]
    assert len(silent) == 1 and "depuis 12 min (dernière : près de 12 Rue de Rivoli" in silent[0]
    assert h.tg.last(L1).text == texts.POSITION_SILENT
    assert (await db.get_user(l1["id"]))["on_duty"] is True

    # 31 min : retiré du service, dispatch prévenu.
    await db._t("livreur_positions").update({"updated_at": iso(now_utc() - timedelta(minutes=31))}).eq(
        "livreur_id", l1["id"]).execute()
    await jobs.stale_positions(h.context)
    assert "⏸ Livreur 1 retiré du service : plus de position depuis 30 min." in h.tg.texts(DISPATCH)
    assert (await db.get_user(l1["id"]))["on_duty"] is False
    await h.text(DISPATCH, "/livreurs")
    assert "Livreur 1 — ⏸ pause" in h.tg.last(DISPATCH).text

    # Livreur sans aucune position : 📍 répond qu'il n'y en a pas.
    await db.delete_position(l2["id"])
    await h.press_data(DISPATCH, h.tg.last(DISPATCH), f"loc:{l2['id']}")
    assert h.tg.answers()[-1]["text"] == texts.NO_POSITION


async def test_dispo_without_position_reminder_and_pause_details(h):
    """/dispo sans position : marche à suivre « jusqu'à ce que je l'arrête » ; 3 min plus tard, relance du
    livreur et alerte au dispatch ; /livreurs détaille les livreurs en pause."""
    from types import SimpleNamespace

    from bot.handlers import livreur as livreur_h

    f1, f2, (l1, l2) = await setup_network(h)
    welcome = h.tg.find(L1, "Tu es validé").text
    assert "Jusqu'à ce que je l'arrête" in welcome

    await h.text(L1, "/dispo")
    assert "Jusqu'à ce que je l'arrête" in h.tg.texts(L1)[-2]
    jobs_ = h.app.job_queue.get_jobs_by_name(f"dispo:{l1['id']}")
    assert len(jobs_) == 1

    await h.text(DISPATCH, "/livreurs")
    listing = h.tg.last(DISPATCH).text
    assert "Livreur 1 — ⏸ pause · /dispo à l'instant, position pas encore reçue" in listing
    assert "Livreur 2 — ⏸ pause · jamais de position" in listing

    # 3 min après, toujours rien : relance et alerte.
    ctx = SimpleNamespace(bot=h.app.bot, job=SimpleNamespace(data=jobs_[0].data))
    await livreur_h.dispo_reminder(ctx)
    assert h.tg.last(L1).text == texts.DISPO_REMINDER
    assert "Livreur 1 a fait /dispo il y a 3 min mais n'a pas partagé sa position" in h.tg.last(DISPATCH).text

    # Il partage sa position : la relance suivante ne dit plus rien.
    await h.location(L1, *BASTILLE, message_id=L1)
    before = len(h.tg.texts(DISPATCH))
    await livreur_h.dispo_reminder(ctx)
    assert len(h.tg.texts(DISPATCH)) == before


async def test_sales_recap_fills_the_sheet(h, monkeypatch):
    """/ventes : exemple, erreurs expliquées, aperçu, ✅ → ventes en base et lignes OK dans la feuille ;
    /synchro les renvoie ; un franchisé (admin) peut aussi le faire, le dispatch est prévenu."""
    from bot.services import sheets

    monkeypatch.setenv("GOOGLE_SHEETS_WEBHOOK_URL", "https://script.google.com/macros/s/x/exec")
    monkeypatch.setenv("GOOGLE_SHEETS_SECRET", "s")
    sent = []

    async def fake_send(rows, client=None):
        sent.extend(rows)
        return len(rows)

    monkeypatch.setattr(sheets, "send_rows", fake_send)
    for name in ("US", "DIV"):
        await db.create_product(name, name.lower(), [])
    f1, f2, (l1, l2) = await setup_network(h)

    await h.text(L1, "/ventes")
    assert h.tg.last(L1).text == texts.NOT_FOR_YOU

    await h.text(DISPATCH, "/ventes")
    assert h.tg.last(DISPATCH).text == texts.SALES_HELP
    await h.text(DISPATCH, "Livreur 1\n2 US 65")
    assert "prix 65 € — les prix vont de 10 en 10 €" in h.tg.last(DISPATCH).text
    await h.text(DISPATCH, "Livreur 1\n2 US 60\nCB 1 DIV 30\n\nLivreur 2\n1 us 30\n1 DIV 0")
    preview = h.tg.last(DISPATCH)
    text = preview.text.replace("\xa0", " ")
    assert "<b>Livreur 1</b>\n💵 2 US — 60 €\n💳 1 DIV — 30 €" in text and "<b>Livreur 2</b>\n💵 1 US — 30 €\n💵 1 DIV — 🎁 offert" in text
    assert "Total : 💵 espèces 90 € · 💳 virement 30 € — 4 lignes" in text
    assert [d for _, d in preview.buttons] == ["sl_ok", "sl_x"]
    await h.press_data(DISPATCH, preview, "sl_ok")
    assert "✅ 4 ventes ajoutées au tableau (onglet" in h.tg.messages[(DISPATCH, preview.message_id)].text
    rows = (await db._t("sales").select("*").order("id").execute()).data
    assert [(r["livreur_name"], r["livreur_id"], r["payment"], r["product"], r["qty"], float(r["price"])) for r in rows] == [
        ("Livreur 1", l1["id"], "especes", "US", 2, 60.0), ("Livreur 1", l1["id"], "virement", "DIV", 1, 30.0),
        ("Livreur 2", l2["id"], "especes", "US", 1, 30.0), ("Livreur 2", l2["id"], "especes", "DIV", 1, 0.0)]
    assert [(r["numero"], r["livreur"], r["statut"], r["paiement"], r["lignes"]) for r in sent] == [
        (f"V{rows[0]['id']}", "Livreur 1", "OK", "Espèces", [{"produit": "US", "qte": 2, "prix": 60.0}]),
        (f"V{rows[1]['id']}", "Livreur 1", "OK", "Virement", [{"produit": "DIV", "qte": 1, "prix": 30.0}]),
        (f"V{rows[2]['id']}", "Livreur 2", "OK", "Espèces", [{"produit": "US", "qte": 1, "prix": 30.0}]),
        (f"V{rows[3]['id']}", "Livreur 2", "OK", "Espèces", [{"produit": "DIV", "qte": 1, "prix": 0.0}])]
    assert {r["adresse"] for r in sent} == {"Récap ventes"}
    # Bouton de nouveau : expiré.
    await h.press_data(DISPATCH, preview, "sl_ok")
    assert h.tg.answers()[-1]["text"] == texts.SALES_EXPIRED

    sent.clear()
    await h.text(DISPATCH, "/synchro")
    assert "4 ventes saisies" in h.tg.last(DISPATCH).text and len(sent) == 4

    # Un franchisé, récap dans le même message que la commande ; annulé puis refait.
    await h.text(F1, "/ventes\nLivreur 2\nVirement 2 DIV 60")
    await h.press_data(F1, h.tg.last(F1), "sl_x")
    assert h.tg.last(F1).text == texts.SALES_CANCELLED
    await h.text(F1, "/ventes\nLivreur 2\n12 rue de la Paix Paris : Virement 2 DIV 60")
    assert "💳 2 DIV — 60 € · 📍 12 rue de la Paix Paris" in h.tg.last(F1).text.replace("\xa0", " ")
    sent.clear()
    await h.press_data(F1, h.tg.last(F1), "sl_ok")
    assert "🧾 <b>Ventes ajoutées" in h.tg.last(DISPATCH).text and "(par Franchisé 1)" in h.tg.last(DISPATCH).text
    assert [r["adresse"] for r in sent] == ["12 rue de la Paix Paris"]
    last = (await db._t("sales").select("*").order("id", desc=True).limit(1).execute()).data[0]
    assert last["address"] == "12 rue de la Paix Paris"


async def test_ravi_text_restock(h, monkeypatch):
    """/ravi : rechargement en texte (livreur, box, « 12 DIV », « -2 MSX », « cash 300 ») → aperçu, ✅,
    rechargements en base, livreur et dispatch prévenus, tableau Rechargement rempli."""
    import asyncio

    from bot.services import sheets

    monkeypatch.setenv("GOOGLE_SHEETS_WEBHOOK_URL", "https://script.google.com/macros/s/x/exec")
    monkeypatch.setenv("GOOGLE_SHEETS_SECRET", "s")
    sent = []

    async def fake_send(rows, client=None):
        sent.extend(rows)
        return len(rows)

    monkeypatch.setattr(sheets, "send_rows", fake_send)
    for name in ("DIV", "US", "MSX"):
        await db.create_product(name, name.lower(), [])
    f1, f2, (l1, l2) = await setup_network(h)
    r1 = await register(h, R1, "ravitailleur", "Sam")

    await h.text(L1, "/ravi 1")
    assert h.tg.last(L1).text == texts.NOT_FOR_YOU

    # Le ravitailleur, sans numéro : c'est lui. Box oublié : le bot le demande ; puis un texte complet.
    await h.text(R1, "/ravi\nLivreur 1\n12 DIV")
    ask = h.tg.last(R1)
    assert "quel box pour Livreur 1" in ask.text and ask.buttons[0][1] == "rvf:b:0:0"
    await h.text(R1, "Livreur 1\nBox 1\n12 DIV\n-2 MSX\ncash 300")
    preview = h.tg.last(R1)
    text = preview.text.replace("\xa0", " ")
    assert "📦 <b>Rechargement — Ravitailleur 1</b>" in text
    assert "<b>Livreur 1</b> · Box 1\n📦 +12 DIV\n↩️ −2 MSX\n💶 cash récupéré 300 €" in text
    assert [d for _, d in preview.buttons] == ["rv_ok", "rv_x"]
    await h.press_data(R1, preview, "rv_ok")
    assert "✅ <b>Rechargement enregistré — Ravitailleur 1</b>" in h.tg.messages[(R1, preview.message_id)].text
    await asyncio.gather(*list(sheets._tasks))
    rows = (await db._t("restocks").select("*").order("id").execute()).data
    assert [(r["kind"], r["box"], r["items"], float(r["cash"]), r["livreur_id"], r["by_user_id"], r["ravitailleur_name"])
            for r in rows] == [
        ("load", "Box 1", [{"p": "DIV", "q": 12}], 300.0, l1["id"], r1["id"], "Ravitailleur 1"),
        ("unload", "Box 1", [{"p": "MSX", "q": 2}], 0.0, l1["id"], r1["id"], "Ravitailleur 1")]
    assert [(s["livreur"], s["box"], s["produits"], s["cash"], s["ravitailleur"]) for s in sent] == [
        ("Livreur 1", "Box 1", {"DIV": 12}, 300.0, "Ravitailleur 1"),
        ("Livreur 1", "Box 1", {"MSX": -2}, 0.0, "Ravitailleur 1")]
    assert any("Chargement reçu" in t for t in h.tg.texts(L1)) and any("Stock repris" in t for t in h.tg.texts(L1))
    assert any(t.startswith(f"📦 R#{rows[0]['id']} — Ravitailleur 1 → Livreur 1") for t in h.tg.texts(DISPATCH))

    # Le dispatch : /ravi sans numéro → boutons du ravitailleur ; /ravi 1 seul → exemple, puis le texte.
    await h.text(DISPATCH, "/ravi")
    assert h.tg.last(DISPATCH).text == texts.RAVI_ASK_WHO
    await h.text(DISPATCH, "/ravi 1")
    assert "📦 <b>Rechargement — Ravitailleur 1</b> — envoie-le ici" in h.tg.last(DISPATCH).text
    await h.text(DISPATCH, "Livreur 2\nbox 2\n4 US")
    await h.press_data(DISPATCH, h.tg.last(DISPATCH), "rv_ok")
    last = (await db._t("restocks").select("*").order("id", desc=True).limit(1).execute()).data[0]
    assert (last["livreur_id"], last["box"], last["by_user_id"], last["ravitailleur_name"]) == (
        l2["id"], "Box 2", r1["id"], "Ravitailleur 1")


async def test_swipe_between_livreurs(h, monkeypatch):
    """/swipe : transfert d'un livreur à un autre (produits ou « tout ») → aperçu (alerte si le stock ne
    suffit pas), ✅, un rechargement « swipe » écrit en une ligne, livreurs et dispatch prévenus."""
    import asyncio

    from bot.services import sheets, stock

    monkeypatch.setenv("GOOGLE_SHEETS_WEBHOOK_URL", "https://script.google.com/macros/s/x/exec")
    monkeypatch.setenv("GOOGLE_SHEETS_SECRET", "s")
    sent = []

    async def fake_send(rows, client=None):
        sent.extend(rows)
        return len(rows)

    async def fake_fetch(action, *args, **kw):
        if action == "stock_livreurs":
            return {"ok": True, "livreurs": {"Livreur 1": {"DIV": 5, "US": 2, "MSX": 0}, "Livreur 2": {}}}
        return {"ok": False, "error": "test"}

    monkeypatch.setattr(sheets, "send_rows", fake_send)
    monkeypatch.setattr(sheets, "fetch_action", fake_fetch)
    for name in ("DIV", "US", "MSX"):
        await db.create_product(name, name.lower(), [])
    f1, f2, (l1, l2) = await setup_network(h)
    await register(h, R1, "ravitailleur", "Sam")

    await h.text(L1, "/swipe")
    assert h.tg.last(L1).text == texts.NOT_FOR_YOU

    # Plus que ce que le livreur a : aperçu avec alerte ; annulé.
    await h.text(R1, "/swipe Livreur 1 > Livreur 2\n7 DIV")
    preview = h.tg.last(R1)
    assert "<b>Livreur 1</b> ➜ <b>Livreur 2</b>\n🔁 7 DIV" in preview.text
    assert "⚠️ Livreur 1 n'a que 5 DIV d'après le tableau (tu en transfères 7)" in preview.text
    assert [d for _, d in preview.buttons] == ["sw_ok", "sw_x"]
    await h.press_data(R1, preview, "sw_x")
    assert h.tg.last(R1).text == texts.SWIPE_CANCELLED
    assert (await db._t("restocks").select("*").execute()).data == []

    # « tout » : le stock du tableau ; rien chez Livreur 2 → erreur.
    await h.text(R1, "/swipe\nLivreur 2 > Livreur 1\ntout")
    assert "Livreur 2 n'a plus rien en stock d'après le tableau" in h.tg.last(R1).text
    await h.text(R1, "Livreur 1 > Livreur 2\ntout")
    preview = h.tg.last(R1)
    assert "<b>Livreur 1</b> ➜ <b>Livreur 2</b> (tout)\n🔁 5 DIV · 2 US" in preview.text
    await h.press_data(R1, preview, "sw_ok")
    assert "✅ <b>Swipe enregistré</b>" in h.tg.messages[(R1, preview.message_id)].text
    await asyncio.gather(*list(sheets._tasks))
    rows = (await db._t("restocks").select("*").order("id").execute()).data
    assert [(r["kind"], r["box"], r["items"], r["livreur_id"], r["livreur_name"], r["to_livreur_id"], r["to_livreur_name"])
            for r in rows] == [
        ("swipe", "Swipe", [{"p": "DIV", "q": 5}, {"p": "US", "q": 2}], l1["id"], "Livreur 1", l2["id"], "Livreur 2")]
    # Une seule ligne dans le tableau : A celui qui donne, quantités positives, T celui qui reçoit.
    assert [(s["livreur"], s["box"], s["produits"], s["ravitailleur"], s["cash"]) for s in sent] == [
        ("Livreur 1", "Swipe", {"DIV": 5, "US": 2}, "Livreur 2", 0.0)]
    assert h.tg.last(L1).text == "🔁 Swipe : tu donnes à Livreur 2\n−5 DIV, −2 US"
    assert h.tg.last(L2).text == "🔁 Swipe : tu reçois de Livreur 1\n+5 DIV, +2 US"
    assert "✅ <b>Swipe enregistré</b>" in h.tg.last(DISPATCH).text and "(par Ravitailleur 1)" in h.tg.last(DISPATCH).text
    assert stock.inflight_deltas(stock.box_key("Swipe")) == {}
    # Feuille injoignable : le bot compte le transfert chez les deux livreurs en attendant.
    async def no_sheet(restock):
        return False

    real_push = sheets.push_restock
    monkeypatch.setattr(sheets, "push_restock", no_sheet)
    await stock.push_restock_tracked(rows[0])
    assert stock.inflight_deltas(l1["id"]) == {"DIV": -5, "US": -2}
    assert stock.inflight_deltas(l2["id"]) == {"DIV": 5, "US": 2}
    assert stock.inflight_deltas(stock.box_key("Swipe")) == {}
    stock.remove_inflight(l1["id"], f"restock:{rows[0]['id']}")
    stock.remove_inflight(l2["id"], f"restock:{rows[0]['id']}")
    monkeypatch.setattr(sheets, "push_restock", real_push)

    # Le dispatch : /swipe seul → exemple, puis le texte.
    await h.text(DISPATCH, "/swipe")
    assert h.tg.last(DISPATCH).text == texts.SWIPE_HELP
    await h.text(DISPATCH, "Livreur 2 vers Livreur 1\n1 US")
    await h.press_data(DISPATCH, h.tg.last(DISPATCH), "sw_ok")
    await asyncio.gather(*list(sheets._tasks))
    assert len((await db._t("restocks").select("id").execute()).data) == 2

    # Swipe de cash : une ligne avec le cash en colonne C ; le bot le compte chez celui qui reçoit.
    await h.text(DISPATCH, "/swipe Livreur 1 > Livreur 2\ncash 350")
    preview = h.tg.last(DISPATCH)
    assert "<b>Livreur 1</b> ➜ <b>Livreur 2</b>\n💶 cash 350" in preview.text.replace("\xa0", " ")
    await h.press_data(DISPATCH, preview, "sw_ok")
    await asyncio.gather(*list(sheets._tasks))
    assert (sent[-1]["livreur"], sent[-1]["box"], sent[-1]["cash"], sent[-1]["produits"], sent[-1]["ravitailleur"]) == (
        "Livreur 1", "Swipe", 350.0, {}, "Livreur 2")
    assert "+350" in h.tg.last(L2).text and "de cash" in h.tg.last(L2).text
    cash_row = (await db._t("restocks").select("*").order("id", desc=True).limit(1).execute()).data[0]
    from bot.services import cash as cash_svc

    monkeypatch.setattr(sheets, "push_restock", no_sheet)
    await stock.push_restock_tracked(cash_row)
    token = f"restock:{cash_row['id']}"
    assert stock.inflight_deltas(l1["id"]) == {} and stock.inflight_deltas(l2["id"]) == {}   # pas de produits
    assert cash_svc.inflight(l1["id"]) == -350.0 and cash_svc.inflight(l2["id"]) == 350.0
    cash_svc.done(l1["id"], token)
    cash_svc.done(l2["id"], token)
    monkeypatch.setattr(sheets, "push_restock", real_push)

    # /close : les swipes à part, pas comptés comme rechargements (nuit en cours, quelle que soit l'heure).
    from bot.handlers import close_day
    from bot.timeutil import night_start_date, now_utc

    debrief = await close_day.build_debrief(night_start_date(now_utc(), 6))
    assert "📦 Rechargements" not in debrief and "🔁 Swipes entre livreurs : 3" in debrief


async def test_sales_for_an_earlier_night(h, monkeypatch):
    """/vente (singulier) et /ventes lundi : récap saisi en retard → onglet du jour choisi, gardé en base
    (night) pour que /synchro le renvoie au même onglet."""
    from bot.services import sales as sales_svc
    from bot.services import sheets
    from bot.timeutil import night_label, night_start_date, now_utc

    monkeypatch.setenv("GOOGLE_SHEETS_WEBHOOK_URL", "https://script.google.com/macros/s/x/exec")
    monkeypatch.setenv("GOOGLE_SHEETS_SECRET", "s")
    sent = []

    async def fake_send(rows, client=None):
        sent.extend(rows)
        return len(rows)

    monkeypatch.setattr(sheets, "send_rows", fake_send)
    await db.create_product("US", "us", [])
    await setup_network(h)
    current = night_start_date(now_utc(), 6)

    await h.text(DISPATCH, "/ventes demain")
    assert h.tg.last(DISPATCH).text == texts.sales_bad_day("demain")

    # Le jour dans la même commande que le récap.
    monday = sales_svc.target_night("lundi", current)
    label = "Lundi" if monday == current else f"Lundi — nuit du {night_label(monday)}"
    await h.text(DISPATCH, "/vente lundi\nLivreur 1\n2 US 60")
    preview = h.tg.last(DISPATCH)
    assert f"🧾 <b>Ventes à ajouter — onglet {label}</b>" in preview.text
    await h.press_data(DISPATCH, preview, "sl_ok")
    row = (await db._t("sales").select("*").execute()).data[0]
    assert row["night"] == monday.isoformat()
    assert [(s["onglet"], s["livreur"]) for s in sent] == [("Lundi", "Livreur 1")]
    assert sheets.sale_row(row)["onglet"] == "Lundi"           # /synchro : même onglet

    # « hier » seul : l'exemple avec l'onglet, puis le récap (la nuit est gardée).
    yesterday = current - timedelta(days=1)
    tab = sheets.JOURS[yesterday.weekday()]
    await h.text(DISPATCH, "/ventes hier")
    assert f"Récap des ventes — onglet {tab} — nuit du {night_label(yesterday)}</b>" in h.tg.last(DISPATCH).text
    await h.text(DISPATCH, "Livreur 2\n1 US 30")
    await h.press_data(DISPATCH, h.tg.last(DISPATCH), "sl_ok")
    assert sent[-1]["onglet"] == tab


async def test_sales_recap_with_charges_and_paye(h, monkeypatch, test_config):
    """/ventes : « charge = 20 essence » / « paye = 100 » → dépenses du livreur du bloc, écrites dans la zone
    DÉPENSES de l'onglet (même pour un livreur des feuilles sans compte), comptées dans le débrief."""
    import dataclasses

    from bot import config
    from bot.handlers import close_day
    from bot.services import cash, sheets
    from bot.timeutil import night_start_date, now_utc

    monkeypatch.setenv("GOOGLE_SHEETS_WEBHOOK_URL", "https://script.google.com/macros/s/x/exec")
    monkeypatch.setenv("GOOGLE_SHEETS_SECRET", "s")
    sent = []

    async def fake_send(rows, client=None):
        sent.extend(rows)
        return len(rows)

    monkeypatch.setattr(sheets, "send_rows", fake_send)
    await db.create_product("US", "us", [])
    f1, f2, (l1, l2) = await setup_network(h)
    config.set_config(dataclasses.replace(test_config, livreur_names=("Livreur Z",)))   # sans compte dans le bot

    await h.text(DISPATCH, "/ventes lundi\nLivreur 1\n12 rue X : 2 US 100 charge = 20 essence\npaye = 100\n\n"
                           "Livreur Z\ncharge 15 parking")
    preview = h.tg.last(DISPATCH)
    text = preview.text.replace("\xa0", " ")
    assert "💵 2 US — 100 € · 📍 12 rue X\n🧾 Charge 20 € — essence\n💸 Paye 100 €" in text
    assert "<b>Livreur Z</b>\n🧾 Charge 15 € — parking" in text
    assert "Dépenses : 🧾 Charge 35 € · 💸 Paye 100 €" in text
    await h.press_data(DISPATCH, preview, "sl_ok")
    assert "✅ 1 vente et 3 dépenses ajoutées au tableau" in h.tg.last(DISPATCH).text
    rows = (await db._t("expenses").select("*").order("id").execute()).data
    assert [(r["livreur_id"], r["livreur_name"], r["kind"], float(r["amount"]), r["motif"]) for r in rows] == [
        (l1["id"], "Livreur 1", "charges", 20.0, "essence"), (l1["id"], "Livreur 1", "paye", 100.0, None),
        (None, "Livreur Z", "charges", 15.0, "parking")]
    deps = [r for r in sent if r.get("type") == "depense"]
    assert [(d["onglet"], d["livreur"], d["depense"], d["montant"], d["motif"]) for d in deps] == [
        ("Lundi", "Livreur 1", "Charges", 20.0, "essence"), ("Lundi", "Livreur 1", "Paye", 100.0, ""),
        ("Lundi", "Livreur Z", "Charges", 15.0, "parking")]
    assert cash.inflight(l1["id"]) == 0                         # la feuille les a : plus rien « en vol »
    # /synchro et le débrief de la nuit les gardent (même sans compte).
    assert sheets.expense_row(rows[2], {})["livreur"] == "Livreur Z"
    await close_day.build_debrief(night_start_date(now_utc(), 6))


async def test_double_tap_on_confirm_saves_once(h, monkeypatch):
    """Deux appuis presque simultanés sur ✅ (/ravi, /ventes) : un seul enregistrement, le second « expiré »."""
    from bot.services import sheets

    monkeypatch.setenv("GOOGLE_SHEETS_WEBHOOK_URL", "https://script.google.com/macros/s/x/exec")
    monkeypatch.setenv("GOOGLE_SHEETS_SECRET", "s")

    async def fake_send(rows, client=None):
        return len(rows)

    monkeypatch.setattr(sheets, "send_rows", fake_send)
    await db.create_product("US", "us", [])
    await setup_network(h)
    await register(h, R1, "ravitailleur", "Sam")

    await h.text(DISPATCH, "/ravi 1")
    await h.text(DISPATCH, "Livreur 1\nBox 1\n3 US")
    preview = h.tg.last(DISPATCH)
    assert [d for _, d in preview.buttons][:1] == ["rv_ok"]
    await asyncio.gather(h.press_data(DISPATCH, preview, "rv_ok"), h.press_data(DISPATCH, preview, "rv_ok"))
    assert len((await db._t("restocks").select("id").execute()).data) == 1
    assert texts.RAVI_EXPIRED in [a.get("text") for a in h.tg.answers()]

    await h.text(DISPATCH, "/ventes\nLivreur 1\n1 US 50")
    preview = h.tg.last(DISPATCH)
    await asyncio.gather(h.press_data(DISPATCH, preview, "sl_ok"), h.press_data(DISPATCH, preview, "sl_ok"))
    assert len((await db._t("sales").select("id").execute()).data) == 1


async def test_sheet_error_alerts_dispatch(h):
    """Une écriture refusée par un tableau est signalée au dispatch (une fois), pas seulement dans les journaux ;
    au démarrage, les erreurs déjà passées ne sont pas resignalées."""
    from bot import jobs

    jobs._sheet_error_seen = None
    await db.log_event("sheet_error", payload={"restock_id": 3, "error": "ancienne"})
    await jobs.sheet_errors(h.context)                       # démarrage : point de départ, rien d'envoyé
    assert not any("ancienne" in t for t in h.tg.texts(DISPATCH))
    await db.log_event("sheet_error", payload={"restock_id": 19, "error": "réponse du script : rechargement R19 : "
                                                                          "produit absent des colonnes : XYZ"})
    await db.log_event("sheet_error", payload={"expense_id": 4, "error": "dépenses pleines"})
    await jobs.sheet_errors(h.context)
    alert = h.tg.last(DISPATCH).text
    assert "⚠️ <b>Pas écrit dans le tableau</b>" in alert
    assert "• Rechargement R#19 : rechargement R19 : produit absent des colonnes : XYZ" in alert
    assert "• Dépense D#4 : dépenses pleines" in alert and "/synchro" in alert
    count = len(h.tg.texts(DISPATCH))
    await jobs.sheet_errors(h.context)                       # rien de nouveau : pas de second message
    assert len(h.tg.texts(DISPATCH)) == count


async def test_livreur_declares_stock_at_start_and_end(h, monkeypatch, test_config):
    """Début de service : le livreur déclare son stock (ou ✅ si le tableau est exact) et confirme ; une seule
    fois par nuit. Fin de service (/close) : ce qu'il lui reste. Le dispatch voit les écarts ; sans réponse,
    une relance et le dispatch est prévenu."""
    from datetime import timedelta

    from bot.handlers import declare as declare_h
    from bot.services import sheets, stock
    from bot.timeutil import iso, now_utc

    import dataclasses

    from bot import config

    config.set_config(dataclasses.replace(test_config, stock_declaration=True))
    for name in ("US", "DIV"):
        await db.create_product(name, name.lower(), [])
    f1, f2, (l1,) = await setup_network(h, livreurs=(L1,))
    await go_on_duty(h, L1, BASTILLE)                        # en service : la question est posée
    prompt = h.tg.find(L1, "📦 <b>Début de service : ton stock</b>")
    assert "Écris ce que tu as <b>sur toi</b>" in prompt.text and not prompt.buttons   # tableau non relié

    await h.text(L1, "3 US, 2 machin")
    assert "produit inconnu « machin »" in h.tg.last(L1).text
    await h.text(L1, "OK")                                   # valide toujours une course, n'est pas un stock
    assert "machin" not in h.tg.last(L1).text
    await h.text(L1, "3 US, 2 DIV")
    preview = h.tg.last(L1)
    assert "tu déclares :</b> 3 US · 2 DIV" in preview.text
    assert [d for _, d in preview.buttons] == [f"dc_ok:{_decl_id(preview)}", f"dc_fix:{_decl_id(preview)}"]
    confirm = preview.buttons[0][1]
    await h.press_data(L1, preview, confirm)
    assert h.tg.messages[(L1, preview.message_id)].text.startswith("✅ <b>Début de service — stock noté :</b> 3 US · 2 DIV")
    assert any("Livreur 1 — début de service, stock déclaré : <b>3 US · 2 DIV</b>" in t for t in h.tg.texts(DISPATCH))
    [row] = (await db._t("stock_declarations").select("*").execute()).data
    assert (row["kind"], row["status"], row["items"]) == ("start", "confirmed", {"US": 3, "DIV": 2})
    await h.press_data(L1, preview, confirm)                 # second appui : rien de plus
    assert h.tg.answers()[-1]["text"] == texts.DECLARE_DONE_ALREADY

    # Pause puis reprise la même nuit : pas de nouvelle question.
    n = len(h.tg.inbox(L1))
    await h.text(L1, "/pause")
    await h.text(L1, "/dispo")
    assert not any("Début de service : ton stock" in m.text for m in h.tg.inbox(L1)[n:])

    # Fin de service, tableau relié : ✅ C'est exact reprend le stock du tableau ; l'écart est signalé.
    monkeypatch.setenv("GOOGLE_SHEETS_WEBHOOK_URL", "https://script.google.com/macros/s/x/exec")
    monkeypatch.setenv("GOOGLE_SHEETS_SECRET", "s")

    async def fake_stock(livreur):
        return {"US": 1.0, "DIV": 0.0}, "Livreur 1"

    monkeypatch.setattr(stock, "current_stock", fake_stock)

    async def no_sheet():
        return {"ok": False, "error": "x"}

    monkeypatch.setattr(stock, "livreurs_now", no_sheet)
    monkeypatch.setattr(sheets, "fetch_action", lambda *a, **k: no_sheet())
    await h.text(L1, "/close")
    end = h.tg.find(L1, "🏁 <b>Fin de service : ce qu'il te reste</b>")
    assert "D'après le tableau : <b>1 US</b>" in end.text and end.buttons[0][0] == "✅ C'est exact"
    await h.text(L1, "2 US")
    await h.press_data(L1, h.tg.last(L1), h.tg.last(L1).buttons[0][1])
    alert = h.tg.last(DISPATCH).text
    assert "fin de service, stock déclaré : <b>2 US</b>" in alert and "• US : 2 au lieu de 1" in alert

    # Relance : une déclaration restée sans réponse 10 min → livreur relancé, dispatch prévenu, une fois.
    await db._t("stock_declarations").update({"status": "asked", "asked_at": iso(now_utc() - timedelta(minutes=11))}) \
        .eq("kind", "end").execute()
    await declare_h.remind(h.context)
    assert h.tg.last(L1).text.startswith("⏳ <b>Rappel — Fin de service")
    assert "Livreur 1 n'a pas confirmé son stock (fin de service) depuis 10 min" in h.tg.last(DISPATCH).text
    n = len(h.tg.inbox(DISPATCH))
    await declare_h.remind(h.context)
    assert len(h.tg.inbox(DISPATCH)) == n


async def test_no_course_until_start_stock_confirmed(h, test_config):
    """Stock de début de service pas confirmé : aucune course proposée ; dès la confirmation, la course
    en attente lui est proposée."""
    import dataclasses

    from bot import config

    config.set_config(dataclasses.replace(test_config, stock_declaration=True))
    await db.create_product("US", "us", [])
    f1, f2, (l1,) = await setup_network(h, livreurs=(L1,))
    await go_on_duty(h, L1, BASTILLE)
    assert "⛔ Pas de course tant que ce n'est pas confirmé." in h.tg.find(L1, "📦 <b>Début de service").text
    course = await order(h, F1, "rivoli")
    assert not any("course_take:" in d for m in h.tg.inbox(L1) for _, d in m.buttons)
    assert (await db.get_course(course["id"]))["status"] == "pending"

    await h.text(L1, "rien")
    preview = h.tg.last(L1)
    assert "tu déclares :</b> rien" in preview.text
    await h.press_data(L1, preview, preview.buttons[0][1])
    assert any(d.startswith("course_take:") for m in h.tg.inbox(L1) for _, d in m.buttons)   # proposée


async def test_livreur_announces_eta(h):
    """Course prise : le livreur écrit son temps précis (« 47 », « 1h10 ») ; franchisé et dispatch prévenus,
    fiche du franchisé à jour ; un nouveau temps = nouveau délai. Pendant une autre saisie, « 20 » n'est pas un délai."""
    from bot.handlers import livreur as livreur_h

    assert [livreur_h.eta_minutes(t) for t in ("47", "47 min", "50mn", "1h10", "1 h", "2h", " 12 minutes ")] == \
        [47, 47, 50, 70, 60, 120, 12]
    assert [livreur_h.eta_minutes(t) for t in ("OK", "3 US", "0", "500", "rue 12", "")] == [None] * 6

    f1, f2, (l1, l2) = await setup_network(h)
    await go_on_duty(h, L1, BASTILLE)
    course = await order(h, F1, "rivoli")
    await h.press(L1, h.tg.last(L1), "course_take:")
    ask = h.tg.find(L1, f"⏱ <b>Course #{course['id']}</b> — dans combien de temps tu y es ?")
    assert not ask.buttons

    await h.text(L1, "47")
    assert h.tg.last(L1).text.startswith(f"⏱ <b>Course #{course['id']}</b> — noté : 47 min (vers ")
    to_f1 = h.tg.last(F1).text
    assert to_f1.startswith("⏱ Livreur 1 sera chez ton client dans 47 min (vers ") and f"course #{course['id']}" in to_f1
    assert any(t.startswith(f"⏱ #{course['id']} — Livreur 1 y sera dans 47 min") for t in h.tg.texts(DISPATCH))
    c = await db.get_course(course["id"])
    assert c["eta_minutes"] == 47 and c["eta_at"]
    assert "⏱ Arrivée prévue vers " in h.tg.messages[(F1, c["franchise_message_id"])].text

    await h.text(L1, "1h10")                                          # retard : nouveau délai
    assert h.tg.last(F1).text.startswith("⏱ <b>Nouveau délai</b> — Livreur 1 sera chez ton client dans 1 h 10")
    assert (await db.get_course(course["id"]))["eta_minutes"] == 70

    await h.text(L2, "15")                                            # pas de course : message habituel
    assert h.tg.last(L2).text == texts.LIVREUR_TEXT_HINT

    await h.text(L1, "/depense")                                      # pendant une dépense, « 20 » est un montant
    n = len(h.tg.inbox(F1))
    await h.text(L1, "20")
    assert len(h.tg.inbox(F1)) == n and (await db.get_course(course["id"]))["eta_minutes"] == 70


async def test_change_course_address(h):
    """📍 Adresse : le franchisé corrige l'adresse d'une course prise ; le livreur reçoit la nouvelle adresse
    (message + fiche), le dispatch est prévenu, l'alerte « sur place » repart. Course en attente : les
    propositions ouvertes sont corrigées. Erreurs : adresse introuvable, hors zone, pas sa course."""
    f1, f2, (l1, l2) = await setup_network(h)
    await go_on_duty(h, L1, BASTILLE)
    course = await order(h, F1, "rivoli")
    await h.press(L1, h.tg.last(L1), "course_take:")
    card = h.tg.messages[(F1, (await db.get_course(course["id"]))["franchise_message_id"])]
    assert f"addr:{course['id']}" in [d for _, d in card.buttons]

    await h.press_data(L2, card, f"addr:{course['id']}")             # un autre livreur : non
    assert h.tg.answers()[-1].get("text") == texts.COURSE_FINISHED
    await h.press_data(F1, card, f"addr:{course['id']}")
    assert h.tg.last(F1).text.startswith(f"📍 <b>Course #{course['id']} — nouvelle adresse</b>")
    await h.text(F1, "99 rue qui n'existe pas")
    assert "Je n'arrive pas à localiser" in h.tg.last(F1).text
    await h.text(F1, "3 rue de la République, Lyon")
    assert "hors zone" in h.tg.last(F1).text
    await h.text(F1, "8 rue Oberkampf, 75011 Paris\ncode 7788, 3e étage")
    preview = h.tg.last(F1)
    assert "<b>Nouvelle : 8 Rue Oberkampf 75011 Paris</b>" in preview.text and "🔑 code 7788, 3e étage" in preview.text
    await h.press_data(F1, preview, "addr_ok")
    c = await db.get_course(course["id"])
    assert (c["address"], c["district"], c["address_detail"], c["status"], c["livreur_id"]) == (
        "8 Rue Oberkampf 75011 Paris", "Paris 11e", "code 7788, 3e étage", "assigned", l1["id"])
    alert = h.tg.find(L1, f"⚠️📍 <b>Nouvelle adresse — course #{course['id']}</b>").text
    assert "8 Rue Oberkampf 75011 Paris" in alert and "au lieu de : 12 Rue de Rivoli" in alert
    assert "8 Rue Oberkampf" in h.tg.messages[(L1, c["livreur_message_id"])].text      # fiche à jour
    assert any(t.startswith(f"📍 #{course['id']} — nouvelle adresse (par Franchisé 1)") for t in h.tg.texts(DISPATCH))
    await h.press_data(F1, preview, "addr_ok")                       # second appui : expiré
    assert h.tg.answers()[-1]["text"] == texts.ADDRESS_EXPIRED

    # Déjà « sur place » à l'ancienne adresse : l'alerte repart pour la nouvelle.
    await h.location(L1, c["lat"], c["lon"], edited=True, message_id=L1)
    assert any(t.startswith("🏁 Livreur 1 est sur place") for t in h.tg.texts(F1))

    # Course en attente : le dispatch change l'adresse depuis /encours ; la proposition est corrigée.
    await go_on_duty(h, L2, REPUBLIQUE)
    course2 = await order(h, F2, "rivoli")
    proposal = next((m for m in h.tg.inbox(L2) if f"#{course2['id']}" in m.text), None)
    assert proposal is not None and "Paris 4e" in proposal.text
    await h.press_data(DISPATCH, h.tg.last(F2), f"addr:{course2['id']}")
    await h.text(DISPATCH, "8 rue Oberkampf, 75011 Paris")
    await h.press_data(DISPATCH, h.tg.last(DISPATCH), "addr_ok")
    c2 = await db.get_course(course2["id"])
    assert c2["district"] == "Paris 11e" and c2["status"] == "pending"
    assert any(f"#{course2['id']} a été changée par Dispatch" in t for t in h.tg.texts(F2))
    fixed = h.tg.messages[(L2, proposal.message_id)]                 # proposition corrigée, bouton gardé
    assert "Paris 11e" in fixed.text and fixed.buttons[0][1] == f"course_take:{course2['id']}"


def _decl_id(msg) -> int:
    return int(msg.buttons[0][1].split(":")[1])


async def test_prenoms_shown_everywhere(h):
    """/prenom : liste, prénom fixé pour un livreur ; chaque message du bot écrit « Livreur 1 (Ketur) » ;
    le prénom suffit dans /ventes."""
    from bot.services import names as names_service

    await db.create_product("US", "us", [])
    f1, f2, (l1, l2) = await setup_network(h)
    await names_service.refresh()                      # au démarrage : prénoms de l'inscription
    assert names_service.label("Livreur 1") == "Livreur 1 (Livreur-3001)"

    await h.text(L1, "/prenom")
    assert h.tg.last(L1).text == texts.NOT_FOR_YOU
    await h.text(DISPATCH, "/prenom 1 Ketur")
    assert h.tg.last(DISPATCH).text == "✅ C'est noté : <b>Livreur 1</b> (Ketur)"
    await h.text(DISPATCH, "/prenom")
    listing = h.tg.last(DISPATCH).text
    assert "• Livreur 1 (Ketur)" in listing and "• Livreur 2 (Livreur-3002)" in listing
    await h.text(DISPATCH, "/prenom Z Paul")
    assert h.tg.last(DISPATCH).text == texts.PRENOM_HELP

    # Le prénom suffit pour désigner le livreur ; l'aperçu l'affiche entre parenthèses.
    await h.text(DISPATCH, "/ventes\nketur\n2 US 60")
    assert "<b>Livreur 1</b> (Ketur)\n💵 2 US" in h.tg.last(DISPATCH).text
    await h.press_data(DISPATCH, h.tg.last(DISPATCH), "sl_x")

    # Retour au prénom de l'inscription.
    await h.text(DISPATCH, "/prenom Livreur 1 -")
    assert names_service.label("Livreur 1") == "Livreur 1 (Livreur-3001)"


async def test_franchise_picks_livreur(h, test_config):
    """Le franchisé choisit son livreur : liste des livreurs en service (libres d'abord), envoi direct ;
    un livreur déjà en livraison reçoit quand même la course, le franchisé est prévenu, et elle lui est
    rappelée quand il valide celle en cours. « Au plus proche » et le délai sans choix : diffusion."""
    import dataclasses
    from types import SimpleNamespace

    from bot import config

    config.set_config(dataclasses.replace(test_config, franchise_picks=True, pick_timeout_minutes=5))
    f1, f2, (l1, l2) = await setup_network(h)
    await go_on_duty(h, L1, BASTILLE)
    await go_on_duty(h, L2, REPUBLIQUE)

    c1 = await order(h, F1, "rivoli")
    card = h.tg.find(F1, f"Course #{c1['id']} enregistrée — <b>à quel livreur")
    labels = [label for label, _ in card.buttons]
    assert labels[0].startswith("🟢 Livreur 1 · ~") and labels[1].startswith("🟢 Livreur 2 · ~")
    assert [d.split(":")[0] for _, d in card.buttons] == ["fp", "fp", "fp_auto", "course_withdraw"]
    assert await db.list_broadcasts(c1["id"]) == []                 # rien n'est diffusé
    await broadcast.kick_pending(h.context)                           # ni par une relance
    assert await db.list_broadcasts(c1["id"]) == []

    await h.press_data(F1, card, f"fp:{c1['id']}:{l1['id']}")
    assert (await db.get_course(c1["id"]))["livreur_id"] == l1["id"]
    fiche1 = h.tg.find(L1, f"🚴 Course #{c1['id']} — c'est pour toi")
    assert "📍 12 Rue de Rivoli" in fiche1.text
    assert f"Course #{c1['id']} — prise par Livreur 1" in h.tg.messages[(F1, card.message_id)].text

    # Deuxième course : Livreur 1 est en livraison, le franchisé le choisit quand même.
    c2 = await order(h, F2, "oberkampf")
    card2 = h.tg.find(F2, f"Course #{c2['id']} enregistrée")
    labels2 = [label for label, _ in card2.buttons]
    assert labels2[0].startswith("🟢 Livreur 2") and labels2[1] == "🛵 Livreur 1 · en livraison"   # libres d'abord
    await h.press_data(F2, card2, f"fp:{c2['id']}:{l1['id']}")
    assert (await db.get_course(c2["id"]))["livreur_id"] == l1["id"]
    assert h.tg.last(L1).text == texts.queued_for_livreur({"id": c2["id"]}, {"id": c1["id"]})
    assert h.tg.find(L1, f"🚴 Course #{c2['id']} — c'est pour toi")
    assert h.tg.last(F2).text == texts.livreur_busy_for_franchise({"id": c2["id"]}, l1, {"id": c1["id"]})

    # Il valide la première : la suivante lui est renvoyée, à faire maintenant.
    await deliver_course(h, L1, fiche1)
    reminder = h.tg.last(L1)
    assert reminder.text.startswith("🔔 <b>Course suivante, à faire maintenant</b>")
    assert f"🚴 Course #{c2['id']}" in reminder.text and "course_deliver" in reminder.buttons[0][1]
    assert (await db.get_course(c2["id"]))["livreur_message_id"] == reminder.message_id

    # « Au plus proche » : diffusion habituelle.
    c3 = await order(h, F1, "rivoli")
    card3 = h.tg.find(F1, f"Course #{c3['id']} enregistrée")
    await h.press_data(F1, card3, f"fp_auto:{c3['id']}")
    assert await db.list_broadcasts(c3["id"]) != []

    # Sans choix dans le délai : diffusion, le franchisé est prévenu.
    c4 = await order(h, F2, "oberkampf")
    assert await db.list_broadcasts(c4["id"]) == []
    await broadcast.pick_timeout(SimpleNamespace(bot=h.app.bot, job_queue=h.app.job_queue,
                                                 job=SimpleNamespace(data=c4["id"])))
    assert texts.pick_timeout(c4["id"]) in h.tg.texts(F2)
    assert await db.list_broadcasts(c4["id"]) != []


async def test_livreur_validates_with_ok_or_modif(h, monkeypatch):
    """« OK » valide la course en cours (espèces ; « OK CB » : virement) ; il faut la valider avant la
    suivante. « Modif » : l'éditeur, la modification part au franchisé, le paiement valide la livraison ;
    la feuille n'est écrite qu'après la décision du franchisé, qui a le dernier mot."""
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
    c1 = await order(h, F1, "rivoli")
    await h.press(L1, h.tg.find(L1, f"\nCourse #{c1['id']}"), "course_take:")
    # Une deuxième course pour lui pendant sa livraison (choisie par le franchisé).
    c2 = await order(h, F2, "oberkampf")
    won = await db.take_course(c2["id"], l1["id"])
    await lifecycle.after_assignment(h.context, won, await db.get_user(l1["id"]), None)
    fiche2 = h.tg.find(L1, f"🚴 Course #{c2['id']} — c'est pour toi")

    await h.text(L1, "ok merci")                                 # pas une validation
    assert h.tg.last(L1).text == texts.LIVREUR_TEXT_HINT
    await h.press_data(L1, fiche2, f"course_deliver:{c2['id']}")
    assert h.tg.answers()[-1]["text"] == texts.finish_first(c1["id"])

    await h.text(L1, "OK")
    done = await db.get_course(c1["id"])
    assert done["status"] == "delivered" and done["payment"] == "especes"
    assert texts.course_validated(done) in h.tg.texts(L1)
    assert h.tg.last(L1).text.startswith("🔔 <b>Course suivante, à faire maintenant</b>")
    await asyncio.gather(*list(sheets._tasks))
    assert [s["prix"] for s in sent] == [60.0]

    # « Modif » sur la course en cours : une vodka de plus, 80 € au lieu de 50.
    await h.text(L1, "modif")
    editor = h.tg.last(L1)
    assert "modifier la commande" in editor.text
    await h.press_data(L1, editor, "oe_q:0:1")
    await h.press_data(L1, h.tg.messages[(L1, editor.message_id)], "oe_s:0")
    await h.press_data(L1, h.tg.messages[(L1, editor.message_id)], "oe_p:0:30")
    await h.press_data(L1, h.tg.messages[(L1, editor.message_id)], "oe_ok")
    ask = h.tg.messages[(L1, editor.message_id)]
    assert ask.text.startswith(f"✏️ Modification de la course #{c2['id']} envoyée à Franchisé 2")
    assert [d for _, d in ask.buttons][:2] == [f"pay:{c2['id']}:e", f"pay:{c2['id']}:v"]
    await h.press_data(L1, ask, f"pay:{c2['id']}:v")
    delivered = await db.get_course(c2["id"])
    assert delivered["status"] == "delivered" and delivered["payment"] == "virement"
    assert float(delivered["price"]) == 50 and delivered["pending_edit"]["price"] == 80
    await asyncio.gather(*list(sheets._tasks))
    assert len(sent) == 1                                        # la feuille attend la décision

    # Le franchisé refuse : la commande reste comme avant, et part dans la feuille.
    request = h.tg.find(F2, "a modifié la course")
    await h.press_data(F2, request, f"me:{c2['id']}:no")
    final = await db.get_course(c2["id"])
    assert float(final["price"]) == 50 and final["pending_edit"] is None
    assert h.tg.last(L1).text == texts.edit_decided(c2["id"], False, {}, "Franchisé 2")
    await asyncio.gather(*list(sheets._tasks))
    assert [s["prix"] for s in sent] == [60.0, 50.0]

    await h.text(L1, "OK CB")
    assert h.tg.last(L1).text == texts.NO_ASSIGNED


async def test_franchise_reminded_of_pending_edit(h):
    """Une « Modif » sans réponse : le franchisé est relancé à 10 min, 40 min et 1 h 10 (boutons ✅ / ❌),
    une seule fois à chaque étape ; au dernier rappel le dispatch est prévenu ; plus rien après la décision."""
    from bot import jobs
    from bot.timeutil import iso

    f1, f2, (l1,) = await setup_network(h, livreurs=(L1,))
    await go_on_duty(h, L1, BASTILLE)
    c = await order(h, F1, "oberkampf")
    await h.press(L1, h.tg.find(L1, f"\nCourse #{c['id']}"), "course_take:")
    await h.text(L1, "Modif")
    editor = h.tg.last(L1)
    await h.press_data(L1, editor, "oe_q:0:1")
    await h.press_data(L1, h.tg.messages[(L1, editor.message_id)], "oe_s:0")
    await h.press_data(L1, h.tg.messages[(L1, editor.message_id)], "oe_p:0:30")
    await h.press_data(L1, h.tg.messages[(L1, editor.message_id)], "oe_ok")
    pending = (await db.get_course(c["id"]))["pending_edit"]

    async def waited(minutes):
        at = iso(now_utc() - timedelta(minutes=minutes))
        await db.update_course(c["id"], {"pending_edit": {**pending, "at": at}})
        for e in await db.list_events(c["id"], "edit_reminder"):     # les rappels suivent la même modification
            await db._t("events").update({"payload": {**e["payload"], "at": at}}).eq("id", e["id"]).execute()
        await jobs.edit_reminders(h.context)

    def reminders():
        return [t for t in h.tg.texts(F1) if t.startswith("⏰ <b>Rappel : modification")]

    await waited(5)
    assert reminders() == []
    await waited(12)
    assert len(reminders()) == 1 and "en attente depuis 12 min" in reminders()[0]
    assert [d for _, d in h.tg.last(F1).buttons] == [f"me:{c['id']}:ok", f"me:{c['id']}:no"]
    await waited(20)
    assert len(reminders()) == 1                                    # pas deux fois la même étape
    await waited(41)
    assert len(reminders()) == 2 and not [t for t in h.tg.texts(DISPATCH) if "sans réponse" in t]
    await waited(71)
    assert len(reminders()) == 3 and "en attente depuis 1 h 11" in reminders()[-1]
    assert [t for t in h.tg.texts(DISPATCH) if f"#{c['id']} — modification du livreur sans réponse" in t]
    await waited(200)
    assert len(reminders()) == 3                                    # on n'insiste plus

    # Le franchisé décide depuis le rappel : plus de rappel ensuite.
    await h.press_data(F1, h.tg.last(F1), f"me:{c['id']}:ok")
    assert float((await db.get_course(c["id"]))["price"]) == 80
    assert len(reminders()) == 2                                    # le rappel utilisé affiche la décision
    await jobs.edit_reminders(h.context)
    assert len(reminders()) == 2 and len(await db.list_events(c["id"], "edit_reminder")) == 3


async def test_flavors_end_to_end(h, monkeypatch, test_config):
    """/gouts : le dispatch donne les goûts de MSX ; /ravi « 12 MSX banane fraise » les coche chez le livreur
    (la feuille ne voit que 12 MSX) ; le livreur décoche ; la liste des livreurs proposée pour une commande
    « 1 MSX banane » dit qui en a (et le met en premier)."""
    import asyncio
    import dataclasses

    from bot import config
    from bot.services import sheets

    monkeypatch.setenv("GOOGLE_SHEETS_WEBHOOK_URL", "https://script.google.com/macros/s/x/exec")
    monkeypatch.setenv("GOOGLE_SHEETS_SECRET", "s")
    sent = []

    async def fake_send(rows, client=None):
        sent.extend(rows)
        return len(rows)

    monkeypatch.setattr(sheets, "send_rows", fake_send)
    monkeypatch.setitem(EXTRACTIONS, "msx banane", [{**RIVOLI, "products": "1 MSX banane", "price": 30}])
    await db.create_product("MSX", "msx", [])
    await db.create_product("US", "us", [])
    f1, f2, (l1, l2) = await setup_network(h)
    await register(h, R1, "ravitailleur", "Sam")

    await h.text(DISPATCH, "/gouts MSX noisette, fraise, banane")
    assert h.tg.last(DISPATCH).text == texts.variants_set("MSX", ["noisette", "fraise", "banane"])
    await h.text(L1, "/gouts MSX kiwi")                         # un livreur ne change pas la liste
    assert "Tes goûts" in h.tg.last(L1).text

    await h.text(R1, "/ravi\nLivreur 1\nBox 1\n12 MSX banane fraise\n2 US")
    preview = h.tg.last(R1)
    assert "📦 +12 MSX (fraise, banane) · +2 US" in preview.text
    await h.press_data(R1, preview, "rv_ok")
    await asyncio.gather(*list(sheets._tasks))
    assert sent[-1]["produits"] == {"MSX": 12, "US": 2}           # la compta ne voit que du MSX
    rows = await db.list_livreur_variants("Livreur 1")
    assert sorted(r["variant"] for r in rows) == ["banane", "fraise"]
    assert "+12 MSX (fraise, banane)" in h.tg.find(L1, "Chargement reçu").text

    # Le livreur n'a plus de banane : il décoche.
    await h.text(L1, "/gouts")
    menu = h.tg.last(L1)
    assert [b for b, _ in menu.buttons] == ["▫️ MSX noisette", "✅ MSX fraise", "✅ MSX banane", "✔️ Terminé"]
    await h.press_data(L1, menu, dict((b, d) for b, d in menu.buttons)["✅ MSX banane"])
    assert [b for b, _ in h.tg.messages[(L1, menu.message_id)].buttons][2] == "▫️ MSX banane"
    await db.add_livreur_variants("Livreur 2", "MSX", ["banane"])

    await h.text(DISPATCH, "/gouts")
    overview = h.tg.last(DISPATCH).text
    assert "<b>MSX</b> : noisette, fraise, banane" in overview
    assert "• Livreur 1 : fraise" in overview and "• Livreur 2 : banane" in overview

    # Commande « 1 MSX banane » : Livreur 2 en a (en premier, même plus loin), Livreur 1 non.
    config.set_config(dataclasses.replace(test_config, franchise_picks=True))
    await go_on_duty(h, L1, BASTILLE)
    await go_on_duty(h, L2, REPUBLIQUE)
    c = await order(h, F1, "msx banane")
    labels = [b for b, _ in h.tg.find(F1, f"Course #{c['id']} enregistrée").buttons]
    assert labels[0].startswith("🟢 Livreur 2") and labels[0].endswith("· ✅banane")
    assert labels[1].startswith("🟢 Livreur 1") and labels[1].endswith("· ❌banane")

    # /stock : les goûts sous le produit, seulement s'il lui en reste.
    text = texts.livreurs_stock({"Livreur 1": {"MSX": 12}, "Livreur 2": {"US": 1}},
                                {"Livreur 1": {"MSX": {"fraise"}}, "Livreur 2": {"MSX": {"banane"}}})
    assert "<b>Livreur 1</b> : MSX <b>12</b>\n   🍬 MSX : fraise" in text and "🍬 MSX : banane" not in text

    # La liste de MSX change : un goût retiré disparaît aussi chez les livreurs.
    await h.text(DISPATCH, "/gouts MSX noisette, banane")
    assert [r["variant"] for r in await db.list_livreur_variants("Livreur 1")] == []



async def test_dispatch_orders_like_a_franchise(h):
    """Le dispatch (grand admin) passe une commande comme un franchisé : fiche, confirmation, livreur ;
    il échange avec le livreur par 💬 (sans copie à lui-même) et peut retirer sa course."""
    f1, f2, (l1,) = await setup_network(h, livreurs=(L1,))
    await go_on_duty(h, L1, BASTILLE)
    await h.text(DISPATCH, "rivoli")
    card = h.tg.last(DISPATCH)
    assert "draft_confirm:" in card.buttons[0][1]
    await h.press(DISPATCH, card, "draft_confirm:")
    course = (await db.list_courses_by_status("pending"))[-1]
    assert course["franchise_id"] == (await db.get_user_by_tg(DISPATCH))["id"]
    assert not [t for t in h.tg.texts(DISPATCH) if t.startswith(f"🆕 #{course['id']}")]   # pas d'avis à lui-même
    await h.press(L1, h.tg.find(L1, f"\nCourse #{course['id']}"), "course_take:")
    fiche = h.tg.find(L1, "c'est pour toi")
    assert "Dispatch" in fiche.text

    await h.press(L1, fiche, "relay_start:")
    await h.text(L1, "J'arrive")
    assert h.tg.last(DISPATCH).text.endswith("Livreur 1 : « J'arrive »")
    assert not [t for t in h.tg.texts(DISPATCH) if t.startswith(f"💬 #{course['id']} —")]

    await h.text(DISPATCH, "/mescourses")
    assert f"#{course['id']}" in h.tg.last(DISPATCH).text


async def test_catalog_edit_product(h):
    """/produits : ✏️ à côté de 🗑. On renvoie « Nom : surnoms » (tout est remplacé) ou « + surnom »
    (ajout) ; un nom déjà pris est refusé, le bot attend une autre ligne ; Annuler sort."""
    await h.text(DISPATCH, "/start")
    msx = await db.create_product("MSX", "msx", ["mx"])
    await db.create_product("DIV", "div", [])
    await h.text(DISPATCH, "/produits")
    menu = h.tg.last(DISPATCH)
    assert ("✏️ MSX", f"prod_edit:{msx['id']}") in menu.buttons and ("🗑", f"prod_del:{msx['id']}") in menu.buttons

    await h.press_data(DISPATCH, menu, f"prod_edit:{msx['id']}")
    assert "<code>MSX : mx</code>" in h.tg.last(DISPATCH).text
    await h.text(DISPATCH, "DIV : divin")                          # nom déjà pris : refusé
    assert h.tg.last(DISPATCH).text.startswith("⚠️ Ce nom est déjà celui de : DIV")
    await h.text(DISPATCH, "MSX : masterx, mx")                     # tout remplacé
    p = await db.get_product(msx["id"])
    assert (p["name"], p["aliases"]) == ("MSX", ["masterx", "mx"])
    assert "✅ <b>MSX</b> — <i>masterx, mx</i>" in h.tg.texts(DISPATCH)

    await h.press_data(DISPATCH, h.tg.last(DISPATCH), f"prod_edit:{msx['id']}")
    await h.text(DISPATCH, "+ msix, div")                          # ajout ; « div » désigne déjà DIV
    p = await db.get_product(msx["id"])
    assert p["aliases"] == ["masterx", "mx", "msix", "div"]
    assert any("« div » désigne aussi : DIV" in t for t in h.tg.texts(DISPATCH))

    # Renommer : les goûts cochés chez les livreurs suivent.
    await db.add_livreur_variants("Livreur A", "MSX", ["banane"])
    await h.press_data(DISPATCH, h.tg.last(DISPATCH), f"prod_edit:{msx['id']}")
    await h.text(DISPATCH, "MSX2 : mx")
    assert (await db.get_product(msx["id"]))["name"] == "MSX2"
    assert [r["product"] for r in await db.list_livreur_variants("Livreur A")] == ["MSX2"]

    await h.press_data(DISPATCH, h.tg.last(DISPATCH), f"prod_edit:{msx['id']}")
    await h.press_data(DISPATCH, h.tg.last(DISPATCH), "prod_cancel")
    assert (await db.get_user_by_tg(DISPATCH))["conversation_state"] is None


async def test_livreur_menu_buttons(h):
    """Le livreur a un menu de gros boutons en bas de l'écran (dès la validation) : 🟢 Je commence,
    ✅ Livrée (puis le paiement), ⏸ Pause… sans rien taper."""
    f1, f2, (l1,) = await setup_network(h, livreurs=(L1,))
    welcome = h.tg.find(L1, "Tu es validé")
    assert [[b["text"] for b in row] for row in welcome.markup["keyboard"]] == [
        ["🟢 Je commence", "⏸ Pause"], ["✅ Livrée", "✏️ Modif"], ["🚴 Ma course", "💶 Ma caisse"],
        ["🍬 Mes goûts", "🧾 Dépense"], ["🏁 Fin de service"]]

    await h.text(L1, "✅ Livrée")                                 # rien en cours
    assert h.tg.last(L1).text == texts.NO_ASSIGNED
    await h.text(L1, "🟢 Je commence")                             # = /dispo
    assert h.tg.texts(L1)[-2:] == [texts.DISPO_PROMPT, texts.TRANSPORT_QUESTION]
    await h.location(L1, *BASTILLE, message_id=L1)
    c = await order(h, F1, "rivoli")
    await h.press(L1, h.tg.find(L1, f"\nCourse #{c['id']}"), "course_take:")

    await h.text(L1, "✅ Livrée")
    ask = h.tg.last(L1)
    assert ask.text.startswith(f"✅ Course #{c['id']} — 12 Rue de Rivoli") and "Le client a payé comment ?" in ask.text
    await h.press_data(L1, ask, f"pay:{c['id']}:v")
    done = await db.get_course(c["id"])
    assert done["status"] == "delivered" and done["payment"] == "virement"

    await h.text(L1, "⏸ Pause")
    assert h.tg.last(L1).text == texts.PAUSED
    assert (await db.get_user(l1["id"]))["on_duty"] is False



async def test_close_livreur_end_of_service(h, monkeypatch):
    """/close Livreur 1 (admin) ou 🏁 Fin de service (le livreur) : il passe hors service, et le récap donne
    ses courses livrées, ce qui est encore en cours, le stock encore sur lui et le cash à récupérer."""
    import asyncio

    from bot.services import sheets

    monkeypatch.setenv("GOOGLE_SHEETS_WEBHOOK_URL", "https://script.google.com/macros/s/x/exec")
    monkeypatch.setenv("GOOGLE_SHEETS_SECRET", "s")

    async def fake_send(rows, client=None):
        return len(rows)

    async def fake_fetch(action, *args, **kw):
        if action == "stock_livreurs":
            return {"ok": True, "livreurs": {"Livreur 1": {"DIV": 3, "US": 0}}}
        if action == "cash_livreurs":
            return {"ok": True, "livreurs": {"Livreur 1": {"especes": 60, "depenses": 0, "recupere": 0, "cash": 60}}}
        return {"ok": False, "error": "?"}

    monkeypatch.setattr(sheets, "send_rows", fake_send)
    monkeypatch.setattr(sheets, "fetch_action", fake_fetch)
    f1, f2, (l1, l2) = await setup_network(h)
    await go_on_duty(h, L1, BASTILLE)
    c = await order(h, F1, "rivoli")
    await h.press(L1, h.tg.find(L1, f"\nCourse #{c['id']}"), "course_take:")
    await deliver_course(h, L1, h.tg.find(L1, "c'est pour toi"), pay="e")
    await asyncio.gather(*list(sheets._tasks))

    await h.text(DISPATCH, "/close Zorro")
    assert h.tg.last(DISPATCH).text == texts.close_livreur_unknown("Zorro")

    await h.text(DISPATCH, "/close Livreur 1")
    text = h.tg.last(DISPATCH).text.replace("\xa0", " ")
    assert text.startswith("🏁 <b>Fin de service — Livreur 1</b>\nNuit du")
    assert "📦 <b>1 course livrée — 60 €</b> (💵 60 €)" in text
    assert f" · #{c['id']} · Franchisé 1 · Paris 4e · 2 vodka + coca · 60 € 💵" in text
    assert "🎒 <b>Stock encore sur lui</b>\nDIV <b>3</b>" in text and "US" not in text.split("Stock")[1]
    assert "<b>= À récupérer : 60 €</b>" in text
    assert (await db.get_user(l1["id"]))["on_duty"] is False
    assert any(t.endswith(texts.CLOSE_FOR_LIVREUR) for t in h.tg.texts(L1))

    # Le livreur termine lui-même son service depuis son menu ; rien n'a été livré par Livreur 2.
    await go_on_duty(h, L2, REPUBLIQUE)
    await h.text(L2, "🏁 Fin de service")
    mine = h.tg.find(L2, "🏁 <b>Fin de service — Livreur 2</b>").text
    assert mine.startswith("🏁 <b>Fin de service — Livreur 2</b>") and "Aucune course livrée cette nuit." in mine
    assert (await db.get_user(l2["id"]))["on_duty"] is False
    assert any(t.startswith("🏁 <b>Fin de service — Livreur 2</b>") for t in h.tg.texts(DISPATCH))

    await h.text(DISPATCH, "/close")                                # sans nom : débrief de la journée
    assert h.tg.last(DISPATCH).text.startswith("🔒 <b>Journée close")


async def test_user_card_edit_from_users(h):
    """/users : chaque personne est un bouton ; sa fiche change nom, prénom, service, rôle."""
    f1, f2, (l1, l2) = await setup_network(h)
    await h.text(DISPATCH, "/users")
    listing = h.tg.last(DISPATCH)
    assert "touche une personne" in listing.text
    await h.press_data(DISPATCH, listing, f"uc:{f2['id']}")
    card = h.tg.messages[(DISPATCH, listing.message_id)]
    assert card.text.startswith("👤 <b>Franchisé 2</b>") and "Nom à l'inscription : Nassim" in card.text

    # Nom du franchisé
    await h.press_data(DISPATCH, card, f"uc_edit:{f2['id']}:name")
    assert "Nouveau nom pour Franchisé 2" in h.tg.last(DISPATCH).text
    await h.text(DISPATCH, "Le Kiosque")
    assert (await db.get_user(f2["id"]))["display_name"] == "Le Kiosque"
    assert h.tg.last(DISPATCH).text.startswith("👤 <b>Le Kiosque</b>")

    # Prénom du livreur, puis service / pause
    await h.press_data(DISPATCH, h.tg.last(DISPATCH), f"uc:{l1['id']}")
    card = h.tg.last(DISPATCH)
    assert "Prénom : —" in card.text and "⏸ en pause" in card.text
    await h.press_data(DISPATCH, card, f"uc_edit:{l1['id']}:prenom")
    await h.text(DISPATCH, "Karim")
    assert "Prénom : Karim" in h.tg.last(DISPATCH).text
    card = h.tg.last(DISPATCH)
    await h.press_data(DISPATCH, card, f"uc_duty:{l1['id']}:on")
    assert (await db.get_user(l1["id"]))["on_duty"] is True
    assert "🟢 en service" in h.tg.messages[(DISPATCH, card.message_id)].text
    assert "service" in h.tg.texts(L1)[-1]
    await h.press_data(DISPATCH, card, f"uc_duty:{l1['id']}:off")
    assert (await db.get_user(l1["id"]))["on_duty"] is False

    # Rôle : le livreur 2 devient ravitailleur (prévenu, nom de feuille proposé)
    await h.press_data(DISPATCH, card, f"uc:{l2['id']}")
    await h.press_data(DISPATCH, card, f"uc_role:{l2['id']}")
    assert "Rôle de Livreur 2" in h.tg.messages[(DISPATCH, card.message_id)].text
    await h.press_data(DISPATCH, card, f"uc_role_set:{l2['id']}:ravitailleur")
    l2 = await db.get_user(l2["id"])
    assert l2["role"] == "ravitailleur" and l2["display_name"].startswith("Ravitailleur")
    assert any("Ton rôle a changé" in t for t in h.tg.texts(L2))
    assert "Rôle : Ravitailleur" in h.tg.messages[(DISPATCH, card.message_id)].text

    # Sa propre fiche n'existe pas (dispatch exclu) ; un franchisé admin ne peut pas se modifier lui-même
    await h.text(F1, "/users")
    await h.press_data(F1, h.tg.last(F1), f"uc:{f1['id']}")
    mine = h.tg.last(F1)
    assert [b["callback_data"] for row in mine.markup["inline_keyboard"] for b in row] == ["uc_list"]
    await h.press_data(F1, mine, f"uc_edit:{f1['id']}:name")
    assert h.tg.answers()[-1].get("text") == texts.USER_SELF

    await h.press_data(F1, mine, "uc_list")
    assert "touche une personne" in h.tg.messages[(F1, mine.message_id)].text


async def test_admin_menu_by_chapters(h):
    """/menu : six chapitres ; un bouton lance la commande comme si elle était tapée."""
    from bot.handlers import menu as menu_h

    for _, items in menu_h.CHAPTERS.values():       # chaque bouton a sa commande
        for cmd, _, _ in items:
            assert cmd.startswith("@") or cmd in menu_h._runners()

    await setup_network(h, livreurs=(L1,))
    await h.text(DISPATCH, "/menu")
    home = h.tg.last(DISPATCH)
    assert home.text == texts.MENU_HOME
    assert len([b for row in home.markup["inline_keyboard"] for b in row]) == 6

    await h.press_data(DISPATCH, home, "mn:users")
    chapter = h.tg.messages[(DISPATCH, home.message_id)]
    assert "👥 Utilisateurs" in chapter.text and "/bannir" in chapter.text
    await h.press_data(DISPATCH, chapter, "mn_run:users")
    assert "touche une personne" in h.tg.last(DISPATCH).text

    await h.press_data(DISPATCH, chapter, "mn:livreurs")
    await h.press_data(DISPATCH, chapter, "mn_run:livreurs")
    assert "Livreur 1" in h.tg.last(DISPATCH).text

    await h.press_data(DISPATCH, chapter, "mn")
    assert h.tg.messages[(DISPATCH, home.message_id)].text == texts.MENU_HOME

    await h.text(F1, "/menu")                        # franchisé : admin aussi
    assert h.tg.last(F1).text == texts.MENU_HOME
    await h.text(L1, "/menu")
    assert h.tg.last(L1).text == texts.NOT_FOR_YOU
    n = len(h.tg.inbox(L1))
    await h.press_data(L1, home, "mn_run:users")     # bouton d'admin pressé par un livreur : rien
    assert len(h.tg.inbox(L1)) == n


async def test_admin_menu_every_button_runs(h):
    from bot.handlers import menu as menu_h

    await setup_network(h, livreurs=(L1,))
    await h.text(DISPATCH, "/menu")
    home = h.tg.last(DISPATCH)
    for _, items in menu_h.CHAPTERS.values():
        for cmd, _, _ in items:
            if cmd.startswith("@") or cmd == "synchro":
                continue
            before = len(h.tg.inbox(DISPATCH))
            await h.press_data(DISPATCH, home, f"mn_run:{cmd}")
            assert len(h.tg.inbox(DISPATCH)) > before, cmd
            assert not any("Erreur technique" in t for t in h.tg.texts(DISPATCH)), cmd
            await h.text(DISPATCH, "/aide")              # sort d'une éventuelle saisie en cours


async def test_course_card_change_livreur_and_time(h):
    """Fiche course unique : /encours → fiche ; 🔄 Changer de livreur sur une course prise ; 🕐 Heure."""
    f1, f2, (l1, l2) = await setup_network(h)
    await go_on_duty(h, L1, BASTILLE)
    course = await order(h, F1, "rivoli")
    await h.press(L1, h.tg.last(L1), "course_take:")
    await go_on_duty(h, L2, REPUBLIQUE)
    fiche_l1 = h.tg.find(L1, f"Course #{course['id']} — c'est pour toi")

    # /encours : un bouton par course → la même fiche que chez le franchisé
    await h.text(DISPATCH, "/encours")
    await h.press_data(DISPATCH, h.tg.last(DISPATCH), f"cc:{course['id']}")
    card = h.tg.last(DISPATCH)
    assert card.text.startswith(f"🚴 Course #{course['id']} — prise par Livreur 1") and "🏪 Franchisé 1" in card.text
    data = [d.split(":")[0] for _, d in card.buttons]
    assert data == ["force_deliver", "order_edit", "addr", "ct", "assign", "force_cancel"]

    # 🔄 Changer de livreur : la liste ne propose pas le livreur actuel, et permet de rediffuser
    await h.press_data(DISPATCH, card, f"assign:{course['id']}")
    picker = h.tg.last(DISPATCH)
    assert "Changer de livreur" in picker.text and "aujourd'hui : Livreur 1" in picker.text
    targets = [d for _, d in picker.buttons]
    assert f"assign_to:{course['id']}:{l1['id']}" not in targets and f"force_release:{course['id']}" in targets
    await h.press_data(DISPATCH, picker, f"assign_to:{course['id']}:{l2['id']}")
    moved = await db.get_course(course["id"])
    assert moved["status"] == "assigned" and moved["livreur_id"] == l2["id"]
    assert "donnée à Livreur 2 (au lieu de Livreur 1)" in h.tg.messages[(DISPATCH, picker.message_id)].text
    assert any("donnée à un autre livreur" in t for t in h.tg.texts(L1))
    assert not h.tg.messages[(L1, fiche_l1.message_id)].markup          # ancienne fiche sans boutons
    h.tg.find(L2, f"Course #{course['id']} — c'est pour toi")
    assert "prise par Livreur 2" in h.tg.messages[(F1, (await db.get_course(course["id"]))["franchise_message_id"])].text

    # 🕐 Heure souhaitée, par le franchisé sur son message de course
    fmsg = h.tg.messages[(F1, moved["franchise_message_id"])]
    await h.press(F1, fmsg, "ct:")
    assert "Heure souhaitée pour la course" in h.tg.last(F1).text
    await h.text(F1, "vers 23h30")
    assert (await db.get_course(course["id"]))["requested_time"] == "vers 23h30"
    assert any("heure souhaitée : vers 23h30 ✅" in t for t in h.tg.texts(F1))
    assert any("heure souhaitée : <b>vers 23h30</b>" in t for t in h.tg.texts(L2))
    assert "🕐 vers 23h30" in h.tg.find(L2, f"Course #{course['id']} — c'est pour toi").text
    assert any("heure souhaitée : <b>vers 23h30</b>" in t for t in h.tg.texts(DISPATCH))
    assert "🕐 vers 23h30" in h.tg.messages[(F1, (await db.get_course(course["id"]))["franchise_message_id"])].text

    # « - » enlève l'heure
    await h.press(F1, h.tg.messages[(F1, (await db.get_course(course["id"]))["franchise_message_id"])], "ct:")
    await h.text(F1, "-")
    assert (await db.get_course(course["id"]))["requested_time"] is None


async def test_ravi_asks_forgotten_livreur_box_and_ravitailleur(h, monkeypatch):
    """/ravi : ravitailleur, livreur ou box oublié → le bot propose des boutons au lieu d'annuler."""
    import asyncio

    from bot.services import sheets

    monkeypatch.setenv("GOOGLE_SHEETS_WEBHOOK_URL", "https://script.google.com/macros/s/x/exec")
    monkeypatch.setenv("GOOGLE_SHEETS_SECRET", "s")

    async def fake_send(rows, client=None):
        return len(rows)

    monkeypatch.setattr(sheets, "send_rows", fake_send)
    for name in ("DIV", "US"):
        await db.create_product(name, name.lower(), [])
    f1, f2, (l1, l2) = await setup_network(h)
    r1 = await register(h, R1, "ravitailleur", "Sam")

    # Dispatch : ni ravitailleur, ni livreur, ni box.
    await h.text(DISPATCH, "/ravi\n12 DIV\ncash 40")
    ask = h.tg.last(DISPATCH)
    assert ask.text == texts.RAVI_ASK_WHO and ask.buttons[0] == ("Ravitailleur 1", "rvf:r:0")
    await h.press_data(DISPATCH, ask, "rvf:r:0")
    ask = h.tg.last(DISPATCH)
    assert "pour quel livreur" in ask.text and "+12 DIV" in ask.text
    assert [t for t, _ in ask.buttons] == ["Livreur 1", "Livreur 2", "❌ Annuler"]
    await h.press_data(DISPATCH, ask, "rvf:l:0:1")
    ask = h.tg.messages[(DISPATCH, ask.message_id)]
    assert "quel box pour Livreur 2" in ask.text
    await h.press_data(DISPATCH, ask, "rvf:b:0:1")
    preview = h.tg.messages[(DISPATCH, ask.message_id)]
    assert "<b>Livreur 2</b> · Box 2" in preview.text and [d for _, d in preview.buttons] == ["rv_ok", "rv_x"]
    await h.press_data(DISPATCH, preview, "rv_ok")
    await asyncio.gather(*list(sheets._tasks))
    row = (await db._t("restocks").select("*").order("id").execute()).data[0]
    assert (row["livreur_id"], row["box"], row["by_user_id"], float(row["cash"])) == (l2["id"], "Box 2", r1["id"], 40.0)

    # Le box choisi vaut aussi pour le livreur suivant ; ❌ annule sans rien enregistrer.
    await h.text(R1, "/ravi\nLivreur 1\n2 US\nLivreur 2\n3 US")
    ask = h.tg.last(R1)
    await h.press_data(R1, ask, "rvf:b:0:0")
    preview = h.tg.messages[(R1, ask.message_id)]
    assert "<b>Livreur 1</b> · Box 1" in preview.text and "<b>Livreur 2</b> · Box 1" in preview.text
    await h.text(R1, "/ravi\n4 US")
    ask = h.tg.last(R1)
    await h.press_data(R1, ask, "rvf:x")
    assert h.tg.messages[(R1, ask.message_id)].text == texts.RAVI_CANCELLED
    assert len((await db._t("restocks").select("id").execute()).data) == 1


async def test_franchises_get_every_notification(h, test_config):
    """Tous les franchisés reçoivent les avis du dispatch, même pour une course qui n'est pas la leur ;
    pas en double pour celui de la course, pas les messages privés relayés."""
    import dataclasses

    from bot import config

    config.set_config(dataclasses.replace(test_config, notify_franchises=True))
    f1, f2, (l1,) = await setup_network(h, livreurs=(L1,))
    await go_on_duty(h, L1, BASTILLE)
    course = await order(h, F1, "rivoli")
    assert any(t.startswith(f"🆕 #{course['id']}") or f"#{course['id']}" in t for t in h.tg.texts(F2))
    n1 = len(h.tg.inbox(F1))
    await h.press(L1, h.tg.last(L1), "course_take:")
    taken = [t for t in h.tg.texts(F2) if f"#{course['id']}" in t and "Livreur 1" in t]
    assert taken                                            # F2 sait que Livreur 1 a pris la course de F1
    assert len([m for m in h.tg.inbox(F1)[n1:] if "Livreur 1" in m.text]) == 1   # F1 : son seul message
    # Message privé relayé entre F1 et le livreur : pas copié à F2.
    before = len(h.tg.inbox(F2))
    await h.press(F1, h.tg.find(F1, f"Course #{course['id']}"), "relay_start:")
    await h.text(F1, "Code 1234")
    assert not any("Code 1234" in m.text for m in h.tg.inbox(F2)[before:])
    # Livrée par le dispatch : les franchisés sont prévenus.
    await h.press_data(DISPATCH, h.tg.last(DISPATCH), f"fy:deliver:{course['id']}:e")
    assert any(f"#{course['id']}" in t and "livr" in t for t in h.tg.texts(F2)[-3:])


# ================================================================ mini-application

def _init_data(tg_id: int, token: str = "123:test", age: int = 0) -> str:
    import hashlib
    import hmac
    import json
    import time
    from urllib.parse import urlencode

    fields = {"auth_date": str(int(time.time()) - age), "query_id": "q1",
              "user": json.dumps({"id": tg_id, "first_name": "X"}, separators=(",", ":"))}
    check = "\n".join(f"{k}={v}" for k, v in sorted(fields.items()))
    secret = hmac.new(b"WebAppData", token.encode(), hashlib.sha256).digest()
    fields["hash"] = hmac.new(secret, check.encode(), hashlib.sha256).hexdigest()
    return urlencode(fields)


def test_webapp_init_data_signature():
    from bot import web

    assert web.check_init_data(_init_data(42), "123:test")["id"] == 42
    assert web.check_init_data(_init_data(42), "999:autre") is None                # autre bot
    assert web.check_init_data(_init_data(42, age=2 * 86400), "123:test") is None  # trop vieux
    assert web.check_init_data(_init_data(42).replace("q1", "q2"), "123:test") is None   # modifié
    assert web.check_init_data("", "123:test") is None


async def test_webapp_board_and_actions(h):
    """L'appli : chaque livreur sur la route n'a qu'une adresse en cours (les autres « à suivre ») avec son
    heure d'arrivée ; actions = mêmes effets que les boutons du chat ; réservée aux admins."""
    from aiohttp.test_utils import TestClient, TestServer

    from bot import web

    f1, f2, (l1, l2) = await setup_network(h)
    await go_on_duty(h, L1, BASTILLE)
    c1 = await order(h, F1, "rivoli")
    await h.press(L1, h.tg.last(L1), "course_take:")
    await h.text(L1, "47")                                     # il écrit son temps
    c2 = await order(h, F2, "oberkampf")
    await h.press(L1, h.tg.find(L1, f"\nCourse #{c2['id']}"), "course_take:")
    c3 = await order(h, F1, "rivoli")

    async with TestClient(TestServer(web.build(h.app))) as client:
        async def post(path, tg_id, body=None):
            return await client.post(path, json=body or {}, headers={"X-Init-Data": _init_data(tg_id)})

        assert (await client.post("/api/board", json={})).status == 401
        assert (await post("/api/board", L1)).status == 403                   # un livreur n'y a pas accès
        assert (await client.get("/")).status == 200

        board = await (await post("/api/board", DISPATCH)).json()
        assert [c["id"] for c in board["pending"]] == [c3["id"]]
        route = next(r for r in board["routes"] if r["livreur"]["id"] == l1["id"])
        assert route["current"]["id"] == c1["id"] and [q["id"] for q in route["queue"]] == [c2["id"]]
        assert route["arrival"]["source"] == "livreur" and 45 <= route["arrival"]["in"] <= 47
        assert board["me"]["dispatch"] is True and board["chapters"]

        # Franchisé : même appli ; attribue la 3e au livreur 2 (en pause : l'admin force).
        r = await (await post("/api/action", F2, {"type": "assign", "course_id": c3["id"], "livreur_id": l2["id"]})).json()
        assert r["ok"] and (await db.get_course(c3["id"]))["livreur_id"] == l2["id"]
        h.tg.find(L2, f"Course #{c3['id']} — c'est pour toi")
        r = await (await post("/api/action", DISPATCH, {"type": "time", "course_id": c2["id"], "value": "vers 23h"})).json()
        assert r["ok"] and (await db.get_course(c2["id"]))["requested_time"] == "vers 23h"
        r = await (await post("/api/action", DISPATCH, {"type": "duty", "livreur_id": l2["id"], "on": True})).json()
        assert r["ok"] and (await db.get_user(l2["id"]))["on_duty"] is True
        r = await (await post("/api/action", DISPATCH, {"type": "deliver", "course_id": c1["id"], "payment": "e"})).json()
        assert r["ok"] and (await db.get_course(c1["id"]))["status"] == "delivered"
        r = await (await post("/api/action", DISPATCH, {"type": "cancel", "course_id": c2["id"]})).json()
        assert r["ok"] and (await db.get_course(c2["id"]))["status"] in db.CANCELLED_STATUSES
        r = await (await post("/api/action", F1, {"type": "run", "cmd": "reset"})).json()
        assert not r["ok"] and r["message"] == texts.DISPATCH_ONLY
        r = await (await post("/api/action", DISPATCH, {"type": "run", "cmd": "encours"})).json()
        assert r["ok"] and "En ce moment" in h.tg.last(DISPATCH).text
        r = await (await post("/api/action", DISPATCH, {"type": "open_chat", "course_id": c3["id"]})).json()
        assert r["ok"] and h.tg.last(DISPATCH).text.startswith(f"🚴 Course #{c3['id']}")

        board = await (await post("/api/board", DISPATCH)).json()
        assert board["totals"]["n"] == 1 and board["totals"]["pay"]["especes"] == 60.0
        team = await (await post("/api/section", DISPATCH, {"name": "team"})).json()
        assert {u["name"] for u in team["users"]} >= {"Franchisé 1", "Livreur 1"}
        r = await (await post("/api/action", F1, {"type": "notify_level", "value": "tout"})).json()
        assert r["ok"] and (await db.get_user(f1["id"]))["notify_level"] == "tout"


async def test_quiet_notifications_go_to_the_feed(h, test_config):
    """Niveau « essentiel » : les avis de routine ne sont plus envoyés en message, ils sont dans le fil ;
    les avis importants partent toujours ; « tout » : tout en message."""
    import dataclasses

    from bot import config, messaging

    config.set_config(dataclasses.replace(test_config, quiet_notifications=True, notify_franchises=True))
    f1, f2, _ = await setup_network(h, livreurs=())
    n_dispatch, n_f2 = len(h.tg.inbox(DISPATCH)), len(h.tg.inbox(F2))
    await messaging.notify_dispatch(h.app.bot, "🆕 routine")
    await messaging.notify_dispatch(h.app.bot, "⚠️ problème", important=True)
    assert [m.text for m in h.tg.inbox(DISPATCH)[n_dispatch:]] == ["⚠️ problème"]
    assert [m.text for m in h.tg.inbox(F2)[n_f2:]] == ["⚠️ problème"]
    feed = await db.list_feed(5)
    assert [(r["text"], r["important"]) for r in feed[:2]] == [("⚠️ problème", True), ("🆕 routine", False)]
    await db.update_user(f2["id"], {"notify_level": "tout"})
    await messaging.notify_dispatch(h.app.bot, "🆕 routine 2")
    assert h.tg.last(F2).text == "🆕 routine 2" and h.tg.last(DISPATCH).text == "⚠️ problème"


async def test_webapp_does_everything(h, monkeypatch, test_config):
    """Tout depuis l'appli : commande (lecture, envoi, choix du livreur), produits d'une course, rechargement,
    swipe, dépense, ventes, fiches (valider, rôle, nom, prénom, bannir, réactiver), message, carte,
    débrief et fin de service affichés dans l'appli, remise à zéro réservée au dispatch."""
    import asyncio
    import dataclasses

    from aiohttp.test_utils import TestClient, TestServer

    from bot import config, web
    from bot.services import sheets

    config.set_config(dataclasses.replace(test_config, franchise_picks=True))
    monkeypatch.setenv("GOOGLE_SHEETS_WEBHOOK_URL", "https://script.google.com/macros/s/x/exec")
    monkeypatch.setenv("GOOGLE_SHEETS_SECRET", "s")

    async def fake_send(rows, client=None):
        return len(rows)

    async def fake_action(action, client=None, **kw):
        return {"ok": False, "error": "tableau non relié (test)"}

    monkeypatch.setattr(sheets, "send_rows", fake_send)
    monkeypatch.setattr(sheets, "fetch_action", fake_action)
    for name in ("DIV", "US"):
        await db.create_product(name, name.lower(), [])
    EXTRACTIONS["commande rivoli"] = [RIVOLI]
    f1, f2, (l1, l2) = await setup_network(h)
    r1 = await register(h, R1, "ravitailleur", "Sam")
    await go_on_duty(h, L1, BASTILLE)

    async with TestClient(TestServer(web.build(h.app))) as client:
        async def act(tg_id, **body):
            r = await client.post("/api/action", json=body, headers={"X-Init-Data": _init_data(tg_id)})
            return await r.json()

        # Commande : lecture → fiche, envoi → choix du livreur → Livreur 1.
        r = await act(F1, type="order_analyse", text="commande rivoli")
        assert r["ok"] and r["cards"][0]["ok"] and "Rivoli" in r["cards"][0]["html"]
        sent = await act(F1, type="order_send", draft_id=r["cards"][0]["draft_id"])
        assert sent["ok"] and [o["id"] for o in sent["options"]] == [l1["id"]]
        cid = sent["course_id"]
        assert (await act(F1, type="order_send", draft_id=r["cards"][0]["draft_id"]))["ok"] is False   # une fois
        r = await act(F1, type="order_pick", course_id=cid, livreur_id=l1["id"])
        assert r["ok"] and (await db.get_course(cid))["livreur_id"] == l1["id"]
        h.tg.find(L1, f"Course #{cid} — c'est pour toi")

        # Produits de la course.
        r = await act(DISPATCH, type="edit_lines", course_id=cid)
        assert r["ok"] and r["lines"]
        r = await act(DISPATCH, type="edit_apply", course_id=cid, lines=[{"p": "DIV", "q": 3, "x": 90}])
        assert r["ok"] and (await db.get_course(cid))["price"] == 90
        assert (await act(DISPATCH, type="edit_apply", course_id=cid, lines=[{"p": "DIV", "q": 1, "x": 15}]))["ok"] is False

        # Rechargement, cash seul, swipe, dépense, ventes.
        r = await act(DISPATCH, type="ravi_prepare", ravi="Ravitailleur 1", text="Livreur 1\nBox 1\n12 DIV\ncash 50")
        assert r["ok"] and "+12 DIV" in r["html"]
        r = await act(DISPATCH, type="ravi_commit", ravi="Ravitailleur 1", text="Livreur 1\nBox 1\n12 DIV\ncash 50")
        assert r["ok"] and "Rechargement enregistré" in r["html"]
        r = await act(DISPATCH, type="ravi_commit", ravi="Ravitailleur 1", text="Livreur 2\ncash 30")
        assert r["ok"]
        rows = (await db._t("restocks").select("*").order("id").execute()).data
        assert [(x["kind"], x["livreur_id"], float(x["cash"]), x["by_user_id"]) for x in rows] == [
            ("load", l1["id"], 50.0, r1["id"]), ("cash", l2["id"], 30.0, r1["id"])]
        assert (await act(DISPATCH, type="ravi_prepare", ravi="Inconnu", text="Livreur 1\ncash 5"))["ok"] is False
        r = await act(DISPATCH, type="swipe_commit", text="Livreur 1 > Livreur 2\n2 DIV")
        assert r["ok"] and len((await db._t("restocks").select("*").eq("kind", "swipe").execute()).data) == 1
        r = await act(DISPATCH, type="expense", livreur="Livreur 2", kind="paye", amount="40", motif="avance")
        assert r["ok"] and (await db._t("expenses").select("*").execute()).data[0]["kind"] == "paye"
        r = await act(DISPATCH, type="sales_commit", text="Livreur 2\n2 US 60")
        assert r["ok"] and len((await db._t("sales").select("*").execute()).data) == 1
        await asyncio.gather(*list(sheets._tasks))

        # Fiches : nouvelle inscription validée, rôle, nom, prénom, bannir / réactiver ; franchisé : non.
        await h.text(5000, "/start")
        await h.press(5000, h.tg.last(5000), "role:livreur")
        await h.text(5000, "Sofiane")
        newbie = await db.get_user_by_tg(5000)
        assert (await act(F1, type="user_approve", user_id=newbie["id"]))["message"] == texts.DISPATCH_ONLY
        assert (await act(DISPATCH, type="user_approve", user_id=newbie["id"]))["ok"]
        assert (await db.get_user(newbie["id"]))["status"] == "active"
        assert (await act(DISPATCH, type="user_prenom", name="Livreur 2", value="Karim"))["ok"]
        assert (await act(DISPATCH, type="user_rename", user_id=f2["id"], value="Le Kiosque"))["ok"]
        assert (await db.get_user(f2["id"]))["display_name"] == "Le Kiosque"
        assert (await act(DISPATCH, type="user_role", user_id=newbie["id"], value="ravitailleur"))["ok"]
        assert (await db.get_user(newbie["id"]))["role"] == "ravitailleur"
        assert (await act(DISPATCH, type="user_ban", user_id=newbie["id"]))["ok"]
        assert (await db.get_user(newbie["id"]))["status"] == "banned"
        assert (await act(DISPATCH, type="user_unban", user_id=newbie["id"]))["ok"]
        team = await (await client.post("/api/section", json={"name": "team"},
                                        headers={"X-Init-Data": _init_data(DISPATCH)})).json()
        assert team["manage"] and any(u["name"] == "Le Kiosque" for u in team["users"])

        # Message, carte, débrief et fin de service dans l'appli.
        r = await act(DISPATCH, type="message", user_id=l1["id"], text="Passe au dépôt")
        assert r["ok"] and "Passe au dépôt" in h.tg.last(L1).text
        r = await act(DISPATCH, type="position", livreur_id=l1["id"])
        assert r["ok"] and r["map"].startswith("https://www.google.com/maps")
        n = len(h.tg.inbox(DISPATCH))
        r = await act(DISPATCH, type="debrief")
        assert r["ok"] and "Journée close" in r["html"]
        assert len(h.tg.inbox(DISPATCH)) == n                       # affiché dans l'appli, pas dans le chat
        r = await act(DISPATCH, type="close_livreur", arg="Livreur 2")
        assert r["ok"] and "Livreur 2" in r["html"] and (await db.get_user(l2["id"]))["on_duty"] is False
        assert (await act(F1, type="reset_check"))["message"] == texts.DISPATCH_ONLY
        r = await act(DISPATCH, type="reset_check")
        assert r["ok"] is False and r["html"]                        # tableau illisible : pas de remise à zéro


async def test_livreur_app_full_night(h, test_config):
    """Appli des livreurs : commencer, nouvelle course → JE LA PRENDS, son temps, message au franchisé,
    « le client a pris autre chose », C'EST LIVRÉ (espèces), dépense, goûts, stock du début à confirmer,
    fin de soirée. Mêmes effets que dans le chat ; réservée aux livreurs."""
    import dataclasses

    from aiohttp.test_utils import TestClient, TestServer

    from bot import config, web

    await db.create_product("DIV", "div", [])
    f1, f2, (l1, l2) = await setup_network(h)
    await h.location(L1, *BASTILLE)                       # position partagée dans le chat

    async with TestClient(TestServer(web.build(h.app))) as client:
        async def act(tg_id, **body):
            r = await client.post("/api/me/action", json=body, headers={"X-Init-Data": _init_data(tg_id)})
            return await r.json()

        async def board(tg_id):
            return await (await client.post("/api/me/board", headers={"X-Init-Data": _init_data(tg_id)})).json()

        assert (await client.get("/livreur")).status == 200
        assert (await client.post("/api/me/board", headers={"X-Init-Data": _init_data(F1)})).status == 403

        assert (await act(L1, type="pause"))["ok"] and (await db.get_user(l1["id"]))["on_duty"] is False
        b = await board(L1)
        assert b["me"]["on_duty"] is False and b["me"]["position_ok"] is True
        r = await act(L1, type="start", transport="deux_roues")
        assert r["ok"] and (await db.get_user(l1["id"]))["on_duty"] is True
        assert (await db.get_user(l1["id"]))["transport_mode"] == "deux_roues"

        course = await order(h, F1, "rivoli")
        b = await board(L1)
        assert [p["id"] for p in b["proposals"]] == [course["id"]]
        r = await act(L1, type="take", course_id=course["id"])
        assert r["ok"] and (await db.get_course(course["id"]))["livreur_id"] == l1["id"]
        assert h.tg.find(L1, f"Course #{course['id']} — c'est pour toi")       # la proposition devient la fiche

        b = await board(L1)
        cur = b["current"]
        assert cur["id"] == course["id"] and cur["nav"]["maps"].startswith("https://www.google.com/maps/dir/")
        assert cur["price"] == 60 and cur["lines"]
        assert (await act(L1, type="eta", course_id=course["id"], minutes="abc"))["ok"] is False
        r = await act(L1, type="eta", course_id=course["id"], minutes="25")
        assert r["ok"] and (await db.get_course(course["id"]))["eta_minutes"] == 25
        assert any("25 min" in t for t in h.tg.texts(F1))

        r = await act(L1, type="message", course_id=course["id"], text="Je suis en bas")
        assert r["ok"] and any("Je suis en bas" in t for t in h.tg.texts(F1))
        assert (await board(L1))["current"]["messages"][-1] == {"mine": True, "text": "Je suis en bas",
                                                                 "at": (await board(L1))["current"]["messages"][-1]["at"]}

        r = await act(L1, type="edit", course_id=course["id"], lines=[{"p": "vodka", "q": 3, "x": 90}])
        assert r["ok"] and (await db.get_course(course["id"]))["pending_edit"]["price"] == 90
        assert any("Tu valides" in t for t in h.tg.texts(F1))

        assert (await act(L2, type="deliver", course_id=course["id"], payment="e"))["ok"] is False   # pas la sienne
        r = await act(L1, type="deliver", course_id=course["id"], payment="e")
        assert r["ok"] and (await db.get_course(course["id"]))["status"] == "delivered"
        assert (await board(L1))["night"]["n"] == 1

        r = await act(L1, type="expense", kind="charges", amount="12", motif="Essence")
        assert r["ok"] and (await db._t("expenses").select("*").execute()).data[0]["livreur_id"] == l1["id"]

        # Stock de fin de soirée demandé à la fin : confirmé depuis l'appli.
        config.set_config(dataclasses.replace(test_config, stock_declaration=True))
        r = await act(L1, type="close")
        assert r["ok"] and r["html"] and (await db.get_user(l1["id"]))["on_duty"] is False
        decl = (await board(L1))["declaration"]
        assert decl and decl["kind"] == "end"
        assert (await act(L1, type="declare_fix", id=decl["id"], items=[{"p": "inconnu", "q": 2}]))["ok"] is False
        r = await act(L1, type="declare_fix", id=decl["id"], items=[{"p": "DIV", "q": 2}])
        assert r["ok"] and (await db.get_declaration(decl["id"]))["status"] == "confirmed"
        assert (await board(L1))["declaration"] is None


async def test_ravitailleur_app(h, monkeypatch):
    """Appli des ravitailleurs : livreurs avec stock et cash, charger (box + compteurs), reprendre, cash,
    donner à un autre livreur — récap puis enregistrement, comme /ravi et /swipe. Réservée aux ravitailleurs."""
    import asyncio

    from aiohttp.test_utils import TestClient, TestServer

    from bot import web
    from bot.services import sheets, stock as stock_service

    monkeypatch.setenv("GOOGLE_SHEETS_WEBHOOK_URL", "https://script.google.com/macros/s/x/exec")
    monkeypatch.setenv("GOOGLE_SHEETS_SECRET", "s")
    sent = []

    async def fake_send(rows, client=None):
        sent.extend(rows)
        return len(rows)

    async def fake_action(action, client=None, **kw):
        return {
            "stock_livreurs": {"ok": True, "livreurs": {"Livreur 1": {"DIV": 4, "US": 0}, "Livreur 2": {"DIV": 0}}},
            "cash_livreurs": {"ok": True, "livreurs": {"Livreur 1": {"especes": 120, "cash": 120}}, "ravitailleur": 30},
            "stock_box": {"ok": True, "boxes": {"Box 1": {"DIV": 20, "US": 6}}, "totals": []},
        }[action]

    monkeypatch.setattr(sheets, "send_rows", fake_send)
    monkeypatch.setattr(sheets, "fetch_action", fake_action)
    stock_service._sheet_cache = None
    for name in ("DIV", "US"):
        await db.create_product(name, name.lower(), [])
    f1, f2, (l1, l2) = await setup_network(h)
    r1 = await register(h, R1, "ravitailleur", "Sam")

    async with TestClient(TestServer(web.build(h.app))) as client:
        async def post(path, tg_id, **body):
            return await client.post(path, json=body, headers={"X-Init-Data": _init_data(tg_id)})

        assert (await client.get("/ravi")).status == 200
        assert (await post("/api/ravi/board", L1)).status == 403
        board = await (await post("/api/ravi/board", R1)).json()
        lv1 = next(lv for lv in board["livreurs"] if lv["name"] == "Livreur 1")
        assert lv1["items"] == {"DIV": 4} and lv1["cash"] == 120 and board["my_money"]["balance"] == 0
        assert board["boxes"][0] == {"name": "Box 1", "items": {"DIV": 20, "US": 6}}

        async def act(**body):
            return await (await post("/api/ravi/action", R1, **body)).json()

        r = await act(type="prepare", mode="load", livreur="Livreur 1", box="Box 1", items=[{"p": "US", "q": 3}], cash="50")
        assert r["ok"] and "+3 US" in r["html"] and "50" in r["html"]
        r = await act(type="commit", mode="load", livreur="Livreur 1", box="Box 1", items=[{"p": "US", "q": 3}], cash="50")
        assert r["ok"] and "Rechargement enregistré" in r["html"]
        r = await act(type="commit", mode="unload", livreur="Livreur 1", box="Box 1", items=[{"p": "DIV", "q": 2}])
        assert r["ok"]
        r = await act(type="commit", mode="cash", livreur="Livreur 1", cash="70")
        assert r["ok"]
        assert (await act(type="prepare", mode="load", livreur="Livreur 1", box="Box 1", items=[]))["ok"] is False
        r = await act(type="commit", mode="swipe", livreur="Livreur 1", to="Livreur 2", items=[{"p": "DIV", "q": 1}])
        assert r["ok"]
        rows = (await db._t("restocks").select("*").order("id").execute()).data
        assert [(x["kind"], x["livreur_id"], x["by_user_id"], x.get("ravitailleur_name")) for x in rows][:3] == [
            ("load", l1["id"], r1["id"], "Ravitailleur 1"), ("unload", l1["id"], r1["id"], "Ravitailleur 1"),
            ("cash", l1["id"], r1["id"], "Ravitailleur 1")]
        assert rows[3]["kind"] == "swipe" and rows[3]["to_livreur_id"] == l2["id"]
        assert any("Chargement reçu" in t for t in h.tg.texts(L1))
        await asyncio.gather(*list(sheets._tasks))
        assert sent
        flavors = await (await post("/api/ravi/flavors", R1)).json()
        assert flavors["ok"]


async def test_franchises_share_every_address(h):
    """Entre franchisés on s'entraide : chacun voit toutes les courses, adresse comprise, et peut agir dessus."""
    from aiohttp.test_utils import TestClient, TestServer

    from bot import web

    f1, f2, (l1,) = await setup_network(h, livreurs=(L1,))
    course = await order(h, F2, "oberkampf")
    async with TestClient(TestServer(web.build(h.app))) as client:
        board = await (await client.post("/api/board", headers={"X-Init-Data": _init_data(F1)})).json()
        seen = next(c for c in board["pending"] + board["assigned"] if c["id"] == course["id"])
        assert seen["address"] == course["address"] and seen["mine"] is False
        assert seen["franchise"]["name"] == "Franchisé 2"
        r = await (await client.post("/api/action", json={"type": "time", "course_id": course["id"], "value": "vers minuit"},
                                     headers={"X-Init-Data": _init_data(F1)})).json()
        assert r["ok"] and (await db.get_course(course["id"]))["requested_time"] == "vers minuit"


async def test_catalog_management_in_app(h):
    """Catalogue dans l'appli (comme /produits et /gouts) : ajouter, nom, surnoms, goûts, supprimer."""
    from aiohttp.test_utils import TestClient, TestServer

    from bot import web

    await setup_network(h, livreurs=())
    async with TestClient(TestServer(web.build(h.app))) as client:
        async def act(tg_id, **body):
            r = await client.post("/api/action", json=body, headers={"X-Init-Data": _init_data(tg_id)})
            return await r.json()

        r = await act(F1, type="catalog_add", text="Produit Test : pt, ptest\nAutre Test")
        assert r["ok"] and "Produit Test" in r["html"]
        assert (await act(F1, type="catalog_add", text=""))["ok"] is False
        products = {p["name"]: p for p in await db.list_products()}
        assert products["Produit Test"]["aliases"] == ["pt", "ptest"]
        pid = products["Produit Test"]["id"]
        section = await (await client.post("/api/section", json={"name": "products"},
                                           headers={"X-Init-Data": _init_data(DISPATCH)})).json()
        assert any(p["id"] == pid and p["aliases"] == ["pt", "ptest"] for p in section["products"])

        r = await act(DISPATCH, type="catalog_edit", id=pid, name="Produit Test", aliases="pt, essai", variants="rouge, vert")
        assert r["ok"]
        p = await db.get_product(pid)
        assert p["aliases"] == ["pt", "essai"] and p["variants"] == ["rouge", "vert"]
        r = await act(DISPATCH, type="catalog_edit", id=pid, name="Autre Test", aliases="", variants="rouge, vert")
        assert r["ok"] is False and (await db.get_product(pid))["name"] == "Produit Test"   # nom déjà pris
        r = await act(DISPATCH, type="catalog_delete", id=pid)
        assert r["ok"] and await db.get_product(pid) is None


async def test_traffic_live_arrival(h, test_config):
    """TomTom : arrivée recalculée avec le trafic pour la course en cours, affichée dans les applis, alerte
    une fois si elle dépasse le temps annoncé par le livreur. Sans clé : rien."""
    import dataclasses

    import httpx
    from aiohttp.test_utils import TestClient, TestServer

    from bot import config, web
    from bot.services import traffic

    seen = {}

    def handler(request):
        seen["url"] = str(request.url)
        return httpx.Response(200, json={"routes": [{"summary": {"travelTimeInSeconds": 1800,
                                                                  "trafficDelayInSeconds": 600, "lengthInMeters": 7400}}]})

    assert await traffic.route(48.85, 2.35, 48.86, 2.36, "voiture") is None          # pas de clé
    config.set_config(dataclasses.replace(test_config, tomtom_api_key="k"))
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        r = await traffic.route(48.85, 2.35, 48.86, 2.36, "voiture", client=client)
    assert r == {"seconds": 1800, "delay": 600, "meters": 7400} and "travelMode=car" in seen["url"]
    assert await traffic.route(48.85, 2.35, 48.86, 2.36, "transport") is None        # métro : pas de TomTom

    f1, f2, (l1,) = await setup_network(h, livreurs=(L1,))
    await go_on_duty(h, L1, BASTILLE)
    course = await order(h, F1, "oberkampf")
    await h.press(L1, h.tg.last(L1), "course_take:")
    await h.text(L1, "5")                                                           # annonce 5 min

    async def fake_route(*a, **k):
        return {"seconds": 1800, "delay": 600, "meters": 7400}

    traffic._live.clear()
    traffic._late_alerted.clear()
    original = traffic.route
    traffic.route = fake_route
    try:
        await traffic.refresh(h.context)
        await traffic.refresh(h.context)
    finally:
        traffic.route = original
    live = traffic.live(course["id"])
    assert live and live["minutes"] == 30 and live["delay"] == 10
    alerts = [t for t in h.tg.texts(DISPATCH) if t.startswith(f"🚦 #{course['id']}")]
    assert len(alerts) == 1 and "sera en retard" in alerts[0]

    async with TestClient(TestServer(web.build(h.app))) as client:
        board = await (await client.post("/api/board", headers={"X-Init-Data": _init_data(DISPATCH)})).json()
        route = board["routes"][0]
        assert route["arrival"]["source"] == "trafic" and route["arrival"]["announced"]
        me = await (await client.post("/api/me/board", headers={"X-Init-Data": _init_data(L1)})).json()
        assert me["current"]["traffic"]["delay"] == 10


NAVITIA = {"journeys": [{"duration": 1260, "arrival_date_time": "20261016T185200", "sections": [
    {"type": "street_network", "duration": 240, "mode": "walking"},
    {"type": "waiting", "duration": 120},
    {"type": "public_transport", "duration": 600, "from": {"name": "Oberkampf"}, "to": {"name": "Robespierre"},
     "display_informations": {"commercial_mode": "Métro", "code": "9", "direction": "Mairie de Montreuil"}},
    {"type": "street_network", "duration": 300, "mode": "walking"}]}],
    "disruptions": [{"id": "d1"}]}


async def test_idfm_transit_route(h, test_config):
    """IDFM (PRIM) : pour un livreur en transports, arrivée d'après les horaires réels et itinéraire simple
    (lignes, arrêts, marche) dans son appli. Sans clé : rien."""
    import dataclasses

    import httpx
    from aiohttp.test_utils import TestClient, TestServer

    from bot import config, web
    from bot.services import traffic

    seen = {}

    def handler(request):
        seen["url"], seen["key"] = str(request.url), request.headers.get("apikey")
        return httpx.Response(200, json=NAVITIA)

    assert await traffic.route_transit(48.85, 2.35, 48.86, 2.36) is None             # pas de clé
    config.set_config(dataclasses.replace(test_config, idfm_api_key="cle-idfm"))
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        r = await traffic.route_transit(48.85, 2.35, 48.86, 2.36, client=client)
    assert seen["key"] == "cle-idfm" and "from=2.35%3B48.85" in seen["url"]
    assert r["seconds"] == 1260 and r["disruptions"] == 1
    assert r["steps"] == ["🚶 4 min à pied", "⏳ 2 min d'attente",
                          "🚇 Métro 9 → Mairie de Montreuil : Oberkampf → Robespierre", "🚶 5 min à pied"]

    f1, f2, (l1,) = await setup_network(h, livreurs=(L1,))
    await go_on_duty(h, L1, BASTILLE)
    await db.update_user(l1["id"], {"transport_mode": "transport"})
    course = await order(h, F1, "oberkampf")
    await h.press(L1, h.tg.last(L1), "course_take:")

    async def fake_transit(*a, **k):
        return {"seconds": 1260, "arrival": None, "steps": r["steps"], "disruptions": 1}

    traffic._live.clear()
    original = traffic.route_transit
    traffic.route_transit = fake_transit
    try:
        await traffic.refresh(h.context)
    finally:
        traffic.route_transit = original
    assert traffic.live(course["id"])["source"] == "transports"
    async with TestClient(TestServer(web.build(h.app))) as client:
        me = await (await client.post("/api/me/board", headers={"X-Init-Data": _init_data(L1)})).json()
        assert me["current"]["traffic"]["source"] == "transports" and len(me["current"]["traffic"]["steps"]) == 4
        board = await (await client.post("/api/board", headers={"X-Init-Data": _init_data(DISPATCH)})).json()
        assert board["routes"][0]["arrival"]["source"] == "transports"


async def test_daily_cash_account(h, monkeypatch):
    """Caisse du livreur au jour le jour : reste d'avant (pas remis) + encaissé aujourd'hui − charges
    (− cash remis, + cash reçu d'un livreur) = ce qui lui reste. Une vente notée pour une nuit passée
    compte dans « avant »."""
    from bot import web_livreur
    from bot.services import cash, sheets

    monkeypatch.setenv("GOOGLE_SHEETS_WEBHOOK_URL", "https://script.google.com/macros/s/x/exec")
    monkeypatch.setenv("GOOGLE_SHEETS_SECRET", "s")

    async def fake_action(action, client=None, **kw):     # cumul de la semaine, comme le script
        return {"ok": True, "livreurs": {"Livreur 1": {"especes": 700, "depenses": 40, "recupere": 360, "cash": 300},
                                         "Livreur 2": {"cash": 30, "recupere": -30}}}

    monkeypatch.setattr(sheets, "fetch_action", fake_action)
    f1, _, (l1, l2) = await setup_network(h)
    now = now_utc()
    await db._t("courses").insert({
        "franchise_id": f1["id"], "livreur_id": l1["id"], "raw_message": "x", "address": "1 rue X", "postal_code": "75004",
        "district": "Paris 4e", "lat": 48.85, "lon": 2.35, "products": "x", "price": 80, "status": "delivered",
        "payment": "especes", "delivered_at": iso(now)}).execute()
    await db._t("sales").insert({"livreur_name": "Livreur 1", "livreur_id": l1["id"], "by_user_id": f1["id"],
                                 "payment": "especes", "product": "TEST", "qty": 1, "price": 50,
                                 "night": (now - timedelta(days=2)).date().isoformat()}).execute()
    await db._t("expenses").insert({"livreur_id": l1["id"], "by_user_id": l1["id"], "kind": "charges",
                                    "amount": 20}).execute()
    await db._t("restocks").insert({"livreur_id": l1["id"], "by_user_id": f1["id"], "kind": "cash",
                                    "cash": 100}).execute()
    await db._t("restocks").insert({"livreur_id": l1["id"], "livreur_name": "Livreur 1", "by_user_id": f1["id"],
                                    "kind": "swipe", "box": "Swipe", "cash": 30, "to_livreur_id": l2["id"],
                                    "to_livreur_name": "Livreur 2"}).execute()

    row = (await cash.livreur_now(l1))["row"]
    assert (row["jour_especes"], row["jour_depenses"], row["jour_remis"], row["jour_recu"]) == (80, 20, 130, 0)
    assert row["avant"] == 370 and row["cash"] == 300          # 370 + 80 − 20 − 130 = 300
    other = (await cash.livreur_now(l2))["row"]
    assert other["jour_recu"] == 30 and other["avant"] == 0

    money = await web_livreur.section(await db.get_user(l1["id"]), "money")
    assert money["ok"] and money["avant"] == 370 and money["jour_especes"] == 80 and money["cash"] == 300

    await h.text(L1, "/macaisse")
    mine = h.tg.last(L1).text.replace("\xa0", " ")
    assert mine.split("\n")[2:6] == ["📅 Reste d'avant (pas encore remis) : 370 €", "💵 + Encaissé aujourd'hui : 80 €",
                                     "🧾 − Charges : 20 €", "🤝 − Remis aujourd'hui : 130 €"]
    assert "<b>= Il te reste : 300 €</b>" in mine


async def test_ravi_money_space(h, monkeypatch):
    """Espace 💼 Ravi : cash récupéré (rechargements) + dépôts − charges − invest − remis = ce qu'il doit avoir ;
    mouvements recopiés dans RAVI (ancien script : en attente), annulation, contrôle de caisse et calage,
    report au reset ; le ravitailleur voit son solde et note ses charges ; réservé au dispatch."""
    from aiohttp.test_utils import TestClient, TestServer

    from bot import web
    from bot.services import ravi, sheets

    monkeypatch.setenv("GOOGLE_SHEETS_WEBHOOK_URL", "https://script.google.com/macros/s/x/exec")
    monkeypatch.setenv("GOOGLE_SHEETS_SECRET", "s")
    calls, script = [], {"new": True}

    async def fake_action(action, client=None, payload=None, **kw):
        if action != "ravi_move":
            return {"ok": False, "error": "pas de tableau dans ce test"}
        if not script["new"]:
            return {"ok": True, "added": 0}                  # ancien script : action inconnue
        calls.append(payload)
        return {"ok": True, "removed": True} if payload["op"] == "remove" else {"ok": True, "written": True}

    monkeypatch.setattr(sheets, "fetch_action", fake_action)
    f1, _, (l1,) = await setup_network(h, livreurs=(L1,))
    r1 = await register(h, R1, "ravitailleur", "Sam")
    name = r1["display_name"]
    dispatch_user = await db.get_user_by_tg(DISPATCH)
    for fields in ({"by_user_id": r1["id"], "cash": 300},                                  # saisi par lui
                   {"by_user_id": dispatch_user["id"], "ravitailleur_name": name, "cash": 100},  # /ravi 1 du dispatch
                   {"by_user_id": dispatch_user["id"], "cash": 50}):                        # sans ravitailleur
        await db._t("restocks").insert({"livreur_id": l1["id"], "kind": "cash", **fields}).execute()

    async with TestClient(TestServer(web.build(h.app))) as client:
        async def post(path, tg_id, **body):
            return await (await client.post(path, json=body, headers={"X-Init-Data": _init_data(tg_id)})).json()

        async def mine():
            return next(a for a in (await post("/api/section", DISPATCH, name="ravi"))["ravis"] if a["name"] == name)

        assert (await post("/api/section", F1, name="ravi"))["ok"] is False          # dispatch seulement
        assert (await post("/api/action", F1, type="rv_move", ravi=name, kind="depot", amount=10))["ok"] is False
        section = await post("/api/section", DISPATCH, name="ravi")
        assert section["unassigned"]["total"] == 50
        acc = await mine()
        assert acc["balance"] == 400 and acc["totals"]["recupere"] == 400 and not acc["start"]["set"]

        r = await post("/api/action", DISPATCH, type="rv_move", ravi=name, kind="depot", amount="200", motif="fond")
        assert r["ok"] and "600" in r["message"].replace("\xa0", " ")
        assert "Tu dois avoir sur toi" in h.tg.last(R1).text
        assert calls[-1]["op"] == "add" and calls[-1]["type"] == "Depot" and calls[-1]["ravitailleur"] == name
        r = await post("/api/action", DISPATCH, type="rv_move", ravi=name, kind="perso", amount=500)
        assert (await mine())["balance"] == 100
        assert (await post("/api/action", DISPATCH, type="rv_move", ravi=name, kind="perso", amount=0))["ok"] is False

        # Le ravitailleur voit son solde et note une charge (le dispatch est prévenu) ; rien d'autre.
        board = await post("/api/ravi/board", R1)
        assert board["my_money"]["balance"] == 100
        assert (await post("/api/ravi/action", R1, type="charge", amount="30", motif="essence"))["ok"]
        assert "a noté une charge" in h.tg.last(DISPATCH).text
        assert (await ravi.add_move(h.app.bot, r1, name, "invest", 5, None))[0] is None
        acc = await mine()
        assert acc["balance"] == 70 and acc["totals"]["charge"] == 30

        # Annuler le « remis » : sa ligne est effacée de RAVI.
        perso = next(x for x in acc["history"] if x["kind"] == "perso")
        assert (await post("/api/action", DISPATCH, type="rv_delete", id=perso["id"]))["ok"]
        assert calls[-1] == {"op": "remove", "numero": perso["id"]}
        assert (await mine())["balance"] == 570

        # Contrôle de caisse : 10 € manquent ; caler le solde sur le compté.
        r = await post("/api/action", DISPATCH, type="rv_count", ravi=name, amount="560")
        assert "manque" in r["message"]
        acc = await mine()
        assert acc["balance"] == 570 and acc["control"]["gap"] == -10 and not acc["control"]["aligned"]
        assert (await post("/api/action", DISPATCH, type="rv_align", id=acc["control"]["id"]))["ok"]
        acc = await mine()
        assert acc["balance"] == 560 and acc["start"]["set"] and acc["control"]["aligned"] and acc["history"] == []

        # Ancien script Google : le mouvement attend, puis passe quand le script est à jour.
        script["new"] = False
        await post("/api/action", DISPATCH, type="rv_move", ravi=name, kind="invest", amount=60, motif="achat")
        acc = await mine()
        assert acc["pending_sheet"] == 1 and acc["balance"] == 500
        script["new"] = True
        assert await ravi.sync_sheet() == 1 and (await mine())["pending_sheet"] == 0

        # Reset de la semaine : le solde devient le départ de la nouvelle semaine, et une ligne Report dans RAVI.
        lines = await ravi.carry_over(dispatch_user, "Reset test")
        assert any(name in x and "500" in x.replace("\xa0", " ") for x in lines)
        assert calls[-1]["type"] == "Depot" and calls[-1]["montant"] == 500 and calls[-1]["motif"] == "Report semaine passée"
        acc = await mine()
        assert acc["balance"] == 500 and acc["start"]["amount"] == 500 and acc["history"] == []


async def test_livreur_notifications_pro(h, test_config):
    """Carte « NOUVELLE COURSE » lisible sur l'écran verrouillé (✅ JE LA PRENDS + 📱 OUVRIR dans l'appli) ; chez
    les livreurs, seuls les messages importants sonnent ; relance qui sonne, effacée quand la course part ;
    guide (icône, sonnerie, raccourci) par /installer et pour tous depuis l'admin ; lien direct /go."""
    import dataclasses

    from aiohttp.test_utils import TestClient, TestServer

    from bot import config, web
    from bot.services import broadcast

    config.set_config(dataclasses.replace(test_config, webapp_url="https://app.test"))
    f1, f2, (l1, l2) = await setup_network(h)
    await go_on_duty(h, L1, BASTILLE)
    await go_on_duty(h, L2, REPUBLIQUE)

    def params(msg):                                   # paramètres envoyés à Telegram pour ce message
        return next(p for m, p in h.tg.calls if m == "sendMessage" and p.get("text") == msg.text
                    and int(p["chat_id"]) == msg.chat_id)

    course = await order(h, F1, "rivoli")
    prop = h.tg.find(L1, f"\nCourse #{course['id']}")
    assert prop.text.startswith("🔔 <b>NOUVELLE COURSE</b> · Paris 4e · 60")
    keys = prop.markup["inline_keyboard"]
    assert keys[0][0] == {"text": "✅ JE LA PRENDS", "callback_data": f"course_take:{course['id']}"}
    assert keys[1][0]["web_app"]["url"] == f"https://app.test/livreur?course={course['id']}"
    assert not params(prop).get("disable_notification")                    # la course sonne

    # Relance : la course n'est pas prise, les livreurs sollicités reçoivent une 2e notification qui sonne.
    assert await broadcast.relance(h.app, course["id"], course["broadcast_round"]) >= 1
    relance = h.tg.find(L1, "⏰ <b>Toujours dispo</b>")
    assert not params(relance).get("disable_notification")
    await h.press(L1, prop, "course_take:")
    assert (h.tg.last(L1).chat_id, relance.message_id) not in h.tg.messages      # effacée : la course est partie
    assert not [m for m in h.tg.inbox(L2) if "⏰ <b>Toujours dispo</b>" in m.text]

    # Message secondaire (son temps d'arrivée) : silencieux.
    eta = h.tg.find(L1, "dans combien de temps tu y es")
    assert params(eta).get("disable_notification") is True

    # /installer : icône, sonnerie, guide (avec le lien direct de l'appli).
    await h.text(L1, "/installer")
    inbox = h.tg.texts(L1)
    assert inbox[-3].startswith("[photo] 🛵 L'icône") and inbox[-2].startswith("[son] 🔔 La sonnerie")
    assert "Raccourcis" in inbox[-1] and "?startapp" in inbox[-1] and "Persistant" in inbox[-1]

    async with TestClient(TestServer(web.build(h.app))) as client:
        async def post(path, tg_id, **body):
            r = await client.post(path, json=body, headers={"X-Init-Data": _init_data(tg_id)})
            return r.status, await r.json()

        assert (await client.get("/go")).status == 200
        assert (await post("/api/whoami", L1))[1] == {"role": "livreur"}
        assert (await post("/api/whoami", DISPATCH))[1] == {"role": "admin"}
        assert (await post("/api/whoami", 999))[0] == 403
        sound = await client.get("/media/sonnerie-course.mp3")
        assert sound.status == 200 and len(await sound.read()) < 300_000          # limite Telegram : 300 Ko
        assert (await client.get("/media/icone-courses.png")).status == 200
        assert (await post("/api/me/action", L2, type="guide"))[1]["ok"]
        assert h.tg.texts(L2)[-1].startswith("📲 <b>Ton appli « Courses »")
        assert (await post("/api/action", F1, type="install_guide_all"))[1]["ok"] is False   # dispatch seulement
        status, r = await post("/api/action", DISPATCH, type="install_guide_all")
        assert r["ok"] and r["message"] == "Guide envoyé à 2 livreurs."
