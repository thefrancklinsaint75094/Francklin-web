# Décisions prises pendant la construction

Choix faits là où la spécification ne tranchait pas, ou écarts assumés. Chaque fois : l'option la plus
simple qui respecte le mur de confidentialité (§12).

## Structure et technique

1. **Projet à la racine du dépôt** plutôt que dans un sous-dossier `dispatch-bot/` : le dépôt était vide
   et Railway déploie la racine sans réglage supplémentaire.
2. **Modules ajoutés** à la structure du §3, pour éviter les dépendances circulaires et la duplication :
   `bot/timeutil.py` (heure de Paris, nuits), `bot/messaging.py` (envois/éditions sûrs),
   `bot/services/lifecycle.py` (attribution, livraison, remise en diffusion, annulation, partagés par
   livreur/franchisé/dispatch), `bot/handlers/common.py` (contrôles, callbacks),
   `bot/handlers/messages.py` (aiguillage des messages ordinaires par rôle).
3. **Client Supabase asynchrone** (`acreate_client`) pour ne jamais bloquer la boucle du bot,
   et **`concurrent_updates` activé** : un appel Anthropic lent (jusqu'à ~40 s avec la nouvelle tentative)
   ne fige pas les autres utilisateurs. La sécurité des accès concurrents repose sur des mises à jour
   conditionnelles en base (`… WHERE status = …`), pas sur l'ordre de traitement.
4. **Idempotence** : chaque bouton passe par une mise à jour conditionnelle (draft `awaiting → confirmed`,
   course `assigned → delivered`, etc.). Un second appui ne fait rien et reçoit un message
   (« Déjà confirmée (course #142) », « Déjà livrée. »…).
5. **RLS activé sans politique** sur toutes les tables (fin de `schema.sql`). Les noms de tables et de colonnes
   sont inchangés. La clé `service_role` du bot contourne RLS ; la clé publique `anon` ne voit rien.
6. **`ANTHROPIC_MODEL` et `BAN_URL`** sont surchargeables par variable d'environnement (défauts = la spec),
   pour pouvoir changer de modèle ou suivre un déménagement de l'API Adresse sans toucher au code.
7. `.python-version` fixe Python 3.11 pour le build Nixpacks.

## Nuits, récap et journal

8. **Fenêtre d'une nuit = de 6h (NIGHT_END_HOUR) le jour D à 6h le lendemain**, heure de Paris.
   C'est la seule définition qui respecte à la fois « livrée à 18h20 après envoi à 17h50 → comptée » et
   « livrée à 6h05 → nuit suivante », sans laisser de course livrée en journée hors de toute nuit.
   `NIGHT_START_HOUR` n'intervient donc pas dans le calcul (il reste lu et documenté).
9. Le journal automatique de 6h couvre la nuit qui vient de se terminer ; le CSV est nommé avec la date du
   matin (`journal_2026-09-04.csv` pour la nuit du 3 au 4). Le CSV n'est joint que s'il y a des livraisons.
10. La ligne « En attente : · En cours : » du récap n'apparaît que pour la nuit en cours (elle n'a pas de sens
    pour une nuit passée).
11. Prix dans le CSV au format français (`60,00`), pour Excel.

## Inscription

12. La colonne `role` étant `NOT NULL`, **l'utilisateur est créé dès le choix du rôle** (`pending`,
    état `awaiting_name`). Le dispatch n'est prévenu qu'après réception du nom. L'état `awaiting_role` est
    inutile : le bouton porte le rôle.
13. Refus : l'utilisateur et ses événements sont supprimés (contrainte de clé étrangère), un événement
    `user_rejected` sans `user_id` garde la trace.
14. Réactivation : l'utilisateur reçoit « ✅ Ton accès est rétabli. » suivi de son message de bienvenue.
15. Le compte dispatch qui écrit avant d'avoir fait `/start` reçoit « Envoie /start pour activer le dispatch. »

## Commandes (franchisé)

16. **Drafts** : un draft est écrit avant tout appel réseau (texte brut, ou `[vocal]`/`[photo]` avec le
    `file_id` pour pouvoir réécouter). Il sert à la première commande valide du message ; les commandes
    suivantes du même message ont chacune leur draft (même `raw_message`). Message sans commande valide →
    `expired` ; panne technique → `error`.
17. « Commande i sur N » numérote parmi toutes les commandes détectées dans le message, même si certaines sont
    refusées (le franchisé reçoit l'explication pour celles-ci à leur place).
18. **Correction** : `created_at` du draft est remis à maintenant au clic sur « Corriger » et à la nouvelle
    fiche, pour que les 15 minutes repartent. Si le message de correction ne donne aucune fiche valide, l'état
    `correcting` est conservé : le message suivant retente. L'ancienne fiche devient « ✏️ Fiche corrigée
    ci-dessous. ». Ouvrir une seconde correction remet la première fiche en attente.
19. Code postal écrit par le franchisé mais sans résultat BAN : nouvelle recherche sans `postcode=`
    (code postal erroné plutôt qu'adresse inexistante).
20. Doublon : comparaison des libellés BAN normalisés, courses annulées exclues.
21. Captures d'écran (§7.3 bis) : l'emporte sur le texte générique du §14 qui refusait les photos. Les images
    envoyées « en fichier » sont aussi acceptées.
22. Le message de suivi côté franchisé porte une seconde ligne `📍 adresse — prix` pour distinguer ses
    différentes courses. C'est toujours un seul message par course, édité.

## Diffusion

23. La vague n'augmente que si la vague précédente a touché au moins un livreur. Sans livreur éligible, on
    réessaie toutes les 2 minutes sans changer de vague.
24. **Dès qu'un livreur devient éligible** (position reçue, `/dispo`, livraison, « bientôt libre »,
    annulation, course retirée), les courses en attente qui n'avaient trouvé personne dans leur vague courante
    sont rediffusées aussitôt.
25. L'état « aucun livreur » affiché au franchisé est gardé en mémoire : après un redémarrage, au pire, son
    message repasse à « envoyée aux livreurs ». Si aucun livreur n'est même en service, le texte est celui de
    la confirmation (« enregistrée — aucun livreur en service… »).
26. **Remise en diffusion** (annulation livreur, « Remettre en diffusion » du dispatch, exclusion) : les autres
    livreurs redeviennent sollicitables (leurs lignes `broadcasts` sont supprimées). Le livreur retiré reste
    exclu de cette course (sa ligne est gardée, en vague 0).
27. Le livreur qui a annulé : la notification au franchisé est une édition de son message de course (§17,
    « jamais un nouveau message par événement »). Le livreur retiré ou libéré, lui, reçoit un nouveau message
    (il est sur la route).
28. `/dispo` avec une position encore fraîche (partage en direct toujours actif) remet en service tout de
    suite. Toute nouvelle position (pas une mise à jour) met en service ; les mises à jour de position en direct
    ne changent jamais `on_duty`, pour qu'un `/pause` reste une pause.
29. Un livreur en pause peut prendre une course d'une proposition reçue avant sa pause (seule la règle
    « 1 + 1 » est vérifiée).
30. Distance au moment du « Livré » : calculée seulement si la position a moins de 10 minutes.

## Dispatch

31. Notification d'attribution au format du §9.4 (`🚴 #142 — Franchisé 3 → Livreur 4 — Paris 12e — 60 €`),
    plus riche que celui du §13.
32. `/encours` : un seul message, avec sous la liste une rangée de boutons par course (numérotée).
    Chaque action demande une confirmation dans un nouveau message.
33. Annulation forcée d'une course en cours → `cancelled_on_site` (comme le demande le §13) : le livreur est
    prévenu, son `cancel_count` n'est pas touché.
34. Exclusion d'un franchisé : ses courses en attente sont annulées sans message (il n'en reçoit plus aucun).
    Exclusion d'un livreur : ses courses sont rediffusées ; il ne reçoit que « Ton accès a été retiré. ».
    Ses commandes Telegram sont retirées du menu.
