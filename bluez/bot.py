# Main bot code

import discord
from discord import app_commands
from discord.ext import commands

import asyncio
import re
import os
import typing
import lyricsgenius
import logging

from bluez.player import *
from bluez.song import *
from bluez.timezones import *
from bluez.util import *



##### Initialization #####


# Load relevant environment variables
BLUEZ_DEBUG = bool(int(os.getenv('BLUEZ_DEBUG', '0')))
BLUEZ_INVITE_LINK = os.getenv('BLUEZ_INVITE_LINK')
BLUEZ_SOURCE_LINK = os.getenv('BLUEZ_SOURCE_LINK', 'https://github.com/hukilau17/bluez')

if BLUEZ_DEBUG:
    logging.basicConfig(level=logging.DEBUG)



# Initialize the lyricsgenius interface
try:
    genius = lyricsgenius.Genius(verbose = BLUEZ_DEBUG)
except:
    # this should only happen if you haven't provided a token for the genius API
    genius = None



# Dict mapping IDs guilds where this bot is a member of to Player instances
player_map = {}



# Initialize the commands.Bot

def command_prefix(bot, message):
    # Get the prefix for the given message
    if message.guild is not None:
        try:
            return player_map[message.guild.id].prefix
        except KeyError:
            return '!'
    else:
        return '!'

description = 'Bluez, ready for your command!'

intents = discord.Intents.default()
intents.members = True
intents.message_content = True


# Create the commands.Bot
bot = commands.Bot(command_prefix=command_prefix, description=description,
                   intents=intents, help_command=None)






##### Bot events #####


@bot.event
async def on_ready():
    # Called when the bot comes online
    # Creates a player for each guild and syncs the application commands
    for guild in bot.guilds:
        player_map[guild.id] = Player(bot, guild)
    await bot.tree.sync()



@bot.event
async def on_guild_join(guild):
    # Called when the bot joins a guild
    # Adds the guild to the player map and sends a welcome message
    player_map[guild.id] = Player(bot, guild)
    await guild.text_channels[0].send('''**Thank you for adding me! :white_check_mark:**
`-` My prefix here is `!`
`-` You can see a list of my commands by typing `!help`
`-` You can change my prefix with `!settings prefix`''')




@bot.event
async def on_voice_state_update(member, before, after):
    # Respond to users joining or leaving voice calls
    if not bot.is_ready():
        return # Don't respond till we're ready
    before_guild = getattr(before.channel, 'guild', None)
    after_guild = getattr(after.channel, 'guild', None)
    if after_guild and not before_guild:
        # Notify the relevant player that someone has joined a voice call the bot is in
        player = player_map[after_guild.id]
        if player.voice_channel == after.channel:
            await player.notify_user_join(member)
    elif before_guild and not after_guild:
        # Notify the relevant player that someone has left a voice call the bot is in
        player = player_map[before_guild.id]
        if player.voice_channel == before.channel:
            await player.notify_user_leave(member)



@bot.event
async def on_command_error(ctx, error):
    if isinstance(error, ParseTimeError):
        # Print out the message of the error itself if it was thrown by parse_time()
        await ctx.send('**:x: %s**' % error)
    elif isinstance(error, commands.UserInputError):
        # Print out a usage message for other user input-related errors
        embed = discord.Embed(title=':x: Invalid usage',
                              description = '`%s%s %s`' % \
                              (command_prefix(bot, ctx), ctx.command.name, ctx.command.signature),
                              color=discord.Color.red())
        await ctx.send(embed=embed)
    elif isinstance(error, commands.CommandNotFound):
        # Ignore this error
        pass
    else:
        # should not happen; but if it does, notify the user
        log_exception(error)
        await ctx.send('**:x: Internal Bluez error: `%s`**' % error)



@bot.tree.error
async def on_error(interaction, error):
    # version of on_command_error() for slash commands
    ctx = (await bot.get_context(interaction))
    await on_command_error(ctx, error)
    

    





##### Helper functions #####


async def get_player(ctx):
    # Return the Player corresponding to the given Context. Return None (and replies with an error message) either
    # if there is no Player, or if the command is not allowed due to bot settings.
    if not bot.is_ready():
        await ctx.send('**:warning: The bot is not ready yet. Please try again in a few seconds.**')
        return None
    if ctx.guild is None:
        # if there is no guild (i.e. the command is being invoked in a DM), return None
        await ctx.send('**:warning: This command cannot be used in private messages**')
        return None
    player = player_map[ctx.guild.id]
    if ctx.channel in player.blacklist:
        # if the channel is blacklisted, return None
        if ctx.interaction:
            # only respond if this is a slash command
            await ctx.send('**:no_entry_sign: This channel cannot be used for music commands.**')
        return None
    if player.djonly and not player.is_dj(ctx.author):
        # if only DJ's are allowed to use bot commands, and the invoking player is not a DJ, return None
        await ctx.send('**:x: The bot is currently in DJ only mode, you must have a role named `%s` \
    or the `Manage Channels` permission to use it**' % player.djrole)
        return None
    # Otherwise return the player
    return player



class ParseTimeError(commands.BadArgument):
    # Specialized BadArgument exception raised by parse_time()
    pass


