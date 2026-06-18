# ClaudePing

Démarre automatiquement le compteur de quota Claude Pro (5h) sans intervention manuelle.

## Comment ça marche

Claude Pro fonctionne avec un système de quota par session de 5h :
- Le compteur **ne démarre qu'après le premier message** envoyé à Claude
- Le reset se produit **exactement 5h après ce premier message**
- Toutes les surfaces Claude (claude.ai, Claude Code…) partagent le même quota

ClaudePing surveille votre quota en arrière-plan et envoie automatiquement un
message minimal dès que le reset est disponible, maximisant votre temps de quota.

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

### Linux

```bash
# Télécharger et rendre exécutable
chmod +x claudeping
./claudeping --ui        # Interface graphique
./claudeping             # Mode service (background)
```

### Windows

```
Double-cliquer sur ClaudePing.exe
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

L'interface affiche :
- **Onglet Statut** : état du service, prochain ping, quotas session/hebdo/mensuel, et tous les paramètres configurables
- **Onglet Logs** : logs en temps réel sans avoir à surveiller le terminal

Paramètres configurables depuis l'UI :
- **Probe quota toutes les N min** : fréquence d'interrogation de `claude /usage`
- **Activer le fallback horaire** : ping de secours à heure fixe si la détection automatique échoue
- **Heure de fallback (HH:MM)** : heure du ping de secours quotidien
- **Claude CLI (exécutable)** : chemin vers le binaire `claude` si absent du PATH
- **Se connecter / Se déconnecter** : lance `claude auth login` / `claude auth logout`
- **Ping immédiat** : force un ping maintenant
- **Ping manuel ponctuel (HH:MM)** : programme un ping unique à une heure précise

### Mode service (ligne de commande)

```bash
# Service background (mode par défaut depuis les sources)
python main.py

# Dashboard CLI
python main.py status

# Forcer un ping immédiat
python main.py ping-now
```

## Configuration (`config.yaml`)

```yaml
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

logging:
  level: INFO
  file: claudeping.log    # Chemin relatif au dossier d'exécution
  max_bytes: 1048576      # 1 MB avant rotation
  backup_count: 3

claude_executable: claude  # Chemin complet si 'claude' n'est pas dans le PATH
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

Les logs sont écrits simultanément :
- Dans l'**onglet Logs** de l'interface graphique (temps réel)
- Dans le fichier `claudeping.log` (JSON Lines, avec rotation automatique)
- Dans le **terminal** si lancé depuis une console

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
# → dist/linux/claudeping  (~66 MB, autonome)
```

### Windows (à exécuter sur Windows)

```powershell
python -m venv .venv; .venv\Scripts\activate
pip install -r requirements.txt pyinstaller
pyinstaller claudeping_windows.spec --distpath dist\windows
# → dist\windows\ClaudePing.exe  (~70 MB, autonome, sans console)
```

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
