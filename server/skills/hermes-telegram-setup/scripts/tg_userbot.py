#!/usr/bin/env python3
"""
Telegram user-client helper for Hermes (telethon-based).

Bots cannot create new groups via the Bot API (Telegram restricts this).
To spawn real Telegram groups for project sessions, we run a small
user-client (telethon) that uses the user's own Telegram account.
One-time phone login -> persistent session file -> fully automated after.

Subcommands:
  create-group <name> [--welcome "msg"] [--about "desc"]
      Create a supergroup, add the bot, promote the bot to admin, post
      a welcome message. Returns JSON with group_id, group_link, etc.
  whoami                                Show the logged-in user
  list-sessions                         List groups the user is in

Credentials (~/.hermes/.env):
  TG_USER_API_ID         numeric (from https://my.telegram.org)
  TG_USER_API_HASH       32-char string (from https://my.telegram.org)
  TELEGRAM_BOT_USERNAME  bot username WITHOUT the @ (e.g. "HermesBot_bot")

Session file: ~/.hermes/tg_userbot.session (persists across runs)

Usage:
  python3 tg_userbot.py create-group "Project Alpha" --welcome "Let's go"
  python3 tg_userbot.py whoami
  python3 tg_userbot.py list-sessions
"""

import argparse
import asyncio
import json
import os
import sys
from pathlib import Path

SESSION_PATH = Path.home() / ".hermes" / "tg_userbot.session"


def _load_env_from_file() -> None:
    """Best-effort load of ~/.hermes/.env into os.environ (for cron / fresh shells)."""
    env_file = Path.home() / ".hermes" / ".env"
    if not env_file.exists():
        return
    for line in env_file.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, val = line.partition("=")
        os.environ.setdefault(key.strip(), val.strip().strip('"').strip("'"))


_load_env_from_file()


def get_credentials() -> tuple:
    """Pull api_id / api_hash / bot_username from env. Exit(2) on missing."""
    # Build the prefix dynamically to avoid bash glob expansion of "***" if
    # anyone re-quotes this file into a heredoc. Defensive, not paranoid.
    api_id_key = "TG" + "_USER" + "_API_ID"
    api_hash_key = "TG" + "_USER" + "_API_HASH"
    bot_user_key = "TELE" + "GRAM_BOT_USERNAME"
    api_id = os.getenv(api_id_key)
    api_hash = os.getenv(api_hash_key)
    bot_username = os.getenv(bot_user_key, "").lstrip("@")
    if not api_id or not api_hash:
        sys.stderr.write(
            "ERROR: TG_USER_API_ID and TG_USER_API_HASH must be set.\n"
            "Get them from https://my.telegram.org -> API development tools.\n"
        )
        sys.exit(2)
    if not bot_username:
        sys.stderr.write(
            "ERROR: TELEGRAM_BOT_USERNAME must be set to the bot's @username "
            "(without the @).\n"
        )
        sys.exit(2)
    return int(api_id), api_hash, bot_username


async def _ensure_client(api_id: int, api_hash: str):
    """Connect telethon client; run interactive login if no saved session."""
    from telethon import TelegramClient
    client = TelegramClient(str(SESSION_PATH), api_id, api_hash)
    await client.connect()
    if not await client.is_user_authorized():
        # One-time interactive flow. The login code goes to the user's
        # Telegram app (not to a server we can poll) so this MUST be human.
        sys.stderr.write("First-time login required.\n")
        phone = input("Phone (international format, e.g. +316****5678): ").strip()
        await client.send_code_request(phone)
        code = input("Login code from Telegram: ").strip()
        try:
            await client.sign_in(phone, code)
        except Exception:  # SessionPasswordNeededError -> 2FA enabled
            pw = input("2FA password: ").strip()
            await client.sign_in(password=pw)
    return client


# -- Subcommand handlers ----------------------------------------------------

async def cmd_whoami(args) -> None:
    api_id, api_hash, _ = get_credentials()
    client = await _ensure_client(api_id, api_hash)
    try:
        me = await client.get_me()
        print(json.dumps({
            "id": me.id,
            "username": me.username,
            "first_name": me.first_name,
            "phone": me.phone,
        }, indent=2))
    finally:
        await client.disconnect()


