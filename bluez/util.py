# Miscellaneous utilities


def format_time(time):
    if time < 3600:
        return '%d:%.2d' % (time // 60, time % 60)
    else:
        return '%d:%.2d:%.2d' % (time // 3600, (time // 60) % 60, time % 60)


def format_user(user):
    str = '%s#%s' % (user.name, user.discriminator)
    if getattr(user, 'nick', None):
        str = '%s (%s)' % (user.nick, str)
    return str


def format_link(song):
    if getattr(song, 'link', None):
        return '[%s](%s)' % (song.name, song.link)
    else:
        return song.name
