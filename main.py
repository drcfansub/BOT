import os
import re
import asyncio
import aiohttp
import yt_dlp
import logging
from dotenv import load_dotenv
from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import CommandStart, Command

# ==========================================
# 1. SECURITY & CẤU HÌNH
# ==========================================
logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(message)s")
load_dotenv()

# Nhắc nhở: Luôn đặt TOKEN và ID trong file .env, KHÔNG để trong code
BOT_TOKEN = os.environ.get("BOT_TOKEN")
ADMIN_ID = os.environ.get("ADMIN_ID")
LOG_CHANNEL_ID = os.environ.get("LOG_CHANNEL_ID")

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()
semaphore = asyncio.Semaphore(10) # Efficiency: Giới hạn 10 tác vụ chạy song song

# ==========================================
# 2. LOGIC XỬ LÝ
# ==========================================
async def send_log(text: str):
    """Gửi log hoạt động về kênh riêng để theo dõi người dùng."""
    if LOG_CHANNEL_ID:
        try:
            await bot.send_message(chat_id=LOG_CHANNEL_ID, text=text, parse_mode="Markdown")
        except Exception as e:
            logging.error(f"Lỗi gửi log kênh: {e}")

def get_track_info(url: str) -> dict:
    """Robustness: Trích xuất thông tin nhạc với cấu hình tối ưu."""
    ydl_opts = {
        'format': 'bestaudio', 
        'quiet': True, 
        'no_warnings': True,
        'noplaylist': True,
        'socket_timeout': 10
    }
    with yt_dlp.YoutubeDL(ydl_opts) as y:
        return y.extract_info(url, download=False)

# ==========================================
# 3. HANDLERS
# ==========================================
@dp.message(CommandStart())
async def cmd_start(message: types.Message):
    await message.reply("🎧 Gửi link SoundCloud, tôi sẽ trả về link Stream trực tiếp!")
    await send_log(f"👤 User: {message.from_user.full_name} (`{message.from_user.id}`) vừa nhấn /start")

@dp.message(F.text.contains("soundcloud.com"))
async def handle_sc_link(message: types.Message):
    url_match = re.search(r'(https?://[^\s]+)', message.text)
    if not url_match: return
    
    raw_url = url_match.group(1)
    status_msg = await message.reply("⏳ `Đang lấy link...`", parse_mode="Markdown")
    
    async with semaphore:
        try:
            # Chạy tác vụ tốn tài nguyên trong thread riêng để tránh treo bot (Efficiency)
            info = await asyncio.to_thread(get_track_info, raw_url)
            direct_url = info.get('url')
            
            if not direct_url:
                raise ValueError("Không lấy được link stream.")

            # Trình bày kết quả trực tiếp (không nút bấm)
            response = (
                f"🎧 **{info.get('title')}**\n"
                f"👤 Nghệ sĩ: `{info.get('uploader')}`\n\n"
                f"🔗 **Link Stream:**\n`{direct_url}`"
            )
            
            await message.reply(response, parse_mode="Markdown", disable_web_page_preview=True)
            await status_msg.delete()
            
            # Gửi log
            await send_log(f"🔗 User: {message.from_user.full_name} vừa lấy link: {info.get('title')}")

        except Exception as e:
            logging.error(f"Lỗi xử lý link: {e}")
            await status_msg.edit_text("❌ **Lỗi:** Không thể lấy link nhạc này.")

# ==========================================
# 4. CHẠY BOT
# ==========================================
async def main():
    logging.info("🚀 Bot đã khởi động.")
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
