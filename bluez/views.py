# Discord UI view classes

import discord

from bluez.song import *
from bluez.util import *

SEARCH_PREV_NEXT = False # whether to enable page switching in the search embed
MAX_SEARCH_PAGES = 20 # the maximum number of pages to show in the search embed






##### Yes/no view #####

# Simple view with a Yes button and a No button


class YesNoView(discord.ui.View):

    def __init__(self, ctx, timeout=10):
        discord.ui.View.__init__(self, timeout=timeout)
        self.ctx = ctx
        self.result = None

    async def on_timeout(self):
        # called if the user's decision times out
        await self.ctx.send('**:no_entry_sign: Timeout**')
        self.stop()

    @discord.ui.button(label='Yes', style=discord.ButtonStyle.green)
    async def yes(self, interaction, button):
        # called when the user clicks "Yes"
        if interaction.user == self.ctx.author:
            self.result = True
            self.stop()
        else:
            await interaction.response.defer()

    @discord.ui.button(label='No', style=discord.ButtonStyle.red)
    async def no(self, interaction, button):
        # called when the user clicks "No"
        if interaction.user == self.ctx.author:
            self.result = False
            self.stop()
        else:
            await interaction.response.defer()




async def yesno(ctx, text):
    # Send a message with a yes/no view, and
    # wait for the user's response.
    view = YesNoView(ctx)
    message = (await ctx.send(text, view=view))
    await view.wait()
    await message.edit(view=None)
    return view.result

    
        




##### Multipage embed view #####

# View with prev/next buttons for viewing a multipage embed


class MultipageEmbedView(discord.ui.View):

    def __init__(self, message, embeds, current_page, timeout=30):
        discord.ui.View.__init__(self, timeout=timeout)
        self.message = message
        self.embeds = embeds
        self.current_page = current_page
        self.prev_button, self.next_button = self.children
        self.update_button_states()

    def update_button_states(self):
        self.prev_button.disabled = (self.current_page == 0)
        self.next_button.disabled = (self.current_page == len(self.embeds) - 1)

    @discord.ui.button(label='< Previous Page')
    async def prev(self, interaction, button):
        # called when someone clicks to go to the previous page
        if self.current_page > 0:
            self.current_page -= 1
            self.update_button_states()
            await self.message.edit(embed=self.embeds[self.current_page], view=self)
        await interaction.response.defer()

    @discord.ui.button(label='Next Page >')
    async def next(self, interaction, button):
        # called when someone clicks to go to the next page
        if self.current_page < len(self.embeds) - 1:
            self.current_page += 1
            self.update_button_states()
            await self.message.edit(embed = self.embeds[self.current_page], view=self)
        await interaction.response.defer()




async def post_multipage_embed(ctx, embeds, start_index=0):
    # Post a view with multiple embeds, and then allow any user to go to
    # the next or previous page in the view.
    if not embeds:
        await ctx.send('**:warning: Empty data**')
        return
    start_index = max(min(start_index, len(embeds)-1), 0)
    message = (await ctx.send(embed=embeds[start_index]))
    if len(embeds) > 1:
        view = MultipageEmbedView(message, embeds, start_index)
        await message.edit(view=view)
        await view.wait()
        await message.edit(view=None)







##### Search view #####

