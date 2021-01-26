import datetime
import logging
from abc import ABC, abstractmethod
from functools import lru_cache, partial
from operator import itemgetter

from exporters.utils.load_settings import load_settings
from more_itertools import ilen, pairwise, only
from os2mo_helpers.mora_helpers import MoraHelper
from tqdm import tqdm


LOGGER_NAME = "updatePrimaryEngagements"
logger = logging.getLogger(LOGGER_NAME)


class MultipleFixedPrimaries(Exception):
    """Thrown when multiple fixed primaries are found doing recalculate."""
    pass


class NoPrimaryFound(Exception):
    """Thrown when no primary is determined doing recalculate."""
    pass


class EngagementMissingUserKey(Exception):
    """Thrown when an engagement is missing its userkey during recalculate."""


def noop(*args, **kwargs):
    """Noop function, which consumes arguments and does nothing."""
    pass


def edit_engagement(data, mo_engagement_uuid):
    payload = {"type": "engagement", "uuid": mo_engagement_uuid, "data": data}
    return payload


class MOPrimaryEngagementUpdater(ABC):
    def __init__(self, settings=None, dry_run=False):
        self.settings = settings or load_settings()
        self.dry_run = dry_run

        self.helper = self._get_mora_helper(self.settings['mora.base'])

        # List of engagement filters to apply to check / recalculate respectively
        # NOTE: Should be overridden by subclasses
        self.check_filters = []
        self.calculate_filters = []

        self.primary_types, self.primary = self._find_primary_types()

    @lru_cache(maxsize=None)
    def _get_org_uuid(self):
        org_uuid = self.helper.read_organisation()
        return org_uuid

    def _get_mora_helper(self, mora_base):
        return MoraHelper(hostname=mora_base, use_cache=False)

    def _get_person(self, cpr=None, uuid=None, mo_person=None):
        """
        Set a new person as the current user. Either a cpr-number or
        an uuid should be given, not both.
        :param cpr: cpr number of the person.
        :param uuid: MO uuid of the person.
        :param mo_person: An already existing user object from mora_helper.
        """
        if uuid:
            mo_person = self.helper.read_user(user_uuid=uuid)
        elif cpr:
            mo_person = self.helper.read_user(
                user_cpr=cpr, org_uuid=self._get_org_uuid()
            )
        return mo_person

    def _read_engagement(self, user_uuid, date):
        mo_engagement = self.helper.read_user_engagement(
            user=user_uuid,
            at=date,
            only_primary=True,  # Do not read extended info from MO.
            use_cache=False,
        )
        return mo_engagement

    @abstractmethod
    def _find_primary_types(self):
        """Find primary classes for the underlying implementation.

        Returns:
            2-tuple:
                primary_types: a dict from indirect primary names to UUIDs.
                    The used names are 'fixed_primary', 'primary' and 'non_primary',
                    as such these names should be keys in the dictionary.
                primary: a list of UUIDs that can considered to be primary.
                    Should be a subset of the values in primary_types.
        """
        raise NotImplementedError()

    @abstractmethod
    def _find_primary(self, mo_engagements):
        """Decide which of the engagements in mo_engagements is the primary.

        This method does not need to handle fixed_primaries as the method will
        not be called if fixed_primaries exist.

        Args:
            mo_engagements: List of engagements

        Returns:
            UUID: The UUID of the primary engagement.
        """
        raise NotImplementedError()

    def _predicate_primary_is(self, primary_type_key, engagement):
        """Predicate on an engagements primary type.

        Example:

            is_non_primary = partial(_predicate_primary_is, "non_primary")
            mo_engagement = ...
            is_mo_engagement_non_primary = is_non_primary(mo_engagement)

        Args:
            primary_type_key: Lookup key into self.primary_types
            engagement: The engagement to check primary status from

        Returns:
            boolean: True if engagement has primary type equal to primary_type_key
                     False otherwise
        """
        assert primary_type_key in self.primary_types

        if engagement["primary"]["uuid"] == self.primary_types[primary_type_key]:
            logger.info(
                "Engagement {} is {}".format(engagement["uuid"], primary_type_key)
            )
            return True
        return False

    def _count_primary_engagements(self, check_filters, user_uuid, mo_engagements):
        """Count number of primaries.

        Args:
            check_filters: A list of predicate functions from (user_uuid, eng).
            user_uuid: UUID of the user to who owns the engagements.
            engagements: A list of MO engagements to count primaries from.

        Returns:
            3-tuple:
                engagement_count: Number of engagements processed.
                primary_count: Number of primaries found.
                filtered_primary_count: Number of primaries passing check_filters.
        """
        # Count number of engagements
        mo_engagements = list(mo_engagements)
        engagement_count = len(mo_engagements)

        # Count number of primary engagements, by filtering on self.primary
        primary_mo_engagements = list(filter(
            lambda eng: eng["primary"]["uuid"] in self.primary,
            mo_engagements,
        ))
        primary_count = len(primary_mo_engagements)

        # Count number of primary engagements, by filtering out special primaries
        # What consistutes a 'special primary' depend on the subclass implementation
        for filter_func in check_filters:
            primary_mo_engagements = filter(
                partial(filter_func, user_uuid), primary_mo_engagements
            )
        filtered_primary_count = ilen(primary_mo_engagements)

        return engagement_count, primary_count, filtered_primary_count

    def _check_user(self, check_filters, user_uuid):
        """Check the users primary engagement(s).

        Args:
            check_filters: A list of predicate functions from (user_uuid, eng).
            user_uuid: UUID of the user to check.

        Returns:
            Dictionary:
                key: Date at which the value is valid.
                value: A 3-tuple, from _count_primary_engagements.
        """
        # List of cut dates, excluding the very last one
        date_list = self.helper.find_cut_dates(uuid=user_uuid)
        date_list = date_list[:-1]
        # Map all our dates, to their corresponding engagements.
        mo_engagements = map(
            partial(self._read_engagement, user_uuid), date_list
        )
        # Map mo_engagements to primary counts
        primary_counts = map(
            partial(self._count_primary_engagements, check_filters, user_uuid),
            mo_engagements
        )
        # Create dicts from cut_dates --> primary_counts
        return dict(zip(date_list, primary_counts))

    def _check_user_outputter(self, check_filters, user_uuid):
        """Check the users primary engagement(s).

        Args:
            check_filters: A list of predicate functions from (user_uuid, eng).
            user_uuid: UUID of the user to check.

        Returns:
            Generator of output 4-tuples:
                outputter: Function to output strings to
                string: The base output string
                user_uuid: User UUID for the output string
                date: Date for the output string
        """
        def to_output(e_count, p_count, fp_count):
            e_count = min(e_count, 1)
            p_count = min(p_count, 2)
            fp_count = min(fp_count, 2)

            fp_table = {
                0: (logger.info, "All primaries are special"),
                1: (logger.info, "Only one non-special primary"),
                2: (print, "Too many primaries"),
            }
            p_table = {
                0: lambda fp_count: (print, "No primary"),
                1: lambda fp_count: (noop, ""),
                2: lambda fp_count: fp_table[fp_count],
            }
            e_table = {
                0: lambda p_count, fp_count: (noop, ""),
                1: lambda p_count, fp_count: p_table[p_count](fp_count)
            }
            return e_table[e_count](p_count, fp_count)

        user_results = self._check_user(
            check_filters, user_uuid
        )
        for date, (e_count, p_count, fp_count) in user_results.items():
            outputter, string = to_output(e_count, p_count, fp_count)
            yield outputter, string, user_uuid, date

    def _check_user_strings(self, check_filters, user_uuid):
        """Check the users primary engagement(s).

        Args:
            check_filters: A list of predicate functions from (user_uuid, eng).
            user_uuid: UUID of the user to check.

        Returns:
            Generator of output 2-tuples:
                outputter: Function to output strings to
                string: Formatted output string
        """
        outputs = self._check_user_outputter(check_filters, user_uuid)
        for outputter, string, user_uuid, date in outputs:
            final_string = string + " for {} at {}".format(user_uuid, date.date())
            yield outputter, final_string

    def check_user(self, user_uuid):
        """Check the users primary engagement(s).

        Prints messages to stdout / log as side-effect.

        Args:
            user_uuid: UUID of the user to check.

        Returns:
            None
        """
        outputs = self._check_user_strings(self.check_filters, user_uuid)
        for outputter, string in outputs:
            outputter(string)

    def recalculate_primary(self, user_uuid, no_past=False):
        # Kept for backwards compatability
        logger.info("DEPRECATED: Called recalculate_primary")
        return self.recalculate_user(user_uuid, no_past=no_past)

    def _decide_primary(self, mo_engagements):
        """Decide which of the engagements in mo_engagements is the primary.

        Args:
            mo_engagements: List of engagements

        Returns:
            2-tuple:
                UUID: The UUID of the primary engagement.
                primary_type_key: The type of the primary.
        """
        # First we attempt to find a fixed primary engagement.
        # If multiple are found, we throw an exception, as only one is allowed.
        # If one is found, it is our primary and we are done.
        # If none are found, we need to calculate the primary engagement.
        find_fixed_primary = partial(self._predicate_primary_is, "fixed_primary")
        # Iterator of UUIDs of engagements with primary = fixed_primary
        fixed_primary_engagement_uuids = map(
            itemgetter('uuid'), filter(find_fixed_primary, mo_engagements)
        )
        # UUID of engagement with primary = fixed_primary, exception or None
        fixed = only(fixed_primary_engagement_uuids, None, too_long=MultipleFixedPrimaries)
        if fixed:
            return fixed, "fixed_primary"

        # No fixed engagements, thus we must calculate the primary engagement.
        #
        # The calulcation of primary engagement depends on the underlying
        # implementation, thus we simply call self._find_primary here.
        primary = self._find_primary(mo_engagements)
        if primary:
            return primary, "primary"
        raise NoPrimaryFound()

    def _ensure_primary(self, engagement, primary_type_uuid, validity):
        """Ensure that engagement has the right primary_type.

        Args:
            engagement: The engagement to (potentially) update.
            primary_type_uuid: The primary type to ensure the engagement has.
            validity: The validity of the change (if made).

        Returns:
            boolean: True if a change is made, False otherwise.
        """
        # Check if the required primary type is already set
        if engagement["primary"]["uuid"] == primary_type_uuid:
            logger.info(
                "No update as primary type is not changed: {}".format(
                    validity['from']
                )
            )
            return False

        # At this point, we have to update the entry
        data = {"primary": {"uuid": primary_type_uuid}, "validity": validity}
        payload = edit_engagement(data, engagement["uuid"])
        logger.debug("Edit payload: {}".format(payload))

        if not self.dry_run:
            response = self.helper._mo_post("details/edit", payload)
            assert response.status_code in (200, 400)
            if response.status_code == 400:
                logger.info("Attempted edit, but no change needed.")
        return True

    def recalculate_user(self, user_uuid, no_past=False):
        """
        Re-calculate primary engagement for the entire history of the current user.
        """
        logger.info("Calculate primary engagement: {}".format(user_uuid))
        date_list = self.helper.find_cut_dates(user_uuid, no_past=no_past)
        number_of_edits = 0

        def fetch_mo_engagements(date):
            def ensure_primary(engagement):
                """Ensure that engagement has a primary field."""
                # TODO: It would seem this happens for leaves, should we make a
                #       special type for this?
                # TODO: What does the above even mean?
                if not engagement["primary"]:
                    engagement["primary"] = {"uuid": self.primary_types["non_primary"]}
                return engagement

            # Fetch engagements
            mo_engagements = self._read_engagement(user_uuid, date)
            # Filter unwanted engagements
            for filter_func in self.calculate_filters:
                mo_engagements = filter(
                    partial(filter_func, user_uuid, no_past), mo_engagements
                )
            # Enrich engagements with primary, if required
            mo_engagements = map(ensure_primary, mo_engagements)
            mo_engagements = list(mo_engagements)

            return mo_engagements

        def calculate_validity(start, end):
            to = datetime.datetime.strftime(
                end - datetime.timedelta(days=1), "%Y-%m-%d"
            )
            if end == datetime.datetime(9999, 12, 30, 0, 0):
                to = None
            validity = {
                "from": datetime.datetime.strftime(start, "%Y-%m-%d"),
                "to": to,
            }
            return validity

        for start, end in pairwise(date_list):
            logger.info("Recalculate primary, date: {}".format(start))

            mo_engagements = fetch_mo_engagements(start)
            logger.debug("MO engagements: {}".format(mo_engagements))

            # No engagements, nothing to do
            if len(mo_engagements) == 0:
                continue

            validity = calculate_validity(start, end)
            primary_uuid, primary_type_key = self._decide_primary(mo_engagements)

            # Update the primary type of all engagements (if required)
            for engagement in mo_engagements:
                # All engagements are non_primary, except for the primary one.
                primary_type_uuid = self.primary_types["non_primary"]
                # The primary engagement gets the primary_type determined by 
                # the _decide_primary function, could be fixed_primary or primary.
                if engagement['uuid'] == primary_uuid:
                    primary_type_uuid = self.primary_types[primary_type_key]

                changed = self._ensure_primary(
                    engagement, primary_type_uuid, validity
                )
                if changed:
                    number_of_edits += 1

        return_dict = {user_uuid: number_of_edits}
        return return_dict

    def check_all(self):
        """Check all users for the existence of primary engagements."""
        print("Reading all users from MO...")
        all_users = self.helper.read_all_users()
        print("OK")
        for user in tqdm(all_users):
            self.check_user(user["uuid"])

    def recalculate_all(self, no_past=False):
        """Recalculate all users primary engagements."""
        print("Reading all users from MO...")
        all_users = self.helper.read_all_users()
        print("OK")
        edit_status = {}
        for user in tqdm(all_users):
            status = self.recalculate_user(user["uuid"], no_past=no_past)
            edit_status.update(status)
        print("Total edits: {}".format(sum(edit_status.values())))
