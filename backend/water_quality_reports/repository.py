"""水质报表只读查询仓储。"""

from __future__ import annotations

import time
from collections.abc import Callable, Mapping, Sequence
from datetime import datetime
from typing import Any

import pymysql
from pymysql.cursors import DictCursor

from config.data_source_config import DataSourceConfig


REPORT_SOURCE_ID = "mysql-lzh-monitor"
REPORT_TABLE_WHITELIST = frozenset(
    {
        "wm_station_info",
        "wm_section_info",
        "wm_waterquality_hour_records",
        "wm_waterquality_day_records",
        "wm_waterquality_month_records",
        "wm_section_wq_info",
    }
)


class ReportDataSourceError(RuntimeError):
    """对外仅暴露安全消息的数据源异常。"""


ConnectionFactory = Callable[[], Any]


class ReportRepository:
    """每次查询均在显式只读事务中执行，并记录耗时。"""

    def __init__(
        self,
        config: DataSourceConfig,
        *,
        connection_factory: ConnectionFactory | None = None,
    ) -> None:
        if config.source_id != REPORT_SOURCE_ID or config.database_type != "mysql":
            raise ValueError("水质报表数据源配置不匹配")
        if config.read_only is not True:
            raise ValueError("水质报表数据源必须为只读")
        self.config = config
        self._connection_factory = connection_factory or self._connect
        self.query_timings: list[dict[str, object]] = []

    def _connect(self):
        settings = dict(self.config.connection_settings)
        return pymysql.connect(
            host=settings["host"],
            port=settings["port"],
            database=settings["database"],
            user=settings["user"],
            password=settings["password"],
            connect_timeout=settings["connect_timeout"],
            charset=settings.get("charset", "utf8mb4"),
            cursorclass=DictCursor,
            autocommit=False,
        )

    def _query(
        self,
        name: str,
        sql: str,
        params: Sequence[object] = (),
    ) -> list[dict[str, Any]]:
        started = time.perf_counter()
        connection = self._connection_factory()
        try:
            with connection.cursor() as cursor:
                cursor.execute("SET SESSION TRANSACTION READ ONLY")
                cursor.execute("START TRANSACTION READ ONLY")
                cursor.execute(sql, params)
                rows = list(cursor.fetchall())
            connection.rollback()
            return rows
        except Exception as exc:
            try:
                connection.rollback()
            except Exception:
                pass
            raise ReportDataSourceError("水质报表数据源暂不可用") from exc
        finally:
            connection.close()
            self.query_timings.append(
                {
                    "query": name,
                    "elapsed_ms": round((time.perf_counter() - started) * 1000, 2),
                }
            )

    def stations(self) -> list[dict[str, Any]]:
        return self._query(
            "stations",
            """
            SELECT s.id, s.station_code, s.station_name, s.station_type,
                   s.build_state, s.water_type, s.water_body_id, s.section_id,
                   s.monitor_frequency, s.remark,
                   sec.tributary_trunk
            FROM wm_station_info AS s
            LEFT JOIN wm_section_info AS sec
              ON sec.id = s.section_id AND sec.del_flag = '0'
            WHERE s.del_flag = '0'
              AND s.water_quality_table_name IS NOT NULL
            ORDER BY s.id
            LIMIT 200
            """,
        )

    def hourly_records(
        self,
        start: datetime,
        end: datetime,
        *,
        limit: int = 100_000,
    ) -> list[dict[str, Any]]:
        columns = ", ".join(
            ["id", "station_id", "monitor_time", "status", "water_quality_level"]
            + [f"m{i}_value" for i in range(1, 32)]
        )
        return self._query(
            "hourly_records",
            f"""
            SELECT {columns}
            FROM wm_waterquality_hour_records
            WHERE del_flag = '0'
              AND monitor_time >= %s
              AND monitor_time < %s
            ORDER BY monitor_time, station_id, id
            LIMIT %s
            """,
            (start, end, limit),
        )

    def daily_records(
        self,
        start: datetime,
        end: datetime,
        *,
        limit: int = 10_000,
    ) -> list[dict[str, Any]]:
        columns = ", ".join(
            ["id", "station_id", "monitor_time", "status", "water_quality_level"]
            + [
                item
                for i in range(1, 32)
                for item in (f"m{i}_value", f"m{i}_count")
            ]
        )
        return self._query(
            "daily_records",
            f"""
            SELECT {columns}
            FROM wm_waterquality_day_records
            WHERE del_flag = '0'
              AND monitor_time >= %s
              AND monitor_time < %s
            ORDER BY monitor_time, station_id, id
            LIMIT %s
            """,
            (start, end, limit),
        )

    def monthly_records(
        self,
        start: datetime,
        end: datetime,
        *,
        limit: int = 2_000,
    ) -> list[dict[str, Any]]:
        return self._query(
            "monthly_records",
            """
            SELECT id, station_id, section_id, monitor_time, monitor_year,
                   monitor_month, water_quality_level, main_pollutant, remark
            FROM wm_waterquality_month_records
            WHERE del_flag = '0'
              AND monitor_time >= %s
              AND monitor_time < %s
            ORDER BY monitor_time, COALESCE(station_id, section_id), id
            LIMIT %s
            """,
            (start, end, limit),
        )

    def targets(self, year: int, month: int) -> list[dict[str, Any]]:
        return self._query(
            "water_quality_targets",
            """
            SELECT id, section_id, year, month, water_quality_target_level
            FROM wm_section_wq_info
            WHERE del_flag = '0'
              AND year = %s
              AND month IN (0, %s)
            ORDER BY section_id, month DESC, id DESC
            LIMIT 500
            """,
            (year, month),
        )
