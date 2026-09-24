"""The token standard lookup: the interfaces a token contract can implement."""

from django.db import models


class TokenStandard(models.Model):
    """One token standard, such as "ERC-20", "ERC-721" or "ERC-1155".

    A static registry: tokens point at a row here rather than repeating the name.
    """

    id = models.AutoField(primary_key=True)
    name = models.CharField(max_length=20, unique=True)  # "ERC-20"

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return self.name
