param(
    [Parameter(Mandatory = $true)]
    [string]$Message,

    [string]$ConfigPath = ".\\telegram-config.json"
)

$ErrorActionPreference = "Stop"

if (-not (Test-Path -LiteralPath $ConfigPath)) {
    throw "Config file not found: $ConfigPath"
}

$config = Get-Content -LiteralPath $ConfigPath -Raw | ConvertFrom-Json

if (-not $config.bot_token) {
    throw "Missing bot_token in config."
}

if (-not $config.chat_id) {
    throw "Missing chat_id in config."
}

$body = @{
    chat_id = [string]$config.chat_id
    text = $Message
} | ConvertTo-Json

$uri = "https://api.telegram.org/bot$($config.bot_token)/sendMessage"

Invoke-RestMethod -Uri $uri -Method Post -ContentType "application/json" -Body $body | Out-Null

Write-Output "Message sent to chat $($config.chat_id)."
