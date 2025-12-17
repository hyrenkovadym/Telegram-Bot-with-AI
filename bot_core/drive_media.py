# bot_core/drive_media.py
"""
Робота з медіа на Google Drive + інтеграція з Google Sheets та AI-аналізом сервісних кейсів.

Сценарій:
- add_photo_to_media_case() викликається при кожному фото у flow service/cable.
- При першому фото створюється папка-кейс на спільному диску.
- Всі фото летять в цю папку.
- У user_data["media_case"] ми тримаємо інформацію про кейс, включно з кількома байтами фото
  (preview_images) для AI-аналізу.
- finalize_media_case() викликається, коли юзер пише "Готово":
  * логуємо кейс у Google Sheets (з URL-ом папки),
  * для flow == "service" запускаємо analyze_service_case(...) і надсилаємо клієнту
    попередній AI-аналіз (НЕ 100% діагноз, а лише ймовірна оцінка).
"""

import os
import io
import datetime
from typing import Tuple, List, Optional

from google.oauth2.service_account import Credentials
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseUpload

from .logging_setup import logger
from .gsheets import gsheet_append_row_with_media
from .service_ai import analyze_service_case

# ===== НАЛАШТУВАННЯ GOOGLE DRIVE =====

_DRIVE_SCOPES = ["https://www.googleapis.com/auth/drive"]
_DRIVE_SERVICE = None

# ID папок беремо з .env (DRIVE_MAIN_FOLDER_ID, DRIVE_SERVICE_FOLDER_ID тощо)
DRIVE_MAIN_FOLDER_ID = os.getenv("DRIVE_MAIN_FOLDER_ID", "").strip()
DRIVE_SERVICE_FOLDER_ID = os.getenv("DRIVE_SERVICE_FOLDER_ID", "").strip()
DRIVE_CABLE_FOLDER_ID = os.getenv("DRIVE_CABLE_FOLDER_ID", "").strip()
DRIVE_DEFAULT_FOLDER_ID = os.getenv("DRIVE_DEFAULT_FOLDER_ID", "").strip()

GOOGLE_SERVICE_ACCOUNT_FILE = os.getenv(
    "GOOGLE_SERVICE_ACCOUNT_FILE", "service-account.json"
).strip()


def _get_drive_service():
    """
    Ледачий ініціалізатор клієнта Google Drive.
    Використовує JSON сервісного акаунту (GOOGLE_SERVICE_ACCOUNT_FILE).
    """
    global _DRIVE_SERVICE
    if _DRIVE_SERVICE is not None:
        return _DRIVE_SERVICE

    if not os.path.exists(GOOGLE_SERVICE_ACCOUNT_FILE):
        raise FileNotFoundError(
            f"Google service account JSON not found: {GOOGLE_SERVICE_ACCOUNT_FILE}"
        )

    creds = Credentials.from_service_account_file(
        GOOGLE_SERVICE_ACCOUNT_FILE, scopes=_DRIVE_SCOPES
    )
    _DRIVE_SERVICE = build("drive", "v3", credentials=creds)
    logger.info("[DRIVE] client initialised OK.")
    return _DRIVE_SERVICE


def _parent_folder_for_flow(flow: Optional[str]) -> str:
    """
    Вибирає ID батьківської папки на спільному диску:
    - "service" → DRIVE_SERVICE_FOLDER_ID
    - "cable"   → DRIVE_CABLE_FOLDER_ID
    - None/інше → DRIVE_DEFAULT_FOLDER_ID, а якщо її немає — DRIVE_MAIN_FOLDER_ID
    """
    flow = (flow or "").strip().lower()
    if flow == "service" and DRIVE_SERVICE_FOLDER_ID:
        return DRIVE_SERVICE_FOLDER_ID
    if flow == "cable" and DRIVE_CABLE_FOLDER_ID:
        return DRIVE_CABLE_FOLDER_ID
    # дефолтна папка для інших сценаріїв
    return DRIVE_DEFAULT_FOLDER_ID or DRIVE_MAIN_FOLDER_ID


