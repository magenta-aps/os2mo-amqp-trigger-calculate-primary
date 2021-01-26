import logging

import click
from click_option_group import RequiredMutuallyExclusiveOptionGroup, optgroup
from integrations.calculate_primary.common import LOGGER_NAME


def setup_logging():
    LOG_LEVEL = logging.DEBUG
    LOG_FILE = "calculate_primary.log"

    detail_logging = ("mora-helper", LOGGER_NAME)
    for name in logging.root.manager.loggerDict:
        if name in detail_logging:
            logging.getLogger(name).setLevel(LOG_LEVEL)
        else:
            logging.getLogger(name).setLevel(logging.ERROR)

    logging.basicConfig(
        format="%(levelname)s %(asctime)s %(name)s %(message)s",
        level=LOG_LEVEL,
        filename=LOG_FILE,
    )


def get_engagement_updater(integration):
    if integration == "SD":
        from integrations.calculate_primary.sd import \
            SDPrimaryEngagementUpdater

        return SDPrimaryEngagementUpdater
    if integration == "OPUS":
        from integrations.calculate_primary.opus import \
            OPUSPrimaryEngagementUpdater

        return OPUSPrimaryEngagementUpdater
    raise NotImplementedError("No engagement updater implemented for " + integration)


@click.command()
@click.option(
    "--integration",
    type=click.Choice(["SD", "OPUS"], case_sensitive=False),
    help="Integration to use",
)
@click.option(
    "--dry-run",
    is_flag=True, type=click.BOOL, help="Make no changes"
)
@optgroup.group("Operation", cls=RequiredMutuallyExclusiveOptionGroup, help="")
@optgroup.option(
    "--check-all", is_flag=True, type=click.BOOL, help="Check all users"
)
@optgroup.option("--check-user", type=click.UUID, help="Check one user")
@optgroup.option(
    "--recalculate-all", is_flag=True, type=click.BOOL, help="Recalculate all users"
)
@optgroup.option("--recalculate-user", type=click.UUID, help="Recalculate one user")
def calculate_primary(
    integration, dry_run, check_all, check_user, recalculate_all, recalculate_user
):
    """Tool to work with primary engagement(s)."""
    setup_logging()

    # Acquire the configured updater
    updater_class = get_engagement_updater(integration)
    updater = updater_class(dry_run=dry_run)

    # Run the specified operation
    if check_all:
        print("Check all")
        updater.check_all()
    if check_user:
        print("Check user")
        updater.check_user(check_user)
    if recalculate_all:
        print("Recalculate all")
        updater.recalculate_all(no_past=True)
    if recalculate_user:
        print("Recalculate user")
        updater.recalculate_user(recalculate_user)


if __name__ == "__main__":
    calculate_primary()
