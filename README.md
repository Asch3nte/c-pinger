# ClaudePing

[![Release](https://img.shields.io/github/v/release/Asch3nte/c-pinger)](https://github.com/Asch3nte/c-pinger/releases/latest)

Démarre automatiquement le compteur de quota Claude Pro (5h) sans intervention manuelle.

## Comment ça marche

Claude Pro fonctionne avec un système de quota par session de 5h :
- Le compteur **ne démarre qu'après le premier message** envoyé à Claude
- Le reset se produit **exactement 5h après ce premier message**
- Toutes les surfaces Claude (claude.ai, Claude Code…) partagent le même quota

ClaudePing surveille le quota en arrière-plan et envoie automatiquement un
message minimal dès que le reset est disponible, pour maximiser le temps de
quota utile — et gère **plusieurs comptes Claude en parallèle**, chacun avec
son propre cycle probe/ping, isolé des autres (voir
[Plusieurs comptes en parallèle](#plusieurs-comptes-claude-en-parallèle)).

## Trois concepts clés

| Concept | Rôle | Appelle Claude ? |
|---------|------|-----------------|
| **Probe** | Interroge `claude /usage` pour lire le % de quota et l'heure exacte du prochain reset | Oui, toutes les N minutes (configurable, défaut 30 min) |
| **Ping** | Envoie `reply with just: ok` au moment du reset pour relancer la session 5h | Oui, une fois par reset |
| **Fallback horaire** | Si le probe échoue à détecter l'heure de reset, programme un ping à une heure fixe chaque jour | Non — c'est juste un planning de secours |

```
Probe toutes les 30 min
    │
    ├── Quota disponible → parse l'heure du prochain reset
    │       └── Schedule un ping 30s après le reset
    │               └── Ping envoyé → session 5h redémarrée ✅
    │
    └── Probe échoue / pas de reset détecté
            └── Fallback : ping programmé à l'heure fixe configurée
```

## Installation

**Prérequis** : [Claude Code CLI](https://claude.ai/code) installé et connecté (`claude auth login`).

### Exécutable standalone (recommandé)

Aucune dépendance Python requise. Télécharger la dernière version :
**[github.com/Asch3nte/c-pinger/releases/latest](https://github.com/Asch3nte/c-pinger/releases/latest)**

```bash
# Linux
curl -LO https://github.com/Asch3nte/c-pinger/releases/latest/download/claudeping
chmod +x claudeping
./claudeping --ui        # Interface graphique
./claudeping              # Mode service (arrière-plan)
```

```
# Windows
Télécharger claudeping-windows-x86_64.exe puis double-cliquer dessus.
```

Un `config.yaml` est créé automatiquement à côté de l'exécutable au premier lancement.

### Depuis les sources (développeurs)

```bash
git clone https://github.com/Asch3nte/c-pinger.git claudeping
cd claudeping
python3 -m venv .venv && source .venv/bin/activate   # Windows : .venv\Scripts\activate
pip install -r requirements.txt
python main.py --ui
```

| Paquet | Rôle |
|--------|------|
| `PySide6` | Interface graphique Qt |
| `pyyaml` | Lecture/écriture de `config.yaml` |
| `plyer` | Notifications desktop |
| `tzdata` | Base de données timezones (nécessaire sur Windows) |

Démarrage automatique au boot :

```bash
bash install/install_linux.sh                                     # Linux, systemd --user
PowerShell -ExecutionPolicy Bypass -File install\install_windows.ps1  # Windows, Planificateur de tâches
```

## Utilisation

### Interface graphique

```bash
python main.py --ui        # ou double-clic sur l'exécutable
```

La fenêtre (~620×540, redimensionnable, défile si besoin) est organisée pour
rester lisible d'un coup d'œil :

- **Un onglet par compte Claude**, structuré en quatre blocs :
  - **État** : statut du service, état CLI, prochain/dernier ping, total de
    pings, dernière probe, quotas session/hebdo (le % affiche directement
    `● ok` en vert ou `● quota atteint` en rouge).
  - **Réglages** : intervalle de probe et fallback horaire — les deux
    réglages ajustés le plus souvent, toujours visibles.
  - **Configuration Claude CLI** *(repliée par défaut)* : exécutable CLI,
    dossier de config isolé, connexion/déconnexion — regroupés et masqués
    tant qu'on n'en a pas besoin.
  - **Ping manuel ponctuel** : programme un ping unique à une heure précise.
- **Onglet Logs** : logs en temps réel de tous les comptes, préfixés `[nom_compte]`.
- **Coin de la barre d'onglets** — **Compte : Ajouter / Supprimer** : ajoute
  un nouveau compte, ou supprime celui de l'onglet actuellement affiché
  (désactivé sur l'onglet Logs).

Un champ modifié mais pas encore enregistré n'est **jamais écrasé** par le
rafraîchissement automatique (toutes les 5s) tant que "Enregistrer la
configuration" n'a pas été cliqué — et valider un champ avec **Entrée**
sauvegarde immédiatement, comme cliquer sur ce bouton.

### Mode service (ligne de commande)

```bash
python main.py           # Service en arrière-plan (mode par défaut depuis les sources)
python main.py status    # Dashboard CLI (un bloc par compte)
python main.py ping-now  # Forcer un ping immédiat sur tous les comptes
```

## Plusieurs comptes Claude en parallèle

ClaudePing peut faire tourner autant de comptes Claude simultanés que
voulu — chacun avec son propre cycle probe/ping, dans son propre thread.

Le CLI `claude` respecte la variable d'environnement `CLAUDE_CONFIG_DIR`
pour isoler entièrement son dossier de config (identifiants inclus) :
`CLAUDE_CONFIG_DIR=~/.claude-pro claude auth login` connecte un compte
séparé de la config par défaut (`~/.claude`), sans jamais y toucher. C'est
ce mécanisme officiel qu'utilise ClaudePing pour chaque compte ajouté —
depuis l'UI (bouton **Ajouter**) ou directement dans `config.yaml` :

```yaml
accounts:
  - name: "DEFAULT"
    claude_config_dir: ""              # vide = config Claude par défaut du poste
    claude_executable: "claude"
    probe: { interval_minutes: 30, model: haiku, message: "reply with just: ok" }
    ping: { model: haiku, message: "reply with just: ok" }
    fallback: { enabled: true, time: "07:00", timezone: "Europe/Brussels" }
    notifications: { enabled: true, on_quota_detected: true, on_ping_sent: true, on_error: false }

  - name: "pro"
    claude_config_dir: "~/.claude-pro"  # dossier isolé → compte totalement séparé
    claude_executable: "claude"
    probe: { interval_minutes: 30, model: haiku, message: "reply with just: ok" }
    ping: { model: haiku, message: "reply with just: ok" }
    fallback: { enabled: true, time: "07:00", timezone: "Europe/Brussels" }
    notifications: { enabled: true, on_quota_detected: true, on_ping_sent: true, on_error: false }

logging:               # global, partagé entre tous les comptes
  level: INFO
  file: claudeping.log
  max_bytes: 1048576
  backup_count: 3
```

Voir [`config.yaml.example`](config.yaml.example) pour la référence complète
des champs. `python main.py status` et `ping-now` affichent/agissent sur
tous les comptes configurés, un dashboard par compte.

**Migration depuis une configuration mono-compte** : aucune action requise,
un `config.yaml` à l'ancien format (sans clé `accounts`) continue de
fonctionner tel quel — chargé comme un unique compte nommé `default`, avec
ses fichiers `claudeping_state.json` et `claudeping.log` habituels. Il n'est
réécrit au nouveau format qu'au premier enregistrement des réglages depuis l'UI.

## Dashboard CLI

```
╭─────────────────────────────────────────────────────╮
│              ClaudePing — Dashboard                 │
├─────────────────────────────────────────────────────┤
│  Heure locale    : 2026-06-18 20:00:00 CEST         │
│  Mode            : Intelligent + Fallback           │
│  Probe interval  : 30 min                           │
├─────────────────────────────────────────────────────┤
│  Prochain ping   : 2026-06-18 21:19:30 CEST         │
│  Dans            : 1h 19m 27s                       │
│  Dernier ping    : 2026-06-18 19:18:22 CEST         │
│  Total pings     : 8                                │
╰─────────────────────────────────────────────────────╯
```

## Logs

Chaque ligne est préfixée par `[nom_compte]` et **colorée par compte**
(couleur stable, assignée à la première rencontre du nom) : dans l'onglet
Logs de l'UI, et dans un terminal couleur en mode service. Le fichier
`claudeping.log` (JSON Lines, avec rotation automatique) contient aussi un
champ structuré `"account"`, pratique pour filtrer/grep.

```bash
tail -f claudeping.log

# Avec formatage JSON (Linux)
tail -f claudeping.log | python3 -c "import sys,json; [print(json.dumps(json.loads(l), indent=2)) for l in sys.stdin]"
```

## Instance unique

ClaudePing empêche de faire tourner plusieurs instances longue-durée
(`--service` et/ou `--ui`) en même temps sur le même poste — sinon deux
instances scheduleraient et enverraient chacune leurs propres pings pour
les mêmes comptes. Le verrou est au niveau de l'OS : si une instance
précédente a planté, il est relâché automatiquement, sans nettoyage manuel.

Relancer `--ui` pendant qu'une instance UI tourne déjà **réaffiche sa
fenêtre** (visible, masquée en tray, ou masquée sans tray) au lieu
d'échouer — fermer la fenêtre (croix) la masque toujours en arrière-plan ;
le seul moyen d'arrêter vraiment ClaudePing est **Fichier → Quitter** (menu
toujours présent, avec ou sans tray système). Les commandes ponctuelles
`status` et `ping-now` ne sont pas concernées par ce verrou.

## Build de l'exécutable (développeurs)

Les binaires prêts à l'emploi sont sur la [page Releases](https://github.com/Asch3nte/c-pinger/releases) —
cette section ne concerne que la reconstruction locale.

```bash
# Linux
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt pyinstaller
pyinstaller claudeping_linux.spec --distpath dist/linux
# → dist/linux/claudeping (~66 Mo, autonome, icône embarquée)
```

```powershell
# Windows (à exécuter sur Windows)
python -m venv .venv; .venv\Scripts\activate
pip install -r requirements.txt pyinstaller
pyinstaller claudeping_windows.spec --distpath dist\windows
# → dist\windows\ClaudePing.exe (~50 Mo, autonome, sans console, icône embarquée)
```

Un tag `vX.Y.Z` poussé déclenche aussi le build Linux + Windows et publie
les deux binaires sur la release GitHub correspondante, via
[`.github/workflows/build-exe.yml`](.github/workflows/build-exe.yml).

## Dépannage

**`claude` introuvable** : vérifiez que Claude Code est installé et dans le PATH, ou renseignez le chemin complet dans le champ "Exécutable CLI" (section "Configuration Claude CLI" de l'UI).

**"État CLI : inconnu (absent)"** : cliquez sur "Vérifier CLI" pour forcer une vérification. Si vous venez de vous connecter, attendez quelques secondes.

**Quota jamais détecté** : réduisez l'intervalle de probe (ex : 10 min) dans l'UI ou dans `config.yaml`.

**Notifications absentes** :
- Linux : installez `python3-dbus` ou `python3-notify2` via votre gestionnaire de paquets.
- Windows : les notifications toast nécessitent Windows 10+.

**`externally-managed-environment` (pip)** : utilisez un environnement virtuel :

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
```
