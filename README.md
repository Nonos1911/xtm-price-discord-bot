# Bot Discord des cours XTM / wXTM

Ce service récupère le meilleur marché `USDT` indexé par CoinGecko pour :

- `XTM` = MinoTari (`minotari`), marché USDT **MEXC** ;
- `wXTM` = Wrapped MinoTari (`wrapped-minotari`), marché USDT **Gate**.

Il publie un nouvel embed dans le salon Discord `1370700962695610430` toutes
les 5 minutes. Les anciens messages restent dans le salon. Le service est
conçu pour fonctionner dans un worker cloud, donc ton PC peut être éteint.

Si la variation 24 h de XTM atteint `+10 %`, le bot envoie `Alert XTM+10%`
dans `mog-post` (`1163364187796426776`) cinq fois, à une minute d'intervalle,
avec un embed vert. À `-10 %` ou moins, il envoie `Alert XTM-10%  GO BUY`
avec un embed rouge, avec la même répétition. Ce message est un signal
automatique et ne constitue pas un conseil financier. Chaque embed d'alerte
contient aussi les deux cours XTM/USDT et wXTM/USDT utilisés au déclenchement.

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
4. Aucun volume persistant n'est nécessaire : chaque actualisation crée un
   nouveau message.

Un service de type **worker/background service** convient mieux qu'un site web
qui s'endort. Railway, Render Background Worker, Fly.io ou un VPS peuvent
exécuter ce Dockerfile. La plateforme doit rester active ; un hébergement qui
s'endort ne garantit pas le rythme de 5 minutes.

## Option gratuite : GitHub Actions

Le dossier contient aussi `.github/workflows/update-xtm.yml`. Cette variante ne
laisse pas un bot connecté en permanence : GitHub démarre un job toutes les
5 minutes (à minutes décalées pour limiter les retards du planificateur), envoie
un nouveau message Discord, puis l'arrête. C'est gratuit sur un
dépôt public et ne dépend pas de ton PC. Les horaires GitHub peuvent toutefois
être décalés en période de charge.

Le workflow est autonome : il utilise le script `src/update_once.py`, qui ne
requiert ni Python ni Discord ouverts sur ton ordinateur. Le token reste dans le
secret GitHub `DISCORD_BOT_TOKEN` et n'est jamais écrit dans le dépôt. Le job
dispose de six minutes, ce qui couvre la publication du prix et les cinq envois
d'alerte espacés d'une minute.

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

Le bot doit avoir `Voir le salon`, `Envoyer des messages` et `Intégrer des
liens` dans le salon des prix et dans `mog-post`. La permission de lire
l'historique n'est pas nécessaire.

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
