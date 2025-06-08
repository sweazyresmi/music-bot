import os
import logging
import re
import random
import asyncio
from pyrogram import Client, filters
from pyrogram.types import InlineKeyboardMarkup, InlineKeyboardButton, BotCommand
from pyrogram.errors import ChatAdminRequired, UserNotParticipant
from pytgcalls import PyTgCalls, idle
from pytgcalls.types import Update, StreamType, InputAudioStream
from pytgcalls.exceptions import GroupCallNotFoundError, NoActiveGroupCall
import yt_dlp
from urllib.parse import urlparse

# Loglama ayarları
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

BOT_TOKEN = os.getenv("7553627647:AAH1ACD80_upyg375AB5zPkmthiNvH_zAas")
API_ID = int(os.getenv("27747039"))
API_HASH = os.getenv("0341ef2a1c98e02498d8c48a5bcb4df5")

ADMINS = [7161462642]  # Kendi Telegram ID'n ile değiştir
BAN_WORDS = ["18+", "ahlama", "küfür", "ahmak", "xxx", "sex"]

app = Client("musicbot", api_id=API_ID, api_hash=API_HASH, bot_token=BOT_TOKEN)
pytgcalls = PyTgCalls(app)

chat_queues = {}  # {chat_id: [(song_url, song_title, duration)]}
playing = {}      # {chat_id: index}
is_paused = {}    # {chat_id: bool} Duraklatma durumu
active_chats = {} # {chat_id: bool} Aktif sohbetler
used_codes = {}   # {kod: bool} Kullanılmış kodları izler

def check_ban_words(text: str):
    text = text.lower()
    return any(word in text for word in BAN_WORDS)

def is_url(text: str) -> bool:
    url_pattern = re.compile(
        r'^https?://'  # http veya https
        r'(?:(?:[a-zA-Z0-9](?:[a-zA-Z0-9-]{0,61}[a-zA-Z0-9])?\.)+[a-zA-Z]{2,}|'  # domain
        r'(?:\d{1,3}\.){3}\d{1,3}|'  # IPv4
        r'\[?[0-9a-fA-F:]+]?)'  # IPv6
        r'(?::\d+)?(?:/[^#\s]*)?(?:#\S*)?$'
    )
    return bool(url_pattern.match(text.strip()))

def format_duration(seconds):
    minutes, secs = divmod(int(seconds or 0), 60)
    return f"{minutes:02d}:{secs:02d}"

def music_buttons(chat_id):
    if not chat_queues.get(chat_id, []):
        return None
    _, song_title, duration = chat_queues[chat_id][playing.get(chat_id, 0)]
    buttons = [
        [
            InlineKeyboardButton("⏪ Geri Sar", callback_data="rewind"),
            InlineKeyboardButton("⏹️ Durdur" if not is_paused.get(chat_id, False) else "▶️ Devam", callback_data="pause_resume"),
            InlineKeyboardButton("⏩ İleri Sar", callback_data="forward"),
        ],
        [
            InlineKeyboardButton("⏮️ Önceki", callback_data="previous"),
            InlineKeyboardButton("⏭️ Atla", callback_data="skip"),
            InlineKeyboardButton("🔁 Tekrarla", callback_data="repeat"),
        ],
        [
            InlineKeyboardButton("❌ Kapat", callback_data="close"),
        ],
        [
            InlineKeyboardButton("🗣️ Destek", url="https://t.me/kiriImaz"),
        ],
    ]
    return InlineKeyboardMarkup(buttons)

async def get_audio_stream(query: str) -> tuple:
    ydl_opts = {
        'format': 'bestaudio/best',
        'noplaylist': True,
        'quiet': True,
        'postprocessors': [{
            'key': 'FFmpegExtractAudio',
            'preferredcodec': 'mp3',
            'preferredquality': '192',
        }],
    }
    
    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(query if is_url(query) else f"ytsearch:{query}", download=False)
            if 'entries' in info:
                entry = info['entries'][0]
                url = entry['url']
                title = entry['title']
                duration = entry.get('duration', 0)
            else:
                url = info['url']
                title = info.get('title', query)
                duration = info.get('duration', 0)
            if 'm3u8' in url or 'manifest' in url:
                url = info['formats'][0]['url'] if 'formats' in info else url
            return url, title, duration
    except yt_dlp.utils.DownloadError as e:
        raise Exception(f"Video alınamadı: {str(e)}")

