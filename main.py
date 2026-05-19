import os
import re
import shutil
import asyncio
import tempfile
import logging
from dotenv import load_dotenv
from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import CommandStart
from aiogram.types import FSInputFile
from aiogram.client.session.aiohttp import AiohttpSession
import yt_dlp
import aiohttp

# ==========================================
# 1. SECURITY & CẤU HÌNH BẢO MẬT
# ==========================================
load_dotenv()
BOT_TOKEN = os.environ.get("BOT_TOKEN")
ADMIN_ID = os.environ.get("ADMIN_ID")

if not BOT_TOKEN:
    raise ValueError("❌ BẢO MẬT: Không tìm thấy BOT_TOKEN trong biến môi trường!")

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)

# [ROBUSTNESS]: Tăng thời gian chờ (Timeout) lên 10 phút (600s) để tránh lỗi đứt gánh khi upload file nặng
session = AiohttpSession(timeout=600)
bot = Bot(token=BOT_TOKEN, session=session)
dp = Dispatcher()

# [EFFICIENCY]: Xử lý tối đa 5 tác vụ song song
semaphore = asyncio.Semaphore(5)

# ==========================================
# 2. XỬ LÝ TẢI NHẠC (EFFICIENCY & ROBUSTNESS)
# ==========================================
async def resolve_sc(url: str, session_http: aiohttp.ClientSession) -> str:
    """Giải mã link rút gọn để yt_dlp có thể đọc được."""
    if "on.soundcloud.com" in url:
        try:
            async with session_http.get(url, allow_redirects=True, timeout=10) as r:
                return str(r.url) if "soundcloud.com" in str(r.url) else url
        except Exception as e:
            logger.warning(f"Lỗi resolve link: {e}")
    return url

def _download_track_sync(url: str) -> dict:
    """Tải và convert nhạc thông qua FFMPEG."""
    temp_dir = tempfile.mkdtemp()
    
    # [EFFICIENCY]: Dùng FFMPEG để convert thành chuẩn MP3 thật sự, tránh lỗi định dạng từ Telegram
    opts = {
        'format': 'bestaudio/best',
        'outtmpl': os.path.join(temp_dir, '%(title)s.%(ext)s'),
        'postprocessors': [{
            'key': 'FFmpegExtractAudio',
            'preferredcodec': 'mp3',
            'preferredquality': '192',
        }],
        'quiet': True,
        'no_warnings': True,
        'noplaylist': True,
        'max_filesize': 45000000  # Giới hạn 45MB
    }
    
    with yt_dlp.YoutubeDL(opts) as ydl:
        info = ydl.extract_info(url, download=True)
        # Vì đã có postprocessor, file chắc chắn sẽ có đuôi .mp3
        filepath = os.path.join(temp_dir, f"{info['title']}.mp3")
        
        # Đảm bảo đường dẫn file tồn tại
        if not os.path.exists(filepath):
            filepath = ydl.prepare_filename(info).rsplit('.', 1)[0] + '.mp3'

        return {
            'filepath': filepath,
            'title': info.get('title', 'Unknown Track'),
            'uploader': info.get('uploader', 'Unknown Artist'),
            'temp_dir': temp_dir
        }

# ==========================================
# 3. HANDLERS (CLEAN CODE)
# ==========================================
@dp.message(CommandStart())
async def cmd_start(message: types.Message):
    await message.reply("🎧 Gửi link SoundCloud, tôi sẽ tải về và gửi lên đây bằng định dạng MP3 chất lượng cao!")

@dp.message(F.text.contains("soundcloud.com"))
async def handle_download(message: types.Message):
    url_match = re.search(r'(https?://[^\s]+)', message.text)
    if not url_match: return
    
    raw_url = url_match.group(1)
    status_msg = await message.reply("⏳ `Đang xử lý và tải bài hát...`", parse_mode="Markdown")

    async with semaphore:
        async with aiohttp.ClientSession() as session_http:
            try:
                # 1. Resolve Link & Download
                url = await resolve_sc(raw_url, session_http)
                info = await asyncio.to_thread(_download_track_sync, url)
                
                # 2. Chuẩn bị upload
                await status_msg.edit_text("⬆️ `Đang tải lên Telegram (có thể mất chút thời gian)...`", parse_mode="Markdown")
                audio_file = FSInputFile(info['filepath'])
                caption = f"🎧 **{info['title']}**\n👤 {info['uploader']}"
                
                # 3. Gửi file (Sẽ không bị timeout vì đã nâng cấu hình session ở trên)
                await message.reply_audio(
                    audio=audio_file,
                    caption=caption,
                    performer=info['uploader'],
                    title=info['title'],
                    parse_mode="Markdown"
                )
                await status_msg.delete()

            except Exception as e:
                # [ROBUSTNESS]: Log chi tiết lỗi ra console để dễ debug
                logger.error(f"Lỗi hệ thống khi tải/upload: {repr(e)}")
                await status_msg.edit_text("❌ Lỗi: Không thể tải bài hát (File quá nặng, lỗi định dạng hoặc lỗi mạng).")
            
            finally:
                # Dọn dẹp rác hệ thống
                if 'info' in locals() and os.path.exists(info['temp_dir']):
                    shutil.rmtree(info['temp_dir'], ignore_errors=True)

# ==========================================
# 4. KHỞI ĐỘNG BOT
# ==========================================
async def main():
    logger.info("🚀 Bot tải nhạc đã khởi động (Đã fix lỗi Timeout/Upload)!")
    await bot.delete_webhook(drop_pending_updates=True)
    await dp.start_polling(bot)

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("🛑 Đã tắt bot an toàn.")
