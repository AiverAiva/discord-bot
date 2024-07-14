import os # default module
import discord
from discord.ext import commands, tasks
import motor.motor_asyncio
from collections import defaultdict
import asyncio
from datetime import datetime, timedelta, timezone

intents = discord.Intents.default()
intents.messages = True
intents.voice_states = True
intents.guilds = True

from dotenv import load_dotenv
load_dotenv() 
bot = discord.Bot()

# MongoDB setup
client = motor.motor_asyncio.AsyncIOMotorClient(os.getenv('MONGO_URL'))
db = client['meow-bot']
users_collection = db['users']
guilds_collection = db['guilds']

def sort_level_roles(roles):
    return dict(sorted(((int(lvl), int(role_id)) for lvl, role_id in roles.items()), key=lambda item: item[0], reverse=True))

@bot.event
async def on_ready():
    print(f'Bot is ready. Logged in as {bot.user}')
    update_voice_minutes.start()

def getUTCtime():
    return datetime.now(timezone.utc).replace(tzinfo=None)

async def get_guild_config(guild_id):
    guild_data = await guilds_collection.find_one({'guild_id': str(guild_id)})
    if guild_data:
        for module in guild_data.get('modules', []):
            if module['id'] == 'level':
                return module.get('settings', {})
    return {}

async def calculate_xp(guild_id, messages, voice_minutes):
    config = await get_guild_config(guild_id)
    message_xp = config.get('MESSAGE_XP')
    voice_xp = config.get('VOICE_XP')
    return (messages * message_xp) + (voice_minutes * voice_xp)

async def calculate_level(guild_id, xp):
    config = await get_guild_config(guild_id)
    base_xp = config.get('BASE_XP')
    exponent = config.get('EXPONENT')

    level = 0
    xp_needed = base_xp
    while xp >= xp_needed:
        xp -= xp_needed
        level += 1
        xp_needed = base_xp * (level ** exponent)
    return level

async def is_tracking_enabled(guild_id):
    guild_data = await guilds_collection.find_one({'guild_id': str(guild_id)})
    if not guild_data: return False
    for module in guild_data.get('modules', []):
        if module['id'] == 'level':
            return module.get('enabled', False)
    return False

async def assign_role(member, level):
    config = await get_guild_config(member.guild.id)
    stack_roles = config.get('STACK_ROLES')
    level_roles = config.get('LEVEL_ROLES')
    sorted_level_roles = sort_level_roles(level_roles)
    guild = member.guild
    roles_to_add = []
    roles_to_remove = []
    role_added = False
    highest_role = None

    for lvl, role_id in sorted_level_roles.items():
        role = guild.get_role(int(role_id))
        if level >= int(lvl):
            if not role_added:
                highest_role = role
                role_added = True
            if stack_roles:
                if role not in member.roles:
                    roles_to_add.append(role)
        else:
            if role in member.roles:
                roles_to_remove.append(role)
 
    if not stack_roles:
        if highest_role and highest_role not in member.roles:
            roles_to_add = [highest_role]
        for lvl, role_id in sorted_level_roles.items():
            role = guild.get_role(int(role_id))
            if role != highest_role and role in member.roles:
                roles_to_remove.append(role)

    if roles_to_add:
        await member.add_roles(*roles_to_add)
    if roles_to_remove:
        await member.remove_roles(*roles_to_remove)
         
                
async def update_user_data(member):
    user_id = str(member.id)
    guild_id = str(member.guild.id)
    user_data = await users_collection.find_one({'user_id': user_id, 'guild_id': guild_id})
    if user_data:
        messages = user_data.get('messages', 0)
        voice_minutes = user_data.get('voice_minutes', 0)
        xp = await calculate_xp(guild_id, messages, voice_minutes)
        level = await calculate_level(guild_id, xp)
        await assign_role(member, level)

