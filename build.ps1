param(
    [switch]$Clean,
    [switch]$ForceIsolated,
    [switch]$SmokeLaunch
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

function Assert-WorkspaceChildPath {
    param([Parameter(Mandatory = $true)][string]$Path)

    $RootPath = [System.IO.Path]::GetFullPath($ProjectRoot).TrimEnd('\', '/')
    if (Test-Path $Path) {
        $ResolvedPath = (Resolve-Path -LiteralPath $Path).Path
    }
    else {
        $ResolvedPath = [System.IO.Path]::GetFullPath($Path)
    }
    $ResolvedPath = $ResolvedPath.TrimEnd('\', '/')

    if (-not $ResolvedPath.StartsWith($RootPath + [System.IO.Path]::DirectorySeparatorChar, [System.StringComparison]::OrdinalIgnoreCase)) {
        throw "Отказ от удаления пути за пределами проекта: $ResolvedPath"
    }
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

function Select-LastNonEmptyString {
    param(
        [Parameter(Mandatory = $true)]
        [AllowEmptyCollection()]
        [object[]]$Values,

        [Parameter(Mandatory = $true)]
        [string]$Name
    )

    $Selected = @($Values | Where-Object { $_ -is [string] -and -not [string]::IsNullOrWhiteSpace($_) } | Select-Object -Last 1)
    if (-not $Selected) {
        throw "Не удалось определить $Name."
    }
    return [string]$Selected[0]
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
        Assert-WorkspaceChildPath -Path $TargetPath
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
            Assert-WorkspaceChildPath -Path $Path
            Remove-Item -LiteralPath $Path -Recurse -Force
            if (Test-Path $Path) {
                throw "Не удалось удалить прошлый артефакт сборки: $Path"
            }
        }
    }
}

Set-Location $ProjectRoot

if (-not (Test-Path $IconPath)) {
    throw "Файл icon.ico не найден в корне проекта. GUI-сборка должна использовать пользовательскую иконку."
}

$PythonCmd = @(Resolve-PythonCommand)
$BuildPython = Select-LastNonEmptyString -Values @(Resolve-BuildPython -PythonCmd $PythonCmd) -Name "Python для сборки"
$BuildPython = Select-LastNonEmptyString -Values @(Install-BuildDependencies -PythonExe $BuildPython) -Name "Python после установки зависимостей"

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

Write-Step "Проверка GUI exe и PySide6 runtime"
$ValidationScript = @'
import sys
from pathlib import Path

exe = Path(sys.argv[1])
data = exe.read_bytes()
if data[:2] != b"MZ":
    raise SystemExit(f"{exe} не похож на Windows PE executable")
pe_offset = int.from_bytes(data[0x3C:0x40], "little")
if data[pe_offset:pe_offset + 4] != b"PE\0\0":
    raise SystemExit(f"{exe} содержит некорректный PE header")
optional_header_offset = pe_offset + 24
subsystem = int.from_bytes(data[optional_header_offset + 68:optional_header_offset + 70], "little")
if subsystem != 2:
    raise SystemExit(f"Ожидался Windows GUI subsystem (2), получено: {subsystem}")

try:
    from PyInstaller.archive.readers import CArchiveReader
except Exception as exc:
    raise SystemExit(f"Не удалось импортировать PyInstaller archive reader: {exc}") from exc

reader = CArchiveReader(str(exe))
toc = {name.replace("\\", "/").lower() for name in reader.toc}

def require_any(label, predicate):
    if not any(predicate(name) for name in toc):
        raise SystemExit(f"В onefile archive не найдено: {label}")

require_any(
    "PySide6 Qt platforms/qwindows.dll",
    lambda name: (
        "pyside6/plugins/platforms/qwindows" in name
        or "pyside6/qt/plugins/platforms/qwindows" in name
    ) and name.endswith(".dll"),
)
require_any(
    "PySide6 Qt styles plugin",
    lambda name: (
        "pyside6/plugins/styles/" in name
        or "pyside6/qt/plugins/styles/" in name
    ) and name.endswith(".dll"),
)
require_any("icon.ico runtime data", lambda name: name.endswith("/icon.ico") or name == "icon.ico")
require_any("logo.txt runtime data", lambda name: name.endswith("/logo.txt") or name == "logo.txt")

print("GUI subsystem: Windows GUI")
print("PySide6 plugins: platforms/styles present")
print("Runtime data: icon.ico/logo.txt present")
'@
Invoke-ExternalCommand -FilePath $BuildPython -Arguments @("-c", $ValidationScript, $ExePath) `
    -ErrorMessage "Проверка собранного exe завершилась с ошибкой."

if ($SmokeLaunch) {
    Write-Step "Smoke launch GUI exe"
    $Process = Start-Process -FilePath $ExePath -PassThru -WindowStyle Normal
    Start-Sleep -Seconds 5
    $RunningExeProcesses = @(
        Get-Process -ErrorAction SilentlyContinue |
            Where-Object { $_.Path -eq $ExePath }
    )
    if ($Process.HasExited -and -not $RunningExeProcesses) {
        throw "Собранный GUI exe завершился сразу после запуска. Код выхода: $($Process.ExitCode)"
    }
    $Closed = $Process.CloseMainWindow()
    Start-Sleep -Seconds 2
    if (-not $Process.HasExited) {
        Stop-Process -Id $Process.Id -Force
        Wait-Process -Id $Process.Id -Timeout 10 -ErrorAction SilentlyContinue
    }
    $RemainingExeProcesses = @(
        Get-Process -ErrorAction SilentlyContinue |
            Where-Object { $_.Path -eq $ExePath }
    )
    foreach ($RemainingProcess in $RemainingExeProcesses) {
        Stop-Process -Id $RemainingProcess.Id -Force
        Wait-Process -Id $RemainingProcess.Id -Timeout 10 -ErrorAction SilentlyContinue
    }
    $StillRunningExeProcesses = @(
        Get-Process -ErrorAction SilentlyContinue |
            Where-Object { $_.Path -eq $ExePath }
    )
    if ($StillRunningExeProcesses) {
        $RemainingPids = ($StillRunningExeProcesses | ForEach-Object { $_.Id }) -join ", "
        throw "Smoke launch process did not exit after forced stop. PID(s): $RemainingPids"
    }
    Write-Host "Smoke launch: процесс стартовал успешно (PID $($Process.Id)). CloseMainWindow=$Closed" -ForegroundColor Green
}

Write-Step "Сборка завершена"
Write-Host "EXE: $ExePath" -ForegroundColor Green
