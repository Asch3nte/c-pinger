# ClaudePing

Démarre automatiquement le compteur de quota Claude Pro (5h) sans intervention manuelle.

## Comment ça marche

Claude Pro fonctionne avec un système de quota par session de 5h :
- Le compteur **ne démarre qu'après le premier message** envoyé à Claude
- Le reset se produit **exactement 5h après ce premier message**
- Toutes les surfaces Claude (claude.ai, Claude Code…) partagent le même quota

ClaudePing surveille votre quota en arrière-plan et envoie automatiquement un
message minimal dès que le reset est disponible, maximisant votre temps de quota.

ClaudePing gère **plusieurs comptes Claude en parallèle** : chaque compte a
son propre cycle probe/ping, tourne dans son propre thread, et peut être
totalement isolé des autres (identifiants séparés). Voir
[Plusieurs comptes Claude en parallèle](#plusieurs-comptes-claude-en-parallèle).

## Trois concepts clés

| Concept | Rôle | Appelle Claude ? |
|---------|------|-----------------|
| **Probe** | Interroge `claude /usage` pour lire le % de quota et l'heure exacte du prochain reset | Oui, toutes les N minutes (configurable, défaut 30 min) |
| **Ping** | Envoie `reply with just: ok` au moment du reset pour relancer la session 5h | Oui, une fois par reset |
| **Fallback horaire** | Si le probe échoue à détecter l'heure de reset, programme un ping à une heure fixe chaque jour | Non (c'est juste un planning de secours) |

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

## Prérequis

