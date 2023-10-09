# Miscellaneous utilities

import sys
import re
import asyncio
import logging
import traceback


def format_time(time):
    if time < 0:
        time = 0
    if time < 3600:
        return '%d:%.2d' % (time // 60, time % 60)
    else:
        return '%d:%.2d:%.2d' % (time // 3600, (time // 60) % 60, time % 60)


def format_user(user):
    str = user.name
    if getattr(user, 'nick', None):
        str = '%s (%s)' % (user.nick, str)
    return str


def format_link(song):
    if getattr(song, 'link', None):
        return '[%s](%s)' % (song.name, song.link)
    else:
        return song.name


def is_url(string):
    # return True if this string appears to be a valid website URL
    return bool(re.match(r'(https:|http:|www\.)\S*', string))


def log_exception(error):
    # Helper utility to log an exception
    logging.error(''.join(traceback.format_exception(type(error), error, error.__traceback__)))


def caller_name():
    # Get the name of the function two or three levels out (for debugging a mutex)
    name = sys._getframe(2).f_code.co_name
    if name in ('__aenter__', '__aexit__'):
        # we used a context manager to acquire/release the mutex so go out another level
        name = sys._getframe(3).f_code.co_name
    return name





class DebugLock(asyncio.Lock):

    # A lock with additional features useful for debugging

    def __init__(self, timeout=5.0, debug=True):
        asyncio.Lock.__init__(self)
        self.timeout = timeout
        self.debug = debug

    async def acquire(self):
        if self.debug:
            logging.info('Mutex acquired in %s()' % caller_name())
        if self.timeout:
            await asyncio.wait_for(asyncio.Lock.acquire(self), timeout=self.timeout)
        else:
            await asyncio.Lock.acquire(self)

    def release(self):
        if self.debug:
            logging.info('Mutex released in %s()' % caller_name())
        asyncio.Lock.release(self)
