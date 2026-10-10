"""Настройки грузятся из .env, скопированного с .env.example (значения пустые)."""

import re
import sys
import tempfile
from pathlib import Path

from app.config import Settings

OK, FAIL = [], []


def check(name, cond, extra=""):
    (OK if cond else FAIL).append(name)
    print(f"  [{'OK ' if cond else 'ПРОВАЛ'}] {name}" + (f" — {extra}" if extra else ""))


example = Path(".env.example").read_text(encoding="utf-8")
names = [m.group(1) for m in re.finditer(r"^([A-Z][A-Z0-9_]*)=", example, re.M)]

print("\n1. .env.example, скопированный как есть")
check("в .env.example есть переменные", len(names) > 10, f"{len(names)} шт.")
check("все значения в .env.example пустые",
      all(re.search(rf"^{n}=\s*$", example, re.M) for n in names))

with tempfile.TemporaryDirectory() as tmp:
    env = Path(tmp) / ".env"
    env.write_text(example, encoding="utf-8")
    try:
        s = Settings(_env_file=str(env))
        check("Settings создаётся из пустого .env", True)
        check("пустое число -> умолчание", s.max_upload_mb == 20, str(s.max_upload_mb))
        check("пустой bool -> умолчание", s.debug is False)
        check("пустая строка -> умолчание", s.model_provider == "cloud", s.model_provider)
    except Exception as exc:  # noqa: BLE001
        check("Settings создаётся из пустого .env", False, str(exc)[:120])

print("\n2. Заданное значение не теряется")
with tempfile.TemporaryDirectory() as tmp:
    env = Path(tmp) / ".env"
    env.write_text("MAX_UPLOAD_MB=7\nMODEL_PROVIDER=local\nDEBUG=\n", encoding="utf-8")
    s = Settings(_env_file=str(env))
    check("значение из .env применяется", s.max_upload_mb == 7 and s.model_provider == "local")

print(f"\n==== успешно: {len(OK)}, провалов: {len(FAIL)}")
sys.exit(1 if FAIL else 0)
