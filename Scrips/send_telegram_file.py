#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os
import argparse
import mimetypes
import requests

# =========================
# 固定配置：直接写死
# =========================
BOT_TOKEN = "8334256556:AAGdKN6i2vijpf53dO-sDC8mpOrh8HGKjTA"

# 改成你的真实 ID
GROUP_CHAT_ID = "-5188801976"
USER_CHAT_ID = "6898341988"


def send_file(chat_id: str, file_path: str, caption: str = ""):
    if not os.path.isfile(file_path):
        raise FileNotFoundError(f"文件不存在: {file_path}")

    file_name = os.path.basename(file_path)
    mime_type, _ = mimetypes.guess_type(file_path)
    mime_type = mime_type or ""

    is_image = mime_type.startswith("image/")

    if is_image:
        url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendPhoto"
        field_name = "photo"
    else:
        url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendDocument"
        field_name = "document"

    data = {
        "chat_id": chat_id,
        "caption": caption
    }

    with open(file_path, "rb") as f:
        files = {
            field_name: (file_name, f)
        }
        response = requests.post(url, data=data, files=files, timeout=60)

    try:
        result = response.json()
    except Exception:
        raise RuntimeError(f"Telegram 返回非 JSON 响应: {response.text}")

    if response.status_code != 200 or not result.get("ok"):
        raise RuntimeError(f"发送失败: {result}")

    return result


def main():
    parser = argparse.ArgumentParser(description="发送 Telegram 文件或图片")
    parser.add_argument("-f", "--file", required=True, help="要发送的文件路径")
    parser.add_argument("-m", "--message", default="", help="附带说明文字")
    parser.add_argument(
        "-g",
        "--group",
        action="store_true",
        help="同时发送到群组；默认只发送给个人"
    )
    args = parser.parse_args()

    file_path = args.file
    caption = args.message

    # 默认只发给个人；加 -g 再额外发群
    targets = [("个人", USER_CHAT_ID)]
    if args.group:
        targets.append(("群组", GROUP_CHAT_ID))

    for target_name, chat_id in targets:
        try:
            result = send_file(chat_id, file_path, caption)
            print(f"[成功] 已发送到{target_name}: {chat_id}")
            print(result)
        except Exception as e:
            print(f"[失败] 发送到{target_name}失败: {chat_id}")
            print(str(e))


if __name__ == "__main__":
    main()