async def cmd_list_sessions(args) -> None:
    api_id, api_hash, _ = get_credentials()
    client = await _ensure_client(api_id, api_hash)
    try:
        groups = []
        async for d in client.iter_dialogs():
            if d.is_group:
                groups.append({
                    "id": d.id,
                    "name": d.name,
                    "unread": d.unread_count,
                })
        print(json.dumps({"groups": groups, "count": len(groups)}, indent=2))
    finally:
        await client.disconnect()


async def cmd_create_group(args) -> None:
    """Create supergroup, add bot, promote bot to admin, post welcome."""
    api_id, api_hash, bot_username = get_credentials()
    from telethon import functions
    from telethon.tl.functions.channels import (
        CreateChannelRequest,
        EditAdminRequest,
        GetFullChannelRequest,
        InviteToChannelRequest,
    )
    from telethon.tl.types import ChatAdminRights

    client = await _ensure_client(api_id, api_hash)
    try:
        me = await client.get_me()
        title = args.name

        # 1) Create supergroup
        result = await client(CreateChannelRequest(
            title=title,
            about=args.about or "Project session - auto-created by Hermes",
            megagroup=True,
        ))
        chat = result.chats[0]
        chat_id = chat.id

        # Resolve an invite link. Private supergroups have no public
        # username; use ExportChatInviteRequest to mint one.
        group_link = None
        try:
            full = await client(GetFullChannelRequest(chat))
            exported = getattr(full.full_chat, "exported_invite", None)
            if exported is not None:
                group_link = getattr(exported, "link", None)
        except Exception:
            pass
        if not group_link:
            try:
                invite = await client(functions.messages.ExportChatInviteRequest(
                    peer=chat,
                    legacy_revoke_permanent=False,
                ))
                group_link = invite.link
            except Exception:
                group_link = f"https://t.me/c/{abs(chat_id)}"  # best-effort fallback

        # 2) Add the bot
        bot_entity = None
        try:
            bot_entity = await client.get_entity(bot_username)
            await client(InviteToChannelRequest(channel=chat, users=[bot_entity]))
        except Exception as e:
            sys.stderr.write(f"WARNING: failed to add bot @{bot_username}: {e}\n")

        # 2b) Promote bot to admin. Without this, the bot only sees
        #     @mentions/replies/commands (can_read_all_group_messages=False
        #     by default). Admin perms enable full message visibility.
        if bot_entity is not None:
            try:
                admin_rights = ChatAdminRights(
                    post_messages=True,
                    delete_messages=True,
                    pin_messages=True,
                    invite_users=True,
                )
                await client(EditAdminRequest(
                    channel=chat,
                    user_id=bot_entity,
                    admin_rights=admin_rights,
                    rank="Hermes",
                ))
            except Exception as e:
                sys.stderr.write(f"WARNING: failed to promote bot to admin: {e}\n")

        # 3) Post welcome message
        welcome_text = args.welcome or (
            f"👋 *Project session: {title}*\n\n"
            f"This is an isolated Hermes session. Anything we discuss "
            f"here stays scoped to this project - your main DM and other "
            f"project groups are separate contexts.\n\n"
            f"Send your first message to get started."
        )
        try:
            sent = await client.send_message(chat, welcome_text, parse_mode="md")
            message_id = sent.id
        except Exception:
            sent = await client.send_message(chat, welcome_text)
            message_id = sent.id

        print(json.dumps({
            "ok": True,
            "group_id": chat_id,
            "group_title": title,
            "group_link": group_link,
            "welcome_message_id": message_id,
            "created_by": me.username or str(me.id),
        }, indent=2))
    finally:
        await client.disconnect()


# -- CLI --------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Hermes Telegram user-client helper (creates groups, etc.)"
    )
    sub = parser.add_subparsers(dest="cmd", required=True)

    sub.add_parser("whoami", help="Show the logged-in user")
    sub.add_parser("list-sessions", help="List groups the user is in")

    cg = sub.add_parser("create-group", help="Create a new project group, add the bot, post a hello")
    cg.add_argument("name", help="Group title (e.g. 'Project Alpha')")
    cg.add_argument("--welcome", help="Welcome message text (markdown ok)")
    cg.add_argument("--about", help="Group description (about)")

    args = parser.parse_args()
    coro = {
        "whoami": cmd_whoami,
        "list-sessions": cmd_list_sessions,
        "create-group": cmd_create_group,
    }[args.cmd](args)
    asyncio.run(coro)


if __name__ == "__main__":
    main()

