# Bot Discord des cours XTM / wXTM

Ce service récupère les cours de XTM et de wXTM depuis les marchés configurés :

- `XTM` = MinoTari (`minotari`), marché USDT **MEXC** ;
- `wXTM` = Wrapped MinoTari (`wrapped-minotari`), pool **Uniswap V4 sur Ethereum** :
  `0x530581e8b4dff575d96af96cbfb74d0cc4ed0ec0cb7c953f491c7a60a787412d`.

Le cours wXTM est lu directement dans ce pool par GeckoTerminal. La source donne
un prix équivalent en USD depuis la paire wXTM/ETH ; l'application l'affiche en
USDT à parité indicative (USD≈USDT), avec cinq décimales au maximum. Sa
variation 24 h pour les alertes vient également de ce pool. XTM reste lu sur
MEXC en USDT.

Il maintient une seule box dans le salon Discord `1370700962695610430` : à chaque
cycle, il publie le nouveau message puis supprime les anciennes boxes afin que
les cours restent en bas du salon. Le service est
conçu pour fonctionner dans un worker cloud, donc ton PC peut être éteint.

Le bot surveille séparément les variations 24 h de `XTM` et `wXTM` fournies par
leurs marchés respectifs. Si au moins l'un atteint `+10 %`, une alerte verte est publiée dans
`mog-post` (`1163364187796426776`). Si au moins l'un atteint
`-10 %`, une alerte rouge distincte est publiée. À chaque actualisation d'une
alerte active, le bot publie un nouveau message puis supprime l'ancien pour
faire remonter l'alerte en bas du salon. Chaque alerte ne
nomme et n'affiche que les actifs qui ont franchi son seuil ; si les deux
franchissent le même seuil, elle affiche les deux et leurs pourcentages/prix.
Le titre de l'alerte commence par une pastille puis le signe et la variation
entre deux alertes, par exemple `🟢 + 0.20%` ou `🔴 - 1.30%`. Si XTM et wXTM
sont concernés, le mouvement dominant apparaît d'abord et l'autre actif suit.
Une variation arrondie à zéro s'affiche comme `🟡 ~ 0.00%`. Ce format est
identique pour XTM et wXTM.
À côté du repère coloré, l'alerte affiche aussi l'écart entre la variation
24 h actuelle et celle de l'alerte précédente, séparément pour les deux actifs.
Les signes du pourcentage d'alerte restent accolés au nom de l'actif
(`XTM+10%`, `XTM-10%`).
Les cours XTM et wXTM sont affichés avec exactement cinq chiffres après le
point dans la box des cours.
Les deux directions peuvent déclencher lors de la même exécution. Les
pourcentages affichés sont plafonnés à `+300 %` et `-300 %`. Quand un actif
repasse sous le seuil, il disparaît de la prochaine mise à jour si un autre
actif reste au-dessus ; si aucun actif ne franchit encore le seuil, l'alerte
de cette direction est supprimée de `mog-post`. Elle ne réapparaît qu'au
prochain franchissement de `+10 %` ou `-10 %`. Une alerte est conservée si une
variation 24 h configurée est momentanément indisponible. `@everyone` ne
notifie qu'aux paliers franchis de 10 % en 10 % (±10,
±20, …, ±300), sans reping à chaque actualisation dans un même palier. Le
dernier palier notifié est conservé dans le pied de l'embed pour éviter les
doublons malgré le remplacement du message. Ce message est un signal automatique
et ne constitue pas un conseil financier.
Le texte de l'alerte apparaît uniquement dans le titre blanc en gras de l'embed ;
quand un palier ping @everyone, le contenu du message ne contient que cette mention.

Un cronjob dédié peut lancer le workflow avec `snapshot_only = true` toutes les
4 heures. Dans ce mode, le bot publie une nouvelle embed ponctuelle dans
`mog-post` avec les deux prix du moment, sans modifier les snapshots précédents.

## Discord

Le bot doit être invité sur le serveur et avoir `Voir le salon`, `Envoyer des
messages` et `Intégrer des liens` dans les deux salons concernés. Il n'a besoin
d'aucun intent privilégié.

