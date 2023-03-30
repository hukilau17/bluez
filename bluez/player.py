# Music player class

import discord
import asyncio
import os
import random
import collections
import math
import time
import datetime
import logging
import tempfile

from bluez.song import *
from bluez.views import *
from bluez.util import *

BLUEZ_DEBUG = bool(int(os.getenv('BLUEZ_DEBUG', '0')))
BLUEZ_SETTINGS_PATH = os.getenv('BLUEZ_SETTINGS_PATH')

MAX_HISTORY_LEN = 100

Lock = DebugLock if BLUEZ_DEBUG else asyncio.Lock





class Player(object):

    def __init__(self, bot, guild):
        self.bot = bot
        self.guild = guild
        self.reset_settings()
        self.reset()
        self.load_settings()
        self.history = collections.deque(maxlen=MAX_HISTORY_LEN)
        self.mutex = Lock()


    # Functions for initializing/resetting the bot's state

    def reset_settings(self):
        self.prefix = '!'
        self.announcesongs = False
        self.preventduplicates = False
        self.blacklist = []
        self.maxqueuelength = 0
        self.maxusersongs = 0
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
        self.skip_forward = False
        self.skip_backward = False
        self.idle_task = None
        self.last_started_playing = None
        self.last_paused = None
        self.searching_channels = []
        self.seek_pos = None
        self.stderr = tempfile.TemporaryFile()
        self.reset_effects()



    # Coroutines to ensure that a certain condition is met before proceeding further

    async def ensure_connected(self, ctx):
        # Make sure the bot has joined some voice channel
        if self.voice_channel is None:
            await ctx.send('**:x: I am not connected to a voice channel.** Type `%sjoin` to get me in one' % self.prefix)
            return False
        return True


    async def ensure_playing(self, ctx):
        # Make sure the bot is currently playing something
        if not (await self.ensure_connected(ctx)):
            return False
        if self.now_playing is None:
            await ctx.send('**:x: I am not currently playing anything.** Type `%splay` to play a song' % self.prefix)
            return False
        return True


    async def ensure_queue(self, ctx):
        # Make sure the bot has some songs queued
        if not (await self.ensure_connected(ctx)):
            return False
        if not self.queue:
            await ctx.send('**:x: The queue is currently empty.** Type `%splay` to play a song' % self.prefix)
            return False
        return True


    async def ensure_history(self, ctx):
        # Make sure the bot has a history
        if not self.history:
            await ctx.send('**:x: There are no songs in the history.** Type `%splay` to play a song' % self.prefix)
            return False
        return True


    async def ensure_joined(self, ctx, quiet=True):
        # Make sure the given member has joined the voice channel that the bot is in
        if self.voice_channel is None:
            # First we need to connect to a voice channel
            if ctx.author.voice and ctx.author.voice.channel:
                await self.connect(ctx, ctx.author.voice.channel)
                return True
        elif ctx.author in self.voice_channel.members:
            if not quiet:
                await ctx.send('**:thumbsup: Already connected to voice**')
            return True
        if self.voice_channel is None:
            await ctx.send('**:x: You have to be in a voice channel to use this command.**')
            return False
        elif ctx.author.voice and ctx.author.voice.channel and (len(self.voice_channel.members) == 1):
            # if the bot is by itself, you can steal it
            await self.disconnect()
            await self.connect(ctx, ctx.author.voice.channel)
            return True
        else:
            await ctx.send('**:x: You have to be in the same voice channel with the bot to use this command.**')
            return False


    def is_dj(self, member):
        return member.guild_permissions.manage_channels or \
               discord.utils.get(member.roles, name=self.djrole) or \
               discord.utils.get(member.roles, name='DJ')


    async def ensure_dj(self, ctx, need_join=True, need_connect=True):
        # Make sure the given member has the DJ role. (Also ensures they are in the right channel if necessary.)
        if need_join:
            if not (await self.ensure_joined(ctx)):
                return False
        elif need_connect:
            if not (await self.ensure_connected(ctx)):
                return False
            for voice_member in self.voice_channel.members:
                if (voice_member != ctx.author) and (voice_member != self.bot.user):
                    break
            else:
                return True # This user is alone with the bot
        if self.is_dj(ctx.author):
            return True
        await ctx.send('**:x: This command requires you to either have a role named DJ or the Manage Channels permission to use it** \
(being alone with the bot also works)')
        return False


    async def ensure_admin(self, ctx):
        # Make sure the given member has permission to change the bot settings
        if ctx.author.guild_permissions.manage_channels or ctx.author.guild_permissions.administrator:
            return True
        else:
            await ctx.send('**:x: You need either `Manage Channels` or `Administrator` privileges to change the bot settings**')
            return False




    ##### Connection methods #####


    async def connect(self, ctx, voice_channel):
        # Connect to a voice channel
        async with self.mutex:
            self.reset()
        self.voice_client = (await voice_channel.connect())
        self.text_channel = ctx.channel
        self.voice_channel = voice_channel
        await ctx.send('**:thumbsup: Joined `%s` and bound to %s**' % (voice_channel.name, ctx.channel.mention))
        if self.autoplay:
            try:
                songs = (await songs_from_url(self.autoplay, self.bot.user))
            except Exception as e:
                await ctx.send('**:x: Error playing songs from `%s`: `%s`**' % (self.autoplay, e))
            else:
                songs = (await self.trim_songs(ctx, songs, anonymous=True))
                if songs:
                    await self.playshuffle(ctx, songs)


    async def disconnect(self, ctx=None):
        # Leave the voice channel
        if self.voice_channel is not None:
            client = self.voice_client
            async with self.mutex:
                self.reset()
                await client.disconnect()
            if ctx:
                await ctx.send('**:mailbox_with_no_mail: Successfully disconnected**')






    ##### Playback methods #####



    async def play_next(self, error=None, lock=False):
        # Play the next song from the queue, if it exists
        # Should only be called when nothing is currently playing
        acquired = False
        try:
            if lock:
                await self.mutex.acquire()
                acquired = True
                # It's possible that someone else messed with the state of the player while we were waiting for the mutex,
                # by invoking a bot command such as !play or !skip right when a song was ending.
                # If this happens, then just let whatever they played keep playing. Otherwise the state can get corrupted
                # because the later parts of this function call assume noting is currently playing.
                if self.voice_client is not None:
                    if self.voice_client.is_playing() or self.voice_client.is_paused():
                        return
            self.votes = []
            if self.voice_client is not None:
                # Check for an error with the previous song
                retrying = False
                if self.now_playing:
                    error = (error or get_error(self.stderr))
                    if error:
                        strerror = str(error)
                        if self.should_retry(strerror):
                            self.seek_pos = None
                            retrying = True
                            await self.now_playing.reload()
                        elif self.should_ignore(strerror):
                            logging.warning(strerror)
                        else:
                            if isinstance(error, Exception):
                                log_exception(error)
                            errmsg = (await self.text_channel.send('**:x: Error playing `%s`: `%s`**' % (self.now_playing.name, error)))
                # Figure out what song to play next
                if (self.seek_pos is None) and not retrying:
                    if self.looping and (self.now_playing is not None) and not (self.skip_forward or self.skip_backward):
                        # play the same song again
                        pass
                    elif self.queue and not self.skip_backward:
                        # play the next song in the queue
                        self.now_playing = self.queue.popleft()
                        if self.queue_looping:
                            # put the just-finished song on the end of the queue
                            self.queue.append(self.now_playing)
                        self.skip_forward = False
                    elif (len(self.history) > 1) and self.skip_backward:
                        # play the previous song in the history
                        self.queue.appendleft(self.now_playing)
                        self.history.pop()
                        self.now_playing, timestamp = self.history.pop()
                        self.skip_backward = False
                    else:
                        if self.skip_backward:
                            # no previous song in the history
                            self.history.clear()
                            self.skip_backward = False
                        # no next song in the queue
                        self.skip_forward = False
                        self.now_playing = None
                        # the player is becoming idle; forget we were paused
                        self.last_started_playing = None
                        self.last_paused = None
                        return
                # Fetch the audio for the song and play it
                if self.now_playing:
                    self.last_started_playing = None
                    source = (await self.now_playing.get_audio(self.seek_pos or 0, self.tempo, self.pitch, self.bass,
                                                               self.nightcore, self.slowed, self.volume, self.stderr))
                    if isinstance(source, Exception):
                        self.seek_pos = None
                        await self.play_next(source, lock=False)
                        return
                    self.voice_client.play(source, after=self._play_next_callback)
                    now = time.time()
                    self.last_started_playing = now - (self.seek_pos or 0)
                    if self.last_paused:
                        # The bot will remember if it was paused if you skip, seek, or mess with audio effects.
                        # The only exception is that if you skip past the end of the queue and the bot become idle,
                        # it forgets it was paused. (Otherwise the behavior might be confusing.)
                        self.last_paused = now
                        self.voice_client.pause()
                    if (self.seek_pos is None) and not retrying:
                        region = self.voice_channel.rtc_region
                        self.history.append((self.now_playing, datetime.datetime.utcnow()))
                    announce = (self.announcesongs and (self.seek_pos is None) and not retrying)
                    self.seek_pos = None
                    if announce:
                        await self.np_message(self.text_channel)
            else:
                # the player has been disconnected
                self.now_playing = None
                self.last_started_playing = None
                self.last_paused = None
        finally:
            if lock and acquired:
                self.mutex.release()



    def _play_next_callback(self, error):
        # Callback for play_next()
        self._play_next_task = self.bot.loop.create_task(self.play_next(error, lock=True))



    async def skip(self, ctx, forward=True, backward=False):
        # Skip to the next song on the queue.
        # Does the same thing as play_next() if there's not
        # currently a song playing.
        if self.voice_client is not None:
            self.skip_forward = forward
            self.skip_backward = backward
            if self.voice_client.is_playing() or self.voice_client.is_paused():
                self.voice_client.stop()
                await ctx.send('***:%s: Skipped :thumbsup:***' % ('rewind' if backward else 'fast_forward'))
            else:
                await self.play_next()


    async def wake_up(self):
        # Play a song if nothing is currently playing
        # Do nothing if there's already a song playing
        if not (self.voice_client.is_playing() or self.voice_client.is_paused()):
            await self.play_next()


    def update_audio(self):
        # Called when the audio effects (volume, speed, bass, etc.) are changed
        # Effectively the same as a "seek" to the current time
        if (self.voice_client is not None) and (self.voice_client.is_playing() or self.voice_client.is_paused()):
            self.seek_pos = (self.get_current_time() or 0)
            self.seek_pos *= self.now_playing.tempo / self.get_adjusted_tempo()
            self.voice_client.stop()


    def get_current_time(self):
        # Get the number of seconds since the most recent track started
        if self.last_paused is not None:
            return self.last_paused - self.last_started_playing
        elif self.last_started_playing is not None:
            return time.time() - self.last_started_playing


    def get_adjusted_tempo(self):
        # Get the tempo that songs are currently playing it
        # (this is different from self.tempo if nightcore or slowed options are enabled)
        return get_adjusted_tempo(self.tempo, self.nightcore, self.slowed)


    def should_retry(self, errmsg):
        # Determine from the text of an error message if we should reload the song and try again
        return 'Server returned 403 Forbidden (access denied)' in errmsg # try again for this stupid bug


    def should_ignore(self, errmsg):
        # Determine from the text of an error message if we should ignore it without printing anything out
        if 'Connection reset by peer' in errmsg:
            return True # these are not worth complaining about
        if 'Estimating duration from bitrate' in errmsg:
            return True # this is a warning, not an error
        if 'Output file is empty, nothing was encoded' in errmsg:
            return True # the user messed with the audio effects for a song right as it was ending
        if 'Error in the pull function' in errmsg:
            return True # these are not worth complaining about either
        return False






    ##### Status message methods #####


    @staticmethod
    async def post_multipage_embed(ctx, embeds, start_index=0):
        if not embeds:
            await ctx.send('**:warning: Empty data**')
            return
        start_index = max(min(start_index, len(embeds)-1), 0)
        message = (await ctx.send(embed=embeds[start_index]))
        if len(embeds) > 1:
            current_page = start_index
            await message.add_reaction('\u25c0')
            await message.add_reaction('\u25b6')
            # Enter event loop to wait a certain amount of time (30 seconds) for the user to scroll through the list
            def check(reaction, user):
                return (reaction.message.id == message.id) and (reaction.emoji in ('\u25c0', '\u25b6')) and (user != ctx.bot.user)
            while True:
                try:
                    reaction, user = (await ctx.bot.wait_for('reaction_add', timeout=30, check=check))
                except asyncio.TimeoutError:
                    if ctx.guild is not None:
                        # can't remove reactions in DMs
                        await message.clear_reaction('\u25c0')
                        await message.clear_reaction('\u25b6')
                    break
                else:
                    # Remove the reaction and advance as appropriate
                    if ctx.guild is not None:
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



    async def np_message(self, ctx):
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
            embed.set_author(name='Now Playing \u266a', icon_url=self.bot.user.avatar.url)
            if song.thumbnail:
                embed.set_thumbnail(url=song.thumbnail)
            await ctx.send(embed=embed)



    async def queue_message(self, ctx, start_index=0):
        # Post the queue to the appropriate channel
        n = len(self.queue)
        npages = (n - 1) // 10 + 1
        total = format_time(sum([i.length for i in self.queue]) / self.get_adjusted_tempo())
        embeds = []
        color = discord.Color.random()
        for i in range(npages):
            embed = discord.Embed(title='Queue for %s' % ctx.guild.name, color=color)
            description = ''
            if i == 0:
                if self.now_playing:
                    description += '__Now Playing:__\n%s | `%s Requested by %s`\n\n' % \
                                   (format_link(self.now_playing), format_time(self.now_playing.adjusted_length),
                                    format_user(self.now_playing.user))
                description += '__Up Next:__\n'
            for j, song in enumerate(tuple(self.queue)[10*i : 10*(i+1)], 10*i+1):
                description += '`%d.` %s | `%s Requested by %s`\n\n' % \
                               (j, format_link(song), format_time(song.length / self.get_adjusted_tempo()),
                                format_user(song.user))
            description += '**%d songs in queue | %s total length**\n\n' % (n, total)
            embed.description = description
            footer = 'Page %d/%d | Loop: %s | Queue Loop: %s' % \
                           (i+1, npages,
                            '\u2705' if self.looping else '\u274c',
                            '\u2705' if self.queue_looping else '\u274c')
            embed.set_footer(text=footer,
                             icon_url=ctx.author.avatar.url)
            embeds.append(embed)
        await self.post_multipage_embed(ctx, embeds, start_index)



    async def history_message(self, ctx, timezone=None):
        # Post the history to the appropriate channel
        n = len(self.history)
        npages = (n - 1) // 10 + 1
        embeds = []
        color = discord.Color.random()
        if timezone:
            tzname = datetime.datetime.utcnow().astimezone(timezone).tzname()
        else:
            tzname = 'UTC'
        for i in range(npages):
            embed = discord.Embed(title='History for %s (`%s` time zone)' % (ctx.guild.name, tzname), color=color)
            description = ''
            for song, timestamp in tuple(self.history)[-10*(i+1) : ((-10*i) or None)]:
                if timezone:
                    timestamp = timestamp.astimezone(timezone)
                description += '`%s` %s | `Requested by %s`\n\n' % (timestamp.strftime('%x %X'), format_link(song), format_user(song.user))
            embed.description = description
            footer = 'Page %d/%d' % (npages-i, npages)
            embed.set_footer(text=footer,
                             icon_url=ctx.author.avatar.url)
            embeds.append(embed)
        embeds.reverse()
        await self.post_multipage_embed(ctx, embeds, npages-1)



    async def enqueue_message(self, ctx, position, songs, now=False, shuffle=False):
        # Send info on the recently enqueued song(s) to the appropriate channel
        if not songs:
            await ctx.send('**:warning: No songs were queued.**')
            return
        if shuffle:
            message = 'Shuffled into queue'
        else:
            message = 'Added to queue'
        if now:
            # Add songs to the queue that will be played right away
            time = position = 'Now'
        elif shuffle:
            # Insert songs randomly into the queue
            time = position = '???'
        else:
            # Add songs to the end of the queue
            # and estimate how long it will be until they are played
            time = sum([i.length for i in tuple(self.queue)[:position]]) / self.get_adjusted_tempo()
            if self.now_playing is not None:
                time += max(self.now_playing.adjusted_length - (self.get_current_time() or 0), 0)
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
            embed.set_author(name='Playlist %s' % message.lower(), icon_url=ctx.author.avatar.url)
            embed.add_field(name='Estimated time until playing', value=time, inline=False)
            embed.add_field(name='Position in queue', value=position, inline=True)
            embed.add_field(name='Enqueued', value='`%d` song%s' % (len(songs), '' if len(songs) == 1 else 's'), inline=True)
        elif time != 'Now':
            # This is a single song being played at some point in the future
            song = songs[0]
            embed = discord.Embed(description=format_link(song))
            embed.set_author(name=message, icon_url=ctx.author.avatar.url)
            if song.channel_url:
                channel_link = '[%s](%s)' % (song.channel, song.channel_url)
            else:
                channel_link = song.channel
            embed.add_field(name='Channel', value=channel_link, inline=True)
            embed.add_field(name='Song Duration', value=format_time(song.length / self.get_adjusted_tempo()), inline=True)
            embed.add_field(name='Estimated time until playing', value=time, inline=True)
            embed.add_field(name='Position in queue', value=position, inline=False)
            if song.thumbnail:
                embed.set_thumbnail(url=song.thumbnail)
        else:
            # This is a single song being played immediately
            await ctx.send('**Playing :notes: `%s` - Now!**' % songs[0].name)
            return
        await ctx.send(embed=embed)






    ##### Methods to get songs matching a link or query #####



    async def songs_from_query(self, ctx, query, source='YouTube'):
        # Return a list of Song objects matching a query,
        # which can be either a URL or a search term
        if is_url(query):
            try:
                songs = (await songs_from_url(query, ctx.author))
            except Exception as e:
                songs = []
            if not songs:
                await ctx.send('**:x: Error playing songs from `%s`: `%s`**' % (query, e))
        else:
            search_key, emoji = SEARCH_INFO[source]
            await ctx.send('**%s Searching :mag: `%s`**' % (emoji, query))
            songs = (await songs_from_search(query, ctx.author, 1, search_key))
            if not songs:
                await ctx.send('**:x: There were no results matching the query**')
        return (await self.trim_songs(ctx, songs))




    async def songs_from_search(self, ctx, query, source='YouTube'):
        # Find the top 10 matches to a query, and prompt the user to choose one of them
        if ctx.channel in self.searching_channels:
            await ctx.send('**:warning: Search is already running in this channel, type `cancel` to exit**')
            return
        search_key, emoji = SEARCH_INFO[source]
        await ctx.send('**%s Searching :mag: `%s`**' % (emoji, query))
        songs = (await songs_from_search(query, ctx.author, 10, search_key))
        if songs:
            # Print out an embed of the songs
            description = '\n\n'.join(['`%d.` %s **[%s]**' % (i+1, format_link(song),
                                                              format_time(song.length / self.get_adjusted_tempo())) \
                                       for i, song in enumerate(songs)])
            description += '\n\n\n\n**Type a number to make a choice, Type `cancel` to exit**'
            embed = discord.Embed(description=description)
            embed.set_author(name=(ctx.author.nick or ctx.author.name), icon_url=ctx.author.avatar.url)
            embed_message = (await ctx.send(embed=embed))
        else:
            # No results
            await ctx.send('**:x: There were no results matching the query**')
            return
        # Wait for the user who made the search query to reply
        def check(m):
            if (m.channel == ctx.channel) and (m.author == ctx.author):
                return m.content.strip().lower() in ('cancel',) + tuple(map(str, range(1, len(songs)+1)))
        self.searching_channels.append(ctx.channel)
        try:
            result = (await self.bot.wait_for('message', check=check, timeout=30))
        except asyncio.TimeoutError:
            await ctx.send('**:no_entry_sign: Timeout**')
            result = None
        self.searching_channels.remove(ctx.channel)
        await embed_message.delete()
        if result is None:
            return
        m = result.content.strip().lower()
        if m == 'cancel':
            await ctx.send(':white_check_mark:')
            return
        song = songs[int(m) - 1]
        if not (await self.trim_songs(ctx, [song])):
            return # the user can't queue this song for some reason
        song.process()
        return [song]
    




    async def trim_songs(self, ctx, songs, anonymous=False):
        # This method takes a list of songs, and removes any that are not allowed to be there due to bot settings.
        # Unless at least one bot setting has been changed from its default value, this method will return the
        # whole list of songs and filter nothing out.
        name = getattr(songs, 'name', None)
        link = getattr(songs, 'link', None)
        if not songs:
            # nothing to do
            return []
        # if self.djplaylists is True, this blocks non-DJs from queueing more than one song at a time
        if (len(songs) > 1) and self.djplaylists and (not anonymous) and self.is_dj(ctx.author):
            await ctx.send('**:x: The server is currently in DJ Only Playlists mode. Only DJs can queue playlists!**')
            return []
        # If self.preventduplicates is True, this removes any songs that are already on the queue
        if self.preventduplicates:
            songs = list(songs)
            for i, song in enumerate(songs):
                if (song in self.queue) or (song in songs[:i]):
                    songs.remove(song)
                    if not anonymous:
                        await ctx.send('**:x: `%s` has already been added to the queue**' % song.name)
        # If self.maxqueuelength is not None, this removes any songs that exceed the length
        if self.maxqueuelength > 0:
            if len(self.queue) >= self.maxqueuelength:
                if not anonymous:
                    await ctx.send('**:x: Cannot queue up any new songs because the queue is full**')
                return []
            elif len(self.queue) + len(songs) > self.maxqueuelength:
                if not anonymous:
                    songs = songs[:self.maxqueuelength - len(self.queue)]
                await ctx.send('**:warning: Shortening playlist due to reaching the song queue limit**')
        # If self.maxusersongs is not None, this removes any songs queued by this user that exceed the limit
        if (self.maxusersongs > 0) and not anonymous:
            nuser = len([song for song in self.queue if song.user == songs[0].user])
            if nuser >= self.maxusersongs:
                await ctx.send('**:x: Unable to queue song, you have reached the maximum songs you can have in the queue**')
                return []
            elif nuser + len(songs) > self.maxusersongs:
                songs = songs[:self.maxusersongs - nuser]
                await ctx.send('**:warning: Shortening playlist due to reaching the maximum songs you can have in the queue**')
        # Make sure the name and url are preserved
        if (name is not None) or (link is not None):
            if not isinstance(songs, Playlist):
                songs = Playlist(songs)
                songs.name = name
                songs.link = link
        return songs

    




    ##### Playing commands #####


    async def play(self, ctx, songs):
        # Place the songs at the bottom of the queue
        async with self.mutex:
            n = len(self.queue)
            self.queue.extend(songs)
            await self.enqueue_message(ctx, n, songs)
            await self.wake_up()


    async def playtop(self, ctx, songs):
        # Place the songs at the top of the queue
        async with self.mutex:
            self.queue.extendleft(songs[::-1])
            await self.enqueue_message(ctx, 0, songs)
            await self.wake_up()


    async def playskip(self, ctx, songs):
        # Place the songs at the top of the queue and then skip to the next song
        async with self.mutex:
            self.queue.extendleft(songs[::-1])
            await self.enqueue_message(ctx, 0, songs, now=True)
            await self.skip(ctx)


    async def playshuffle(self, ctx, songs):
        # Place the songs in the queue, then shuffle the queue
        async with self.mutex:
            now = not (self.now_playing or self.queue)
            self.queue.extend(songs)
            random.shuffle(self.queue)
            await self.enqueue_message(ctx, 0, songs, now=now, shuffle=True)
            await self.wake_up()



    ##### Seeking commands #####


    async def seek(self, ctx, time):
        # Seek to a specified time in the now playing song
        async with self.mutex:
            if self.now_playing:
                time = max(time, 0)
                if time > self.now_playing.adjusted_length:
                    await self.skip(ctx, forward=False) # don't break out of a loop
                elif (self.voice_client is not None) and (self.voice_client.is_playing() or self.voice_client.is_paused()):
                    self.seek_pos = time
                    self.voice_client.stop()
                    await ctx.send('**:thumbsup: Seeking to time `%s`**' % format_time(time))


    async def rewind(self, ctx, time):
        # Rewind by a certain number of seconds
        time = (self.get_current_time() or 0) - time
        await self.seek(ctx, time)


    async def forward(self, ctx, time):
        # Skip forward by a certain number of seconds
        time = (self.get_current_time() or 0) + time
        await self.seek(ctx, time)



    ##### Looping commands #####
        

    async def loop(self, ctx, on):
        # Turn song looping on or off (or toggle if on is None)
        async with self.mutex:
            self.looping = (not self.looping) if (on is None) else on
            if self.looping:
                await ctx.send('**:repeat_one: Enabled!**')
            else:
                await ctx.send('**:repeat_one: Disabled!**')
        

    async def loopqueue(self, ctx, on):
        # Turn queue looping on or off (or toggle if on is None)
        async with self.mutex:
            self.queue_looping = (not self.queue_looping) if (on is None) else on
            if self.queue_looping:
                await ctx.send('**:repeat: Enabled!**')
            else:
                await ctx.send('**:repeat: Disabled!**')



    ##### Skipping commands #####


    async def voteskip(self, ctx):
        # Vote to skip the currently playing song
        async with self.mutex:
            if len(self.voice_channel.members) <= 3:
                await self.skip(ctx)
            elif ctx.author in self.votes:
                await ctx.send('**:x: You already voted to skip the current song** (%d/%d people)' \
                               % (len(self.votes), int(.75 * (len(self.voice_channel.members) - 1))))
            else:
                self.votes.append(ctx.author)
                if len(self.votes) >= int(.75 * (len(self.voice_channel.members) - 1)):
                    await self.skip(ctx)
                else:
                    await ctx.send('**Skipping?** (%d/%d people)%s' \
                                   % (len(self.votes), int(.75 * (len(self.voice_channel.members) - 1)),
                                      ' **`%sforceskip` or `%sfs` to force**' % (self.prefix, self.prefix) \
                                      if self.is_dj(ctx.author) else ''))


    async def skipto(self, ctx, position):
        # Skip to the song at a specified position in the queue
        async with self.mutex:
            for n in range(position-1):
                if self.queue:
                    song = self.queue.popleft()
                    if self.queue_looping:
                        self.queue.append(song)
                else:
                    break
            await self.skip(ctx)


    async def skipback(self, ctx):
        # Skip backwards to the previously played song in the history
        async with self.mutex:
            await self.skip(ctx, forward=False, backward=True)





    ##### Pausing commands #####


    async def pause(self, ctx):
        # Pause the currently playing song
        async with self.mutex:
            if self.last_paused is None:
                self.voice_client.pause()
                self.last_paused = time.time()
                await ctx.send('**Paused :pause_button:**')
            else:
                await ctx.send('**:no_entry_sign: Already paused**')


    async def resume(self, ctx):
        # Resume the currently playing song
        async with self.mutex:
            if self.last_paused is not None:
                self.voice_client.resume()
                self.last_started_playing += (time.time() - self.last_paused)
                self.last_paused = None
                await ctx.send('**:play_pause: Resuming :thumbsup:**')
            else:
                await ctx.send('**:no_entry_sign: Already playing**')




    ##### Queue modification commands #####


    async def shuffle(self, ctx):
        # Shuffle the queue
        async with self.mutex:
            random.shuffle(self.queue)
            await ctx.send('**:twisted_rightwards_arrows: Shuffled queue :ok_hand:**')


    async def move(self, ctx, old, new):
        # Move a song to a different spot in the queue
        async with self.mutex:
            if not ((1 <= old <= len(self.queue)) and (1 <= new <= len(self.queue))):
                await ctx.send('**:x: Invalid position, should be between 1 and %d**' % len(self.queue))
            else:
                song = self.queue[old - 1]
                del self.queue[old - 1]
                self.queue.insert(new - 1, song)
                await ctx.send('**:white_check_mark: Moved `%s` to position %d in the queue**' % (song.name, new))


    async def remove(self, ctx, position):
        # Remove a song from the queue
        async with self.mutex:
            if not (1 <= position <= len(self.queue)):
                await ctx.send('**:x: Invalid position, should be between 1 and %d**' % len(self.queue))
            else:
                song = self.queue[position - 1]
                del self.queue[position - 1]
                await ctx.send('**:white_check_mark: Removed `%s`**' % song.name)


    async def remove_range(self, ctx, start, end):
        # Remove a whole range of songs from the queue
        async with self.mutex:
            if not (1 <= start <= end <= len(self.queue)):
                await ctx.send('**:x: Invalid position, should be between 1 and %d**' % len(self.queue))
            else:
                removed = tuple(self.queue)[start-1 : end]
                for song in removed:
                    self.queue.remove(song)
                await ctx.send('**:white_check_mark: Removed `%d` songs**' % len(removed))


    async def clear(self, ctx, user):
        # Remove all the songs from the queue
        async with self.mutex:
            if user is None:
                self.queue.clear()
                await ctx.send('***:boom: Cleared... :stop_button:***')
            else:
                # only remove the songs queued up by this particular user
                n = 0
                for song in tuple(self.queue):
                    if song.user == user:
                        self.queue.remove(song)
                        n += 1
                await ctx.send('**:thumbsup: %d song%s removed from the queue**' % (n, '' if n == 1 else 's'))


    async def leavecleanup(self, ctx):
        # Remove all songs queued by absent users
        async with self.mutex:
            n = 0
            for song in tuple(self.queue):
                if song.user not in self.voice_channel.members:
                    self.queue.remove(song)
                    n += 1
            await ctx.send('**:thumbsup: %d song%s removed from the queue**' % (n, '' if n == 1 else 's'))


    async def removedupes(self, ctx, quiet=False):
        # Remove all duplicate songs from the queue
        async with self.mutex:
            t = tuple(self.queue)
            n = 0
            for i, song in enumerate(t):
                if song in t[:i]:
                    self.queue.remove(song)
                    n += 1
            if (n or not quiet):
                await ctx.send('**:thumbsup: %d song%s removed from the queue**' % (n, '' if n == 1 else 's'))




    ##### Setting commands #####


    async def settings_show(self, ctx):
        # Show the settings embed
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
        await ctx.send(embed=embed)


    async def settings_prefix(self, ctx, prefix):
        # Query or modify the prefix
        if prefix is None:
            # query the prefix
            embed = discord.Embed(title='Bluez Settings - :exclamation: Prefix',
                                  description='Changes the prefix used to address Bluez bot.')
            embed.add_field(name=':page_facing_up: Current Setting:', value='`%s`' % self.prefix)
            embed.add_field(name=':pencil2: Update:', value='`%ssettings prefix [New Prefix]`' % self.prefix)
            embed.add_field(name=':white_check_mark: Valid Settings', value='`Any text, at most 5 characters (e.g. !)`')
            await ctx.send(embed=embed)
        else:
            # modify the prefix
            self.prefix = prefix
            await ctx.send('**:thumbsup: Prefix set to `%s`**' % prefix)


    async def settings_blacklist(self, ctx, channel):
        # Query or modify the blacklist
        if channel is None:
            # query the blacklist
            embed = discord.Embed(title='Bluez Settings - :no_entry_sign: Blacklist',
                                  description='Keyword `blacklist` also removes channels from Blacklist')
            embed.add_field(name=':page_facing_up: Current Setting:',
                            value=('`%s`' % ', '.join([channel.mention for channel in self.blacklist]) \
                                   if self.blacklist else 'Blacklist empty'))
            embed.add_field(name=':pencil2: Update:', value='`%ssettings blacklist [Mention Channel]`' % self.prefix)
            embed.add_field(name=':white_check_mark: Valid Settings:', value='`Any number of mentioned text channels`')
            await ctx.send(embed=embed)
        else:
            # modify the blacklist
            if channel not in self.blacklist:
                self.blacklist.append(channel)
                await ctx.send('**Blacklisted `%s`**' % channel.name)
            else:
                self.blacklist.remove(channel)
                await ctx.send('**Unblacklisted `%s`**' % channel.name)


    async def settings_autoplay(self, ctx, playlist):
        # Query or modify the autoplay playlist
        if playlist is None:
            # query the playlist
            if self.autoplay:
                await ctx.send('**:musical_note: AutoPlay playlist link:** %s' % self.autoplay)
            else:
                await ctx.send('**:musical_note: No AutoPlay playlist currently configured**')
        else:
            # modify the playlist
            if playlist in ('', 'disable'):
                self.autoplay = None
                await ctx.send('**:no_entry_sign: AutoPlay disabled**')
            else:
                try:
                    await songs_from_url(playlist, ctx.author)
                except Exception as e:
                    await ctx.send('**:x: Error finding songs from `%s`: `%s`**' % (playlist, e))
                else:
                    self.autoplay = playlist
                    await ctx.send('**:white_check_mark: Success**')


    async def settings_announcesongs(self, ctx, on):
        # Query or modify the announcesongs flag
        if on is None:
            # query the flag
            await ctx.send('**:%s: Announcing new songs is currently turned %s**' % \
                           ('bell' if self.announcesongs else 'no_bell',
                            'on' if self.announcesongs else 'off'))
        else:
            # modify the flag
            self.announcesongs = on
            if on:
                await ctx.send('**:bell: I will now announce new songs**')
            else:
                await ctx.send('**:no_bell: I will not announce new songs**')


    async def settings_maxqueuelength(self, ctx, length):
        # query or modify the maximum queue length
        if length is None:
            # query the length
            if self.maxqueuelength == 0:
                await ctx.send('**:hash: Max queue length disabled**')
            else:
                await ctx.send('**:hash: Max queue length set to %d**' % self.maxqueuelength)
        else:
            # modify the length
            self.maxqueuelength = length
            if length == 0:
                await ctx.send('**:no_entry_sign: Max queue length disabled**')
            else:
                await ctx.send('**:white_check_mark: Max queue length set to %d**' % length)
                if (self.voice_channel is not None) and (len(self.queue) > length):
                    # if the queue is too full, go ahead and delete the excess songs now
                    n = len(self.queue) - length
                    self.queue = collections.deque(tuple(self.queue)[:length])
                    await ctx.send('**:thumbsup: %d song%s removed from the end of the queue**' % (n, '' if n == 1 else 's'))


    async def settings_maxusersongs(self, ctx, number):
        # query or modify the maximum number of songs a single user can post
        if number is None:
            # query the number
            if self.maxusersongs == 0:
                await ctx.send('**:1234: Max user song limit disabled**')
            else:
                await ctx.send('**:1234: Max user song limit set to %d**' % self.maxusersongs)
        else:
            # modify the number
            self.maxusersongs = number
            if number == 0:
                await ctx.send('**:no_entry_sign: Max user song limit disabled**')
            else:
                await ctx.send('**:white_check_mark: Max user song limit set to %d**' % number)
                if (self.voice_channel is not None) and self.queue:
                    # go ahead and check through the queue now for excess songs
                    counter = collections.Counter()
                    n = 0
                    for song in tuple(self.queue):
                        counter[song.user.id] += 1
                        if counter[song.user.id] > number:
                            self.queue.remove(song)
                            n += 1
                    if n:
                        await ctx.send('**:thumbsup: %d song%s removed from the queue**' % (n, '' if n == 1 else 's'))


    async def settings_preventduplicates(self, ctx, on):
        # query or modify the preventduplicates flag
        if on is None:
            # query the flag
            await ctx.send('**:notes: Duplicate prevention is currently turned %s**' % \
                           ('on' if self.preventduplicates else 'off'))
        else:
            # modify the flag
            self.preventduplicates = on
            if on:
                await ctx.send('**:white_check_mark: I will automatically prevent duplicate songs**')
                if (self.voice_channel is not None) and self.queue:
                    await self.removedupes(ctx, quiet=True)
            else:
                await ctx.send('**:no_entry_sign: I will not prevent duplicate songs**')


    async def settings_defaultvolume(self, ctx, volume):
        # query or modify the default volume
        if volume is None:
            # query the default volume
            await ctx.send('**:loud_sound: Default volume level is currently %d**' % round(200 * self.defaultvolume))
        else:
            # modify the default volume
            self.defaultvolume = volume / 200.0
            await ctx.send('**:loud_sound: Default volume is now set to %d**' % volume)


    async def settings_djplaylists(self, ctx, on):
        # query or modify the djplaylists flag
        if on is None:
            # query the flag
            await ctx.send('**:1234: DJ Only Playlists mode is currently turned %s**' % \
                           ('on' if self.djplaylists else 'off'))
        else:
            # modify the flag
            self.djplaylists = on
            if on:
                await ctx.send('**:white_check_mark: DJ Only Playlists enabled**')
            else:
                await ctx.send('**:no_entry_sign: DJ Only Playlists disabled**')


    async def settings_djonly(self, ctx, on):
        # query or modify the djonly flag
        if on is None:
            # query the flag
            await ctx.send('**:no_pedestrians: DJ Only mode is currently turned %s**' % \
                           ('on' if self.djonly else 'off'))
        else:
            # modify the flag
            self.djonly = on
            if on:
                await ctx.send('**:white_check_mark: DJ Only mode enabled**')
            else:
                await ctx.send('**:no_entry_sign: DJ Only mode disabled**')


    async def settings_djrole(self, ctx, role):
        # query or modify the DJ role
        if role is None:
            # query the DJ role
            await ctx.send('**:page_with_curl: The DJ Role here is `%s`**' % self.djrole)
        else:
            # modify the DJ role
            self.djrole = role.name
            await ctx.send('**:page_with_curl: DJ role set to `%s`**' % role.name)


    async def settings_alwaysplaying(self, ctx, on):
        # query or modify the alwaysplaying flag
        if on is None:
            # query the flag
            await ctx.send('**:infinity: Always Playing mode is currently turned %s**' %
                           ('on' if self.alwaysplaying else 'off'))
        else:
            # modify the flag
            self.alwaysplaying = on
            if on:
                await ctx.send('**:white_check_mark: Always Playing mode enabled**')
            else:
                await ctx.send('**:no_entry_sign: Always Playing mode disabled**')


    async def settings_reset(self, ctx):
        # reset the settings
        if (await yesno(ctx, '**:warning: You are about to reset all settings to their defaults. Continue?**')):
            self.reset_settings()
            await ctx.send('**:white_check_mark: All settings have been reset to their defaults**')
        
        




    ##### Effect commands #####


    async def effects_show(self, ctx):
        # Show the audio effect settings
        embed = discord.Embed(title='Current audio effect settings',
                              description='''\
:man_running: Speed - %.3g

:musical_score: Pitch - %.3g (%+.3g semitones)

:guitar: Bass - %d

:crescent_moon: Nightcore - %s

:stopwatch: Slowed - %s

:loud_sound: Volume - %d''' % (self.tempo, self.pitch, 12*math.log2(self.pitch),
                               self.bass, 'On' if self.nightcore else 'Off',
                               'On' if self.slowed else 'Off', round(200 * self.volume)))
        await ctx.send(embed=embed)


    async def effects_help(self, ctx):
        # Describe the audio effect settings
        embed = discord.Embed(title='Bluez audio effects',
                              description='''\
`%sspeed <0.3 - 3>` - adjust the speed of the song playing
`%spitch <0.3 - 3>` - adjust the pitch of the song playing
`%sbass <1 - 5>` - adjust the bass boost
`%snightcore` - toggle the nightcore effect on or off
`%sslowed` - toggle the slowed effect on or off
`%svolume <1-200>` - adjust the volume of the song playing''' % ((self.prefix,) * 6))
        await ctx.send(embed=embed)


    async def effects_clear(self, ctx):
        # Reset all effects to default
        if (self.tempo == self.pitch == self.bass == 1) and not (self.nightcore or self.slowed) and (self.volume == self.defaultvolume):
            await ctx.send('**:thumbsup: All effects are already at their default values**')
        elif (await yesno(ctx, '**:warning: You are about to reset all audio effects to their defaults. Continue?**')):
            async with self.mutex:
                self.reset_effects()
                self.update_audio()
            await ctx.send('**:white_check_mark: All audio effects have been reset to their defaults**')


    async def effect_speed(self, ctx, speed):
        # Query or modify the speed effect
        if speed is None:
            # query the speed
            await ctx.send('**:man_running: Current playback speed is set to %.3g**' % self.tempo)
        else:
            # modify the speed
            if self.tempo != speed:
                async with self.mutex:
                    self.tempo = speed
                    self.update_audio()
            await ctx.send('**:white_check_mark: Playback speed set to %.3g**' % speed)


    async def effect_pitch_scale(self, ctx, scale):
        # Query or modify the pitch effect
        if scale is None:
            # query the pitch
            await ctx.send('**:musical_score: Current playback frequency multiplier is set to %.3g**' % self.pitch)
        else:
            # modify the pitch
            if self.pitch != scale:
                async with self.mutex:
                    self.pitch = scale
                    self.update_audio()
            await ctx.send('**:white_check_mark: Playback frequency multiplier set to %.3g**' % scale)


    async def effect_pitch_steps(self, ctx, steps):
        # Query or modify the pitch effect, in semitones
        if steps is None:
            # query the pitch
            await ctx.send('**:musical_score: Current playback pitch is shifted by %.3g semitones**' % (12*math.log2(self.pitch)))
        else:
            # modify the pitch
            scale = 2.0 ** (steps/12.0)
            if self.pitch != scale:
                async with self.mutex:
                    self.pitch = scale
                    self.update_audio()
            await ctx.send('**:white_check_mark: Playback pitch shifted by %.3g semitones**' % steps)


    async def effect_bassboost(self, ctx, bass):
        # Query or modify the bass boost effect
        if bass is None:
            # query the bass boost
            await ctx.send('**:guitar: Current bass boost is set to %d**' % self.bass)
        else:
            # modify the bass boost
            if self.bass != bass:
                async with self.mutex:
                    self.bass = bass
                    self.update_audio()
            await ctx.send('**:white_check_mark: Bass boost set to %d**' % self.bass)


    async def effect_nightcore(self, ctx, on):
        # Modify the nightcore effect
        if (on is None) or (on != self.nightcore):
            async with self.mutex:
                self.nightcore = (not self.nightcore) if (on is None) else on
                self.update_audio()
        await ctx.send('**:white_check_mark: Nightcore effect turned %s**' % ('on' if self.nightcore else 'off'))


    async def effect_slowed(self, ctx, on):
        # Modify the slowed effect
        if (on is None) or (on != self.slowed):
            async with self.mutex:
                self.slowed = (not self.slowed) if (on is None) else on
                self.update_audio()
        await ctx.send('**:white_check_mark: Slowed effect turned %s**' % ('on' if self.nightcore else 'off'))


    async def effect_volume(self, ctx, volume):
        # Query or modify the playback volume
        if volume is None:
            # query the volume
            await ctx.send('**:loud_sound: Volume is currently set to %d**' % round(200 * self.volume))
        else:
            if self.volume != volume / 200.0:
                async with self.mutex:
                    self.volume = volume / 200.0
                    self.update_audio()
            await ctx.send('**:white_check_mark: Volume set to %d**' % volume)





    ##### Pruning command #####

    async def prune(self, ctx, number):
        count = 0
        async for message in ctx.channel.history(after = datetime.datetime.now() - datetime.timedelta(hours=24),
                                                 oldest_first = False):
            # iterate over messages in the pruned channel within the last 24 hours
            if message.author == self.bot.user:
                await message.delete()
                count += 1
                if 0 < number <= count:
                    break
        if ctx.interaction is not None:
            # we require a reply
            await ctx.send('**:thumbsup: %d messages deleted**' % count)




    ##### Voice channel notifications #####


    async def notify_user_join(self, member):
        # Called when a user joins a voice channel that this bot is in
        async with self.mutex:
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
        async with self.mutex:
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






    ##### Loading/saving settings #####


    def load_settings(self):
        # Load the bot settings from Google drive
        if not BLUEZ_SETTINGS_PATH:
            return # no settings path
        filename = os.path.join(BLUEZ_SETTINGS_PATH, 'bluez_settings_%d.txt' % self.guild.id)
        try:
            with open(filename, 'r') as o:
                settings = o.read()
        except IOError:
            return # unable to find settings file; use defaults
        for line in settings.splitlines():
            if ':' not in line:
                continue
            setting, value = [i.strip() for i in line.split(':', 1)]
            setting = setting.lower()
            # Boolean settings
            if setting in ('announcesongs', 'preventduplicates', 'djonly', 'djplaylists', 'alwaysplaying'):
                try:
                    setattr(self, setting, bool(int(value)))
                except ValueError:
                    logging.warning('illegal value for setting %r: %r' % (setting, value))
            # Integer settings
            elif setting in ('maxqueuelength', 'maxusersongs', 'defaultvolume'):
                try:
                    setattr(self, setting, int(value))
                except ValueError:
                    logging.warning('illegal value for setting %r: %r' % (setting, value))
                else:
                    if setting == 'defaultvolume':
                        self.defaultvolume /= 200.0
            # String settings
            elif setting in ('prefix', 'djrole', 'autoplay'):
                if (setting == 'prefix') and (len(value) > 5):
                    logging.warning('prefix %r too long' % value)
                else:
                    setattr(self, setting, value)
                    if (setting == 'autoplay') and not value:
                        self.autoplay = None
            # Blacklist settings
            elif setting == 'blacklist':
                if value.strip():
                    try:
                        blacklist_ids = list(map(int, map(str.strip, value.split(','))))
                    except ValueError:
                        logging.warning('illegal blacklist %r' % value)
                    else:
                        self.blacklist = [channel for channel in self.guild.text_channels if channel.id in blacklist_ids]
                else:
                    self.blacklist = []



    def save_settings(self):
        # Save the bot settings to Google drive
        if not BLUEZ_SETTINGS_PATH:
            return # no settings path
        filename = os.path.join(BLUEZ_SETTINGS_PATH, 'bluez_settings_%d.txt' % self.guild.id)
        settings = '''\
PREFIX            : %s
BLACKLIST         : %s
AUTOPLAY          : %s
ANNOUNCESONGS     : %d
MAXQUEUELENGTH    : %d
MAXUSERSONGS      : %d
PREVENTDUPLICATES : %d
DEFAULTVOLUME     : %d
DJPLAYLISTS       : %d
DJONLY            : %d
DJROLE            : %s
ALWAYSPLAYING     : %d''' % \
(self.prefix, ','.join([str(channel.id) for channel in self.blacklist]),
 self.autoplay or '', self.announcesongs, self.maxqueuelength,
 self.maxusersongs, self.preventduplicates, self.defaultvolume * 200,
 self.djplaylists, self.djonly, self.djrole, self.alwaysplaying)
        try:
            with open(filename, 'w') as o:
                o.write(settings)
        except IOError:
            pass # unable to create settings file
                    
    
