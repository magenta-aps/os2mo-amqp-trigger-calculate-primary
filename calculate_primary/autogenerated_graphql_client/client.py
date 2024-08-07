# Generated by ariadne-codegen on 2024-08-08 08:47
# Source: queries.graphql

from uuid import UUID

from .async_base_client import AsyncBaseClient
from .get_engagement_person import GetEngagementPerson
from .get_engagement_person import GetEngagementPersonEngagements


def gql(q: str) -> str:
    return q


class GraphQLClient(AsyncBaseClient):
    async def get_engagement_person(self, uuid: UUID) -> GetEngagementPersonEngagements:
        query = gql(
            """
            query GetEngagementPerson($uuid: UUID!) {
              engagements(filter: {uuids: [$uuid], from_date: null, to_date: null}) {
                objects {
                  validities(start: null, end: null) {
                    person {
                      uuid
                    }
                  }
                }
              }
            }
            """
        )
        variables: dict[str, object] = {"uuid": uuid}
        response = await self.execute(query=query, variables=variables)
        data = self.get_data(response)
        return GetEngagementPerson.parse_obj(data).engagements