# View that supports searching for a song or playlist and viewing/selecting results in a text channel



        
class SearchView(discord.ui.View):


    def __init__(self, ctx, query, where, priority, is_dj, search_key=None, playlists=False, tempo=1.0, timeout=30):
        discord.ui.View.__init__(self, timeout=timeout)
        self.ctx = ctx
        self.query = query
        self.where = where
        self.priority = priority
        self.is_dj = is_dj
        self.search_key = search_key
        self.playlists = playlists
        self.tempo = tempo
        self.current_page = 0
        self.num_pages = 0
        self.message = None
        self.selection = None
        self.options = []
        self.select_menu, self.where_menu, self.priority_button, self.cancel_button = self.children[-4:]
        if SEARCH_PREV_NEXT:
            self.prev_button, self.next_button = self.children[:2]
            self.last_page = MAX_SEARCH_PAGES - 1
        self.update_button_states()


    def update_button_states(self):
        if SEARCH_PREV_NEXT:
            self.prev_button.disabled = (self.current_page == 0)
            self.next_button.disabled = (self.current_page == self.last_page)
        self.select_menu.options = [discord.SelectOption(label=str(i+1)) for i in range(len(self.options))]
        if self.select_menu.options:
            self.select_menu.disabled = False
        else:
            self.select_menu.options = [discord.SelectOption(label='1')]
            self.select_menu.disabled = True
        where_options = [
            discord.SelectOption(label='Bottom', description='Place the selected %s at the bottom of the queue' % ('playlist' if self.playlists else 'song')),
            discord.SelectOption(label='Top', description='Place the selected %s at the top of the queue' % ('playlist' if self.playlists else 'song')),
            discord.SelectOption(label='Now', description='Play the selected %s immediately, skipping anything that is currently playing' % ('playlist' if self.playlists else 'song')),
            discord.SelectOption(label='Shuffle', description='Shuffle the selected %s into the queue' % ('playlist' if self.playlists else 'song')),
            ]
        if self.is_dj:
            where_options[('Bottom', 'Top', 'Now', 'Shuffle').index(self.where)].default = True
            self.where_menu.options = where_options
            self.where_menu.disabled = False
        else:
            where_options[0].default = True
            self.where_menu.options = where_options[:1]
            self.where = 'Bottom'
            self.where_menu.disabled = True
        if self.where in ('Top', 'Now'):
            self.priority_button.disabled = True
            self.priority_button.style = discord.ButtonStyle.blurple
        elif self.priority:
            self.priority_button.disabled = False
            self.priority_button.style = discord.ButtonStyle.blurple
        else:
            self.priority_button.disabled = False
            self.priority_button.style = discord.ButtonStyle.gray



    async def open(self, emoji=':arrow_forward:'):
        await self.ctx.send('**%s Searching :mag: `%s`**' % (emoji, self.query))
        await self.search()
        if self.options:
            await self.update_embed()


    async def close(self):
        self.stop()
        if self.message is not None:
            await self.message.delete()
            self.message = None


    async def search(self):
        try:
            # Search using youtube-DL for playlists or songs
            if self.playlists:
                options = (await playlists_from_search(self.query, self.ctx.author, self.current_page * 10, (self.current_page + 1) * 10))
            else:
                options = (await songs_from_search(self.query, self.ctx.author, self.current_page * 10, (self.current_page + 1) * 10, self.search_key))
        except Exception as e:
            # error occurred (should not happen)
            await self.close()
            await self.ctx.send('**:x: Error searching for `%s`: `%s`**' % (self.query, e))
        else:
            if not options:
                if self.current_page == 0:
                    # There were no results at all. Don't even show the embed.
                    await self.close()
                    await self.ctx.send('**:x: There were no results matching the query**')
                else:
                    # There were no more results, but we have a previous page of results.
                    await self.ctx.send('**:warning: There are no more results to show**')
                    self.last_page = self.current_page - 1
                    self.current_page -= 1
                    self.update_button_states()
            else:
                # We have results; add them to the list of options
                self.options.extend(options)
                self.num_pages += 1
                self.update_button_states()
        if self.message is not None:
            await self.message.edit(view=self)



    async def update_embed(self):
        # Create an embed of the songs currently visible in the search view
        options = self.options[self.current_page * 10 : (self.current_page + 1) * 10]
        if self.playlists:
            description = '\n\n'.join(['`%d.` %s' % (i+1, format_link(playlist)) for i, playlist in enumerate(options, self.current_page * 10)])
        else:
            description = '\n\n'.join(['`%d.` %s **[%s]**' % (i+1, format_link(song),
                                                              format_time(song.length / self.tempo)) \
                                       for i, song in enumerate(options, self.current_page * 10)])
        embed = discord.Embed(description=description)
        embed.set_author(name=(self.ctx.author.nick or self.ctx.author.name), icon_url=self.ctx.author.avatar.url)
        embed.set_footer(text = 'Page %d of results' % (self.current_page + 1))
        if self.message is None:
            self.message = (await self.ctx.send(embed=embed, view=self))
        else:
            await self.message.edit(embed=embed, view=self)



    if SEARCH_PREV_NEXT:

        @discord.ui.button(label='< Previous Page', row=0)
        async def prev(self, interaction, button):
            # called when the user clicks to go to the previous page
            if interaction.user == self.ctx.author:
                if self.current_page > 0:
                    self.current_page -= 1
                    self.update_button_states()
                    await self.update_embed()
            await interaction.response.defer()


        @discord.ui.button(label='> Next Page', row=0)
        async def next(self, interaction, button):
            # called when the user clicks to go to the next page
            if interaction.user == self.ctx.author:
                if self.current_page < self.last_page:
                    self.current_page += 1
                    if self.current_page == self.num_pages:
                        await self.search()
                    else:
                        self.update_button_states()
                    await self.update_embed()
            await interaction.response.defer()


    @discord.ui.select(row=1)
    async def select_option(self, interaction, select):
        # called when the user picks a song/playlist to play
        if interaction.user == self.ctx.author:
            index = int(select.values[0]) - 1
            if index < len(self.options):
                self.selection = self.options[index]
                await self.close()
        else:
            await interaction.response.defer()


    @discord.ui.select(row=2)
    async def select_where(self, interaction, select):
        # called when the user selects a mode for when/how to play the selected song/playlist
        if interaction.user == self.ctx.author:
            self.where = select.values[0]
            self.update_button_states()
            await self.message.edit(view=self)
        await interaction.response.defer()


    @discord.ui.button(label='Priority', row=3)
    async def toggle_priority(self, interaction, button):
        # called when the user toggles the priority
        if interaction.user == self.ctx.author:
            if self.where in ('Bottom', 'Shuffle'):
                self.priority = (not self.priority)
                self.update_button_states()
                await self.message.edit(view=self)
        await interaction.response.defer()


    @discord.ui.button(label='Cancel', style=discord.ButtonStyle.red, row=3)
    async def cancel(self, interaction, button):
        # called to cancel the dialog without selecting anything
        if interaction.user == self.ctx.author:
            self.selection = None
            await self.close()
            await self.ctx.send(':white_check_mark:')
        else:
            await interaction.response.defer()
        



