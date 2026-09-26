"""The polling loop the realtime commands share: a tick every --interval seconds.

Django skips a commands module whose name starts with an underscore, so this
one is not a command itself.
"""

import time

from django.core.exceptions import ImproperlyConfigured
from django.core.management.base import BaseCommand

DEFAULT_INTERVAL_SECONDS = 5


class PollingCommand(BaseCommand):
    """Runs :meth:`tick` every --interval seconds until stopped, or once with --once.

    A tick that raises is reported and the next one retries, so a subclass's
    tick must leave the database where the next can resume from. A missing
    setting ends the polling instead: no tick can succeed until it changes.
    """

    # Printed once polling starts; ``{interval}`` is the seconds between ticks.
    start_message = "a tick every {interval:g}s; Ctrl-C to stop"

    def add_arguments(self, parser):
        parser.add_argument(
            "--interval",
            type=float,
            default=DEFAULT_INTERVAL_SECONDS,
            help=f"Seconds to wait after each tick (default {DEFAULT_INTERVAL_SECONDS}).",
        )
        parser.add_argument(
            "--once", action="store_true", help="Run one tick and exit instead of polling."
        )

    def handle(self, *args, **options):
        if options["once"]:
            self.tick()
            return
        self.stdout.write(self.start_message.format(interval=options["interval"]))
        try:
            while True:
                try:
                    self.tick()
                except ImproperlyConfigured:
                    raise
                except Exception as exc:
                    self.stderr.write(f"tick failed, retrying next interval: {exc!r}")
                time.sleep(options["interval"])
        except KeyboardInterrupt:
            self.stdout.write("stopped")

    def tick(self):
        raise NotImplementedError
