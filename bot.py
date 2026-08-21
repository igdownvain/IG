import os
import re
import logging
import tempfile
import shutil
from pathlib import Path
from typing import Optional, List, Dict, Any, Union
import telebot
from telebot.types import InputMediaPhoto, InputMediaVideo, Message
import requests
import yt_dlp

# ---------- InstagramDownloader (provided by user) ----------
DEFAULT_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept-Language": "en-US,en;q=0.9",
    "Referer": "https://www.instagram.com/",
}

class InstagramDownloader:
    """
    A library for downloading Instagram posts (images, reels, slideshows).

    Usage:
        downloader = InstagramDownloader(output_dir="./downloads")
        result = downloader.download("https://www.instagram.com/p/...")
    """

    def __init__(
        self,
        headers: Optional[Dict[str, str]] = None,
        session: Optional[requests.Session] = None,
        output_dir: Union[str, Path] = "."
    ):
        self.headers = headers or DEFAULT_HEADERS
        self.session = session or requests.Session()
        self.session.headers.update(self.headers)
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)

    def extract_id(self, url: str) -> Optional[str]:
        pattern = r'instagram\.com/(?:p|reel|tv)/([A-Za-z0-9_-]+)'
        match = re.search(pattern, url)
        return match.group(1) if match else None

    def get_info(self, url: str) -> Optional[Dict[str, Any]]:
        class ErrorCollectorLogger:
            def __init__(self):
                self.error_ids: List[str] = []
                self.all_messages: List[str] = []

            def debug(self, msg: str) -> None:
                self.all_messages.append(msg)

            def warning(self, msg: str) -> None:
                self.all_messages.append(msg)

            def error(self, msg: str) -> None:
                self.all_messages.append(msg)
                match = re.search(r'ERROR: \[Instagram\] ([A-Za-z0-9_-]+):', msg)
                if match:
                    self.error_ids.append(match.group(1))

        ydl_opts = {
            'quiet': True,
            'no_warnings': True,
            'ignoreerrors': True,
            'logger': ErrorCollectorLogger(),
        }

        downloader = yt_dlp.YoutubeDL(ydl_opts)
        try:
            info = downloader.extract_info(url, download=False)
            if info is None:
                return None

            failed_ids = downloader.params['logger'].error_ids

            result = {
                "id": info.get("id"),
                "title": info.get("title"),
                "name": info.get("uploader"),
                "username": info.get("channel"),
                "description": str(info.get("description") or ""),
                "upload_date": int(info.get("upload_date") or 0),
                "like": int(info.get("like_count") or 0),
                "comments": info.get("comments"),
                "items": None
            }

            if failed_ids:
                entries = info.get("entries")
                if entries is None:
                    entries = []
                for idx, item in enumerate(entries):
                    if item is None:
                        entries[idx] = failed_ids.pop(0) if failed_ids else None
                    else:
                        formats = item.get("formats")
                        if formats and isinstance(formats, list):
                            entries[idx] = formats[0].get("url")
                        else:
                            entries[idx] = None
                result["items"] = entries

            return result

        except Exception:
            return None

    def download_reel(
        self,
        url: str,
        output: Optional[Union[str, Path]] = None
    ) -> Union[Path, bool]:
        if output is None:
            post_id = self.extract_id(url)
            if not post_id:
                return False
            output = self.output_dir / f"{post_id}.mp4"
        else:
            output = Path(output)

        ydl_opts = {
            'quiet': True,
            'no_warnings': True,
            'ignoreerrors': True,
            'outtmpl': str(output),
        }
        try:
            downloader = yt_dlp.YoutubeDL(ydl_opts)
            downloader.download([url])
            return output if output.exists() else False
        except Exception:
            return False

    def download_image(
        self,
        post_id: str,
        save_path: Optional[Union[str, Path]] = None,
        index: int = 0
    ) -> Union[Path, bool]:
        if index == 0:
            url = f"https://www.instagram.com/p/{post_id}/media/?size=l"
        else:
            url = f"https://www.instagram.com/p/{post_id}/media/?size=l&img_index={index}"

        if save_path is None:
            suffix = "" if index == 0 else f"_{index}"
            save_path = self.output_dir / f"{post_id}{suffix}.png"
        else:
            save_path = Path(save_path)

        try:
            response = self.session.get(url, timeout=15)
            response.raise_for_status()
            save_path.write_bytes(response.content)
            return save_path
        except (requests.RequestException, IOError):
            return False

    def download_slides(self, info: Dict[str, Any]) -> List[Union[Path, bool]]:
        post_id = info.get("id")
        if not post_id or not info.get("items"):
            return []

        downloaded: List[Union[Path, bool]] = []
        for idx, item in enumerate(info["items"]):
            if not item:
                downloaded.append(False)
                continue

            if isinstance(item, str) and item.startswith("http"):
                filename = self.output_dir / f"{post_id}_{idx+1:02d}.mp4"
                result = self.download_reel(item, output=filename)
            else:
                filename = self.output_dir / f"{post_id}_{idx+1:02d}.png"
                result = self.download_image(str(item), save_path=filename, index=idx + 1)

            downloaded.append(result)

        return downloaded

    def download(self, url: str) -> Dict[str, Any]:
        try:
            post_id = self.extract_id(url)
            if not post_id:
                return {"success": False}

            info = self.get_info(url)

            if info is None:
                saved_path = self.download_image(post_id, save_path=self.output_dir / f"{post_id}.png")
                return {"success": True if saved_path else False, "type": "img", "path": [saved_path] if saved_path else [], "id": post_id}

            if info.get("items") is not None:
                paths = self.download_slides(info)
                return {"success": True if paths else False, "type": "slides", "info": info, "path": paths}

            else:
                saved_path = self.download_reel(url, output=self.output_dir / f"{post_id}.mp4")
                return {"success": True if saved_path else False, "type": "reel", "info": info, "path": [saved_path] if saved_path else []}

        except Exception:
            return {"success": False}