def parse_time(time):
    # Converter function that takes a string describing a timestamp and returns an integer number of seconds.
    if len(time) > MAX_INPUT_LENGTH:
        raise ParseTimeError('time `%s` is too large to parse' % time)
    try:
        # maybe it's just an integer number of seconds already
        result = int(time)
    except ValueError:
        # HH:MM:SS format
        match = re.match(r'(?:(\d+):)?(\d+):(\d+)$', time)
        if match:
            h = int(match.group(1) or 0)
            m = int(match.group(2))
            s = int(match.group(3))
            result = 3600*h + 60*m + s
        else:
            ##HHhMMmSSs format
            match = re.match(r'(?:(\d+)h)?(?:(\d+)m)?(?:(\d+)s)?$', time)
            if match and match.group():
                h = int(match.group(1) or 0)
                m = int(match.group(2) or 0)
                s = int(match.group(3) or 0)
                result = 3600*h + 60*m + s
            else:
                raise ParseTimeError('unable to parse time `%s`' % time)
    if result < 0:
        raise ParseTimeError('number of seconds must be nonnegative')
    if abs(result) > MAX_TIME_VALUE:
        # thanks to all the lovely Austin Math Circle members
        # for tirelessly trying to break my bot
        # and ultimately forcing me to add this code here.
        raise ParseTimeError('time `%s` is too large to parse' % time)
    return result




async def ensure_range(ctx, arg, descr, min, max):
    # Error handler for commands that accept a range
    if hasattr(arg, '__len__'):
        arg = len(arg)
    if (min is None) or (arg >= min):
        if (max is None) or (arg <= max):
            return True
    if max is None:
        await ctx.send('**:x: %s must be at least %s**' % (descr, min))
    elif min is None:
        await ctx.send('**:x: %s must be at most %s**' % (descr, max))
    else:
        await ctx.send('**:x: %s must be between %s and %s**' % (descr, min, max))
    return False
    
    
    

    





##### Bot commands #####


@bot.hybrid_command(name='join', aliases=['summon'])
async def command_join(ctx):
    '''Summon the bot to the voice channel you are in'''
    player = (await get_player(ctx))
    if player is not None:
        await player.ensure_joined(ctx, quiet=False)
        

@bot.tree.command(name='summon')
async def app_summon(interaction):
    '''Summon the bot to the voice channel you are in'''
    await command_join.callback(await bot.get_context(interaction))




@bot.tree.command(name='play')
@app_commands.describe(
    query='A link or search query describing the song or playlist to queue up',
    where='Whether to place the song or playlist at the bottom, top, or random spot in the queue,\
or to skip the current song and play it immediately',
    source='Where to look up search queries (YouTube, SoundCloud, etc.)',
    browse='Whether to pop up a menu for the user to select a song from \
rather than just queueing the top match')
async def app_play(ctx, query: str,
                   where: typing.Literal['Bottom', 'Top', 'Now', 'Shuffle'] = 'Bottom',
                   source: typing.Literal[tuple(SEARCH_INFO)] = 'YouTube',
                   browse: bool = False):
    '''Play a song with the given name or url'''
    if isinstance(ctx, discord.Interaction):
        ctx = (await bot.get_context(ctx))
    player = (await get_player(ctx))
    if player is not None:
        # Make sure the user is allowed to play this song. They need DJ permissions
        # if they are trying to queue it ahead of other people's songs or skip
        # other people's songs
        ensure = player.ensure_joined
        if (where == 'Top') and player.queue:
            ensure = player.ensure_dj
        elif (where == 'Now') and (player.queue or player.now_playing):
            ensure = player.ensure_dj
        if (await ensure(ctx)):
            if browse:
                songs = (await player.songs_from_search(ctx, query, source))
            else:
                songs = (await player.songs_from_query(ctx, query, source))
            if songs:
                if where == 'Bottom':
                    await player.play(ctx, songs)
                elif where == 'Top':
                    await player.playtop(ctx, songs)
                elif where == 'Now':
                    await player.playskip(ctx, songs)
                elif where == 'Shuffle':
                    await player.playshuffle(ctx, songs)
        
    
                       
# shortcuts for play

@bot.tree.command(name='search')
@app_commands.describe(
    query='A search query describing the song or playlist to queue up',
    where='Whether to place the song or playlist at the bottom, top, or random spot in the queue,\
or to skip the current song and play it immediately',
    source='Where to look up search queries (YouTube, SoundCloud, etc.)')
async def app_search(ctx, query: str,
                     where: typing.Literal['Bottom', 'Top', 'Now', 'Shuffle'] = 'Bottom',
                     source: typing.Literal[tuple(SEARCH_INFO)] = 'YouTube'):
    '''Search for a song using the query, and return the top 10 results'''
    await app_play.callback(ctx, query, where, source, True)

bot.tree.command(name='find')(app_search.callback)



@bot.command(name='play', aliases=['p'])
async def command_play(ctx, *, query: str):
    '''Play a song with the given name or url'''
    await app_play.callback(ctx, query)