Le token doit être ajouté comme secret d'environnement sous le nom
`DISCORD_BOT_TOKEN`. Comme un token Discord déjà partagé doit être considéré
comme compromis, régénère-le dans le portail développeur Discord avant de le
mettre dans l'hébergeur.

## Déploiement cloud avec Docker

1. Copie `.env.example` vers `.env` sur l'hébergeur et renseigne
   `DISCORD_BOT_TOKEN`.
2. Conserve `DISCORD_CHANNEL_ID=1370700962695610430`.
3. Lance le conteneur avec `docker compose up -d --build`.
4. Aucun volume persistant n'est nécessaire : chaque actualisation réutilise
   la même embed de prix.

Un service de type **worker/background service** convient mieux qu'un site web
qui s'endort. Railway, Render Background Worker, Fly.io ou un VPS peuvent
exécuter ce Dockerfile. La plateforme doit rester active ; un hébergement qui
s'endort ne garantit pas le rythme de 5 minutes.

## Option gratuite : GitHub Actions

Le dossier contient aussi `.github/workflows/price-scheduler.yml`. Cette variante ne
laisse pas un bot connecté en permanence : le planificateur externe démarre un job
toutes les minutes, publie une nouvelle box dans le salon des prix, supprime
l'ancienne, puis s'arrête. C'est gratuit sur un
dépôt public et ne dépend pas de ton PC. Les horaires GitHub peuvent toutefois
être décalés en période de charge.

Le workflow est autonome : il utilise le script `src/update_once.py`, qui ne
requiert ni Python ni Discord ouverts sur ton ordinateur. Le token reste dans le
secret GitHub `DISCORD_BOT_TOKEN` et n'est jamais écrit dans le dépôt. Le job
met à jour le prix et remplace chaque alerte active par un nouveau message,
puis supprime le message d'alerte précédent.

Pour l'utiliser :

1. Crée un dépôt GitHub public ou privé et mets-y le contenu de ce dossier
   (le dossier `.github` doit être à la racine du dépôt).
2. Dans `Settings > Secrets and variables > Actions`, ajoute le secret
   `DISCORD_BOT_TOKEN`.
3. Ajoute éventuellement `COINGECKO_API_KEY` comme second secret, si tu en as
   une ; le service fonctionne aussi avec l'API publique dans la limite de ses
   quotas.
4. Dans l'onglet `Actions`, lance `Mettre à jour les cours XTM` une première
   fois avec `Run workflow` pour tester immédiatement.

Pour tester ponctuellement les deux alertes dans `mog-post` sans attendre une
variation réelle de 10 %, lance le workflow manuellement avec l'option
`test_alerts = both_once`. Il envoie une seule alerte verte et une seule alerte
rouge, puis le fonctionnement planifié reste inchangé.

Pour vérifier les remplacements de messages sur quatre minutes, lance le
workflow avec `test_alerts = progression_4min`. Il simule `+10, +25, +100,
+200, +300 %` et les mêmes valeurs négatives. Les messages portent la mention
`[TEST FICTIF 4 MIN]`, affichent des prix XTM fictifs cohérents avec le taux
(une baisse simulée ne peut pas faire descendre un prix sous zéro). `@everyone`
est notifié aux paliers de 10 % atteints, sans ping à chaque minute. L'option
`verify_test` relit ensuite les deux messages finaux pour confirmer qu'il n'y en
a qu'un par couleur, avec le palier de notification et le prix fictif final attendus.

Pour tester un snapshot, lance le workflow avec `snapshot_only = true`. Le
cronjob de quatre heures utilise cette option et publie dans `mog-post` sans
remplacer la box actualisée dans le salon des prix.

Le bot doit avoir `Voir le salon`, `Envoyer des messages`, `Lire l'historique
des messages` et `Intégrer des liens` dans le salon des prix et dans
`mog-post`.

## Test local ponctuel

```powershell
Copy-Item .env.example .env
# édite .env, puis :
docker compose up --build
```

Ne versionne pas `.env` et ne colle jamais le token dans le code.

## Vérification

```powershell
python -m pip install -r requirements-dev.txt
python -m pytest -q
```

Le service refuse de démarrer sans token, réessaie les réponses limitées par
CoinGecko ou GeckoTerminal, conserve les erreurs d'un actif séparément et ne
remplace pas le message si aucun cours valide n'est disponible.
