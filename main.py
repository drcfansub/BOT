import os
import re
import asyncio
import aiohttp
import yt_dlp
import logging
import tempfile
from dotenv import load_dotenv
from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import CommandStart, Command
from aiogram.types import FSInputFile
from aiogram.exceptions import TelegramAPIError

# ==========================================
# 1. SECURITY & CẤU HÌNH
# ==========================================
logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)

load_dotenv()
BOT_TOKEN = os.environ.get("BOT_TOKEN")
ADMIN_ID = os.environ.get("ADMIN_ID")
LOG_CHANNEL_ID = os.environ.get("LOG_CHANNEL_ID")

if not all([BOT_TOKEN, ADMIN_ID]):
    raise ValueError("❌ LỖI: Thiếu BOT_TOKEN hoặc ADMIN_ID trong file .env!")

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()
# Giới hạn 5 tác vụ tải/upload song song để không làm quá tải RAM/Băng thông của server
semaphore = asyncio.Semaphore(5) 
USERS_FILE = "users.txt"

# ==========================================
# 2. EFFICIENCY (LOGIC XỬ LÝ & LƯU TRỮ)
# ==========================================
def get_all_users() -> set:
    if not os.path.exists(USERS_FILE): return set()
    with open(USERS_FILE, "r") as f:
        return set(line.strip() for line in f if line.strip())

def save_user(user_id: int):
    user_str = str(user_id)
    if user_str not in get_all_users():
        with open(USERS_FILE, "a") as f: f.write(f"{user_str}\n")

async def send_channel_log(text: str):
    if not LOG_CHANNEL_ID: return
    try:
        await bot.send_message(chat_id=LOG_CHANNEL_ID, text=text, parse_mode="Markdown")
    except Exception as e:
        logger.warning(f"Lỗi gửi log kênh: {e}")

async def resolve_sc(url: str, session: aiohttp.ClientSession) -> str:
    """Xử lý link rút gọn on.soundcloud.com"""
    if "on.soundcloud.com" in url:
        try:
            async with session.get(url, allow_redirects=True, timeout=5) as r:
                return str(r.url) if "soundcloud.com" in str(r.url) else url
        except Exception as e:
            logger.error(f"Lỗi resolve link {url}: {e}")
    return url

def _download_track(url: str, temp_dir: str) -> dict:
    """
    Tải file nhạc vào thư mục tạm.
    Thiết lập để KHÔNG dùng FFMPEG (không có postprocessors).
    """
    opts = {
        'format': 'bestaudio/best', # Lấy audio tốt nhất có sẵn nguyên bản
        'outtmpl': os.path.join(temp_dir, '%(title)s.%(ext)s'),
        'quiet': True,
        'noplaylist': True,
        'no_warnings': True,
        'restrictfilenames': True, # Tránh lỗi tên file khi deploy trên môi trường Linux
        'max_filesize': 45000000 # Giới hạn ~45MB vì Telegram bot API giới hạn gửi file 50MB
    }
    with yt_dlp.YoutubeDL(opts) as ydl: 
        info = ydl.extract_info(url, download=True)
        return {
            'filepath': ydl.prepare_filename(info),
            'title': info.get('title', 'Unknown Track'),
            'uploader': info.get('uploader', 'Unknown Artist'),
            'duration': info.get('duration', 0)
        }

# ==========================================
# 3. ROBUSTNESS (HANDLERS)
# ==========================================
@dp.message(Command("tb"))
async def cmd_broadcast(message: types.Message):
    if str(message.from_user.id) != ADMIN_ID: return
    
    parts = message.text.split(maxsplit=1)
    if len(parts) < 2:
        return await message.reply("⚠️ Nhập nội dung: `/tb [thông báo]`", parse_mode="Markdown")
        
    users = get_all_users()
    if not users: return await message.reply("⚠️ Chưa có user.")

    status = await message.reply(f"⏳ Đang gửi tới {len(users)} users...")
    success, fail = 0, 0
    
    for uid in users:
        try:
            await bot.send_message(uid, f"**📢 THÔNG BÁO**\n\n{parts[1]}", parse_mode="Markdown")
            success += 1
            await asyncio.sleep(0.05)
        except TelegramAPIError:
            fail += 1

    await status.edit_text(f"✅ **Hoàn tất!**\n🟢 Thành công: `{success}`\n🔴 Thất bại: `{fail}`", parse_mode="Markdown")

