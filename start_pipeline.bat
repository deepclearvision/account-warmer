@echo off
REM Launch pipeline with output to log file
cd /d "C:\Users\Administrator\Desktop\AccountWarmer-Deploy-Enhanced\account-warmer"
"C:\Program Files\Python312\python.exe" -u run_pipeline.py --all >> "C:\WarmingData\logs\pipeline_output.log" 2>&1
