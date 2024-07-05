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

# XP settings
MESSAGE_XP = 25  # XP per message
VOICE_XP = 15     # XP per minute in voice chat
BASE_XP = 100  # Base XP for the first level
EXPONENT = 1.15  # Growth rate for the XP curve
MESSAGE_XP_COOLDOWN = 5  # Cooldown time in seconds

STACK_ROLES = False
REQUIRES_NOT_MUTED = False
REQUIRES_NOT_ALONE = False

LEVEL_ROLES = {
    10: 1197677650349666314,
    25: 1197677694285000784,
    50: 1197680315519479898,
    80: 1197680334750367924,
    120: 1197680368183169185,
}

sorted_level_roles = dict(sorted(LEVEL_ROLES.items(), key=lambda item: item[0], reverse=True))

@bot.event
async def on_ready():
    print(f'Bot is ready. Logged in as {bot.user}')
    update_voice_minutes.start()

def getUTCtime():
    return datetime.now(timezone.utc).replace(tzinfo=None)

def calculate_xp(messages, voice_minutes):
    return (messages * MESSAGE_XP) + (voice_minutes * VOICE_XP)

def calculate_level(xp):
    level = 0
    xp_needed = BASE_XP
    while xp >= xp_needed:
        xp -= xp_needed
        level += 1
        xp_needed = BASE_XP * (level ** EXPONENT)
    return level

async def is_tracking_enabled(guild_id):
    guild_data = await guilds_collection.find_one({'guild_id': str(guild_id)})
    return guild_data and guild_data.get('tracking_enabled', False)

async def assign_role(member, level):
    guild = member.guild
    roles_to_add = []
    roles_to_remove = []
    role_added = False
    highest_role = None

    # return
    for lvl, role_id in sorted_level_roles.items():
        role = guild.get_role(role_id)
        if level >= lvl:
            if not role_added:
                highest_role = role
                role_added = True
            if STACK_ROLES:
                if role not in member.roles:
                    roles_to_add.append(role)
        else:
            if role in member.roles:
                roles_to_remove.append(role)

    if not STACK_ROLES:
        if highest_role and highest_role not in member.roles:
            roles_to_add = [highest_role]
        for lvl, role_id in sorted_level_roles.items():
            role = guild.get_role(role_id)
            if role != highest_role and role in member.roles:
                roles_to_remove.append(role)

    if roles_to_add:
        await member.add_roles(*roles_to_add)
    if roles_to_remove:
        await member.remove_roles(*roles_to_remove)
    # print(roles_to_add, roles_to_remove)
         
                
async def update_user_data(member):
    user_id = str(member.id)
    guild_id = str(member.guild.id)
    user_data = await users_collection.find_one({'user_id': user_id, 'guild_id': guild_id})
    if user_data:
        messages = user_data.get('messages', 0)
        voice_minutes = user_data.get('voice_minutes', 0)
        xp = calculate_xp(messages, voice_minutes)
        level = calculate_level(xp)
        await assign_role(member, level)

async def update_user_data_message(member):
    user_id = str(member.id)
    guild_id = str(member.guild.id)
    current_time = getUTCtime()
    
    user_data = await users_collection.find_one({'user_id': user_id, 'guild_id': guild_id})
    if user_data: 
        last_message_time = user_data.get('last_message_time')
        if last_message_time:
            if current_time - last_message_time < timedelta(seconds=MESSAGE_XP_COOLDOWN): return
    
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

        # Ensure member.voice is not None before accessing its attributes
        if member.voice:
            if member.voice.self_mute and REQUIRES_NOT_MUTED: return
            if len(member.voice.channel.members) == 1 and REQUIRES_NOT_ALONE: return

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

@bot.slash_command(name="level", description="Say hello to the bot")
async def level(ctx: discord.ApplicationContext, member: discord.Member = None):
    if member is None:
        member = ctx.author
    
    user_id = str(member.id)
    guild_id = str(member.guild.id)
    user_data = await users_collection.find_one({'user_id': user_id, 'guild_id': guild_id})
    if user_data:
        messages = user_data.get('messages', 0)
        voice_minutes = user_data.get('voice_minutes', 0)
        xp = calculate_xp(messages, voice_minutes)
        level = calculate_level(xp)
        await ctx.respond(f'{member.display_name} is at level {level} with {xp} XP, {messages} messages sent, and {voice_minutes:.2f} minutes in voice chat.')
    else:
        await ctx.respond(f'No data found for {member.display_name}.')

@bot.slash_command(name="set_xp")
async def set_xp(ctx: discord.ApplicationContext, message_xp: int, voice_xp: int, level_multiplier: float):
    global MESSAGE_XP, VOICE_XP, LEVEL_MULTIPLIER
    MESSAGE_XP = message_xp
    VOICE_XP = voice_xp
    LEVEL_MULTIPLIER = level_multiplier
    await ctx.respond(f'Settings updated: MESSAGE_XP={MESSAGE_XP}, VOICE_XP={VOICE_XP}, LEVEL_MULTIPLIER={LEVEL_MULTIPLIER}')

@bot.slash_command(name="toggle_tracking")
@commands.has_permissions(administrator=True)
async def toggle_tracking(ctx: discord.ApplicationContext):
    guild_id = str(ctx.guild.id)
    tracking_status = await guilds_collection.find_one({'guild_id': guild_id})
    if tracking_status and 'tracking_enabled' in tracking_status:
        new_status = not tracking_status['tracking_enabled']
    else:
        new_status = True
    await guilds_collection.update_one(
        {'guild_id': guild_id},
        {'$set': {'tracking_enabled': new_status}},
        upsert=True
    )
    status_message = 'enabled' if new_status else 'disabled'
    await ctx.respond(f'Activity tracking has been {status_message} for this guild.')

bot.run(os.getenv('DISCORD_BOT_TOKEN')) 