35. Alerte « course bloquée » : une seule fois par attribution (une course réattribuée peut alerter à nouveau).
36. Le message d'erreur générique n'est pas envoyé au dispatch lui-même (il reçoit déjà « ⚠️ Erreur technique »).
    Une indisponibilité de l'API Anthropic est aussi signalée au dispatch.

## Rétention

37. Les colonnes `raw_message` et `content` étant `NOT NULL`, elles sont remplacées par `[effacé]`.
    Les drafts de plus de 90 jours sont effacés de la même façon (texte brut et extraction).

## Tests

38. `test_lock.py` et `test_scenarios.py` tournent contre une base de test Supabase (`SUPABASE_URL_TEST`) ou
    un PostgREST local (`POSTGREST_URL_TEST`) — c'est la même API que Supabase, et c'est le vrai code de
    `db.py` qui est exercé. Les scénarios pilotent le vrai bot contre un faux serveur Telegram.
39. Les essais manuels sur Telegram demandés au §16 restent à faire : ils ne sont pas possibles depuis
    l'environnement de construction. La checklist est dans le README.
40. L'API Adresse n'était pas joignable depuis l'environnement de construction : le format de ses réponses est
    vérifié par des réponses simulées, pas en direct.

## Déploiement

41. `railway.toml` fixe la région Amsterdam (`ams`, l'identifiant actuel de Railway) et une seule réplique. Amsterdam est
    proche des serveurs Telegram et de la base Supabase créée à Paris (`eu-west-3`) ; la région par défaut
    d'un nouveau compte Railway est en Californie, ce qui ajoutait ~150 ms à chaque requête en base.

## Lecture des commandes sans IA

42. **Mode `regles`** (`EXTRACTION_MODE`), demandé par l'exploitant : la lecture des commandes se fait par
    règles fixes (`bot/services/rules_extraction.py`), sans appel à une IA. Même interface et même
    validation que l'extraction IA ; la fiche de confirmation reste le garde-fou. Le mode `ia` reste
    disponible en changeant une variable.
43. En mode `regles`, les vocaux et les captures d'écran sont refusés poliment : les lire demanderait de
    l'IA (Whisper, vision). Le message de bienvenue du franchisé conseille de séparer les informations par
    des virgules, avec le code postal et le signe € — c'est un conseil, pas une obligation.
44. Un nombre suivi d'un mot (« 50 mousseux ») est une quantité, jamais un prix : sans €, ni « prix », ni
    nombre seul en fin de message, le bot répond qu'il manque le prix plutôt que de deviner.
45. Correctif : le SDK `anthropic` 1.x n'accepte plus `temperature` en argument ; il passe désormais par
    `extra_body`. Les deux premières commandes réelles avaient échoué pour cette raison.

## Modèle de commande et catalogue

46. **Modèle** : quantité, produit, prix total par ligne ; ligne vide ; commentaire. L'adresse n'était pas
    placée dans la demande : elle est attendue sur les lignes avant les produits (en pratique la 1re).
    Le prix de la course est la somme des lignes ; une ligne sans prix rend le total manquant.
47. Le modèle est lu **sans IA dans les deux modes**. Un message qui n'y correspond pas (texte libre,
    commandes à puces) retombe sur la lecture habituelle.
48. Le commentaire devient le complément d'adresse : comme le digicode, seul le livreur qui a pris la
    course le voit.
49. **Catalogue** (table `products`) : reconnaissance par nom normalisé, autre écriture, nom contenu dans
    le texte, puis faute de frappe proche (similarité de lettres, sans IA). Un mot qui désigne plusieurs
    produits est gardé tel quel avec un avertissement ; un produit inconnu aussi. Catalogue vide : aucun
    avertissement.
50. Pas de prix dans le catalogue : le franchisé donne le prix total de chaque ligne.
