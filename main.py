import os
import re
import asyncio
import aiohttp
import yt_dlp
import logging
from dotenv import load_dotenv
from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import CommandStart, Command
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.exceptions import TelegramAPIError

# ==========================================
# 1. SECURITY & CẤU HÌNH
# ==========================================
logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)

load_dotenv()
BOT_TOKEN = os.environ.get("BOT_TOKEN")
ADMIN_ID = os.environ.get("ADMIN_ID")
LOG_CHANNEL_ID = os.environ.get("LOG_CHANNEL_ID") # Thêm ID kênh để nhận log

if not all([BOT_TOKEN, ADMIN_ID]):
    raise ValueError("❌ LỖI: Thiếu BOT_TOKEN hoặc ADMIN_ID trong file .env!")

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()
semaphore = asyncio.Semaphore(10)
USERS_FILE = "users.txt"

# ==========================================
# 2. EFFICIENCY (LOGIC XỬ LÝ & LƯU TRỮ)
# ==========================================
def get_all_users() -> set:
    """Lấy danh sách người dùng. Dùng set để tìm kiếm nhanh (O(1))."""
    if not os.path.exists(USERS_FILE): return set()
    with open(USERS_FILE, "r") as f:
        return set(line.strip() for line in f if line.strip())

def save_user(user_id: int):
    """Lưu ID nếu người dùng chưa tồn tại trong danh sách."""
    user_str = str(user_id)
    if user_str not in get_all_users():
        with open(USERS_FILE, "a") as f: f.write(f"{user_str}\n")

async def send_channel_log(text: str):
    """Gửi log hoạt động về kênh Telegram."""
    if not LOG_CHANNEL_ID: return
    try:
        await bot.send_message(chat_id=LOG_CHANNEL_ID, text=text, parse_mode="Markdown")
    except Exception as e:
        logger.warning(f"Không thể gửi log vào kênh (Kiểm tra lại ID hoặc quyền của bot): {e}")

async def resolve_sc(url: str, session: aiohttp.ClientSession) -> str:
    """Resolve link rút gọn SoundCloud."""
    if "on.soundcloud.com" in url:
        try:
            async with session.get(url, allow_redirects=True, timeout=5) as r:
                return str(r.url) if "soundcloud.com" in str(r.url) else url
        except Exception as e:
            logger.error(f"Lỗi resolve link {url}: {e}")
    return url

def _extract_info(url: str) -> dict:
    opts = {'format': 'bestaudio', 'quiet': True, 'noplaylist': True, 'simulate': True}
    with yt_dlp.YoutubeDL(opts) as y: 
        return y.extract_info(url, download=False)

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
            await asyncio.sleep(0.05) # Tránh Flood Limit
        except TelegramAPIError:
            fail += 1

    await status.edit_text(f"✅ **Hoàn tất!**\n🟢 Thành công: `{success}`\n🔴 Thất bại: `{fail}`", parse_mode="Markdown")

@dp.message(CommandStart())
async def cmd_start(message: types.Message):
    user = message.from_user
    save_user(user.id)
    
    # Bắn log qua kênh
    await send_channel_log(f"🟢 **#START**\n👤 User: [{user.full_name}](tg://user?id={user.id}) (`{user.id}`)")
    
    await message.reply("🎧 Gửi link SoundCloud (hoặc `on.soundcloud.com`) để lấy link Stream nhạc gốc ngay lập tức!", parse_mode="Markdown")

@dp.message(F.text)
async def handle_msg(message: types.Message):
    if "soundcloud.com" not in message.text: return
    
    url_match = re.search(r'(https?://[^\s]+)', message.text)
    if not url_match: return
    
    user = message.from_user
    raw_url = url_match.group(1)
    save_user(user.id)
    
    # Bắn log khi có người thả link vào
    await send_channel_log(f"🔗 **#LINK_REQUEST**\n👤 User: [{user.full_name}](tg://user?id={user.id})\n🎵 Link: {raw_url}")
    
    status_msg = await message.reply("⏳ `Đang bóc tách...`", parse_mode="Markdown")

    async with semaphore:
        async with aiohttp.ClientSession() as session:
            try:
                url = await resolve_sc(raw_url, session)
                info = await asyncio.to_thread(_extract_info, url)
                
                direct_url = info.get('url')
                if not direct_url:
                    return await status_msg.edit_text("❌ Không thể trích xuất link Stream!")

                caption = (
                    f"🎧 **{info.get('title', 'Unknown Track')}**\n"
                    f"👤 Nghệ sĩ: `{info.get('uploader', 'Unknown')}`\n"
                    f"⏱ Thời lượng: `{info.get('duration_string', 'N/A')}`"
                )
                kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="▶️ STREAM NGAY", url=direct_url)]])
                
                if info.get('thumbnail'):
                    await message.reply_photo(photo=info.get('thumbnail'), caption=caption, reply_markup=kb, parse_mode="Markdown")
                    await status_msg.delete()
                else:
                    await status_msg.edit_text(caption, reply_markup=kb, parse_mode="Markdown")

            except Exception as e:
                logger.error(f"Lỗi {raw_url}: {e}")
                await status_msg.edit_text("❌ **Lỗi:** Bài hát không tồn tại, bị giới hạn khu vực hoặc link hỏng!", parse_mode="Markdown")

# ==========================================
# 4. CHẠY BOT
# ==========================================
async def main():
    logger.info("🚀 Bot SoundCloud khởi động!")
    try:
        await bot.send_message(ADMIN_ID, "🟢 **Hệ thống Bot đã sẵn sàng!**", parse_mode="Markdown")
    except: pass

    await bot.delete_webhook(drop_pending_updates=True)
    await dp.start_polling(bot)

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("🛑 Bot đã tắt.")