async def start_playing(chat_id):
    if not chat_queues.get(chat_id, []) or not active_chats.get(chat_id, False):
        return
    index = playing.get(chat_id, 0)
    if index >= len(chat_queues[chat_id]):
        playing[chat_id] = 0
    song_url, song_title, duration = chat_queues[chat_id][index]
    try:
        if not pytgcalls.is_connected(chat_id):
            msg = await app.send_message(chat_id, "🎶 Şarkı yükleniyor...")
        else:
            msg = await app.get_messages(chat_id, message_ids=await app.get_history(chat_id, limit=1))
        await pytgcalls.join_group_call(
            chat_id,
            InputAudioStream(song_url),
            stream_type=StreamType.PULSE_STREAM,
        )
        is_paused[chat_id] = False
        message_text = (
            "Akış Başlatıldı! ✅\n\n"
            f"🎙️ {song_title}\n"
            f"Süre: {format_duration(duration)} ⌛\n\n"
            "Menü;\n"
            "Oynatılan şarkı 🎶\n\n"
            f"🎙️ {song_title}\n"
            f"Süre: {format_duration(duration)} ⌛\n"
        )
        await msg.edit_text(message_text, reply_markup=music_buttons(chat_id))
        logger.info(f"Şarkı çalınıyor: {song_title} (Chat ID: {chat_id})")
    except GroupCallNotFoundError:
        await app.send_message(chat_id, "Grup araması bulunamadı, lütfen bir sesli sohbet başlatın!")
    except Exception as e:
        logger.error(f"Hata oluştu: {str(e)}")
        await app.send_message(chat_id, f"Şarkı çalınamadı: {str(e)}")

async def is_admin(client, chat_id, user_id):
    try:
        member = await client.get_chat_member(chat_id, user_id)
        return member.status in ["creator", "administrator"] or user_id in ADMINS
    except (ChatAdminRequired, UserNotParticipant) as e:
        logger.warning(f"Admin kontrol hatası: {str(e)}")
        return user_id in ADMINS
    except Exception as e:
        logger.error(f"Beklenmedik hata: {str(e)}")
        return False

async def set_bot_commands():
    commands = [
        BotCommand(command="help", description="Kullanılabilir komutları gösterir."),
        BotCommand(command="oynat", description="Ses akışı başlatır."),
        BotCommand(command="msil", description="Sıradan yazılan şarkıyı siler."),
        BotCommand(command="sira", description="Sırayı gösterir."),
        BotCommand(command="duraklat", description="Akışı duraklatır."),
        BotCommand(command="devam", description="Akışı devam ettirir."),
        BotCommand(command="atla", description="Sonraki akışa atlar."),
        BotCommand(command="döngü", description="Mevcut akışı tekrarlar."),
        BotCommand(command="ilerisar", description="Akışı ileri sarar."),
        BotCommand(command="gerisar", description="Akışı geri sarar."),
        BotCommand(command="basasar", description="Akışı başa sarar."),
        BotCommand(command="denetle", description="Gecikmeyi ölçer."),
        BotCommand(command="destek", description="Admin hesabı @kiriImaz destek almak için yazabilirsiniz."),
        BotCommand(command="activate", description="Botu bu sohbet için aktive et."),
        BotCommand(command="kod", description="Rastgele 8 haneli kod oluşturur (sadece adminler)."),
    ]
    await app.set_bot_commands(commands)

def generate_code():
    return ''.join(str(random.randint(1, 9)) for _ in range(8))

@app.on_message(filters.command(["activate"], prefixes=["/", "\\", "|", "!", "₺"]) & (filters.private | filters.group))
async def activate_bot(client, message):
    chat_id = message.chat.id
    user_id = message.from_user.id
    if not await is_admin(client, chat_id, user_id):
        await message.reply("Bu komutu sadece yönetici kullanabilir!")
        return
    active_chats[chat_id] = True
    await message.reply("Bot bu sohbet için aktive edildi! Şimdi komutları kullanabilirsiniz. ⛔ Kodunuz yoksa @kiriImaz'dan satın alabilirsiniz.")

