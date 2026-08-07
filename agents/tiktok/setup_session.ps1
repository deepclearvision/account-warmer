cd "C:\Users\Administrator\Desktop\AccountWarmer-Deploy-Enhanced\agents\tiktok"
git checkout social-account-agents
git pull origin social-account-agents
try { git checkout -b parallel-ai-tiktok-fix } catch { git checkout parallel-ai-tiktok-fix }
Write-Host "READY: Paste the tiktok prompt and say begin." -ForegroundColor Green
