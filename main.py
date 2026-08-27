import os
import json
import logging
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes

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

# ---------- Helper: Admin Check ----------
async def is_admin(bot, chat_id, user_id):
    try:
        member = await bot.get_chat_member(chat_id, user_id)
        return member.status in ['administrator', 'creator']
    except:
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
        "5. Check your settings with `/status`.",
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
    storage.set_user_default_chat(user.id, chat.id)   # also remember this group

async def status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    chat_id = storage.get_user_default_chat(user.id)
    if not chat_id:
        await update.message.reply_text("No default group set. Forward a message from a group to set it.")
        return

    topic = storage.get_user_topic(user.id, chat_id)
    topic_str = f"Topic ID: `{topic}`" if topic is not None else "General (no topic)"
    await update.message.reply_text(f"Default group: `{chat_id}`\n{topic_str}", parse_mode='Markdown')

# ---------- Main Message Handler ----------
async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    message = update.effective_message

    # Determine source (forwarded or typed)
    if message.forward_from_chat:
        # Forwarded from a group
        source_chat = message.forward_from_chat
        if source_chat.type not in ['group', 'supergroup']:
            await message.reply_text("I can only repost from groups.")
            return
        chat_id = source_chat.id
        if not await is_admin(context.bot, chat_id, user.id):
            await message.reply_text("You are not an admin of that group.")
            return
        from_chat_id = source_chat.id
        from_message_id = message.forward_from_message_id
        # Remember this group as default
        storage.set_user_default_chat(user.id, chat_id)
    else:
        # Typed message (in private or group? We'll expect private, but handle any)
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

    # Get the topic for this user in this group
    thread_id = storage.get_user_topic(user.id, chat_id)

    # Copy the message to the target group (with topic if any)
    try:
        await context.bot.copy_message(
            chat_id=chat_id,
            from_chat_id=from_chat_id,
            message_id=from_message_id,
            message_thread_id=thread_id   # None = general
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
    app.add_handler(MessageHandler(filters.ALL & ~filters.COMMAND, handle_message))

    # Use webhook on Render, fallback to polling locally
    webhook_url = os.environ.get('WEBHOOK_URL')
    port = int(os.environ.get('PORT', 8443))

    if webhook_url:
        print(f"Starting webhook on port {port} with URL {webhook_url}/webhook")
        app.run_webhook(
            listen='0.0.0.0',
            port=port,
            url_path='webhook',
            webhook_url=webhook_url + '/webhook'
        )
    else:
        print("WEBHOOK_URL not set – starting polling")
        app.run_polling()

if __name__ == '__main__':
    main()
