# Bluez bot implementation

import os
from bluez.bot import bot

if __name__ == '__main__':
    bot.run(os.getenv('BLUEZ_TOKEN'))
