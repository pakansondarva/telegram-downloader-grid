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

# --- 📁 AUTOMATED CREDENTIAL LOADER CONFIGURATION ---
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
# ---------------------------------------------------

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
            "Send me any link from Instagram, YouTube, Facebook, or Pinterest.\n"
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
    stream_url = None
    title_text = "Media Asset"
    ext = "mp4"

    try:
        if "instagram.com" in url or "facebook.com" in url:
            api_url = "https://snapinsta.app"
            req_headers = {
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
                "Referer": "https://snapinsta.app"
            }
            res = requests.post(api_url, data={"url": url, "action": "post"}, headers=req_headers, timeout=15)
            html_content = res.text
            
            video_matches = re.findall(r'href=\\?"(https://[^"]+download=1[^"]+)\\?"', html_content)
            if not video_matches:
                video_matches = re.findall(r'href=\\?"(https://[^"]+&oe=[^"]+)\\?"', html_content)
                
            if video_matches:
                # FIXED: Grabs the single string result out of the extracted list before running string replacement
                stream_url = video_matches[0].replace('\\', '')
            else:
                raise Exception("Private media signature challenge block. Try again.")
                
        else:
            cobalt_servers = ["https://unblockit.pro", "https://kuko.rip"]
            for srv in cobalt_servers:
                try:
                    response = requests.post(srv, json={"url": url, "videoQuality": "720", "downloadMode": "auto"}, headers={"Accept": "application/json", "Content-Type": "application/json"}, timeout=12)
                    if response.ok:
                        data = response.json()
                        if data.get("url"):
                            stream_url = data.get("url")
                            title_text = data.get("text", "Media Asset")
                            ext = "mp4" if data.get("pickerType") != "photo" else "jpg"
                            break
                except Exception:
                    continue

        if not stream_url:
            raise Exception("All parsing lanes failed to extract the asset. The link format might be broken.")

        file_res = requests.get(stream_url, stream=True, timeout=90, headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"})
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
            final_caption = f"🎬 <b>{title_text[:50]}...</b>\n\nDownloaded via Enterprise Network Grid ✨"
            
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
        else:
            raise Exception("The server returned an empty binary file payload track.")
            
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
            api_url = "https://kuko.rip"
            response = requests.post(api_url, json={"url": target_url, "downloadMode": "audio"}, headers={"Accept": "application/json", "Content-Type": "application/json"}, timeout=15).json()
            
            if response.get("status") == "error" or not response.get("url"):
                raise Exception("Audio stream track completely missing on mirror cluster nodes.")
                
            file_res = requests.get(response.get("url"), stream=True, timeout=90)
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
            if filename and os.path.exists(filename):
                os.remove(filename)

def start_bot_worker(token):
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