@bot.hybrid_command(name='playtop', aliases=['pt', 'ptop'])
@app_commands.describe(query='A link or search query describing the song or playlist to queue up')
async def command_playtop(ctx, *, query: str):
    '''Add a song with the given name/url to the top of the queue'''
    await app_play.callback(ctx, query, where='Top')


@bot.hybrid_command(name='playskip', aliases=['ps', 'pskip', 'pn', 'playnow'])
@app_commands.describe(query='A link or search query describing the song or playlist to queue up')
async def command_playskip(ctx, *, query: str):
    '''Skip the current song and play the song with the given name/url'''
    await app_play.callback(ctx, query, where='Now')

bot.tree.command(name='playnow')(command_playskip.callback)


@bot.command(name='search', aliases=['find'])
async def command_search(ctx, *, query: str):
    '''Search for a song using the query, and return the top 10 results'''
    await app_play.callback(ctx, query, browse=True)


@bot.hybrid_command(name='soundcloud', aliases=['sc'])
@app_commands.describe(query='A link or search query describing the song or playlist to queue up')
async def command_soundcloud(ctx, *, query: str):
    '''Play a song from SoundCloud with the given name/url'''
    await app_play.callback(ctx, query, source='SoundCloud')
            
        



# now playing commands

@bot.hybrid_command(name='nowplaying', aliases=['np'])
async def command_nowplaying(ctx):
    '''Show what song is currently playing'''
    player = (await get_player(ctx))
    if player is not None:
        if (await player.ensure_playing(ctx)):
            await player.np_message(ctx)


@bot.hybrid_command(name='grab', aliases=['save', 'yoink'])
async def command_grab(ctx):
    '''Show what song is currently playing'''
    player = (await get_player(ctx))
    if player is not None:
        if (await player.ensure_playing(ctx)):
            try:
                await player.np_message(ctx.author)
            except discord.Forbidden:
                await ctx.send('**:warning: Unable to send message**')
            else:
                if ctx.interaction is not None:
                    await ctx.send('**:thumbsup: Message sent**')





# seeking commands


@bot.hybrid_command(name='seek')
@app_commands.describe(time='The time in the song to seek to')
async def command_seek(ctx, time: parse_time):
    '''Seek to a certain point in the current track'''
    player = (await get_player(ctx))
    if player is not None:
        if (await player.ensure_dj(ctx)) and (await player.ensure_playing(ctx)):
            await player.seek(ctx, time)


@bot.hybrid_command(name='rewind', aliases=['rwd'])
@app_commands.describe(time='The amount of time to seek backward')
async def command_rewind(ctx, time: parse_time):
    '''Rewind by a certain amount of time in the current track'''
    player = (await get_player(ctx))
    if player is not None:
        if (await player.ensure_dj(ctx)) and (await player.ensure_playing(ctx)):
            await player.rewind(ctx, time)


@bot.hybrid_command(name='forward', aliases=['fwd'])
@app_commands.describe(time='The amount of time to seek forward')
async def command_forward(ctx, time: parse_time):
    '''Skip forward by a certain amount of time in the current track'''
    player = (await get_player(ctx))
    if player is not None:
        if (await player.ensure_dj(ctx)) and (await player.ensure_playing(ctx)):
            await player.forward(ctx, time)


@bot.hybrid_command(name='replay')
async def command_replay(ctx):
    '''Reset the progress of the current song'''
    player = (await get_player(ctx))
    if player is not None:
        if (await player.ensure_dj(ctx)) and (await player.ensure_playing(ctx)):
            await player.seek(ctx, 0)





# song looping

@bot.hybrid_command(name='loop', aliases=['repeat'])
@app_commands.describe(on='Indicate whether to turn looping on or off')
async def command_loop(ctx, on: typing.Optional[bool] = None):
    '''Toggle looping for the currently playing song'''
    player = (await get_player(ctx))
    if player is not None:
        if (await player.ensure_dj(ctx)) and (await player.ensure_playing(ctx)):
            await player.loop(ctx, on)


@bot.tree.command(name='repeat')
@app_commands.describe(on='Indicate whether to turn looping on or off')
async def app_repeat(interaction, on: typing.Optional[bool] = None):
    '''Toggle looping for the currently playing song'''
    await command_loop.callback(await bot.get_context(interaction))



# song skipping

@bot.hybrid_command(name='voteskip', aliases=['skip', 'next', 's'])
async def command_voteskip(ctx):
    '''Vote to skip the currently playing song'''
    player = (await get_player(ctx))
    if player is not None:
        if (await player.ensure_playing(ctx)):
            await player.voteskip(ctx)


@bot.tree.command(name='skip')
async def app_skip(interaction):
    '''Vote to skip the currently playing song'''
    await command_voteskip.callback(await bot.get_context(interaction))


@bot.hybrid_command(name='forceskip', aliases=['fs', 'fskip'])
@app_commands.describe(position='The position in the queue to skip to (1 is the top of the queue)')
async def command_forceskip(ctx, position: int = 1):
    '''Skip the currently playing song immediately'''
    player = (await get_player(ctx))
    if player is not None:
        if (await player.ensure_dj(ctx)) and (await player.ensure_playing(ctx)):
            await player.skipto(ctx, position)





# pausing/resuming