@app.on_message(filters.command(["kod"], prefixes=["/", "\\", "|", "!", "₺"]) & (filters.private | filters.group))
async def generate_code_command(client, message):
    chat_id = message.chat.id
    user_id = message.from_user.id
    if not await is_admin(client, chat_id, user_id):
        await message.reply("Bu komutu sadece yönetici kullanabilir!")
        return
    code = generate_code()
    used_codes[code] = False  # Yeni kod oluşturuldu, henüz kullanılmadı
    await app.send_message(user_id, f"Özel kodunuz: `{code}`\nBu kod yalnızca bir kez kullanılabilir.", parse_mode="Markdown")
    logger.info(f"Kod oluşturuldu: {code} için kullanıcı {user_id}")

@app.on_message(filters.command(["oynat"], prefixes=["/", "\\", "|", "!", "₺"]) & (filters.private | filters.group))
async def play_song(client, message):
    chat_id = message.chat.id
    user_id = message.from_user.id
    if not active_chats.get(chat_id, False):
        await message.reply("Bot bu sohbet için aktive edilmemiş! Lütfen /activate komutunu kullanın. ⛔ Kodunuz yoksa @kiriImaz'dan satın alabilirsiniz.")
        return
    if len(message.command) < 2:
        await message.reply("Şarkı ismi veya linki yazmalısın!")
        return
    query = " ".join(message.command[1:])
    if check_ban_words(query):
        await message.reply("Bu içerik yasaklandı, başka şarkı deneyin!")
        return
    if chat_id not in chat_queues:
        chat_queues[chat_id] = []
        playing[chat_id] = 0
    
    msg = await message.reply("🔎 Şarkı aranıyor...")
    try:
        song_url, song_title, duration = await get_audio_stream(query)
    except Exception as e:
        await msg.edit_text(f"Şarkı bulunamadı: {str(e)}")
        return
    
    chat_queues[chat_id].append((song_url, song_title, duration))
    await msg.edit_text(f"🎵 Şarkı sıraya eklendi: {song_title}", reply_markup=music_buttons(chat_id))
    if len(chat_queues[chat_id]) == 1:
        await start_playing(chat_id)

@app.on_message(filters.command(["sira"], prefixes=["/", "\\", "|", "!", "₺"]) & (filters.private | filters.group))
async def show_queue(client, message):
    chat_id = message.chat.id
    if not active_chats.get(chat_id, False):
        await message.reply("Bot bu sohbet için aktive edilmemiş! Lütfen /activate komutunu kullanın. ⛔ Kodunuz yoksa @kiriImaz'dan satın alabilirsiniz.")
        return
    if not chat_queues.get(chat_id, []):
        await message.reply("📜 Kuyruk boş!")
        return
    queue_text = "📜 Şarkı Kuyruğu:\n"
    for i, (url, title, duration) in enumerate(chat_queues[chat_id]):
        status = "🎶 Çalınıyor" if i == playing.get(chat_id, 0) else f"{i + 1}. "
        queue_text += f"{status} {title} ({format_duration(duration)})\n"
    await message.reply(queue_text, reply_markup=music_buttons(chat_id))

@app.on_message(filters.command(["msil"], prefixes=["/", "\\", "|", "!", "₺"]) & (filters.private | filters.group))
async def delete_song(client, message):
    chat_id = message.chat.id
    user_id = message.from_user.id
    if not active_chats.get(chat_id, False):
        await message.reply("Bot bu sohbet için aktive edilmemiş! Lütfen /activate komutunu kullanın. ⛔ Kodunuz yoksa @kiriImaz'dan satın alabilirsiniz.")
        return
    if not await is_admin(client, chat_id, user_id):
        await message.reply("Bu komutu sadece yönetici kullanabilir!")
        return
    if len(message.command) < 2:
        await message.reply("Silmek için şarkı numarasını belirtmelisin! Örnek: /msil 2")
        return
    try:
        index = int(message.command[1]) - 1
        if 0 <= index < len(chat_queues.get(chat_id, [])):
            song_title = chat_queues[chat_id][index][1]
            if index == playing.get(chat_id, 0):
                await pytgcalls.leave_group_call(chat_id)
                del chat_queues[chat_id][index]
                if chat_queues[chat_id]:
                    playing[chat_id] = min(playing[chat_id], len(chat_queues[chat_id]) - 1)
                    await start_playing(chat_id)
                else:
                    playing[chat_id] = 0
            else:
                del chat_queues[chat_id][index]
            await message.reply(f"🗑 Şarkı silindi: {song_title}", reply_markup=music_buttons(chat_id))
        else:
            await message.reply("Geçersiz şarkı numarası!")
    except ValueError as e:
        await message.reply(f"Şarkı numarası bir sayı olmalı: {str(e)}")

