// The Azure Bot resource of an Evokai Teams channel (deploy with:
//   az deployment group create -g <resource group> -f azure-bot.bicep \
//     -p botName=<name> appId=<Entra app id> tenantId=<tenant id> endpoint=<messaging endpoint>).
// The messaging endpoint is the one the console's Channels page shows:
//   {EVOKAI_PUBLIC_URL}/v1/channels/{application}/teams/{channel}/messages
param botName string
param appId string
param tenantId string
param endpoint string

resource bot 'Microsoft.BotService/botServices@2022-09-15' = {
  name: botName
  location: 'global'
  kind: 'azurebot'
  sku: { name: 'F0' }
  properties: {
    displayName: botName
    endpoint: endpoint
    msaAppId: appId
    msaAppTenantId: tenantId
    msaAppType: 'SingleTenant'
  }
}

resource teams 'Microsoft.BotService/botServices/channels@2022-09-15' = {
  parent: bot
  name: 'MsTeamsChannel'
  location: 'global'
  properties: {
    channelName: 'MsTeamsChannel'
    properties: { isEnabled: true }
  }
}
