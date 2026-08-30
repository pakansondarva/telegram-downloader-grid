import os
import re
import html
import uuid
import json
import threading
import time
import telebot
from telebot.types import InlineKeyboardMarkup, InlineKeyboardButton
import yt_dlp

# --- 📁 AUTOMATED CONFIGURATION LOADER LINE ---
CONFIG_FILE = "config.json"

def load_bot_tokens():
    if not os.path.exists(CONFIG_FILE):
        # Creates a blank template automatically if the file gets misplaced
        default_config = {"BOT_TOKENS": ["PASTE_YOUR_TOKEN_HERE"]}
        with open(CONFIG_FILE, 'w') as f:
            json.dump(default_config, f, indent=4)
        print(f"⚠️ {CONFIG_FILE} was missing. Created a blank template file. Add your tokens there!")
        return []
    
    try:
        with open(CONFIG_FILE, 'r') as f:
            config_data = json.load(f)
            return config_data.get("BOT_TOKENS", [])
    except Exception as e:
        print(f"❌ Error reading config.json: {e}")
        return []

BOT_TOKENS = load_bot_tokens()
# ------------------------------------------------

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
                    data = json.load(f)
                    return data.get(link_id)
            except Exception:
                return None
        return None

def register_bot_logic(bot_instance):
    @bot_instance.message_handler(commands=['start', 'help'])
    def send_welcome(message):
        welcome_text = (
            f"👋 <b>Welcome to {bot_instance.get_me().first_name}!</b>\n\n"
            "Send me any link from Instagram, YouTube, X, or Pinterest.\n"
            "I will immediately fetch and send the actual playable video or photo directly!"
        )
        bot_instance.reply_to(message, welcome_text, parse_mode='HTML')

    @bot_instance.message_handler(func=lambda message: re.search(LINK_REGEX, message.text))
    def handle_links(message):
        url = re.search(LINK_REGEX, message.text).group(1)
        sent_msg = bot_instance.reply_to(message, "⚡ <i>Downloading media asset... Please wait.</i>", parse_mode='HTML')
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
    outtmpl = os.path.join(DOWNLOAD_DIR, f"{link_id}_%(title)s.%(ext)s")
    
    ydl_opts = {
        'outtmpl': outtmpl,
        'max_filesize': 50 * 1024 * 1024,
        'socket_timeout': 300,
        'retries': 10,
        'ignoreerrors': True,
        'http_headers': {
            'User-Agent': 'Mozilla/5.0 (Linux; Android 10; SM-G960F) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Mobile Safari/537.36',
            'Accept': '*/*',
            'X-IG-App-ID': '936619743392459',
        }
    }
    
    if "youtube.com" in url or "youtu.be" in url:
        ydl_opts['extractor_args'] = {'youtube': ['player_client=ios,android', 'skip=dash,hls']}
        ydl_opts['format'] = 'mp4[height<=720]/best'
    elif "instagram.com" in url:
        ydl_opts['extractor_args'] = {'instagram': ['client=web']}
        ydl_opts['format'] = 'best'
    else:
        ydl_opts['format'] = 'best[ext=mp4]/best'
    
    filename = None
    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=True)
            if not info:
                raise Exception("Media extraction blocked by platform network tags.")
            if 'entries' in info and info['entries']:
                info = info['entries']
            title = safe_html(info.get('title', 'Media Asset'))
            caption_text = safe_html(info.get('description', 'No description.'))[:150]
            filename = ydl.prepare_filename(info)
            
            if not os.path.exists(filename):
                base, _ = os.path.splitext(filename)
                for ext in ['mp4', 'm4a', 'webm', 'jpg', 'jpeg', 'png', 'webp', 'mkv', 'mp3']:
                    if os.path.exists(f"{base}.{ext}"):
                        filename = f"{base}.{ext}"
                        break

        if filename and os.path.exists(filename):
            markup = InlineKeyboardMarkup()
            markup.row(
                InlineKeyboardButton("🎵 Extract MP3 Audio", callback_data=f"aud|{link_id}"),
                InlineKeyboardButton("📝 Copy Full Caption", callback_data=f"txt|{link_id}")
            )
            final_caption = f"🎬 <b>{title}</b>\n\n📝 {caption_text}\n\nDownloaded via Creator Network ✨"
            
            try:
                bot.delete_message(message.chat.id, sent_msg.message_id)
            except:
                pass
            
            _, ext = os.path.splitext(filename.lower())
            
            if ext in ['.jpg', '.jpeg', '.png', '.webp']:
                with open(filename, 'rb') as media_file:
                    bot.send_photo(message.chat.id, media_file, caption=final_caption, reply_markup=markup, parse_mode='HTML', timeout=300)
            else:
                if ext not in ['.mp4', '.mkv', '.webm', '.3gp']:
                    forced_mp4_path = os.path.splitext(filename) + ".mp4"
                    os.rename(filename, forced_mp4_path)
                    filename = forced_mp4_path
                
                with open(filename, 'rb') as media_file:
                    bot.send_video(message.chat.id, media_file, caption=final_caption, reply_markup=markup, parse_mode='HTML', timeout=300)
            
            if os.path.exists(filename):
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
        with yt_dlp.YoutubeDL({'extract_flat': True, 'ignoreerrors': True}) as ydl:
            info = ydl.extract_info(target_url, download=False)
            caption = safe_html(info.get('description', 'No caption found.'))
            bot.send_message(chat_id, f"📝 <b>Raw Caption:</b>\n\n<code>{caption}</code>", parse_mode='HTML')
            
    elif action == "aud":
        status_msg = bot.send_message(chat_id, "📥 <i>Extracting audio track...</i>", parse_mode='HTML')
        unique_id = str(uuid.uuid4())[:8]
        outtmpl = os.path.join(DOWNLOAD_DIR, f"audio_{unique_id}_%(title)s.%(ext)s")
        ydl_opts = {'outtmpl': outtmpl, 'format': 'bestaudio/best', 'max_filesize': 50*1024*1024, 'socket_timeout': 300, 'retries': 10, 'ignoreerrors': True}
        try:
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                info = ydl.extract_info(target_url, download=True)
                filename = ydl.prepare_filename(info)
                if not os.path.exists(filename):
                    base, _ = os.path.splitext(filename)
                    for ext in ['m4a', 'mp3', 'webm', 'ogg', 'wav']:
                        if os.path.exists(f"{base}.{ext}"):
                            filename = f"{base}.{ext}"
                            break
            with open(filename, 'rb') as media_file:
                bot.send_audio(chat_id, media_file, caption="🎵 <b>Audio track isolated!</b>", parse_mode='HTML', timeout=300)
            os.remove(filename)
            bot.delete_message(chat_id, status_msg.message_id)
        except Exception as e:
            bot.send_message(chat_id, f"❌ Audio extraction failed: {safe_html(str(e))}", parse_mode='HTML')

def start_bot_worker(token):
    while True:
        try:
            instance = telebot.TeleBot(token, threaded=False)
            telebot.apihelper.CONNECT_TIMEOUT = 300
            telebot.apihelper.READ_TIMEOUT = 300
            register_bot_logic(instance)
            print(f"🤖 Bot Online: @{instance.get_me().username}")
            instance.infinity_polling(skip_pending=True, timeout=60, long_polling_timeout=60)
        except Exception as err:
            time.sleep(5)

if __name__ == "__main__":
    if not BOT_TOKENS:
        print("❌ System halted: No tokens loaded from config.json.")
    else:
        print("🚀 Initializing Local Multi-Bot Network Terminal Grid...")
        for t in BOT_TOKENS:
            threading.Thread(target=start_bot_worker, args=(t,), daemon=True).start()
        threading.Event().wait()
