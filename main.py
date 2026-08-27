import os
import json
import logging
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes

# ---------- Logging setup ----------
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# ---------- Simple JSON Storage ----------
class Storage:
    def __init__(self, file_path='data.json'):
        self.file_path = file_path
        self.data = {}
        self.load()

    def load(self):
        try:
            with open(self.file_path, 'r') as f:
                self.data = json.load(f)
        except FileNotFoundError:
            self.data = {}

    def save(self):
        with open(self.file_path, 'w') as f:
            json.dump(self.data, f)

    def get_user_default_chat(self, user_id):
        return self.data.get(str(user_id), {}).get('default_chat')

    def set_user_default_chat(self, user_id, chat_id):
        self.data.setdefault(str(user_id), {})['default_chat'] = chat_id
        self.save()

    def get_user_topic(self, user_id, chat_id):
        return self.data.get(str(user_id), {}).get('topics', {}).get(str(chat_id))

    def set_user_topic(self, user_id, chat_id, thread_id):
        self.data.setdefault(str(user_id), {}).setdefault('topics', {})[str(chat_id)] = thread_id
        self.save()

    def clear_user_default_chat(self, user_id):
        if str(user_id) in self.data:
            self.data[str(user_id)].pop('default_chat', None)
            self.save()

storage = Storage()

# ---------- Helper: Admin Check with Logging ----------
async def is_admin(bot, chat_id, user_id):
    try:
        member = await bot.get_chat_member(chat_id, user_id)
        return member.status in ['administrator', 'creator']
    except Exception as e:
        logger.error(f"Admin check failed for chat {chat_id}, user {user_id}: {e}")
        return False

# ---------- Command Handlers ----------
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "I'm Incognito Poster Bot.\n"
        "Forward any message from a group to me, and I'll repost it.\n"
        "Use /help for details."
    )

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "📌 *How to use*\n"
        "1. Add me to a group (as admin).\n"
        "2. *Forward* any message from that group to me in private – I'll repost it.\n"
        "3. To set a default *topic*, go to the group, enter the topic, and send `/settopic`.\n"
        "4. You can also *type* any message to me in private – I'll post it to your default group.\n"
        "5. Reply to a message in a group with `/post` to repost it (works even if forwarding is disabled).\n"
        "6. Reply to a message in a group with `/sendto <chat_id>` to cross-post it to another group.\n"
        "7. Check your settings with `/status`.",
        parse_mode='Markdown'
    )

