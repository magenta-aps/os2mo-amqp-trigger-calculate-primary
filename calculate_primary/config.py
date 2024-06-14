# SPDX-FileCopyrightText: Magenta ApS <https://magenta.dk>
# SPDX-License-Identifier: MPL-2.0
from typing import Literal
from uuid import UUID

from fastramqpi.config import Settings as FastRAMQPISettings
from pydantic import BaseSettings

class _Settings(BaseSettings):
    class Config:
        frozen = True
        env_nested_delimiter = "__"

    fastramqpi: FastRAMQPISettings

    integration: Literal["DEFAULT", "OPUS", "SD"]
    dry_run: bool = False
    eng_types_primary_order: list[UUID] = []
