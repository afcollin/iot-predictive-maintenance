// Infrastructure as Code for Asset Condition Monitoring i Azure.
// Udrulles med infra/deploy.ps1. Se README for arkitektur.
//
//   GitHub Actions --OIDC--> id-github --AcrPush--> Container Registry
//                                       --Contributor (kun app'en)--> Container App
//   Container App --id-app--> AcrPull + Key Vault Secrets User
//   Container App --TLS--> PostgreSQL Flexible Server

@description('Azure-region. Skal være tilladt af abonnementets policy.')
param location string = resourceGroup().location

@description('GitHub-repo i formatet ejer/navn, som må deploye via OIDC.')
param githubRepo string

@description('Opret selve Container App\'en. Første kørsel er false, fordi image\'et skal ligge i registry\'et først.')
param deployApp bool = false

@description('Container image, fx acrxxx.azurecr.io/iot-predictive-maintenance:sha-abc1234')
param containerImage string = ''

@description('Antal instanser, der altid kører. 0 = skalér til nul (billigst, men langsom første request).')
@minValue(0)
param minReplicas int = 1

@secure()
@description('API-nøgle til IoT-enheder. Gemmes i Key Vault.')
param apiKey string

@secure()
@description('Administratoradgangskode til PostgreSQL. Gemmes i Key Vault.')
param dbPassword string

@description('Objekt-id på den person, der må læse og skifte hemmeligheder i Key Vault. Tom = ingen.')
param adminPrincipalId string = ''

var suffix = uniqueString(resourceGroup().id)
var dbAdmin = 'iotadmin'
var dbName = 'iot_monitoring'
var tags = {
  project: 'iot-predictive-maintenance'
  boundedContext: 'asset-condition-monitoring'
  course: 'KEA-6.2-afleveringsopgave-2'
}

// Indbyggede Azure-roller (faste id'er)
var roles = {
  acrPull: '7f951dda-4ed3-4680-a7ca-43fe172d538d'
  acrPush: '8311e382-0749-4cb8-b61a-304f252e45ec'
  keyVaultSecretsUser: '4633458b-17de-408a-b874-0445c86b69e6'
  keyVaultSecretsOfficer: 'b86a8fe4-44ce-4948-aee5-eccb2c155cd7'
  contributor: 'b24988ac-6180-42a0-ab88-20f7382dd24c'
}

// --- Identiteter (ingen adgangskoder: Azure udsteder tokens) ------------------

resource appIdentity 'Microsoft.ManagedIdentity/userAssignedIdentities@2023-01-31' = {
  name: 'id-iotpm-app'
  location: location
  tags: tags
}

resource githubIdentity 'Microsoft.ManagedIdentity/userAssignedIdentities@2023-01-31' = {
  name: 'id-iotpm-github'
  location: location
  tags: tags
}

// GitHub Actions må kun logge ind fra main-branchen i netop dette repo
resource githubFederation 'Microsoft.ManagedIdentity/userAssignedIdentities/federatedIdentityCredentials@2023-01-31' = {
  parent: githubIdentity
  name: 'github-main'
  properties: {
    issuer: 'https://token.actions.githubusercontent.com'
    subject: 'repo:${githubRepo}:ref:refs/heads/main'
    audiences: ['api://AzureADTokenExchange']
  }
}

// --- Container Registry ---------------------------------------------------------

resource acr 'Microsoft.ContainerRegistry/registries@2023-07-01' = {
  name: 'acriotpm${suffix}'
  location: location
  tags: tags
  sku: { name: 'Basic' }
  properties: {
    adminUserEnabled: false // kun adgang via Entra ID-identiteter
  }
}

resource acrPullForApp 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(acr.id, appIdentity.id, roles.acrPull)
  scope: acr
  properties: {
    principalId: appIdentity.properties.principalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', roles.acrPull)
  }
}

resource acrPushForGithub 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(acr.id, githubIdentity.id, roles.acrPush)
  scope: acr
  properties: {
    principalId: githubIdentity.properties.principalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', roles.acrPush)
  }
}

// --- PostgreSQL (separat database) ----------------------------------------------

resource postgres 'Microsoft.DBforPostgreSQL/flexibleServers@2024-08-01' = {
  name: 'psql-iotpm-${suffix}'
  location: location
  tags: tags
  sku: {
    name: 'Standard_B1ms'
    tier: 'Burstable'
  }
  properties: {
    version: '16'
    administratorLogin: dbAdmin
    administratorLoginPassword: dbPassword
    storage: { storageSizeGB: 32 }
    backup: {
      backupRetentionDays: 7
      geoRedundantBackup: 'Disabled'
    }
    highAvailability: { mode: 'Disabled' }
    network: { publicNetworkAccess: 'Enabled' }
  }
}

resource postgresDatabase 'Microsoft.DBforPostgreSQL/flexibleServers/databases@2024-08-01' = {
  parent: postgres
  name: dbName
  properties: {
    charset: 'UTF8'
    collation: 'en_US.utf8'
  }
}

// Tillader kun trafik fra Azure-tjenester. I produktion: privat netværk (VNet/private endpoint).
resource postgresFirewall 'Microsoft.DBforPostgreSQL/flexibleServers/firewallRules@2024-08-01' = {
  parent: postgres
  name: 'AllowAzureServices'
  properties: {
    startIpAddress: '0.0.0.0'
    endIpAddress: '0.0.0.0'
  }
}

// --- Key Vault (hemmeligheder) ---------------------------------------------------

