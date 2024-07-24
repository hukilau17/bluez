# Individual song class

import discord
import yt_dlp
import tinytag
import httpio
import asyncio
import re
import os
import logging
import urllib.parse

from bluez.util import *


NIGHTCORE_TEMPO = 1.3
NIGHTCORE_PITCH = 1.3
SLOWED_TEMPO = 0.7
BASS_BOOST_DB = 5
TREBLE_ATTENUATE_DB = 2
METADATA_TIMEOUT = 30

MAX_TIME_VALUE = 36000000 # ffmpeg does not allow timestamps of 10000 hours or more
MAX_INPUT_LENGTH = 30

BLUEZ_DEBUG = bool(int(os.getenv('BLUEZ_DEBUG', '0')))
BLUEZ_DOWNLOAD = bool(int(os.getenv('BLUEZ_DOWNLOAD', '0')))
BLUEZ_DOWNLOAD_PATH = os.getenv('BLUEZ_DOWNLOAD_PATH')
BLUEZ_PROXY = os.getenv('BLUEZ_PROXY', '')


# Search keys
SEARCH_INFO = {
    # name of streaming service -> (youtube-dl search key, appropriate emoji, whether or not it searches for playlists)
    # more of these may be added in the future
    'YouTube Video'      : ('ytsearch', ':arrow_forward:', False),
    'YouTube Playlist'   : ('ytsearch', ':arrow_forward:', True ),
    'SoundCloud'         : ('scsearch', ':cloud:'        , False),
    }



# Youtube-DL options
YTDL_OPTIONS = {
    'format': 'bestaudio/best',
    'outtmpl': '%(extractor)s-%(id)s-%(title)s.%(ext)s',
    'restrictfilenames': True,
    'nocheckcertificate': True,
    'ignoreerrors': False,
    'logtostderr': False,
    'verbose': BLUEZ_DEBUG,
    'quiet': not BLUEZ_DEBUG,
    'proxy': BLUEZ_PROXY,
    'no_warnings': True,
    'cachedir': False,
    'default_search': 'auto',
    'source_address': '0.0.0.0',
    'noplaylist': True,
    'extract_flat': 'in_playlist',
    'paths': ({'home': BLUEZ_DOWNLOAD_PATH} if BLUEZ_DOWNLOAD_PATH else {}),
}





