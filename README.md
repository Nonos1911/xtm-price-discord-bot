# Bot Discord des cours XTM / wXTM

Ce service récupère le meilleur marché `USDT` indexé par CoinGecko pour :

- `XTM` = MinoTari (`minotari`), marché USDT **MEXC** ;
- `wXTM` = Wrapped MinoTari (`wrapped-minotari`), marché USDT **Gate**.

Il maintient un seul embed dans le salon Discord `1370700962695610430` et le
met à jour toutes les minutes. Les anciens embeds de cours sont nettoyés. Le service est
conçu pour fonctionner dans un worker cloud, donc ton PC peut être éteint.

Si la variation 24 h de XTM atteint `+10 %`, le bot crée une seule alerte verte
dans `mog-post` (`1163364187796426776`). Tant que la variation reste à `+10 %`
ou plus, il actualise ce même message avec le pourcentage courant, plafonné à
`+300 %`. À `-10 %` ou moins, il fait de même avec une seule alerte rouge,
affichant la variation de `-10 %` à `-300 %`. Si la variation repasse sous le
seuil correspondant, le bot cesse d'actualiser l'alerte et la laisse telle
quelle. Les modifications ne renvoient pas de nouvelle notification
`@everyone`. Ce message est un signal automatique et ne constitue pas un conseil
financier. Chaque embed d'alerte contient aussi les cours XTM/USDT et wXTM/USDT.

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
toutes les minutes, met à jour une seule embed dans le salon des prix, puis l'arrête. C'est gratuit sur un
dépôt public et ne dépend pas de ton PC. Les horaires GitHub peuvent toutefois
être décalés en période de charge.

Le workflow est autonome : il utilise le script `src/update_once.py`, qui ne
requiert ni Python ni Discord ouverts sur ton ordinateur. Le token reste dans le
secret GitHub `DISCORD_BOT_TOKEN` et n'est jamais écrit dans le dépôt. Le job
met à jour le prix et, si nécessaire, édite le message d'alerte existant au lieu
d'en publier plusieurs.

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
CoinGecko, conserve les erreurs d'un actif séparément et ne remplace pas le
message si aucun cours USDT n'est disponible.
