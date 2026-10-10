# Instructions pour Claude

## Règle absolue : catalogue des produits (privé)

Décision de l'exploitant, sans exception :

- **Ne jamais consulter** les noms ni les surnoms des produits du catalogue : pas de lecture de la table
  `products` en production (Supabase, SQL, PostgREST), pas de `/produits`, pas d'export.
- **Ne jamais citer** un surnom de produit, dans une réponse, un exemple, un message de commit, une PR ou
  la documentation.
- **Ne jamais modifier** le catalogue : ni les noms, ni les surnoms, ni l'ajout ou la suppression de
  produits, que ce soit dans la base, par le bot ou dans les feuilles Google.
- Si une évolution du code doit toucher au catalogue (structure de la table `products`, reconnaissance
  des produits…), **demander d'abord l'accord de l'exploitant**.

Les produits créés dans la base de test locale par les tests automatisés ne sont pas concernés.
