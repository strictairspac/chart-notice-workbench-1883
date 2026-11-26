from src.models import archives  # Register the archive tables.
from src.models.base import metadata


def upgrade(connection):
    # Version 2 adds archive imports and restored receipts; additive and
    # declarative so existing databases gain only the missing tables.
    metadata.create_all(connection)
