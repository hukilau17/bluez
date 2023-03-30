# Timezone information for Bluez

try:
    
    import zoneinfo
    import logging


    TIMEZONES = tuple(sorted(set([i.split('/')[-1].replace('_', ' ') for i in zoneinfo.available_timezones() if i != 'localtime'])))

    def get_timezone(name):
        for key in zoneinfo.available_timezones():
            if key.endswith(name.replace(' ', '_')):
                return zoneinfo.ZoneInfo(key)
        logging.warning('get_timezone() unable to find timezone matching "%s"; returning None' % name)
        return None



    
except ImportError:

    # the Python version does not support zoneinfo (>= 3.9)
    # just use UTC

    TIMEZONES = ('UTC',)

    def get_timezone(name):
        return None
