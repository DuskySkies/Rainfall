# cogs/dm_handler.py
# the majority of the functionality of rainfall is in this file
# handles all DM related things
import discord
from discord.ext import commands
from discord import app_commands
import os
import hashlib
import json
import traceback
import asyncio
from typing import Optional

# set directory for user config files
CONFIG_DIR = "user_configs"
if not os.path.exists(CONFIG_DIR):
    os.makedirs(CONFIG_DIR)

# helper for hashing ids
def hash_user_id(user_id: int) -> str:
    """Return a hashed representation of the user ID for anonymity."""
    return hashlib.sha256(str(user_id).encode()).hexdigest()

# Intial setup, recovery and configuration
class DMHandler(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        # user_id -> {"guild_ids": [...], "message": discord.Message}
        self.awaiting_identity: dict[int, dict] = {}
        # runtime only: hash -> user
        self.anon_sessions: dict[str, discord.User] = {}

    # ─── User Config ───
    def get_guild_dir(self, guild_id: int) -> str:
        path = os.path.join(CONFIG_DIR, str(guild_id))
        os.makedirs(path, exist_ok=True)
        return path

    def get_user_config_path(
        self, guild_id: int, user: discord.User, identity_mode: str = "identified"
    ) -> str:
        guild_dir = self.get_guild_dir(guild_id)
        if identity_mode == "anonymous":
            filename = f"{hash_user_id(user.id)}.json"
        else:
            filename = f"{user.id}.json"
        return os.path.join(guild_dir, filename)

    def load_user_config(self, guild_id: int, user: discord.User) -> dict:
        """
        Searches the guild's config folder for a config corresponding to the user.
        Returns the loaded config dict (with `_config_path` set) or {} if none found.
        """
        guild_dir = self.get_guild_dir(guild_id)
        for file in os.listdir(guild_dir):
            if not file.endswith(".json"):
                continue
            path = os.path.join(guild_dir, file)
            try:
                with open(path, "r") as f:
                    config = json.load(f)
            except Exception:
                continue

            if (
                config.get("identity_mode") == "identified"
                and str(user.id) == file.split(".")[0]
            ):
                config["_config_path"] = path
                return config

            if (
                config.get("identity_mode") == "anonymous"
                and config.get("user_hash") == hash_user_id(user.id)
            ):
                config["_config_path"] = path
                self.anon_sessions[config["user_hash"]] = user
                return config

        return {}

    def save_user_config(self, guild_id: int, user: discord.User, data: dict):
        """Saves the provided data to disk (never stores raw IDs for anonymous users)."""
        to_save = dict(data)
        if "_config_path" in to_save:
            path = to_save.pop("_config_path")
        else:
            identity_mode = to_save.get("identity_mode", "identified")
            path = self.get_user_config_path(guild_id, user, identity_mode)

        to_save["guild_id"] = guild_id
        if to_save.get("identity_mode") == "anonymous":
            user_hash = hash_user_id(user.id)
            to_save["user_hash"] = user_hash
            self.anon_sessions[user_hash] = user
            to_save.pop("original_user_id", None)  # ensure no raw ID leaks

        with open(path, "w") as f:
            json.dump(to_save, f, indent=4)

    def delete_user_config(self, guild_id: int, user: discord.User):
        config = self.load_user_config(guild_id, user)
        path = config.get("_config_path")
        if path and os.path.exists(path):
            os.remove(path)

    # Ticket thread creation
    async def create_ticket_thread(
        self, user: discord.User, guild: discord.Guild, identity_mode: str
    ) -> Optional[discord.Thread]:
        config_manager = self.bot.get_cog("ConfigManager")
        if not config_manager:
            return None

        config = config_manager.load_config(guild)
        thread_channel_id = config.get("rainfall_thread_channel")
        if not thread_channel_id:
            return None

        try:
            thread_channel_id = int(thread_channel_id)
        except Exception:
            return None

        channel = guild.get_channel(thread_channel_id)
        if not channel or not isinstance(channel, discord.TextChannel):
            return None

        if identity_mode == "anonymous":
            thread_name = "Anonymous Ticket"
        else:
            member = guild.get_member(user.id)
            display_name = member.display_name if member else user.name
            thread_name = f"{display_name}'s Ticket"

        try:
            thread = await channel.create_thread(
                name=thread_name, type=discord.ChannelType.public_thread
            )
        except Exception as e:
            print(f"[DMHandler] Failed to create thread: {e}")
            traceback.print_exc()
            return None

        user_config = {
            "ticket_open": True,
            "identity_mode": identity_mode,
            "thread_id": thread.id,
            "guild_id": guild.id,
        }
        if identity_mode == "anonymous":
            user_hash = hash_user_id(user.id)
            user_config["user_hash"] = user_hash
            self.anon_sessions[user_hash] = user

        self.save_user_config(guild.id, user, user_config)
        return thread

    def mark_ticket_closed(self, guild_id: int, user: discord.User):
        config = self.load_user_config(guild_id, user)
        if config:
            config["ticket_open"] = False
            self.save_user_config(guild_id, user, config)

    async def send_ticket_closed_message(self, guild_id: int, user: discord.User):
        config = self.load_user_config(guild_id, user)
        thread_id = config.get("thread_id")
        if not thread_id:
            return
        guild = self.bot.get_guild(guild_id)
        if guild:
            thread = guild.get_thread(thread_id)
            if thread:
                try:
                    # Add [Closed] to the front of the thread name (if not already there)
                    if not thread.name.startswith("[Closed]"):
                        try:
                            await thread.edit(name=f"[Closed] {thread.name}")
                        except discord.Forbidden:
                            print(f"[DMHandler] Missing permissions to rename thread {thread_id}")
                        except Exception:
                            traceback.print_exc()

                    await thread.send("This ticket has been closed by the user.")
                    await thread.edit(archived=True)
                except discord.Forbidden:
                    print(f"[DMHandler] Missing permissions to archive {thread_id}")
                except Exception:
                    traceback.print_exc()

    # Close ticket slash command
    @app_commands.command(name="closeticket", description="Close your open ticket with the bot.")
    async def closeticket(self, interaction: discord.Interaction):
        if interaction.guild is not None:
            await interaction.response.send_message(
                "This command can only be used in DMs.", ephemeral=True
            )
            return

        user = interaction.user
        closed_any = False
        for guild in self.bot.guilds:
            config = self.load_user_config(guild.id, user)
            if config and config.get("ticket_open", False):
                self.mark_ticket_closed(guild.id, user)
                await self.send_ticket_closed_message(guild.id, user)
                self.delete_user_config(guild.id, user)
                closed_any = True

        if closed_any:
            await interaction.response.send_message("Your ticket has been closed.", ephemeral=True)
        else:
            await interaction.response.send_message("You don’t have any open tickets.", ephemeral=True)

    # message proxying
    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        try:
            if message.author.bot:
                return

            # User DMs
            if message.guild is None:
                user = message.author
                found_ticket = False
                for guild in self.bot.guilds:
                    config = self.load_user_config(guild.id, user)
                    if config and config.get("ticket_open", False):
                        thread = guild.get_thread(config.get("thread_id"))
                        if not thread:
                            continue
                        identity_prefix = (
                            "Anonymous User" if config.get("identity_mode") == "anonymous" else user.name
                        )
                        files = [await a.to_file() for a in message.attachments] if message.attachments else None
                        content = f"**{identity_prefix}:** {message.content}" if message.content else None
                        embeds = message.embeds if message.embeds else None
                        if message.stickers:
                            names = ", ".join(s.name for s in message.stickers)
                            content = f"{content}\n[Sticker(s): {names}]" if content else f"[Sticker(s): {names}]"
                        try:
                            # send to staff thread first
                            await thread.send(content=content, files=files, embeds=embeds)
                            # react to the original DM message after successful send
                            try:
                                await message.add_reaction("📩")
                            except discord.HTTPException:
                                pass
                        except Exception:
                            traceback.print_exc()
                        found_ticket = True
                        break

                if not found_ticket:
                    view = IdentityChoiceView(self, user, message)
                    try:
                        await user.send(
                            "Would you like to open this ticket **Anonymously** or be **Identified** to staff?",
                            view=view,
                        )
                        self.awaiting_identity[user.id] = {
                            "guild_ids": [g.id for g in self.bot.guilds],
                            "message": message,
                        }
                    except discord.Forbidden:
                        print(f"[DMHandler] Cannot DM {user}")
                    except Exception:
                        traceback.print_exc()

            # Staff thread
            else:
                if message.channel.type in [discord.ChannelType.public_thread, discord.ChannelType.private_thread]:
                    thread = message.channel
                    guild_id = thread.guild.id
                    target_user = None
                    guild_dir = self.get_guild_dir(guild_id)
                    for file in os.listdir(guild_dir):
                        if not file.endswith(".json"):
                            continue
                        with open(os.path.join(guild_dir, file), "r") as f:
                            try:
                                uconf = json.load(f)
                            except Exception:
                                continue
                        if uconf.get("thread_id") != thread.id or not uconf.get("ticket_open", False):
                            continue
                        if uconf.get("identity_mode") == "anonymous":
                            target_user = self.anon_sessions.get(uconf.get("user_hash"))
                        else:
                            try:
                                target_user = self.bot.get_user(int(file.split(".")[0]))
                            except Exception:
                                target_user = None
                        if target_user:
                            break

                    if target_user:
                        files = [await a.to_file() for a in message.attachments] if message.attachments else None
                        content = f"**{message.author.display_name}:** {message.content}" if message.content else None
                        embeds = message.embeds if message.embeds else None
                        if message.stickers:
                            names = ", ".join(s.name for s in message.stickers)
                            content = f"{content}\n[Sticker(s): {names}]" if content else f"[Sticker(s): {names}]"
                        try:
                            # send to the target user's DM first
                            await target_user.send(content=content, files=files, embeds=embeds)
                            # react to the original staff thread message after successful send
                            try:
                                await message.add_reaction("📩")
                            except discord.HTTPException:
                                pass
                        except Exception:
                            traceback.print_exc()

        except Exception as e:
            print(f"[DMHandler Error] {e}")
            traceback.print_exc()


# anonymous/identified popup
class IdentityChoiceView(discord.ui.View):
    def __init__(self, handler: DMHandler, user: discord.User, first_message: discord.Message):
        super().__init__(timeout=60)
        self.handler = handler
        self.user = user
        self.first_message = first_message

    @discord.ui.button(label="Anonymous", style=discord.ButtonStyle.secondary)
    async def anonymous(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.proceed(interaction, "anonymous")

    @discord.ui.button(label="Identified", style=discord.ButtonStyle.primary)
    async def identified(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.proceed(interaction, "identified")

    async def proceed(self, interaction: discord.Interaction, mode: str):
        try:
            await interaction.response.send_message(
                f"You chose **{mode.title()}**. Creating your ticket...", ephemeral=True
            )
            mutual_guilds = []
            for guild in self.user.mutual_guilds:
                config_manager = self.handler.bot.get_cog("ConfigManager")
                if config_manager:
                    conf = config_manager.load_config(guild)
                    if conf.get("rainfall_thread_channel"):
                        mutual_guilds.append(guild)

            if not mutual_guilds:
                await self.user.send("I couldn’t find any servers where you can open a ticket.")
                return

            if len(mutual_guilds) == 1:
                await self.create_ticket_in_guild(mode, mutual_guilds[0])
            else:
                view = GuildChoiceView(self.handler, self.user, self.first_message, mode, mutual_guilds)
                if view.children:
                    await self.user.send("Which server would you like to submit this ticket in?", view=view)
                else:
                    await self.user.send("No servers available to select for your ticket.")
        except Exception:
            traceback.print_exc()

    async def create_ticket_in_guild(self, mode: str, guild: discord.Guild):
        try:
            thread = await self.handler.create_ticket_thread(self.user, guild, mode)
            if not thread:
                await self.user.send(f"Could not create a ticket in {guild.name}.")
                return
            await thread.send(f"📩 New {mode.title()} Ticket opened.")
            identity_prefix = "Anonymous User" if mode == "anonymous" else self.user.name
            msg = self.first_message
            files = [await a.to_file() for a in msg.attachments] if msg.attachments else None
            content = f"**{identity_prefix}:** {msg.content}" if msg.content else None
            embeds = msg.embeds if msg.embeds else None
            if msg.stickers:
                names = ", ".join(s.name for s in msg.stickers)
                content = f"{content}\n[Sticker(s): {names}]" if content else f"[Sticker(s): {names}]"
            # send the user's original message into the thread
            await thread.send(content=content, files=files, embeds=embeds)
            # react to the ORIGINAL DM message after successful send into thread
            try:
                await self.first_message.add_reaction("📩")
            except discord.HTTPException:
                pass
            await self.user.send(f"Your {mode} ticket has been created in **{guild.name}**. Please be aware that edits to messages are not carried over.")
        except Exception:
            traceback.print_exc()


# Server picker for ticket
# Only relevant if user is in multiple servers with rainfall
class GuildChoiceView(discord.ui.View):
    def __init__(self, handler: DMHandler, user: discord.User, first_message: discord.Message, mode: str, guilds: list[discord.Guild]):
        super().__init__(timeout=60)
        self.handler = handler
        self.user = user
        self.first_message = first_message
        self.mode = mode
        self.guilds = guilds

        options = [discord.SelectOption(label=g.name, value=str(g.id)) for g in guilds]
        if options:
            self.select = discord.ui.Select(placeholder="Choose a server", options=options)
            self.select.callback = self.select_guild
            self.add_item(self.select)

    async def select_guild(self, interaction: discord.Interaction):
        try:
            guild_id = int(interaction.data["values"][0])
            guild = self.handler.bot.get_guild(guild_id)
            if guild:
                await interaction.response.send_message(
                    f"Creating your ticket in **{guild.name}**...", ephemeral=True
                )
                await IdentityChoiceView(self.handler, self.user, self.first_message).create_ticket_in_guild(self.mode, guild)
            else:
                await self.user.send("Could not find that server.")
        except Exception:
            traceback.print_exc()


# setup for loading cog
async def setup(bot: commands.Bot):
    await bot.add_cog(DMHandler(bot))
