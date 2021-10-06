# Music player class

import discord
import discord_slash
import asyncio
import re
import random
import collections
import time
import io
import struct

from bluez.song import *
from bluez.util import *





class Player(object):

    def __init__(self, client, guild):
        self.client = client
        self.guild = guild
        self.reset_settings()
        self.reset()
        self.load_settings()


    # Functions for initializing/resetting the bot's state

    def reset_settings(self):
        self.prefix = '!'
        self.announcesongs = False
        self.preventduplicates = False
        self.blacklist = []
        self.maxqueuelength = None
        self.maxusersongs = None
        self.djonly = False
        self.djrole = 'DJ'
        self.djplaylists = False
        self.defaultvolume = 0.5
        self.autoplay = None
        self.alwaysplaying = False


    def reset_effects(self):
        self.tempo = 1.0
        self.pitch = 1.0
        self.bass = 1
        self.nightcore = False
        self.slowed = False
        self.volume = self.defaultvolume


    def reset(self):
        self.text_channel = None
        self.voice_channel = None
        self.voice_client = None
        self.now_playing = None
        self.queue = collections.deque()
        self.looping = False
        self.queue_looping = False
        self.votes = []
        self.empty_paused = False
        self.idle_task = None
        self.last_started_playing = None
        self.last_paused = None
        self.bot_messages = []
        self.searching_channels = []
        self.seek_pos = None
        self.reset_effects()



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
        self.bot_messages.append(message)
        return message
        



    # Coroutines to ensure that a certain condition is met before proceeding further

    async def ensure_connected(self, member, target):
        # Make sure the bot has joined some voice channel
        if self.voice_channel is None:
            await self.send(target, '**:x: I am not connected to a voice channel.** Type `%sjoin` to get me in one' % self.prefix)
            return False
        return True


    async def ensure_playing(self, member, target):
        # Make sure the bot is currently playing something
        if not (await self.ensure_connected(member, target)):
            return False
        if self.now_playing is None:
            await self.send(target, '**:x: I am not currently playing anything.** Type `%splay` to play a song' % self.prefix)
            return False
        return True


    async def ensure_queue(self, member, target):
        # Make sure the bot has some songs queued
        if not (await self.ensure_connected(member, target)):
            return False
        if not self.queue:
            await self.send(target, '**:x: The queue is currently empty.** Type `%splay` to play a song' % self.prefix)
            return False
        return True


    async def ensure_joined(self, member, target):
        # Make sure the given member has joined the voice channel that the bot is in
        if self.voice_channel is None:
            # First we need to connect to a voice channel
            if member.voice and member.voice.channel:
                await self.connect(target, member.voice.channel)
                return True
        elif member in self.voice_channel.members:
            return True
        if self.voice_channel is None:
            await self.send(target, '**:x: You have to be in a voice channel to use this command.**')
        elif len(self.voice_channel.members) == 1:
            # if the bot is by itself, you can steal it
            await self.disconnect()
            await self.connect(target, member.voice.channel)
            return True
        else:
            await self.send(target, '**:x: You have to be in the same voice channel with the bot to use this command.**')
        return False


    def is_dj(self, member):
        return member.guild_permissions.manage_channels or \
               discord.utils.get(member.roles, name=self.djrole) or \
               discord.utils.get(member.roles, name='DJ')


    async def ensure_dj(self, member, target, need_join=True):
        # Make sure the given member has the DJ role. (Also ensures they are in the right channel if necessary.)
        ensure = (self.ensure_joined if need_join else self.ensure_connected)
        if not (await ensure(member, target)):
            return False
        for voice_member in self.voice_channel.members:
            if (voice_member != member) and (voice_member != self.client.user):
                break
        else:
            return True # This user is alone with the bot
        if self.is_dj(member):
            return True
        await self.send(target, '**:x: This command requires you to either have a role named DJ or the Manage Channels permission to use it** \
(being alone with the bot also works)')
        return False



    ##### Simple coroutines #####


    async def connect(self, target, voice_channel):
        # Connect to a voice channel
        self.reset()
        self.voice_client = (await voice_channel.connect())
        text_channel = target
        if not isinstance(text_channel, discord.TextChannel):
            text_channel = text_channel.channel
        self.text_channel = text_channel
        self.voice_channel = voice_channel
        await self.send(target, '**:thumbsup: Joined `%s` and bound to %s**' % \
                        (voice_channel.name, text_channel.mention))
        if self.autoplay:
            try:
                songs = (await songs_from_url(self.autoplay, self.client.user))
            except Exception as e:
                await self.send(target, '**:x: Error playing songs from `%s`: `%s`**' % (self.autoplay, e))
            else:
                self.queue.extend(songs)
                random.shuffle(self.queue)
                await self.enqueue_message(0, songs, target)
                await self.wake_up()


    async def disconnect(self, target=None):
        # Leave the voice channel
        if self.voice_channel is not None:
            client = self.voice_client
            self.reset()
            await client.disconnect()
            if target:
                await self.send(target, '**:mailbox_with_no_mail: Successfully disconnected**')


    async def play_next(self, error=None):
        # Play the next song from the queue, if it exists
        # Should only be called when nothing is currently playing
        self.votes = []
        if self.voice_client is not None:
            if self.now_playing and error:
                errmsg = (await self.text_channel.send('**:x: Error playing `%s`: `%s`**' % (self.now_playing.name, error)))
                self.bot_messages.append(errmsg)
            if self.seek_pos is None:
                if self.looping and (self.now_playing is not None):
                    pass
                elif self.queue:
                    self.now_playing = self.queue.popleft()
                    if self.queue_looping:
                        self.queue.append(self.now_playing)
                else:
                    self.now_playing = None
                    self.last_started_playing = None
                    self.last_paused = None
                    return
            if self.now_playing:
                source = self.now_playing.get_audio(self.seek_pos or 0, self.tempo, self.pitch, self.bass,
                                                    self.nightcore, self.slowed, self.volume)
                if isinstance(source, Exception):
                    await self.play_next(source)
                    return
                self.voice_client.play(source, after=self._play_next_callback)
                self.last_started_playing = time.time() - (self.seek_pos or 0)
                self.last_paused = None
                announce = (self.announcesongs and (self.seek_pos is None))
                self.seek_pos = None
                if announce:
                    await self.np_message(self.text_channel)
        else:
            self.now_playing = None
            self.last_started_playing = None
            self.last_paused = None



    def _play_next_callback(self, error):
        # Callback for play_next()
        self._play_next_task = self.client.loop.create_task(self.play_next(error))



    async def skip(self, target):
        # Skip to the next song on the queue.
        # Does the same thing as play_next() if there's not
        # currently a song playing.
        if self.voice_client is not None:
            if self.voice_client.is_playing() or self.voice_client.is_paused():
                self.voice_client.stop()
                await self.send(target, '***:fast_forward: Skipped :thumbsup:***')
            else:
                await self.play_next()


    async def wake_up(self):
        # Play a song if nothing is currently playing
        # Do nothing if there's already a song playing
        if not (self.voice_client.is_playing() or self.voice_client.is_paused()):
            await self.play_next()



    async def seek(self, pos, target):
        # Seek to the given position in the currently playing song
        if (self.voice_client is not None) and (self.voice_client.is_playing() or self.voice_client.is_paused()):
            self.seek_pos = pos
            self.voice_client.stop()
            await self.send(target, '**:thumbsup: Seeking to time `%s`**' % format_time(pos))


    def update_audio(self):
        # Called when the audio effects (volume, speed, bass, etc.) are changed
        # Effectively the same as a "seek" to the current time
        if (self.voice_client is not None) and (self.voice_client.is_playing() or self.voice_client.is_paused()):
            self.seek_pos = self.get_current_time()
            self.seek_pos *= self.now_playing.tempo / self.now_playing.get_adjusted_tempo(self.tempo, self.nightcore, self.slowed)
            self.voice_client.stop()


    def get_current_time(self):
        # Get the number of seconds since the most recent track started
        if self.last_paused is not None:
            return self.last_paused - self.last_started_playing
        elif self.last_started_playing is not None:
            return time.time() - self.last_started_playing


    async def np_message(self, target):
        # Send the now_playing message to the appropriate channel
        if self.now_playing:
            song = self.now_playing
            time = self.get_current_time()
            progress_bar = ['\u25ac'] * 30
            if (time is not None) and song.adjusted_length:
                progress_bar[min(int((time * 30) / song.adjusted_length), 29)] = '\U0001f518'
            progress_bar = '`%s`' % ''.join(progress_bar)
            if time is None:
                time_message = 'Not started yet'
            else:
                time_message = '%s / %s' % (format_time(time), format_time(song.adjusted_length))
                if self.voice_client.is_paused():
                    time_message += ' (paused)'
            time_message = '`%s`' % time_message
            embed = discord.Embed(description = \
                                  format_link(song) + '\n\n' + \
                                  progress_bar + '\n\n' + \
                                  time_message + '\n\n' + \
                                  '`Requested by:` ' + format_user(song.user),
                                  color=discord.Color.blue())
            if hasattr(target, 'author'):
                icon_url = target.author.avatar_url
            elif hasattr(target, 'avatar_url'):
                icon_url = target.avatar_url
            else:
                icon_url = self.client.user.avatar_url
            embed.set_author(name='Now Playing \u266a', icon_url=icon_url)
            if song.thumbnail:
                embed.set_thumbnail(url=song.thumbnail)
            await self.send(target, embed=embed)



    async def queue_message(self, target, start_index=0):
        # Post the queue to the appropriate channel
        if not (await self.ensure_queue(None, target)):
            return
        n = len(self.queue)
        npages = (n - 1) // 10 + 1
        total = format_time(sum([i.length for i in self.queue]))
        embeds = []
        color = discord.Color.random()
        for i in range(npages):
            embed = discord.Embed(title='Queue for %s' % target.guild.name, color=color)
            description = ''
            if i == 0:
                if self.now_playing:
                    description += '__Now Playing:__\n%s | `%s Requested by %s`\n\n' % \
                                   (format_link(self.now_playing), format_time(self.now_playing.adjusted_length),
                                    format_user(self.now_playing.user))
                description += '__Up Next:__\n'
            for j, song in enumerate(tuple(self.queue)[10*i : 10*(i+1)], 10*i+1):
                description += '`%d.` %s | `%s Requested by %s`\n\n' % \
                               (j, format_link(song), format_time(song.length),
                                format_user(song.user))
            description += '**%d songs in queue | %s total length**\n\n' % (n, total)
            embed.description = description
            footer = 'Page %d/%d | Loop: %s | Queue Loop: %s' % \
                           (i+1, npages,
                            '\u2705' if self.looping else '\u274c',
                            '\u2705' if self.queue_looping else '\u274c')
            embed.set_footer(text=footer,
                             icon_url=target.author.avatar_url)
            embeds.append(embed)
        await self.client.post_multipage_embed(embeds, target, start_index)



    async def enqueue_message(self, position, songs, target, now=False):
        # Send info on the recently enqueued song(s) to the appropriate channel
        if songs:
            if now:
                time = position = 'Now'
            else:
                time = sum([i.length for i in tuple(self.queue)[:position]])
                if self.now_playing is not None:
                    time += max(self.now_playing.adjusted_length - self.get_current_time(), 0)
                if time == 0:
                    if self.now_playing and not self.now_playing.length:
                        time = 'Unknown'
                        position = str(position + 1)
                    else:
                        time = position = 'Now'
                else:
                    time = format_time(time)
                    position = str(position + 1)
            if len(songs) > 1:
                # This is a playlist
                embed = discord.Embed(description=format_link(songs))
                embed.set_author(name='Playlist added to queue', icon_url=target.author.avatar_url)
                embed.add_field(name='Estimated time until playing', value=time, inline=False)
                embed.add_field(name='Position in queue', value=position, inline=True)
                embed.add_field(name='Enqueued', value='`%d` song%s' % (len(songs), '' if len(songs) == 1 else 's'), inline=True)
            elif time != 'Now':
                embed = discord.Embed(description=format_link(songs[0]))
                embed.set_author(name='Added to queue', icon_url=target.author.avatar_url)
                embed.add_field(name='Channel', value=songs[0].channel, inline=True)
                embed.add_field(name='Song Duration', value=format_time(songs[0].length), inline=True)
                embed.add_field(name='Estimated time until playing', value=time, inline=True)
                embed.add_field(name='Position in queue', value=position, inline=False)
                if songs[0].thumbnail:
                    embed.set_thumbnail(url=songs[0].thumbnail)
            else:
                # This is a single song being played immediately
                await self.send(target, '**Playing :notes: `%s` - Now!**' % songs[0].name)
                return
            await self.send(target, embed=embed)



    async def songs_from_query(self, query, target, soundcloud=False):
        # Return a list of Song objects matching a query,
        # which can be either a URL or a search term
        if is_url(query):
            try:
                return (await songs_from_url(query, target.author))
            except Exception as e:
                await self.send(target, '**:x: Error playing songs from `%s`: `%s`**' % (query, e))
                return []
        else:
            await self.send(target, '**:arrow_forward: Searching :mag: `%s`**' % query)
            return (await songs_from_search(query, target.author, 1, soundcloud))
        
        
            
                      

    
            
        


    


    ##### Bot command implementation #####

    # The argument `target` is either a discord.Message (if the command is invoked in the
    # traditional way using the prefix) or a discord_slash.SlashContext (if the command
    # is invoked by a slash command)


    async def command_join(self, target):
        '''Summon the bot to the voice channel you are in'''
        # !join
        await self.ensure_joined(target.author, target)
        await self.send(target)


    async def command_play(self, target, query=None):
        '''Play a song with the given name or url'''
        # !play
        if (await self.ensure_joined(target.author, target)):
            query = self.get_string(target, query)
            if not query:
                await self.usage_embed('%splay [Link or query]' % self.prefix, target)
                return
            songs = (await self.songs_from_query(query, target))
            songs = (await self.trim_songs(songs, target))
            if not songs:
                return
            n = len(self.queue)
            self.queue.extend(songs)
            await self.enqueue_message(n, songs, target)
            await self.wake_up()


    async def command_playtop(self, target, query=None):
        '''Add a song with the given name/url to the top of the queue'''
        # !playtop
        ensure = (self.ensure_dj if self.queue else self.ensure_joined)
        # you need DJ permissions to insert music into the queue ahead of other people's songs,
        # but not if the queue is empty
        if (await ensure(target.author, target)):
            query = self.get_string(target, query)
            if not query:
                await self.usage_embed('%splaytop [Link or query]' % self.prefix, target)
                return
            songs = (await self.songs_from_query(query, target))
            songs = (await self.trim_songs(songs, target))
            if not songs:
                return
            self.queue.extendleft(songs[::-1])
            await self.enqueue_message(0, songs, target)
            await self.wake_up()


    async def command_playskip(self, target, query=None):
        '''Skip the current song and play the song with the given name/url'''
        # !playskip
        ensure = (self.ensure_dj if self.queue or self.now_playing else self.ensure_joined)
        # you need DJ permissions to insert music into the queue ahead of other people's songs,
        # or to skip other people's songs
        if (await ensure(target.author, target)):
            query = self.get_string(target, query)
            if not query:
                await self.usage_embed('%splayskip [Link or query]' % self.prefix, target)
                return
            songs = (await self.songs_from_query(query, target))
            songs = (await self.trim_songs(songs, target))
            if not songs:
                return
            self.queue.extendleft(songs[::-1])
            await self.enqueue_message(0, songs, target, now=True)
            await self.skip(target)


    async def command_search(self, target, query=None):
        '''Search from YouTube for a song using the query, and return the top 10 results'''
        # !search
        if (await self.ensure_joined(target.author, target)):
            if target.channel in self.searching_channels:
                await self.send(target, '**:warning: Search is already running in this channel, type `cancel` to exit**')
                return
            query = self.get_string(target, query)
            if not query:
                await self.usage_embed('%ssearch [query]' % self.prefix, target)
                return
            await self.send(target, '**:arrow_forward: Searching :mag: `%s`**' % query)
            songs = (await songs_from_search(query, target.author, 10))
            if songs:
                # Print out an embed of the songs
                description = '\n\n'.join(['`%d.` %s **[%s]**' % (i+1, format_link(song), format_time(song.length)) for i, song in enumerate(songs)])
                description += '\n\n\n\n**Type a number to make a choice, Type `cancel` to exit**'
                embed = discord.Embed(description=description)
                embed.set_author(name=(target.author.nick or target.author.name), icon_url=target.author.avatar_url)
                embed_message = (await self.send(target, embed=embed))
            else:
                # No results
                await self.send(target, '**:x: There were no results matching the query**')
                return
            # Wait for the user who made the search query to reply
            def check(m):
                if (m.channel == target.channel) and (m.author == target.author):
                    return m.content.strip().lower() in ('cancel',) + tuple(map(str, range(1, len(songs)+1)))
            self.searching_channels.append(target.channel)
            try:
                result = (await self.client.wait_for('message', check=check, timeout=30))
            except asyncio.TimeoutError:
                await self.send(target, '**:no_entry_sign: Timeout**')
                result = None
            self.searching_channels.remove(target.channel)
            await embed_message.delete()
            self.bot_messages.remove(embed_message)
            if result is None:
                return
            m = result.content.strip().lower()
            if m == 'cancel':
                await self.send(target, ':white_check_mark:')
                return
            song = songs[int(m) - 1]
            if not (await self.trim_songs([song], target)):
                return # the user can't queue this song for some reason
            song.process()
            self.queue.append(song)
            await self.enqueue_message(len(self.queue) - 1, [song], target)
            await self.wake_up()
            


    async def command_soundcloud(self, target, query=None):
        '''Play a song from SoundCloud with the given name/url'''
        # !soundcloud
        if (await self.ensure_joined(target.author, target)):
            query = self.get_string(target, query)
            if not query:
                await self.usage_embed('%ssoundcloud [Link or query]' % self.prefix, target)
                return
            songs = (await self.songs_from_query(query, target, soundcloud=True))
            songs = (await self.trim_songs(songs, target))
            if not songs:
                return
            n = len(self.queue)
            self.queue.extend(songs)
            await self.enqueue_message(n, songs, target)
            await self.wake_up()


    async def command_nowplaying(self, target):
        '''Show what song is currently playing'''
        # !nowplaying
        if (await self.ensure_playing(target.author, target)):
            await self.np_message(target)


    async def command_grab(self, target):
        '''Save the song currently playing to your DMs'''
        # !grab
        if (await self.ensure_playing(target.author, target)):
            await self.np_message(target.author)
            await self.send(target)


    async def command_seek(self, target, time=None):
        '''Seek to a certain point in the current track'''
        # !seek
        if (await self.ensure_playing(target.author, target)) and \
           (await self.ensure_dj(target.author, target)):
            time = self.get_string(target, time)
            if time is None:
                await self.usage_embed('%sseek [time]' % self.prefix, target)
                return
            else:
                time = (await self.parse_time(time, target))
                if time is not None:
                    time = max(time, 0)
                    if time > self.now_playing.adjusted_length:
                        await self.skip(target)
                    else:
                        await self.seek(time, target)


    async def command_rewind(self, target, time=None):
        '''Rewind by a certain amount of time in the current track'''
        # !rewind
        if (await self.ensure_playing(target.author, target)) and \
           (await self.ensure_dj(target.author, target)):
            time = self.get_string(target, time)
            if time is None:
                await self.usage_embed('%srewind [seconds]' % self.prefix, target)
                return
            else:
                time = (await self.parse_time(time, target))
                if time is not None:
                    time = self.get_current_time() - time
                    time = max(time, 0)
                    await self.seek(time, target)


    async def command_forward(self, target, time=None):
        '''Skip forward by a certain amount of time in the current track'''
        # !forward
        if (await self.ensure_playing(target.author, target)) and \
           (await self.ensure_dj(target.author, target)):
            time = self.get_string(target, time)
            if time is None:
                await self.usage_embed('%sforward [seconds]' % self.prefix, target)
                return
            else:
                time = (await self.parse_time(time, target))
                if time is not None:
                    time = self.get_current_time() + time
                    if time > self.now_playing.adjusted_length:
                        await self.skip(target)
                    else:
                        await self.seek(time, target)


    async def command_replay(self, target):
        '''Reset the progress of the current song'''
        # !replay
        if (await self.ensure_playing(target.author, target)) and \
           (await self.ensure_dj(target.author, target)):
            await self.seek(0, target)


    async def command_loop(self, target):
        '''Toggle looping for the currently playing song'''
        # !loop
        if (await self.ensure_playing(target.author, target)) and \
           (await self.ensure_dj(target.author, target)):
            self.looping = (not self.looping)
            if self.looping:
                await self.send(target, '**:repeat_one: Enabled!**')
            else:
                await self.send(target, '**:repeat_one: Disabled!**')
            

    async def command_voteskip(self, target):
        '''Vote to skip the currently playing song'''
        # !voteskip
        if (await self.ensure_playing(target.author, target)) and \
           (await self.ensure_joined(target.author, target)):
            if len(self.voice_channel.members) <= 3:
                await self.skip(target)
            elif target.author in self.votes:
                await self.send(target, '**:x: You already voted to skip the current song** (%d/%d people)' \
                                % (len(self.votes), int(.75 * (len(self.voice_channel.members) - 1))))
            else:
                self.votes.append(target.author)
                if len(self.votes) >= int(.75 * (len(self.voice_channel.members) - 1)):
                    await self.skip(target)
                else:
                    await self.send(target, '**Skipping?** (%d/%d people)%s' \
                                    % (len(self.votes), int(.75 * (len(self.voice_channel.members) - 1)),
                                       ' **`%sforceskip` or `%sfs` to force**' % (self.prefix, self.prefix) \
                                       if self.is_dj(target.author) else ''))
                    

    async def command_forceskip(self, target, position=None):
        '''Skip the currently playing song immediately'''
        # !forceskip
        if (await self.ensure_playing(target.author, target)) and \
           (await self.ensure_dj(target.author, target)):
            if isinstance(target, discord.Message):
                try:
                    position = target.content[len(self.prefix):].split(None, 1)[1]
                except IndexError:
                    position = None
                else:
                    try:
                        position = int(position)
                    except ValueError:
                        await self.usage_embed('%sforceskip [position]' % self.prefix, target)
                        return
            if position is None:
                position = 1
            for n in range(position-1):
                if self.queue:
                    self.queue.popleft()
                else:
                    break
            await self.skip(target)


    async def command_pause(self, target):
        '''Pause the currently playing track'''
        # !pause
        if (await self.ensure_playing(target.author, target)) and \
           (await self.ensure_dj(target.author, target)):
            if self.last_paused is None:
                self.voice_client.pause()
                self.last_paused = time.time()
                await self.send(target, '**Paused :pause_button:**')
            else:
                await self.send(target, '**:no_entry_sign: Already paused**')


    async def command_resume(self, target):
        '''Resume paused music'''
        # !resume
        if (await self.ensure_playing(target.author, target)) and \
           (await self.ensure_dj(target.author, target)):
            if self.last_paused is not None:
                self.voice_client.resume()
                self.last_started_playing += (time.time() - self.last_paused)
                self.last_paused = None
                await self.send(target, '**:play_pause: Resuming :thumbsup:**')
            else:
                await self.send(target, '**:no_entry_sign: Already playing**')
            

    async def command_disconnect(self, target):
        '''Disconnect the bot from the voice channel it is in'''
        # !disconnect
        if (await self.ensure_dj(target.author, target, need_join=False)):
            await self.disconnect(target)


    async def command_queue(self, target, page=None):
        '''Show the list of songs in the queue'''
        # !queue
        page = (await self.parse_value(target, page))
        if page is None:
            page = 1
        await self.queue_message(target, page-1)


    async def command_loopqueue(self, target):
        '''Toggle looping for the whole queue'''
        # !loopqueue
        if (await self.ensure_queue(target.author, target)) and \
           (await self.ensure_dj(target.author, target)):
            self.queue_looping = (not self.queue_looping)
            if self.queue_looping:
                await self.send(target, '**:repeat: Enabled!**')
            else:
                await self.send(target, '**:repeat: Disabled!**')


    async def command_move(self, target, old=None, new=None):
        '''Move a certain song to a chosen position in the queue'''
        # !move
        if (await self.ensure_queue(target.author, target)) and \
           (await self.ensure_dj(target.author, target)):
            if isinstance(target, discord.Message):
                try:
                    numbers = target.content[len(self.prefix):].split(None, 1)[1]
                except IndexError:
                    numbers = ''
                try:
                    numbers = list(map(int, numbers.split()))
                    if len(numbers) == 1:
                        numbers.append(1)
                    old, new = numbers
                except ValueError:
                    await self.usage_embed('%smove [old position] [new position]' % self.prefix, target)
                    return
            elif new is None:
                new = 1
            if not ((1 <= old <= len(self.queue)) and (1 <= new <= len(self.queue))):
                await self.send(target, '**:x: Invalid position, should be between 1 and %d**' % len(self.queue))
            else:
                song = self.queue[old - 1]
                del self.queue[old - 1]
                self.queue.insert(new - 1, song)
                await self.send(target, '**:white_check_mark: Moved `%s` to position %d in the queue**' % (song.name, new))


    async def command_skipto(self, target, position=None):
        '''Skip to a certain position in the queue'''
        # !skipto
        if (await self.ensure_queue(target.author, target)) and \
           (await self.ensure_dj(target.author, target)):
            if isinstance(target, discord.Message):
                try:
                    position = target.content[len(self.prefix):].split(None, 1)[1]
                except IndexError:
                    position = ''
                try:
                    position = int(position)
                except ValueError:
                    await self.usage_embed('%sskipto [position]' % self.prefix, target)
                    return
            if not (1 <= position <= len(self.queue)):
                await self.send(target, '**:x: Invalid position, should be between 1 and %d**' % len(self.queue))
            else:
                for n in range(position-1):
                    self.queue.popleft()
                await self.skip(target)


    async def command_shuffle(self, target):
        '''Shuffle the entire queue'''
        # !shuffle
        if (await self.ensure_queue(target.author, target)) and \
           (await self.ensure_dj(target.author, target)):
            random.shuffle(self.queue)
            await self.send(target, '**:twisted_rightwards_arrows: Shuffled queue :ok_hand:**')


    async def command_remove(self, target, number=None):
        '''Remove a certain entry from the queue'''
        # !remove
        if (await self.ensure_queue(target.author, target)) and \
           (await self.ensure_dj(target.author, target)):
            if isinstance(target, discord.Message):
                try:
                    content = target.content[len(self.prefix):].split(None, 1)[1]
                except IndexError:
                    content = ''
                numbers = []
                for string in content.split():
                    try:
                        numbers.append(int(string))
                    except ValueError:
                        # maybe it's of the form a-b
                        m = re.match(r'(\d+)-(\d+)', string)
                        if m:
                            numbers.extend(range(int(m.group(1)), int(m.group(2))+1))
                        else:
                            await self.usage_embed('%sremove [positions]' % self.prefix, target)
                            return
            else:
                numbers = [number]
            for number in numbers:
                if not (1 <= number <= len(self.queue)):
                    await self.send(target, '**:x: Invalid position, should be between 1 and %d**' % len(self.queue))
                    break
            else:
                numbers = sorted(set(numbers), reverse=True)
                removed = []
                for n in numbers:
                    removed.append(self.queue[n-1])
                    del self.queue[n-1]
                if len(removed) > 1:
                    await self.send(target, '**:white_check_mark: Removed `%d` songs**' % len(removed))
                elif len(removed) == 1:
                    await self.send(target, '**:white_check_mark: Removed `%s`**' % removed[0].name)


    async def command_clear(self, target, user=None):
        '''Clear the whole queue'''
        # !clear
        if (await self.ensure_queue(target.author, target)) and \
           (await self.ensure_dj(target.author, target)):
            if isinstance(target, discord.Message) and target.mentions:
                user = target.mentions[0]
            if user is None:
                self.queue.clear()
                await self.send(target, '***:boom: Cleared... :stop_button:***')
            else:
                n = 0
                for song in tuple(self.queue):
                    if song.user == user:
                        self.queue.remove(song)
                        n += 1
                await self.send(target, '**:thumbsup: %d song%s removed from the queue**' % (n, '' if n == 1 else 's'))


    async def command_leavecleanup(self, target):
        '''Remove absent users' songs from the queue'''
        # !leavecleanup
        if (await self.ensure_queue(target.author, target)) and \
           (await self.ensure_dj(target.author, target)):
            n = 0
            for song in tuple(self.queue):
                if song.user not in self.voice_channel.members:
                    self.queue.remove(song)
                    n += 1
            await self.send(target, '**:thumbsup: %d song%s removed from the queue**' % (n, '' if n == 1 else 's'))
            

    async def command_removedupes(self, target):
        '''Remove duplicate songs from the queue'''
        # !removedupes
        if (await self.ensure_queue(target.author, target)) and \
           (await self.ensure_dj(target.author, target)):
            t = tuple(self.queue)
            n = 0
            for i, song in enumerate(t):
                if song in t[:i]:
                    self.queue.remove(song)
                    n += 1
            await self.send(target, '**:thumbsup: %d song%s removed from the queue**' % (n, '' if n == 1 else 's'))





    async def command_settings(self, target, value=None):
        '''List out the Bluez bot settings'''
        # !settings
        if isinstance(target, discord.Message):
            args = target.content[len(self.prefix):].split(None, 2)[1:]
            if not args:
                setting = ''
            else:
                setting = args[0].lower()
                value = (args[1] if len(args) == 2 else None)
        else:
                setting = target.subcommand_name
        if setting in ('', 'show'):
            # Print out all the settings
            embed = discord.Embed(title='Bluez Settings',
                                  description='Use the command format `%ssettings <options>` to view more info about an option.' % self.prefix)
            embed.add_field(name=':exclamation: Prefix', value='`%ssettings prefix`' % self.prefix, inline=True)
            embed.add_field(name=':no_entry_sign: Blacklist', value='`%ssettings blacklist`' % self.prefix, inline=True)
            embed.add_field(name=':musical_note: Autoplay', value='`%ssettings autoplay`' % self.prefix, inline=True)
            embed.add_field(name=':bell: Announce Songs', value='`%ssettings announcesongs`' % self.prefix, inline=True)
            embed.add_field(name=':hash: Max Queue Length', value='`%ssettings maxqueuelength`' % self.prefix, inline=True)
            embed.add_field(name=':1234: Max User Songs', value='`%ssettings maxusersongs`' % self.prefix, inline=True)
            embed.add_field(name=':notes: Duplicate Song Prevention', value='`%ssettings preventduplicates`' % self.prefix, inline=True)
            embed.add_field(name=':loud_sound: Default Volume', value='`%ssettings defaultvolume`' % self.prefix, inline=True)
            embed.add_field(name=':1234: DJ Only Playlists', value='`%ssettings djplaylists`' % self.prefix, inline=True)
            embed.add_field(name=':no_pedestrians: DJ Only', value='`%ssettings djonly`' % self.prefix, inline=True)
            embed.add_field(name=':page_with_curl: Set DJ Role', value='`%ssettings djrole`' % self.prefix, inline=True)
            embed.add_field(name=':infinity: Always Playing', value='`%ssettings alwaysplaying`' % self.prefix, inline=True)
            embed.add_field(name=':recycle: Reset', value='`%ssettings reset`' % self.prefix, inline=True)
            await self.send(target, embed=embed)
            return
        if (setting == 'blacklist') and isinstance(target, discord.Message):
            value = target.channel_mentions or None
        if value is None:
            # Query a setting
            if setting == 'prefix':
                embed = discord.Embed(title='Bluez Settings - :exclamation: Prefix',
                                      description='Changes the prefix used to address Bluez bot.')
                embed.add_field(name=':page_facing_up: Current Setting:', value='`%s`' % self.prefix)
                embed.add_field(name=':pencil2: Update:', value='`%ssettings prefix [New Prefix]`' % self.prefix)
                embed.add_field(name=':white_check_mark: Valid Settings', value='`Any text, at most 5 characters (e.g. !)`')
                await self.send(target, embed=embed)
                return
            elif setting == 'blacklist':
                embed = discord.Embed(title='Bluez Settings - :no_entry_sign: Blacklist',
                                      description='Keyword `blacklist` also removes channels from Blacklist')
                embed.add_field(name=':page_facing_up: Current Setting:',
                                value=('`%s`' % ', '.join([channel.mention for channel in self.blacklist]) \
                                       if self.blacklist else 'Blacklist empty'))
                embed.add_field(name=':pencil2: Update:', value='`%ssettings blacklist [Mention Channel]`' % self.prefix)
                embed.add_field(name=':white_check_mark: Valid Settings:', value='`Any number of mentioned text channels`')
                await self.send(target, embed=embed)
                return
            elif setting == 'autoplay':
                if self.autoplay:
                    await self.send(target, '**:musical_note: AutoPlay playlist link:** %s' % self.autoplay)
                else:
                    await self.send(target, '**:musical_note: No AutoPlay playlist currently configured**')
                return
            elif setting == 'announcesongs':
                await self.send(target, '**:bell: Announcing new songs is currently turned %s**' % \
                                ('on' if self.announcesongs else 'off'))
                return
            elif setting == 'maxqueuelength':
                if self.maxqueuelength is None:
                    await self.send(target, '**:hash: Max queue length disabled**')
                else:
                    await self.send(target, '**:hash: Max queue length set to %d**' % self.maxqueuelength)
                return
            elif setting == 'maxusersongs':
                if self.maxusersongs is None:
                    await self.send(target, '**:1234: Max user song limit disabled**')
                else:
                    await self.send(target, '**:1234: Max user song limit set to %d**' % self.maxusersongs)
                return
            elif setting == 'preventduplicates':
                await self.send(target, '**:notes: Duplicate prevention is currently turned %s**' % \
                                ('on' if self.preventduplicates else 'off'))
                return
            elif setting == 'defaultvolume':
                await self.send(target, '**:loud_sound: Default volume level is currently %d**' % round(200 * self.defaultvolume))
                return
            elif setting == 'djplaylists':
                await self.send(target, '**:1234: DJ Only Playlists mode is currently turned %s**' % \
                                ('on' if self.djplaylists else 'off'))
                return
            elif setting == 'djonly':
                await self.send(target, '**:no_pedestrians: DJ Only mode is currently turned %s**' % \
                                ('on' if self.djonly else 'off'))
                return
            elif setting == 'djrole':
                await self.send(target, '**:page_with_curl: The DJ Role here is `%s`**' % self.djrole)
                return
            elif setting == 'alwaysplaying':
                await self.send(target, '**:infinity: Always Playing mode is currently turned %s**' % \
                                ('on' if self.alwaysplaying else 'off'))
                return
            elif setting == 'reset':
                pass # fall through
            else:
                await self.send(target, '**:x: Unknown setting `%s`**' % setting)
                return
        # Need permission to change a setting
        if not (target.author.guild_permissions.manage_channels or \
                target.author.guild_permissions.administrator):
            await self.send(target, '**:x: You need either `Manage Channels` or `Administrator` privileges to change the bot settings')
            return
        if isinstance(target, discord.Message):
            if setting in ('announcesongs', 'preventduplicates', 'djplaylists', 'djonly', 'alwaysplaying'):
                value = (await self.parse_boolean(value, target))
                if value is None:
                    return
        # Change a setting
        if setting == 'prefix':
            if len(value) > 5:
                await self.send(target, '**:x: Prefix is too long (should be at most 5 characters)**')
            else:
                self.prefix = value
                await self.send(target, '**:thumbsup: Prefix set to `%s`**' % value)
        elif setting == 'blacklist':
            if isinstance(value, discord.TextChannel):
                blacklist = [value]
            else:
                blacklist = list(value)
            unblacklist = []
            for channel in blacklist[:]:
                if channel in self.blacklist:
                    blacklist.remove(channel)
                    self.blacklist.remove(channel)
                    unblacklist.append(channel)
            self.blacklist.extend(blacklist)
            if blacklist:
                await self.send(target, 'Blacklisted `%s`' % ', '.join([channel.name for channel in blacklist]))
            if unblacklist:
                await self.send(target, 'Unblacklisted `%s`' % ', '.join([channel.name for channel in unblacklist]))
        elif setting == 'autoplay':
            if value == 'disable':
                self.autoplay = None
                await self.send(target, '**:no_entry_sign: AutoPlay disabled**')
            else:
                try:
                    await songs_from_url(value)
                except Exception as e:
                    await self.send(target, '**:x: Error finding songs from `%s`: `%s`**' % (value, e))
                else:
                    self.autoplay = value
                    await self.send(target, '**:white_check_mark: Success**')
        elif setting == 'announcesongs':
            self.announcesongs = value
            if value:
                await self.send(target, '**:white_check_mark: I will now announce new songs**')
            else:
                await self.send(target, '**:no_entry_sign: I will not announce new songs**')
        elif setting == 'maxqueuelength':
            if value in ('disable', 0):
                value = None
            else:
                value = (await self.parse_integer(value, target, 10, 10000))
                if value is None:
                    return
            self.maxqueuelength = value
            if value is None:
                await self.send(target, '**:no_entry_sign: Max queue length disabled**')
            else:
                await self.send(target, '**:white_check_mark: Max queue length set to %d**' % value)
        elif setting == 'maxusersongs':
            if value in ('disable', 0):
                value = None
            else:
                value = (await self.parse_integer(value, target, 1, 10000))
                if value is None:
                    return
            self.maxusersongs = value
            if value is None:
                await self.send(target, '**:no_entry_sign: Max user song limit disabled**')
            else:
                await self.send(target, '**:white_check_mark: Max user song limit set to %d**' % value)
        elif setting == 'preventduplicates':
            self.preventduplicates = value
            if value:
                await self.send(target, '**:white_check_mark: I will automatically prevent duplicate songs**')
            else:
                await self.send(target, '**:no_entry_sign: I will not prevent duplicate songs**')
        elif setting == 'defaultvolume':
            value = (await self.parse_integer(value, target, 1, 200))
            if value is None:
                return
            self.volume = value / 200.0
            await self.send(target, '**:loud_sound: Default volume is now set to %d**' % value)
        elif setting == 'djplaylists':
            self.djplaylists = value
            if value:
                await self.send(target, '**:white_check_mark: DJ Only Playlists enabled**')
            else:
                await self.send(target, '**:no_entry_sign: DJ Only Playlists disabled**')
        elif setting == 'djonly':
            self.djonly = value
            if value:
                await self.send(target, '**:white_check_mark: DJ Only mode enabled**')
            else:
                await self.send(target, '**:no_entry_sign: DJ Only mode disabled**')
        elif setting == 'djrole':
            if target.role_mentions:
                value = target.role_mentions[0].name
            self.djrole = value
            await self.send(target, '**:page_with_curl: DJ role set to `%s`**' % value)
        elif setting == 'alwaysplaying':
            self.alwaysplaying = value
            if value:
                await self.send(target, '**:white_check_mark: Always Playing mode enabled**')
            else:
                await self.send(target, '**:no_entry_sign: Always Playing mode disabled**')
        # Resetting all settings
        elif setting == 'reset':
            await self.send(target, '**:warning: You are about to reset all settings to their defaults. Continue? (yes/no)**')
            def check(m):
                return (m.channel == target.channel) and (m.author == target.author) and m.content.lower().strip() in ('yes', 'no')
            try:
                yesno = (await self.client.wait_for('message', check=check, timeout=10))
            except asyncio.TimeoutError:
                await self.send(target, '**:no_entry_sign: Timeout**')
                return
            if yesno.content.lower().strip() == 'no':
                return
            # Otherwise reset everything
            self.reset_settings()
            await self.send(target, '**:white_check_mark: All settings have been reset to their defaults**')
        else:
            await self.send(target, '**:x: Unknown setting `%s`**' % setting)
            return
        # Save off the settings if we get to this point
        self.save_settings()



    async def command_effects(self, target):
        '''Show current audio effects'''
        # !effects
        if isinstance(target, discord.Message):
            try:
                command = target.content[len(self.prefix):].split()[1]
            except IndexError:
                command = ''
            command = command.lower()
        else:
            command = target.subcommand_name
        if command in ('', 'show'):
            # Show current effects
            if (await self.ensure_connected(target.author, target)):
                embed = discord.Embed(title='Current audio effect settings',
                                      description='''\
:man_running: Speed - %s

:musical_score: Pitch - %s

:guitar: Bass - %d

:crescent_moon: Nightcore - %s

:stopwatch: Slowed - %s

:loud_sound: Volume - %d''' % (self.tempo, self.pitch, self.bass, 'On' if self.nightcore else 'Off',
                  'On' if self.slowed else 'Off', round(200 * self.volume)))
                await self.send(target, embed=embed)
        elif command == 'help':
            # Describe effects
            embed = discord.Embed(title='Bluez audio effects',
                                  description='''\
`%sspeed <0.1 - 3>` - adjust the speed of the song playing
`%spitch <0.1 - 3>` - adjust the pitch of the song playing
`%sbass <1 - 5>` - adjust the bass boost
`%snightcore` - toggle the nightcore effect on or off
`%sslowed` - toggle the slowed effect on or off
`%svolume <1-200>` - adjust the volume of the song playing''' % ((self.prefix,) * 6))
            await self.send(target, embed=embed)
        elif command == 'clear':
            # Reset all effects to default
            if (await self.self.ensure_connected(target.author, target)) and \
               (await self.self.ensure_dj(target.author, target)):
                await self.send(target, '**:warning: You are about to reset all audio effects to their defaults. Continue? (yes/no)**')
                def check(m):
                    return (m.channel == target.channel) and (m.author == target.author) and m.content.lower().strip() in ('yes', 'no')
                try:
                    yesno = (await self.client.wait_for('message', check=check, timeout=10))
                except asyncio.TimeoutError:
                    await self.send(target, '**:no_entry_sign: Timeout**')
                    return
                if yesno.content.lower().strip() == 'no':
                    return
                # Otherwise reset everything
                self.reset_effects()
                self.update_audio()
                await self.send(target, '**:white_check_mark: All audio effects have been reset to their defaults**')
        else:
            await self.send(target, '**:x: Unknown command; should be `%seffects`, `%seffects help`, or `%seffects clear`**' % \
                            (self.prefix, self.prefix, self.prefix))



    async def command_speed(self, target, speed=None):
        '''Show or adjust the playback speed'''
        # !speed
        if (await self.ensure_connected(target.author, target)):
            speed = (await self.parse_value(target, speed, 0.1, 3, integer=False))
            if speed is None:
                await self.send(target, '**:man_running: Current playback speed is set to %s**' % self.tempo)
            elif (await self.ensure_dj(target.author, target)):
                self.tempo = speed
                self.update_audio()
                await self.send(target, '**:white_check_mark: Playback speed set to %s**' % self.tempo)



    async def command_pitch(self, target, pitch=None):
        '''Show or adjust the playback pitch'''
        # !pitch
        if (await self.ensure_connected(target.author, target)):
            pitch = (await self.parse_value(target, pitch, 0.1, 3, integer=False))
            if pitch is None:
                await self.send(target, '**:musical_score: Current playback pitch is set to %s**' % self.pitch)
            elif (await self.ensure_dj(target.author, target)):
                self.pitch = pitch
                self.update_audio()
                await self.send(target, '**:white_check_mark: Playback pitch set to %s**' % self.pitch)



    async def command_bass(self, target, bass=None):
        '''Show or adjust the bass-boost effect'''
        # !bass
        if (await self.ensure_connected(target.author, target)):
            bass = (await self.parse_value(target, bass, 1, 5, integer=True))
            if bass is None:
                await self.send(target, '**:guitar: Current bass boost is set to %d**' % self.bass)
            elif (await self.ensure_dj(target.author, target)):
                self.bass = bass
                self.update_audio()
                await self.send(target, '**:white_check_mark: Bass boost set to %d**' % self.bass)



    async def command_nightcore(self, target):
        '''Toggle the nightcore effect'''
        # !nightcore
        if (await self.ensure_connected(target.author, target)) and \
           (await self.ensure_dj(target.author, target)):
            self.nightcore = (not self.nightcore)
            self.update_audio()
            await self.send(target, '**:white_check_mark: Nightcore effect turned %s**' % ('on' if self.nightcore else 'off'))


    async def command_slowed(self, target):
        '''Toggle the slowed effect'''
        # !slowed
        if (await self.ensure_connected(target.author, target)) and \
           (await self.ensure_dj(target.author, target)):
            self.slowed = (not self.slowed)
            self.update_audio()
            await self.send(target, '**:white_check_mark: Slowed effect turned %s**' % ('on' if self.slowed else 'off'))



    async def command_volume(self, target, volume=None):
        '''Show or adjust the playback volume'''
        # !volume
        if (await self.ensure_connected(target.author, target)):
            volume = (await self.parse_value(target, volume, 1, 200, integer=True))
            if volume is None:
                await self.send(target, '**:loud_sound: Volume is currently set to %d**' % round(200 * self.volume))
            elif (await self.ensure_dj(target.author, target)):
                self.volume = volume / 200.0
                self.update_audio()
                await self.send(target, '**:white_check_mark: Volume set to %d**' % volume)
                    
        



    async def command_prune(self, target, number=None):
        '''Delete the bot's messages and commands'''
        # !prune
        if isinstance(target, discord.Message):
            try:
                number = target.content[len(self.prefix):].split()[1]
            except IndexError:
                number = None
        await self.send(target)
        count = 0
        for message in self.bot_messages[:]:
            if message.channel == target.channel:
                self.bot_messages.remove(message)
                await message.delete()
                count += 1
                if (number is not None) and (count == number):
                    break
                    
            
    







    ##### Other utilities #####


    async def parse_time(self, time, target):
        try:
            return int(time)
        except ValueError:
            pass
        match = re.match(r'(?:(\d+):)?(\d+):(\d+)', time)
        if match:
            h = int(match.group(1) or 0)
            m = int(match.group(2))
            s = int(match.group(3))
            return 3600*h + 60*m + s
        match = re.match(r'(?:(\d+)h)?(?:(\d+)m)?(?:(\d+)s)', time)
        if match:
            h = int(match.group(1) or 0)
            m = int(match.group(2) or 0)
            s = int(match.group(3) or 0)
            return 3600*h + 60*m + s
        await self.send(target, '**:x: unable to parse time `%s`**' % time)



    async def parse_boolean(self, value, target):
        value = value.lower()
        if value in ('y', 'yes', 't', 'true', 'on'):
            return True
        elif value in ('n', 'no', 'f', 'false', 'off'):
            return False
        else:
            await self.send(target, '**:x: unable to parse true/false value `%s`**' % value)
            return None


    async def parse_integer(self, value, target, min=None, max=None):
        try:
            value = int(value)
        except ValueError:
            await self.send(target, '**:x: unable to parse integer `%s`**' % value)
            return None
        if (min is not None) and (max is not None) and not (min <= value <= max):
            await self.send(target, '**:x: value must be between %d and %d**' % (min, max))
            return None
        return value


    async def parse_number(self, value, target, min=None, max=None):
        try:
            value = float(value)
        except ValueError:
            await self.send(target, '**:x: unable to parse number `%s`**' % value)
            return None
        if (min is not None) and (max is not None) and not (min <= value <= max):
            await self.send(target, '**:x: value must be between %s and %s**' % (min, max))
            return None
        return value



    async def parse_value(self, target, value=None, min=None, max=None, integer=True):
        if value is None:
            if not isinstance(target, discord.Message):
                return None
            try:
                value = target.content[len(self.prefix):].split()[1]
            except IndexError:
                return None
        parse = (self.parse_integer if integer else self.parse_number)
        return (await parse(value, target, min, max))



    async def notify_user_join(self, member):
        # Called when a user joins a voice channel that this bot is in
        if self.idle_task:
            self.idle_task.cancel()
            self.idle_task = None
        if self.empty_paused:
            self.voice_client.resume()
            self.last_started_playing += (time.time() - self.last_paused)
            self.empty_paused = False
            self.last_paused = None


    async def notify_user_leave(self, member):
        # Called when a user leaves a voice channel that this bot is in
        if self.voice_channel is not None:
            if len(self.voice_channel.members) == 1:
                self.votes = []
                if not self.alwaysplaying:
                    self.idle_task = asyncio.create_task(self.idle_timer_func())
                    if self.voice_client.is_playing() and not self.voice_client.is_paused():
                        self.empty_paused = True
                        self.last_paused = time.time()
                        self.voice_client.pause()
            elif member in self.votes:
                self.votes.remove(member)
            elif self.votes and (len(self.votes) >= int(.75 * (len(self.voice_channel.members) - 1))):
                await self.skip(self.text_channel)


    async def idle_timer_func(self):
        # Wait a certain amount of time, and then leave the voice channel
        await asyncio.sleep(300) # 5-minute delay
        await self.disconnect()


    async def usage_embed(self, syntax, target):
        # Print out a usage message
        embed = discord.Embed(title=':x: Invalid usage',
                              description=syntax,
                              color=discord.Color.red())
        await self.send(target, embed=embed)




    async def trim_songs(self, songs, target):
        # This method takes a list of songs, and removes any that are not allowed to be there due to bot settings.
        # Unless at least one bot setting has been changed from its default value, this method will return the
        # whole list of songs and filter nothing out.
        name = getattr(songs, 'name', None)
        link = getattr(songs, 'link', None)
        if not songs:
            # nothing to do
            return []
        # if self.djplaylists is True, this blocks non-DJs from queueing more than one song at a time
        if (len(songs) > 1) and self.djplaylists and not self.is_dj(target.author):
            await self.send(target, '**:x: The server is currently in DJ Only Playlists mode. Only DJs can queue playlists!**')
            return []
        # If self.preventduplicates is True, this removes any songs that are already on the queue
        if self.preventduplicates:
            songs = list(songs)
            for i, song in enumerate(songs):
                if (song in self.queue) or (song in songs[:i]):
                    songs.remove(song)
                    await self.send(target, '**:x: `%s` has already been added to the queue**' % song.name)
        # If self.maxqueuelength is not None, this removes any songs that exceed the length
        if self.maxqueuelength is not None:
            if len(self.queue) == self.maxqueuelength:
                await self.send(target, '**:x: Cannot queue up any new songs because the queue is full**')
                return []
            elif len(self.queue) + len(songs) > self.maxqueuelength:
                songs = songs[:self.maxqueuelength - len(self.queue)]
                await self.send(target, '**:warning: Shortening playlist due to reaching the song queue limit**')
        # If self.maxusersongs is not None, this removes any songs queued by this user that exceed the limit
        if self.maxusersongs is not None:
            nuser = len([song for song in self.queue if song.user == songs[0].user])
            if nuser == self.maxusersongs:
                await self.send(target, '**:x: Unable to queue song, you have reached the maximum songs you can have in the queue**')
                return []
            elif nuser + len(songs) > self.maxusersongs:
                songs = songs[:self.maxusersongs - nuser]
                await self.send(target, '**:warning: Shortening playlist due to reaching the maximum songs you can have in the queue**')
        # Make sure the name and url are preserved
        if (name is not None) or (link is not None):
            if not isinstance(songs, Playlist):
                songs = Playlist(songs)
                songs.name = name
                songs.link = link
        return songs



    def get_string(self, target, string):
        # Utility to either get a string from a slash command (in which case the first argument
        # will be a discord_slash.SlashContext and the second will be the string, or None if optional),
        # or else get the string from the content of a discord.Message (in which case the second
        # argument is None)
        if string is not None:
            return string
        if isinstance(target, discord.Message):
            try:
                return target.content[len(self.prefix):].split(None, 1)[1]
            except IndexError:
                pass




    def load_settings(self):
        # Load the bot settings from S3
        if self.client.s3:
            file = io.BytesIO()
            try:
                self.client.s3.download_fileobj(self.client.s3_bucket, 'Guild%d' % self.guild.id, file)
            except:
                return
            file.seek(0)
            # Reading boolean values
            packed_int = file.read(1)[0]
            self.announcesongs     = bool(packed_int & 0x01)
            self.preventduplicates = bool(packed_int & 0x02)
            self.djonly            = bool(packed_int & 0x04)
            self.djplaylists       = bool(packed_int & 0x08)
            self.alwaysplaying     = bool(packed_int & 0x10)
            # Reading integers
            size = struct.calcsize('HHB')
            self.maxqueuelength, self.maxusersongs, self.defaultvolume = struct.unpack('3H', file.read(size))
            if self.maxqueuelength == 0:
                self.maxqueuelength = None
            if self.maxusersongs == 0:
                self.maxusersongs = None
            self.defaultvolume /= 200.0
            # Reading strings
            prefix_len = file.read(1)[0]
            self.prefix = file.read(prefix_len).decode('utf-8')
            djrole_len = file.read(1)[0]
            self.djrole = file.read(djrole_len).decode('utf-8')
            autoplay_len = struct.unpack('H', file.read(struct.calcsize('H')))[0]
            if autoplay_len:
                self.autoplay = file.read(autoplay_len).decode('utf-8')
            else:
                self.autoplay = None
            # Reading channels
            blacklist_len = struct.unpack('H', file.read(struct.calcsize('H')))[0]
            size = struct.calcsize('%dQ' % blacklist_len)
            blacklist_ids = struct.unpack('%dQ' % blacklist_len, file.read(size))
            self.blacklist = [channel for channel in self.guild.text_channels if channel.id in blacklist_ids]
                



    def save_settings(self):
        # Save the bot settings to S3
        if self.client.s3:
            file = io.BytesIO()
            # Writing boolean values
            packed_int = 0
            if self.announcesongs    : packed_int |= 0x01
            if self.preventduplicates: packed_int |= 0x02
            if self.djonly           : packed_int |= 0x04
            if self.djplaylists      : packed_int |= 0x08
            if self.alwaysplaying    : packed_int |= 0x10
            file.write(bytes([packed_int]))
            # Writing integers
            file.write(struct.pack('HHB',
                                   self.maxqueuelength or 0,
                                   self.maxusersongs or 0,
                                   int(round(self.defaultvolume * 200))))
            # Writing strings
            prefix = self.prefix.encode('utf-8')
            file.write(bytes([len(prefix)]))
            file.write(prefix)
            djrole = self.djrole.encode('utf-8')
            file.write(bytes([len(djrole)]))
            file.write(djrole)
            if self.autoplay:
                autoplay = self.autoplay.encode('utf-8')
                file.write(struct.pack('H', len(autoplay)))
                file.write(autoplay)
            else:
                file.write(struct.pack('H', 0))
            # Writing channels
            file.write(struct.pack('H', len(self.blacklist)))
            for channel in self.blacklist:
                file.write(struct.pack('Q', channel.id))
            # Save the file
            file.seek(0)
            try:
                self.client.s3.upload_fileobj(file, self.client.s3_bucket, 'Guild%d' % self.guild.id)
            except:
                pass
                
            
