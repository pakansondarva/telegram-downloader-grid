import os
import re
import html
import uuid
import json
import time
import threading
import requests
from http.server import BaseHTTPRequestHandler, HTTPServer
import telebot
from telebot.types import InlineKeyboardMarkup, InlineKeyboardButton

# 📝 TOKENS LOADED FROM YOUR config.json FILE
CONFIG_FILE = "config.json"

def load_bot_tokens():
    if not os.path.exists(CONFIG_FILE):
        default_config = {"BOT_TOKENS": ["PASTE_YOUR_TOKEN_HERE"]}
        with open(CONFIG_FILE, 'w') as f:
            json.dump(default_config, f, indent=4)
        return []
    try:
        with open(CONFIG_FILE, 'r') as f:
            return json.load(f).get("BOT_TOKENS", [])
    except Exception:
        return []

BOT_TOKENS = load_bot_tokens()

DOWNLOAD_DIR = "./downloads"
DATA_FILE = "session_map.json"
os.makedirs(DOWNLOAD_DIR, exist_ok=True)

LINK_REGEX = r'(https?://[^\s]+)'
db_lock = threading.Lock()

def safe_html(text):
    if not text:
        return ""
    return html.escape(str(text))

def save_link_session(link_id, url):
    with db_lock:
        data = {}
        if os.path.exists(DATA_FILE):
            try:
                with open(DATA_FILE, 'r') as f:
                    data = json.load(f)
            except Exception:
                data = {}
        data[link_id] = url
        with open(DATA_FILE, 'w') as f:
            json.dump(data, f)

def get_link_session(link_id):
    with db_lock:
        if os.path.exists(DATA_FILE):
            try:
                with open(DATA_FILE, 'r') as f:
                    return json.load(f).get(link_id)
            except Exception:
                return None
        return None

def register_bot_logic(bot_instance):
    @bot_instance.message_handler(commands=['start', 'help'])
    def send_welcome(message):
        welcome_text = (
            f"👋 <b>Welcome to {bot_instance.get_me().first_name}!</b>\n\n"
            "Send me any link from Instagram, YouTube, X, or Pinterest.\n"
            "I will immediately download it and send you the playable media card!"
        )
        bot_instance.reply_to(message, welcome_text, parse_mode='HTML')

    @bot_instance.message_handler(func=lambda message: re.search(LINK_REGEX, message.text))
    def handle_links(message):
        url = re.search(LINK_REGEX, message.text).group(1)
        sent_msg = bot_instance.reply_to(message, "⚡ <i>Processing media asset... Please wait.</i>", parse_mode='HTML')
        threading.Thread(target=process_media_download, args=(bot_instance, message, url, sent_msg), daemon=True).start()

    @bot_instance.callback_query_handler(func=lambda call: True)
    def handle_query(call):
        action, link_id = call.data.split('|')
        chat_id = call.message.chat.id
        try:
            bot_instance.answer_callback_query(call.id)
        except Exception:
            pass
        threading.Thread(target=process_callback_query, args=(bot_instance, call, action, link_id, chat_id), daemon=True).start()

