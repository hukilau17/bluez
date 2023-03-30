# Individual song class

import discord
import youtube_dl
#import yt_dlp as youtube_dl
import tinytag
import httpio
import asyncio
import re
import os
import logging

from bluez.util import *


NIGHTCORE_TEMPO = 1.3
NIGHTCORE_PITCH = 1.3
SLOWED_TEMPO = 0.7
BASS_BOOST_DB = 5
TREBLE_ATTENUATE_DB = 2
METADATA_TIMEOUT = 30

BLUEZ_DEBUG = bool(int(os.getenv('BLUEZ_DEBUG', '0')))


# Search keys
SEARCH_INFO = {
    # name of streaming service -> (youtube-dl search key, appropriate emoji)
    # more of these may be added in the future
    'YouTube'   : ('ytsearch', ':arrow_forward:'),
    'SoundCloud': ('scsearch', ':cloud:'        ),
    }


PLAYLIST_HACK = True # implement at your own risk



if PLAYLIST_HACK:
    
    def _process_iterable_entry(self, entry, download, extra_info):
        result = entry.copy()
        result['extra_info_hack'] = extra_info.copy()
        return result
        
    youtube_dl.YoutubeDL._YoutubeDL__process_iterable_entry = _process_iterable_entry

    


ydl = youtube_dl.YoutubeDL({
    'format': 'bestaudio/best',
    'outtmpl': '%(extractor)s-%(id)s-%(title)s.%(ext)s',
    'restrictfilenames': True,
    'nocheckcertificate': True,
    'ignoreerrors': False,
    'logtostderr': False,
    'verbose': BLUEZ_DEBUG,
    'quiet': not BLUEZ_DEBUG,
    'no_warnings': True,
    'cachedir': False,
    'default_search': 'auto',
    'source_address': '0.0.0.0'
})





class Song(object):

    def __init__(self, data, user):
        self.data = data
        self.user = user
        self.tempo = 1.0
        self.adjusted_length = 0
        self.error = None
        self.init()

    def __eq__(self, other):
        return isinstance(other, Song) and (self.name == other.name)


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
        self.trim()
        if 'extra_info_hack' in self.data:
            self.url = None
            if self.data.get('ie_key') == 'Youtube':
                self.link = 'https://www.youtube.com/watch?v=%s' % self.data['id']
            else:
                self.link = self.data.get('url')
        else:
            self.url = self.data['url']
            self.link = self.data.get('webpage_url', getattr(self, 'link', None))



    def trim(self):
        # trim a song's length if its start and end are specified
        if self.length and ((self.start is not None) or (self.end is not None)):
            if self.end is not None:
                self.length = self.end - (self.start or 0)
            else:
                self.length -= self.start
            self.adjusted_length = self.length / self.tempo
            
        
        
    def process(self):
        # process a Song (i.e. actually ask youtube-dl to find the URL
        # for it rather than delaying it till later). This does nothing if the
        # ugly playlist hack (t.m.) is disabled.
        if (self.url is None) and ('extra_info_hack' in self.data):
            extra_info = self.data.pop('extra_info_hack')
            try:
                self.data = ydl.process_ie_result(self.data, download=False, extra_info=extra_info)
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
            logging.warning('unable to get metadata for "%s": %s' % (self.name, error))
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
                self.name = '%s - %s' % (self.artist, self.track)
            else:
                self.name = self.track


    async def get_metadata_with_timeout(self, timeout):
        # try to get the metadata, but don't wait forever
        try:
            await asyncio.wait_for(self.get_metadata(), timeout)
        except asyncio.TimeoutError:
            logging.warning('timed out while getting metadata for "%s"' % self.name)


    def fetch_metadata(self):
        # Begin loading the metadata asynchronously
        if not (self.length or hasattr(self, 'metadata_task')):
            self.metadata_task = asyncio.create_task(self.get_metadata_with_timeout(METADATA_TIMEOUT))
        
        
        

    async def reload(self):
        # Reload this song and get a fresh link to it
        # this should only be called if something goes wrong
        logging.warning('Attempting to reload "%s"' % self.name)
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
        self.process()
        if self.error:
            return self.error
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
            before_options += ' -ss %s' % format_time(seek_pos * self.tempo)
        if self.end is not None:
            before_options += ' -to %s' % format_time(self.end * self.tempo)
        af = []
        # change the bass and treble gains if bass-boosting is turned on
        if bass != 1:
            bass_gain = BASS_BOOST_DB * (bass-1)
            treble_loss = -TREBLE_ATTENUATE_DB * (bass-1)
            af.append('bass=g=%d' % bass_gain)
            af.append('treble=g=%d' % treble_loss)
        # change the tempo and pitch
        # tempo/pitch is first adjusted by varying the sampling rate,
        # then tempo can be additionally altered by using the atempo filter
        if (self.tempo != 1.0) or (pitch != 1.0):
            asr = self.asr
            if pitch != 1.0:
                if asr is None:
                    asr = 44100
                    af.append('aresample=44100')
                af.append('asetrate=%d' % (asr * pitch))
            tempo = self.tempo / pitch
            if tempo != 1.0:
                while tempo > 2.0:
                    af.append('atempo=2.0')
                    tempo /= 2.0
                while tempo < 0.5:
                    af.append('atempo=0.5')
                    tempo *= 2.0
                af.append('atempo=%s' % float(tempo))
            if pitch != 1.0:
                af.append('aresample=%d' % asr)
        options = '-vn'
        if af:
            options += ' -af "%s"' % ','.join(af)
        loop = asyncio.get_event_loop()
        source = (await loop.run_in_executor(None, lambda: self.get_source(before_options, options, stderr, volume)))
        # Check for an error written to the stream
        error = get_error(stderr)
        if error:
            return Exception(error)
        # Otherwise return the source
        return source
        




class Playlist(list):
    # subclass that can have attributes (.name and .link) assigned
    # for `enqueue_message` to read.
    pass




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
        



def is_url(string):
    # return True if this string appears to be a valid website URL
    return bool(re.match(r'(https:|http:|www\.)\S*', string))


async def extract_info(url):
    # ask youtube-dl to get the info for a given URL or search query, running
    # the command in the asyncio event loop to avoid blocking.
    loop = asyncio.get_event_loop()
    return (await loop.run_in_executor(None, lambda: ydl.extract_info(url, download=False)))



async def songs_from_url(url, user):
    # find and return songs from the given URL
    data = (await extract_info(url))
    if 'entries' in data:
        entries = data['entries']
        pl = Playlist([Song(entry, user) for entry in entries])
        if 'title' in data:
            pl.name = data['title']
        if 'webpage_url' in data:
            pl.link = data['webpage_url']
        return pl
    else:
        return [Song(data, user)]



async def songs_from_search(query, user, maxn, search_key):
    # find and return songs matching the given search query
    data = (await extract_info('%s%d:%s' % (search_key, maxn, query)))
    entries = data['entries']
    songs = [Song(entry, user) for entry in entries]
    if songs and (maxn == 1):
        songs[0].process()
    return songs[:maxn] # apparently SoundCloud can give you multiple songs even when you only ask for one...



