$ErrorActionPreference = 'Stop'
$downloadDir = Join-Path ([IO.Path]::GetTempPath()) ('LottoPython-' + [guid]::NewGuid())
try {
    # Pin an official CPython release with pip and Tk support.
    $version = '3.13.12'
    $architecture = $env:PROCESSOR_ARCHITECTURE
    if ($env:PROCESSOR_ARCHITEW6432) { $architecture = $env:PROCESSOR_ARCHITEW6432 }
    $suffix = switch ($architecture) {
        'ARM64' { '-arm64' }
        'AMD64' { '-amd64' }
        'x86' { '' }
        default { throw "Unsupported processor architecture: $architecture" }
    }
    New-Item -ItemType Directory -Path $downloadDir | Out-Null
    $installer = Join-Path $downloadDir 'python-setup.exe'
    [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
    Invoke-WebRequest -UseBasicParsing -Uri "https://www.python.org/ftp/python/$version/python-$version$suffix.exe" -OutFile $installer
    $signature = Get-AuthenticodeSignature -LiteralPath $installer
    if ($signature.Status -ne 'Valid' -or $signature.SignerCertificate.Subject -notmatch 'O=Python Software Foundation(?:,|$)') {
        throw 'The Python installer signature could not be verified.'
    }
    $target = Join-Path $env:LOCALAPPDATA 'Programs\Python\LottoGeneratorPython313'
    $arguments = @('/quiet', 'InstallAllUsers=0', 'Include_pip=1', 'Include_tcltk=1',
        'Include_test=0', 'Include_launcher=0', 'PrependPath=1', 'Shortcuts=0',
        ('TargetDir="' + $target + '"'))
    $process = Start-Process -FilePath $installer -ArgumentList $arguments -Wait -PassThru -WindowStyle Hidden
    if ($process.ExitCode -notin @(0, 3010)) {
        throw "Python installer failed (exit code $($process.ExitCode))."
    }
    exit 0
} catch {
    Write-Host "[ERROR] $($_.Exception.Message)"
    Write-Host 'Check your internet connection, or install Python with tkinter from https://www.python.org/downloads/ and retry.'
    exit 1
} finally {
    if (Test-Path -LiteralPath $downloadDir) {
        Remove-Item -LiteralPath $downloadDir -Recurse -Force
    }
}
