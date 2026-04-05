param(
    [switch]$Clean,
    [switch]$ForceIsolated
)

$ErrorActionPreference = "Stop"

$ProjectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$BuildRoot = Join-Path $ProjectRoot ".build"
$VenvPath = Join-Path $BuildRoot "venv"
$RequirementsPath = Join-Path $ProjectRoot "requirements.txt"
$SpecPath = Join-Path $ProjectRoot "vynex_vpn_client.spec"
$DistPath = Join-Path $ProjectRoot "dist"
$BuildCachePath = Join-Path $ProjectRoot "build"
$ProjectVenvPath = Join-Path $ProjectRoot ".venv"
$IconPath = Join-Path $ProjectRoot "icon.ico"

function Write-Step {
    param([string]$Message)
    Write-Host "==> $Message" -ForegroundColor Cyan
}

function Invoke-ExternalCommand {
    param(
        [Parameter(Mandatory = $true)]
        [string]$FilePath,

        [Parameter()]
        [string[]]$Arguments = @(),

        [string]$ErrorMessage = "Внешняя команда завершилась с ошибкой."
    )

    & $FilePath @Arguments | Out-Host
    if ($LASTEXITCODE -ne 0) {
        throw "$ErrorMessage Код выхода: $LASTEXITCODE"
    }
}

function Resolve-PythonCommand {
    try {
        & py -3 -c "print('ok')" | Out-Null
        return @("py", "-3")
    }
    catch {
        throw "Python 3.10+ не найден. Установите Python и проверьте, что команда 'py -3' работает."
    }
}

function Get-VenvPythonPath {
    param([string]$BasePath)
    return Join-Path $BasePath "Scripts\python.exe"
}

function New-IsolatedVenv {
    param(
        [string[]]$PythonCmd,
        [string]$TargetPath
    )

    if (Test-Path $TargetPath) {
        Remove-Item -LiteralPath $TargetPath -Recurse -Force
    }

    Write-Step "Создание isolated virtualenv для сборки"
    Invoke-ExternalCommand -FilePath $PythonCmd[0] -Arguments @($PythonCmd[1], "-m", "venv", $TargetPath) `
        -ErrorMessage "Не удалось создать isolated virtualenv."

    $PythonExe = Get-VenvPythonPath -BasePath $TargetPath
    if (-not (Test-Path $PythonExe)) {
        throw "Не найден интерпретатор virtualenv: $PythonExe"
    }

    Invoke-ExternalCommand -FilePath $PythonExe -Arguments @("-m", "ensurepip", "--upgrade") `
        -ErrorMessage "Не удалось выполнить ensurepip в isolated virtualenv."
    return $PythonExe
}

function Resolve-BuildPython {
    param([string[]]$PythonCmd)

    if (-not $ForceIsolated) {
        if ($env:VIRTUAL_ENV) {
            $ActiveVenvPython = Get-VenvPythonPath -BasePath $env:VIRTUAL_ENV
            if (Test-Path $ActiveVenvPython) {
                Write-Step "Используется активированный virtualenv: $env:VIRTUAL_ENV"
                return $ActiveVenvPython
            }
        }

        $ProjectVenvPython = Get-VenvPythonPath -BasePath $ProjectVenvPath
        if (Test-Path $ProjectVenvPython) {
            Write-Step "Используется проектный virtualenv: $ProjectVenvPath"
            return $ProjectVenvPython
        }
    }

    if (-not (Test-Path $BuildRoot)) {
        New-Item -ItemType Directory -Path $BuildRoot | Out-Null
    }

    $IsolatedPython = Get-VenvPythonPath -BasePath $VenvPath
    if (-not (Test-Path $IsolatedPython)) {
        return New-IsolatedVenv -PythonCmd $PythonCmd -TargetPath $VenvPath
    }
    return $IsolatedPython
}

function Install-BuildDependencies {
    param([string]$PythonExe)

    try {
        Write-Step "Установка зависимостей для сборки"
        Invoke-ExternalCommand -FilePath $PythonExe -Arguments @(
            "-m",
            "pip",
            "install",
            "--disable-pip-version-check",
            "--no-input",
            "-r",
            $RequirementsPath,
            "pyinstaller"
        ) -ErrorMessage "Не удалось установить зависимости для сборки."
    }
    catch {
        if ($PythonExe -eq (Get-VenvPythonPath -BasePath $VenvPath)) {
            Write-Step "Изолированный build-venv поврежден, пересоздаю"
            $PythonCmd = Resolve-PythonCommand
            $RecoveredPython = New-IsolatedVenv -PythonCmd $PythonCmd -TargetPath $VenvPath
            Invoke-ExternalCommand -FilePath $RecoveredPython -Arguments @(
                "-m",
                "pip",
                "install",
                "--disable-pip-version-check",
                "--no-input",
                "-r",
                $RequirementsPath,
                "pyinstaller"
            ) -ErrorMessage "Не удалось установить зависимости после пересоздания isolated virtualenv."
            return $RecoveredPython
        }
        throw
    }

    return $PythonExe
}

if ($Clean) {
    Write-Step "Удаление прошлых артефактов"
    foreach ($Path in @($BuildRoot, $DistPath, $BuildCachePath)) {
        if (Test-Path $Path) {
            Remove-Item -LiteralPath $Path -Recurse -Force
        }
    }
}

Set-Location $ProjectRoot

if (-not (Test-Path $IconPath)) {
    Write-Host "Предупреждение: файл icon.ico не найден в корне проекта. EXE будет собран без пользовательской иконки." -ForegroundColor Yellow
}

$PythonCmd = Resolve-PythonCommand
$BuildPython = Resolve-BuildPython -PythonCmd $PythonCmd
$BuildPython = Install-BuildDependencies -PythonExe $BuildPython

Write-Step "Сборка exe через PyInstaller"
Invoke-ExternalCommand -FilePath $BuildPython -Arguments @(
    "-m",
    "PyInstaller",
    "--noconfirm",
    "--clean",
    $SpecPath
) -ErrorMessage "Сборка exe через PyInstaller завершилась с ошибкой."

$ExePath = Join-Path $DistPath "VynexVPNClient.exe"
if (-not (Test-Path $ExePath)) {
    throw "Сборка завершилась без выходного exe. Ожидался файл: $ExePath"
}

Write-Step "Сборка завершена"
Write-Host "EXE: $ExePath" -ForegroundColor Green