def create_case_folder(
    flow: Optional[str],
    phone: str = "",
    label: str = "",
) -> Tuple[str, str]:
    """
    Створює окрему папку-кейс на спільному диску у відповідній секції (service/cable/default).
    Повертає (folder_id, folder_url).
    """
    service = _get_drive_service()

    now = datetime.datetime.now().strftime("%Y-%m-%d_%H-%M")
    safe_phone = phone or "no-phone"
    safe_label = label or (flow or "default")
    name = f"{now}_{safe_phone}_{safe_label}"

    parent_id = _parent_folder_for_flow(flow)
    if not parent_id:
        raise RuntimeError("No parent folder ID configured for media cases.")

    file_metadata = {
        "name": name,
        "mimeType": "application/vnd.google-apps.folder",
        "parents": [parent_id],
    }

    folder = (
        service.files()
        .create(
            body=file_metadata,
            fields="id",
            supportsAllDrives=True,
        )
        .execute()
    )

    folder_id = folder["id"]
    folder_url = f"https://drive.google.com/drive/folders/{folder_id}"

    logger.info(
        "[DRIVE] Created case folder '%s' (flow=%s, id=%s)",
        name,
        flow or "default",
        folder_id,
    )
    return folder_id, folder_url


def upload_photo_bytes(folder_id: str, filename: str, data: bytes) -> str:
    """
    Завантажує один файл (фото) в задану папку на спільному диску.
    Повертає file_id.
    """
    service = _get_drive_service()

    file_metadata = {
        "name": filename,
        "parents": [folder_id],
    }

    media = MediaIoBaseUpload(io.BytesIO(data), mimetype="image/jpeg")

    file = (
        service.files()
        .create(
            body=file_metadata,
            media_body=media,
            fields="id",
            supportsAllDrives=True,
        )
        .execute()
    )

    file_id = file["id"]
    logger.info(
        "[DRIVE] Uploaded photo %s to folder %s (file_id=%s)",
        filename,
        folder_id,
        file_id,
    )
    return file_id


async def add_photo_to_media_case(update, context, photo_bytes: bytes, file_name: str):
    """
    Викликається з хендлера фото (handlers/media.py).

    Логіка:
    - по user_data["flow"] визначаємо сценарій (service/cable/…);
    - створюємо кейс-папку при першому фото;
    - вантажимо фото у Drive;
    - зберігаємо до 3 превʼю-байтів у user_data["media_case"]["preview_images"],
      щоб потім передати їх у AI при finalize_media_case().
    """
    ud = context.user_data
    flow = ud.get("flow")  # "service", "cable" або інше
    phone = ud.get("phone", "")
    case = ud.get("media_case")

    # якщо ще немає кейсу — створюємо нову папку
    if not case:
        folder_id, folder_url = create_case_folder(
            flow=flow,
            phone=phone,
            label=flow or "default",
        )
        case = {
            "flow": flow or "default",
            "folder_id": folder_id,
            "folder_url": folder_url,
            "files": [],
            # превʼю-фото, які надішлемо в AI (байти)
            "preview_images": [],
        }
        ud["media_case"] = case

    # вантажимо фото в уже створену папку
    file_id = upload_photo_bytes(case["folder_id"], file_name, photo_bytes)
    case["files"].append(file_id)

    # додаємо байти у preview_images (до 3 шт.)
    previews: List[bytes] = case.get("preview_images") or []
    if len(previews) < 3:
        previews.append(photo_bytes)
    case["preview_images"] = previews
    ud["media_case"] = case

    try:
        await update.message.reply_text(
            "Фото збережено ✅ Якщо є ще — надсилайте.\n"
            "Коли все надішлете — напишіть «Готово».",
        )
    except Exception as e:
        logger.error("[DRIVE] Failed to send confirmation to user: %s", e)