async def settopic(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat = update.effective_chat
    if chat.type not in ['group', 'supergroup']:
        await update.message.reply_text("This command works only in groups.")
        return

    user = update.effective_user
    if not await is_admin(context.bot, chat.id, user.id):
        await update.message.reply_text("You must be an admin to set a topic.")
        return

    thread_id = update.effective_message.message_thread_id
    if thread_id is None:
        await update.message.reply_text("Topic set to *General* (no specific topic).", parse_mode='Markdown')
    else:
        await update.message.reply_text(f"Topic set to current topic (ID: `{thread_id}`).", parse_mode='Markdown')

    storage.set_user_topic(user.id, chat.id, thread_id)
    storage.set_user_default_chat(user.id, chat.id)

async def status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    chat_id = storage.get_user_default_chat(user.id)
    if not chat_id:
        await update.message.reply_text("No default group set. Forward a message from a group to set it.")
        return

    topic = storage.get_user_topic(user.id, chat_id)
    topic_str = f"Topic ID: `{topic}`" if topic is not None else "General (no topic)"
    await update.message.reply_text(f"Default group: `{chat_id}`\n{topic_str}", parse_mode='Markdown')

# ---------- /post command – repost inside same group ----------
async def post_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Reply to a message with /post to repost it as the bot in the same group."""
    message = update.effective_message
    user = update.effective_user
    chat = update.effective_chat

    if chat.type not in ['group', 'supergroup']:
        await message.reply_text("This command works only in groups.")
        return

    if not await is_admin(context.bot, chat.id, user.id):
        await message.reply_text("You must be an admin to use this.")
        return

    if not message.reply_to_message:
        await message.reply_text("Reply to a message with /post to repost it.")
        return

    target_msg = message.reply_to_message
    thread_id = storage.get_user_topic(user.id, chat.id)

    try:
        await context.bot.copy_message(
            chat_id=chat.id,
            from_chat_id=chat.id,
            message_id=target_msg.message_id,
            message_thread_id=thread_id
        )
        await message.reply_text("✅ Message reposted successfully.")
    except Exception as e:
        await message.reply_text(f"❌ Failed to repost: {e}")

# ---------- /sendto command – cross-post to another group ----------
async def sendto_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Reply to a message with /sendto <target_chat_id> to copy it to another group."""
    message = update.effective_message
    user = update.effective_user
    chat = update.effective_chat

    if chat.type not in ['group', 'supergroup']:
        await message.reply_text("This command works only in groups.")
        return

    if not await is_admin(context.bot, chat.id, user.id):
        await message.reply_text("You must be an admin to use this.")
        return

    if not message.reply_to_message:
        await message.reply_text("Reply to a message with /sendto <target_chat_id> to copy it to another group.")
        return

    args = context.args
    if not args:
        await message.reply_text("Usage: /sendto <target_chat_id>\nExample: /sendto -100123456789")
        return

    try:
        target_chat_id = int(args[0])
    except ValueError:
        await message.reply_text("Invalid chat ID. Make sure it's a number (e.g., -100123456789).")
        return

    target_msg = message.reply_to_message

    try:
        await context.bot.copy_message(
            chat_id=target_chat_id,
            from_chat_id=chat.id,
            message_id=target_msg.message_id,
            message_thread_id=None
        )
        await message.reply_text(f"✅ Message copied to chat ID `{target_chat_id}` successfully.")
    except Exception as e:
        await message.reply_text(f"❌ Failed to copy: {e}")

# ---------- Main Message Handler (private only) ----------
async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # This only runs in private chats due to the filter in main()
    user = update.effective_user
    message = update.effective_message

    forward_from_chat = getattr(message, 'forward_from_chat', None)
    forward_from_message_id = getattr(message, 'forward_from_message_id', None)

    if forward_from_chat:
        source_chat = forward_from_chat
        if source_chat.type not in ['group', 'supergroup']:
            await message.reply_text("I can only repost from groups.")
            return
        chat_id = source_chat.id
        if not await is_admin(context.bot, chat_id, user.id):
            await message.reply_text("You are not an admin of that group.")
            return
        from_chat_id = source_chat.id
        from_message_id = forward_from_message_id
        storage.set_user_default_chat(user.id, chat_id)
    else:
        chat_id = storage.get_user_default_chat(user.id)
        if not chat_id:
            await message.reply_text(
                "No default group set. Please forward a message from a group first, "
                "or use /settopic in a group."
            )
            return
        if not await is_admin(context.bot, chat_id, user.id):
            storage.clear_user_default_chat(user.id)
            await message.reply_text(
                "You are no longer an admin of the default group. Please set a new group."
            )
            return
        from_chat_id = message.chat.id
        from_message_id = message.message_id

    thread_id = storage.get_user_topic(user.id, chat_id)

    try:
        await context.bot.copy_message(
            chat_id=chat_id,
            from_chat_id=from_chat_id,
            message_id=from_message_id,
            message_thread_id=thread_id
        )
        await message.reply_text("✅ Message posted successfully.")
    except Exception as e:
        await message.reply_text(f"❌ Failed to post: {e}")

# ---------- Main ----------
def main():
    token = os.environ.get('BOT_TOKEN')
    if not token:
        raise ValueError("BOT_TOKEN environment variable not set.")

    app = Application.builder().token(token).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_command))
    app.add_handler(CommandHandler("settopic", settopic))
    app.add_handler(CommandHandler("status", status))
    app.add_handler(CommandHandler("post", post_command))
    app.add_handler(CommandHandler("sendto", sendto_command))

    # 👇 Fixed: Use ChatType.PRIVATE instead of PRIVATE
    app.add_handler(MessageHandler(filters.ChatType.PRIVATE & ~filters.COMMAND, handle_message))

    webhook_url = os.environ.get('WEBHOOK_URL')
    port = int(os.environ.get('PORT', 8443))

    if webhook_url:
        logger.info(f"Starting webhook on port {port} with URL {webhook_url}/webhook")
        app.run_webhook(
            listen='0.0.0.0',
            port=port,
            url_path='webhook',
            webhook_url=webhook_url + '/webhook'
        )
    else:
        logger.info("WEBHOOK_URL not set – starting polling")
        app.run_polling()

if __name__ == '__main__':
    main()
