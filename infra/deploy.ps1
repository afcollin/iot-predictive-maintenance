<#
.SYNOPSIS
    Udruller Asset Condition Monitoring til Azure: Container Apps, PostgreSQL, Key Vault og Container Registry.

.DESCRIPTION
    1. Registrerer resource providers og opretter resource group
    2. Udruller infrastrukturen fra main.bicep (uden app)
    3. Bygger Docker-image lokalt og pusher det til Azure Container Registry
    4. Udruller igen, nu med Container App'en
    5. Gemmer Azure-id'erne som GitHub-variabler, så pipelinen kan deploye via OIDC

    Scriptet kan køres igen. Eksisterende hemmeligheder genbruges, medmindre -RotateSecrets angives.

.EXAMPLE
    ./infra/deploy.ps1 -Location francecentral
#>
param(
    # francecentral: Azure for Students tillader kun 5 EU-regioner, og PostgreSQL B1ms afvises i swedencentral og polandcentral
    [string]$Location = "francecentral",
    [string]$ResourceGroup = "rg-iot-predictive-maintenance",
    [string]$GitHubRepo = "afcollin/iot-predictive-maintenance",
    [int]$MinReplicas = 1,
    [switch]$RotateSecrets
)

$ErrorActionPreference = "Stop"
$repoRoot = Split-Path -Parent $PSScriptRoot
$azPath = "C:\Program Files\Microsoft SDKs\Azure\CLI2\wbin"
if (-not (Get-Command az -ErrorAction SilentlyContinue) -and (Test-Path $azPath)) { $env:Path = "$azPath;$env:Path" }

function Invoke-Native {
    # Stopper scriptet, hvis et eksternt program (az, docker, gh) fejler
    $output = & $args[0] @($args | Select-Object -Skip 1)
    if ($LASTEXITCODE -ne 0) { throw "Kommandoen fejlede: $($args -join ' ')" }
    return $output
}

function New-Secret([int]$Length = 32) {
    $alphabet = [char[]]"ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz23456789"
    $bytes = New-Object byte[] $Length
    [System.Security.Cryptography.RandomNumberGenerator]::Create().GetBytes($bytes)
    return -join ($bytes | ForEach-Object { $alphabet[$_ % $alphabet.Length] })
}

function Invoke-Optional {
    # Til opslag, der godt må fejle (fx når en ressource ikke findes endnu). Returnerer $null ved fejl.
    $previous = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    try {
        $output = & $args[0] @($args | Select-Object -Skip 1) 2>$null
        if ($LASTEXITCODE -eq 0 -and $output) { return ($output | Out-String).Trim() } else { return $null }
    } finally {
        $ErrorActionPreference = $previous
    }
}

function Get-ExistingSecret([string]$Vault, [string]$Name) {
    if (-not $Vault) { return $null }
    return Invoke-Optional az keyvault secret show --vault-name $Vault --name $Name --query value -o tsv
}

