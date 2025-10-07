import discord
from discord.ext import commands
from discord import app_commands
import os
import json
import asyncio

CONFIG_BASE = "/home/ubuntu/Rainfall/guild_configs"  # Use full path

def get_config_path(guild_id: int):
    return os.path.join(CONFIG_BASE, str(guild_id), "config.json")

def load_config(guild_id: int):
    path = get_config_path(guild_id)
    if not os.path.exists(path):
        print(f"[ReactRoles] No config found for {guild_id}, creating default.")
        return {}
    with open(path, "r") as f:
        print(f"[ReactRoles] Loaded config for {guild_id}")
        return json.load(f)

def save_config(guild_id: int, config: dict):
    path = get_config_path(guild_id)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        json.dump(config, f, indent=4)
    print(f"[ReactRoles] Saved config for {guild_id} -> {path}")

def is_rainfall_authorized(user: discord.Member, config: dict) -> bool:
    uid = str(user.id)
    staff = [str(u) for u in config.get("rainfall_staff", [])]
    admins = [str(u) for u in config.get("rainfall_admins", [])]
    return uid in staff or uid in admins


class ReactRoles(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    # -----------------------------
    # /set_react_message
    # -----------------------------
    @app_commands.command(name="set_react_message", description="Set the message ID for reaction roles.")
    async def set_react_message(self, interaction: discord.Interaction, message_id: str):
        guild = interaction.guild
        if not guild:
            return await interaction.response.send_message("Use this in a server.", ephemeral=True)
        
        config = load_config(guild.id)
        if not is_rainfall_authorized(interaction.user, config):
            return await interaction.response.send_message("You are not authorized.", ephemeral=True)

        config["reaction_message_id"] = str(message_id)
        save_config(guild.id, config)
        await interaction.response.send_message(f"✅ Reaction role message ID set to `{message_id}`.", ephemeral=True)

    # -----------------------------
    # /set_react_role
    # -----------------------------
    @app_commands.command(name="set_react_role", description="Bind emojis to add or remove a role.")
    async def set_react_role(
        self,
        interaction: discord.Interaction,
        add_emoji: str,
        add_role: discord.Role,
        remove_emoji: str,
        remove_role: discord.Role
    ):
        guild = interaction.guild
        if not guild:
            return await interaction.response.send_message("Use this in a server.", ephemeral=True)

        config = load_config(guild.id)
        if not is_rainfall_authorized(interaction.user, config):
            return await interaction.response.send_message("You are not authorized.", ephemeral=True)

        message_id = config.get("reaction_message_id")
        if not message_id:
            return await interaction.response.send_message("⚠️ Run `/set_react_message` first.", ephemeral=True)

        config.setdefault("reaction_roles", {})
        config["reaction_roles"]["add"] = {"emoji": add_emoji, "role_id": int(add_role.id)}
        config["reaction_roles"]["remove"] = {"emoji": remove_emoji, "role_id": int(remove_role.id)}
        save_config(guild.id, config)

        try:
            channel = interaction.channel
            message = await channel.fetch_message(int(message_id))
            await message.add_reaction(add_emoji)
            await message.add_reaction(remove_emoji)
            print(f"[ReactRoles] Added emojis {add_emoji}, {remove_emoji} to message {message_id}")
        except Exception as e:
            print(f"[ReactRoles] Failed to add reactions: {e}")
            return await interaction.response.send_message(f"❌ Couldn't add reactions: {e}", ephemeral=True)

        await interaction.response.send_message(
            f"✅ Reaction roles ready:\n"
            f"➕ {add_emoji} → {add_role.mention}\n"
            f"➖ {remove_emoji} → {remove_role.mention}",
            ephemeral=True
        )

    # -----------------------------
    # /proxy
    # -----------------------------
    @app_commands.command(name="proxy", description="Make the bot send a message on your behalf.")
    async def proxy(self, interaction: discord.Interaction, message: str):
        guild = interaction.guild
        if not guild:
            return await interaction.response.send_message("Use this in a server.", ephemeral=True)

        config = load_config(guild.id)
        if not is_rainfall_authorized(interaction.user, config):
            return await interaction.response.send_message("You are not authorized.", ephemeral=True)

        await interaction.channel.send(message)
        await interaction.response.send_message("✅ Message proxied.", ephemeral=True)

    # -----------------------------
    # Reaction handler
    # -----------------------------
    @commands.Cog.listener()
    async def on_raw_reaction_add(self, payload: discord.RawReactionActionEvent):
        if payload.guild_id is None:
            return

        config = load_config(payload.guild_id)
        if not config.get("reaction_message_id"):
            return

        if str(payload.message_id) != str(config["reaction_message_id"]):
            return

        guild = self.bot.get_guild(payload.guild_id)
        if not guild:
            return

        member = guild.get_member(payload.user_id)
        if not member or member.bot:
            return

        rr_config = config.get("reaction_roles", {})
        emoji_str = str(payload.emoji)
        print(f"[ReactRoles] Reaction detected: {emoji_str} from {member} on message {payload.message_id}")

        add_conf = rr_config.get("add")
        remove_conf = rr_config.get("remove")

        role_to_add = None
        role_to_remove = None

        if add_conf and emoji_str == add_conf["emoji"]:
            role_to_add = guild.get_role(int(add_conf["role_id"]))
        elif remove_conf and emoji_str == remove_conf["emoji"]:
            role_to_remove = guild.get_role(int(remove_conf["role_id"]))

        if role_to_add:
            try:
                await member.add_roles(role_to_add, reason="Reaction role add")
                print(f"[ReactRoles] Added role {role_to_add.name} to {member}")
            except discord.Forbidden:
                print(f"[ReactRoles] Missing permissions to add {role_to_add.name}")
        elif role_to_remove:
            try:
                await member.remove_roles(role_to_remove, reason="Reaction role remove")
                print(f"[ReactRoles] Removed role {role_to_remove.name} from {member}")
            except discord.Forbidden:
                print(f"[ReactRoles] Missing permissions to remove {role_to_remove.name}")

        # Remove the user's reaction
        try:
            channel = guild.get_channel(payload.channel_id)
            if channel:
                message = await channel.fetch_message(payload.message_id)
                await message.remove_reaction(payload.emoji, member)
        except Exception as e:
            print(f"[ReactRoles] Failed to remove reaction: {e}")


async def setup(bot: commands.Bot):
    await bot.add_cog(ReactRoles(bot))