import asyncio
import logging
from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo
from typing import Optional

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.error import BadRequest
from telegram.ext import (
    Application,
    ApplicationBuilder,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    ContextTypes,
    filters,
)

from ledger.config import Settings, get_settings
from ledger.db import (
    get_engine,
    get_session_factory,
    init_db,
    prune_pending_actions,
    Checkin,
)
from ledger.llm import LLM, GroqLLM, OllamaLLM, AnthropicLLM, FallbackLLM
from ledger.reply import Reply
from ledger.router import handle_text, handle_callback
from ledger.checkin import create_checkin_card, create_morning_nudge
from ledger.projects import seed_projects_from_env
from ledger.util import local_today, to_local

logger = logging.getLogger(__name__)


def build_llm_chain(settings: Settings) -> LLM:
    providers = []
    for p_name in settings.LLM_PROVIDERS:
        name = p_name.lower().strip()
        if name == "groq" and settings.GROQ_API_KEY:
            providers.append(
                GroqLLM(
                    api_key=settings.GROQ_API_KEY,
                    model=settings.GROQ_MODEL,
                    json_mode=settings.GROQ_JSON_MODE,
                )
            )
        elif name == "ollama":
            providers.append(
                OllamaLLM(
                    url=settings.OLLAMA_URL,
                    model=settings.OLLAMA_MODEL,
                )
            )
        elif name == "anthropic" and settings.ANTHROPIC_API_KEY:
            providers.append(
                AnthropicLLM(
                    api_key=settings.ANTHROPIC_API_KEY,
                    model=settings.ANTHROPIC_MODEL,
                )
            )

    return FallbackLLM(providers)


def reply_to_markup(reply: Reply) -> Optional[InlineKeyboardMarkup]:
    if not reply.buttons:
        return None
    keyboard = []
    for row in reply.buttons:
        btn_row = [InlineKeyboardButton(text=label, callback_data=data) for label, data in row]
        keyboard.append(btn_row)
    return InlineKeyboardMarkup(keyboard)


async def send_reply(
    reply: Reply,
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    chat_id: Optional[int] = None,
) -> None:
    markup = reply_to_markup(reply)
    target_chat = chat_id or (update.effective_chat.id if update and update.effective_chat else None)

    if update and update.callback_query and reply.edit:
        # Edit message in place
        try:
            await update.callback_query.edit_message_text(
                text=reply.text,
                reply_markup=markup,
            )
        except BadRequest as e:
            if "Message is not modified" in str(e):
                pass
            else:
                logger.warning("Failed to edit message: %s", e)
    else:
        # Send new message
        if target_chat:
            await context.bot.send_message(
                chat_id=target_chat,
                text=reply.text,
                reply_markup=markup,
            )


def parse_time_str(time_str: str) -> tuple[int, int]:
    parts = time_str.strip().split(":")
    return int(parts[0]), int(parts[1])


async def run_catchup(
    session_factory,
    settings: Settings,
    bot,
) -> None:
    """Startup catch-up: if check-in time passed by < 4h today and no check-in sent today, send now."""
    if settings.ALLOWED_USER_ID <= 0:
        return

    tz = ZoneInfo(settings.LEDGER_TZ)
    now_local = datetime.now(tz)
    today = now_local.date()

    chk_h, chk_m = parse_time_str(settings.CHECKIN_TIME)
    checkin_local_dt = datetime(today.year, today.month, today.day, chk_h, chk_m, tzinfo=tz)

    diff_seconds = (now_local - checkin_local_dt).total_seconds()
    if 0 <= diff_seconds < 4 * 3600:
        with session_factory() as session:
            # Check if any checkin sent today in local time
            start_local = datetime.combine(today, time.min, tzinfo=tz)
            start_utc = start_local.astimezone(ZoneInfo("UTC")).replace(tzinfo=None)

            from sqlalchemy import select
            stmt = select(Checkin).where(Checkin.sent_at >= start_utc)
            existing = session.scalars(stmt).all()
            if not existing:
                reply = create_checkin_card(session, settings.ALLOWED_USER_ID, today, settings.LEDGER_TZ)
                if reply:
                    markup = reply_to_markup(reply)
                    await bot.send_message(
                        chat_id=settings.ALLOWED_USER_ID,
                        text=reply.text,
                        reply_markup=markup,
                    )


