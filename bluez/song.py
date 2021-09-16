# Individual song class

import discord
import youtube_dl
import asyncio
import re

from bluez.util import *


ydl = youtube_dl.YoutubeDL({
    'format': 'bestaudio/best',
    'outtmpl': '%(extractor)s-%(id)s-%(title)s.%(ext)s',
    'restrictfilenames': True,
    'nocheckcertificate': True,
    'ignoreerrors': False,
    'logtostderr': False,
    'quiet': False, # change to True later
    'no_warnings': True,
    'default_search': 'auto',
    'source_address': '0.0.0.0' # bind to ipv4 since ipv6 addresses cause issues sometimes
})





class Song(object):

    def __init__(self, data, user):
        self.data = data
        self.user = user
        self.name = data.get('title', '[no title]')
        self.length = self.adjusted_length = data.get('duration', 0)
        self.tempo = 1.0
        self.thumbnail = data.get('thumbnail', None)
        self.channel = data.get('channel', 'None')
        self.url = self.data['url']
        self.link = self.data['webpage_url']

    def __eq__(self, other):
        return isinstance(other, Song) and (self.url == other.url)


    def get_adjusted_tempo(self, tempo=1.0, nightcore=False, slowed=False):
        return tempo * (1.43 if nightcore else 1.0) * (0.5 if slowed else 1.0)


    def get_audio(self, seek_pos=0, tempo=1.0, bass=1, nightcore=False, slowed=False, volume=1.0):
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



async def songs_from_url(url, user, maxn):
    data = (await extract_info(url))
    if 'entries' in data:
        entries = data['entries']
        pl = Playlist([Song(entries[i], user) for i in range(min(maxn, len(entries)))])
        pl.name = data['title']
        pl.link = data['webpage_url']
        return pl
    else:
        return [Song(data, user)]



async def songs_from_youtube(query, user, maxn):
    data = (await extract_info('ytsearch%d:%s' % (maxn, query)))
    entries = data['entries']
    return [Song(entry, user) for entry in entries]


async def songs_from_soundcloud(query, user, maxn):
    data = (await extract_info('scsearch%d:%s' % (maxn, query)))
    entries = data['entries']
    return [Song(entry, user) for entry in entries]



