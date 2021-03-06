# Main bot class

import discord
import discord_slash
import asyncio
import re
import os
import logging
import signal
import datetime
import lyricsgenius

#from pydrive.auth import GoogleAuth
#from pydrive.drive import GoogleDrive

from bluez.player import Player
from bluez.util import *



BLUEZ_DEBUG = bool(int(os.getenv('BLUEZ_DEBUG', '0')))
BLUEZ_INVITE_LINK = os.getenv('BLUEZ_INVITE_LINK')
BLUEZ_SOURCE_LINK = os.getenv('BLUEZ_SOURCE_LINK', 'https://github.com/hukilau17/bluez')
BLUEZ_DRIVE_CREDENTIALS = os.getenv('BLUEZ_DRIVE_CREDENTIALS')
BLUEZ_RESET_TIME = os.getenv('BLUEZ_RESET_TIME')









class Bot(discord.Client):

    def __init__(self):
        intents = discord.Intents.default()
        intents.members = True
        discord.Client.__init__(self, intents=intents)
        self.players = {}
        try:
            self.genius = lyricsgenius.Genius(verbose = BLUEZ_DEBUG)
        except:
            # this should only happen if you haven't provided a token for the genius API
            self.genius = None
        self.slash = discord_slash.SlashCommand(self)
        self.drive = None
        self.init_drive()
        if BLUEZ_DEBUG:
            logging.basicConfig(level=logging.DEBUG)



    async def send(self, target, *args, **kwargs):
        if isinstance(target, discord_slash.SlashContext) and (target.deferred or target.responded):
            target = target.channel
        elif isinstance(target, discord.Message):
            target = target.channel
        if not (args or kwargs):
            if isinstance(target, discord_slash.SlashContext):
                await target.defer(hidden=True)
            return
        message = (await target.send(*args, **kwargs))
        if getattr(target, 'guild', None):
            player = self.players[target.guild.id]
            player.bot_messages.append(message)
        return message




    def run(self):
        # Run the bot
        discord.Client.run(self, os.getenv('BLUEZ_TOKEN'))



    def get_prefix(self, target):
        # Get the prefix for the given message
        if getattr(target, 'guild', None):
            return self.players[target.guild.id].prefix
        else:
            return '!'



    def init_drive(self):
        # Initialize the Google drive interface
        pass
