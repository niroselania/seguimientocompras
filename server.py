import json
import os
import sqlite3
from datetime import date, datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

APP_DIR = Path(__file__).resolve().parent
DATA_DIR = Path(os.environ.get("DATA_DIR", APP_DIR / "data"))
DB_PATH = DATA_DIR / "seguimiento_descuento.db"
SEED_PATH = APP_DIR / "seed_data.json"
LIMIT_AMOUNT = 1_400_000
ANCHOR_DATE = date(2026, 5, 1)
PERIOD_MONTHS = 6


def parse_purchase_date(value):
    if not value:
        raise ValueError("La fecha de compra es obligatoria.")
    value = str(value).strip()
    for fmt in ("%Y-%m-%d", "%d/%m/%Y"):
        try:
            return datetime.strptime(value, fmt).date()
        except ValueError:
            pass
    raise ValueError("La fecha debe tener formato AAAA-MM-DD o DD/MM/AAAA.")


def period_for(value):
    purchase_date = parse_purchase_date(value)
    months = (purchase_date.year - ANCHOR_DATE.year) * 12 + purchase_date.month - ANCHOR_DATE.month
    block = months // PERIOD_MONTHS
    start_month_index = ANCHOR_DATE.month - 1 + block * PERIOD_MONTHS
    start_year = ANCHOR_DATE.year + start_month_index // 12
    start_month = start_month_index % 12 + 1
    start = date(start_year, start_month, 1)

    end_month_index = start.month - 1 + PERIOD_MONTHS
    end_year = start.year + end_month_index // 12
    end_month = end_month_index % 12 + 1
    end = date(end_year, end_month, 1)
    end = date.fromordinal(end.toordinal() - 1)
    return start.isoformat(), end.isoformat()


def clean_amount(value):
    if value is None or value == "":
        raise ValueError("El monto es obligatorio.")
    try:
        amount = float(str(value).replace(".", "").replace(",", "."))
    except ValueError as exc:
        raise ValueError("El monto debe ser numerico.") from exc
    if amount <= 0:
        raise ValueError("El monto debe ser mayor a cero.")
    return round(amount, 2)


def db():
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row
    return con


def init_db():
    with db() as con:
        con.execute(
            """
            CREATE TABLE IF NOT EXISTS purchases (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                fiscal_year TEXT NOT NULL,
                purchase_date TEXT NOT NULL,
                full_name TEXT NOT NULL,
                dni TEXT NOT NULL,
                email TEXT NOT NULL,
                order_number TEXT NOT NULL,
                amount REAL NOT NULL,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        count = con.execute("SELECT COUNT(*) FROM purchases").fetchone()[0]
        if count == 0 and SEED_PATH.exists():
            rows = json.loads(SEED_PATH.read_text(encoding="utf-8"))
            for row in rows:
                insert_purchase(con, row, commit=False)
        con.commit()


def insert_purchase(con, payload, commit=True):
    row = normalize_payload(payload)
    cur = con.execute(
        """
        INSERT INTO purchases
            (fiscal_year, purchase_date, full_name, dni, email, order_number, amount)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (
            row["fiscal_year"],
            row["purchase_date"],
            row["full_name"],
            row["dni"],
            row["email"],
            row["order_number"],
            row["amount"],
        ),
    )
    if commit:
        con.commit()
    return cur.lastrowid


def normalize_payload(payload):
    purchase_date = parse_purchase_date(payload.get("purchase_date")).isoformat()
    required = {
        "fiscal_year": "Año fiscal",
        "full_name": "Nombre y apellido",
        "dni": "DNI",
        "email": "Mail asociado",
        "order_number": "Numero de orden",
    }
    row = {}
    for key, label in required.items():
        value = str(payload.get(key, "")).strip()
        if not value:
            raise ValueError(f"{label} es obligatorio.")
        row[key] = value
    row["purchase_date"] = purchase_date
    row["amount"] = clean_amount(payload.get("amount"))
    return row


def row_to_dict(row):
    item = dict(row)
    start, end = period_for(item["purchase_date"])
    item["period_start"] = start
    item["period_end"] = end
    item["amount"] = round(float(item["amount"]), 2)
    return item


def purchase_query(search=""):
    args = []
    where = ""
    if search:
        where = """
            WHERE full_name LIKE ?
               OR dni LIKE ?
               OR email LIKE ?
               OR order_number LIKE ?
        """
        term = f"%{search}%"
        args = [term, term, term, term]
    with db() as con:
        rows = con.execute(
            f"""
            SELECT * FROM purchases
            {where}
            ORDER BY purchase_date DESC, id DESC
            """,
            args,
        ).fetchall()
    return [row_to_dict(row) for row in rows]


def spending_for(dni, purchase_date, exclude_id=None):
    start, end = period_for(purchase_date)
    args = [dni, start, end]
    extra = ""
    if exclude_id:
        extra = "AND id <> ?"
        args.append(exclude_id)
    with db() as con:
        total = con.execute(
            f"""
            SELECT COALESCE(SUM(amount), 0) AS total
            FROM purchases
            WHERE dni = ?
              AND purchase_date BETWEEN ? AND ?
              {extra}
            """,
            args,
        ).fetchone()["total"]
    return round(float(total), 2), start, end


