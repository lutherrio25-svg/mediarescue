<#
.SYNOPSIS
    Sign every Windows binary mediarescue ships, then prove the signatures are there.

.DESCRIPTION
    SmartScreen says "Unknown publisher" for an unsigned exe, and it says it for every download
    until the file carries a signature from a certificate Windows already trusts. This script is
    the only place that knows how to attach one, so the local build and CI sign identically.

    Two ways in, because that is what the two real signers look like:
      * a certificate file (.pfx) + password  -- an OV/EV code signing certificate you bought
      * a thumbprint of a certificate already in the store -- a hardware token (EV keys cannot be
        exported, so they never become a .pfx) or Azure Trusted Signing on the runner

    It uses signtool when Windows SDK is installed and falls back to .NET's own signer otherwise,
    so a developer laptop without the SDK can still sign and verify locally. Timestamps matter:
    without one, every signature dies the day the certificate expires and already-shipped copies
    start warning again.

.PARAMETER Path
    Files or directories to sign. Directories are searched for *.exe (and *.dll).

.PARAMETER Thumbprint
    Thumbprint of a code signing certificate in Cert:\CurrentUser\My or Cert:\LocalMachine\My.

.PARAMETER PfxPath
    Path to a .pfx/.p12 code signing certificate. Requires -PfxPassword on CI unless the file is
    unencrypted.

.PARAMETER PfxPassword
    Password for -PfxPath.

.PARAMETER TimestampServer
    RFC 3161 timestamp authority. Default is DigiCert's, which stays free for this purpose.

.PARAMETER Verify
    After signing, check every file with Get-AuthenticodeSignature and fail if any is unsigned.

.PARAMETER AllowUntrusted
    Verification accepts a signature whose chain is not trusted here. Only for a self-signed test
    certificate on the developer's own machine -- a file signed that way still warns on a stranger's
    computer, so this flag must never be used for a release.

.EXAMPLE
    # local rehearsal with a throwaway self-signed certificate
    .\packaging\sign.ps1 -Path dist-exe\mediarescue -Thumbprint <thumb> -Verify -AllowUntrusted

.EXAMPLE
    # what CI does
    .\packaging\sign.ps1 -Path dist-exe\mediarescue,dist-setup -PfxPath cert.pfx -PfxPassword $env:CERT_PASSWORD -Verify
#>
[CmdletBinding(DefaultParameterSetName = "Thumbprint")]
param(
    [Parameter(Mandatory = $true)]
    [string[]] $Path,

    [Parameter(Mandatory = $true, ParameterSetName = "Thumbprint")]
    [string] $Thumbprint,

    [Parameter(Mandatory = $true, ParameterSetName = "Pfx")]
    [string] $PfxPath,

    [Parameter(Mandatory = $false, ParameterSetName = "Pfx")]
    [string] $PfxPassword,

    [string] $TimestampServer = "http://timestamp.digicert.com",

    [switch] $Verify,

    [switch] $AllowUntrusted
)

$ErrorActionPreference = "Stop"

function Get-Targets {
    param([string[]] $Paths)
    $files = New-Object System.Collections.Generic.List[string]
    foreach ($p in $Paths) {
        if (-not (Test-Path $p)) { throw "nothing to sign at $p" }
        if ((Get-Item $p).PSIsContainer) {
            Get-ChildItem -Path $p -Recurse -File |
                Where-Object { $_.Extension -in @(".exe", ".dll") } |
                ForEach-Object { $files.Add($_.FullName) }
        }
        else {
            $files.Add((Get-Item $p).FullName)
        }
    }
    if ($files.Count -eq 0) { throw "no .exe or .dll found under: $($Paths -join ', ')" }
    return $files
}

function Find-SignTool {
    $onPath = Get-Command "signtool.exe" -ErrorAction SilentlyContinue
    if ($onPath) { return $onPath.Source }
    $roots = @(
        "${env:ProgramFiles(x86)}\Windows Kits\10\bin",
        "$env:ProgramFiles\Windows Kits\10\bin"
    )
    foreach ($root in $roots) {
        if (-not (Test-Path $root)) { continue }
        $candidate = Get-ChildItem -Path $root -Recurse -Filter "signtool.exe" -ErrorAction SilentlyContinue |
            Where-Object { $_.FullName -match "x64" } |
            Sort-Object FullName -Descending |
            Select-Object -First 1
        if ($candidate) { return $candidate.FullName }
    }
    return $null
}

$targets = Get-Targets -Paths $Path
$signtool = Find-SignTool