@bot.hybrid_command(name='pause', aliases=['stop'])
async def command_pause(ctx):
    '''Pause the currently playing track'''
    player = (await get_player(ctx))
    if player is not None:
        if (await player.ensure_dj(ctx)) and (await player.ensure_playing(ctx)):
            await player.pause(ctx)


@bot.hybrid_command(name='resume', aliases=['re', 'res', 'continue', 'unpause'])
async def command_resume(ctx):
    '''Resume paused music'''
    player = (await get_player(ctx))
    if player is not None:
        if (await player.ensure_dj(ctx)) and (await player.ensure_playing(ctx)):
            await player.resume(ctx)


@bot.tree.command(name='unpause')
async def app_unpause(interaction):
    '''Resume paused music'''
    await command_resume.callback(await bot.get_context(interaction))




# lyrics

@bot.hybrid_command(name='lyrics', aliases=['l', 'ly'])
async def command_lyrics(ctx, *, query: typing.Optional[str] = None):
    '''Get the lyrics of a song (by default the currently playing song)'''
    if genius is None:
        await ctx.send('**:x: Lyric searching is not enabled.**')
        return
    is_now_playing = False
    artist = ''
    # figure out who the guild is
    if ctx.guild is None:
        player = None
    else:
        player = (await get_player(ctx))
        if player is None:
            return
    if not query:
        # get the lyrics to the currently playing song
        if player is None:
            # this is a DM
            await ctx.send('**:x: I am not currently playing anything.**')
            return
        elif (await player.ensure_playing(ctx)):
            song_name = (player.now_playing.track or player.now_playing.name)
            artist = (player.now_playing.artist or '')
            is_now_playing = True
        else:
            return
    # otherwise the song is just the string query they typed in
    else:
        song_name = query
    if artist:
        await ctx.send('**:mag: Searching lyrics for `%s` by `%s`**' % (song_name, artist))
    else:
        await ctx.send('**:mag: Searching lyrics for `%s`**' % song_name)
    try:
        song = genius.search_song(song_name, artist)
    except Exception as e:
        await ctx.send('**:x: Genius error: `%s`**' % e)
        return
    if is_now_playing and (not song) and (not artist):
        # Sometimes song titles on YouTube videos contain too much information (e.g. "Official Audio/Video")
        # that makes Genius fail to return a meaningful result. This is a really cheap attempt to lower the probability
        # of that happening.
        m = re.search(r'[()\[\]|]', song_name)
        if m:
            song_name = song_name[:m.start()] # "artist - song (official audio)" becomes just "artist - song"
            try:
                song = genius.search_song(song_name)
            except Exception as e:
                await ctx.send('**:x: Genius error: `%s`**' % e)
                return
    if song:
        lyrics = song.lyrics
        m = re.search(r'\d*Embed', lyrics)
        if m:
            # get rid of trailing garbage that Genius puts in
            lyrics = lyrics[:m.start()]
        embeds = []
        npages = (len(lyrics) - 1) // 4000 + 1
        for i in range(npages):
            # make sure the lyrics aren't too long to fit into a single embed
            # the lyrics to "American Pie" are over 4000 characters long :P
            embed = discord.Embed(title='%s - %s' % (song.artist, song.title),
                                  description=lyrics[4000 * i : 4000 * (i+1)] + '\n\nPage %d/%d' % (i+1, npages),
                                  color=discord.Color.green())
            embed.set_thumbnail(url=song.song_art_image_thumbnail_url)
            embed.set_footer(text='Requested by %s' % format_user(ctx.author), icon_url=ctx.author.avatar.url)
            embeds.append(embed)
        await Player.post_multipage_embed(ctx, embeds)
    else:
        await ctx.send('**:x: There were no results matching the query**')






# disconnect

@bot.hybrid_command(name='disconnect', aliases=['dc', 'leave', 'dis'])
async def command_disconnect(ctx):
    '''Disconnect the bot from the voice channel it is in'''
    player = (await get_player(ctx))
    if player is not None:
        if (await player.ensure_dj(ctx, need_join=False)):
            await player.disconnect(ctx)


# queue/history

@bot.hybrid_command(name='queue', aliases=['q'])
@app_commands.describe(page='The page number in the queue to show')
async def command_queue(ctx, page: typing.Optional[int] = None):
    '''Show the list of songs in the queue'''
    player = (await get_player(ctx))
    if player is not None:
        if (await player.ensure_queue(ctx)):
            if page is None:
                page = 1
            await player.queue_message(ctx, page-1)


@bot.hybrid_command(name='history', aliases=['hist', 'h'])
@app_commands.describe(timezone='Indicate what time zone the history should use')
async def command_history(ctx, *, timezone: typing.Optional[str] = None):
    '''Show the list of recently played songs'''
    player = (await get_player(ctx))
    if player is not None:
        if (await player.ensure_history(ctx)):
            if timezone:
                if timezone in TIMEZONES:
                    timezone = get_timezone(timezone)
                else:
                    await ctx.send('**:x: Invalid timezone `%s`**' % timezone)
                    timezone = None
                    # don't exit; still output the history in this case, in UTC time
            await player.history_message(ctx, timezone)


