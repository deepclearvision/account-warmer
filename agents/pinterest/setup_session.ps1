cd "C:\Users\Administrator\Desktop\AccountWarmer-Deploy-Enhanced\agents\pinterest"
git checkout social-account-agents
git pull origin social-account-agents
try { git checkout -b parallel-ai-pinterest-fix } catch { git checkout parallel-ai-pinterest-fix }
Write-Host "READY: Paste the pinterest prompt and say begin." -ForegroundColor Green
