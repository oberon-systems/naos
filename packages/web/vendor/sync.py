"""Copy the npm packages of package-lock.json into static/vendor under fixed names."""

import re
from pathlib import Path

HERE = Path(__file__).resolve().parent
VENDOR = HERE.parent / "src" / "naos_web" / "static" / "vendor"
FILES = {
    "htmx.org/dist/htmx.min.js": "htmx.min.js",
    "@xterm/xterm/lib/xterm.js": "xterm.js",
    "@xterm/xterm/css/xterm.css": "xterm.css",
    "@xterm/addon-fit/lib/addon-fit.js": "xterm-addon-fit.js",
}
# static() serves no source maps, so the browser would ask for a missing file.
SOURCE_MAP = re.compile(r"^//# sourceMappingURL=.*$\n?", re.MULTILINE)


def main() -> None:
    for source, name in FILES.items():
        text = (HERE / "node_modules" / source).read_text(encoding="utf-8")
        text = SOURCE_MAP.sub("", text).rstrip("\n") + "\n"
        (VENDOR / name).write_text(text, encoding="utf-8")


if __name__ == "__main__":
    main()