# Everything after the import runs inside try/finally: a verification failure must not be able to
# leave a code-signing private key sitting in a shared runner's certificate store.
try {

# Resolve the certificate once, so a wrong thumbprint fails before anything is touched.
$certificate = $null
$imported = $null
if ($PSCmdlet.ParameterSetName -eq "Pfx") {
    if (-not (Test-Path $PfxPath)) { throw "certificate file not found: $PfxPath" }
    $secure = ConvertTo-SecureString -String $PfxPassword -AsPlainText -Force
    # Imported into the user store so the private key is usable, then removed at the end: a key
    # left behind on a shared runner is a leak waiting for someone with less to lose.
    $imported = Import-PfxCertificate -FilePath $PfxPath -CertStoreLocation Cert:\CurrentUser\My `
        -Password $secure
    $certificate = $imported
}
else {
    $normalised = $Thumbprint -replace "[^0-9A-Fa-f]", ""
    $certificate = Get-ChildItem Cert:\CurrentUser\My, Cert:\LocalMachine\My -ErrorAction SilentlyContinue |
        Where-Object { $_.Thumbprint -eq $normalised } |
        Select-Object -First 1
    if (-not $certificate) { throw "no certificate with thumbprint $normalised in the user or machine store" }
}
if (-not $certificate.HasPrivateKey) { throw "certificate $($certificate.Thumbprint) has no private key on this machine" }

Write-Host ("signing {0} file(s) as '{1}'" -f $targets.Count, $certificate.Subject)
if ($signtool) {
    Write-Host "using signtool: $signtool"
    # /fd sha256 is not optional in practice: a plain SHA-1 signature is rejected by several
    # Windows versions downloaded today, and /tr with an RFC 3161 authority is what survives
    # the certificate expiring.
    foreach ($file in $targets) {
        if ($PSCmdlet.ParameterSetName -eq "Pfx") {
            & $signtool sign /fd sha256 /tr $TimestampServer /td sha256 /f $PfxPath /p $PfxPassword $file
        }
        else {
            & $signtool sign /fd sha256 /tr $TimestampServer /td sha256 /sha1 $certificate.Thumbprint $file
        }
        if ($LASTEXITCODE -ne 0) { throw "signtool failed on $file (exit $LASTEXITCODE)" }
    }
}
else {
    Write-Host "signtool not found; signing with .NET (Set-AuthenticodeSignature)"
    foreach ($file in $targets) {
        $result = Set-AuthenticodeSignature -FilePath $file -Certificate $certificate `
            -TimestampServer $TimestampServer -HashAlgorithm SHA256
        if ($result.Status -ne "Valid" -and $result.Status -ne "UnknownError" -and $result.Status -ne "NotTrusted") {
            throw "signing $file failed: $($result.Status) $($result.StatusMessage)"
        }
    }
}

if ($Verify) {
    # Two facts differ and both matter: a file can carry a signature and still be signed by
    # something Windows does not trust (a self-signed certificate, an expired chain). Asking
    # Get-AuthenticodeSignature for Status alone conflates them -- an untrusted root reports
    # UnknownError, which is not the same word as "unsigned" -- so the chain is walked here and
    # only the reason is compared.
    $bad = @()
    foreach ($file in $targets) {
        $signature = Get-AuthenticodeSignature -FilePath $file
        $name = Split-Path $file -Leaf
        if (-not $signature.SignerCertificate) {
            Write-Host ("  {0,-28} {1}" -f $name, "UNSIGNED")
            $bad += "$file is unsigned"
            continue
        }
        $chain = New-Object System.Security.Cryptography.X509Certificates.X509Chain
        # Revocation needs the internet and is irrelevant for a rehearsal; Windows checks it at
        # launch time on the machine that runs the file.
        $chain.ChainPolicy.RevocationMode = [System.Security.Cryptography.X509Certificates.X509RevocationMode]::NoCheck
        $trusted = $chain.Build($signature.SignerCertificate)
        $reasons = @($chain.ChainStatus | ForEach-Object { $_.Status } | Where-Object { $_ -ne "NoError" })
        $onlySelfSigned = ($reasons.Count -gt 0) -and (@($reasons | Where-Object { $_ -ne "UntrustedRoot" }).Count -eq 0)
        Write-Host ("  {0,-28} {1,-14} {2}" -f $name, $(if ($trusted) { "trusted" } else { ($reasons -join ",") }), $signature.SignerCertificate.Subject)
        if ($trusted) { continue }
        if ($AllowUntrusted -and $onlySelfSigned) { continue }
        $bad += "$file is signed but this machine does not trust the chain ($($reasons -join ','))"
    }
    if ($bad.Count -gt 0) {
        throw ("signature check failed:`n  " + ($bad -join "`n  "))
    }
    if ($AllowUntrusted) { Write-Host "every file carries a signature from a trusted-or-rehearsal certificate" }
    else { Write-Host "every file is signed by a certificate Windows trusts" }
}

}
finally {
    if ($imported) {
        Remove-Item -Path ("Cert:\CurrentUser\My\" + $imported.Thumbprint) -Force -ErrorAction SilentlyContinue
        Write-Host "removed the imported certificate and its private key from this machine"
    }
}
