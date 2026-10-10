"""/gouts : goûts (variantes) des produits, sans rien changer à la compta.

Admins : « /gouts MSX noisette, fraise, orange, banane » fixe la liste (« /gouts MSX - » l'efface) ;
/gouts seul → les goûts de chaque produit et ce qu'il reste chez chaque livreur.
Livreur : /gouts → un bouton par goût, ✅ s'il en a encore ; il touche pour cocher / décocher.
Ravitailleur : /gouts → la liste, comme les admins (sans changer les goûts des produits)."""
from __future__ import annotations

from telegram import Update

from bot import db, keyboards, messaging, texts
from bot.handlers import common
from bot.services import catalog as catalog_svc
from bot.services import variants

CLEAR = ("-", "aucun", "rien", "supprimer")


def _split(args: str, cat) -> tuple[dict | None, str]:
    """« MSX noisette, fraise » ou « Jack Daniels : miel, pomme » → (produit, « noisette, fraise »)."""
    if ":" in args:
        name, _, rest = args.partition(":")
        match = cat.match(name)
        return (match.product, rest) if match.product else (None, "")
    words = args.split()
    for k in range(len(words) - 1, 0, -1):        # le nom le plus long qui est exactement un produit
        hits = cat.index.get(catalog_svc.normalize(" ".join(words[:k])))
        if hits and len(hits) == 1:
            return hits[0], " ".join(words[k:])
    return None, ""


async def _livreur_view(user: dict) -> tuple[str, object]:
    products = [p for p in await db.list_products() if p.get("variants")]
    mine = variants.by_livreur(await db.list_livreur_variants(user["display_name"])).get(user["display_name"], {})
    return texts.my_variants(products), keyboards.my_variants(products, mine)


async def gouts(update: Update, context) -> None:
    user = await common.actor(update)
    if not await common.require(update, user):
        return
    if user["role"] == "livreur":
        text, markup = await _livreur_view(user)
        await messaging.reply(update, text, markup)
        return
    if not common.is_admin(user) and user["role"] != "ravitailleur":
        await messaging.reply(update, texts.NOT_FOR_YOU)
        return
    args = " ".join((update.message.text or "").split()[1:])
    if not args or not common.is_admin(user):
        products = [p for p in await db.list_products() if p.get("variants")]
        flavors = variants.by_livreur(await db.list_livreur_variants())
        await messaging.reply(update, texts.variants_overview(products, flavors, admin=common.is_admin(user)))
        return
    cat = await catalog_svc.load()
    product, rest = _split(args, cat)
    if product is None or not rest.strip():
        await messaging.reply(update, texts.VARIANTS_HELP)
        return
    new = [] if rest.strip().lower() in CLEAR else variants.parse_list(rest)
    await db.update_product(product["id"], {"variants": new})
    # Les goûts retirés de la liste disparaissent aussi chez les livreurs.
    for row in await db.list_livreur_variants():
        if row["product"] == product["name"] and row["variant"] not in new:
            await db.remove_livreur_variant(row["livreur_name"], row["product"], row["variant"])
    await db.log_event("variants_set", user_id=user["id"], payload={"product": product["name"], "variants": new})
    await messaging.reply(update, texts.variants_set(product["name"], new))


@common.callback
async def toggle(update: Update, context):
    """gv:<produit>:<n° du goût> — le livreur coche / décoche ; gv_done : fin."""
    user = await common.actor(update)
    if user is None or user["status"] != "active" or user["role"] != "livreur":
        return None
    chat_id, msg_id = update.effective_chat.id, update.callback_query.message.message_id
    data = update.callback_query.data or ""
    if data == "gv_done":
        await messaging.edit_markup(context.bot, chat_id, msg_id, None)
        return "C'est noté ✅"
    _, pid, idx = data.split(":", 2)
    product = await db.get_product(int(pid))
    flavors = list((product or {}).get("variants") or [])
    if product is None or not 0 <= int(idx) < len(flavors):
        return texts.ALREADY_HANDLED, True
    variant = flavors[int(idx)]
    mine = variants.by_livreur(await db.list_livreur_variants(user["display_name"])).get(user["display_name"], {})
    if variant in mine.get(product["name"], set()):
        await db.remove_livreur_variant(user["display_name"], product["name"], variant)
        answer = f"Plus de {variant}"
    else:
        await db.add_livreur_variants(user["display_name"], product["name"], [variant])
        answer = f"{variant} ✅"
    text, markup = await _livreur_view(user)
    await messaging.edit(context.bot, chat_id, msg_id, text, markup)
    return answer
