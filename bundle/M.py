import os
import json
import time
import threading
import subprocess
import logging
import http.server
import socketserver
from datetime import datetime
from typing import Optional, Set, List, Dict

import requests

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger("multi_project_bot")

# ============================ تنظیمات ============================
# توکن ربات تلگرام
TELEGRAM_BOT_TOKEN = "8663835659:AAESnBy38O55Ypq6o_Y1G7PQE8VoOMdeZu8"

# کلید و مدل هوش مصنوعی (سرویس B.AI / BANK OF AI)
AI_API_KEY = "sk-t19anutsf3jd4xj35xjc7t9pzhccj0ed"
AI_MODEL = "qwen3.8-flash"

# ⚠️ توجه: طبق مستندات عمومی B.AI (docs.bankofai.io/llmservice)، سرویس این
# پلتفرم به‌صورت OpenAI-compatible (فرمت استاندارد /chat/completions) کار می‌کنه،
# و مدل‌های موجود در کاتالوگ عمومی‌شون qwen3.7-max و qwen3.8-max هستن — نه
# qwen3.8-flash؛ اگه این اسم مدل در حساب شما موجود نباشه، فراخوانی با خطای
# "model not found" مواجه می‌شه، پس نام دقیق مدل رو از پنل B.AI خودتون تایید کنید.
# آدرس دقیق endpoint هم در مستندات عمومی پیدا نشد؛ مقدار زیر رایج‌ترین الگوی
# این‌جور پلتفرم‌هاست ولی حتماً از داشبورد/مستندات B.AI خودتون تایید و جایگزین کنید:
AI_API_URL = "https://api.bankofai.io/v1/chat/completions"

# پوشه‌ای که همه پروژه‌های پایتون (هر کدوم در یک زیرپوشه) داخلش هستن.
# ساختار فعلی: symbol_projects/BTC/DC-Cr_BTC.py -> symbol_projects/BTC/BTC-Combined.txt
# و همینطور برای BNB, DOGE, ETH, SOL, XAUT, XRP
PROJECTS_BASE_DIR = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "symbol_projects"
)

PYTHON_EXECUTABLE = "python"  # در ویندوز دستور python3 معمولاً وجود نداره؛ از python استفاده می‌کنیم
SUBPROCESS_TIMEOUT = None  # هیچ‌وقت timeout نشه؛ هر چقدر طول بکشه صبر می‌کنه
AI_REQUEST_TIMEOUT = None  # درخواست به هوش مصنوعی هم هیچ‌وقت timeout نشه؛ هر چقدر طول بکشه صبر می‌کنه
TELEGRAM_MAX_CHARS = 4000

KNOWN_CHATS_FILE = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "known_chats.json"
)

_known_chats_lock = threading.Lock()


# ============================ کشف خودکار پروژه‌ها ============================
def discover_projects(base_dir: str) -> List[Dict]:
    """
    هر زیرپوشه‌ی base_dir رو به‌عنوان یک پروژه در نظر می‌گیره:
    - اولین فایل .py داخل اون پوشه رو به‌عنوان اسکریپت اجرایی انتخاب می‌کنه
    - طبق الگوی مشاهده‌شده در پروژه‌های شما، خروجی هر پروژه با نام
      "<NAME>-Combined.txt" داخل همون پوشه ساخته می‌شه (مسیر نسبی داخل خود اسکریپت)
    """
    projects: List[Dict] = []
    if not os.path.isdir(base_dir):
        logger.error(f"پوشه پروژه‌ها پیدا نشد: {base_dir}")
        return projects

    for entry in sorted(os.listdir(base_dir)):
        project_dir = os.path.join(base_dir, entry)
        if not os.path.isdir(project_dir):
            continue
        py_files = [f for f in os.listdir(project_dir) if f.endswith(".py")]
        if not py_files:
            continue
        script_name = py_files[0]
        output_txt = f"{entry}-Combined.txt"
        projects.append({
            "name": entry,
            "dir": project_dir,
            "script": script_name,
            "output_txt": output_txt,
        })

    if not projects:
        logger.warning(f"هیچ پروژه‌ای در {base_dir} پیدا نشد.")
    else:
        names = ", ".join(p["name"] for p in projects)
        logger.info(f"{len(projects)} پروژه شناسایی شد: {names}")

    return projects


