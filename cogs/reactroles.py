import discord
from discord.ext import commands
from discord import app_commands
import os
import json
import asyncio

CONFIG_BASE = os.path.expanduser("~/Rainfall/guild_configs")

def get_config_path(guild_id: int):
    return os.path.join(CONFIG_BASE, str(guild_id), "config.json")

def load_config(guild_id: int):
    path = get_config_path(guild_id)
    if not os.path.exists(path):
        return {}
    with open(path, "r") as f:
        return json.load(f)

def save_config(guild_id: int, config: dict):
    path = get_config_path(guild_id)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        json.dump(config, f, indent=4)

def is_rainfall_staff(user: discord.Member, config: dict) -> bool:
    return str(user.id) in config.get("rainfall_staff", [])

class ReactRoles(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @app_commands.command(name="set_react_message", description="Set the message ID for reaction roles.")
    async def set_react_message(self, interaction: discord.Interaction, message_id: str):
        guild = interaction.guild
        if not guild:
            return await interaction.response.send_message("This command must be used in a server.", ephemeral=True)
        
        config = load_config(guild.id)
        if not is_rainfall_staff(interaction.user, config):
            return await interaction.response.send_message("You are not authorized to use this command.", ephemeral=True)

        config["reaction_message_id"] = message_id
        save_config(guild.id, config)
        await interaction.response.send_message(f"Reaction role message ID set to `{message_id}`.", ephemeral=True)

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
            return await interaction.response.send_message("This command must be used in a server.", ephemeral=True)

        config = load_config(guild.id)
        if not is_rainfall_staff(interaction.user, config):
            return await interaction.response.send_message("You are not authorized to use this command.", ephemeral=True)

        config.setdefault("reaction_roles", {})
        config["reaction_roles"]["add"] = {"emoji": add_emoji, "role_id": add_role.id}
        config["reaction_roles"]["remove"] = {"emoji": remove_emoji, "role_id": remove_role.id}
        save_config(guild.id, config)

        await interaction.response.send_message(
            f"Set up reaction roles:\n"
            f"➕ `{add_emoji}` → adds {add_role.mention}\n"
            f"➖ `{remove_emoji}` → removes {remove_role.mention}",
            ephemeral=True
        )

    @app_commands.command(name="proxy", description="Make the bot send a message on your behalf.")
    async def proxy(self, interaction: discord.Interaction, message: str):
        guild = interaction.guild
        if not guild:
            return await interaction.response.send_message("This command must be used in a server.", ephemeral=True)

        config = load_config(guild.id)
        if not is_rainfall_staff(interaction.user, config):
            return await interaction.response.send_message("You are not authorized to use this command.", ephemeral=True)

        # Send the proxied message
        await interaction.channel.send(message)

        # Confirm privately
        await interaction.response.send_message("✅ Message sent.", ephemeral=True)

    @commands.Cog.listener()
    async def on_raw_reaction_add(self, payload: discord.RawReactionActionEvent):
        if payload.guild_id is None:
            return  # ignore DMs

        config = load_config(payload.guild_id)
        message_id = str(config.get("reaction_message_id"))
        if not message_id or str(payload.message_id) != message_id:
            return

        guild = self.bot.get_guild(payload.guild_id)
        if not guild:
            return

        member = guild.get_member(payload.user_id)
        if not member or member.bot:
            return

        rr_config = config.get("reaction_roles", {})
        emoji = str(payload.emoji)

        add_conf = rr_config.get("add")
        remove_conf = rr_config.get("remove")

        try:
            if add_conf and emoji == add_conf["emoji"]:
                role = guild.get_role(add_conf["role_id"])
                if role:
                    await member.add_roles(role, reason="Reaction role add")
            elif remove_conf and emoji == remove_conf["emoji"]:
                role = guild.get_role(remove_conf["role_id"])
                if role:
                    await member.remove_roles(role, reason="Reaction role remove")
        except discord.Forbidden:
            pass

        # Remove the reaction afterward
        channel = guild.get_channel(payload.channel_id)
        if channel:
            try:
                message = await channel.fetch_message(payload.message_id)
                await message.remove_reaction(payload.emoji, member)
            except discord.NotFound:
                pass
            except discord.Forbidden:
                pass

async def setup(bot: commands.Bot):
    await bot.add_cog(ReactRoles(bot))
