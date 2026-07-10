"""
IRAI — Seed dos targets BR (WIN$N/WDO$N) em asset_models.

calibrate_universal.py só faz UPDATE em asset_models (nunca INSERT) — então,
num banco recém-criado, os targets BR precisam de uma linha base antes da
primeira calibração. factors/factor_labels ficam vazios aqui;
calibrate_universal.py --force os preenche.

session_start_h/session_end_h em UTC: 09h-18h BRT = 12h-21h UTC.

Uso: python scripts/seed_br_targets.py
"""
import sqlite3

TARGETS = [
    {
        "target": "WIN$N", "slug": "win", "display_name": "Mini Índice",
        "icon": "🇧🇷", "session_start_h": 12, "session_end_h": 21,
    },
    {
        "target": "WDO$N", "slug": "wdo", "display_name": "Mini Dólar",
        "icon": "💵", "session_start_h": 12, "session_end_h": 21,
    },
]


def main():
    conn = sqlite3.connect("data/irai.db")
    for t in TARGETS:
        conn.execute(
            """
            INSERT INTO asset_models
                (target, slug, display_name, icon, factors, factor_labels,
                 session_start_h, session_end_h, data_proxy, active)
            VALUES (?, ?, ?, ?, '[]', '{}', ?, ?, NULL, 1)
            ON CONFLICT(target) DO NOTHING
            """,
            (t["target"], t["slug"], t["display_name"], t["icon"],
             t["session_start_h"], t["session_end_h"]),
        )
        print(f"  seed: {t['target']} (slug={t['slug']})")
    conn.commit()
    conn.close()
    print("OK")


if __name__ == "__main__":
    main()
