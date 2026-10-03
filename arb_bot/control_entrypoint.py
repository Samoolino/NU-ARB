from __future__ import annotations

import os
import pwd
import pathlib
import sys


def main() -> None:
    data_dir = pathlib.Path("/data")
    data_dir.mkdir(parents=True, exist_ok=True)
    db_path = pathlib.Path(os.getenv("ARBX_APP_DB", str(data_dir / "arbx_app.sqlite3")))
    db_path.parent.mkdir(parents=True, exist_ok=True)
    if os.geteuid() == 0:
        user = pwd.getpwnam("arbx")
        os.chown(data_dir, user.pw_uid, user.pw_gid)
        os.chown(db_path.parent, user.pw_uid, user.pw_gid)
        os.setgid(user.pw_gid)
        os.setuid(user.pw_uid)
    port = os.getenv("PORT", "8000")
    os.execvp("uvicorn", ["uvicorn", "arbx.web_api:app", "--app-dir", "/app/arb_bot",
                          "--host", "0.0.0.0", "--port", port, "--proxy-headers"])


if __name__ == "__main__":
    main()
