"""Fifteen synthetic assets; no accounts or checkout history are created."""
from psycopg import sql

ASSETS = [
    ("GEAR-001", "Demo camera A", "good"),
    ("GEAR-002", "Demo camera B", "new"),
    ("GEAR-003", "Wide lens", "good"),
    ("GEAR-004", "Portrait lens", "good"),
    ("GEAR-005", "Travel tripod", "worn"),
    ("GEAR-006", "Studio tripod", "good"),
    ("GEAR-007", "Audio recorder", "new"),
    ("GEAR-008", "Shotgun microphone", "good"),
    ("GEAR-009", "Lapel microphone kit", "good"),
    ("GEAR-010", "LED light A", "new"),
    ("GEAR-011", "LED light B", "good"),
    ("GEAR-012", "Reflector kit", "worn"),
    ("GEAR-013", "Portable projector", "good"),
    ("GEAR-014", "Presentation remote", "good"),
    ("GEAR-015", "Demo tablet", "broken"),
]


def seed_assets(conn, dynamic=True):
    if not dynamic:
        raise ValueError("Example seeds only support spec-generated tables")
    table = sql.Identifier("x_asset")
    if conn.execute(sql.SQL("select 1 from ") + table + sql.SQL(" limit 1")).fetchone():
        return
    statement = (sql.SQL("insert into ") + table + sql.SQL(" (")
                 + sql.SQL(", ").join(map(sql.Identifier, ("tag", "name", "condition", "sort")))
                 + sql.SQL(") values (%s,%s,%s,%s)"))
    with conn.cursor() as cursor:
        cursor.executemany(statement, [(tag, name, condition, index)
                                       for index, (tag, name, condition) in enumerate(ASSETS)])
