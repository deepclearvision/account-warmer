#!/bin/bash
# Wait for first run to complete, then run security pass
cd "C:/Users/Administrator/Desktop/AccountWarmer-Deploy/account-warmer"

echo "$(date): Waiting for first setup run to complete..."
while true; do
    last=$(tail -3 C:/WarmingData/logs/ml_setup_all.log 2>/dev/null)
    if echo "$last" | grep -q "Done\. Succeeded:"; then
        echo "$(date): First run complete. Starting security pass..."
        break
    fi
    sleep 15
done

python ml_setup_run.py --all --force --concurrency 2 >> C:/WarmingData/logs/ml_setup_security.log 2>&1
echo "$(date): Security pass complete." >> C:/WarmingData/logs/ml_setup_security.log
