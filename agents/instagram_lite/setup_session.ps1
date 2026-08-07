cd "C:\Users\Administrator\Desktop\AccountWarmer-Deploy-Enhanced\agents\instagram_lite"
git checkout social-account-agents
git pull origin social-account-agents
try { git checkout -b parallel-ai-instagram_lite-fix } catch { git checkout parallel-ai-instagram_lite-fix }
Write-Host "READY: Paste the instagram_lite prompt and say begin." -ForegroundColor Green
