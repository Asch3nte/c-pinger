# Installation de ClaudePing sur Windows via Task Scheduler
# Usage : PowerShell -ExecutionPolicy Bypass -File install\install_windows.ps1

#Requires -Version 5.1

$ErrorActionPreference = "Stop"

 $ InstallDir = " $ env:USERPROFILE\claudeping"
$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$ProjectDir = Split-Path -Parent $ScriptDir
$TaskName = "ClaudePing"

# Compatibilité PowerShell 5.1 (pas d'opérateur ?.)
$PythonCmd = Get-Command python -ErrorAction SilentlyContinue
 $ PythonExe = if ( $ PythonCmd) { $PythonCmd.Source } else { $null }

Write-Host "=== Installation de ClaudePing ===" -ForegroundColor Cyan

# 1. Vérifications
if (-not $PythonExe) {
    Write-Error "Python n'est pas trouvé dans le PATH. Installez Python 3.10+."
    exit 1
}

$ClaudeCmd = Get-Command claude -ErrorAction SilentlyContinue
 $ ClaudeExe = if ( $ ClaudeCmd) { $ClaudeCmd.Source } else { $null }
if (-not $ClaudeExe) {
    Write-Warning "'claude' non trouvé dans PATH. Assurez-vous que Claude Code est installé."
}

# 2. Copie des fichiers
Write-Host "-> Copie vers $InstallDir..." -ForegroundColor Yellow
New-Item -ItemType Directory -Force -Path  $ InstallDir | Out-Null
Copy-Item -Path " $ ProjectDir\*" -Destination  $ InstallDir -Recurse -Force

# 3. Config
if (-not (Test-Path " $ InstallDir\config.yaml")) {
    Copy-Item " $ InstallDir\config.yaml.example" " $ InstallDir\config.yaml"
    Write-Host "-> config.yaml créé. Éditez $InstallDir\config.yaml." -ForegroundColor Yellow
}

# 4. Dépendances Python
Write-Host "-> Installation des dépendances Python..." -ForegroundColor Yellow
& $PythonEx

