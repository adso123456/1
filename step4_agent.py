"""已禁用的遗留入口。"""

import sys


DISABLED_MESSAGE = "旧入口已禁用，请使用 step4_server.py"


def main() -> int:
    print(DISABLED_MESSAGE)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
