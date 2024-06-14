# SPDX-FileCopyrightText: Magenta ApS <https://magenta.dk>
# SPDX-License-Identifier: MPL-2.0
import structlog
from fastramqpi.ramqp.mo import MORouter
from fastramqpi.ramqp.mo import PayloadUUID
from calculate_primary import depends

from calculate_primary.main import calculate_user


router = MORouter()
logger = structlog.get_logger(__name__)

@router.register("engagement")
async def calculate_engagement(
    engagement_uuid: PayloadUUID,
    mo: depends.GraphQLClient,
    updater: depends.Updater,
) -> None:
    logger.info(f"Registered event for engagement {engagement_uuid=}")
    result = await mo.get_engagement_person(engagement_uuid)
    uuids = {e.uuid for obj in result.objects for o in obj.objects for e in o.employee}

    logger.info(f"Found related person(s) {uuids=}")
    #There are probably only one person pr. engagement, but just to be sure
    for person_uuid in uuids:
        calculate_user(updater, person_uuid)