@command_history.autocomplete('timezone')
async def history_autocomplete(interaction, current):
    # return list of choices matching what the user has typed so far in the timezone for the history slash command
    matching = [timezone for timezone in TIMEZONES if current.lower() in timezone.lower()][:25]
    return [app_commands.Choice(name=timezone, value=timezone) for timezone in matching]



@bot.hybrid_command(name='back')
async def command_back(ctx):
    '''Skip backwards and play the previous song again'''
    player = (await get_player(ctx))
    if player is not None:
        if (await player.ensure_dj(ctx)) and (await player.ensure_history(ctx)):
            await player.skipback(ctx)


@bot.hybrid_command(name='loopqueue', aliases=['loopq', 'lq', 'qloop', 'queueloop'])
@app_commands.describe(on='Indicate whether to turn queue looping on or off')
async def command_loopqueue(ctx, on: typing.Optional[bool] = None):
    '''Toggle looping for the whole queue'''
    player = (await get_player(ctx))
    if player is not None:
        if (await player.ensure_dj(ctx)) and (await player.ensure_queue(ctx)):
            await player.loopqueue(ctx, on)



@bot.hybrid_command(name='move', aliases=['m', 'mv'])
@app_commands.describe(old='The position of the song before it is moved (1 is the top of the queue)',
                       new='The position the song is to be moved to')
async def command_move(ctx, old: int, new: int = 1):
    '''Move a certain song to a chosen position in the queue'''
    player = (await get_player(ctx))
    if player is not None:
        if (await player.ensure_dj(ctx)) and (await player.ensure_queue(ctx)):
            await player.move(ctx, old, new)



@bot.hybrid_command(name='skipto', aliases=['st'])
@app_commands.describe(position='The position in the queue to skip to (1 is the top of the queue)')
async def command_skipto(ctx, position: int):
    '''Skip to a certain position in the queue'''
    # this is exactly the same as forceskip except the position is not optional
    player = (await get_player(ctx))
    if player is not None:
        if (await player.ensure_dj(ctx)) and (await player.ensure_playing(ctx)):
            await player.skipto(ctx, position)
    

@bot.hybrid_command(name='shuffle', aliases=['random'])
async def command_shuffle(ctx):
    '''Shuffle the entire queue'''
    player = (await get_player(ctx))
    if player is not None:
        if (await player.ensure_dj(ctx)) and (await player.ensure_queue(ctx)):
            await player.shuffle(ctx)




# remove command family

@bot.hybrid_group(name='remove', fallback='song', aliases=['rm'])
@app_commands.describe(position='The position of the song in the queue to remove (1 is the top of the queue)')
async def command_remove(ctx, position: int):
    '''Remove a certain song from the queue'''
    player = (await get_player(ctx))
    if player is not None:
        if (await player.ensure_dj(ctx)) and (await player.ensure_queue(ctx)):
            await player.remove(ctx, position)


@command_remove.command(name='range', aliases=['songs'])
@app_commands.describe(start='The first position to remove (1 is the top of the queue)',
                       end='The last position to remove (defaults to the end of the queue)')
async def command_remove_range(ctx, start: int, end: typing.Optional[int] = None):
    player = (await get_player(ctx))
    if player is not None:
        if (await player.ensure_dj(ctx)) and (await player.ensure_queue(ctx)):
            await player.remove_range(ctx, start, end)




# advanced queue commands


@bot.hybrid_command(name='clear', aliases=['cl'])
@app_commands.describe(user='Remove only the songs posted by this user')
async def command_clear(ctx, user: typing.Optional[discord.Member] = None):
    '''Clear the whole queue'''
    player = (await get_player(ctx))
    if player is not None:
        if (await player.ensure_dj(ctx)) and (await player.ensure_queue(ctx)):
            await player.clear(ctx, user)


@bot.hybrid_command(name='leavecleanup', aliases=['lc'])
async def command_leavecleanup(ctx):
    '''Remove absent users' songs from the queue'''
    player = (await get_player(ctx))
    if player is not None:
        if (await player.ensure_dj(ctx)) and (await player.ensure_queue(ctx)):
            await player.leavecleanup(ctx)


@bot.hybrid_command(name='removedupes', aliases=['rd', 'rmd', 'drm'])
async def command_removedupes(ctx):
    '''Remove duplicate songs from the queue'''
    player = (await get_player(ctx))
    if player is not None:
        if (await player.ensure_dj(ctx)) and (await player.ensure_queue(ctx)):
            await player.removedupes(ctx)





# settings command family

@bot.hybrid_group(name='settings', fallback='show', aliases=['setting'])
async def command_settings(ctx):
    '''List out the Bluez bot settings'''
    player = (await get_player(ctx))
    if player is not None:
        await player.settings_show(ctx)


@command_settings.command(name='prefix')
@app_commands.describe(prefix='The command prefix, such as `!`')
async def command_settings_prefix(ctx, prefix: typing.Optional[app_commands.Range[str, 1, 5]] = None):
    '''Query or set the Bluez bot prefix'''
    player = (await get_player(ctx))
    if player is not None:
        if (prefix is None) or (await player.ensure_admin(ctx)):
            await player.settings_prefix(ctx, prefix)
            if prefix is not None:
                player.save_settings()