PROJECTS: List[Dict] = discover_projects(PROJECTS_BASE_DIR)


# ============================ مدیریت چت‌های شناخته‌شده ============================
def load_known_chats() -> Set[str]:
    if os.path.exists(KNOWN_CHATS_FILE):
        try:
            with open(KNOWN_CHATS_FILE, "r", encoding="utf-8") as f:
                return set(json.load(f))
        except Exception:
            return set()
    return set()


def save_known_chats(chats: Set[str]) -> None:
    try:
        with open(KNOWN_CHATS_FILE, "w", encoding="utf-8") as f:
            json.dump(list(chats), f)
    except Exception as e:
        logger.error(f"خطا در ذخیره لیست چت‌ها: {e}")


known_chats: Set[str] = load_known_chats()


def add_known_chat(chat_id) -> None:
    chat_id = str(chat_id)
    with _known_chats_lock:
        if chat_id not in known_chats:
            known_chats.add(chat_id)
            save_known_chats(known_chats)
            logger.info(f"چت جدید ثبت شد و به لیست گیرنده‌ها اضافه شد: {chat_id}")


def poll_telegram_updates() -> None:
    """
    به‌صورت مداوم (long polling) پیام‌های جدید بات رو چک می‌کنه.
    هر کاربر یا گروهی که پیام بده به لیست گیرنده‌ها اضافه می‌شه.

    نکته: برای اینکه بات پیام‌های عادی گروه (نه فقط کامندها) رو ببینه،
    باید Privacy Mode بات رو در BotFather با دستور /setprivacy خاموش کنید.
    """
    offset = None
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/getUpdates"
    while True:
        try:
            params = {"timeout": 30}
            if offset is not None:
                params["offset"] = offset
            resp = requests.get(url, params=params, timeout=40)
            resp.raise_for_status()
            data = resp.json()
            for update in data.get("result", []):
                offset = update["update_id"] + 1
                message = update.get("message") or update.get("channel_post")
                if message and "chat" in message:
                    add_known_chat(message["chat"]["id"])
        except Exception as e:
            logger.error(f"خطا در دریافت آپدیت‌های تلگرام: {e}")
            time.sleep(5)


# ============================ ارسال پیام تلگرام ============================
def send_telegram_message(text: str, chat_id: str) -> None:
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    chunks = [text[i:i + TELEGRAM_MAX_CHARS] for i in range(0, len(text), TELEGRAM_MAX_CHARS)] or [""]
    for chunk in chunks:
        try:
            resp = requests.post(url, data={"chat_id": chat_id, "text": chunk}, timeout=30)
            resp.raise_for_status()
        except Exception as e:
            logger.error(f"خطا در ارسال پیام به چت {chat_id}: {e}")


def broadcast_telegram_message(text: str) -> None:
    with _known_chats_lock:
        chats = list(known_chats)
    if not chats:
        logger.warning("هنوز هیچ چتی با بات تعامل نداشته؛ پیامی ارسال نشد.")
        return
    for chat_id in chats:
        send_telegram_message(text, chat_id)


