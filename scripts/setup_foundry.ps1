<#
.SYNOPSIS
  Create the Microsoft Foundry resource and deploy MAI-Image-2.6.

.DESCRIPTION
  Runs the full provisioning sequence with checks between each step, so a
  failure tells you which prerequisite is missing rather than leaving a
  half-created resource group.

  Deploys to southindia by default: it is one of only two regions carrying
  MAI-Image-2.6 (the other is uaenorth) and it suits an India-focused product.

  You must run `az login` yourself first - this script never handles
  credentials.

.EXAMPLE
  ./scripts/setup_foundry.ps1 -ResourceName mcg-foundry-rg01

.EXAMPLE
  ./scripts/setup_foundry.ps1 -ResourceName mcg-foundry-rg01 -WhatIf
#>
[CmdletBinding(SupportsShouldProcess = $true)]
param(
    # Must be globally unique - it becomes the endpoint hostname.
    [Parameter(Mandatory = $true)]
    [string]$ResourceName,

    [string]$ResourceGroup = "mcg-rg",
    [ValidateSet("southindia", "uaenorth")]
    [string]$Location = "southindia",
    [string]$ProjectName = "mcg-project",
    [string]$ImageDeployment = "mai-image-26",
    [string]$DraftDeployment = "mai-image-26-flash",
    [string]$ModelVersion = "2026-07-31",

    # Deploying Flash as a separate deployment gives it its own RPM bucket,
    # which is what makes routing drafts to it worth doing.
    [switch]$SkipFlash,

    # Write the resulting settings to .env when finished.
    [switch]$WriteEnv
)

$ErrorActionPreference = "Stop"

function Step($n, $text) { Write-Host "`n[$n] $text" -ForegroundColor Cyan }
function Ok($text)       { Write-Host "    OK  $text" -ForegroundColor Green }
function Warn($text)     { Write-Host "    !   $text" -ForegroundColor Yellow }
function Fail($text)     { Write-Host "    X   $text" -ForegroundColor Red }

# --- prerequisites -------------------------------------------------------
Step 1 "Checking prerequisites"

if (-not (Get-Command az -ErrorAction SilentlyContinue)) {
    Fail "Azure CLI not found."
    Write-Host @"

    Install it, then open a NEW terminal so PATH refreshes:

        winget install --id Microsoft.AzureCLI -e

    Then sign in:

        az login
"@
    exit 1
}

$azVersion = (az version --output json | ConvertFrom-Json).'azure-cli'
Ok "Azure CLI $azVersion"
if ([version]$azVersion -lt [version]"2.80.0") {
    Warn "Project creation needs 2.80.0 or later. Run 'az upgrade'."
}

$account = az account show --output json 2>$null | ConvertFrom-Json
if (-not $account) {
    Fail "Not signed in. Run 'az login' first."
    exit 1
}
Ok "Signed in as $($account.user.name)"
Ok "Subscription: $($account.name)"

# --- resource group ------------------------------------------------------
Step 2 "Resource group '$ResourceGroup' in $Location"

if ($PSCmdlet.ShouldProcess($ResourceGroup, "create resource group")) {
    az group create --name $ResourceGroup --location $Location --output none
    Ok "ready"
}

# --- foundry resource ----------------------------------------------------
Step 3 "Foundry resource '$ResourceName'"
Write-Host "    (kind=AIServices, with managed identity and project management)"

if ($PSCmdlet.ShouldProcess($ResourceName, "create Foundry resource")) {
    # --custom-domain must be globally unique; it becomes the hostname.
    # --assign-identity is required or project creation fails later.
    # --allow-project-management cannot be changed after creation.
    az cognitiveservices account create `
        --name $ResourceName `
        --resource-group $ResourceGroup `
        --kind AIServices `
        --sku S0 `
        --location $Location `
        --custom-domain $ResourceName `
        --assign-identity `
        --allow-project-management true `
        --yes `
        --output none

    if ($LASTEXITCODE -ne 0) {
        Fail "Creation failed. If the error mentions CustomDomainInUse, the name is taken globally - pick another -ResourceName."
        exit 1
    }
    Ok "created"
}

# --- project -------------------------------------------------------------
Step 4 "Project '$ProjectName'"