@command_settings.command(name='blacklist')
@app_commands.describe(channel='Channel to add to or remove from the blacklist')
async def command_settings_blacklist(ctx, channel: typing.Optional[discord.TextChannel] = None):
    '''Toggle whether a channel is blacklisted or not'''
    player = (await get_player(ctx))
    if player is not None:
        if (channel is None) or (await player.ensure_admin(ctx)):
            await player.settings_blacklist(ctx, channel)
            if channel is not None:
                player.save_settings()


@command_settings.command(name='autoplay')
@app_commands.describe(playlist='URL linking to a playlist, or `disable` to turn off autoplay')
async def command_settings_autoplay(ctx, playlist: typing.Optional[str] = None):
    '''Query or set the playlist that Bluez automatically plays when it comes online'''
    player = (await get_player(ctx))
    if player is not None:
        if (playlist is None) or (await player.ensure_admin(ctx)):
            await player.settings_autoplay(ctx, playlist)
            if playlist is not None:
                player.save_settings()


@command_settings.command(name='announcesongs')
@app_commands.describe(on='Indicate whether to turn announcing songs on or off')
async def command_settings_announcesongs(ctx, on: typing.Optional[bool] = None):
    '''Query or set whether Bluez announces new songs that come on'''
    player = (await get_player(ctx))
    if player is not None:
        if (on is None) or (await player.ensure_admin(ctx)):
            await player.settings_announcesongs(ctx, on)
            if on is not None:
                player.save_settings()


@command_settings.command(name='maxqueuelength')
@app_commands.describe(length='The maximum allowed length of the queue, or `0` to allow any length')
async def command_settings_maxqueuelength(ctx, length: typing.Optional[app_commands.Range[int, 0, 10000]] = None):
    '''Query or set the maximum number of songs allowed on the queue at once'''
    player = (await get_player(ctx))
    if player is not None:
        if (length is None) or (await player.ensure_admin(ctx)):
            await player.settings_maxqueuelength(ctx, length)
            if length is not None:
                player.save_settings()


@command_settings.command(name='maxusersongs')
@app_commands.describe(number='The maximum number of songs per user, or `0` to allow any number')
async def command_settings_maxusersongs(ctx, number: typing.Optional[app_commands.Range[int, 0, 10000]] = None):
    '''Query or set the maximum number of songs a single user is allowed to add to the queue at once'''
    player = (await get_player(ctx))
    if player is not None:
        if (number is None) or (await player.ensure_admin(ctx)):
            await player.settings_maxusersongs(ctx, number)
            if number is not None:
                player.save_settings()


@command_settings.command(name='preventduplicates')
@app_commands.describe(on='Indicate whether to prevent duplicate songs from being posted')
async def command_settings_preventduplicates(ctx, on: typing.Optional[bool] = None):
    '''Query or set whether Bluez blocks duplicate songs from being added to the queue'''
    player = (await get_player(ctx))
    if player is not None:
        if (on is None) or (await player.ensure_admin(ctx)):
            await player.settings_preventduplicates(ctx, on)
            if on is not None:
                player.save_settings()


@command_settings.command(name='defaultvolume')
@app_commands.describe(volume='The default volume Bluez should use in this server')
async def command_settings_defaultvolume(ctx, volume: typing.Optional[app_commands.Range[int, 0, 200]] = None):
    '''Query or set the default volume Bluez uses when joining'''
    player = (await get_player(ctx))
    if player is not None:
        if (volume is None) or (await player.ensure_admin(ctx)):
            await player.settings_defaultvolume(ctx, volume)
            if volume is not None:
                player.save_settings()


@command_settings.command(name='djplaylists')
@app_commands.describe(on='Indicate whether to prevent non-DJ members from queueing up playlists')
async def command_settings_djplaylists(ctx, on: typing.Optional[bool] = None):
    '''Query or set whether Bluez blocks non-DJ members from adding whole playlists to the queue'''
    player = (await get_player(ctx))
    if player is not None:
        if (on is None) or (await player.ensure_admin(ctx)):
            await player.settings_djplaylists(ctx, on)
            if on is not None:
                player.save_settings()


@command_settings.command(name='djonly')
@app_commands.describe(on='Indicate whether Bluez should only respond to commands from DJs')
async def command_settings_djonly(ctx, on: typing.Optional[bool] = None):
    '''Query or set whether Bluez blocks non-DJ members from interacting with it'''
    player = (await get_player(ctx))
    if player is not None:
        if (on is None) or (await player.ensure_admin(ctx)):
            await player.settings_djonly(ctx, on)
            if on is not None:
                player.save_settings()


@command_settings.command(name='djrole')
@app_commands.describe(role='The role to set as the new DJ role')
async def command_settings_djrole(ctx, role: typing.Optional[discord.Role] = None):
    '''Query or set the role that Bluez recognizes as the DJ role in this server'''
    player = (await get_player(ctx))
    if player is not None:
        if (role is None) or (await player.ensure_admin(ctx)):
            await player.settings_djrole(ctx, role)
            if role is not None:
                player.save_settings()


