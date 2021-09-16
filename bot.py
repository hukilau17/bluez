# Main bot class

import discord
import asyncio
import os

from bluez.player import Player










class Bot(discord.Client):

    def __init__(self):
        intents = discord.Intents.default()
        intents.members = True
        discord.Client.__init__(self, intents=intents)
        self.players = {}
        

    def run(self):
        # Run the bot
        discord.Client.run(self, os.getenv('BLUEZ_TOKEN'))


    async def on_ready(self):
        for guild in self.guilds:
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
        command = message.content[len(player.prefix):].split(None, 1)[0].lower()
        command = self.aliases.get(command, command)
        if command in self.global_commands:
            # If it's a global command, invoke it
            if hasattr(self, 'command_' + command):
                await getattr(self, 'command_' + command)(message)
            else:
                await message.channel.send('**:warning: This command is not supported yet.**')
        if command in self.player_commands:
            # If it's a bot command, invoke it
            if player is None:
                await message.channel.send('**:warning: This command cannot be used in private messages**')
            elif hasattr(player, 'command_' + command):
                await getattr(player, 'command_' + command)(message)
            else:
                await message.channel.send('**:warning: This command is not supported yet.**')




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



    async def post_multipage_embed(self, embeds, channel):
        message = (await channel.send(embed=embeds[0]))
        if len(embeds) > 1:
            current_page = 0
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
        
    

    global_commands = ('lyrics', 'invite', 'info', 'shard', 'ping', 'aliases') # mostly unimplemented/unnecessary for now
    
    player_commands = ('join', 'play', 'playtop', 'playskip', 'search', 'soundcloud',
                       'nowplaying', 'grab', 'seek', 'rewind', 'forward', 'replay',
                       'loop', 'voteskip', 'forceskip', 'pause', 'resume',
                       'disconnect', 'queue', 'loopqueue', 'move', 'skipto', 'shuffle',
                       'remove', 'clear', 'leavecleanup', 'removedupes', 'settings', 'effects',
                       'speed', 'bass', 'nightcore', 'slowed', 'volume', 'prune')



    async def command_aliases(self, message):
        aliases = {}
        for key, value in self.aliases:
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
        await message.channel.send('**Howdy.**')





