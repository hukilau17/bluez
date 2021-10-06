# Individual song class

import discord
import youtube_dl
import asyncio
import os
import re

from bluez.util import *


NIGHTCORE_TEMPO = 1.4
NIGHTCORE_PITCH = 1.3
SLOWED_TEMPO = 0.7
BASS_BOOST_DB = 5
TREBLE_ATTENUATE_DB = 2


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
    'quiet': (not int(os.getenv('BLUEZ_DEBUG', '0'))),
    'no_warnings': True,
    'default_search': 'auto',
    'source_address': '0.0.0.0'
})

ydl.cache.remove()





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
        self.artist = self.data.get('artist')
        self.track = self.data.get('track')
        self.asr = self.data.get('asr')
        if 'extra_info_hack' in self.data:
            self.url = None
            self.link = None
            if self.data.get('ie_key') == 'Youtube':
                self.link = 'https://www.youtube.com/watch?v=%s' % self.data['id']
            else:
                self.link = self.data.get('url')
        else:
            self.url = self.data['url']
            self.link = self.data.get('webpage_url', getattr(self, 'link', None))

        
        
        
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


    def get_adjusted_tempo(self, tempo=1.0, nightcore=False, slowed=False):
        # given the tempo and the nightcore/slowed options, adjusts the tempo and makes
        # sure it is within the allowed range
        tempo *= (NIGHTCORE_TEMPO if nightcore else 1.0) * (SLOWED_TEMPO if slowed else 1.0)
        return min(max(tempo, 0.1), 3)


    def get_audio(self, seek_pos=0, tempo=1.0, pitch=1.0, bass=1, nightcore=False, slowed=False, volume=1.0):
        # given start position and audio effect parameters, returns
        # an audio source object that can be played using a voice client
        self.process()
        if self.error:
            return self.error
        before_options = '-reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 5'
        self.tempo = self.get_adjusted_tempo(tempo, nightcore, slowed)
        if nightcore:
            # nightcore adjusts the pitch upward
            pitch *= NIGHTCORE_PITCH
            pitch = min(pitch, 3)
        self.adjusted_length = self.length / self.tempo
        if seek_pos != 0:
            before_options += ' -ss %s' % format_time(seek_pos * self.tempo)
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
        source = discord.FFmpegPCMAudio(self.url, before_options=before_options, options=options)
        if volume != 1.0:
            source = discord.PCMVolumeTransformer(source, volume)
        return source
        




class Playlist(list):
    # subclass that can have attributes (.name and .link) assigned
    # for `enqueue_message` to read.
    pass






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



async def songs_from_search(query, user, maxn, soundcloud=False):
    # find and return songs matching the given search query
    source = ('sc' if soundcloud else 'yt')
    data = (await extract_info('%ssearch%d:%s' % (source, maxn, query)))
    entries = data['entries']
    songs = [Song(entry, user) for entry in entries]
    if maxn == 1:
        songs[0].process()
    return songs