# ============================ اجرای پروژه‌های پایتون ============================
def run_python_project(script_name: str, project_dir: str, output_path: str) -> Optional[str]:
    """اسکریپت رو با cwd روی پوشه خودش اجرا می‌کنه تا فایل txt خروجی (که با
    مسیر نسبی نوشته می‌شه) داخل همون پوشه ساخته بشه.

    برای اینکه هیچ‌وقت یه فایل txt قدیمی/باقی‌مونده از اجرای قبلی به اشتباه
    خونده نشه و به هوش مصنوعی داده نشه، قبل از اجرای اسکریپت، فایل خروجی
    قبلی (اگه وجود داشته باشه) حذف می‌شه. اینطوری اگه اسکریپت به هر دلیلی
    فایل رو دوباره نسازه، این تابع خطا برمی‌گردونه به‌جای اینکه بی‌سروصدا
    محتوای قدیمی رو معتبر جا بزنه.

    خروجی: None یعنی موفق بود و فایل txt همین الان (تازه) ساخته شده.
    هر مقدار دیگه (رشته) یعنی خطا رخ داده و همون رشته توضیح/خلاصه خطاست."""
    try:
        if os.path.exists(output_path):
            os.remove(output_path)
    except Exception as e:
        logger.error(f"خطا در حذف فایل قدیمی {output_path}: {e}")

    try:
        result = subprocess.run(
            [PYTHON_EXECUTABLE, script_name],
            cwd=project_dir,
            capture_output=True,
            text=True,
            timeout=SUBPROCESS_TIMEOUT,
        )
        if result.returncode != 0:
            error_text = (result.stderr or result.stdout or "بدون پیام خطا").strip()
            logger.error(f"اسکریپت {script_name} با خطا تموم شد:\n{error_text}")
            return error_text

        if not os.path.exists(output_path):
            msg = "اسکریپت با موفقیت اجرا شد ولی فایل txt تازه‌ای نساخت."
            logger.error(f"{script_name}: {msg}")
            return msg

        return None
    except subprocess.TimeoutExpired:
        msg = "اجرای اسکریپت بیش از حد طول کشید (timeout)."
        logger.error(f"{script_name}: {msg}")
        return msg
    except Exception as e:
        logger.error(f"خطا در اجرای {script_name}: {e}")
        return str(e)


def read_output_file(path: str) -> Optional[str]:
    if not os.path.exists(path):
        logger.error(f"فایل خروجی پیدا نشد: {path}")
        return None
    try:
        with open(path, "r", encoding="utf-8") as f:
            return f.read().strip()
    except Exception as e:
        logger.error(f"خطا در خواندن فایل {path}: {e}")
        return None


# ============================ تماس با هوش مصنوعی ============================
def ask_ai(prompt_text: str) -> Optional[str]:
    """ارسال متن فایل txt به API هوش مصنوعی (فرمت OpenAI-compatible)."""
    headers = {
        "Authorization": f"Bearer {AI_API_KEY}",
        "Content-Type": "application/json",
    }
    payload = {
        "model": AI_MODEL,
        "messages": [{"role": "user", "content": prompt_text}],
    }
    try:
        resp = requests.post(AI_API_URL, headers=headers, json=payload, timeout=AI_REQUEST_TIMEOUT)
        resp.raise_for_status()
        data = resp.json()
        return data["choices"][0]["message"]["content"].strip()
    except Exception as e:
        logger.error(f"خطا در تماس با API هوش مصنوعی: {e}")
        return None


# ============================ پردازش یک پروژه کامل ============================
def process_single_project(project: Dict) -> None:
    name = project["name"]
    project_dir = project["dir"]
    logger.info(f"شروع اجرای {name} ...")

    # فایل txt همیشه همین الان (تازه، در همین اجرا) ساخته می‌شه؛ چون
    # run_python_project قبل از اجرای اسکریپت نسخه‌ی قبلی رو حذف می‌کنه و
    # بعد از اجرا هم وجودش رو تایید می‌کنه، پس محتوایی که چند خط پایین‌تر
    # خونده و مستقیماً به هوش مصنوعی داده می‌شه، همیشه آپدیته و مال همین
    # دوره‌ست؛ نه یه فایل قدیمی از اجرای قبلی.
    output_path = os.path.join(project_dir, project["output_txt"])
    error_text = run_python_project(project["script"], project_dir, output_path)
    if error_text is not None:
        # فقط ۶۰۰ کاراکتر آخر خطا رو می‌فرستیم تا پیام تلگرام شلوغ نشه
        short_error = error_text[-600:]
        broadcast_telegram_message(
            f"⚠️ خطا در اجرای اسکریپت {name} ({project['script']}):\n\n{short_error}"
        )
        return

    prompt_text = read_output_file(output_path)
    if not prompt_text:
        broadcast_telegram_message(f"⚠️ فایل خروجی {name} خالی یا پیدا نشد.")
        return

    # همین‌جا، بلافاصله بعد از خوندن فایل تازه‌ساخته‌شده، به هوش مصنوعی داده می‌شه
    ai_response = ask_ai(prompt_text)
    if ai_response is None:
        broadcast_telegram_message(f"⚠️ خطا در دریافت پاسخ هوش مصنوعی برای {name}")
        return

    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    message = f"✅ {name}\n🕒 {timestamp}\n\n{ai_response}"
    broadcast_telegram_message(message)
    logger.info(f"پایان اجرای {name} و ارسال پیام به همه چت‌ها.")


