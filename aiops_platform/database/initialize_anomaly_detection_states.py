"""Create only app.anomaly_detection_states when it is missing."""

from aiops_platform.database.database import engine
from aiops_platform.database.models import AnomalyDetectionState


def main() -> None:
    AnomalyDetectionState.__table__.create(bind=engine, checkfirst=True)
    print("Table app.anomaly_detection_states is ready.")


if __name__ == "__main__":
    main()