async def update_user_data_message(member):
    user_id = str(member.id)
    guild_id = str(member.guild.id)
    current_time = getUTCtime()
    
    config = await get_guild_config(guild_id)
    message_xp_cooldown = config.get('MESSAGE_XP_COOLDOWN')

    user_data = await users_collection.find_one({'user_id': user_id, 'guild_id': guild_id})
    if user_data: 
        last_message_time = user_data.get('last_message_time')
        if last_message_time:
            if current_time - last_message_time < timedelta(seconds=message_xp_cooldown): return
    
    await users_collection.update_one(
        {'user_id': user_id, 'guild_id': guild_id},
        {
            '$inc': {'messages': 1},
            '$set': {'last_message_time': current_time}
        },
        upsert=True
    )

    await update_user_data(member)

async def update_user_data_voice(member):
    user_id = str(member.id)
    guild_id = str(member.guild.id)
    user_data = await users_collection.find_one({'user_id': user_id, 'guild_id': guild_id})
    if user_data and 'voice_start' in user_data:
        start_time = user_data['voice_start']
        end_time = getUTCtime()
        duration = (end_time - start_time).total_seconds() / 60  # Duration in minutes

        config = await get_guild_config(guild_id)
        requires_not_muted = config.get('REQUIRES_NOT_MUTED')
        requires_not_alone = config.get('REQUIRES_NOT_ALONE')

        if member.voice:
            if member.voice.self_mute and requires_not_muted: return
            if len(member.voice.channel.members) == 1 and requires_not_alone: return

        await users_collection.update_one(
            {'user_id': user_id, 'guild_id': guild_id},
            {
                '$inc': {'voice_minutes': duration},
                '$set': {'voice_start': end_time}
            },
            upsert=True
        )

        await update_user_data(member)


@tasks.loop(minutes=1)
async def update_voice_minutes():
    print('Iterating voice minutes update')
    try:
        for guild in bot.guilds:
            if await is_tracking_enabled(guild.id):
                print(f'Checking guild: {guild.name} ({guild.id})')
                for voice_channel in guild.voice_channels:
                    for member in voice_channel.members:
                        await update_user_data_voice(member)
                        print(f'Updated voice data for member: {member.name} ({member.id})')
    except Exception as e:
        print(f'Error in update_voice_minutes: {e}')

@bot.event
async def on_message(message):
    if message.author.bot:
        return
    
    guild_id = message.guild.id
    if await is_tracking_enabled(guild_id):
        await update_user_data_message(message.author)

@bot.event
async def on_voice_state_update(member, before, after):
    if await is_tracking_enabled(member.guild.id):
        user_id = str(member.id)
        guild_id = str(member.guild.id)
        
        if before.channel is None and after.channel is not None:
            # Member joined voice channel
            start_time = getUTCtime()
            await users_collection.update_one(
                {'user_id': user_id, 'guild_id': guild_id},
                {'$set': {'voice_start': start_time}},
                upsert=True
            )
        
        elif before.channel is not None and after.channel is None:
            # Member left voice channel
            await update_user_data_voice(member)
            await users_collection.update_one(
                {'user_id': user_id, 'guild_id': guild_id},
                {'$unset': {'voice_start': ""}}
            )

@bot.slash_command(name="level", description="Show user's level")
async def level(ctx: discord.ApplicationContext, member: discord.Member = None):
    if member is None:
        member = ctx.author

    guild_id = str(ctx.guild.id)
    
    if not await is_tracking_enabled(guild_id):
        await ctx.respond(f'The leveling feature is not enabled on this server. Please enable it to use this command.')
        return

    user_id = str(member.id)
    user_data = await users_collection.find_one({'user_id': user_id, 'guild_id': guild_id})
    
    if user_data:
        messages = user_data.get('messages', 0)
        voice_minutes = user_data.get('voice_minutes', 0)
        xp = await calculate_xp(guild_id, messages, voice_minutes)
        level = await calculate_level(guild_id, xp)
        await ctx.respond(f'{member.display_name} is at level {level} with {xp} XP, {messages} messages sent, and {voice_minutes:.2f} minutes in voice chat.')
    else:
        await ctx.respond(f'No data found for {member.display_name}.')

bot.run(os.getenv('DISCORD_BOT_TOKEN')) 