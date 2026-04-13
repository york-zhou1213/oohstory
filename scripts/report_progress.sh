#!/bin/bash
while true; do
  sleep 1800
  # 使用当前 session 的 workspace
  cd /root/.openclaw-tcs/workspace/webnovel-writer
  
  # 简化的进度统计
  progress=$(python3 -c "
import os, json
path = '/root/.openclaw-tcs/workspace/webnovel-writer/backend/services/skill_executor.py'
with open(path, 'r') as f:
    text = f.read()
    # 简单的进度逻辑判断
    completed = 0
    if 'if co_create and existing_world' in text: completed += 1
    if 'if co_create and existing_power' in text: completed += 1
    if 'if co_create and existing_char' in text: completed += 1
    if 'if co_create and existing_outline' in text: completed += 1
    print(int((completed/4)*100))
")
  
  # 发送 Telegram 消息
  /usr/bin/python3 -c "
import requests
token = '你的TelegramBotToken'
chat_id = '6898341988'
msg = f'''• 已完成: 初始化框架, A/B/C模式, 增量约束\n• 进行中: B类文件级增量补全\n• 未开始: C类重塑\n• 进度: {progress}%'''
requests.post(f'https://api.telegram.org/bot{token}/sendMessage', data={'chat_id': chat_id, 'text': msg})
" 2>/dev/null
done
