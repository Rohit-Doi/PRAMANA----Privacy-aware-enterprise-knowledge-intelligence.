"""Create .env with generated local secrets.

Safe to re-run: existing values are kept, only missing ones are filled in.
If the v1 project's .env is present next door, GOOGLE_API_KEY is copied from it
so the key never has to be retyped. Values are never printed.
"""

from __future__ import annotations

import base64
import secrets
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ENV_PATH = ROOT / ".env"
TEMPLATE_PATH = ROOT / ".env.example"
LEGACY_ENV_PATH = ROOT.parent / "pramana" / ".env"

COPY_FROM_LEGACY = ("GOOGLE_API_KEY",)


def _parse(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    if not path.exists():
        return values
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, _, value = stripped.partition("=")
        values[key.strip()] = value.strip()
    return values


def _generators() -> dict[str, str]:
    return {
        "POSTGRES_PASSWORD": secrets.token_urlsafe(24),
        "NEO4J_PASSWORD": secrets.token_urlsafe(24),
        "JWT_SECRET": secrets.token_urlsafe(48),
        # 32 random bytes, urlsafe base64: the format Fernet expects.
        "ENCRYPTION_KEY": base64.urlsafe_b64encode(secrets.token_bytes(32)).decode(),
        "DEMO_PASSWORD": "Demo-" + secrets.token_urlsafe(9) + "-7",
    }


def main() -> int:
    template = _parse(TEMPLATE_PATH)
    current = _parse(ENV_PATH)
    legacy = _parse(LEGACY_ENV_PATH)
    generated = _generators()

    actions: list[str] = []
    final: dict[str, str] = {}
    for key, default in template.items():
        if current.get(key):
            final[key] = current[key]
        elif key in generated:
            final[key] = generated[key]
            actions.append(f"generated {key}")
        elif key in COPY_FROM_LEGACY and legacy.get(key):
            final[key] = legacy[key]
            actions.append(f"copied {key} from the v1 project")
        else:
            final[key] = default
    # Keep anything the user added that the template does not know about.
    for key, value in current.items():
        final.setdefault(key, value)

    lines = []
    for line in TEMPLATE_PATH.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if stripped and not stripped.startswith("#") and "=" in stripped:
            key = stripped.partition("=")[0].strip()
            lines.append(f"{key}={final.pop(key)}")
        else:
            lines.append(line)
    if final:
        lines.append("")
        lines.append("# ── Additional values ─────────────────────────────────────")
        lines.extend(f"{key}={value}" for key, value in final.items())
    ENV_PATH.write_text("\n".join(lines) + "\n", encoding="utf-8")

    print(f".env written to {ENV_PATH}")
    for action in actions:
        print(f"  - {action}")
    if not actions:
        print("  - nothing to change")
    if not _parse(ENV_PATH).get("GOOGLE_API_KEY"):
        print("  ! GOOGLE_API_KEY is empty: add it to .env before running the app")
    return 0


if __name__ == "__main__":
    sys.exit(main())
