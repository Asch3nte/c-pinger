# Installation de ClaudePing sur Windows via Task Scheduler
# Usage : PowerShell -ExecutionPolicy Bypass -File install\install_windows.ps1

#Requires -Version 5.1

$ErrorActionPreference = "Stop"

$InstallDir = "$env:USERPROFILE\claudeping"
$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$ProjectDir = Split-Path -Parent $ScriptDir
$TaskName = "ClaudePing"

# Compatibilité PowerShell 5.1 (pas d'opérateur ?.)
$PythonCmd = Get-Command python -ErrorAction SilentlyContinue
$PythonExe = if ($PythonCmd) { $PythonCmd.Source } else { $null }

Write-Host "=== Installation de ClaudePing ===" -ForegroundColor Cyan

# 1. Vérifications
if (-not $PythonExe) {
    Write-Error "Python n'est pas trouvé dans le PATH. Installez Python 3.10+."
    exit 1
}

$ClaudeCmd = Get-Command claude -ErrorAction SilentlyContinue
$ClaudeExe = if ($ClaudeCmd) { $ClaudeCmd.Source } else { $null }

if (-not $ClaudeExe) {
    Write-Host "-> 'claude' non trouvé dans PATH, recherche automatique..." -ForegroundColor Yellow

    $SearchPaths = @(
        "$env:LOCALAPPDATA\Packages",
        "$env:USERPROFILE\.vscode\extensions"
    )

    # Chercher tous les claude.exe, exclure les extensions VSCode (binaires de secours)
    $Found = Get-ChildItem -Path $SearchPaths -Filter "claude.exe" -Recurse -ErrorAction SilentlyContinue |
             Where-Object { $_.FullName -notlike "*\.vscode\*" } |
             # Prendre la version la plus récente si plusieurs
             Sort-Object { $_.FullName } -Descending |
             Select-Object -First 1

    if ($Found) {
        $ClaudeExe = $Found.FullName
        Write-Host "-> Claude trouvé : $ClaudeExe" -ForegroundColor Green

        # Ajouter le dossier au PATH utilisateur pour les prochaines sessions
        $ClaudeDir = Split-Path -Parent $ClaudeExe
        $CurrentUserPath = [Environment]::GetEnvironmentVariable("PATH", "User")
        if ($CurrentUserPath -notlike "*$ClaudeDir*") {
            [Environment]::SetEnvironmentVariable("PATH", "$CurrentUserPath;$ClaudeDir", "User")
            Write-Host "-> Dossier ajouté au PATH utilisateur : $ClaudeDir" -ForegroundColor Green
            Write-Host "   (Redémarrez PowerShell pour que 'claude' soit disponible globalement)" -ForegroundColor Gray
        }
    } else {
        Write-Warning "Claude Code introuvable. Le ping ne fonctionnera pas sans lui."
        Write-Warning "Installez Claude Code depuis : https://claude.ai/download"
    }
}

if ($ClaudeExe) {
    Write-Host "-> Claude utilisé : $ClaudeExe" -ForegroundColor Green
}

# 2. Copie des fichiers
Write-Host "-> Copie vers $InstallDir..." -ForegroundColor Yellow
New-Item -ItemType Directory -Force -Path $InstallDir | Out-Null
Copy-Item -Path "$ProjectDir\*" -Destination $InstallDir -Recurse -Force

# 3. Config
if (-not (Test-Path "$InstallDir\config.yaml")) {
    Copy-Item "$InstallDir\config.yaml.example" "$InstallDir\config.yaml"
    Write-Host "-> config.yaml créé. Éditez $InstallDir\config.yaml." -ForegroundColor Yellow
}

# Injecter le chemin claude dans config.yaml si trouvé et pas déjà présent
if ($ClaudeExe -and (Test-Path "$InstallDir\config.yaml")) {
    $ConfigContent = Get-Content "$InstallDir\config.yaml" -Raw
    # Échapper les backslashes pour YAML
    $ClaudeExeYaml = $ClaudeExe.Replace("\", "\\")
    if ($ConfigContent -notmatch "claude_executable:") {
        Add-Content "$InstallDir\config.yaml" "`nclaude_executable: `"$ClaudeExeYaml`""
        Write-Host "-> Chemin claude injecté dans config.yaml" -ForegroundColor Green
    }
}

# 4. Dépendances Python
Write-Host "-> Installation des dépendances Python..." -ForegroundColor Yellow
& $PythonExe -m pip install --user -r "$InstallDir\requirements.txt"

# 5. Task Scheduler
Write-Host "-> Création de la tâche planifiée..." -ForegroundColor Yellow

$Action = New-ScheduledTaskAction `
    -Execute $PythonExe `
    -Argument "$InstallDir\main.py" `
    -WorkingDirectory $InstallDir

$Trigger = New-ScheduledTaskTrigger -AtLogOn -User $env:USERNAME

$Settings = New-ScheduledTaskSettingsSet `
    -ExecutionTimeLimit (New-TimeSpan -Hours 0) `
    -RestartCount 3 `
    -RestartInterval (New-TimeSpan -Minutes 1) `
    -StartWhenAvailable

$Principal = New-ScheduledTaskPrincipal `
    -UserId $env:USERNAME `
    -LogonType Interactive `
    -RunLevel Limited

$TaskParams = @{
    TaskName    = $TaskName
    Action      = $Action
    Trigger     = $Trigger
    Settings    = $Settings
    Principal   = $Principal
    Description = "ClaudePing - Demarrage automatique du compteur de quota Claude Pro"
    Force       = $true
}
Register-ScheduledTask @TaskParams | Out-Null

# Démarrage immédiat
Start-ScheduledTask -TaskName $TaskName

Write-Host ""
Write-Host "OK ClaudePing installe et demarre !" -ForegroundColor Green
Write-Host ""
Write-Host "Commandes utiles :" -ForegroundColor Cyan
Write-Host "  Get-ScheduledTask -TaskName ClaudePing    # Statut"
Write-Host "  python $InstallDir\main.py status         # Dashboard CLI"
Write-Host "  python $InstallDir\main.py ping-now       # Forcer un ping"