resource keyVault 'Microsoft.KeyVault/vaults@2023-07-01' = {
  name: 'kv-iotpm-${take(suffix, 10)}'
  location: location
  tags: tags
  properties: {
    tenantId: subscription().tenantId
    sku: {
      family: 'A'
      name: 'standard'
    }
    enableRbacAuthorization: true
    enableSoftDelete: true
    softDeleteRetentionInDays: 7
  }
}

resource apiKeySecret 'Microsoft.KeyVault/vaults/secrets@2023-07-01' = {
  parent: keyVault
  name: 'api-key'
  properties: { value: apiKey }
}

resource databaseUrlSecret 'Microsoft.KeyVault/vaults/secrets@2023-07-01' = {
  parent: keyVault
  name: 'database-url'
  properties: {
    value: 'postgresql+psycopg://${dbAdmin}:${dbPassword}@${postgres.properties.fullyQualifiedDomainName}:5432/${dbName}?sslmode=require'
  }
}

resource dbPasswordSecret 'Microsoft.KeyVault/vaults/secrets@2023-07-01' = {
  parent: keyVault
  name: 'db-password'
  properties: { value: dbPassword }
}

resource secretsOfficerForAdmin 'Microsoft.Authorization/roleAssignments@2022-04-01' = if (!empty(adminPrincipalId)) {
  name: guid(keyVault.id, adminPrincipalId, roles.keyVaultSecretsOfficer)
  scope: keyVault
  properties: {
    principalId: adminPrincipalId
    principalType: 'User'
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', roles.keyVaultSecretsOfficer)
  }
}

resource secretsUserForApp 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(keyVault.id, appIdentity.id, roles.keyVaultSecretsUser)
  scope: keyVault
  properties: {
    principalId: appIdentity.properties.principalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', roles.keyVaultSecretsUser)
  }
}

// --- Container Apps (kørselsmiljø) ----------------------------------------------

resource logAnalytics 'Microsoft.OperationalInsights/workspaces@2023-09-01' = {
  name: 'log-iotpm-${suffix}'
  location: location
  tags: tags
  properties: {
    sku: { name: 'PerGB2018' }
    retentionInDays: 30
  }
}

resource environment 'Microsoft.App/managedEnvironments@2024-03-01' = {
  name: 'cae-iotpm'
  location: location
  tags: tags
  properties: {
    appLogsConfiguration: {
      destination: 'log-analytics'
      logAnalyticsConfiguration: {
        customerId: logAnalytics.properties.customerId
        sharedKey: logAnalytics.listKeys().primarySharedKey
      }
    }
  }
}

resource app 'Microsoft.App/containerApps@2024-03-01' = if (deployApp) {
  name: 'ca-iotpm-api'
  location: location
  tags: tags
  identity: {
    type: 'UserAssigned'
    userAssignedIdentities: { '${appIdentity.id}': {} }
  }
  properties: {
    managedEnvironmentId: environment.id
    configuration: {
      activeRevisionsMode: 'Single'
      ingress: {
        external: true
        targetPort: 8000
        transport: 'auto'
        allowInsecure: false // kun HTTPS
      }
      registries: [
        {
          server: acr.properties.loginServer
          identity: appIdentity.id
        }
      ]
      // Hemmelighederne hentes fra Key Vault ved opstart; de står aldrig i koden eller pipelinen
      secrets: [
        {
          name: 'api-key'
          keyVaultUrl: apiKeySecret.properties.secretUri
          identity: appIdentity.id
        }
        {
          name: 'database-url'
          keyVaultUrl: databaseUrlSecret.properties.secretUri
          identity: appIdentity.id
        }
      ]
    }
    template: {
      containers: [
        {
          name: 'api'
          image: containerImage
          resources: {
            cpu: json('0.25')
            memory: '0.5Gi'
          }
          env: [
            { name: 'API_KEY', secretRef: 'api-key' }
            { name: 'DATABASE_URL', secretRef: 'database-url' }
            { name: 'LOG_LEVEL', value: 'INFO' }
            { name: 'RATE_LIMIT_PER_MINUTE', value: '120' }
          ]
          probes: [
            {
              type: 'Liveness'
              httpGet: { path: '/health', port: 8000 }
              initialDelaySeconds: 10
              periodSeconds: 30
            }
            {
              type: 'Readiness'
              httpGet: { path: '/health', port: 8000 }
              initialDelaySeconds: 5
              periodSeconds: 10
            }
          ]
        }
      ]
      scale: {
        minReplicas: minReplicas
        maxReplicas: 2
        rules: [
          {
            name: 'http-load'
            http: { metadata: { concurrentRequests: '50' } }
          }
        ]
      }
    }
  }
  dependsOn: [
    acrPullForApp
    secretsUserForApp
  ]
}

// Least privilege: GitHub må kun opdatere selve Container App'en, ikke database eller Key Vault
resource contributorOnAppForGithub 'Microsoft.Authorization/roleAssignments@2022-04-01' = if (deployApp) {
  name: guid(resourceGroup().id, 'ca-iotpm-api', githubIdentity.id, roles.contributor)
  scope: app
  properties: {
    principalId: githubIdentity.properties.principalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', roles.contributor)
  }
}

output acrName string = acr.name
output acrLoginServer string = acr.properties.loginServer
output keyVaultName string = keyVault.name
output githubClientId string = githubIdentity.properties.clientId
output appName string = 'ca-iotpm-api'
output appUrl string = deployApp ? 'https://${app!.properties.configuration.ingress.fqdn}' : ''
