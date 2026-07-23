"""旧导入路径兼容层；正式定义位于 config.data_source_config。"""

from config.data_source_config import (
    POSTGRESQL_REQUIRED_CONNECTION_FIELDS,
    SOURCE_ID_PATTERN,
    DataSourceConfig,
)

__all__ = (
    "DataSourceConfig",
    "POSTGRESQL_REQUIRED_CONNECTION_FIELDS",
    "SOURCE_ID_PATTERN",
)