async def finalize_media_case(update, context, comment_text: str = ""):
    """
    Викликається, коли користувач пише «Готово» (або аналог) і є active media_case.

    Логіка:
    - пишемо рядок у головну таблицю лідів (FRENDT Leads) з URL папки;
    - якщо flow == "service", запускаємо AI-аналіз фото + коментаря;
    - очищаємо media_case в user_data;
    - відправляємо користувачу підтвердження + (за можливості) попередній аналіз.
    """
    ud = context.user_data
    case = ud.get("media_case")
    if not case:
        return  # немає активного медіа-кейсу

    user = update.effective_user
    full_name = ((user.first_name or "") + " " + (user.last_name or "")).strip()
    phone = ud.get("phone", "")
    folder_url = case.get("folder_url") or ""
    files_count = len(case.get("files", []))
    flow = (case.get("flow") or "default").lower()

    comment_text = (comment_text or "").strip()

    # ---- 1) Лог у FRENDT Leads (з URL папки) ----
    base_comment = f"[{flow.upper()} MEDIA] {files_count} фото."
    if comment_text:
        full_comment = f"{base_comment} Коментар клієнта: {comment_text}"
    else:
        full_comment = base_comment

    # ---- 1.1) AI-аналіз для кабельних кейсів (дописати в коментар для менеджера) ----
    if flow == "cable":
        try:
            preview_images = case.get("preview_images") or []
            if preview_images:
                from .cable_ai import classify_cable_or_connector_from_photo

                guess = await classify_cable_or_connector_from_photo(preview_images[0], flow="cable")
                if guess:
                    code = (guess.get("code") or "").strip()
                    name = (guess.get("name") or "").strip()

                    if code and name:
                        ai_line = f"[CABLE-AI] Ймовірно: {code} | {name}"
                    elif name:
                        ai_line = f"[CABLE-AI] Ймовірно: {name}"
                    else:
                        ai_line = ""

                    if ai_line:
                        full_comment = (full_comment + "\n" + ai_line).strip()
            else:
                logger.info("[CABLE-AI] Немає preview_images у кейсі – пропускаю AI-аналіз.")
        except Exception as e:
            logger.error("[CABLE-AI] Error during cable AI classify: %s", e)



    try:
        gsheet_append_row_with_media(
            full_name=full_name,
            phone=phone,
            comment=full_comment,
            media_url=folder_url,
        )
        logger.info(
            "[DRIVE] Logged media case to Sheets (%s, %s, flow=%s)",
            full_name,
            phone,
            flow,
        )
    except Exception as e:
        logger.error("[DRIVE] Failed to log media case to Sheets: %s", e)

    # ---- 2) AI-аналіз для сервісних кейсів ----
    ai_reply: Optional[str] = None
    if flow == "service":
        try:
            previews: List[bytes] = case.get("preview_images") or []
            if previews:
                ai_reply = analyze_service_case(
                    comment_text=comment_text,
                    images=previews,
                )
            else:
                logger.info(
                    "[SERVICE-AI] Немає preview_images у кейсі – пропускаю AI-аналіз."
                )
        except Exception as e:
            logger.error("[SERVICE-AI] Error during analyze_service_case: %s", e)
            ai_reply = None

    # ---- 3) Чистимо кейс ----
    ud.pop("media_case", None)

    # ---- 4) Відповідь користувачу ----
    if ai_reply:
        text = (
            "Фото збережено та передано менеджеру ✅\n\n"
            "Попередній аналіз від сервісного ШІ "
            "(це не фінальний діагноз, а лише ймовірна оцінка):\n\n"
            f"{ai_reply}"
        )
    else:
        text = (
            "Фото збережено та передано менеджеру ✅\n"
            "Якщо потрібно — можете ще щось дописати або поставити додаткові питання."
        )

    await update.message.reply_text(text)
