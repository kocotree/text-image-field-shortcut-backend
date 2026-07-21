from __future__ import annotations

import os

from api.app import create_app

app = create_app()


def main() -> None:
    """启动本地 Flask 开发服务器。

    返回值：
        无。
    """
    host = os.getenv("FLASK_HOST", "127.0.0.1")
    port = int(os.getenv("FLASK_PORT", "5000"))
    debug = os.getenv("FLASK_DEBUG", "true").lower() == "true"
    app.run(host=host, port=port, debug=debug)


if __name__ == "__main__":
    main()