@command_settings.command(name='alwaysplaying')
@app_commands.describe(on='Indicate whether Bluez should stay in voice channels permanently')
async def command_settings_alwaysplaying(ctx, on: typing.Optional[bool] = None):
    '''Query or set whether Bluez stays in voice channels and continues playing even when no one is there'''
    player = (await get_player(ctx))
    if player is not None:
        if (on is None) or (await player.ensure_admin(ctx)):
            await player.settings_alwaysplaying(ctx, on)
            if on is not None:
                player.save_settings()


@command_settings.command(name='reset', aliases=['clear'])
async def command_settings_reset(ctx):
    '''Reset all Bluez settings to their default values'''
    player = (await get_player(ctx))
    if player is not None:
        if (await player.ensure_admin(ctx)):
            await player.settings_reset(ctx)
            player.save_settings()





# effects command family

@bot.hybrid_group(name='effects', fallback='show', aliases=['effect'])
async def command_effects(ctx):
    '''List out the current audio effect settings'''
    player = (await get_player(ctx))
    if player is not None:
        if (await player.ensure_connected(ctx)):
            await player.effects_show(ctx)


@command_effects.command(name='help')
async def command_effects_help(ctx):
    '''Print descriptive info about the different audio effect settings'''
    player = (await get_player(ctx))
    if player is not None:
        await player.effects_help(ctx)


@command_effects.command(name='clear')
async def command_effects_clear(ctx):
    '''Reset all audio effects back to their defaults'''
    player = (await get_player(ctx))
    if player is not None:
        if (await player.ensure_connected(ctx)) and (await player.ensure_dj(ctx)):
            await player.effects_clear(ctx)



# individual effect settings


@bot.tree.command(name='speed')
@app_commands.describe(speed='The factor by which to speed up or slow down the playback')
async def app_speed(ctx, speed: typing.Optional[app_commands.Range[float, 0.3, 3.0]] = None):
    '''Show or adjust the playback speed'''
    if isinstance(ctx, discord.Interaction):
        ctx = (await bot.get_context(ctx))
    player = (await get_player(ctx))
    if player is not None:
        if (await player.ensure_connected(ctx)):
            if (speed is None) or (await player.ensure_dj(ctx)):
                await player.effect_speed(ctx, speed)


@bot.command(name='speed')
async def command_speed(ctx, speed: typing.Optional[float] = None):
    '''Show or adjust the playback speed'''
    if (speed is None) or (await ensure_range(ctx, speed, 'speed', 0.3, 3)):
        await app_speed.callback(ctx, speed)


@bot.hybrid_group(name='pitch', fallback='scale')
@app_commands.describe(scale='The factor by which the playback should be pitched up or down')
async def command_pitch(ctx, scale: typing.Optional[app_commands.Range[float, 0.3, 3.0]] = None):
    '''Show or adjust the playback pitch'''
    player = (await get_player(ctx))
    if player is not None:
        if (await player.ensure_connected(ctx)):
            if (scale is None) or (await player.ensure_dj(ctx)):
                await player.effect_pitch_scale(ctx, scale)


@command_pitch.command(name='steps')
@app_commands.describe(steps='The number of semitones by which the playback should be shifted up or down')
async def command_pitch_steps(ctx, steps: typing.Optional[app_commands.Range[float, -20, 20]] = None):
    '''Show or adjust the playback pitch in semitones'''
    player = (await get_player(ctx))
    if player is not None:
        if (await player.ensure_connected(ctx)):
            if (steps is None) or (await player.ensure_dj(ctx)):
                await player.effect_pitch_steps(ctx, steps)


@bot.tree.command(name='bassboost')
@app_commands.describe(bass='The level of the bass boost (1 is normal, 5 is maximal)')
async def app_bassboost(ctx, bass: typing.Optional[app_commands.Range[int, 1, 5]] = None):
    '''Show or adjust the bass-boost effect'''
    if isinstance(ctx, discord.Interaction):
        ctx = (await bot.get_context(ctx))
    player = (await get_player(ctx))
    if player is not None:
        if (await player.ensure_connected(ctx)):
            if (bass is None) or (await player.ensure_dj(ctx)):
                await player.effect_bassboost(ctx, bass)


@bot.command(name='bassboost', aliases=['bass'])
async def command_bassboost(ctx, bass: typing.Optional[int] = None):
    '''Show or adjust the bass-boost effect'''
    if (bass is None) or (await ensure_range(ctx, bass, 'bass', 1, 5)):
        await app_bassboost.callback(ctx, bass)


@bot.hybrid_command(name='nightcore', aliases=['weeb'])
@app_commands.describe(on='Indicate whether the nightcore audio effect should be turned on or off')
async def command_nightcore(ctx, on: typing.Optional[bool] = None):
    '''Toggle the nightcore effect'''
    player = (await get_player(ctx))
    if player is not None:
        if (await player.ensure_connected(ctx)) and (await player.ensure_dj(ctx)):
            await player.effect_nightcore(ctx, on)


@bot.tree.command(name='weeb')
@app_commands.describe(on='Indicate whether the nightcore audio effect should be turned on or off')
async def app_weeb(interaction, on: typing.Optional[bool] = None):
    '''Toggle the nightcore effect'''
    await command_nightcore.callback(await bot.get_context(interaction), on)


