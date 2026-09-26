"""Model fields for what a node returns."""

from django.db import models

from project.app.evm.constants import ADDRESS_LENGTH


class AddressField(models.CharField):
    """An account or contract address, stored lowercased.

    A checksummed address mixes case, so one account is one value only once
    every write folds it, and the column folds it whatever writes: a save, a
    bulk insert or upsert, an ``update()`` to a literal. The value an exact or
    ``in`` lookup compares with is folded the same way, and a saved instance
    holds the value as stored.
    """

    def __init__(self, *args, **kwargs):
        kwargs.setdefault("max_length", ADDRESS_LENGTH)
        super().__init__(*args, **kwargs)

    def pre_save(self, model_instance, add):
        value = super().pre_save(model_instance, add)
        if isinstance(value, str):
            value = value.lower()
            setattr(model_instance, self.attname, value)
        return value

    def get_prep_value(self, value):
        value = super().get_prep_value(value)
        return value.lower() if isinstance(value, str) else value