@dp.message(CommandStart())
async def cmd_start(message: types.Message):
    user = message.from_user
    save_user(user.id)
    await send_channel_log(f"🟢 **#START**\n👤 User: [{user.full_name}](tg://user?id={user.id}) (`{user.id}`)")
    await message.reply("🎧 Gửi link SoundCloud (hoặc `on.soundcloud.com`).\nTôi sẽ tải bài hát và gửi trực tiếp qua đây cho bạn!", parse_mode="Markdown")

@dp.message(F.text)
async def handle_msg(message: types.Message):
    if "soundcloud.com" not in message.text: return
    
    url_match = re.search(r'(https?://[^\s]+)', message.text)
    if not url_match: return
    
    user = message.from_user
    raw_url = url_match.group(1)
    save_user(user.id)
    
    await send_channel_log(f"🔗 **#DOWNLOAD_REQUEST**\n👤 User: [{user.full_name}](tg://user?id={user.id})\n🎵 Link: {raw_url}")
    status_msg = await message.reply("⏳ `Đang xử lý và tải bài hát... Vui lòng chờ!`", parse_mode="Markdown")

    async with semaphore:
        async with aiohttp.ClientSession() as session:
            # Tạo một thư mục tạm thời tự động hủy sau khi ra khỏi khối lệnh (với vòng đời with)
            with tempfile.TemporaryDirectory() as temp_dir:
                try:
                    # 1. Giải mã link rút gọn nếu có
                    url = await resolve_sc(raw_url, session)
                    
                    # 2. Đẩy tiến trình tải vào một thread riêng biệt để không treo bot
                    info = await asyncio.to_thread(_download_track, url, temp_dir)
                    
                    # 3. Chuẩn bị file để upload
                    audio_file = FSInputFile(info['filepath'])
                    caption = (
                        f"🎧 **{info['title']}**\n"
                        f"👤 Nghệ sĩ: `{info['uploader']}`\n"
                        f"🤖 Tải bởi: @{bot._me.username if bot._me else 'Bot'}"
                    )
                    
                    # 4. Cập nhật trạng thái
                    await status_msg.edit_text("⬆️ `Đang upload lên Telegram...`", parse_mode="Markdown")
                    
                    # 5. Gửi file audio
                    await message.reply_audio(
                        audio=audio_file,
                        caption=caption,
                        performer=info['uploader'],
                        title=info['title'],
                        duration=info['duration'],
                        parse_mode="Markdown"
                    )
                    
                    # 6. Xóa tin nhắn trạng thái
                    await status_msg.delete()
                    await send_channel_log(f"✅ **#DOWNLOAD_SUCCESS**\n👤 User: [{user.full_name}](tg://user?id={user.id})\n🎵 Bài: {info['title']}")

                except Exception as e:
                    logger.error(f"Lỗi xử lý/tải link {raw_url}: {e}")
                    error_msg = str(e)
                    if "File is larger than" in error_msg or "max_filesize" in error_msg:
                        await status_msg.edit_text("❌ **Lỗi:** Bài hát quá dài (vượt quá 50MB giới hạn của Telegram)!", parse_mode="Markdown")
                    else:
                        await status_msg.edit_text("❌ **Lỗi:** Không thể tải bài hát. Có thể bị giới hạn khu vực hoặc link hỏng!", parse_mode="Markdown")

# ==========================================
# 4. CHẠY BOT
# ==========================================
async def main():
    logger.info("🚀 Bot tải nhạc SoundCloud đã khởi động!")
    try:
        await bot.send_message(ADMIN_ID, "🟢 **Hệ thống tải nhạc đã sẵn sàng!**", parse_mode="Markdown")
    except: pass

    await bot.delete_webhook(drop_pending_updates=True)
    await dp.start_polling(bot)

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("🛑 Bot đã tắt.")