# ---------- Telegram Bot ----------
TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
if not TOKEN:
    raise ValueError("No TELEGRAM_BOT_TOKEN set. Please set the environment variable.")

bot = telebot.TeleBot(TOKEN, parse_mode=None)
logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO)

def is_instagram_url(text: str) -> bool:
    pattern = r'instagram\.com/(?:p|reel|tv)/[A-Za-z0-9_-]+'
    return bool(re.search(pattern, text))

@bot.message_handler(commands=['start', 'help'])
def send_welcome(message: Message):
    bot.reply_to(message, "Send me an Instagram post/reel URL, and I'll download it for you!")

@bot.message_handler(func=lambda m: is_instagram_url(m.text))
def handle_instagram_url(message: Message):
    # Extract the first URL from the message
    match = re.search(r'https?://(?:www\.)?instagram\.com/(?:p|reel|tv)/[A-Za-z0-9_-]+', message.text)
    if not match:
        bot.reply_to(message, "Invalid Instagram URL.")
        return
    url = match.group(0)

    bot.reply_to(message, "Downloading... Please wait.")

    temp_dir = Path(tempfile.mkdtemp())
    try:
        downloader = InstagramDownloader(output_dir=temp_dir)
        result = downloader.download(url)

        if not result.get('success', False):
            bot.reply_to(message, "Failed to download. The post might be private or invalid.")
            return

        paths = result.get('path', [])
        if not paths:
            bot.reply_to(message, "No files were downloaded.")
            return

        # Ensure paths is a list of Path objects
        if not isinstance(paths, list):
            paths = [paths]

        valid_paths = [p for p in paths if isinstance(p, Path) and p.exists()]

        if not valid_paths:
            bot.reply_to(message, "No valid files to send.")
            return

        # Send the files
        if len(valid_paths) == 1:
            send_single_file(bot, message.chat.id, valid_paths[0])
        else:
            send_media_group(bot, message.chat.id, valid_paths)

        bot.reply_to(message, "✅ Done!")

    except Exception as e:
        logger.exception("Error processing download")
        bot.reply_to(message, f"An error occurred: {e}")
    finally:
        # Clean up temporary directory
        shutil.rmtree(temp_dir, ignore_errors=True)

def send_single_file(bot, chat_id, file_path: Path):
    """Send a single photo, video, or document."""
    file_size = file_path.stat().st_size
    ext = file_path.suffix.lower()

    # Photo: up to 10 MB
    if ext in ['.jpg', '.jpeg', '.png', '.gif'] and file_size <= 10 * 1024 * 1024:
        with open(file_path, 'rb') as f:
            bot.send_photo(chat_id, f)
    # Video: up to 50 MB
    elif ext in ['.mp4', '.mov', '.avi'] and file_size <= 50 * 1024 * 1024:
        with open(file_path, 'rb') as f:
            bot.send_video(chat_id, f)
    # Otherwise send as document
    else:
        with open(file_path, 'rb') as f:
            bot.send_document(chat_id, f)

def send_media_group(bot, chat_id, file_paths: List[Path]):
    """Send multiple files as an album (max 10 per group)."""
    # Split into chunks of 10
    for i in range(0, len(file_paths), 10):
        chunk = file_paths[i:i+10]
        media = []
        for fp in chunk:
            ext = fp.suffix.lower()
            if ext in ['.jpg', '.jpeg', '.png', '.gif']:
                media.append(InputMediaPhoto(open(fp, 'rb')))
            elif ext in ['.mp4', '.mov', '.avi']:
                media.append(InputMediaVideo(open(fp, 'rb')))
            # Documents cannot be in media group; skip them and send individually later.
        if media:
            try:
                bot.send_media_group(chat_id, media)
            except Exception as e:
                logger.error(f"Failed to send media group: {e}")
                # Fallback: send each file individually
                for fp in chunk:
                    send_single_file(bot, chat_id, fp)

@bot.message_handler(func=lambda m: True)
def echo_all(message: Message):
    bot.reply_to(message, "Send me an Instagram URL.")

if __name__ == "__main__":
    bot.infinity_polling()