##        if self.drive:
##            gauth = self.drive.auth
##        else:
##            if not os.path.isfile('credentials.txt'):
##                if not BLUEZ_DRIVE_CREDENTIALS:
##                    return
##                with open('credentials.txt', 'w') as o:
##                    o.write(BLUEZ_DRIVE_CREDENTIALS)
##            gauth = GoogleAuth()
##            gauth.LoadCredentialsFile('credentials.txt')
##            if not gauth.credentials:
##                logging.warning('no credentials found, drive not activated')
##                self.drive = None
##                return
##        update_drive = (self.drive is None)
##        if gauth.access_token_expired:
##            gauth.Refresh()
##            update_drive = True
##        elif update_drive:
##            gauth.Authorize()
##        if update_drive:
##            gauth.SaveCredentialsFile('credentials.txt')
##            self.drive = GoogleDrive(gauth)
        



    async def schedule_reset(self):
        # Arrange for the process to terminate itself (and then be restarted)
        # This does nothing if the environment variable BLUEZ_RESET_TIME is not defined
        if BLUEZ_RESET_TIME is not None:
            # Build a datetime object that represents the next time we're going to terminate this process
            reset_time = datetime.datetime.strptime(BLUEZ_RESET_TIME, '%H:%M:%S')
            now = datetime.datetime.utcnow()
            reset_time = datetime.datetime.combine(now.date(), reset_time.time())
            if reset_time < now:
                reset_time += datetime.timedelta(days=1)
            delta = reset_time - now
            # Sleep for the appropriate number of seconds
            logging.warning('Bluez resetting in %s' % delta)
            await asyncio.sleep(delta.total_seconds())
            # Then reset
            logging.warning('Bluez resetting now!')
            signal.raise_signal(signal.SIGTERM)
            
            



    async def command(self, command, ctx, *args, **kwargs):
        # Invoke an individual bot command with the given name, context, and arguments
        if not self.is_ready():
            return # Don't respond till we're ready
        if getattr(ctx, 'guild', None):
            player = self.players[ctx.guild.id]
            if ctx.channel in player.blacklist:
                # do not run commands in blacklisted channels
                await self.send(ctx, '**:no_entry_sign: This channel cannot be used for music commands.**')
                return
            if player.djonly and not player.is_dj(ctx.author):
                # do not run commands in DJ-only mode except from DJ users
                await self.send(ctx, '**:x: The bot is currently in DJ only mode, you must have a role named `%s` \
    or the `Manage Channels` permission to use it**' % player.djrole)
                return
        else:
            player = None
        if command in self.global_commands:
            # If it's a global command, invoke it
            await getattr(self, 'command_' + command)(ctx, *args, **kwargs)
        else:
            # If it's a bot command, invoke it
            if player is None:
                await self.send(ctx, '**:warning: This command cannot be used in private messages**')
            else:
                await getattr(player, 'command_' + command)(ctx, *args, **kwargs)
        
        
        
        
    async def on_ready(self):
        # Called when the bot comes online
        # Sets up all slash commands and creates a player for each guild
        for guild in self.guilds:
            self.players[guild.id] = Player(self, guild)
        # Schedule reset task
        self.reset_task = asyncio.create_task(self.schedule_reset())
        # Set up slash commands
        def slashfunc(command):
            return lambda ctx, *args, **kwargs: self.command(command, ctx, *args, **kwargs)
        for command in self.global_commands:
            self.slash.add_slash_command(slashfunc(command), command,
                                         description=getattr(self, 'command_' + command).__doc__,
                                         options=self.command_options.get(command, []))
        for command in self.player_commands:
            if command in self.commands_with_subcommands:
                for subcommand in self.command_options[command]:
                    self.slash.add_subcommand(slashfunc(command), command, name=subcommand['name'],
                                              description=subcommand['description'],
                                              base_description=getattr(Player, 'command_' + command).__doc__,
                                              options=subcommand['options'])
            else:
                self.slash.add_slash_command(slashfunc(command), command,
                                             description=getattr(Player, 'command_' + command).__doc__,
                                             options=self.command_options.get(command, []))
        for alias, command in self.slash_aliases.items():
            self.slash.add_slash_command(slashfunc(command), alias,
                                         description=getattr(Player, 'command_' + command).__doc__,
                                         options=self.command_options.get(command, []))
        await self.slash.sync_all_commands()



    async def on_guild_join(self, guild):
        # Called when the bot joins a guild
        self.players[guild.id] = Player(self, guild)
        await guild.text_channels[0].send('''**Thank you for adding me! :white_check_mark:**
`-` My prefix here is `!`
`-` You can see a list of my commands by typing `!help`
`-` You can change my prefix with `!settings prefix`''')



    async def on_message(self, message):
        # Reply to bot commands
        if not self.is_ready():
            return # Don't respond till we're ready
        if message.author == self.user:
            return # This bot does not reply to itself
        if getattr(message, 'guild', None):
            player = self.players[message.guild.id]
            prefix = player.prefix
        else:
            player = None
            prefix = '!'
        if self.user.mentioned_in(message):
            reply = (await message.channel.send('**Howdy.** I am definitely not a music bot. Type `%shelp` for more info.' % prefix))
            if player:
                player.bot_messages.append(reply)
        if not message.content.startswith(prefix):
            return # This is not a bot command
        if message.content == prefix:
            return # No actual command, just a prefix
        # Figure out which command it is and invoke it
        command = message.content[len(prefix):].split(None, 1)[0].lower()
        command = self.aliases.get(command, command)
        if command in self.global_commands + self.player_commands:
            if player:
                player.bot_messages.append(message)
            await self.command(command, message)




    async def on_voice_state_update(self, member, before, after):
        # Respond to users joining or leaving voice calls
        if not self.is_ready():
            return # Don't respond till we're ready
        before_guild = getattr(before.channel, 'guild', None)
        after_guild = getattr(after.channel, 'guild', None)
        if after_guild and not before_guild:
            player = self.players[after_guild.id]
            if player.voice_channel == after.channel:
                await player.notify_user_join(member)
        elif before_guild and not after_guild:
            player = self.players[before_guild.id]
            if player.voice_channel == before.channel:
                await player.notify_user_leave(member)



    async def post_multipage_embed(self, embeds, target, start_index=0):
        start_index = max(min(start_index, len(embeds)-1), 0)
        message = (await self.send(target, embed=embeds[start_index]))
        if len(embeds) > 1:
            current_page = start_index
            await message.add_reaction('\u25c0')
            await message.add_reaction('\u25b6')
            # Enter event loop to wait a certain amount of time (30 seconds) for the user to scroll through the list
            def check(reaction, user):
                return (reaction.message.id == message.id) and (reaction.emoji in ('\u25c0', '\u25b6')) and (user != self.user)
            while True:
                try:
                    reaction, user = (await self.wait_for('reaction_add', timeout=30, check=check))
                except asyncio.TimeoutError:
                    if target.guild:
                        # can't remove reactions in DMs
                        await message.clear_reaction('\u25c0')
                        await message.clear_reaction('\u25b6')
                    break
                else:
                    # Remove the reaction and advance as appropriate
                    if target.guild:
                        # can't remove reactions in DMs
                        await reaction.remove(user)
                    if reaction.emoji == '\u25c0': # page backward
                        if current_page > 0:
                            current_page -= 1
                            await message.edit(embed = embeds[current_page])
                    else: # page forward
                        if current_page < len(embeds) - 1:
                            current_page += 1
                            await message.edit(embed = embeds[current_page])
            



    aliases = {
        'summon'    : 'join',
        'p'         : 'play',
        'pt'        : 'playtop',
        'ptop'      : 'playtop',
        'ps'        : 'playskip',
        'pskip'     : 'playskip',
        'playnow'   : 'playskip',
        'pn'        : 'playskip',
        'find'      : 'search',
        'sc'        : 'soundcloud',
        'np'        : 'nowplaying',
        'save'      : 'grab',
        'yoink'     : 'grab',
        'rwd'       : 'rewind',
        'fwd'       : 'forward',
        'repeat'    : 'loop',
        'skip'      : 'voteskip',
        'next'      : 'voteskip',
        's'         : 'voteskip',
        'fs'        : 'forceskip',
        'fskip'     : 'forceskip',
        'stop'      : 'pause',
        're'        : 'resume',
        'res'       : 'resume',
        'continue'  : 'resume',
        'unpause'   : 'resume',
        'l'         : 'lyrics',
        'ly'        : 'lyrics',
        'dc'        : 'disconnect',
        'leave'     : 'disconnect',
        'dis'       : 'disconnect',
        'q'         : 'queue',
        'h'         : 'history',
        'hist'      : 'history',
        'qloop'     : 'loopqueue',
        'lq'        : 'loopqueue',
        'queueloop' : 'loopqueue',
        'm'         : 'move',
        'mv'        : 'move',
        'st'        : 'skipto',
        'jump'      : 'skipto',
        'random'    : 'shuffle',
        'rm'        : 'remove',
        'cl'        : 'clear',
        'lc'        : 'leavecleanup',
        'rmd'       : 'removedupes',
        'rd'        : 'removedupes',
        'drm'       : 'removedupes',
        'setting'   : 'settings',
        'effect'    : 'effects',
        'weeb'      : 'nightcore',
        'vol'       : 'volume',
        'purge'     : 'prune',
        'clean'     : 'prune',
        'links'     : 'invite',
        'commands'  : 'help',
        }
        
    

    global_commands = ('lyrics', 'invite', 'info', 'ping', 'aliases', 'help')

    if BLUEZ_DEBUG:
        global_commands += ('reboot',)
    
    player_commands = ('join', 'play', 'playtop', 'playskip', 'search', 'soundcloud',
                       'nowplaying', 'grab', 'seek', 'rewind', 'forward', 'replay',
                       'loop', 'voteskip', 'forceskip', 'pause', 'resume', 'history', 'back',
                       'disconnect', 'queue', 'loopqueue', 'move', 'skipto', 'shuffle',
                       'remove', 'clear', 'leavecleanup', 'removedupes', 'settings', 'effects',
                       'speed', 'pitch', 'bass', 'nightcore', 'slowed', 'volume', 'prune')



    command_syntax = {
        'lyrics':       '<name of song?>',
        'play':         '<name or url of song>',
        'playtop':      '<name or url of song>',
        'playskip':     '<name or url of song>',
        'search':       '<name of song>',
        'soundcloud':   '<name or url of song>',
        'seek':         '<time>',
        'rewind':       '<seconds>',
        'forward':      '<seconds>',
        'queue':        '<page number?>',
        'move':         '<old position> <new position?>',
        'skipto':       '<position in queue>',
        'remove':       '<positions in queue>',
        'clear':        '<user?>',
        'prune':        '<max number of messages?>',
        'speed':        '<new speed?>',
        'pitch':        '<new pitch?>',
        'bass':         '<new bass boost?>',
        'volume':       '<new volume?>',
        'effects':      '<show|help|clear?>',
        'settings':     '<name of setting|reset?> <value?>',
        }



    command_options = {
        'lyrics':       [{'name': 'song',
                          'description': 'the name of the song to show the lyrics for',
                          'type': discord_slash.SlashCommandOptionType.STRING,
                          'required': False}],
        'play':         [{'name': 'query',
                          'description': 'the name or url of the song to play',
                          'type': discord_slash.SlashCommandOptionType.STRING,
                          'required': True}],
        'playtop':      [{'name': 'query',
                          'description': 'the name or url of the song to play',
                          'type': discord_slash.SlashCommandOptionType.STRING,
                          'required': True}],
        'playskip':     [{'name': 'query',
                          'description': 'the name or url of the song to play',
                          'type': discord_slash.SlashCommandOptionType.STRING,
                          'required': True}],
        'search':       [{'name': 'query',
                          'description': 'the name of the song to seearch for',
                          'type': discord_slash.SlashCommandOptionType.STRING,
                          'required': True}],
        'soundcloud':   [{'name': 'query',
                          'description': 'the name or url of the song to play',
                          'type': discord_slash.SlashCommandOptionType.STRING,
                          'required': True}],
        'seek':         [{'name': 'time',
                          'description': 'the time to seek to',
                          'type': discord_slash.SlashCommandOptionType.STRING,
                          'required': True}],
        'rewind':       [{'name': 'time',
                          'description': 'the amount of time to rewind by',
                          'type': discord_slash.SlashCommandOptionType.STRING,
                          'required': True}],
        'forward':      [{'name': 'time',
                          'description': 'the amount of time to skip forward by',
                          'type': discord_slash.SlashCommandOptionType.STRING,
                          'required': True}],
        'forceskip':    [{'name': 'position',
                          'description': 'the position of the song to skip to',
                          'type': discord_slash.SlashCommandOptionType.INTEGER,
                          'required': False}],
        'queue':        [{'name': 'page',
                          'description': 'the page number to show',
                          'type': discord_slash.SlashCommandOptionType.INTEGER,
                          'required': False}],
        'move':         [{'name': 'old',
                          'description': 'the old position of the song to move in the queue',
                          'type': discord_slash.SlashCommandOptionType.INTEGER,
                          'required': True},
                         {'name': 'new',
                          'description': 'the new position of the song to move in the queue',
                          'type': discord_slash.SlashCommandOptionType.INTEGER,
                          'required': False}],
        'skipto':       [{'name': 'position',
                          'description': 'the position of the song to skip to',
                          'type': discord_slash.SlashCommandOptionType.INTEGER,
                          'required': True}],
        'remove':       [{'name': 'song',
                          'description': 'Remove a single song from the queue',
                          'type': discord_slash.SlashCommandOptionType.SUB_COMMAND,
                          'options': [{
                              'name': 'position',
                              'description': 'the position of the song to remove from the queue',
                              'type': discord_slash.SlashCommandOptionType.INTEGER,
                              'required': True}]},
                         {'name': 'range',
                          'description': 'Remove a range of songs from the queue',
                          'type': discord_slash.SlashCommandOptionType.SUB_COMMAND,
                          'options': [{'name': 'start',
                                       'description': 'the position of the first song to remove from the queue',
                                       'type': discord_slash.SlashCommandOptionType.INTEGER,
                                       'required': True},
                                      {'name': 'end',
                                       'description': 'the position of the last song to remove from the queue',
                                       'type': discord_slash.SlashCommandOptionType.INTEGER,
                                       'required': False}]}],
        'clear':        [{'name': 'user',
                          'description': 'clear only songs queued by this user',
                          'type': discord_slash.SlashCommandOptionType.USER,
                          'required': False}],
        'prune':        [{'name': 'number',
                          'description': 'the maximum number of messages to delete',
                          'type': discord_slash.SlashCommandOptionType.INTEGER,
                          'required': False}],
        'speed':        [{'name': 'speed',
                          'description': 'the playback speed',
                          'type': discord_slash.SlashCommandOptionType.FLOAT,
                          'required': False}],
        'pitch':        [{'name': 'scale',
                          'description': 'Scale the playback pitch up or down',
                          'type': discord_slash.SlashCommandOptionType.SUB_COMMAND,
                          'options': [{
                              'name': 'scale',
                              'description': 'the factor to multiply the playback frequency by',
                              'type': discord_slash.SlashCommandOptionType.FLOAT,
                              'required': False}]},
                         {'name': 'steps',
                          'description': 'Shift the playback pitch up or down by semitones',
                          'type': discord_slash.SlashCommandOptionType.SUB_COMMAND,
                          'options': [{
                              'name': 'steps',
                              'description': 'the number of semitones to shift the playback frequency by',
                              'type': discord_slash.SlashCommandOptionType.FLOAT,
                              'required': False}]}],
        'bass':         [{'name': 'bass',
                          'description': 'the bass intensity',
                          'type': discord_slash.SlashCommandOptionType.INTEGER,
                          'required': False}],
        'volume':       [{'name': 'volume',
                          'description': 'the playback volume',
                          'type': discord_slash.SlashCommandOptionType.INTEGER,
                          'required': False}],
        'effects':      [{'name': 'show',
                          'description': 'Show the current settings for the audio effects',
                          'type': discord_slash.SlashCommandOptionType.SUB_COMMAND,
                          'options': []},
                         {'name': 'help',
                          'description': 'Describe the available audio effects',
                          'type': discord_slash.SlashCommandOptionType.SUB_COMMAND,
                          'options': []},
                         {'name': 'clear',
                          'description': 'Reset all audio effects to default',
                          'type': discord_slash.SlashCommandOptionType.SUB_COMMAND,
                          'options': []}],
        'settings':     [{'name': 'show',
                          'description': 'Show the list of available settings',
                          'type': discord_slash.SlashCommandOptionType.SUB_COMMAND,
                          'options': []},
                         {'name': 'reset',
                          'description': 'Reset all settings to default',
                          'type': discord_slash.SlashCommandOptionType.SUB_COMMAND,
                          'options': []},
                         {'name': 'prefix',
                          'description': 'Query or change the prefix used for Bluez bot commands',
                          'type': discord_slash.SlashCommandOptionType.SUB_COMMAND,
                          'options': [{
                              'name': 'value',
                              'description': 'the bot prefix',
                              'type': discord_slash.SlashCommandOptionType.STRING,
                              'required': False}]},
                         {'name': 'blacklist',
                          'description': 'Query or change the list of channels that Bluez will ignore',
                          'type': discord_slash.SlashCommandOptionType.SUB_COMMAND,
                          'options': [{
                              'name': 'value',
                              'description': 'channel to blacklist or unblacklist',
                              'type': discord_slash.SlashCommandOptionType.CHANNEL,
                              'required': False}]},
                         {'name': 'autoplay',
                          'description': 'Query or change the autoplay link',
                          'type': discord_slash.SlashCommandOptionType.SUB_COMMAND,
                          'options': [{
                              'name': 'value',
                              'description': 'autoplay link ("disable" to turn off autoplay)',
                              'type': discord_slash.SlashCommandOptionType.STRING,
                              'required': False}]},
                         {'name': 'announcesongs',
                          'description': 'Query or change whether the bot posts every time a new song is played',
                          'type': discord_slash.SlashCommandOptionType.SUB_COMMAND,
                          'options': [{
                              'name': 'value',
                              'description': 'whether or not songs are announced',
                              'type': discord_slash.SlashCommandOptionType.BOOLEAN,
                              'required': False}]},
                         {'name': 'maxqueuelength',
                          'description': 'Query or change the maximum number of songs allowed on the queue at a time',
                          'type': discord_slash.SlashCommandOptionType.SUB_COMMAND,
                          'options': [{
                              'name': 'value',
                              'description': 'the maximum possible length of the queue (0 to disable maximum length)',
                              'type': discord_slash.SlashCommandOptionType.INTEGER,
                              'required': False}]},
                         {'name': 'maxusersongs',
                          'description': 'Query or change the maximum number of songs allowed on the queue by a single user',
                          'type': discord_slash.SlashCommandOptionType.SUB_COMMAND,
                          'options': [{
                              'name': 'value',
                              'description': 'the user song limit of the queue (0 to disable the limit)',
                              'type': discord_slash.SlashCommandOptionType.INTEGER,
                              'required': False}]},
                         {'name': 'preventduplicates',
                          'description': 'Query or change whether the bot blocks duplicate songs from being placed on the queue',
                          'type': discord_slash.SlashCommandOptionType.SUB_COMMAND,
                          'options': [{
                              'name': 'value',
                              'description': 'whether or not duplicate songs are prevented',
                              'type': discord_slash.SlashCommandOptionType.BOOLEAN,
                              'required': False}]},
                         {'name': 'defaultvolume',
                          'description': 'Query or change the default playback volume',
                          'type': discord_slash.SlashCommandOptionType.SUB_COMMAND,
                          'options': [{
                              'name': 'value',
                              'description': 'the default playback volume',
                              'type': discord_slash.SlashCommandOptionType.INTEGER,
                              'required': False}]},
                         {'name': 'djplaylists',
                          'description': 'Query or change whether the bot blocks non-DJs from queueing playlists',
                          'type': discord_slash.SlashCommandOptionType.SUB_COMMAND,
                          'options': [{
                              'name': 'value',
                              'description': 'whether or not non-DJ playlists are blocked',
                              'type': discord_slash.SlashCommandOptionType.BOOLEAN,
                              'required': False}]},
                         {'name': 'djonly',
                          'description': 'Query or change whether the bot can only be used by DJs',
                          'type': discord_slash.SlashCommandOptionType.SUB_COMMAND,
                          'options': [{
                              'name': 'value',
                              'description': 'whether or not DJ only mode is turned on',
                              'type': discord_slash.SlashCommandOptionType.BOOLEAN,
                              'required': False}]},
                         {'name': 'alwaysplaying',
                          'description': 'Query or change whether the bot is always in a voice channel',
                          'type': discord_slash.SlashCommandOptionType.SUB_COMMAND,
                          'options': [{
                              'name': 'value',
                              'description': 'whether or not the bot is always in a voice channel',
                              'type': discord_slash.SlashCommandOptionType.BOOLEAN,
                              'required': False}]},
                        ],
        }



    slash_aliases = {
        'summon'    : 'join',
        'playnow'   : 'playskip',
        'find'      : 'search',
        'save'      : 'grab',
        'repeat'    : 'loop',
        'skip'      : 'voteskip',
        'jump'      : 'skipto',
        'stop'      : 'pause',
        'continue'  : 'resume',
        'unpause'   : 'resume',
        'leave'     : 'disconnect',
        'random'    : 'shuffle',
        'weeb'      : 'nightcore',
        'purge'     : 'prune',
        'clean'     : 'prune',
        }


    commands_with_subcommands = ('effects', 'settings', 'remove', 'pitch')



    # Global commands


    async def command_aliases(self, target):
        '''List all command aliases'''
        # !aliases
        aliases = {}
        for key, value in self.aliases.items():
            if value not in aliases:
                aliases[value] = []
            aliases[value].append(key)
        commands = []
        prefix = self.get_prefix(target)
        for key in sorted(aliases):
            commands.append('%s%s - `%s`' % (prefix, key, ', '.join(sorted(aliases[key]))))
        embeds = []
        npages = (len(commands) - 1) // 20 + 1
        for i in range(npages):
            embed = discord.Embed(title='Aliases!')
            page = commands[20*i : 20*(i+1)]
            embed.description = '\n'.join(page) + ('\n\nPage %d/%d' % (i+1, npages))
            embed.set_footer(text='Bluez, ready for your command!', icon_url=self.user.avatar_url)
            embeds.append(embed)
        await self.post_multipage_embed(embeds, target)



    async def command_help(self, target):
        '''List all supported bot commands'''
        # !help
        aliases = {}
        for key, value in self.aliases.items():
            if value not in aliases:
                aliases[value] = []
            aliases[value].append(key)
        commands = []
        prefix = self.get_prefix(target)
        for command in sorted(self.player_commands + self.global_commands):
            syntax = self.command_syntax.get(command, '')
            if syntax:
                syntax = ' ' + syntax
            if command in self.global_commands:
                doc = getattr(self, 'command_' + command).__doc__
            else:
                doc = getattr(Player, 'command_' + command).__doc__
            alias = aliases.get(command, '')
            if alias:
                alias = ' (also known as: `%s`)' % ', '.join(sorted(alias))
            commands.append('`%s%s%s` - %s%s' % (prefix, command, syntax, doc, alias))
        embeds = []
        npages = (len(commands) - 1) // 10 + 1
        for i in range(npages):
            embed = discord.Embed(title='Bluez bot commands')
            page = commands[10*i : 10*(i+1)]
            embed.description = '\n\n'.join(page) + ('\n\nPage %d/%d' % (i+1, npages))
            embed.set_footer(text='Bluez, ready for your command!', icon_url=self.user.avatar_url)
            embeds.append(embed)
        await self.post_multipage_embed(embeds, target)
            

        


    async def command_ping(self, target):
        '''Check the bot's response time to Discord'''
        # !ping
        await self.send(target, '**Howdy.** Ping time is %d ms :heartbeat:' % (self.latency * 1000))


    async def command_info(self, target):
        '''Show information about Bluez'''
        # !info
        if BLUEZ_INVITE_LINK:
            invite = '\n[Invite](%s)' % BLUEZ_INVITE_LINK
        else:
            invite = ''
        embed = discord.Embed(title='About Bluez',
                              description='''Bluez is a personal-use, open source music bot implemented in Python.
[Source](%s)%s''' % (BLUEZ_SOURCE_LINK, invite))
        await self.send(target, embed=embed)


    async def command_invite(self, target):
        '''Show the links for Bluez'''
        # !invite
        if BLUEZ_INVITE_LINK:
            await self.send(target, '**:link: Use this link to invite Bluez to other servers:** %s' % BLUEZ_INVITE_LINK)
        else:
            await self.send(target, '**:no_entry_sign: Do not add Bluez to other servers, since it is currently in beta and strictly \
for personal use. Source code is freely available online: %s**' % BLUEZ_SOURCE_LINK)



    async def command_reboot(self, target):
        '''Reboot the Bluez bot (available in debug mode only)'''
        # !reboot
        await self.send(target, '**:bomb: Rebooting now, be back soon!**')
        signal.raise_signal(signal.SIGTERM)



    async def command_lyrics(self, target, song=None):
        '''Get the lyrics of a song (by default the currently playing song)'''
        # !lyrics
        if self.genius is None:
            await self.send(target, '**:x: Lyric searching is not enabled.**')
            return
        prefix = self.get_prefix(target)
        if isinstance(target, discord.Message):
            try:
                song = target.content[len(prefix):].split(None, 1)[1]
            except IndexError:
                song = None
        is_now_playing = False
        artist = ''
        if not song:
            if getattr(target, 'guild', None):
                player = self.players[target.guild.id]
                if (await player.ensure_playing(target.author, target)):
                    song = (player.now_playing.track or player.now_playing.name)
                    artist = (player.now_playing.artist or '')
                    is_now_playing = True
                else:
                    return
            else:
                # this is a DM
                await self.send(target, '**:x: I am not currently playing anything.**')
                return
        song_name = song
        if artist:
            await self.send(target, '**:mag: Searching lyrics for `%s` by `%s`**' % (song_name, artist))
        else:
            await self.send(target, '**:mag: Searching lyrics for `%s`**' % song_name)
        song = self.genius.search_song(song_name, artist)
        if is_now_playing and (not song) and (not artist):
            # Sometimes song titles on YouTube videos contain too much information (e.g. "Official Audio/Video")
            # that makes Genius fail to return a meaningful result. This is a really cheap attempt to lower the probability
            # of that happening.
            m = re.search(r'[()\[\]|]', song_name)
            if m:
                song_name = song_name[:m.start()] # "artist - song (official audio)" becomes just "artist - song"
                song = self.genius.search_song(song_name)
        if song:
            lyrics = song.lyrics
            m = re.search(r'\d*EmbedShare', lyrics)
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
                embed.set_footer(text='Requested by %s' % format_user(target.author), icon_url=target.author.avatar_url)
                embeds.append(embed)
            await self.post_multipage_embed(embeds, target)
        else:
            await self.send(target, '**:x: There were no results matching the query**')













