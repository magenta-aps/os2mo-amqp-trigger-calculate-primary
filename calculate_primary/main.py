# SPDX-FileCopyrightText: 2019-2020 Magenta ApS
#
# SPDX-License-Identifier: MPL-2.0
"""Event-driven recalculate primary program."""
from typing import List
from uuid import UUID
import structlog

from prometheus_client import Counter
from prometheus_client import Gauge
from prometheus_client import Info

from calculate_primary.calculate_primary import get_engagement_updater
from calculate_primary.common import MOPrimaryEngagementUpdater
logger = structlog.get_logger(__name__)

edit_counter = Counter("recalculate_edit", "Number of edits made")
no_edit_counter = Counter("recalculate_no_edit", "Number of noops made")
last_processing = Gauge(
    "recalculate_last_processing", "Timestamp of the last processing"
)
version_info = Info("recalculate_build_version", "Version information")


def calculate_user(updater: MOPrimaryEngagementUpdater, uuid: UUID) -> None:
    """Recalculate the user given by uuid.

    Called for the side-effect of making calls against MO using the updater.

    Args:
        updater: The calculate primary updater instance.
        uuid: UUID for the user to recalculate.

    Returns:
        None
    """
    logger.info(f"Recalculating user: {uuid}")
    last_processing.set_to_current_time()
    # TODO: An async version would be desireable
    updates = updater.recalculate_user(uuid)
    # Update edit metrics
    for number_of_edits in updates.values():
        if number_of_edits == 0:
            no_edit_counter.inc()
        edit_counter.inc(number_of_edits)


def _setup_updater(
    settings,
) -> MOPrimaryEngagementUpdater:
    """Exchange integration to updater.

    Args:
        integration: The integration to construct.
        dry_run: Whether to dry-run the updater.
        mo_url: URL for OS2mo.

    Returns:
        The constructed updater.
    """
    logger.info("Configuring calculate-primary logging")

    logger.info(f"Acquiring updater: {settings.integration}")
    updater_class = get_engagement_updater(settings.integration)
    logger.info(f"Got class: {updater_class}")
    updater: MOPrimaryEngagementUpdater = updater_class(
        settings=settings,
    )
    logger.info(f"Got object: {updater}")
    return updater