def enriched_purchase(row):
    previous_total, start, end = spending_for(row["dni"], row["purchase_date"], row.get("id"))
    projected_total = round(previous_total + float(row["amount"]), 2)
    row["period_start"] = start
    row["period_end"] = end
    row["previous_period_total"] = previous_total
    row["projected_period_total"] = projected_total
    row["available_after"] = round(LIMIT_AMOUNT - projected_total, 2)
    row["exceeds_limit"] = projected_total > LIMIT_AMOUNT
    return row


def summaries():
    grouped = {}
    for row in purchase_query():
        key = (row["dni"], row["period_start"], row["period_end"])
        if key not in grouped:
            grouped[key] = {
                "dni": row["dni"],
                "full_name": row["full_name"],
                "email": row["email"],
                "period_start": row["period_start"],
                "period_end": row["period_end"],
                "total": 0,
                "orders": 0,
            }
        grouped[key]["total"] += row["amount"]
        grouped[key]["orders"] += 1
    result = []
    for item in grouped.values():
        item["total"] = round(item["total"], 2)
        item["available"] = round(LIMIT_AMOUNT - item["total"], 2)
        item["exceeds_limit"] = item["total"] > LIMIT_AMOUNT
        result.append(item)
    return sorted(result, key=lambda x: (x["exceeds_limit"], x["total"]), reverse=True)


class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        print("%s - %s" % (self.address_string(), fmt % args))

    def send_json(self, data, status=200):
        body = json.dumps(data, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def read_json(self):
        length = int(self.headers.get("Content-Length", 0))
        if not length:
            return {}
        return json.loads(self.rfile.read(length).decode("utf-8"))

    def do_GET(self):
        parsed = urlparse(self.path)
        if parsed.path == "/api/config":
            self.send_json(
                {
                    "limit_amount": LIMIT_AMOUNT,
                    "anchor_date": ANCHOR_DATE.isoformat(),
                    "period_months": PERIOD_MONTHS,
                }
            )
            return
        if parsed.path == "/api/purchases":
            search = parse_qs(parsed.query).get("search", [""])[0].strip()
            self.send_json(purchase_query(search))
            return
        if parsed.path == "/api/summaries":
            self.send_json(summaries())
            return
        if parsed.path in ("/", "/index.html"):
            self.serve_file(APP_DIR / "index.html", "text/html; charset=utf-8")
            return
        if parsed.path == "/styles.css":
            self.serve_file(APP_DIR / "styles.css", "text/css; charset=utf-8")
            return
        if parsed.path == "/app.js":
            self.serve_file(APP_DIR / "app.js", "application/javascript; charset=utf-8")
            return
        self.send_json({"error": "No encontrado."}, 404)

    def do_POST(self):
        if self.path != "/api/purchases":
            self.send_json({"error": "No encontrado."}, 404)
            return
        try:
            payload = self.read_json()
            row = normalize_payload(payload)
            check = enriched_purchase(row.copy())
            with db() as con:
                new_id = insert_purchase(con, row)
                saved = row_to_dict(con.execute("SELECT * FROM purchases WHERE id = ?", [new_id]).fetchone())
            self.send_json({"purchase": saved, "limit_check": check}, 201)
        except ValueError as exc:
            self.send_json({"error": str(exc)}, 400)

    def do_PUT(self):
        if not self.path.startswith("/api/purchases/"):
            self.send_json({"error": "No encontrado."}, 404)
            return
        try:
            purchase_id = int(self.path.rsplit("/", 1)[1])
            row = normalize_payload(self.read_json())
            check = enriched_purchase({**row, "id": purchase_id})
            with db() as con:
                con.execute(
                    """
                    UPDATE purchases
                    SET fiscal_year = ?, purchase_date = ?, full_name = ?, dni = ?,
                        email = ?, order_number = ?, amount = ?, updated_at = CURRENT_TIMESTAMP
                    WHERE id = ?
                    """,
                    (
                        row["fiscal_year"],
                        row["purchase_date"],
                        row["full_name"],
                        row["dni"],
                        row["email"],
                        row["order_number"],
                        row["amount"],
                        purchase_id,
                    ),
                )
                con.commit()
                saved = con.execute("SELECT * FROM purchases WHERE id = ?", [purchase_id]).fetchone()
            if not saved:
                self.send_json({"error": "Registro no encontrado."}, 404)
                return
            self.send_json({"purchase": row_to_dict(saved), "limit_check": check})
        except ValueError as exc:
            self.send_json({"error": str(exc)}, 400)

    def do_DELETE(self):
        if not self.path.startswith("/api/purchases/"):
            self.send_json({"error": "No encontrado."}, 404)
            return
        try:
            purchase_id = int(self.path.rsplit("/", 1)[1])
        except ValueError:
            self.send_json({"error": "ID invalido."}, 400)
            return
        with db() as con:
            cur = con.execute("DELETE FROM purchases WHERE id = ?", [purchase_id])
            con.commit()
        self.send_json({"deleted": cur.rowcount > 0})

    def serve_file(self, path, content_type):
        body = path.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


if __name__ == "__main__":
    init_db()
    port = int(os.environ.get("PORT", "7070"))
    server = ThreadingHTTPServer(("0.0.0.0", port), Handler)
    print(f"Seguimiento descuento escuchando en http://0.0.0.0:{port}")
    server.serve_forever()
