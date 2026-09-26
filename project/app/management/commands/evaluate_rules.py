"""Evaluate the enabled rules against every block not evaluated yet, recording what they match.

Run after the blocks are stored and decoded: a rule reading token transfers
waits for decoding to finish with a block. Each block is evaluated once,
against every owner's enabled rules, and each row a rule matches is recorded as
a MatchedRule, so a re-run only picks up blocks stored since and the ones left
waiting for decoding.
"""

from django.core.management.base import BaseCommand

from project.app.rules import services


class Command(BaseCommand):
    help = (
        "Evaluate every enabled rule against each block not evaluated yet, "
        "recording each row a rule matches."
    )

    def handle(self, *args, **options):
        run = services.evaluate_blocks()
        for rule, error in run.refused.items():
            self.stderr.write(f"rule {rule.pk} {rule.name!r} could not be evaluated: {error}")
        self.stdout.write(
            f"Evaluated {run.blocks} block(s) and recorded {run.matches} match(es); "
            f"{run.undecoded} block(s) wait for decoding."
        )