class Song(object):

    def __init__(self, ydl, data, user):
        self.ydl = ydl
        self.data = data
        self.user = user
        self.tempo = 1.0
        self.adjusted_length = 0
        self.error = None
        self.init()

    def __eq__(self, other):
        return isinstance(other, Song) and (self.name == other.name) and (self.link == other.link)


    def init(self):
        # initialize the data for a Song object
        self.name = self.data.get('title', '[no title]')
        self.length = self.adjusted_length = self.data.get('duration') or 0
        self.thumbnail = self.data.get('thumbnail')
        self.channel = self.data.get('channel', 'None')
        self.channel_url = self.data.get('channel_url')
        self.artist = self.data.get('artist')
        self.track = self.data.get('track')
        self.asr = self.data.get('asr')
        self.start = self.data.get('start_time')
        self.end = self.data.get('end_time')
        # sanitize start and end since it's possible to set them maliciously
        if self.start is not None:
            if self.start < 0:
                self.start = None
            elif self.start >= MAX_TIME_VALUE:
                self.error = Exception('start time too big, no audio data')
        if self.end is not None:
            if self.end < 0:
                self.end = 0
            elif self.end >= MAX_TIME_VALUE:
                self.end = None
            elif (self.start is not None) and (self.end < self.start):
                self.start = self.end
                self.error = Exception('start time later than end time, no audio data')
        # trim the length to be between the start and end
        self.trim()
        if self.data.get('_type', 'video') != 'video':
            # this song was part of a playlist so we don't have its url yet
            self.url = None
            self.link = self.data.get('url')
        else:
            # this song has a URL loaded and ready to go
            self.link = self.data.get('webpage_url', getattr(self, 'link', None))
            if BLUEZ_DOWNLOAD:
                self.url = self.data['requested_downloads'][0]['filepath']
            else:
                self.url = self.data['url']



    def trim(self):
        # trim a song's length if its start and end are specified
        if self.length and ((self.start is not None) or (self.end is not None)):
            if self.end is not None:
                self.length = min(self.end, self.length) - (self.start or 0)
            else:
                self.length -= self.start
            if self.length <= 0:
                self.length = 0
                if self.error is None:
                    self.error = Exception('start time later than end of song, no audio data')
            self.adjusted_length = self.length / self.tempo
            
        
        
    async def process(self):
        # process a Song (i.e. actually ask youtube-dl to find the URL
        # for it rather than delaying it till later).
        if self.url is None:
            loop = asyncio.get_event_loop()
            try:
                self.data = (await loop.run_in_executor(None, lambda: self.ydl.process_ie_result(self.data, download=BLUEZ_DOWNLOAD)))
            except Exception as e:
                self.error = e
            self.init()
        # Get metadata if we need to
        self.fetch_metadata()




    async def get_metadata(self):
        # try to get information from the metadata of a song
        try:
            with httpio.open(self.url) as fp:
                parser_class = tinytag.TinyTag.get_parser_class(fp.url, fp)
                tag = parser_class(fp, fp.length)
                loop = asyncio.get_event_loop()
                await loop.run_in_executor(None, lambda: tag.load(tags=True, duration=True))
        except Exception as error:
            logging.warning(f'unable to get metadata for "{self.name}": {error}')
            return
        # If successful, set information from this tag object
        if not self.length:
            self.length = float(tag.duration)
            self.trim()
            self.adjusted_length = self.length / self.tempo
        if self.artist is None:
            self.artist = tag.artist
        if self.track is None:
            self.track = tag.title
        if (self.name == '[no title]') and self.track:
            if self.artist:
                self.name = f'{self.artist} - {self.track}'
            else:
                self.name = self.track


    async def get_metadata_with_timeout(self, timeout):
        # try to get the metadata, but don't wait forever
        try:
            await asyncio.wait_for(self.get_metadata(), timeout)
        except asyncio.TimeoutError:
            logging.warning(f'timed out while getting metadata for "{self.name}"')


    def fetch_metadata(self):
        # Begin loading the metadata asynchronously
        if not (self.length or self.error or hasattr(self, 'metadata_task')):
            self.metadata_task = asyncio.create_task(self.get_metadata_with_timeout(METADATA_TIMEOUT))
        
        
        

    async def reload(self):
        # Reload this song and get a fresh link to it
        # this should only be called if something goes wrong
        logging.warning(f'Attempting to reload "{self.name}"')
        songs = (await songs_from_url(self.link, self.user))
        if songs:
            self.__dict__.update(songs[0].__dict__)



    def get_source(self, before_options='', options='', stderr=None, volume=1.0):
        try:
            source = discord.FFmpegPCMAudio(self.url, before_options=before_options, options=options, stderr=stderr)
            # Adjust the volume if possible
            if volume != 1.0:
                source = discord.PCMVolumeTransformer(source, volume)
            return source
        except Exception as e:
            return e
    


    async def get_audio(self, seek_pos=0, tempo=1.0, pitch=1.0, bass=1, nightcore=False, slowed=False, volume=1.0, stderr=None):
        # given start position and audio effect parameters, returns
        # an audio source object that can be played using a voice client
        await self.process()
        if self.error:
            return self.error
        if BLUEZ_DOWNLOAD:
            before_options = ''
        else:
            before_options = '-reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 5'
        self.tempo = get_adjusted_tempo(tempo, nightcore, slowed)
        if nightcore:
            # nightcore adjusts the pitch upward
            pitch *= NIGHTCORE_PITCH
            pitch = min(pitch, 3)
        self.adjusted_length = self.length / self.tempo
        if self.start is not None:
            seek_pos += self.start
        if seek_pos != 0:
            before_options += f' -ss {format_time(seek_pos * self.tempo)}'
        if self.end is not None:
            before_options += f' -to {format_time(self.end * self.tempo)}'
        af = []
        # change the bass and treble gains if bass-boosting is turned on
        if bass != 1:
            bass_gain = BASS_BOOST_DB * (bass-1)
            treble_loss = -TREBLE_ATTENUATE_DB * (bass-1)
            af.append(f'bass=g={bass_gain}')
            af.append(f'treble=g={treble_loss}')
        # change the tempo and pitch
        # tempo/pitch is first adjusted by varying the sampling rate,
        # then tempo can be additionally altered by using the atempo filter
        if (self.tempo != 1.0) or (pitch != 1.0):
            asr = self.asr
            if pitch != 1.0:
                if asr is None:
                    asr = 44100
                    af.append('aresample=44100')
                asetrate = int(round(asr * pitch))
                af.append(f'asetrate={asetrate}')
            tempo = self.tempo / pitch
            if tempo != 1.0:
                while tempo > 2.0:
                    af.append('atempo=2.0')
                    tempo /= 2.0
                while tempo < 0.5:
                    af.append('atempo=0.5')
                    tempo *= 2.0
                af.append(f'atempo={tempo}')
            if pitch != 1.0:
                af.append(f'aresample={asr}')
        options = '-vn'
        if af:
            af = ','.join(af)
            options += f' -af "{af}"'
        loop = asyncio.get_event_loop()
        source = (await loop.run_in_executor(None, lambda: self.get_source(before_options, options, stderr, volume)))
        # Check for an error written to the stream
        error = get_error(stderr)
        if error:
            return Exception(error)
        # Otherwise return the source
        return source
        







