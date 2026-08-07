cd "C:\Users\Administrator\Desktop\AccountWarmer-Deploy-Enhanced\agents\twitter"
git checkout social-account-agents
git pull origin social-account-agents
try { git checkout -b parallel-ai-twitter-fix } catch { git checkout parallel-ai-twitter-fix }
Write-Host "READY: Paste the twitter prompt and say begin." -ForegroundColor Green
