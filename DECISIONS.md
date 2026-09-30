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

## Google Sheets

51. Liaison par **Google Apps Script** attaché à la feuille plutôt que par un compte de service Google
    Cloud : moins d'étapes pour l'exploitant, aucune clé Google à stocker. Le script est protégé par un
    secret partagé et ne sait qu'ajouter des lignes.
52. Une ligne par course **livrée**, mêmes colonnes que le CSV du journal. Envoi en arrière-plan après
    « Livré » : Google lent ou en panne ne ralentit jamais le livreur. Un échec écrit un événement
    `sheet_error` ; `/synchro` renvoie la nuit, le script ignorant les courses déjà présentes.
53. La rétention de 90 jours (§15) ne s'applique pas à la feuille : les compléments (digicodes) y restent
    tant que l'exploitant ne les efface pas.
52. **Feuille existante de l'exploitant** (« Dispatch », un onglet par jour, 40 lignes de commande, 3 produits
    par ligne, totaux au statut « OK ») : le script écrit dans ces onglets plutôt que dans un onglet « Courses »
    à part. L'onglet est celui de la nuit (même découpage que le récap). Une course livrée a le statut « OK » ;
    le mode de paiement, inconnu du bot, reste vide.
53. Le script est un projet **autonome** (script.google.com) qui ouvre la feuille par son identifiant : sur
    téléphone, le lien de la feuille ouvre l'appli Sheets, qui n'a pas le menu Extensions.
54. Noms : les listes de la feuille (PNO, Livreur A…) ne correspondent pas aux noms du bot. Une table
    PARAMETRES!Q:R, modifiable dans l'appli Sheets, fait la correspondance (nom du bot ou vrai nom → nom de la
    feuille). Sans correspondance, le nom du bot est écrit et se corrige avec le menu déroulant.
55. Produits : le bot redécoupe le texte des produits (« 2 DIV (60 €) + 1 KT (5 €) ») et met le nom exact du
    catalogue. Sans prix par ligne (commande en texte libre), le total va sur le premier produit : le total de
    la feuille reste juste.
56. Anti-doublon par une note « Bot #142 » sur la cellule Vendeur, invisible dans le tableau : pas de colonne
    ajoutée à la feuille.
57. **Vendeur = « TOTAL »**, demandé par l'exploitant, sur toutes les lignes de commande des 7 onglets. Le menu
    Vendeur accepte TOTAL (liste PARAMETRES!M4:M8, sans toucher à la liste des vendeurs). Une ligne dont seule
    la case Vendeur est remplie par « TOTAL » compte comme libre ; le script remet « TOTAL » dans les cases
    Vendeur vidées. La correspondance des noms ne sert plus qu'aux livreurs.

## Modification de la commande par le livreur

58. Bouton **✏️ Modifier la commande** sur la fiche du livreur, tant que la course est en cours (`assigned`).
    Tout se fait par boutons dans le même message : quantités ➖/➕, sélecteur des produits du catalogue,
    prix de ligne par pas de ±10/20/50 €. Taper un prix reste possible pendant le réglage d'une ligne.
59. Le brouillon de modification est gardé dans l'état de conversation du livreur (`editing_order`,
    30 minutes), comme les autres états : il survit à un redémarrage. Un bouton d'un éditeur expiré remet la
    fiche. Les appuis d'un même livreur passent par son verrou : pas de modification perdue en tapant vite.
60. Changer la quantité garde le prix à l'unité. Un produit ajouté arrive sans prix et son réglage s'ouvre
    tout de suite ; « Valider » est refusé tant qu'une ligne n'a pas de prix.
61. Les produits sont réécrits au format du modèle (« 3 DIV (90 €) + 1 KT (5 €) ») et le prix de la course
    devient la somme des lignes : récap, journal et Google Sheets suivent sans autre changement.