- **[Claude Code CLI](https://claude.ai/code)** installé et connecté (`claude auth login`)
- Python 3.10+ (uniquement si vous lancez depuis les sources)
- PySide6, PyYAML, plyer, tzdata (uniquement depuis les sources — voir ci-dessous)

> **Exécutable standalone** : aucune dépendance Python nécessaire, Claude CLI suffit.

## Installation rapide (exécutable standalone)

Les binaires prêts à l'emploi sont versionnés dans le dépôt, sous `dist/`.

### Linux

```bash
git clone https://github.com/Asch3nte/c-pinger.git claudeping
cd claudeping/dist/linux
chmod +x claudeping
./claudeping --ui        # Interface graphique
./claudeping              # Mode service (background)
```

### Windows

```
Télécharger dist\windows\ClaudePing.exe puis double-cliquer dessus.
```

L'application crée automatiquement un `config.yaml` dans le même dossier au premier lancement.

---

## Installation depuis les sources

### Linux

```bash
git clone https://github.com/Asch3nte/c-pinger.git claudeping
cd claudeping
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python main.py --ui
```

Pour installer en tant que service systemd (démarrage automatique) :

```bash
bash install/install_linux.sh
```

### Windows

```powershell
git clone https://github.com/Asch3nte/c-pinger.git claudeping
cd claudeping
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
python main.py --ui
```

Pour installer via le Planificateur de tâches Windows (démarrage automatique) :

```powershell
# Une ligne à la fois
PowerShell -ExecutionPolicy Bypass -File install\install_windows.ps1
```

## Dépendances Python (sources uniquement)

| Paquet | Rôle |
|--------|------|
| `PySide6` | Interface graphique Qt |
| `pyyaml` | Lecture/écriture de `config.yaml` |
| `plyer` | Notifications desktop (Windows/Linux/macOS) |
| `tzdata` | Base de données timezones (nécessaire sur Windows, optionnel sur Linux) |

## Utilisation

### Interface graphique

```bash
python main.py --ui        # ou double-clic sur l'exécutable
```

L'interface tient dans une fenêtre compacte (≈560×460 px), organisée pour
rester lisible d'un coup d'œil :

- **Un onglet par compte Claude**, chacun structuré en quatre blocs :
  - **État** : statut du service, état CLI, prochain/dernier ping, total de
    pings, dernière probe, et quotas session/hebdo (le % affiche directement
    `● ok` en vert ou `● quota atteint` en rouge, pas besoin de faire le calcul).
  - **Réglages** : intervalle de probe et fallback horaire — les deux
    réglages qu'on ajuste le plus souvent, toujours visibles.
  - **Configuration Claude CLI** *(repliée par défaut)* : exécutable CLI,
    dossier de config isolé, connexion/déconnexion — regroupé et masqué
    tant qu'on n'en a pas besoin, pour ne pas alourdir la fenêtre.
  - **Ping manuel ponctuel** : programme un ping unique à une heure précise.
- **Onglet Logs** : logs en temps réel de tous les comptes (préfixés `[nom_compte]`), sans avoir à surveiller le terminal
- **Coin supérieur droit de la barre d'onglets** : **"+ Ajouter un compte"**
  et, juste en dessous, **"🗑 Supprimer ce compte"** (agit sur l'onglet compte
  actuellement affiché ; désactivé sur l'onglet Logs) — voir
  [Plusieurs comptes Claude en parallèle](#plusieurs-comptes-claude-en-parallèle)

Un champ modifié mais pas encore enregistré n'est **jamais écrasé** par le
rafraîchissement automatique (toutes les 5s) tant que "Enregistrer la
configuration" n'a pas été cliqué — et valider un champ avec **Entrée**
sauvegarde immédiatement, comme cliquer sur ce bouton.

Réglages disponibles par compte :
- **Probe quota toutes les N min** : fréquence d'interrogation de `claude /usage`
- **Activer le fallback horaire** / **Heure (HH:MM)** : ping de secours à heure fixe si la détection automatique échoue
- **Exécutable CLI** : chemin vers le binaire `claude` si absent du PATH
- **Dossier de config** : dossier d'identifiants isolé pour ce compte (vide = config par défaut du poste), avec bouton **Ouvrir**
- **Se connecter / Se déconnecter** : lance `claude auth login` / `claude auth logout` pour ce compte
- **Ping immédiat** : force un ping maintenant
- **Ping manuel ponctuel (HH:MM)** : programme un ping unique à une heure précise

### Mode service (ligne de commande)

```bash
# Service background (mode par défaut depuis les sources)
python main.py

# Dashboard CLI (un bloc par compte configuré)
python main.py status

# Forcer un ping immédiat sur tous les comptes
python main.py ping-now
```

## Plusieurs comptes Claude en parallèle

ClaudePing peut faire tourner **autant de comptes Claude simultanés que
vous le souhaitez** — chacun avec son propre cycle probe/ping, dans son
propre thread, indépendant des autres.

### Comment ça marche

Le CLI `claude` respecte la variable d'environnement `CLAUDE_CONFIG_DIR`
pour isoler entièrement son dossier de config (y compris les identifiants
de connexion) : `CLAUDE_CONFIG_DIR=~/.claude-pro claude auth login`
connecte un compte séparé de votre config Claude par défaut (`~/.claude`),
sans jamais toucher à celle-ci. C'est le mécanisme officiel pour faire
cohabiter plusieurs comptes Claude sur la même machine, et c'est celui
qu'utilise ClaudePing pour chaque compte que vous ajoutez.

### Depuis l'interface graphique

- Chaque compte a son propre **onglet** (statut, réglages, logs regroupés
  dans l'onglet "Logs" partagé, préfixés `[nom_compte]`).
- Bouton **"+ Ajouter un compte"** (coin supérieur droit des onglets) :
  demande un nom et, optionnellement, un dossier de config Claude dédié
  (laissé vide = ce compte utilise la config Claude par défaut du poste).
  Le compte est démarré immédiatement, en parallèle des autres.
- Dans chaque onglet, champ **"Dossier de config Claude"** + bouton
  **"Ouvrir dossier de config"** : ouvre ce dossier dans l'explorateur de
  fichiers du système (Nautilus/Explorer/Finder…), pour inspecter ou
  gérer manuellement les fichiers du compte (identifiants, historique…).
- Bouton **"Se connecter"** (dans "Configuration Claude CLI") : lance
  `claude auth login` avec le `CLAUDE_CONFIG_DIR` de ce compte — la
  fenêtre de connexion navigateur authentifie bien ce compte-là, pas le
  compte par défaut du poste.
- Bouton **"🗑 Supprimer ce compte"** (coin de la barre d'onglets, sous
  "+ Ajouter un compte") : arrête le scheduler du compte actuellement
  affiché et le retire de `config.yaml` (les fichiers d'état/logs
  existants ne sont pas supprimés du disque).

### Depuis `config.yaml`

Voir [`config.yaml.example`](config.yaml.example) pour la référence
complète. En résumé, `accounts` est une liste, un item = un compte :

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

`python main.py status` et `python main.py ping-now` affichent/agissent
sur tous les comptes configurés, un dashboard par compte.

### Migration depuis une configuration mono-compte

Aucune action requise : un `config.yaml` à l'ancien format (sans clé
`accounts`) continue de fonctionner tel quel — il est chargé comme un
unique compte nommé `default`, avec ses fichiers `claudeping_state.json`
et `claudeping.log` habituels. Le fichier n'est réécrit au nouveau format
qu'au premier enregistrement des réglages depuis l'UI.

## Configuration (`config.yaml`)

```yaml
accounts:
  - name: DEFAULT
    claude_config_dir: ""     # vide = config Claude par défaut du poste
    claude_executable: claude # Chemin complet si 'claude' n'est pas dans le PATH
    probe:
      interval_minutes: 30    # Fréquence des probes (appels à claude /usage)
      model: haiku            # Modèle le plus léger pour les probes
      message: "reply with just: ok"
    ping:
      model: haiku
      message: "reply with just: ok"
    fallback:
      enabled: true
      time: "07:00"           # Heure fixe si le probe échoue
      timezone: "Europe/Brussels"
    notifications:
      enabled: true
      on_quota_detected: true
      on_ping_sent: true
      on_error: false

logging:                      # global, partagé entre tous les comptes
  level: INFO
  file: claudeping.log        # Chemin relatif au dossier d'exécution
  max_bytes: 1048576          # 1 MB avant rotation
  backup_count: 3
```

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

Chaque ligne de log est préfixée par `[nom_compte]` pour rester lisible
quand plusieurs comptes tournent en parallèle. Ce tag est en plus **coloré
par compte** (couleur stable, assignée à la première rencontre du nom) :
dans l'onglet Logs de l'UI, et dans un terminal couleur (via codes ANSI)
en mode service. Le fichier `claudeping.log` (JSON Lines) contient aussi
un champ structuré `"account"` par entrée, pratique pour filtrer/grep.

Les logs sont écrits simultanément :
- Dans l'**onglet Logs** de l'interface graphique (temps réel, tag coloré)
- Dans le fichier `claudeping.log` (JSON Lines, avec rotation automatique)
- Dans le **terminal** si lancé depuis une console (coloré si le terminal le supporte)

```bash
# Suivre les logs bruts (Linux)
tail -f claudeping.log

# Avec formatage JSON (Linux)
tail -f claudeping.log | python3 -c "import sys,json; [print(json.dumps(json.loads(l), indent=2)) for l in sys.stdin]"

# Windows PowerShell
Get-Content "$env:USERPROFILE\claudeping\claudeping.log" -Wait -Tail 50
```

## Build de l'exécutable (développeurs)

Nécessite Python 3.10+ et les dépendances installées dans un venv.

### Linux

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt pyinstaller
pyinstaller claudeping_linux.spec --distpath dist/linux
# → dist/linux/claudeping  (~66 MB, autonome, icône assets/icon.png embarquée)
```

### Windows (à exécuter sur Windows)

```powershell
python -m venv .venv; .venv\Scripts\activate
pip install -r requirements.txt pyinstaller
pyinstaller claudeping_windows.spec --distpath dist\windows
# → dist\windows\ClaudePing.exe  (~70 MB, autonome, sans console, icône assets/icon.ico)
```

## Instance unique

ClaudePing empêche de faire tourner plusieurs instances longue-durée
(`--service` et/ou `--ui`, dans n'importe quelle combinaison) en même
temps sur le même poste — sans ça, deux instances scheduleraient et
enverraient chacune leurs propres pings/notifications pour les mêmes
comptes, causant des notifications en double voire des pings envoyés
plusieurs fois. Ce verrou est au niveau de l'OS (pas un simple fichier
PID) : si une instance précédente a planté, il est relâché automatiquement,
sans nettoyage manuel.

**Relancer `--ui` pendant qu'une instance UI tourne déjà réaffiche sa
fenêtre** (visible, masquée en tray, ou masquée sans tray) au lieu
d'échouer — utile en particulier sur Linux quand l'icône system tray
n'est pas disponible (paquets de notification manquants) : fermer la
fenêtre (croix) la masque toujours en arrière-plan, la relancer
(double-clic sur l'exécutable, raccourci…) la fait réapparaître. Le seul
moyen d'arrêter vraiment ClaudePing est le menu **Fichier → Quitter** (ou
"Quitter" dans le menu du tray s'il est disponible) — pas besoin de tray
pour ça non plus, le menu Fichier est toujours présent dans la fenêtre.

Si l'instance déjà lancée est un `--service` headless (sans interface —
ex : démarré via systemd/Planificateur de tâches), relancer `--ui` ne
peut évidemment rien réafficher : le message l'indique explicitement et
invite à arrêter le service ou à utiliser `claudeping status` /
`claudeping ping-now` en ligne de commande.

Les commandes ponctuelles `status` et `ping-now` ne sont pas concernées
par ce verrou (elles ne lancent pas de scheduler en tâche de fond) et
peuvent être utilisées librement même pendant qu'une instance tourne.

## Dépannage

**`claude` introuvable** : Vérifiez que Claude Code est installé et dans votre PATH, ou renseignez le chemin complet dans le champ "Claude CLI (exécutable)" de l'UI.

**"État CLI : inconnu (disponible)"** : Cliquez sur "Vérifier CLI" pour forcer une vérification. Si vous venez de vous connecter, attendez quelques secondes.

**Quota jamais détecté** : Réduisez l'intervalle de probe (ex : 10 min) dans l'UI ou dans `config.yaml`.

**Notifications absentes** :
- Linux : installez `python3-dbus` ou `python3-notify2` via votre gestionnaire de paquets.
- Windows : les notifications toast nécessitent Windows 10+.

**`externally-managed-environment` (pip)** : Utilisez un environnement virtuel :

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
```