@bot.hybrid_command(name='slowed')
@app_commands.describe(on='Indicate whether the slowed audio effect should be turned on or off')
async def command_slowed(ctx, on: typing.Optional[bool] = None):
    '''Toggle the slowed effect'''
    player = (await get_player(ctx))
    if player is not None:
        if (await player.ensure_connected(ctx)) and (await player.ensure_dj(ctx)):
            await player.effect_slowed(ctx, on)


@bot.tree.command(name='volume')
@app_commands.describe(volume='The volume Bluez should play at (0 is silent, 100 is default, 200 is maximal)')
async def app_volume(ctx, volume: typing.Optional[app_commands.Range[int, 0, 200]] = None):
    '''Show or adjust the playback volume'''
    if isinstance(ctx, discord.Interaction):
        ctx = (await bot.get_context(ctx))
    player = (await get_player(ctx))
    if player is not None:
        if (await player.ensure_connected(ctx)):
            if (volume is None) or (await player.ensure_dj(ctx)):
                await player.effect_volume(ctx, volume)


@bot.command(name='volume', aliases=['vol'])
async def command_volume(ctx, volume: typing.Optional[int] = None):
    '''Show or adjust the playback volume'''
    if (volume is None) or (await ensure_range(ctx, volume, 'volume', 0, 200)):
        await app_volume.callback(ctx, volume)



# Miscellaneous other stuff


@bot.hybrid_command(name='prune', aliases=['purge', 'clean'])
@app_commands.describe(number='The number of recent bot messages in this channel to delete (0 to delete all within the last 24 hours)')
async def command_prune(ctx, number: typing.Optional[int] = None):
    '''Delete the bot's messages and commands'''
    player = (await get_player(ctx))
    if player is not None:
        if (await player.ensure_dj(ctx, need_join=False)):
            if number is None:
                number = 100
            await player.prune(ctx, number)


@bot.hybrid_command(name='aliases')
async def command_aliases(ctx):
    '''List all command aliases'''
    commands = []
    prefix = command_prefix(bot, ctx)
    for command in sorted(bot.commands, key = lambda x: x.name):
        if command.aliases:
            commands.append('%s%s - `%s`' % (prefix, command.name, ', '.join(sorted(command.aliases))))
    embeds = []
    npages = (len(commands) - 1) // 20 + 1
    for i in range(npages):
        embed = discord.Embed(title='Aliases!')
        page = commands[20*i : 20*(i+1)]
        embed.description = '\n'.join(page) + ('\n\nPage %d/%d' % (i+1, npages))
        embed.set_footer(text='Bluez, ready for your command!', icon_url=bot.user.avatar.url)
        embeds.append(embed)
    await Player.post_multipage_embed(ctx, embeds)


@bot.hybrid_command(name='help', aliases=['commands'])
async def command_help(ctx):
    '''List all supported bot commands'''
    commands = []
    prefix = command_prefix(bot, ctx)
    for command in sorted(bot.commands, key = lambda x: x.name):
        if command.aliases:
            alias = ' (also known as: `%s`)' % ', '.join(sorted(command.aliases))
        else:
            alias = ''
        signature = command.signature
        if signature: signature = ' ' + signature
        commands.append('`%s%s%s` - %s%s' % (prefix, command.name, signature, command.help, alias))
    embeds = []
    npages = (len(commands) - 1) // 10 + 1
    for i in range(npages):
        embed = discord.Embed(title='Bluez bot commands')
        page = commands[10*i : 10*(i+1)]
        embed.description = '\n\n'.join(page) + ('\n\nPage %d/%d' % (i+1, npages))
        embed.set_footer(text='Bluez, ready for your command!', icon_url=bot.user.avatar.url)
        embeds.append(embed)
    await Player.post_multipage_embed(ctx, embeds)



@bot.hybrid_command(name='ping')
async def command_ping(ctx):
    '''Check the bot's response time to Discord'''
    await ctx.send('**Howdy.** Ping time is %d ms :heartbeat:' % (bot.latency * 1000))


@bot.hybrid_command(name='info')
async def command_info(ctx):
    '''Show information about Bluez'''
    if BLUEZ_INVITE_LINK:
        invite = '\n[Invite](%s)' % BLUEZ_INVITE_LINK
    else:
        invite = ''
    embed = discord.Embed(title='About Bluez',
                          description='''Bluez is a personal-use, open source music bot implemented in Python.
[Source](%s)%s''' % (BLUEZ_SOURCE_LINK, invite))
    await ctx.send(embed=embed)


@bot.hybrid_command(name='invite', aliases=['links'])
async def command_invite(ctx):
    '''Show the links for Bluez'''
    # !invite
    if BLUEZ_INVITE_LINK:
        await ctx.send('**:link: Use this link to invite Bluez to other servers:** %s' % BLUEZ_INVITE_LINK)
    else:
        await ctx.send('**:no_entry_sign: Do not add Bluez to other servers, since it is currently in beta and strictly \
for personal use. Source code is freely available online: %s**' % BLUEZ_SOURCE_LINK)
    



