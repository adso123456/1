"""容器部署自检：验证外部数据库与镜像内关键资产，不输出凭据。"""

from __future__ import annotations

import os
import sqlite3

import psycopg2
import pymysql

from config.data_sources import build_mysql_data_source_config
from config.settings import build_db_kwargs, resolve_project_path


def main() -> None:
    with psycopg2.connect(**build_db_kwargs()) as connection:
        with connection.cursor() as cursor:
            cursor.execute("SELECT 1")

    mysql_settings = build_mysql_data_source_config().connection_settings
    with pymysql.connect(**mysql_settings) as connection:
        with connection.cursor() as cursor:
            cursor.execute("SELECT 1")

    catalog_path = resolve_project_path(os.environ["DATA_SOURCE_CATALOG_PATH"])
    with sqlite3.connect(catalog_path) as connection:
        absolute_path_count = connection.execute(
            """
            SELECT count(*) FROM data_sources
            WHERE instr(metadata_path, ':') > 0
               OR substr(metadata_path, 1, 1) = '/'
               OR instr(memory_path, ':') > 0
               OR substr(memory_path, 1, 1) = '/'
            """
        ).fetchone()[0]

    env_exists = resolve_project_path(".env").is_file()
    model_exists = resolve_project_path(
        ".cache/huggingface/hub/models--BAAI--bge-small-zh-v1.5"
    ).is_dir()
    print(
        "postgresql=ok mysql=ok "
        f"catalog_absolute_paths={absolute_path_count} "
        f"env_in_image={env_exists} model_in_image={model_exists}"
    )


if __name__ == "__main__":
    main()
