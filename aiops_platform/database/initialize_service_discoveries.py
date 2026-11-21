"""Create only the additive service-discovery table, if it is missing."""

from aiops_platform.database.database import engine
from aiops_platform.database.models import ServiceDiscovery


def main() -> None:
    # Target this table only. Do not call Base.metadata.create_all(), which
    # could attempt to manage unrelated application tables.
    ServiceDiscovery.__table__.create(bind=engine, checkfirst=True)
    print("Table app.service_discoveries is ready.")


if __name__ == "__main__":
    main()
