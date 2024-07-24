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
BLUEZ_DOWNLOAD_PATH = os.getenv('BLUEZ_DOWNLOAD_PATH')

MAX_HISTORY_LEN = 100

Lock = DebugLock if BLUEZ_DEBUG else asyncio.Lock





class Player(object):

    def __init__(self, bot, guild):
        self.bot = bot
        self.guild = guild
        self.queue = collections.deque()
        self.history = collections.deque(maxlen=MAX_HISTORY_LEN)
        self.current_history = collections.deque(maxlen=MAX_HISTORY_LEN)
        self.reset_settings()
        self.reset()
        if not self.load_settings():
            # create a new settings file with default settings
            # if one doesn't exist yet
            self.save_settings()
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


    def clear_downloads(self):
        if BLUEZ_DOWNLOAD_PATH:
            try:
                for filename in os.listdir(BLUEZ_DOWNLOAD_PATH):
                    os.remove(os.path.join(BLUEZ_DOWNLOAD_PATH, filename))
            except OSError:
                pass


    def reset(self):
        self.text_channel = None
        self.voice_channel = None
        self.voice_client = None
        self.now_playing = None
        self.queue.clear()
        self.current_history.clear()
        self.queue_end = 0
        self.looping = False
        self.queue_looping = False
        self.votes = []
        self.empty_paused = False
        self.skip_forward = False
        self.skip_backward = False
        self.idle_task = None
        self.last_started_playing = None
        self.last_paused = None
        self.seek_pos = None
        self.stderr = tempfile.TemporaryFile()
        self.reset_effects()
        self.clear_downloads()



    # Coroutines to ensure that a certain condition is met before proceeding further

    async def ensure_connected(self, ctx):
        # Make sure the bot has joined some voice channel
        if self.voice_channel is None:
            await ctx.send(f'**:x: I am not connected to a voice channel.** Type `{self.prefix}join` to get me in one')
            return False
        return True


    async def ensure_playing(self, ctx):
        # Make sure the bot is currently playing something
        if not (await self.ensure_connected(ctx)):
            return False
        if self.now_playing is None:
            await ctx.send(f'**:x: I am not currently playing anything.** Type `{self.prefix}play` to play a song')
            return False
        return True


    async def ensure_queue(self, ctx):
        # Make sure the bot has some songs queued
        if not (await self.ensure_connected(ctx)):
            return False
        if not self.queue:
            await ctx.send(f'**:x: The queue is currently empty.** Type `{self.prefix}play` to play a song')
            return False
        return True


    async def ensure_history(self, ctx):
        # Make sure the bot has a history
        if not self.history:
            await ctx.send(f'**:x: There are no songs in the history.** Type `{self.prefix}play` to play a song')
            return False
        return True


    async def ensure_current_history(self, ctx):
        # Make sure the bot has a current history
        if not self.current_history:
            await ctx.send(f'**:x: There are no songs in the history since the bot was last disconnected.** Type `{self.prefix}play` to play a song')
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
        return member.guild_permissions.manage_channels or ctx.author.guild_permissions.administrator or \
               discord.utils.get(member.roles, name=self.djrole) or discord.utils.get(member.roles, name='DJ')


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
        self.text_channel = ctx.channel
        self.voice_channel = voice_channel
        self.voice_client = (await voice_channel.connect())
        await ctx.send(f'**:thumbsup: Joined `{voice_channel.name}` and bound to {ctx.channel.mention}**')
        if self.autoplay:
            try:
                songs = (await songs_from_url(self.autoplay, self.bot.user))
            except Exception as e:
                await ctx.send(f'**:x: Error playing songs from `{self.autoplay}`: `{e}`**')
            else:
                songs = (await self.trim_songs(ctx, songs, 'Shuffle', False, anonymous=True))
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
                errmsg = None
                if self.now_playing:
                    error = (error or get_error(self.stderr))
                    if error:
                        strerror = str(error)
                        if self.should_retry(strerror):
                            # this is legacy code; should_retry() currently always returns False
                            self.seek_pos = None
                            retrying = True
                            await self.now_playing.reload()
                        elif self.should_ignore(strerror):
                            logging.warning(strerror)
                        else:
                            if isinstance(error, Exception):
                                log_exception(error)
                            errmsg = (await self.text_channel.send(f'**:x: Error playing `{self.now_playing.name}`: `{error}`**'))
                # Figure out what song to play next
                if (self.seek_pos is None) and not retrying:
                    if self.looping and (self.now_playing is not None) and not (self.skip_forward or self.skip_backward or errmsg):
                        # play the same song again
                        # if either the user skipped (using !skip, !forceskip, !back, etc.) or an error happened when playing the song,
                        # do not loop this song anymore and move on to the next one.
                        pass
                    elif self.queue and not self.skip_backward:
                        # play the next song in the queue
                        if errmsg and self.queue_looping:
                            # remove the now-playing song from the queue if it's in there
                            # since it caused an error when we tried to play it
                            try:
                                self.queue.remove(self.now_playing)
                            except ValueError:
                                pass
                        self.now_playing = self.queue.popleft()
                        if self.queue_looping:
                            # put the just-finished song on the end of the queue
                            self.queue.append(self.now_playing)
                        if (self.queue_end > 0) and not ((self.queue_end == len(self.queue)) and self.queue_looping):
                            self.queue_end -= 1
                        self.skip_forward = False
                    elif (len(self.current_history) > 1) and self.skip_backward:
                        # play the previous song in the history
                        if self.now_playing:
                            # put the currently playing song, if any, back onto the queue
                            self.queue.appendleft(self.now_playing)
                            self.queue_end += 1
                            self.current_history.pop()
                        self.now_playing, timestamp = self.current_history.pop()
                        self.skip_backward = False
                    else:
                        if self.skip_backward:
                            # no previous song in the history
                            self.current_history.clear()
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
                    if self.last_paused is not None:
                        # The bot will remember if it was paused if you skip, seek, or mess with audio effects.
                        # The only exception is that if you skip past the end of the queue and the bot becomes idle,
                        # it forgets it was paused. (Otherwise the behavior might be confusing.)
                        self.last_paused = now
                        self.voice_client.pause()
                    if (self.seek_pos is None) and not retrying:
                        entry = (self.now_playing, datetime.datetime.utcnow())
                        self.history.append(entry)
                        self.current_history.append(entry)
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
            emoji = ('rewind' if backward else 'fast_forward')
            await ctx.send(f'***:{emoji}: Skipped :thumbsup:***')
            self.skip_forward = forward
            self.skip_backward = backward
            if self.voice_client.is_playing() or self.voice_client.is_paused():
                self.voice_client.stop()
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
        return False
        #return 'Server returned 403 Forbidden (access denied)' in errmsg # try again for this stupid bug


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



    async def np_message(self, ctx):
        # Send the now_playing message to the appropriate channel
        if self.now_playing:
            song = self.now_playing
            time = self.get_current_time()
            progress_bar = ['\u25ac'] * 30
            if (time is not None) and song.adjusted_length:
                progress_bar[min(int((time * 30) / song.adjusted_length), 29)] = '\U0001f518'
            progress_bar = ''.join(progress_bar)
            if time is None:
                time_message = 'Not started yet'
            else:
                time_message = f'{format_time(time)} / {format_time(song.adjusted_length)}'
                if self.voice_client.is_paused():
                    time_message += ' (paused)'
            embed = discord.Embed(description = f'{format_link(song)}\n\n`{progress_bar}`\n\n`{time_message}`\n\n`Requested by:` {format_user(song.user)}',
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
            embed = discord.Embed(title=f'Queue for {ctx.guild.name}', color=color)
            description = ''
            if i == 0:
                if self.now_playing:
                    description += f'__Now Playing:__\n{format_link(self.now_playing)} | '
                    description += f'`{format_time(self.now_playing.adjusted_length)} Requested by {format_user(self.now_playing.user)}`\n\n'
                description += '__Up Next:__\n'
            for j, song in enumerate(tuple(self.queue)[10*i : 10*(i+1)], 10*i+1):
                if j == self.queue_end + 1:
                    description += '\u25ac' * 20 + '\n\n'
                description += f'`{j}.` {format_link(song)} | '
                description += f'`{format_time(song.length / self.get_adjusted_tempo())} Requested by {format_user(song.user)}`\n\n'
            description += f'**{n} songs in queue | {total} total length**\n\n'
            embed.description = description
            footer = f'Page {i+1}/{npages} | '
            footer += 'Loop: ' + ('\u2705' if self.looping else '\u274c') + ' | '
            footer += 'Queue Loop: ' + ('\u2705' if self.queue_looping else '\u274c')
            embed.set_footer(text=footer, icon_url=ctx.author.avatar.url)
            embeds.append(embed)
        await post_multipage_embed(ctx, embeds, start_index)



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
            embed = discord.Embed(title=f'History for {ctx.guild.name} (`{tzname}` time zone)', color=color)
            description = ''
            for song, timestamp in tuple(self.history)[-10*(i+1) : ((-10*i) or None)]:
                if timezone:
                    timestamp = timestamp.astimezone(timezone)
                strftime = timestamp.strftime('%x %X')
                description += f'`{strftime}` {format_link(song)} | `Requested by {format_user(song.user)}`\n\n'
            embed.description = description
            footer = f'Page {npages-i}/{npages}'
            embed.set_footer(text=footer,
                             icon_url=ctx.author.avatar.url)
            embeds.append(embed)
        embeds.reverse()
        await post_multipage_embed(ctx, embeds, npages-1)



    async def enqueue_message(self, ctx, position, songs, now=False, shuffle=False):
        # Send info on the recently enqueued song(s) to the appropriate channel
        if not songs:
            await ctx.send('**:warning: No songs were queued.**')
            return
        if shuffle:
            message = 'Shuffled into queue'
        else:
            message = 'Added to queue'
        if now or not self.queue:
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
        if self.last_paused is not None:
            time += ' (paused)'
        if len(songs) > 1:
            # This is a playlist
            embed = discord.Embed(description=format_link(songs))
            embed.set_author(name=f'Playlist {message.lower()}', icon_url=ctx.author.avatar.url)
            embed.add_field(name='Estimated time until playing', value=time, inline=False)
            embed.add_field(name='Position in queue', value=position, inline=True)
            embed.add_field(name='Enqueued', value=f'`{len(songs)}` song{plural(len(songs))}', inline=True)
        elif time != 'Now':
            # This is a single song being played at some point in the future
            song = songs[0]
            embed = discord.Embed(description=f'**{format_link(song)}**')
            embed.set_author(name=message, icon_url=ctx.author.avatar.url)
            if song.channel_url:
                channel_link = f'[{ESC(song.channel)}]({song.channel_url})'
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
            await ctx.send(f'**Playing :notes: `{songs[0].name}` - Now!**')
            return
        await ctx.send(embed=embed)






    ##### Methods to get songs matching a link or query #####


    async def songs_from_url(self, ctx, query, where, priority):
        # Return a list or Playlist of Song objects matching a URL
        await ctx.send(f'**:link: Playing songs from `{query}`**')
        try:
            songs = (await songs_from_url(query, ctx.author))
        except Exception as e:
            await ctx.send(f'**:x: Error playing songs from `{query}`: `{e}`**')
            return []
        else:
            if not songs:
                await ctx.send(f'**:x: No songs found at URL `{query}`**')
                return []
        return (await self.trim_songs(ctx, songs, where, priority))



    async def songs_from_query(self, ctx, query, where, priority, source='YouTube Video'):
        # Return a list of Song objects matching a query,
        # which can be either a URL or a search term
        if is_url(query):
            return (await self.songs_from_url(ctx, query, where, priority))
        search_key, emoji = SEARCH_INFO[source][:2]
        await ctx.send(f'**{emoji} Searching :mag: `{query}`**')
        try:
            songs = (await songs_from_search(query, ctx.author, 0, 1, search_key))
        except Exception as e:
            await ctx.send(f'**:x: Error searching {source} for `{query}`: `{e}`**')
            return []
        if not songs:
            await ctx.send('**:x: There were no results matching the query**')
            return []
        return (await self.trim_songs(ctx, songs, where, priority))



    async def playlist_from_query(self, ctx, query, where, priority, source='YouTube Playlist'):
        # Search YouTube for a playlist matching a query
        # (source is ignored for now)
        if is_url(query):
            return (await self.songs_from_url(ctx, query, where, priority))
        await ctx.send(f'**:arrow_forward: Searching :mag: `{query}`**')
        try:
            playlists = (await playlists_from_search(query, ctx.author, 0, 1))
        except Exception as e:
            await ctx.send(f'**:x: Error searching YouTube for playlist `{query}`: `{e}`**')
            return []
        if not playlists:
            await ctx.send('**:x: There were no results matching the query**')
            return []
        return (await self.trim_songs(ctx, playlists[0], where, priority))
        


    async def songs_from_search(self, ctx, query, where, priority, source='YouTube Video'):
        # Find the top song matches to a query, and prompt the user to choose one of them
        # return a 3-tuple (songs, where, priority)
        search_key, emoji = SEARCH_INFO[source][:2]
        view = SearchView(ctx, query, where, priority, self.is_dj(ctx.author),
                          search_key, tempo=self.get_adjusted_tempo())
        await view.open(emoji)
        await view.wait()
        if view.selection is None:
            return [], 'Bottom', False
        song = view.selection
        if not (await self.trim_songs(ctx, [song], where, priority)):
            return [], 'Bottom', False # the user can't queue this song for some reason
        await song.process()
        return [song], view.where, view.priority



    async def playlist_from_search(self, ctx, query, where, priority, source='YouTube Playlist'):
        # Find the top playlist matches to a query, and prompt the user to choose one of them
        # return a 3-tuple (playlist, where, priority)
        # (source is ignored for now)
        view = SearchView(ctx, query, where, priority, self.is_dj(ctx.author), playlists=True)
        await view.open()
        await view.wait()
        if view.selection is None:
            return [], 'Bottom', False
        playlist = view.selection
        await playlist.process()
        playlist = (await self.trim_songs(ctx, playlist, where, priority))
        return playlist, view.where, view.priority
    



    async def trim_songs(self, ctx, songs, where, priority, anonymous=False):
        # This method takes a list of songs, and removes any that are not allowed to be there due to bot settings.
        # Unless at least one bot setting has been changed from its default value, this method will return the
        # whole list of songs and filter nothing out.
        songs = songs.copy()
        if not songs:
            # nothing to do
            return []
        # if self.djplaylists is True, this blocks non-DJs from queueing more than one song at a time
        if (len(songs) > 1) and self.djplaylists and (not anonymous) and (not self.is_dj(ctx.author)):
            await ctx.send('**:x: The server is currently in DJ Only Playlists mode. Only DJs can queue playlists!**')
            return []
        # If self.maxqueuelength is not None, this removes any songs that exceed the length
        if (self.maxqueuelength > 0):
            if len(self.queue) >= self.maxqueuelength:
                if not anonymous:
                    await ctx.send('**:x: Cannot queue up any new songs because the queue is full**')
                return []
            elif len(self.queue) + len(songs) > self.maxqueuelength:
                del songs[self.maxqueuelength - len(self.queue):]
                if not anonymous:
                    await ctx.send('**:warning: Shortening playlist due to reaching the song queue limit**')
        # If self.maxusersongs is not None, this removes any songs queued by this user that exceed the limit
        if (self.maxusersongs > 0) and (not anonymous):
            nuser = len([song for song in self.queue if song.user == songs[0].user])
            if nuser >= self.maxusersongs:
                await ctx.send('**:x: Unable to queue song, you have reached the maximum songs you can have in the queue**')
                return []
            elif nuser + len(songs) > self.maxusersongs:
                del songs[self.maxusersongs - nuser:]
                await ctx.send('**:warning: Shortening playlist due to reaching the maximum songs you can have in the queue**')
        # If self.preventduplicates is True, this removes (or moves forward) any songs that are already on the queue
        # Note that max queue/user songs is checked first, and then this. Thus it's possible that a non-duplicate song is removed
        # from the end of the playlist, and then duplicate songs are removed later, resulting in the size of the queue being
        # less than the max. This is maybe not ideal, but the logic otherwise is too complicated.
        if self.preventduplicates:
            removed = []
            moved = []
            if where == 'Bottom':
                cutoff = (self.queue_end if priority else len(self.queue))
            elif where == 'Shuffle':
                cutoff = len(self.queue)
            else: # 'Top' or 'Now'
                cutoff = 0
            # Split the queue into "songs that will play before these songs play" and "songs that will play after".
            # If we're shuffling, the entire queue is considered "before" -- it doesn't make any difference in this case.
            pre_queue = tuple(self.queue)[:cutoff]
            post_queue = tuple(self.queue)[cutoff:]
            orig_songs = songs.copy()
            for i, song in enumerate(orig_songs):
                # If the song is already on the queue ahead of where we're going to insert it,
                # or if it appears more than once in the playlist, just throw it out of the playlist.
                if (song in pre_queue) or (song in orig_songs[:i]):
                    songs.remove(song)
                    removed.append(song)
                elif song in post_queue:
                    # if we're doing /playtop or /playskip; or if we're doing /play with priority,
                    # move the song forward if it's already in the queue, rather than just leaving it where it is.
                    self.queue.remove(song)
                    moved.append(song)
            if not anonymous:
                # Send messages for songs that were removed, and separately for songs that were moved forward.
                # It is rare, but possible, to get both messages. (For example, if you're using /playtop or /playskip
                # to play a playlist that has repeated songs on it.)
                if removed:
                    if len(removed) == 1:
                        await ctx.send(f'**:x: `{removed[0].name}` has already been added to the queue**')
                    else:
                        await ctx.send(f'**:x: {len(removed)} songs have been removed from this playlist since they are already on the queue**')
                if moved:
                    forward = ('forward' if where == 'Bottom' else 'to the front')
                    if len(moved) == 1:
                        await ctx.send(f'**:warning: `{moved[0].name}` has already been added to the queue. Moving it {forward}.**')
                    else:
                        await ctx.send(f'**:warning: {len(moved)} songs from this playlist that are already on the queue have been moved {forward}.**')
        return songs

    




    ##### Playing commands #####


    async def play(self, ctx, songs, priority=None):
        # Place the songs at the bottom of the queue
        if priority is None:
            priority = (len(songs) <= 1)
        async with self.mutex:
            if priority:
                n = self.queue_end
                for song in songs[::-1]:
                    self.queue.insert(n, song)
                self.queue_end += len(songs)
            else:
                n = len(self.queue)
                self.queue.extend(songs)
                if self.queue_end == n:
                    self.queue_end += len(songs)
            await self.enqueue_message(ctx, n, songs)
            await self.wake_up()


    async def playtop(self, ctx, songs):
        # Place the songs at the top of the queue
        async with self.mutex:
            self.queue.extendleft(songs[::-1])
            self.queue_end += len(songs)
            await self.enqueue_message(ctx, 0, songs)
            await self.wake_up()


    async def playskip(self, ctx, songs):
        # Place the songs at the top of the queue and then skip to the next song
        async with self.mutex:
            self.queue.extendleft(songs[::-1])
            self.queue_end += len(songs)
            await self.enqueue_message(ctx, 0, songs, now=True)
            await self.skip(ctx)


    async def playshuffle(self, ctx, songs, priority=False):
        # Place the songs in the queue, then shuffle the queue
        async with self.mutex:
            now = not (self.now_playing or self.queue)
            self.queue.extend(songs)
            random.shuffle(self.queue)
            self.queue_end = len(self.queue) if priority else 0
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
                    await ctx.send(f'**:thumbsup: Seeking to time `{format_time(time)}`**')


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
            threshold = int(.75 * (len(self.voice_channel.members) - 1))
            if threshold <= 1:
                await self.skip(ctx)
            elif ctx.author in self.votes:
                await ctx.send(f'**:x: You already voted to skip the current song** ({len(self.votes)}/{threshold} people)')
            else:
                self.votes.append(ctx.author)
                if len(self.votes) >= threshold:
                    await self.skip(ctx)
                else:
                    force_message = f' **`{self.prefix}forceskip` or `{self.prefix}fs` to force**' if self.is_dj(ctx.author) else ''
                    await ctx.send(f'**Skipping?** ({len(self.votes)}/{threshold} people){force_message}')


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
                if self.queue_end < position:
                    self.queue_end = 0
                elif not (self.queue_looping and (self.queue_end == len(self.queue))):
                    self.queue_end -= position - 1
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


    async def shuffle(self, ctx, priority=False):
        # Shuffle the queue
        async with self.mutex:
            random.shuffle(self.queue)
            self.queue_end = (len(self.queue) if priority else 0)
            await ctx.send('**:twisted_rightwards_arrows: Shuffled queue :ok_hand:**')


    async def move(self, ctx, old, new):
        # Move a song to a different spot in the queue
        async with self.mutex:
            if not ((1 <= old <= len(self.queue)) and (1 <= new <= len(self.queue))):
                await ctx.send(f'**:x: Invalid position, should be between 1 and {len(self.queue)}**')
            else:
                song = self.queue[old - 1]
                del self.queue[old - 1]
                if new >= old:
                    new += 1
                self.queue.insert(new - 1, song)
                if self.queue_end < new-1:
                    # make sure all the songs up to the new index we inserted at are ahead of the priority marker
                    self.queue_end = new-1
                elif self.queue_end < old-1:
                    # we took a song that was behind the priority marker and moved it ahead of it
                    self.queue_end += 1
                await ctx.send(f'**:white_check_mark: Moved `{song.name}` to position {new} in the queue**')


    async def remove(self, ctx, position):
        # Remove a song from the queue
        async with self.mutex:
            if not (1 <= position <= len(self.queue)):
                await ctx.send(f'**:x: Invalid position, should be between 1 and {len(self.queue)}**')
            else:
                song = self.queue[position - 1]
                del self.queue[position - 1]
                if position-1 < self.queue_end:
                    self.queue_end -= 1
                await ctx.send(f'**:white_check_mark: Removed `{song.name}`**')


    async def remove_range(self, ctx, start, end):
        # Remove a whole range of songs from the queue
        async with self.mutex:
            if end is None:
                end = len(self.queue)
            if not (1 <= start <= end <= len(self.queue)):
                if 1 <= end < start <= len(self.queue):
                    await ctx.send('**:x: Invalid range, start should be before end**')
                else:
                    await ctx.send(f'**:x: Invalid position, should be between 1 and {len(self.queue)}**')
            else:
                removed = tuple(self.queue)[start-1 : end]
                for song in removed:
                    self.queue.remove(song)
                if end-1 < self.queue_end:
                    self.queue_end -= (end - start + 1)
                elif start-1 < self.queue_end:
                    self.queue_end = start-1
                await ctx.send(f'**:white_check_mark: Removed {len(removed)} song{plural(len(removed))}**')


    async def clear(self, ctx, user):
        # Remove all the songs from the queue
        async with self.mutex:
            if user is None:
                self.queue.clear()
                self.queue_end = 0
                await ctx.send('***:boom: Cleared... :stop_button:***')
            else:
                # only remove the songs queued up by this particular user
                n = 0
                for song in tuple(self.queue):
                    if song.user == user:
                        if self.queue.index(song) < self.queue_end:
                            self.queue_end -= 1
                        self.queue.remove(song)
                        n += 1
                await ctx.send(f'**:thumbsup: {n} song{plural(n)} removed from the queue**')


    async def leavecleanup(self, ctx):
        # Remove all songs queued by absent users
        async with self.mutex:
            n = 0
            for song in tuple(self.queue):
                if song.user not in self.voice_channel.members:
                    if self.queue.index(song) < self.queue_end:
                        self.queue_end -= 1
                    self.queue.remove(song)
                    n += 1
            await ctx.send(f'**:thumbsup: {n} song{plural(n)} removed from the queue**')


    async def removedupes(self, ctx, quiet=False):
        # Remove all duplicate songs from the queue
        async with self.mutex:
            t = tuple(self.queue)
            n = 0
            for i, song in enumerate(t):
                if song in t[:i]:
                    if self.queue.index(song) < self.queue_end:
                        self.queue_end -= 1
                    self.queue.remove(song)
                    n += 1
            if (n or not quiet):
                await ctx.send(f'**:thumbsup: {n} song{plural(n)} removed from the queue**')




    ##### Setting commands #####


    async def settings_show(self, ctx):
        # Show the settings embed
        embed = discord.Embed(title='Bluez Settings',
                              description=f'Use the command format `{self.prefix}settings <options>` to view more info about an option.')
        embed.add_field(name=':exclamation: Prefix', value=f'`{self.prefix}settings prefix`', inline=True)
        embed.add_field(name=':no_entry_sign: Blacklist', value=f'`{self.prefix}settings blacklist`', inline=True)
        embed.add_field(name=':musical_note: Autoplay', value=f'`{self.prefix}settings autoplay`', inline=True)
        embed.add_field(name=':bell: Announce Songs', value=f'`{self.prefix}settings announcesongs`', inline=True)
        embed.add_field(name=':hash: Max Queue Length', value=f'`{self.prefix}settings maxqueuelength`', inline=True)
        embed.add_field(name=':1234: Max User Songs', value=f'`{self.prefix}settings maxusersongs`', inline=True)
        embed.add_field(name=':notes: Duplicate Song Prevention', value=f'`{self.prefix}settings preventduplicates`', inline=True)
        embed.add_field(name=':loud_sound: Default Volume', value=f'`{self.prefix}settings defaultvolume`', inline=True)
        embed.add_field(name=':1234: DJ Only Playlists', value=f'`{self.prefix}settings djplaylists`', inline=True)
        embed.add_field(name=':no_pedestrians: DJ Only', value=f'`{self.prefix}settings djonly`', inline=True)
        embed.add_field(name=':page_with_curl: Set DJ Role', value=f'`{self.prefix}settings djrole`', inline=True)
        embed.add_field(name=':infinity: Always Playing', value=f'`{self.prefix}settings alwaysplaying`', inline=True)
        embed.add_field(name=':recycle: Reset', value=f'`{self.prefix}settings reset`', inline=True)
        await ctx.send(embed=embed)


    async def settings_prefix(self, ctx, prefix):
        # Query or modify the prefix
        if prefix is None:
            # query the prefix
            embed = discord.Embed(title='Bluez Settings - :exclamation: Prefix',
                                  description='Changes the prefix used to address Bluez bot.')
            embed.add_field(name=':page_facing_up: Current Setting:', value=f'`{self.prefix}`')
            embed.add_field(name=':pencil2: Update:', value=f'`{self.prefix}settings prefix [New Prefix]`')
            embed.add_field(name=':white_check_mark: Valid Settings', value='`Any text, at most 5 characters (e.g. !)`')
            await ctx.send(embed=embed)
        else:
            # modify the prefix
            self.prefix = prefix
            await ctx.send(f'**:thumbsup: Prefix set to `{self.prefix}`**')


    async def settings_blacklist(self, ctx, channel):
        # Query or modify the blacklist
        if channel is None:
            # query the blacklist
            embed = discord.Embed(title='Bluez Settings - :no_entry_sign: Blacklist',
                                  description='Keyword `blacklist` also removes channels from Blacklist')
            embed.add_field(name=':page_facing_up: Current Setting:',
                            value=('`' + ', '.join([channel.mention for channel in self.blacklist]) + '`' \
                                   if self.blacklist else 'Blacklist empty'))
            embed.add_field(name=':pencil2: Update:', value=f'`{self.prefix}settings blacklist [Mention Channel]`')
            embed.add_field(name=':white_check_mark: Valid Settings:', value='`Any number of mentioned text channels`')
            await ctx.send(embed=embed)
        else:
            # modify the blacklist
            if channel not in self.blacklist:
                self.blacklist.append(channel)
                await ctx.send(f'**Blacklisted `{channel.name}`**')
            else:
                self.blacklist.remove(channel)
                await ctx.send(f'**Unblacklisted `{channel.name}`**')


    async def settings_autoplay(self, ctx, playlist):
        # Query or modify the autoplay playlist
        if playlist is None:
            # query the playlist
            if self.autoplay:
                await ctx.send(f'**:musical_note: AutoPlay playlist link:** {self.autoplay}')
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
                    await ctx.send(f'**:x: Error finding songs from `{playlist}`: `{e}`**')
                else:
                    self.autoplay = playlist
                    await ctx.send('**:white_check_mark: Success**')


    async def settings_announcesongs(self, ctx, on):
        # Query or modify the announcesongs flag
        if on is None:
            # query the flag
            emoji = 'bell' if self.announcesongs else 'no_bell'
            await ctx.send(f'**:{emoji}: Announcing new songs is currently turned {on_off(self.announcesongs)}**')
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
                await ctx.send(f'**:hash: Max queue length set to {self.maxqueuelength}**')
        else:
            # modify the length
            self.maxqueuelength = length
            if length == 0:
                await ctx.send('**:no_entry_sign: Max queue length disabled**')
            else:
                await ctx.send(f'**:white_check_mark: Max queue length set to {length}**')
                if (self.voice_channel is not None) and (len(self.queue) > length):
                    # if the queue is too full, go ahead and delete the excess songs now
                    n = len(self.queue) - length
                    self.queue = collections.deque(tuple(self.queue)[:length])
                    self.queue_end = min(self.queue_end, len(self.queue))
                    await ctx.send(f'**:thumbsup: {n} song{plural(n)} removed from the end of the queue**')


    async def settings_maxusersongs(self, ctx, number):
        # query or modify the maximum number of songs a single user can post
        if number is None:
            # query the number
            if self.maxusersongs == 0:
                await ctx.send('**:1234: Max user song limit disabled**')
            else:
                await ctx.send(f'**:1234: Max user song limit set to {self.maxusersongs}**')
        else:
            # modify the number
            self.maxusersongs = number
            if number == 0:
                await ctx.send('**:no_entry_sign: Max user song limit disabled**')
            else:
                await ctx.send(f'**:white_check_mark: Max user song limit set to {number}**')
                if (self.voice_channel is not None) and self.queue:
                    # go ahead and check through the queue now for excess songs
                    counter = collections.Counter()
                    n = 0
                    for song in tuple(self.queue):
                        counter[song.user.id] += 1
                        if counter[song.user.id] > number:
                            if self.queue.index(song) < self.queue_end:
                                self.queue_end -= 1
                            self.queue.remove(song)
                            n += 1
                    if n:
                        await ctx.send(f'**:thumbsup: {n} song{plural(n)} removed from the queue**')


    async def settings_preventduplicates(self, ctx, on):
        # query or modify the preventduplicates flag
        if on is None:
            # query the flag
            await ctx.send(f'**:notes: Duplicate prevention is currently turned {on_off(self.preventduplicates)}**')
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
            await ctx.send(f'**:loud_sound: Default volume level is currently {round(200 * self.defaultvolume):d}**')
        else:
            # modify the default volume
            self.defaultvolume = volume / 200.0
            await ctx.send(f'**:loud_sound: Default volume is now set to {volume:d}**')


    async def settings_djplaylists(self, ctx, on):
        # query or modify the djplaylists flag
        if on is None:
            # query the flag
            await ctx.send(f'**:1234: DJ Only Playlists mode is currently turned {on_off(self.djplaylists)}**')
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
            await ctx.send(f'**:no_pedestrians: DJ Only mode is currently turned {on_off(self.djonly)}**')
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
            await ctx.send(f'**:page_with_curl: The DJ Role here is `{self.djrole}`**')
        else:
            # modify the DJ role
            self.djrole = role.name
            await ctx.send(f'**:page_with_curl: DJ role set to `{self.djrole}`**')


    async def settings_alwaysplaying(self, ctx, on):
        # query or modify the alwaysplaying flag
        if on is None:
            # query the flag
            await ctx.send(f'**:infinity: Always Playing mode is currently turned {on_off(self.alwaysplaying)}**')
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
                              description=f'''\
:man_running: Speed - {self.tempo:.3g}

:musical_score: Pitch - {self.pitch:.3g} ({12*math.log2(self.pitch):.3g} semitones)

:guitar: Bass - {self.bass}

:crescent_moon: Nightcore - {on_off(self.nightcore).title()}

:stopwatch: Slowed - {on_off(self.slowed).title()}

:loud_sound: Volume - {round(200 * self.volume):d}''')
        await ctx.send(embed=embed)


    async def effects_help(self, ctx):
        # Describe the audio effect settings
        embed = discord.Embed(title='Bluez audio effects',
                              description=f'''\
`{self.prefix}speed <0.3 - 3>` - adjust the speed of the song playing
`{self.prefix}pitch <0.3 - 3>` - adjust the pitch of the song playing
`{self.prefix}bass <1 - 5>` - adjust the bass boost
`{self.prefix}nightcore` - toggle the nightcore effect on or off
`{self.prefix}slowed` - toggle the slowed effect on or off
`{self.prefix}volume <1-200>` - adjust the volume of the song playing''')
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
            await ctx.send(f'**:man_running: Current playback speed is set to {self.tempo:.3g}**')
        else:
            # modify the speed
            if self.tempo != speed:
                async with self.mutex:
                    self.tempo = speed
                    self.update_audio()
            await ctx.send(f'**:white_check_mark: Playback speed set to {self.tempo:.3g}**')


    async def effect_pitch_scale(self, ctx, scale):
        # Query or modify the pitch effect
        if scale is None:
            # query the pitch
            await ctx.send(f'**:musical_score: Current playback frequency multiplier is set to {self.pitch:.3g}**')
        else:
            # modify the pitch
            if self.pitch != scale:
                async with self.mutex:
                    self.pitch = scale
                    self.update_audio()
            await ctx.send(f'**:white_check_mark: Playback frequency multiplier set to {self.pitch:.3g}**')


    async def effect_pitch_steps(self, ctx, steps):
        # Query or modify the pitch effect, in semitones
        if steps is None:
            # query the pitch
            await ctx.send(f'**:musical_score: Current playback pitch is shifted by {12*math.log2(self.pitch):.3g} semitones**')
        else:
            # modify the pitch
            scale = 2.0 ** (steps/12.0)
            if self.pitch != scale:
                async with self.mutex:
                    self.pitch = scale
                    self.update_audio()
            await ctx.send(f'**:white_check_mark: Playback pitch shifted by {steps:.3g} semitones**')


    async def effect_bassboost(self, ctx, bass):
        # Query or modify the bass boost effect
        if bass is None:
            # query the bass boost
            await ctx.send(f'**:guitar: Current bass boost is set to {self.bass}**')
        else:
            # modify the bass boost
            if self.bass != bass:
                async with self.mutex:
                    self.bass = bass
                    self.update_audio()
            await ctx.send(f'**:white_check_mark: Bass boost set to {self.bass}**')


    async def effect_nightcore(self, ctx, on):
        # Modify the nightcore effect
        if (on is None) or (on != self.nightcore):
            async with self.mutex:
                self.nightcore = (not self.nightcore) if (on is None) else on
                self.update_audio()
        await ctx.send(f'**:white_check_mark: Nightcore effect turned {on_off(self.nightcore)}**')


    async def effect_slowed(self, ctx, on):
        # Modify the slowed effect
        if (on is None) or (on != self.slowed):
            async with self.mutex:
                self.slowed = (not self.slowed) if (on is None) else on
                self.update_audio()
        await ctx.send(f'**:white_check_mark: Slowed effect turned {on_off(self.slowed)}**')


    async def effect_volume(self, ctx, volume):
        # Query or modify the playback volume
        if volume is None:
            # query the volume
            await ctx.send(f'**:loud_sound: Volume is currently set to {round(200 * self.volume)}**')
        else:
            if self.volume != volume / 200.0:
                async with self.mutex:
                    self.volume = volume / 200.0
                    self.update_audio()
            await ctx.send(f'**:white_check_mark: Volume set to {volume}**')





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
            await ctx.send(f'**:thumbsup: {count} messages deleted**')




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


    async def notify_change_channel(self, channel):
        # Notify the bot that it is being moved to a new channel or kicked from a channel
        if channel is None:
            logging.warning('Bluez has been unexpectedly disconnected')
            self.reset()
        else:
            logging.warning('Bluez has been unexpectedly moved to a different channel')
            self.voice_channel = channel


    async def idle_timer_func(self):
        # Wait a certain amount of time, and then leave the voice channel
        await asyncio.sleep(300) # 5-minute delay
        await self.disconnect()






    ##### Loading/saving settings #####


    def load_settings(self):
        # Load the bot settings from Google drive
        if not BLUEZ_SETTINGS_PATH:
            return False # no settings path
        filename = os.path.join(BLUEZ_SETTINGS_PATH, f'bluez_settings_{self.guild.id}.txt')
        try:
            with open(filename, 'r') as o:
                settings = o.read()
        except IOError as e:
            logging.warning(f'error loading settings file: {e}; using defaults')
            return False
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
                    logging.warning(f'illegal value for setting {setting}: {value}')
            # Integer settings
            elif setting in ('maxqueuelength', 'maxusersongs', 'defaultvolume'):
                try:
                    setattr(self, setting, int(value))
                except ValueError:
                    logging.warning(f'illegal value for setting {setting}: {value}')
                else:
                    if setting == 'defaultvolume':
                        self.defaultvolume /= 200.0
            # String settings
            elif setting in ('prefix', 'djrole', 'autoplay'):
                if (setting == 'prefix') and (len(value) > 5):
                    logging.warning(f'prefix {value} too long')
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
                        logging.warning(f'illegal blacklist {value}')
                    else:
                        self.blacklist = [channel for channel in self.guild.text_channels if channel.id in blacklist_ids]
                else:
                    self.blacklist = []
        return True



    def save_settings(self):
        # Save the bot settings to Google drive
        if not BLUEZ_SETTINGS_PATH:
            return False # no settings path
        filename = os.path.join(BLUEZ_SETTINGS_PATH, f'bluez_settings_{self.guild.id}.txt')
        settings = f'''\
PREFIX            : {self.prefix}
BLACKLIST         : {','.join([str(channel.id) for channel in self.blacklist])}
AUTOPLAY          : {self.autoplay or ''}
ANNOUNCESONGS     : {self.announcesongs:d}
MAXQUEUELENGTH    : {self.maxqueuelength}
MAXUSERSONGS      : {self.maxusersongs}
PREVENTDUPLICATES : {self.preventduplicates:d}
DEFAULTVOLUME     : {self.defaultvolume * 200}
DJPLAYLISTS       : {self.djplaylists:d}
DJONLY            : {self.djonly:d}
DJROLE            : {self.djrole}
ALWAYSPLAYING     : {self.alwaysplaying:d}'''
        try:
            with open(filename, 'w') as o:
                o.write(settings)
                return True
        except IOError as e:
            logging.warning(f'error writing settings file: {e}')
            return False
                    
    
