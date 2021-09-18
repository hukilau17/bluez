# Individual song class

import discord
import youtube_dl
import asyncio
import re

from bluez.util import *

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
    'quiet': (not BLUEZ_DEBUG),
    'no_warnings': True,
    'default_search': 'auto',
    'source_address': '0.0.0.0' # bind to ipv4 since ipv6 addresses cause issues sometimes
})





class Song(object):

    def __init__(self, data, user):
        self.data = data
        self.user = user
        self.tempo = 1.0
        self.error = None
        self.init()

    def init(self):
        self.name = self.data.get('title', '[no title]')
        self.length = self.adjusted_length = self.data.get('duration') or 0
        self.thumbnail = self.data.get('thumbnail', None)
        self.channel = self.data.get('channel', 'None')
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
        if (self.url is None) and ('extra_info_hack' in self.data):
            extra_info = self.data.pop('extra_info_hack')
            try:
                self.data = ydl.process_ie_result(self.data, download=False, extra_info=extra_info)
            except Exception as e:
                self.error = e
            self.init()

    def __eq__(self, other):
        return isinstance(other, Song) and (self.name == other.name)

    def get_adjusted_tempo(self, tempo=1.0, nightcore=False, slowed=False):
        return tempo * (1.43 if nightcore else 1.0) * (0.5 if slowed else 1.0)


    def get_audio(self, seek_pos=0, tempo=1.0, bass=1, nightcore=False, slowed=False, volume=1.0):
        self.process()
        if self.error:
            return self.error
        before_options = '-reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 5'
        options = '-vn'
        self.adjusted_length = self.length
        self.tempo = self.get_adjusted_tempo(tempo, nightcore, slowed)
        if seek_pos != 0:
            before_options += ' -ss %s' % format_time(seek_pos * self.tempo)
        if slowed:
            tempo *= 0.5
            self.adjusted_length *= 2.0
        tempo = max(tempo, 0.1)
        if tempo != 1.0:
            self.adjusted_length /= tempo
            tempo_options = []
            while tempo > 2.0:
                tempo_options.append('atempo=2.0')
                tempo /= 2.0
            while tempo < 0.5:
                tempo_options.append('atempo=0.5')
                tempo *= 2.0
            tempo_options.append('atempo=%s' % float(tempo))
            options += ' -filter:a "%s" ' % ','.join(tempo_options)
        af = []
        if bass != 1:
            af.append('bass=g=%d' % (2*(bass-1)))
        if nightcore:
            self.adjusted_length /= 1.43
            af.append('asetrate=57300,atempo=1.1,aresample=44100')
        if af:
            options += ' -af "%s"' % ','.join(af)
        source = discord.FFmpegPCMAudio(self.url, before_options=before_options, options=options)
        if volume != 1.0:
            source = discord.PCMVolumeTransformer(source, volume)
        return source
        




class Playlist(list):
    pass






def is_url(string):
    return bool(re.match(r'(https:|http:|www\.)\S*', string))


async def extract_info(url):
    loop = asyncio.get_event_loop()
    return (await loop.run_in_executor(None, lambda: ydl.extract_info(url, download=False)))



async def songs_from_url(url, user):
    data = (await extract_info(url))
    if 'entries' in data:
        entries = data['entries']
        pl = Playlist([Song(entry, user) for entry in entries])
        pl.name = data['title']
        pl.link = data['webpage_url']
        return pl
    else:
        return [Song(data, user)]



async def songs_from_youtube(query, user, maxn):
    data = (await extract_info('ytsearch%d:%s' % (maxn, query)))
    entries = data['entries']
    songs = [Song(entry, user) for entry in entries]
    if maxn == 1:
        songs[0].process()
    return songs


async def songs_from_soundcloud(query, user, maxn):
    data = (await extract_info('scsearch%d:%s' % (maxn, query)))
    entries = data['entries']
    songs = [Song(entry, user) for entry in entries]
    if maxn == 1:
        songs[0].process()
    return songs



