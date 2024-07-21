# Function to fetch the lyrics to a song

import os
import re
import lyricsgenius

BLUEZ_DEBUG = bool(int(os.getenv('BLUEZ_DEBUG', '0')))

from bluez.util import *


# Initialize the lyricsgenius interface
try:
    genius = lyricsgenius.Genius(verbose = BLUEZ_DEBUG)
except:
    # this should only happen if you haven't provided a token for the genius API
    genius = None




# Main function to get lyrics

def get_lyrics(title, artist='', is_now_playing=False):
    if genius is None:
        raise RuntimeError('Lyric searching is not enabled.')
    # Loop over all songs matching the title/query
    hits = genius.search_songs(title)['hits']
    song = None
    for hit in hits:
        hit = hit['result']
        if matches(hit['title'], title):
            if (not artist) or matches(hit['primary_artist'], artist):
                song = genius.song(hit)
                break
    if song is None:
        raise RuntimeError('There were no results matching the query.')
    lyrics = song.lyrics
    # get rid of some of the garbage that Genius puts in
    m = re.search(r'\d* Contributors.*?\n', lyrics)
    if m:
        lyrics = lyrics[m.end():]
    m = re.search(r'\d*Embed', lyrics)
    if m:
        lyrics = lyrics[:m.start()]
    return lyrics



# Helper functions to detect if two strings match

def matches(s1, s2):
    return cleanup(s1) == cleanup(s2)


def cleanup(s):
    # convert to lowercase, remove non-alphanumeric characters, and
    # strip anything that's to the right of a ( or [ character
    s = s.lower()
    match = re.search(r'[\(\[]', s)
    if match:
        s = s[:match.start()]
    s = re.sub(r'[^\w]', '', s)
    return s
    
