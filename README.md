# Bluez Discord bot

Recently, several high-profile music bots have been forced to go offline. Bluez bot is an open source, personal use alternative 
to these bots. It is coded in Python and uses Youtube-DL to stream music off of various places on the internet. It has a rich array of features, and its 
interface is designed to reverse engineer the behavior of a previously existing discord bot that was taken offline in September 2021 as precisely as possible.
With that being said, there are a couple of things to note:

#### Bluez is still in beta.
What this means is that, if you run Bluez bot on your own server, there is a good chance you will discover glitches or unexpected behavior. Please feel free
to open an issue if this happens.

#### Bluez is not particularly scalable.
The current structure of the code is such that it may not scale well to run simultaneously on many servers. For this reason (together with
the fact that it's still in beta), rather than adding the same bot to a ton of different servers, we recommend that users follow the detailed steps 
below in this README to set up and run their own instance of Bluez. If you are so inclined, you can also feel free to clone the repository and 
make modifications as you see fit. If you create your own instance of the bot, you can then add it to as many or as few servers as you like; 
just be aware that scalability may become an issue. We may eventually modify the code to try to make it more scalable.

#### Other known issues.
  - The !lyrics command can make some peculiar choices if you just run it with no argument (meaning it tries to find the lyrics of the currently playing song).
The reason for this is that it uses the title of the YouTube video playing as the search query for Genius, and sometimes this title contains unnecessary
information that confuses the search engine. There does not seem to be an easy fix for this.
  - The bot settings all work as expected, but whenever the bot is reset (which is approximately every 24 hours if you are using Heroku) they will be reset to
default. We intend to fix this eventually by having the bot save off the settings to an external database.

# Setting up the bot

Bluez, unlike the previously existing generation of high profile discord bots, is open source and has no intellectual property protections or anything
of the sort. We welcome casual users to clone, share, modify, redistribute, and implement the code here in any way they please. Below, we provide the
technical steps for how to get your own instance of Bluez bot up and running.

### 1. Create a Discord developer account
If you do not already have a Discord developer account, go to discord.com/developers and follow the steps to create one.

### 2. Create a new application
Inside the developer portal, click "New Application". You can name your application "Bluez" or anything else. You can also use the bluez.png file in this
repository for the profile picture if you want.

### 3. Create a bot for your application
Inside your application, go to the "Bot" tab to create the bot for your application. Once this is done, make note of the bot's token (it is hidden by
default; click the "reveal token" button to see it). Do not share this token with anyone else, or they will be able to hijack your bot. (You can regenerate
a new secret token in the developer portal if this happens.) You will need the token later to run the bot.

### 4. Generate an invite link for your bot
There are two ways to do this. If you just intend to run the bot as is, without modifications, you can use a link that looks like this:
https://discord.com/api/oauth2/authorize?client_id=<your client id\>&permissions=397388377664&scope=bot%20applications.commands
where \<your client id\> is replaced by the Application ID number that you can see in the "General Information" tab. If you want to tinker with the
permissions and generate your own invite link, you can do that in the "OAuth2" tab. Check at least the "bot" and "applications.command" boxes, and
then check any permissions you want your bot to have in servers it joins. The link will be modified as needed to reflect these permissions. The
default permissions requested by the link given above are:
  - Change Nickname
  - View Channels
  - Send Messages
  - Public Threads
  - Private Threads
  - Send Messages in Threads
  - Manage Messages
  - Manage Threads
  - Embed Links
  - Read Message History
  - Add Reactions
  - Use Slash Commands
  - Connect
  - Speak
  - Video
  - Use Voice Activity

This is the link people will eventually use to invite your bot to servers. Don't use it just yet though or your bot won't do anything! You need to run the source
code as well.
  
### 5. Create a Github account
If you don't already have a Github account, you'll want to create one, and then clone this repository. You can then modify the code in your own version
of the repository if you so desire.
  
### 6. Create a Genius API account (optional)
You can set up an account for free here: http://genius.com/api-clients. After doing this, you'll want to create a new API client and generate a client access 
token for it. Keep track of the token it generates, as you will need it later. (If you don't do this step, the !lyrics command will not work, but the
other bot features will behave normally.)
  
### 7. Create a Heroku account
Go to heroku.com and create an account. Heroku is the simplest way to host an instance of the Bluez bot for free. If you know what you're doing, you can also
host it somewhere else, or even locally; but the following steps will assume you are using Heroku.

### 8. Create a new Heroku app
Again, we suggest you call it "Bluez" but it is up to you. 

### 9. Link your app to the Github repository you created in step 5
You can do this under the "Deploy" tab for your Heroku app.

### 10. Specify the config variables
You can do this under the "Settings" tab for your Heroku app. If you are hosting the bot someplace other than Heroku, you will need to specify environment/config variables
as well for the code to work properly. At minimum, you will need to specify your token as an environment variable. The following variables are recognized
by Bluez:
  - `BLUEZ_TOKEN`:          You should set this variable to your Discord bot token you generated in step 3.
  - `GENIUS_ACCESS_TOKEN`:  You should set this variable to the Genius API token you generated in step 6. You can skip this one if you don't care about the !lyrics command.
  - `BLUEZ_DEBUG`:          This variable is optional, you can set its value to "1" to enable debug messages or "0" to disable them. They are disabled by default.
  - `BLUEZ_INVITE_LINK`:    You can optionally set this to the invite link you generated in step 4 if you want to make it easy for people to add your bot to other servers. Be aware of the potential scalability issues if you do this.

### 11. Create a worker dyno
You can do this under the "Resources" tab for your Heroku app. Set the dyno to run the following shell command: `python bluez/__init__.py`

### 12. Deploy your bot
You can do this under the "Deploy" tab for your Heroku app. You can also enable automatic deploys if you desire. Note that Heroku will automatically
reset your bot approximately every 24 hours; there is not an easy way to get around this.

Once this is all done, the bot should be up and running on any servers you invite it to.
