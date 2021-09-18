# Main bot class

import discord
import asyncio
import os
import logging
import lyricsgenius
import discord_slash

from bluez.player import Player
from bluez.util import BLUEZ_DEBUG









class Bot(discord.Client):

    def __init__(self):
        intents = discord.Intents.default()
        intents.members = True
        discord.Client.__init__(self, intents=intents)
        self.players = {}
        self.genius = lyricsgenius.Genius()
        self.slash = discord_slash.SlashCommand(self)
        if BLUEZ_DEBUG:
            logging.basicConfig(level=logging.DEBUG)
        


    def run(self):
        # Run the bot
        discord.Client.run(self, os.getenv('BLUEZ_TOKEN'))
        


    async def on_ready(self):
        # Called when the bot comes online
        # Sets up all slash commands and creates a player for each guild
        for guild in self.guilds:
            self.players[guild.id] = Player(self, guild)
        for command in self.global_commands:
            self.slash.add_slash_command(getattr(self, 'command_' + command), command, options=self.command_options.get(command))
        for command in self.player_commands:
            self.slash.add_slash_command(lambda ctx, *args: getattr(self.players[ctx.guild.id], 'command_' + command), command,
                                         options=self.command_options.get(command))
        for alias, command in self.slash_aliases.items():
            self.slash.add_slash_command(lambda ctx, *args: getattr(self.players[ctx.guild.id], 'command_' + command), alias,
                                         options=self.command_options.get(command))
        await self.slash.sync_all_commands()


    async def on_guild_join(self, guild):
        # Called when the bot joins a guild
        self.players[guild.id] = Player(self, guild)



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
            await message.channel.send('**Howdy.**')
        if not message.content.startswith(prefix):
            return # This is not a bot command
        if player and (message.channel in player.blacklist):
            await message.channel.send('**:no_entry_sign: This channel cannot be used for music commands.**')
            return # Do not respond in blacklisted channels
        if player and player.djonly and not player.is_dj(message.author):
            await message.channel.send('**:x: The bot is currently in DJ only mode, you must have a role named `%s` \
or the `Manage Channels` permission to use it**' % player.djrole)
        command = message.content[len(prefix):].split(None, 1)[0].lower()
        command = self.aliases.get(command, command)
        if command in self.global_commands:
            # If it's a global command, invoke it
            await getattr(self, 'command_' + command)(message)
        if command in self.player_commands:
            # If it's a bot command, invoke it
            if player is None:
                await message.channel.send('**:warning: This command cannot be used in private messages**')
            else:
                await getattr(player, 'command_' + command)(message)




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



    async def post_multipage_embed(self, embeds, channel, start_index=0):
        start_index = max(min(start_index, len(embeds)-1), 0)
        message = (await channel.send(embed=embeds[start_index]))
        if len(embeds) > 1:
            current_page = start_index
            await message.add_reaction('\u25c0')
            await message.add_reaction('\u25b6')
            # Enter event loop to wait a certain amount of time (30 seconds) for the user to scroll through the list
            def check(reaction, user):
                return (reaction.message == message) and (reaction.emoji in ('\u25c0', '\u25b6')) and (user != self.user)
            while True:
                try:
                    reaction, user = (await self.wait_for('reaction_add', timeout=30, check=check))
                except asyncio.TimeoutError:
                    await message.clear_reaction('\u25c0')
                    await message.clear_reaction('\u25b6')
                    break
                else:
                    # Remove the reaction and advance as appropriate
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
        'l'         : 'lyrics',
        'ly'        : 'lyrics',
        'dc'        : 'disconnect',
        'leave'     : 'disconnect',
        'dis'       : 'disconnect',
        'q'         : 'queue',
        'qloop'     : 'loopqueue',
        'lq'        : 'loopqueue',
        'queueloop' : 'loopqueue',
        'm'         : 'move',
        'mv'        : 'move',
        'st'        : 'skipto',
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
        'debug'     : 'shard',
        }
        
    

    global_commands = ('lyrics', 'invite', 'info', 'ping', 'aliases') # mostly unimplemented/unnecessary for now
    
    player_commands = ('join', 'play', 'playtop', 'playskip', 'search', 'soundcloud',
                       'nowplaying', 'grab', 'seek', 'rewind', 'forward', 'replay',
                       'loop', 'voteskip', 'forceskip', 'pause', 'resume',
                       'disconnect', 'queue', 'loopqueue', 'move', 'skipto', 'shuffle',
                       'remove', 'clear', 'leavecleanup', 'removedupes', 'settings', 'effects',
                       'speed', 'bass', 'nightcore', 'slowed', 'volume', 'prune')


    command_options = {
        'lyrics':       [{'name': 'song',
                          'description': 'the name of the song to show the lyrics for',
                          'type': discord_slash.SlashCommandOptionType.STRING,
                          'required': False,
                          'choices': []}],
        'play':         [{'name': 'query',
                          'description': 'the name or url of the song to play',
                          'type': discord_slash.SlashCommandOptionType.STRING,
                          'required': True,
                          'choices': []}],
        'playtop':      [{'name': 'query',
                          'description': 'the name or url of the song to play',
                          'type': discord_slash.SlashCommandOptionType.STRING,
                          'required': True,
                          'choices': []}],
        'playskip':     [{'name': 'query',
                          'description': 'the name or url of the song to play',
                          'type': discord_slash.SlashCommandOptionType.STRING,
                          'required': True,
                          'choices': []}],
        'search':       [{'name': 'query',
                          'description': 'the name of the song to seearch for',
                          'type': discord_slash.SlashCommandOptionType.STRING,
                          'required': True,
                          'choices': []}],
        'soundcloud':   [{'name': 'query',
                          'description': 'the name or url of the song to play',
                          'type': discord_slash.SlashCommandOptionType.STRING,
                          'required': True,
                          'choices': []}],
        'seek':         [{'name': 'time',
                          'description': 'the time to seek to',
                          'type': discord_slash.SlashCommandOptionType.STRING,
                          'required': True,
                          'choices': []}],
        'rewind':       [{'name': 'time',
                          'description': 'the amount of time to rewind by',
                          'type': discord_slash.SlashCommandOptionType.STRING,
                          'required': True,
                          'choices': []}],
        'forward':      [{'name': 'time',
                          'description': 'the amount of time to skip forward by',
                          'type': discord_slash.SlashCommandOptionType.STRING,
                          'required': True,
                          'choices': []}],
        'queue':        [{'name': 'page',
                          'description': 'the page number to show',
                          'type': discord_slash.SlashCommandOptionType.INTEGER,
                          'required': False,
                          'choices': []}],
        'move':         [{'name': 'old',
                          'description': 'the old position of the song to move in the queue',
                          'type': discord_slash.SlashCommandOptionType.INTEGER,
                          'required': True,
                          'choices': []},
                         {'name': 'new',
                          'description': 'the new position of the song to move in the queue',
                          'type': discord_slash.SlashCommandOptionType.INTEGER,
                          'required': False,
                          'choices': []}],
        'skipto':       [{'name': 'position',
                          'description': 'the position of the song to skip to',
                          'type': discord_slash.SlashCommandOptionType.INTEGER,
                          'required': True,
                          'choices': []}],
        'remove':       [{'name': 'number',
                          'description': 'the position of the song to remove from the queue',
                          'type': discord_slash.SlashCommandOptionType.INTEGER,
                          'required': True,
                          'choices': []}],
        'clear':        [{'name': 'user',
                          'description': 'clear only songs queued by this user',
                          'type': discord_slash.SlashCommandOptionType.USER,
                          'required': False,
                          'choices': []}],
        'speed':        [{'name': 'speed',
                          'description': 'the playback speed',
                          'type': discord_slash.SlashCommandOptionType.FLOAT,
                          'required': False,
                          'choices': []}],
        'bass':         [{'name': 'bass',
                          'description': 'the bass intensity',
                          'type': discord_slash.SlashCommandOptionType.INTEGER,
                          'required': False,
                          'choices': []}],
        'volume':       [{'name': 'volume',
                          'description': 'the playback volume',
                          'type': discord_slash.SlashCommandOptionType.INTEGER,
                          'required': False,
                          'choices': []}],
        }



    slash_aliases = {
        'summon'    : 'join',
        'playnow'   : 'playskip',
        'find'      : 'search',
        'save'      : 'grab',
        'repeat'    : 'loop',
        'skip'      : 'voteskip',
        'stop'      : 'pause',
        'continue'  : 'resume',
        'dc'        : 'disconnect',
        'leave'     : 'disconnect',
        'random'    : 'shuffle',
        'weeb'      : 'nightcore',
        'purge'     : 'prune',
        'clean'     : 'prune',
        }



    # Global commands


    async def command_aliases(self, message):
        '''List all command aliases'''
        # !aliases
        aliases = {}
        for key, value in self.aliases.items():
            if value not in aliases:
                aliases[value] = []
            aliases[value].append(key)
        commands = []
        for key in sorted(aliases):
            commands.append('!%s - `%s`' % (key, ', '.join(sorted(aliases[key]))))
        embeds = []
        npages = (len(commands) - 1) // 20 + 1
        for i in range(npages):
            embed = discord.Embed(title='Aliases!')
            page = commands[20*i : 20*(i+1)]
            embed.description = '\n'.join(page) + ('\n\nPage %d/%d' % (i+1, npages))
            embeds.append(embed)
        await self.post_multipage_embed(embeds, message.channel)


    async def command_ping(self, message):
        '''Check the bot's response time to Discord'''
        # !ping
        await message.channel.send('**Howdy.**')


    async def command_info(self, message):
        '''Show information about Bluez'''
        # !info
        embed = discord.Embed(title='About Bluez',
                              description='''Bluez is a personal-use, open source music bot implemented in Python by Matthew Kroesche.
[Source](https://github.com/hukilau17/bluez)''')
        await message.channel.send(embed=embed)


    async def command_invite(self, message):
        '''Show the source link for Bluez'''
        # !invite
        await message.channel.send('**:no_entry_sign: Do not add Bluez to other servers, since it is currently in beta and strictly \
for personal use. Source code is freely available online: `https://github.com/hukilau17/bluez`**')



    async def command_lyrics(self, message, search_term=None):
        '''Get the lyrics of a song (by default the currently playing song)'''
        # !lyrics
        if getattr(message, 'guild', None):
            player = self.players[message.guild.id]
            prefix = player.prefix
        else:
            player = None
            prefix = '!'
        if (search_term is None) and isinstance(message, discord.Message):
            try:
                search_term = message.content[len(prefix):].split(None, 1)[1]
            except IndexError:
                search_term = None
        if not search_term:
            if player:
                if (await player.ensure_playing(message.author, message.channel)):
                    search_term = player.now_playing.name
                else:
                    return
            else:
                await message.channel.send('**:x: I am not currently playing anything.**')
                return
        song = self.genius.search_song(search_term)
        if song:
            embed = discord.Embed(title='%s - %s' % (song.artist, song.title),
                                  description=song.lyrics)
            embed.set_thumbnail(url=song.song_art_image_thumbnail_url)
            await message.channel.send(embed=embed)
        else:
            await message.channel.send('**:x: There were no results matching the query**')













