# ClaudePing

Démarre automatiquement le compteur de quota Claude Pro (5h) sans intervention manuelle.

## Comment ça marche

Claude Pro fonctionne avec un système de quota par session de 5h :
- Le compteur **ne démarre qu'après le premier message** envoyé à Claude
- Le reset se produit **exactement 5h après ce premier message**
- Toutes les surfaces Claude (claude.ai, Claude Code...) partagent le même quota

ClaudePing surveille votre quota en arrière-plan et envoie automatiquement un 
message minimal dès que le reset est disponible, maximisant votre temps de quota.

## Fonctionnement

```
Probe toutes les 30min (configurable)
    │
    ├── Quota PAS atteint → rien à faire
    │
    └── Quota atteint → parse l'heure de reset depuis le message Claude
            │
            └── Schedule un ping 30s après le reset
                    │
                    └── Ping envoyé → compteur 5h démarré ✅
                            │
                            └── Notification desktop + log
```

## Prérequis

- Python 3.10+
- [Claude Code CLI](https://claude.ai/code) installé et authentifié (`claude login`)

## Installation

### Linux (systemd)

```bash
git clone <repo> c-ping
cd c-ping
bash install/install_linux.sh
```

### Windows (Task Scheduler)

```powershell
git clone <repo> c-ping
cd c-ping
PowerShell -ExecutionPolicy Bypass -File install\install_windows.ps1
```

## Configuration

Éditez `config.yaml` :

```yaml
probe:
  interval_minutes: 30    # Fréquence des probes
  model: "haiku"          # Modèle le plus léger

fallback:
  enabled: true
  time: "07:00"           # Heure fixe si détection échoue
  timezone: "Europe/Brussels"

notifications:
  enabled: true
```

## Commandes

```bash
# Démarrer le service manuellement
python main.py

# Afficher le dashboard
python main.py status

# Forcer un ping immédiat
python main.py ping-now
```

## Dashboard

```
╭─────────────────────────────────────────────────────╮
│              ClaudePing — Dashboard                 │
├─────────────────────────────────────────────────────┤
│  Heure locale    : 2025-01-13 14:30:00 CET          │
│  Mode            : Intelligent + Fallback           │
│  Probe interval  : 30 min                           │
├─────────────────────────────────────────────────────┤
│  Prochain ping   : 2025-01-13 14:50:30 CET          │
│  Dans            : 0h 20m 27s                       │
│  Dernier ping    : 2025-01-13 09:00:00 CET          │
│  Total pings     : 3                                │
╰─────────────────────────────────────────────────────╯
```

## Logs

Les logs sont en JSON Lines dans `claudeping.log` avec rotation automatique.

```bash
# Linux : suivre les logs en temps réel
journalctl --user -u claudeping -f

# Ou directement
tail -f claudeping.log | python3 -c "import sys,json; [print(json.dumps(json.loads(l), indent=2)) for l in sys.stdin]"
```

## Dépannage

**`claude` introuvable** : Vérifiez que Claude Code est installé et dans votre PATH.

**Quota jamais détecté** : Augmentez la fréquence des probes (`interval_minutes: 10`).

**Notifications absentes** : Installez `plyer` (`pip install plyer`) et vérifiez les permissions de notification de votre OS.
