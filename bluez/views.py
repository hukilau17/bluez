# Discord UI view classes

import discord
import asyncio





##### Yes/no view #####

# Simple view with a Yes button and a No button


class YesNoView(discord.ui.View):

    def __init__(self, ctx, timeout=10):
        discord.ui.View.__init__(self, timeout=timeout)
        self.ctx = ctx
        self.event = asyncio.Event()
        self.result = None

    async def wait(self):
        # wait for the user to make a decision, then return True/False
        # returns None if the decision timed out
        await self.event.wait()
        return self.result

    async def on_timeout(self):
        # called if the user's decision times out
        await self.ctx.send('**:no_entry_sign: Timeout**')
        self.close()

    def close(self):
        # close the view, either because the user made a decision
        # or it timed out
        self.stop()
        self.event.set()

    @discord.ui.button(label='Yes', style=discord.ButtonStyle.green)
    async def yes(self, interaction, button):
        # called when the user clicks "Yes"
        if interaction.user == self.ctx.author:
            self.result = True
            self.close()

    @discord.ui.button(label='No', style=discord.ButtonStyle.red)
    async def no(self, interaction, button):
        # called when the user clicks "No"
        if interaction.user == self.ctx.author:
            self.result = False
            self.close()




async def yesno(ctx, text):
    # Send a message with a yes/no view, and
    # wait for the user's response.
    view = YesNoView(ctx)
    message = (await ctx.send(text, view=view))
    result = (await view.wait())
    await message.edit(view=None)
    return result

    
        
        
