from telegram import Update
from telegram.ext import ContextTypes

from ..utils import reload_blacklist, last_user_message
from ..config import MODEL_CHAT
from ..kb import load_kb_index, get_kb_chunk_count
from ..ui import bottom_keyboard


async def cmd_reload_blacklist(update: Update, context: ContextTypes.DEFAULT_TYPE):
    count = reload_blacklist()
    await update.message.reply_text(
        f"Готово. Оновлено чорний список: {count} номерів.",
        reply_markup=bottom_keyboard(context, tg_user_id=str(update.effective_user.id)),
    )


async def cmd_last(update: Update, context: ContextTypes.DEFAULT_TYPE):
    prev_user_msg = last_user_message(context)
    if prev_user_msg:
        text = "Останнє ваше повідомлення:\n\n" + prev_user_msg
    else:
        text = "Поки що немає попереднього повідомлення у моїй історії."
    await update.message.reply_text(
        text,
        reply_markup=bottom_keyboard(context, tg_user_id=str(update.effective_user.id)),
    )


async def cmd_model(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(f"Поточна модель GPT: {MODEL_CHAT}")


async def cmd_reload_kb(update: Update, context: ContextTypes.DEFAULT_TYPE):
    load_kb_index()
    n = get_kb_chunk_count()
    await update.message.reply_text(
        f"Базу знань оновлено. Фрагментів: {n}.",
        reply_markup=bottom_keyboard(context, tg_user_id=str(update.effective_user.id)),
    )
