from __future__ import annotations

import uvicorn


def main() -> None:
    uvicorn.run(
        "oohstory_admin.app:create_app",
        host="127.0.0.1",
        port=8092,
        factory=True,
        proxy_headers=True,
        forwarded_allow_ips="127.0.0.1",
    )


if __name__ == "__main__":
    main()