62. Le franchisé reçoit un **nouveau message** (« modifiée par … sur place », avec l'ancien prix) en plus de
    l'édition de son message de course : un changement d'argent ne doit pas passer inaperçu (exception
    assumée au principe « un seul message par course »). Le dispatch est prévenu ; un événement
    `course_modified` garde l'avant et l'après.
63. Fiche du livreur : un produit par ligne quand la commande en a plusieurs, plus lisible sur place.
64. **Prix de 10 en 10 €** (règle de l'exploitant) dans l'éditeur du livreur : boutons ±10/20/50 €, prix tapé
    refusé s'il n'est pas un multiple de 10, prix recalculé après ➖/➕ arrondi à 10 € près, et « Valider »
    refusé si une ligne modifiée garde un prix hors pas. Une commande ouverte puis validée sans changement
    n'est pas contrôlée (rien n'est enregistré).
65. La règle des 10 € vaut aussi pour les **commandes des franchisés** : total et prix de ligne écrits
    (« 1 KT (5 €) ») doivent être des multiples de 10 €. Sinon, pas de fiche : un message explique quoi
    corriger, comme pour une information manquante (le franchisé renvoie la commande, ou la corrige avec
    ✏️ Corriger). L'exemple du modèle passe à « 1 coca 10 ».

## Rechargement des livreurs

66. **Nouveau rôle « ravitailleur »** (inscription et validation comme les autres ; nom visible « Ravitailleur 1 »).
    `/recharge` est ouvert au ravitailleur et au dispatch. Livreurs et franchisés n'y ont pas accès.
67. Un rechargement = un livreur, un type (chargement, reprise, cash seul), un box (sauf cash seul), des
    quantités positives et le cash récupéré. Table `restocks` (migration 003) ; la reprise est rendue
    négative seulement dans la feuille, comme le veut le tableau (+ chargé / − repris).
68. Tout par boutons dans un seul message, brouillon dans l'état de conversation `restocking` (30 min), verrou
    par utilisateur. Seul le montant du cash peut être tapé. Pas de règle des 10 € sur le cash : c'est de
    l'argent récupéré, pas un prix.
69. Le livreur reçoit un message à chaque rechargement (il doit savoir ce qu'on lui a compté) ; le dispatch est
    prévenu quand c'est un ravitailleur qui saisit.
70. **Un seul script Google** pour les deux tableaux : les lignes `type: recharge` vont dans le tableau
    Rechargement (`RECHARGE_SPREADSHEET_ID`). Même secret, même adresse. Colonne du produit trouvée par son nom
    en ligne 2 ; un produit absent des colonnes est une erreur (rien n'est écrit à moitié). Ligne libre = rien
    entre C et T (un livreur ou un box laissé seul ne bloque pas la ligne). Anti-doublon par la note
    « Bot R#7 » sur la cellule Livreur.
71. Même découpage des nuits que les courses : un rechargement à 2h dans la nuit de lundi à mardi va dans « Lundi ».

## Pleins pouvoirs des franchisés

72. **Franchisés = administrateurs**, à la demande de l'exploitant : mêmes commandes que le dispatch (`/encours`,
    `/livreurs`, `/recap`, `/journal`, `/users`, `/recharge`, `/synchro`, `/exclure`, `/reactiver`, catalogue)
    et validation des inscriptions. C'est un choix assumé qui lève le mur de confidentialité du §12 pour eux :
    un franchisé voit les vrais noms dans `/users`. Personne ne peut s'exclure soi-même, ni exclure le dispatch.
    Les notifications automatiques (nouvelle inscription, course prise, erreurs, journal de 6h) restent
    envoyées au seul dispatch ; `/journal` est envoyé à celui qui le demande.
73. **`/livreurs`** : un admin met un livreur en service (🟢) ou en pause (⏸). Mis en service ainsi
    (`duty_forced`, migration 004), le livreur reçoit les courses même sans position partagée — proposées
    « distance inconnue », après les livreurs dont on connaît la position — et le contrôle des positions ne le
    met pas hors service. Son propre `/pause`, ou une pause par un admin, lève ce mode.
74. **👤 Attribuer** (dans `/encours`, et sur le message de course du franchisé) : l'admin choisit le livreur
    d'une course en attente, sans passer par « Je prends » ni par les vagues. Même verrou en base que la prise
    (`take_course`) : si un livreur a pris la course entre-temps, rien ne se passe. La règle « une course à la
    fois » ne s'applique pas à une attribution par un admin.
75. Sur son message de course, le franchisé a aussi **✏️ Modifier** (même éditeur que le livreur, course en
    attente ou en cours) et **📦 Livrée** (avec confirmation, comme dans `/encours`). Une modification par un
    admin prévient le livreur (nouveau message + fiche mise à jour) et le dispatch.
76. Au démarrage, les menus de commandes Telegram de tous les utilisateurs actifs sont remis à jour.

## Alerte d'arrivée

77. **« Le livreur arrive »** : à chaque position reçue, pour chaque course en cours du livreur, alerte au
    franchisé (nouveau message, pour qu'il sonne) et au dispatch quand la distance à l'adresse passe sous
    `ARRIVAL_NOTIFY_METERS` (500 m) **ou** le temps estimé sous `ARRIVAL_NOTIFY_MINUTES` (5 min). Une seule fois
    par attribution (événement `arrival_notified`, avec la date d'attribution : une course réattribuée peut
    alerter à nouveau).
78. Temps estimé = distance à vol d'oiseau / vitesse. La vitesse est mesurée entre les deux dernières positions
    (échantillon de 5 s à 10 min, entre 1 et 20 m/s) ; sinon 15 km/h, moyenne d'un deux-roues en ville. C'est
    une estimation : pas d'appel à un service d'itinéraires (coût, dépendance), la marge de 500 m / 5 min suffit.
79. Une erreur dans l'alerte n'interrompt jamais l'enregistrement de la position ni le « bientôt libre ».

## Alertes de stock

80. **Source du stock : l'onglet SOLDES du tableau Rechargement** (« stock actuel chez le livreur »), et non un
    calcul du bot : l'exploitant saisit aussi des mouvements et des ventes à la main dans les feuilles, que le
    bot ne voit pas. Le script Google répond à `action: stock` (même adresse, même secret), avec la table de
    noms PARAMETRES J/K du tableau Rechargement.
81. À l'attribution (prise par un livreur ou attribution par un admin) : alerte si la course prend tout ce qui
    reste d'un produit (« les 2 derniers »), plus qu'il n'en reste, ou un produit absent.
82. À la livraison : le stock est lu **avant** l'envoi de la vente à la feuille Dispatch (même tâche, dans cet
    ordre), puis on retire les quantités livrées ; à 0 ou moins : « n'a plus de … sur lui ». Limite connue : une
    vente précédente pas encore remontée dans SOLDES (IMPORTRANGE entre les feuilles) peut retarder une alerte.
83. Prévenus : franchisé de la course, livreur, ravitailleurs actifs et dispatch — celui qui recharge doit
    savoir. Les lectures se font en tâche de fond : la prise et la livraison ne sont jamais ralenties ni
    bloquées, et sans stock lisible il n'y a simplement pas d'alerte (`STOCK_ALERTS=0` pour couper).
84. **Stock calculé en parallèle des feuilles** (demande de l'exploitant : pas de retard). SOLDES ① dépend
    d'un IMPORTRANGE (ventes de la feuille Dispatch importées dans Rechargement) qui peut prendre du temps. Le
    script calcule donc lui-même, à chaque demande : chargé net (SOLDES ②, formules locales au tableau
    Rechargement, immédiates) − ventes au statut OK lues directement dans les 7 onglets de Dispatch (colonnes
    Produit/Qté 1 à 3). Nom du livreur comparé avec les deux tables de noms (Dispatch Q/R et Rechargement J/K).
    Sans section ②, repli sur SOLDES ①.
85. Le bot ajoute les mouvements qu'il connaît mais que les feuilles n'ont pas encore (« en vol ») : une vente
    est comptée dès la livraison et jusqu'à ce que la feuille confirme l'avoir écrite ; idem pour un
    rechargement. Un mouvement jamais confirmé (feuille injoignable) est gardé 2 h, le temps d'un /synchro.
86. À l'attribution, le stock disponible retire les quantités des autres courses du livreur, attribuées mais pas
    encore livrées (cas « 1 + 1 » ou attribution par un admin) : « c'est le dernier US » tient compte de ce qu'il
    doit déjà livrer.

## Noms de feuille

87. **Le nom d'un livreur (ou ravitailleur) dans le bot est son nom dans les feuilles** (« Livreur A »…),
    choisi par un admin parmi `LIVREUR_NAMES` / `RAVITAILLEUR_NAMES` : à la validation (premier nom libre
    proposé d'office + boutons pour changer) et à tout moment depuis `/livreurs` → 🏷. Plus de table de
    correspondance à tenir côté feuilles, donc plus d'incohérence entre bot, Dispatch et Rechargement.
88. Unicité : un nom porté par un autre utilisateur non exclu est refusé (🔒). Un exclu libère son nom.
    Liste vide (`LIVREUR_NAMES=-`) : ancien comportement « Livreur 1 », « Livreur 2 »…
89. Les lignes déjà écrites dans les feuilles gardent l'ancien nom ; le livreur est prévenu de son nouveau nom.

## Consultation du stock

90. **`/stock`** (admins et ravitailleurs) : boutons par box, « Tous les box », « Livreurs » ; `/stock box 1`
    répond directement. Lecture par le script (actions `stock_box` et `stock_livreurs`, même adresse et secret).
91. Box : ORGA ④ tel que calculé par la feuille. Ses sorties vers les livreurs (③) sont des formules locales
    au tableau Rechargement, donc immédiates ; le stock initial et les mouvements viennent du Compta par
    IMPORTRANGE mais changent rarement. Le bot retire en plus les rechargements qu'il n'a pas encore écrits
    (un chargement ôte au box ce qu'il donne au livreur, une reprise le lui rend).
92. « Tous les box » ajoute les produits dont le total (box + livreurs) est sous le seuil d'ORGA ⑤.
93. Livreurs : même calcul direct que pour les alertes (chargé net − ventes OK lues dans Dispatch), plus les
    mouvements du bot pas encore écrits.

## Paiement à la livraison

94. Le mode de paiement (**Espèces** / **Virement**, les deux valeurs de la liste PAIEMENT de la feuille) est
    demandé au moment du « 📦 Livré », par un second écran sur le même message (↩️ Retour possible) : un appui de
    plus, mais la caisse en dépend. Même choix pour un admin qui marque la course livrée. Stocké dans
    `courses.payment` (migration 005) ; les courses livrées avant restent sans mode.

## Caisse

95. **Cash qu'un livreur doit avoir** = ventes OK en espèces − dépenses (charges et avances sur paye) − cash
    récupéré : la définition de SOLDES ④, reprise telle quelle. Le script la calcule en direct (action
    `cash_livreurs`) à partir des 7 onglets de chaque fichier, sans IMPORTRANGE ; le bot ajoute ses mouvements
    en vol (livraison en espèces +, dépense −, cash récupéré −) avec le même registre que le stock (clé
    `cash:<livreur>`).
96. `/depense` écrit dans la zone DÉPENSES LIVREURS existante de l'onglet de la nuit (lignes 46 à 57, Type =
    « Charges » ou « Paye » comme la liste TYPE DÉPENSE), note « Bot D#… » contre les doublons, `/synchro` la
    renvoie. Les montants ne sont pas limités aux multiples de 10 € (essence à 37,50 €).
97. **💶 Récupérer** dans `/caisse` ne crée rien directement : il ouvre le rechargement « cash seulement »
    prérempli, pour que le ravitailleur confirme le montant réellement remis.

## Dispatch selon le stock

98. Le stock **reclasse** les livreurs, il n'en **exclut** aucun : la feuille peut être en retard ou incomplète,
    et une course ne doit jamais rester sans livreur à cause d'elle. Ordre : a tout → stock inconnu → manque
    quelque chose ; la distance départage dans chaque groupe. Produits absents des colonnes de la feuille :
    ignorés (sinon « coca » ferait passer tout le monde en « manque »).
99. Alerte « aucun livreur n'a tout » : une fois par course (mémoire du processus), aux ravitailleurs et au
    dispatch — pas au franchisé, qui n'y peut rien. Lecture de la feuille gardée 60 s et oubliée dès qu'un
    mouvement du bot est confirmé écrit, pour ne pas compter deux fois.

## Clôture de la semaine

100. `/cloture` est manuelle (rappel le lundi à 6h05) : une remise à zéro automatique effacerait une semaine
     dont le cash n'a pas été récupéré. Vérification d'abord, confirmation ensuite ; double appui refusé
     10 min.
101. Ordre du script : tout lire, **archiver** (copie des 3 fichiers dans Drive), puis écrire. Si la copie
     échoue, rien n'est effacé. Le stock initial du COMPTA n'est écrit qu'en colonnes B à P (A = formules).
102. Stock chez les livreurs **reporté** (ligne « Report clôture » sans box : compté dans le chargé net du
     livreur, pas dans les sorties des box). Cash des livreurs et reste du ravitailleur **non reportés** :
     pas de ligne propre pour le faire sans fausser charges ou récupérations ; ils sont signalés avant de
     confirmer et restent dans l'archive.
103. Après la clôture, le bot oublie ses mouvements en vol : ils appartenaient à la semaine archivée.

## Débrief de la journée et nom du reset

104. `/cloture` renommée **`/reset`** à la demande de l'exploitant ; l'ancien nom reste accepté (sans être
     affiché) pour ne pas surprendre. Le nom de l'action du script (`cloture`) ne change pas : pas besoin de
     recoller le script.
105. **`/close`** = débrief seulement : il ne met personne en pause et ne touche ni à la base ni aux feuilles.
     Journée visée : la nuit en cours ; en journée (entre fin et début de nuit), la nuit qui vient de finir,
     sauf si des courses ont déjà été livrées depuis la fin de nuit (activité de jour). Le cash à récupérer
     affiché est celui de la feuille, donc le cumul de la semaine, et il est présenté comme tel.
106. **Prix libre à la modification** (demande de l'exploitant) : ➖ / ➕ ne changent plus que la quantité ; le
     prix de la ligne n'est jamais recalculé (remplace le « prix unitaire gardé » des n° 60 et 64). Le livreur
     ou l'admin met le prix qu'il veut en touchant le produit ; la règle des 10 € reste.

## Moyen de déplacement

107. Trois modes, décidés par l'exploitant : **transport** (à pied et transports en commun, c'est pareil),
     **deux-roues**, **voiture**. Déclaré au `/dispo` par boutons sous le message (pas de message de plus) et
     retenu d'un jour sur l'autre ; un livreur sans mode est traité comme un deux-roues (comportement d'avant).
108. Dispatch : tri par **temps de trajet estimé** (transport : le plus court entre marcher à 4,5 km/h et
     10 min d'accès + métro à 25 km/h ; deux-roues : 20 km/h ; voiture : 4 min de stationnement + 17 km/h),
     et rayon de `TRANSPORT_MAX_KM` (5 km) pour un livreur en transport. Le classement selon le stock (n° 98)
     garde cet ordre dans chaque groupe.
109. **Métro reconnu sans historique de positions** : la position précédente et son heure (déjà en base)
     suffisent. Trou de 2 min 30 à 30 min + saut ≥ 800 m à 12–45 km/h + deux stations **différentes** à
     moins de 400 m (métro et RER seulement, données IDFM téléchargées au démarrage). Sans liste de stations,
     trou + saut + vitesse seuls. Seulement pendant une course attribuée avant le trou, et seulement sur des
     mises à jour de la position en direct (pas un nouveau partage après une pause).
110. **Véhicule** : 3 positions de suite > 25 km/h espacées de moins de 90 s (un livreur en métro n'envoie rien
     sous terre ; un bus dépasse rarement 25 km/h de façon continue). Limite connue : RER en surface.
     **À pied** : ≥ 4 positions, jamais > 8 km/h, constaté à la livraison (statistiques en mémoire, perdues à un
     redémarrage : alors pas de conclusion).
111. Le dispatch n'est prévenu que d'une **contradiction** (métro alors que deux-roues/voiture déclaré,
     véhicule alors que transport déclaré), une fois par course et par mode. Les autres détections sont
     seulement enregistrées (`courses.detected_mode`, événement `transport_detected`) et montrées au `/close`.

## Retirer quelqu'un

112. **`/supprimer`** en plus de `/exclure` : exclure garde le compte (réactivable) ; supprimer le passe au
     statut `deleted`, efface son vrai nom et son @pseudo, et **détache son identifiant Telegram** (rendu
     négatif) : un `/start` du même compte crée une inscription neuve, dans le rôle choisi. La ligne reste en
     base pour l'historique (courses, récap, journal), sans jamais apparaître dans les listes.
113. Retirer l'accès (les deux cas) : le bot **efface les messages qu'il a envoyés** à la personne. Il note
     chaque message envoyé à un utilisateur (hors dispatch) dans `bot_messages`, gardés 48 h (limite de
     Telegram pour qu'un bot efface un message) ; l'effacement se fait par lots de 100. Ensuite
     `messaging.send` n'envoie plus rien à un compte exclu ou supprimé (sauf l'avis de retrait lui-même).
114. `/exclure` renommée **`/bannir`** à la demande de l'exploitant (l'ancien nom reste accepté). Un banni ne
     reçoit plus aucune réponse, même à `/start` : la conversation est morte pour lui. Telegram ne permet pas
     à un bot de se cacher d'un utilisateur précis ; un nouveau compte Telegram reste bloqué à la validation.

## Positions des livreurs pour le dispatch

115. La position garde **en direct / fixe** (`live`) et la **fin du partage** choisie dans Telegram
     (`live_until`, calculée au premier message : heure + `live_period` ; « sans fin » = vide). Une mise à jour
     en direct ne touche pas `live_until`. Une position fixe envoyée pour se mettre en service prévient le
     dispatch immédiatement (le livreur, lui, était déjà prévenu).
116. **📍** envoie un *venue* Telegram (point + titre + adresse) : ouvrable dans Maps d'un appui, sans lien
     externe. Adresse la plus proche par l'API Adresse (`/reverse/`, même adresse de base que la recherche) ;
     sans réponse, les coordonnées.
117. Silence de position : alerte au dispatch et rappel au livreur à `POSITION_ALERT_MINUTES` (10 min), une
     fois par position (mémoire du processus), avant la sortie de service à 30 min, elle aussi signalée
     maintenant au dispatch. Vérification toutes les 2 min au lieu de 5. Un livreur mis en service par un
     admin (sans position) n'est jamais concerné.
118. **Partage « jusqu'à ce que je l'arrête »** plutôt que 8 heures : Telegram n'autorise aucun bot à lancer le
     partage en direct (ni un bouton qui le ferait) ; le bouton « envoyer ma position » ne donne qu'une position
     fixe, à revalider sans cesse (refusé par l'exploitant). Le livreur partage donc une seule fois, sans fin ;
     `/dispo` le met ensuite en service directement. Marche à suivre dans la bienvenue et `/dispo`, relance
     3 min après un `/dispo` resté sans position (job par livreur, remplacé à chaque `/dispo`), dispatch prévenu.