class Playlist(list):

    def __init__(self, ydl, data, user):
        list.__init__(self)
        self.ydl = ydl
        self.data = data
        self.user = user
        self.init()

    def __eq__(self, other):
        return isinstance(other, Playlist) and (self.name == other.name) and (self.link == other.link)

    def copy(self):
        new = list.__new__(Playlist)
        new[:] = self
        new.__dict__.update(self.__dict__)
        return new

    def init(self):
        # initialize the data for a Playlist object
        if 'entries' in self.data:
            self[:] = [Song(self.ydl, entry, self.user) for entry in self.data['entries']]
        else:
            self[:] = []
        self.name = self.data.get('title', '[no title]')
        self.channel = self.data.get('channel', 'None')
        self.channel_url = self.data.get('channel_url')
        self.link = self.data.get('url', self.data.get('webpage_url', None))
            
    async def process(self):
        # process a Playlist (i.e. actually ask youtube-dl to find the songs
        # for it rather than delaying it till later).
        if 'entries' not in self.data:
            loop = asyncio.get_event_loop()
            self.data = (await loop.run_in_executor(None, lambda: self.ydl.process_ie_result(self.data, download=BLUEZ_DOWNLOAD)))
            self.init()

    









def get_adjusted_tempo(tempo=1.0, nightcore=False, slowed=False):
    # given the tempo and the nightcore/slowed options, adjusts the tempo and makes
    # sure it is within the allowed range
    tempo *= (NIGHTCORE_TEMPO if nightcore else 1.0) * (SLOWED_TEMPO if slowed else 1.0)
    return min(max(tempo, 0.1), 3)



def get_error(stderr):
    # check if an error message has been written to the stream
    if stderr is not None:
        stderr.flush()
        if stderr.tell() != 0:
            stderr.seek(0)
        error = stderr.read()
        if error:
            stderr.seek(0)
            stderr.truncate()
            return error.decode('latin-1')
        



async def extract_info(ydl, url):
    # ask youtube-dl to get the info for a given URL or search query, running
    # the command in the asyncio event loop to avoid blocking.
    loop = asyncio.get_event_loop()
    return (await loop.run_in_executor(None, lambda: ydl.extract_info(url, download=BLUEZ_DOWNLOAD)))



async def songs_from_url(url, user):
    # find and return songs from the given URL
    ydl = yt_dlp.YoutubeDL(YTDL_OPTIONS)
    data = (await extract_info(ydl, url))
    if data.get('_type') == 'playlist':
        return Playlist(ydl, data, user)
    else:
        return [Song(ydl, data, user)]



async def songs_from_search(query, user, start, maxn, search_key):
    # find and return songs matching the given search query
    ydl = yt_dlp.YoutubeDL(YTDL_OPTIONS)
    ydl.params['playliststart'] = start + 1
    ydl.params['playlistend'] = maxn
    data = (await extract_info(ydl, f'{search_key}{maxn}:{query}'))
    del ydl.params['playliststart'], ydl.params['playlistend']
    entries = data['entries']
    songs = [Song(ydl, entry, user) for entry in entries]
    if songs and (maxn == 1):
        await songs[0].process()
    return songs[:maxn] # apparently SoundCloud can give you multiple songs even when you only ask for one...



async def playlists_from_search(query, user, start, maxn):
    # search youtube for playlists matching the given search query
    ydl = yt_dlp.YoutubeDL(YTDL_OPTIONS)
    ydl.params['playliststart'] = start + 1
    ydl.params['playlistend'] = maxn
    data = (await extract_info(ydl, 'https://www.youtube.com/results?sp=EgIQAw%253D%253D&search_query=' + \
                               urllib.parse.quote_plus(query)))
    del ydl.params['playliststart'], ydl.params['playlistend']
    entries = data['entries']
    playlists = [Playlist(ydl, entry, user) for entry in entries]
    if playlists and (maxn == 1):
        await playlists[0].process()
    return playlists[:maxn]
    
    
    