def build_application(
    settings: Settings,
    llm: Optional[LLM] = None,
    engine=None,
    session_factory=None,
) -> Application:
    if engine is None:
        engine = get_engine(settings.DATABASE_URL)
    init_db(engine)

    if session_factory is None:
        session_factory = get_session_factory(engine)

    active_llm = llm if llm is not None else build_llm_chain(settings)

    # Clean startup prune
    with session_factory() as session:
        prune_pending_actions(session, days=14)
        if settings.ALLOWED_USER_ID > 0 and settings.SEED_PROJECTS:
            seed_projects_from_env(session, settings.ALLOWED_USER_ID, settings.SEED_PROJECTS)

    app = ApplicationBuilder().token(settings.TELEGRAM_BOT_TOKEN or "dummy:token").build()

    if app.job_queue is None:
        raise RuntimeError("job_queue is not available. Please install python-telegram-bot[job-queue].")

    # Handlers
    async def on_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not update.message or not update.message.text:
            if update.message:
                await update.message.reply_text("Text only for now.")
            return

        user_id = update.effective_user.id if update.effective_user else 0
        text = update.message.text

        with session_factory() as session:
            replies = await handle_text(
                text=text,
                user_id=user_id,
                session=session,
                llm=active_llm,
                settings=settings,
            )

        for rep in replies:
            await send_reply(rep, update, context)

    async def on_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        query = update.callback_query
        if not query or not query.data:
            return
        await query.answer()

        user_id = update.effective_user.id if update.effective_user else 0
        data = query.data

        with session_factory() as session:
            replies = await handle_callback(
                data=data,
                user_id=user_id,
                session=session,
                settings=settings,
            )

        for rep in replies:
            await send_reply(rep, update, context)

    app.add_handler(CallbackQueryHandler(on_callback))
    app.add_handler(MessageHandler(filters.TEXT, on_text))

    # Jobs
    if settings.ALLOWED_USER_ID > 0:
        tz = ZoneInfo(settings.LEDGER_TZ)

        chk_h, chk_m = parse_time_str(settings.CHECKIN_TIME)
        checkin_time = time(chk_h, chk_m, tzinfo=tz)

        async def checkin_job(context: ContextTypes.DEFAULT_TYPE) -> None:
            try:
                today = local_today(settings.LEDGER_TZ)
                with session_factory() as session:
                    card = create_checkin_card(session, settings.ALLOWED_USER_ID, today, settings.LEDGER_TZ)
                    if card:
                        markup = reply_to_markup(card)
                        await context.bot.send_message(
                            chat_id=settings.ALLOWED_USER_ID,
                            text=card.text,
                            reply_markup=markup,
                        )
            except Exception as e:
                logger.error("Error in checkin_job: %s", e)

        app.job_queue.run_daily(checkin_job, time=checkin_time)

        nudge_h, nudge_m = parse_time_str(settings.MORNING_NUDGE_TIME)
        nudge_time = time(nudge_h, nudge_m, tzinfo=tz)

        async def nudge_job(context: ContextTypes.DEFAULT_TYPE) -> None:
            try:
                today = local_today(settings.LEDGER_TZ)
                with session_factory() as session:
                    nudge = create_morning_nudge(session, settings.ALLOWED_USER_ID, today, settings.LEDGER_TZ)
                    if nudge:
                        markup = reply_to_markup(nudge)
                        await context.bot.send_message(
                            chat_id=settings.ALLOWED_USER_ID,
                            text=nudge.text,
                            reply_markup=markup,
                        )
            except Exception as e:
                logger.error("Error in nudge_job: %s", e)

        app.job_queue.run_daily(nudge_job, time=nudge_time)

    # Store startup catchup on app post_init
    async def post_init(application: Application) -> None:
        try:
            await run_catchup(session_factory, settings, application.bot)
        except Exception as e:
            logger.error("Error running catchup: %s", e)

    app.post_init = post_init

    return app


def main() -> None:
    logging.basicConfig(
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
        level=logging.INFO,
    )
    settings = get_settings()
    app = build_application(settings)
    logger.info("Starting Work Ledger bot with long polling...")
    app.run_polling()


if __name__ == "__main__":
    main()
