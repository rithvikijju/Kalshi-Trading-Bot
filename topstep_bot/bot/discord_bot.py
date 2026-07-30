"""
Discord control surface + alert sink.

Commands (prefix `!`):
  !status            full snapshot (risk, positions, capture, model state)
  !risk              risk line only
  !pause / !resume   stop / start taking new entries
  !flatten           market-close everything now
  !approve [INST]    take a pending setup (manual-approve mode)
  !mode              show current mode (sim/live) — promotion is a restart, by design
  !positions         open positions
  !help              list commands

The bot also pushes real-time alerts (setups, entries, exits, risk events) to the
configured channel via `notify()`, which the Trader calls.
"""
from __future__ import annotations

import discord
from discord.ext import commands


class DiscordControl:
    def __init__(self, cfg):
        self.cfg = cfg
        self.trader = None                       # set by run.py after Trader is built
        self._channel = None
        intents = discord.Intents.default()
        intents.message_content = True
        self.bot = commands.Bot(command_prefix="!", intents=intents, help_command=None)
        self._register()

    async def notify(self, msg: str):
        """Push an alert to the configured channel (called by the Trader)."""
        if self._channel is None and self.cfg.discord_channel:
            self._channel = self.bot.get_channel(self.cfg.discord_channel)
        if self._channel is not None:
            for chunk in _chunks(msg, 1900):
                await self._channel.send(chunk)
        else:
            print(msg)

    def _register(self):
        bot, self_ref = self.bot, self

        @bot.event
        async def on_ready():
            self_ref._channel = bot.get_channel(self_ref.cfg.discord_channel)
            await self_ref.notify(f"🤖 Discord control online as {bot.user}.")

        @bot.command()
        async def status(ctx):
            await ctx.send(_code(self_ref.trader.status()))

        @bot.command()
        async def risk(ctx):
            await ctx.send(_code(self_ref.trader._risk_line()))

        @bot.command()
        async def positions(ctx):
            pos = [f"{i}: {p.size} @ {p.avg_price:.2f} (real ${p.realized:+.2f})"
                   for i, p in self_ref.trader.broker.positions.items() if not p.flat]
            await ctx.send(_code("\n".join(pos) if pos else "flat"))

        @bot.command()
        async def pause(ctx):
            await self_ref.trader.pause()

        @bot.command()
        async def resume(ctx):
            await self_ref.trader.resume()

        @bot.command()
        async def flatten(ctx):
            await self_ref.trader.flatten_all()

        @bot.command()
        async def approve(ctx, instrument: str = None):
            await self_ref.trader.approve(instrument)

        @bot.command()
        async def mode(ctx):
            await ctx.send(_code(f"mode = {self_ref.cfg.mode} "
                                 f"(promotion to live is an explicit restart with mode=live)"))

        @bot.command(name="help")
        async def help_cmd(ctx):
            await ctx.send(_code(
                "!status !risk !positions !pause !resume !flatten !approve [INST] !mode"))

    async def run(self):
        await self.bot.start(self.cfg.discord_token)


def _chunks(s: str, n: int):
    for i in range(0, len(s), n):
        yield s[i:i + n]


def _code(s: str) -> str:
    return f"```\n{s}\n```"