@app.on_message(filters.command(["duraklat", "devam", "atla", "döngü", "ilerisar", "gerisar", "basasar", "denetle", "destek"], prefixes=["/", "\\", "|", "!", "₺"]) & (filters.private | filters.group))
async def admin_commands(client, message):
    chat_id = message.chat.id
    user_id = message.from_user.id
    if not active_chats.get(chat_id, False):
        await message.reply("Bot bu sohbet için aktive edilmemiş! Lütfen /activate komutunu kullanın. ⛔ Kodunuz yoksa @kiriImaz'dan satın alabilirsiniz.")
        return
    if not await is_admin(client, chat_id, user_id) and message.command[0] != "destek":
        await message.reply("Bu komutu sadece yönetici kullanabilir!")
        return
    cmd = message.command[0]
    
    if cmd == "duraklat":
        try:
            await pytgcalls.pause_stream(chat_id)
            is_paused[chat_id] = True
            await message.reply("⏸ Şarkı duraklatıldı.", reply_markup=music_buttons(chat_id))
        except AttributeError:
            await message.reply("⚠️ Duraklatma özelliği bu sürümde desteklenmiyor.")
    elif cmd == "devam":
        try:
            await pytgcalls.resume_stream(chat_id)
            is_paused[chat_id] = False
            await message.reply("▶ Şarkı devam ediyor.", reply_markup=music_buttons(chat_id))
        except AttributeError:
            await message.reply("⚠️ Devam etme özelliği bu sürümde desteklenmiyor.")
    elif cmd == "atla":
        if chat_queues.get(chat_id, []) and playing.get(chat_id, 0) < len(chat_queues[chat_id]) - 1:
            playing[chat_id] += 1
            await start_playing(chat_id)
        else:
            await pytgcalls.leave_group_call(chat_id)
            await message.reply("⏭ Kuyrukta başka şarkı yok.", reply_markup=music_buttons(chat_id))
    elif cmd == "döngü":
        if chat_queues.get(chat_id, []):
            await start_playing(chat_id)
            await message.reply("🔁 Şarkı tekrarlanıyor.", reply_markup=music_buttons(chat_id))
    elif cmd == "ilerisar":
        await message.reply("⚠️ İleri sarma tam desteklenmiyor.", reply_markup=music_buttons(chat_id))
    elif cmd == "gerisar":
        await message.reply("⚠️ Geri sarma tam desteklenmiyor.", reply_markup=music_buttons(chat_id))
    elif cmd == "basasar":
        if chat_queues.get(chat_id, []):
            await start_playing(chat_id)
            await message.reply("⏮ Şarkı başa sarıldı.", reply_markup=music_buttons(chat_id))
    elif cmd == "denetle":
        try:
            latency = await pytgcalls.ping(chat_id)
            await message.reply(f"🕒 Gecikme: {latency} ms", reply_markup=music_buttons(chat_id))
        except Exception as e:
            await message.reply(f"⚠️ Gecikme ölçümü yapılamadı: {str(e)}", reply_markup=music_buttons(chat_id))
    elif cmd == "destek":
        await message.reply("Destek için: https://t.me/kiriImaz", reply_markup=music_buttons(chat_id))