if ($PSCmdlet.ShouldProcess($ProjectName, "create project")) {
    az cognitiveservices account project create `
        --name $ResourceName `
        --resource-group $ResourceGroup `
        --project-name $ProjectName `
        --location $Location `
        --output none
    Ok "created"
}

# --- confirm the model is actually offered -------------------------------
Step 5 "Checking MAI-Image-2.6 is available to this subscription"

$models = az cognitiveservices account list-models `
    --name $ResourceName --resource-group $ResourceGroup --output json | ConvertFrom-Json

$mai = $models | Where-Object { $_.name -like "MAI-Image-2.6*" }
if (-not $mai) {
    Fail "MAI-Image-2.6 is not offered to this subscription in $Location."
    Write-Host "    Available Microsoft-format models here:"
    $models | Where-Object { $_.format -eq "Microsoft" } |
        ForEach-Object { Write-Host "      $($_.name) v$($_.version)" }
    Write-Host "`n    These models are in public preview; access can vary by subscription."
    exit 1
}
foreach ($m in $mai) { Ok "$($m.name) v$($m.version) (sku $($m.skus[0].name))" }

# Prefer the version the service actually reports over our hard-coded guess.
$exact = $mai | Where-Object { $_.name -eq "MAI-Image-2.6" } | Select-Object -First 1
if ($exact -and $exact.version -ne $ModelVersion) {
    Warn "Service reports version $($exact.version); using that instead of $ModelVersion."
    $ModelVersion = $exact.version
}

# --- deployments ---------------------------------------------------------
Step 6 "Deploying MAI-Image-2.6 as '$ImageDeployment'"

if ($PSCmdlet.ShouldProcess($ImageDeployment, "create deployment")) {
    az cognitiveservices account deployment create `
        --name $ResourceName `
        --resource-group $ResourceGroup `
        --deployment-name $ImageDeployment `
        --model-name "MAI-Image-2.6" `
        --model-format Microsoft `
        --model-version $ModelVersion `
        --sku-name GlobalStandard `
        --sku-capacity 1 `
        --output none
    Ok "deployed"
}

if (-not $SkipFlash) {
    Step 7 "Deploying MAI-Image-2.6-Flash as '$DraftDeployment'"
    Write-Host "    (separate deployment = its own RPM bucket, so drafts never starve final renders)"

    if ($PSCmdlet.ShouldProcess($DraftDeployment, "create deployment")) {
        $flash = $mai | Where-Object { $_.name -eq "MAI-Image-2.6-Flash" } | Select-Object -First 1
        if ($flash) {
            az cognitiveservices account deployment create `
                --name $ResourceName `
                --resource-group $ResourceGroup `
                --deployment-name $DraftDeployment `
                --model-name "MAI-Image-2.6-Flash" `
                --model-format Microsoft `
                --model-version $flash.version `
                --sku-name GlobalStandard `
                --sku-capacity 1 `
                --output none
            Ok "deployed"
        } else {
            Warn "Flash not offered here - skipping. Drafts will use the main deployment."
        }
    }
}

# --- report --------------------------------------------------------------
if ($WhatIfPreference) { Write-Host "`n(what-if: nothing was created)`n"; exit 0 }

$endpoint = "https://$ResourceName.services.ai.azure.com"

Write-Host "`n" ("=" * 68)
Write-Host " Done. Settings for your .env:" -ForegroundColor Green
Write-Host ("=" * 68)
$envText = @"
MAI_MOCK=0
FOUNDRY_ENDPOINT=$endpoint
MAI_IMAGE_DEPLOYMENT=$ImageDeployment
MAI_DRAFT_DEPLOYMENT=$DraftDeployment
MAI_RPM=2
"@
Write-Host $envText

if ($WriteEnv) {
    $envPath = Join-Path (Split-Path $PSScriptRoot -Parent) ".env"
    if (Test-Path $envPath) {
        Copy-Item $envPath "$envPath.bak" -Force
        Warn "Existing .env backed up to .env.bak"
    }
    Set-Content -Path $envPath -Value $envText -Encoding utf8
    Ok "Written to $envPath"
}

Write-Host @"

Next:

  1. Verify the deployment actually works, and answer the six open
     questions the documentation cannot:

         .venv/Scripts/python scripts/probe_foundry.py

  2. Request a quota increase NOW - https://aka.ms/oai/stuquotarequest
     Priority goes to accounts already using their allocation, so the
     clock starts when you begin generating, not when you ask.

  Authentication uses Entra ID via your az login. No API key is needed,
  and none should be used in production.

"@