function Invoke-Deployment([hashtable]$Parameters) {
    # Hemmelighederne skrives til en midlertidig parameterfil, så de ikke står i proceslisten eller loggen
    $file = Join-Path $env:TEMP "iotpm-params-$([guid]::NewGuid()).json"
    $content = @{ '$schema' = "https://schema.management.azure.com/schemas/2019-04-01/deploymentParameters.json#"; contentVersion = "1.0.0.0"; parameters = @{} }
    foreach ($key in $Parameters.Keys) { $content.parameters[$key] = @{ value = $Parameters[$key] } }
    [System.IO.File]::WriteAllText($file, ($content | ConvertTo-Json -Depth 5), (New-Object System.Text.UTF8Encoding $false))
    try {
        $json = Invoke-Native az deployment group create --resource-group $ResourceGroup --name "iotpm-$(Get-Date -Format yyyyMMdd-HHmmss)" `
            --template-file (Join-Path $PSScriptRoot "main.bicep") --parameters "@$file" --query properties.outputs -o json --only-show-errors
        return ($json | Out-String | ConvertFrom-Json)
    } finally {
        Remove-Item $file -Force -ErrorAction SilentlyContinue
    }
}

Write-Host "`n== 1/5 Abonnement og resource group ==" -ForegroundColor Cyan
$account = Invoke-Native az account show -o json | Out-String | ConvertFrom-Json
Write-Host "Abonnement: $($account.name)  Bruger: $($account.user.name)"
foreach ($provider in "Microsoft.App", "Microsoft.OperationalInsights", "Microsoft.ContainerRegistry",
                      "Microsoft.DBforPostgreSQL", "Microsoft.KeyVault", "Microsoft.ManagedIdentity") {
    Invoke-Native az provider register --namespace $provider --wait --only-show-errors | Out-Null
}
Invoke-Native az group create --name $ResourceGroup --location $Location --tags project=iot-predictive-maintenance -o none
$adminPrincipalId = Invoke-Optional az ad signed-in-user show --query id -o tsv
if (-not $adminPrincipalId) { $adminPrincipalId = "" }

$existingVault = Invoke-Optional az keyvault list --resource-group $ResourceGroup --query "[0].name" -o tsv
$apiKey = if ($RotateSecrets) { $null } else { Get-ExistingSecret $existingVault "api-key" }
$dbPassword = if ($RotateSecrets) { $null } else { Get-ExistingSecret $existingVault "db-password" }
if (-not $apiKey) { $apiKey = New-Secret 40; Write-Host "Ny API-nøgle genereret" }
if (-not $dbPassword) { $dbPassword = "Aa1" + (New-Secret 29); Write-Host "Ny databaseadgangskode genereret" }

$githubOwnerId = (Invoke-Native gh api "repos/$GitHubRepo" --jq .owner.id | Out-String).Trim()
$githubRepoId = (Invoke-Native gh api "repos/$GitHubRepo" --jq .id | Out-String).Trim()

$parameters = @{
    location         = $Location
    githubRepo       = $GitHubRepo
    githubOwnerId    = $githubOwnerId
    githubRepoId     = $githubRepoId
    alertEmail       = $account.user.name   # alarmer sendes til den, der udruller
    apiKey           = $apiKey
    dbPassword       = $dbPassword
    adminPrincipalId = $adminPrincipalId
    minReplicas      = $MinReplicas
}

Write-Host "`n== 2/5 Infrastruktur (Bicep): registry, database, Key Vault, identiteter ==" -ForegroundColor Cyan
Write-Host "PostgreSQL tager typisk 5-10 minutter første gang..."
$outputs = Invoke-Deployment ($parameters + @{ deployApp = $false })
$acrName = $outputs.acrName.value
$loginServer = $outputs.acrLoginServer.value

Write-Host "`n== 3/5 Docker-image til Azure Container Registry ==" -ForegroundColor Cyan
$sha = (Invoke-Native git -C $repoRoot rev-parse --short HEAD).Trim()
$image = "$loginServer/iot-predictive-maintenance:sha-$sha"
Invoke-Native az acr login --name $acrName --only-show-errors | Out-Null
Invoke-Native docker build -t $image $repoRoot | Out-Null
Invoke-Native docker push $image | Out-Null
Write-Host "Pushet: $image"

Write-Host "`n== 4/5 Container App ==" -ForegroundColor Cyan
$outputs = Invoke-Deployment ($parameters + @{ deployApp = $true; containerImage = $image })
$appUrl = $outputs.appUrl.value

Write-Host "`n== 5/5 GitHub-variabler til OIDC-deploy ==" -ForegroundColor Cyan
$variables = @{
    AZURE_CLIENT_ID         = $outputs.githubClientId.value
    AZURE_TENANT_ID         = $account.tenantId
    AZURE_SUBSCRIPTION_ID   = $account.id
    AZURE_RESOURCE_GROUP    = $ResourceGroup
    AZURE_ACR_NAME          = $acrName
    AZURE_CONTAINERAPP_NAME = $outputs.appName.value
}
foreach ($name in $variables.Keys) {
    Invoke-Native gh variable set $name --body $variables[$name] --repo $GitHubRepo | Out-Null
}
Write-Host "Sat: $($variables.Keys -join ', ')"

Write-Host "`nFærdig." -ForegroundColor Green
Write-Host "Swagger:    $appUrl/docs"
Write-Host "Health:     $appUrl/health"
Write-Host "API-nøgle:  az keyvault secret show --vault-name $($outputs.keyVaultName.value) --name api-key --query value -o tsv"