def run_all_projects_once() -> None:
    """اجرای پشت‌سرهم همه پروژه‌های شناسایی‌شده، بدون هیچ تاخیری بین‌شون."""
    if not PROJECTS:
        logger.error("هیچ پروژه‌ای برای اجرا پیدا نشد؛ مسیر symbol_projects رو چک کنید.")
        return
    for project in PROJECTS:
        process_single_project(project)


# ============================ حلقه اصلی ============================
def main_loop() -> None:
    """اجرای مداوم و بدون توقف: از اولین پروژه تا آخرین پروژه، و به محض
    تمام شدن آخرین پروژه، بلافاصله (بدون هیچ صبر یا تاخیری) دوباره از اول
    شروع می‌شه. حلقه هیچ‌وقت خودش متوقف نمی‌شه."""
    while True:
        logger.info("=== شروع یک دور جدید (از اولین تا آخرین پروژه) ===")
        run_all_projects_once()
        logger.info("=== دور تمام شد؛ بلافاصله دور بعدی شروع می‌شه ===")


# ============================ وب‌سرور سلامت (برای Render.com) ============================
def start_health_server() -> None:
    """
    یک وب‌سرور خیلی ساده بالا میاره که فقط به درخواست‌های GET جواب "OK" می‌ده.
    دلیل وجودش:
    1) پلن رایگان Render.com فقط از نوع «Web Service» پشتیبانی می‌کنه (نه Worker)،
       و Web Service باید حتماً روی یک پورت HTTP گوش بده وگرنه دیپلوی fail می‌شه.
    2) از همین آدرس برای «پینگ دوره‌ای» (که در ادامه توضیح داده می‌شه) استفاده
       می‌کنیم تا Render سرویس رو به‌خاطر بی‌کاری نخوابونه.
    منطق اصلی بات (اجرای پروژه‌ها + تلگرام) در تردهای جداگانه در پس‌زمینه
    اجرا می‌شه و کاری به این سرور نداره.
    """
    port = int(os.environ.get("PORT", 10000))

    class HealthHandler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200)
            self.send_header("Content-type", "text/plain; charset=utf-8")
            self.end_headers()
            self.wfile.write("Bot is running.".encode("utf-8"))

        def log_message(self, format, *args):
            # جلوگیری از شلوغ شدن لاگ‌ها با هر پینگ سلامت
            return

    with socketserver.ThreadingTCPServer(("0.0.0.0", port), HealthHandler) as httpd:
        logger.info(f"وب‌سرور سلامت روی پورت {port} بالا اومد.")
        httpd.serve_forever()


def main() -> None:
    threading.Thread(target=poll_telegram_updates, daemon=True).start()
    threading.Thread(target=main_loop, daemon=True).start()
    logger.info("بات راه‌اندازی شد. منتظر پیام از کاربران/گروه‌ها برای ثبت چت هستیم...")
    # این تابع بلاک می‌کنه و ترد اصلی رو زنده نگه می‌داره؛ روی Render لازمه
    # سرویس یک پورت HTTP باز کنه تا هم دیپلوی موفق بشه و هم پینگ‌های
    # بیرونی (برای جلوگیری از خواب رفتن سرویس رایگان) جواب بگیرن.
    start_health_server()


if __name__ == "__main__":
    main()
