import os
import re
import shutil
import asyncio
import tempfile
import logging
from dotenv import load_dotenv
from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import CommandStart, Command
from aiogram.types import FSInputFile
from aiogram.exceptions import TelegramAPIError
import yt_dlp
import aiohttp

# ==========================================
# [SECURITY] 1. CẤU HÌNH & BẢO MẬT
# ==========================================
# Không hardcode token. Sử dụng biến môi trường (Environment Variables)
load_dotenv()
BOT_TOKEN = os.environ.get("BOT_TOKEN")
ADMIN_ID = os.environ.get("ADMIN_ID")

if not BOT_TOKEN:
    raise ValueError("❌ BẢO MẬT: Không tìm thấy BOT_TOKEN trong biến môi trường!")

# Cấu hình logging để theo dõi lỗi
logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()

# [EFFICIENCY] Giới hạn số lượng người tải cùng lúc để tránh sập RAM server
semaphore = asyncio.Semaphore(5)

# ==========================================
# [EFFICIENCY & ROBUSTNESS] 2. XỬ LÝ TẢI NHẠC
# ==========================================
async def resolve_sc(url: str, session: aiohttp.ClientSession) -> str:
    """Xử lý link rút gọn on.soundcloud.com để tránh yt_dlp không nhận diện được"""
    if "on.soundcloud.com" in url:
        try:
            async with session.get(url, allow_redirects=True, timeout=5) as r:
                return str(r.url) if "soundcloud.com" in str(r.url) else url
        except Exception as e:
            logger.warning(f"Lỗi resolve link: {e}")
    return url

def _download_track_sync(url: str) -> dict:
    """Hàm tải nhạc chạy đồng bộ (sẽ được gọi trong thread riêng)"""
    # Tạo thư mục tạm thời độc lập cho mỗi bài hát
    temp_dir = tempfile.mkdtemp() 
    
    opts = {
        'format': 'bestaudio/best',
        'outtmpl': os.path.join(temp_dir, '%(title)s.%(ext)s'),
        'quiet': True,
        'no_warnings': True,
        'noplaylist': True,
        'max_filesize': 45000000 # Chặn file >45MB (Telegram giới hạn 50MB cho bot)
    }
    
    with yt_dlp.YoutubeDL(opts) as ydl:
        info = ydl.extract_info(url, download=True)
        filepath = ydl.prepare_filename(info)
        
        # Nếu đuôi file không phổ biến, đổi thành .mp3 để Telegram dễ đọc (Yêu cầu FFMPEG)
        if not filepath.endswith(('.mp3', '.m4a', '.wav')):
            new_filepath = filepath.rsplit('.', 1)[0] + '.mp3'
            os.rename(filepath, new_filepath)
            filepath = new_filepath

        return {
            'filepath': filepath,
            'title': info.get('title', 'Unknown Track'),
            'uploader': info.get('uploader', 'Unknown Artist'),
            'temp_dir': temp_dir # Lưu lại đường dẫn để xóa sau khi upload
        }

# ==========================================
# [CLEAN CODE] 3. HANDLERS GIAO TIẾP
# ==========================================
@dp.message(CommandStart())
async def cmd_start(message: types.Message):
    await message.reply("🎧 Gửi link SoundCloud (hoặc on.soundcloud.com) vào đây, tôi sẽ tải về ngay!")

@dp.message(F.text.contains("soundcloud.com"))
async def handle_download(message: types.Message):
    url_match = re.search(r'(https?://[^\s]+)', message.text)
    if not url_match: return
    
    raw_url = url_match.group(1)
    status_msg = await message.reply("⏳ `Đang xử lý và tải bài hát...`", parse_mode="Markdown")

    async with semaphore:
        async with aiohttp.ClientSession() as session:
            try:
                # 1. Lấy link gốc
                url = await resolve_sc(raw_url, session)
                
                # 2. Đẩy việc tải nặng sang Thread khác để bot không bị treo (Efficiency)
                info = await asyncio.to_thread(_download_track_sync, url)
                
                # 3. Upload lên Telegram
                await status_msg.edit_text("⬆️ `Đang tải lên Telegram...`", parse_mode="Markdown")
                audio_file = FSInputFile(info['filepath'])
                caption = f"🎧 **{info['title']}**\n👤 {info['uploader']}"
                
                await message.reply_audio(
                    audio=audio_file,
                    caption=caption,
                    performer=info['uploader'],
                    title=info['title'],
                    parse_mode="Markdown"
                )
                await status_msg.delete()

            except yt_dlp.utils.DownloadError as e:
                logger.error(f"Lỗi YT-DLP: {e}")
                await status_msg.edit_text("❌ Lỗi: Không thể tải bài hát (Có thể do bản quyền hoặc lỗi mạng).")
            except Exception as e:
                logger.error(f"Lỗi hệ thống: {e}")
                await status_msg.edit_text("❌ Lỗi hệ thống khi tải file.")
            
            finally:
                # [ROBUSTNESS] Bất kể thành công hay thất bại, LUÔN xóa file rác để giải phóng ổ cứng
                if 'info' in locals() and os.path.exists(info['temp_dir']):
                    shutil.rmtree(info['temp_dir'], ignore_errors=True)

# ==========================================
# 4. KHỞI ĐỘNG BOT
# ==========================================
async def main():
    logger.info("🚀 Bot tải nhạc đã khởi động!")
    await bot.delete_webhook(drop_pending_updates=True)
    await dp.start_polling(bot)

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("🛑 Đã tắt bot an toàn.")
