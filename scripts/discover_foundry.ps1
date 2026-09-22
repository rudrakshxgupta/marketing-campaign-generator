<#
.SYNOPSIS
  Find an existing Foundry resource, check whether MAI-Image-2.6 can run on it,
  and deploy it if so.

.DESCRIPTION
  For the case where a resource already exists rather than being created from
  scratch. It answers the three questions that decide whether this project can
  use it at all:

    1. Which Cognitive Services / AIServices resources exist?
    2. Is MAI-Image-2.6 offered to that subscription in that resource's region?
       It is preview and only in southindia and uaenorth, so an otherwise fine
       resource in the wrong region simply cannot run it.
    3. What is already deployed there?

  Read-only unless -Deploy is passed. Deploying is free; only generating costs.

  Requires `az login` first - this script never handles credentials.

.EXAMPLE
  ./scripts/discover_foundry.ps1

.EXAMPLE
  ./scripts/discover_foundry.ps1 -Deploy
#>
[CmdletBinding()]
param(
    [string]$ResourceName,
    # Create the MAI-Image-2.6 deployment if the region supports it.
    [switch]$Deploy,
    [string]$DeploymentName = "mai-image-26"
)

$ErrorActionPreference = "Stop"

function Head($t) { Write-Host "`n$t" -ForegroundColor Cyan }
function Ok($t)   { Write-Host "    OK  $t" -ForegroundColor Green }
function Warn($t) { Write-Host "    !   $t" -ForegroundColor Yellow }
function Bad($t)  { Write-Host "    X   $t" -ForegroundColor Red }

$az = (Get-Command az -ErrorAction SilentlyContinue).Source
if (-not $az) {
    $az = @(
        "$env:ProgramFiles\Microsoft SDKs\Azure\CLI2\wbin\az.cmd",
        "${env:ProgramFiles(x86)}\Microsoft SDKs\Azure\CLI2\wbin\az.cmd"
    ) | Where-Object { Test-Path $_ } | Select-Object -First 1
}
if (-not $az) { Bad "Azure CLI not found."; exit 1 }

function Invoke-AzJson {
    <#
      Run an az command and return parsed JSON, or $null on failure.

      Windows PowerShell 5.1 turns a native command's stderr into ErrorRecords,
      so a perfectly expected failure - "not signed in" - prints a wall of red
      NativeCommandError noise over the message we actually want to show. This
      keeps az quiet and lets the caller decide what to say.
    #>
    param([string[]]$Arguments)
    $previous = $ErrorActionPreference
    $ErrorActionPreference = "SilentlyContinue"
    try {
        $output = & $az @Arguments --output json --only-show-errors 2>$null
        if ($LASTEXITCODE -ne 0 -or -not $output) { return $null }
        return ($output | ConvertFrom-Json)
    } catch {
        return $null
    } finally {
        $ErrorActionPreference = $previous
    }
}

Head "[1] Account"
$account = Invoke-AzJson @("account", "show")
if (-not $account) { Bad "Not signed in. Run: az login"; exit 1 }
Ok "$($account.user.name)"
Ok "subscription: $($account.name)"

Head "[2] AI resources in this subscription"
$accounts = Invoke-AzJson @("cognitiveservices", "account", "list")
if (-not $accounts) { Bad "No Cognitive Services / AIServices resources found."; exit 1 }

foreach ($a in $accounts) {
    $marker = if ($a.location -in @("southindia", "uaenorth")) { "  <- can run MAI images" } else { "" }
    Write-Host ("    {0,-38} {1,-16} {2}{3}" -f $a.name, $a.location, $a.kind, $marker)
}

if ($ResourceName) {
    $target = $accounts | Where-Object { $_.name -eq $ResourceName } | Select-Object -First 1
} else {
    # Prefer a resource in a region that can actually run MAI images.
    $target = $accounts |
        Where-Object { $_.location -in @("southindia", "uaenorth") } |
        Select-Object -First 1
    if (-not $target) { $target = $accounts | Select-Object -First 1 }
}
if (-not $target) { Bad "Resource '$ResourceName' not found."; exit 1 }

$group = $target.resourceGroup
Head "[3] Using '$($target.name)'  (group $group, region $($target.location), kind $($target.kind))"

Head "[4] Existing deployments"
$deployments = Invoke-AzJson @("cognitiveservices", "account", "deployment", "list", "--name", $target.name, "--resource-group", $group)
if ($deployments) {
    foreach ($d in $deployments) {
        Write-Host ("    {0,-28} model={1} v{2}" -f $d.name, $d.properties.model.name, $d.properties.model.version)
    }
} else { Warn "none" }

Head "[5] Is MAI-Image-2.6 available here?"
$models = Invoke-AzJson @("cognitiveservices", "account", "list-models", "--name", $target.name, "--resource-group", $group)
$mai = $models | Where-Object { $_.name -like "MAI-Image*" }

if (-not $mai) {
    Bad "MAI image models are NOT offered to this subscription in $($target.location)."
    Write-Host @"

    They are public preview and available only in southindia and uaenorth.

    Either create a resource in one of those regions:

        ./scripts/setup_foundry.ps1 -ResourceName <globally-unique-name> -WriteEnv

    or use a different existing resource:

        ./scripts/discover_foundry.ps1 -ResourceName <name>
"@
    $imageModels = $models | Where-Object { $_.name -match "image|dall" } | Select-Object -First 8
    if ($imageModels) {
        Write-Host "`n    Image models that ARE available here:"
        foreach ($m in $imageModels) { Write-Host "      $($m.name) v$($m.version)" }
    }
    exit 1
}

foreach ($m in $mai) { Ok "$($m.name) v$($m.version)  sku=$($m.skus[0].name)" }

$exact = $mai | Where-Object { $_.name -eq "MAI-Image-2.6" } | Select-Object -First 1
if (-not $exact) { $exact = $mai | Select-Object -First 1; Warn "2.6 not offered; using $($exact.name)" }

if (-not $Deploy) {
    Write-Host "`n    Re-run with -Deploy to create the deployment (free; only generating costs).`n"
    exit 0
}

Head "[6] Deploying $($exact.name) as '$DeploymentName'"
& $az cognitiveservices account deployment create `
    --name $target.name --resource-group $group `
    --deployment-name $DeploymentName `
    --model-name $exact.name --model-format Microsoft `
    --model-version $exact.version `
    --sku-name GlobalStandard --sku-capacity 1 --output none
if ($LASTEXITCODE -ne 0) { Bad "Deployment failed."; exit 1 }
Ok "deployed"

$endpoint = "https://$($target.name).services.ai.azure.com"
Write-Host "`n" ("=" * 66)
Write-Host " Add to .env:" -ForegroundColor Green
Write-Host ("=" * 66)
Write-Host @"
MAI_MOCK=0
FOUNDRY_ENDPOINT=$endpoint
MAI_IMAGE_DEPLOYMENT=$DeploymentName
MAI_RPM=2

Then verify without spending anything:
    cd backend
    ..\.venv\Scripts\python scripts\check_connection.py $DeploymentName
"@