@app.on_callback_query()
async def callbacks(client, callback_query):
    user_id = callback_query.from_user.id
    chat_id = callback_query.message.chat.id
    if not active_chats.get(chat_id, False):
        await callback_query.answer("Bot bu sohbet için aktive edilmemiş! Lütfen /activate komutunu kullanın. ⛔ Kodunuz yoksa @kiriImaz'dan satın alabilirsiniz.", show_alert=True)
        return
    if not await is_admin(client, chat_id, user_id):
        await callback_query.answer("Yetkin yok!", show_alert=True)
        return
    data = callback_query.data
    
    if data == "pause_resume":
        if is_paused.get(chat_id, False):
            try:
                await pytgcalls.resume_stream(chat_id)
                is_paused[chat_id] = False
            except AttributeError:
                await callback_query.answer("Devam etme özelliği desteklenmiyor!", show_alert=True)
                return
        else:
            try:
                await pytgcalls.pause_stream(chat_id)
                is_paused[chat_id] = True
            except AttributeError:
                await callback_query.answer("Duraklatma özelliği desteklenmiyor!", show_alert=True)
                return
        _, song_title, duration = chat_queues[chat_id][playing.get(chat_id, 0)]
        message_text = (
            "Akış Devam Ediyor! ✅\n\n" if not is_paused[chat_id] else "Akış Duraklatıldı! ⏸\n\n"
            f"🎙️ {song_title}\n"
            f"Süre: {format_duration(duration)} ⌛\n\n"
            "Menü;\n"
            "Oynatılan şarkı 🎶\n\n"
            f"🎙️ {song_title}\n"
            f"Süre: {format_duration(duration)} ⌛\n"
        )
        await callback_query.message.edit_text(message_text, reply_markup=music_buttons(chat_id))
    elif data == "skip":
        if chat_queues.get(chat_id, []) and playing.get(chat_id, 0) < len(chat_queues[chat_id]) - 1:
            playing[chat_id] += 1
            await start_playing(chat_id)
        else:
            await pytgcalls.leave_group_call(chat_id)
            await callback_query.message.delete()
    elif data == "previous":
        if chat_queues.get(chat_id, []) and playing.get(chat_id, 0) > 0:
            playing[chat_id] -= 1
            await start_playing(chat_id)
        else:
            await callback_query.message.edit_text("⏮ Önceki şarkı yok.", reply_markup=music_buttons(chat_id))
    elif data == "repeat":
        if chat_queues.get(chat_id, []):
            await start_playing(chat_id)
            _, song_title, duration = chat_queues[chat_id][playing.get(chat_id, 0)]
            message_text = (
                "Akış Tekrarlandı! 🔁\n\n"
                f"🎙️ {song_title}\n"
                f"Süre: {format_duration(duration)} ⌛\n\n"
                "Menü;\n"
                "Oynatılan şarkı 🎶\n\n"
                f"🎙️ {song_title}\n"
                f"Süre: {format_duration(duration)} ⌛\n"
            )
            await callback_query.message.edit_text(message_text, reply_markup=music_buttons(chat_id))
    elif data == "forward":
        await callback_query.message.edit_text("⚠️ İleri sarma tam desteklenmiyor.", reply_markup=music_buttons(chat_id))
    elif data == "rewind":
        await callback_query.message.edit_text("⚠️ Geri sarma tam desteklenmiyor.", reply_markup=music_buttons(chat_id))
    elif data == "close":
        await callback_query.message.delete()
    await callback_query.answer()

@pytgcalls.on_stream_end()
async def on_stream_end(client, update: Update):
    chat_id = update.chat_id
    if chat_queues.get(chat_id, []) and playing.get(chat_id, 0) < len(chat_queues[chat_id]) - 1 and active_chats.get(chat_id, False):
        playing[chat_id] += 1
        await start_playing(chat_id)
    else:
        try:
            await pytgcalls.leave_group_call(chat_id)
            if chat_id in chat_queues:
                del chat_queues[chat_id]
            if chat_id in playing:
                del playing[chat_id]
            if chat_id in is_paused:
                del is_paused[chat_id]
            if chat_id in active_chats:
                del active_chats[chat_id]
            await app.send_message(chat_id, "🎵 Kuyruk bitti.", reply_markup=None)
        except NoActiveGroupCall:
            logger.warning(f"Grup çağrısı zaten aktif değil: {chat_id}")

@app.on_message(filters.command(["start", "help"]) & (filters.private | filters.group))
async def start(client, message):
    await set_bot_commands()
    await message.reply("Bot başlatıldı! Botu aktive etmek için /activate komutunu kullanın. ⛔ Kodunuz yoksa @kiriImaz'dan satın alabilirsiniz. Kullanılabilir komutlar arayüzde listelenmiştir.")

if __name__ == "__main__":
    os.makedirs("downloads", exist_ok=True)
    app.start()
    pytgcalls.start()
    loop = asyncio.get_event_loop()
    loop.run_until_complete(set_bot_commands())
    logger.info("Bot başladı...")
    idle()