def process_media_download(bot, message, url, sent_msg):
    link_id = str(uuid.uuid4())[:8]
    save_link_session(link_id, url)
    filename = None
    try:
        api_url = "https://cobalt.tools"
        res_data = requests.post(api_url, json={"url": url, "videoQuality": "720", "downloadMode": "auto"}, headers={"Accept": "application/json", "Content-Type": "application/json"}, timeout=25).json()
        
        if res_data.get("status") == "error":
            raise Exception(res_data.get("text", "Platform Extraction Blocked"))
            
        stream_url = res_data.get("url")
        file_text = safe_html(res_data.get("text", "Media Asset"))
        
        if not stream_url:
            raise Exception("Media tracks failed to resolve.")
            
        file_res = requests.get(stream_url, stream=True, timeout=90)
        ext = "mp4" if res_data.get("pickerType") != "photo" else "jpg"
        
        filename = os.path.join(DOWNLOAD_DIR, f"{link_id}_output.{ext}")
        with open(filename, 'wb') as f:
            for chunk in file_res.iter_content(chunk_size=8192):
                if chunk:
                    f.write(chunk)
                    
        if os.path.exists(filename) and os.path.getsize(filename) > 0:
            markup = InlineKeyboardMarkup()
            markup.row(
                InlineKeyboardButton("🎵 Extract MP3 Audio", callback_data=f"aud|{link_id}"),
                InlineKeyboardButton("📝 Copy Full Caption", callback_data=f"txt|{link_id}")
            )
            final_caption = f"🎬 <b>{file_text[:60]}...</b>\n\nDownloaded via Cloud Network ✨"
            
            try:
                bot.delete_message(message.chat.id, sent_msg.message_id)
            except:
                pass
                
            with open(filename, 'rb') as media_file:
                if ext == "jpg":
                    bot.send_photo(message.chat.id, media_file, caption=final_caption, reply_markup=markup, parse_mode='HTML')
                else:
                    bot.send_video(message.chat.id, media_file, caption=final_caption, reply_markup=markup, parse_mode='HTML')
            os.remove(filename)
    except Exception as e:
        try:
            bot.edit_message_text(f"❌ <b>Extraction failed:</b> {safe_html(str(e))}", chat_id=message.chat.id, message_id=sent_msg.message_id, parse_mode='HTML')
        except:
            bot.send_message(message.chat.id, f"❌ <b>Extraction failed:</b> {safe_html(str(e))}", parse_mode='HTML')
        if filename and os.path.exists(filename):
            os.remove(filename)

def process_callback_query(bot, call, action, link_id, chat_id):
    target_url = get_link_session(link_id)
    if not target_url:
        bot.send_message(chat_id, "❌ <b>Session expired!</b>", parse_mode='HTML')
        return
    if action == "txt":
        bot.send_message(chat_id, f"🔗 <b>Source URL:</b>\n\n<code>{target_url}</code>", parse_mode='HTML')
    elif action == "aud":
        status_msg = bot.send_message(chat_id, "📥 <i>Extracting audio track...</i>", parse_mode='HTML')
        filename = None
        try:
            api_url = "https://cobalt.tools"
            res = requests.post(api_url, json={"url": target_url, "downloadMode": "audio"}, headers={"Accept": "application/json", "Content-Type": "application/json"}, timeout=25).json()
            if res.get("status") == "error" or not res.get("url"):
                raise Exception("Audio stream missing.")
            file_res = requests.get(res.get("url"), stream=True, timeout=90)
            filename = os.path.join(DOWNLOAD_DIR, f"audio_{link_id}.mp3")
            with open(filename, 'wb') as f:
                for chunk in file_res.iter_content(chunk_size=8192):
                    f.write(chunk)
            with open(filename, 'rb') as media_file:
                bot.send_audio(chat_id, media_file, caption="🎵 <b>Audio track isolated!</b>", parse_mode='HTML')
            os.remove(filename)
            bot.delete_message(chat_id, status_msg.message_id)
        except Exception as e:
            bot.send_message(chat_id, f"❌ Audio failed: {safe_html(str(e))}", parse_mode='HTML')

def start_bot_worker(token):
    # CRUCIAL DELAY: Gives Render exactly 15 seconds to kill the old container and free your tokens completely
    time.sleep(15)
    while True:
        try:
            instance = telebot.TeleBot(token, threaded=False)
            register_bot_logic(instance)
            print(f"🤖 Bot Online: @{instance.get_me().username}")
            instance.infinity_polling(skip_pending=True, timeout=40, long_polling_timeout=40)
        except Exception:
            time.sleep(5)

class FreeTierPingHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-type", "text/html")
        self.end_headers()
        self.wfile.write(b"Bot Grid Status: Operational & Online")

def run_ping_server():
    port = int(os.environ.get("PORT", 8080))
    server = HTTPServer(("0.0.0.0", port), FreeTierPingHandler)
    server.serve_forever()

if __name__ == "__main__":
    print("🚀 Initializing Free-Tier Validation Port Server...")
    # 1. Start web server FIRST to pass Render's port checker immediately
    web_thread = threading.Thread(target=run_ping_server, daemon=True)
    web_thread.start()
    
    # 2. Boot background bots after validation passes
    for t in BOT_TOKENS:
        threading.Thread(target=start_bot_worker, args=(t,), daemon=True).start()
        
    threading.Event().wait()
