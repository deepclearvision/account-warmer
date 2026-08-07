cd "C:\Users\Administrator\Desktop\AccountWarmer-Deploy-Enhanced\agents\facebook"
git checkout social-account-agents
git pull origin social-account-agents
try { git checkout -b parallel-ai-facebook-fix } catch { git checkout parallel-ai-facebook-fix }
Write-Host "READY: Paste the facebook prompt and say begin." -ForegroundColor Green
