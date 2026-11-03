import io
import json
import os
import sqlite3
import threading
import time
import unicodedata
import urllib.error
import urllib.request
import zipfile
from datetime import date, datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse
from xml.sax.saxutils import escape

APP_DIR = Path(__file__).resolve().parent
DATA_DIR = Path(os.environ.get("DATA_DIR", APP_DIR / "data"))
DB_PATH = DATA_DIR / "seguimiento_descuento.db"
SEED_PATH = APP_DIR / "seed_data.json"
LIMIT_AMOUNT = 1_400_000
ANCHOR_DATE = date(2026, 5, 1)
PERIOD_MONTHS = 6
AMOUNT_FIX_FLAG = "amounts_divided_by_10_20260601"
AMOUNT_FIX_V2_FLAG = "seed_amounts_normalized_20260601_v2"


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
    raw = str(value).strip().replace("$", "").replace(" ", "")
    if "," in raw:
        normalized = raw.replace(".", "").replace(",", ".")
    else:
        parts = raw.split(".")
        if len(parts) > 1 and all(len(part) == 3 for part in parts[1:]):
            normalized = raw.replace(".", "")
        else:
            normalized = raw
    try:
        amount = float(normalized)
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
            CREATE TABLE IF NOT EXISTS app_meta (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            )
            """
        )
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
        fix_amounts_if_needed(con)
        normalize_seed_amounts(con)
        con.commit()


def fix_amounts_if_needed(con):
    done = con.execute(
        "SELECT value FROM app_meta WHERE key = ?", [AMOUNT_FIX_FLAG]
    ).fetchone()
    if done:
        return

    if not SEED_PATH.exists():
        con.execute(
            "INSERT OR REPLACE INTO app_meta (key, value) VALUES (?, ?)",
            [AMOUNT_FIX_FLAG, "no_seed"],
        )
        return

    corrected = 0
    for row in json.loads(SEED_PATH.read_text(encoding="utf-8")):
        expected = round(float(row["amount"]), 2)
        inflated = round(expected * 10, 2)
        cur = con.execute(
            """
            UPDATE purchases
            SET amount = ?
            WHERE dni = ?
              AND order_number = ?
              AND purchase_date = ?
              AND ABS(amount - ?) < 0.01
            """,
            [
                expected,
                row["dni"],
                row["order_number"],
                row["purchase_date"],
                inflated,
            ],
        )
        corrected += cur.rowcount

    con.execute(
        "INSERT OR REPLACE INTO app_meta (key, value) VALUES (?, ?)",
        [AMOUNT_FIX_FLAG, f"corrected:{corrected}"],
    )


def normalize_seed_amounts(con):
    done = con.execute(
        "SELECT value FROM app_meta WHERE key = ?", [AMOUNT_FIX_V2_FLAG]
    ).fetchone()
    if done:
        return

    if not SEED_PATH.exists():
        con.execute(
            "INSERT OR REPLACE INTO app_meta (key, value) VALUES (?, ?)",
            [AMOUNT_FIX_V2_FLAG, "no_seed"],
        )
        return

    corrected = 0
    factors = (10, 100, 1000)
    for row in json.loads(SEED_PATH.read_text(encoding="utf-8")):
        expected = round(float(row["amount"]), 2)
        inflated_values = [round(expected * factor, 2) for factor in factors]
        placeholders = ",".join("?" for _ in inflated_values)
        cur = con.execute(
            f"""
            UPDATE purchases
            SET amount = ?
            WHERE dni = ?
              AND order_number = ?
              AND purchase_date = ?
              AND ROUND(amount, 2) IN ({placeholders})
            """,
            [
                expected,
                row["dni"],
                row["order_number"],
                row["purchase_date"],
                *inflated_values,
            ],
        )
        corrected += cur.rowcount

    con.execute(
        "INSERT OR REPLACE INTO app_meta (key, value) VALUES (?, ?)",
        [AMOUNT_FIX_V2_FLAG, f"corrected:{corrected}"],
    )


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


def normalize_payload(payload, allow_purchase_date=False):
    purchase_date = date.today().isoformat()
    if allow_purchase_date:
        purchase_date = parse_purchase_date(payload.get("purchase_date", "")).isoformat()
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


def summaries(current_only=True):
    grouped = {}
    rows = purchase_query()
    current_start, current_end = period_for(date.today().isoformat())
    if current_only:
        for row in rows:
            key = (row["dni"], current_start, current_end)
            if key not in grouped:
                grouped[key] = {
                    "dni": row["dni"],
                    "full_name": row["full_name"],
                    "email": row["email"],
                    "period_start": current_start,
                    "period_end": current_end,
                    "total": 0,
                    "orders": 0,
                }
        rows = [
            row for row in rows
            if current_start <= row["purchase_date"] <= current_end
        ]
    for row in rows:
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


def normalized_name(value):
    folded = unicodedata.normalize("NFKD", value.casefold())
    return "".join(char for char in folded if not unicodedata.combining(char)).strip()


def search_person_period_totals(query):
    search = normalized_name(query)
    if not search:
        return [], []
    with db() as con:
        records = con.execute(
            "SELECT dni, full_name, purchase_date, amount FROM purchases ORDER BY purchase_date DESC, id DESC"
        ).fetchall()
    matching = [row for row in records if search in normalized_name(row["full_name"])]
    people = {}
    for row in matching:
        people.setdefault(row["dni"], row["full_name"])
    if not people:
        return [], []
    exact = [dni for dni, name in people.items() if normalized_name(name) == search]
    if len(exact) == 1:
        selected = exact
        people = {dni: people[dni] for dni in selected}
        matching = [row for row in matching if row["dni"] in people]
    if len(people) != 1:
        return [(name, dni) for dni, name in people.items()], []

    dni, name = next(iter(people.items()))
    totals = {}
    for row in matching:
        start, _ = period_for(row["purchase_date"])
        totals[start] = totals.get(start, 0) + float(row["amount"])

    current_start, _ = period_for(date.today().isoformat())
    first_start = min(totals)
    final_start = max(current_start, max(totals))
    periods = []
    cursor = parse_purchase_date(first_start)
    final = parse_purchase_date(final_start)
    while cursor <= final:
        start, end = period_for(cursor.isoformat())
        periods.append((start, end, round(totals.get(start, 0), 2)))
        cursor = date(cursor.year + (cursor.month + PERIOD_MONTHS - 1) // 12,
                      (cursor.month + PERIOD_MONTHS - 1) % 12 + 1, 1)
    return [], {"name": name, "dni": dni, "periods": periods}


def all_people_period_totals():
    with db() as con:
        records = con.execute(
            "SELECT dni, full_name, purchase_date, amount FROM purchases ORDER BY full_name, purchase_date, id"
        ).fetchall()
    people = {}
    for row in records:
        person = people.setdefault(row["dni"], {"name": row["full_name"], "period_totals": {}})
        start, _ = period_for(row["purchase_date"])
        person["period_totals"][start] = person["period_totals"].get(start, 0) + float(row["amount"])

    current_start, _ = period_for(date.today().isoformat())
    result = []
    for dni, person in people.items():
        totals = person["period_totals"]
        first = parse_purchase_date(min(totals))
        last = max(parse_purchase_date(current_start), parse_purchase_date(max(totals)))
        periods = []
        cursor = first
        while cursor <= last:
            start, end = period_for(cursor.isoformat())
            periods.append((start, end, round(totals.get(start, 0), 2)))
            cursor = date(cursor.year + (cursor.month + PERIOD_MONTHS - 1) // 12,
                          (cursor.month + PERIOD_MONTHS - 1) % 12 + 1, 1)
        result.append({"name": person["name"], "dni": dni, "periods": periods})
    return sorted(result, key=lambda person: normalized_name(person["name"]))


def format_telegram_totals(result):
    lines = [f"Compras de {result['name']} (DNI {result['dni']}):"]
    for start, end, total in result["periods"]:
        lines.append(f"{format_display_date(start)} al {format_display_date(end)}: ${total:,.2f}".replace(",", "X").replace(".", ",").replace("X", "."))
    return "\n".join(lines)


def format_telegram_all_totals(people):
    if not people:
        return "Todavía no hay compras registradas."
    blocks = ["Totales de todas las personas con compras:"]
    for person in people:
        block = [f"{person['name']} · DNI {person['dni']}"]
        block.extend(
            f"  {format_display_date(start)} al {format_display_date(end)}: ${total:,.2f}"
            .replace(",", "X").replace(".", ",").replace("X", ".")
            for start, end, total in person["periods"]
        )
        blocks.append("\n".join(block))
    return "\n\n".join(blocks)


def send_telegram_long_message(token, chat_id, text):
    max_length = 3500
    chunks = []
    current = ""
    for block in text.split("\n\n"):
        addition = block if not current else f"\n\n{block}"
        if len(current) + len(addition) <= max_length:
            current += addition
            continue
        if current:
            chunks.append(current)
        while len(block) > max_length:
            chunks.append(block[:max_length])
            block = block[max_length:]
        current = block
    if current:
        chunks.append(current)
    for chunk in chunks:
        send_telegram_message(token, chat_id, chunk)


def telegram_request(token, method, payload):
    url = f"https://api.telegram.org/bot{token}/{method}"
    body = json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(url, data=body, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(request, timeout=35) as response:
        result = json.loads(response.read().decode("utf-8"))
    if not result.get("ok"):
        raise RuntimeError(result.get("description", "Error de Telegram"))
    return result["result"]


def send_telegram_message(token, chat_id, text):
    telegram_request(token, "sendMessage", {"chat_id": chat_id, "text": text})


def handle_telegram_message(token, allowed_chats, message):
    chat_id = str(message.get("chat", {}).get("id", ""))
    if chat_id not in allowed_chats:
        return
    text = (message.get("text") or "").strip()
    if text.startswith("/"):
        command, _, argument = text.partition(" ")
        command = command.split("@", 1)[0].lower()
        if command in ("/start", "/help", "/ayuda"):
            send_telegram_message(token, chat_id, "Escribí un nombre para ver sus totales por semestre, o enviá «todos» para listar a todas las personas con compras.")
            return
        if command in ("/todos", "/todas"):
            send_telegram_long_message(token, chat_id, format_telegram_all_totals(all_people_period_totals()))
            return
        query = argument.strip()
    else:
        query = text
    if not query:
        send_telegram_message(token, chat_id, "Escribí un nombre para buscar.")
        return

    if normalized_name(query) in ("todos", "todo", "todas", "todos los empleados", "todas las personas"):
        send_telegram_long_message(token, chat_id, format_telegram_all_totals(all_people_period_totals()))
        return

    matches, result = search_person_period_totals(query)
    if result:
        send_telegram_message(token, chat_id, format_telegram_totals(result))
    elif matches:
        options = "\n".join(f"• {name} · DNI {dni}" for name, dni in matches[:15])
        suffix = "\nHay más coincidencias; afiná la búsqueda." if len(matches) > 15 else "\nEscribí más letras del nombre para precisar."
        send_telegram_message(token, chat_id, f"Encontré varias personas:\n{options}{suffix}")
    else:
        send_telegram_message(token, chat_id, "No encontré compras con ese nombre.")


def telegram_polling_loop(token, allowed_chats):
    offset = None
    while True:
        try:
            payload = {"timeout": 25, "allowed_updates": ["message"]}
            if offset is not None:
                payload["offset"] = offset
            updates = telegram_request(token, "getUpdates", payload)
            for update in updates:
                offset = update["update_id"] + 1
                message = update.get("message")
                if message:
                    try:
                        handle_telegram_message(token, allowed_chats, message)
                    except Exception as exc:
                        print(f"Error procesando mensaje de Telegram: {exc}")
        except Exception as exc:
            print(f"Error consultando Telegram: {exc}")
            time.sleep(5)


def start_telegram_bot():
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
    allowed_chats = {
        item.strip()
        for item in os.environ.get("TELEGRAM_ALLOWED_CHAT_IDS", "").split(",")
        if item.strip()
    }
    if not token:
        print("Bot de Telegram desactivado: falta TELEGRAM_BOT_TOKEN.")
        return
    if not allowed_chats:
        print("Bot de Telegram desactivado: falta TELEGRAM_ALLOWED_CHAT_IDS.")
        return
    threading.Thread(target=telegram_polling_loop, args=(token, allowed_chats), daemon=True).start()
    print("Bot de búsqueda de Telegram iniciado.")


def excel_col(index):
    name = ""
    while index:
        index, remainder = divmod(index - 1, 26)
        name = chr(65 + remainder) + name
    return name


def excel_date(value):
    parsed = parse_purchase_date(value)
    return (parsed - date(1899, 12, 30)).days


def xlsx_cell(row_idx, col_idx, value, style=None):
    ref = f"{excel_col(col_idx)}{row_idx}"
    style_attr = f' s="{style}"' if style is not None else ""
    if isinstance(value, (int, float)):
        return f'<c r="{ref}"{style_attr}><v>{value}</v></c>'
    text = escape("" if value is None else str(value))
    return f'<c r="{ref}" t="inlineStr"{style_attr}><is><t>{text}</t></is></c>'


def build_backup_xlsx(period_start=None, period_end=None, dni=None):
    headers = [
        "Año Fiscal",
        "Fecha de Compra (DD/MM/AAAA)",
        "Nombre y Apellido",
        "DNI",
        "Mail asociado a Patagonia",
        "Numero de Orden",
        "Monto de la compra (con el descuento del 70% aplicado)",
        "Periodo",
        "Total persona en periodo",
        "Disponible",
        "Estado",
    ]
    rows = purchase_query()
    if period_start or period_end:
        if not period_start or not period_end:
            raise ValueError("Debe indicar el inicio y el fin del período.")
        start = parse_purchase_date(period_start)
        expected_start, expected_end = period_for(start.isoformat())
        if start.isoformat() != expected_start or period_end != expected_end:
            raise ValueError("El período indicado no corresponde a un semestre válido.")
        rows = [row for row in rows if period_start <= row["purchase_date"] <= period_end]
    if dni:
        rows = [row for row in rows if row["dni"] == dni]
    rows.sort(key=lambda row: (row["full_name"].casefold(), row["purchase_date"], row["id"]))
    summary_lookup = {
        (item["dni"], item["period_start"], item["period_end"]): item
        for item in summaries(current_only=False)
    }

    sheet_rows = []
    sheet_rows.append(
        '<row r="1">'
        + "".join(xlsx_cell(1, idx, header, style=1) for idx, header in enumerate(headers, 1))
        + "</row>"
    )
    for row_idx, row in enumerate(rows, 2):
        summary = summary_lookup[(row["dni"], row["period_start"], row["period_end"])]
        values = [
            row["fiscal_year"],
            excel_date(row["purchase_date"]),
            row["full_name"],
            row["dni"],
            row["email"],
            row["order_number"],
            row["amount"],
            f'{format_display_date(row["period_start"])} al {format_display_date(row["period_end"])}',
            summary["total"],
            summary["available"],
            "Excedido" if summary["exceeds_limit"] else "Dentro del cupo",
        ]
        cells = []
        for col_idx, value in enumerate(values, 1):
            style = None
            if col_idx == 2:
                style = 2
            elif col_idx in (7, 9, 10):
                style = 3
            cells.append(xlsx_cell(row_idx, col_idx, value, style=style))
        sheet_rows.append(f'<row r="{row_idx}">' + "".join(cells) + "</row>")

    last_row = max(1, len(rows) + 1)
    worksheet = f"""<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"
 xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">
  <dimension ref="A1:K{last_row}"/>
  <sheetViews><sheetView workbookViewId="0"><pane ySplit="1" topLeftCell="A2" activePane="bottomLeft" state="frozen"/><selection pane="bottomLeft" activeCell="A2" sqref="A2"/></sheetView></sheetViews>
  <sheetFormatPr defaultRowHeight="15"/>
  <cols>
    <col min="1" max="1" width="12" customWidth="1"/>
    <col min="2" max="2" width="18" customWidth="1"/>
    <col min="3" max="3" width="26" customWidth="1"/>
    <col min="4" max="4" width="14" customWidth="1"/>
    <col min="5" max="5" width="34" customWidth="1"/>
    <col min="6" max="6" width="20" customWidth="1"/>
    <col min="7" max="7" width="24" customWidth="1"/>
    <col min="8" max="8" width="24" customWidth="1"/>
    <col min="9" max="10" width="22" customWidth="1"/>
    <col min="11" max="11" width="18" customWidth="1"/>
  </cols>
  <sheetData>{''.join(sheet_rows)}</sheetData>
  <autoFilter ref="A1:K{last_row}"/>
</worksheet>"""
    styles = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<styleSheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">
  <numFmts count="2">
    <numFmt numFmtId="164" formatCode="dd/mm/yyyy"/>
    <numFmt numFmtId="165" formatCode="$ #,##0"/>
  </numFmts>
  <fonts count="2"><font><sz val="11"/><name val="Calibri"/></font><font><b/><sz val="11"/><name val="Calibri"/><color rgb="FFFFFFFF"/></font></fonts>
  <fills count="2"><fill><patternFill patternType="none"/></fill><fill><patternFill patternType="solid"><fgColor rgb="FF1E6B52"/></patternFill></fill></fills>
  <borders count="1"><border><left/><right/><top/><bottom/></border></borders>
  <cellStyleXfs count="1"><xf numFmtId="0" fontId="0" fillId="0" borderId="0"/></cellStyleXfs>
  <cellXfs count="4">
    <xf numFmtId="0" fontId="0" fillId="0" borderId="0" xfId="0"/>
    <xf numFmtId="0" fontId="1" fillId="1" borderId="0" xfId="0" applyFont="1" applyFill="1"/>
    <xf numFmtId="164" fontId="0" fillId="0" borderId="0" xfId="0" applyNumberFormat="1"/>
    <xf numFmtId="165" fontId="0" fillId="0" borderId="0" xfId="0" applyNumberFormat="1"/>
  </cellXfs>
  <cellStyles count="1"><cellStyle name="Normal" xfId="0" builtinId="0"/></cellStyles>
  <dxfs count="0"/>
  <tableStyles count="0" defaultTableStyle="TableStyleMedium2" defaultPivotStyle="PivotStyleLight16"/>
</styleSheet>"""
    workbook = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"
 xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">
  <bookViews><workbookView/></bookViews>
  <sheets><sheet name="Descuento Total" sheetId="1" r:id="rId1"/></sheets>
</workbook>"""
    workbook_rels = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
  <Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet1.xml"/>
  <Relationship Id="rId2" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" Target="styles.xml"/>
</Relationships>"""
    root_rels = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
  <Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/>
</Relationships>"""
    content_types = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
  <Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>
  <Default Extension="xml" ContentType="application/xml"/>
  <Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>
  <Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>
  <Override PartName="/xl/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.styles+xml"/>
</Types>"""
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("[Content_Types].xml", content_types)
        zf.writestr("_rels/.rels", root_rels)
        zf.writestr("xl/workbook.xml", workbook)
        zf.writestr("xl/_rels/workbook.xml.rels", workbook_rels)
        zf.writestr("xl/styles.xml", styles)
        zf.writestr("xl/worksheets/sheet1.xml", worksheet)
    return buffer.getvalue()


def format_display_date(value):
    parsed = parse_purchase_date(value)
    return parsed.strftime("%d/%m/%Y")


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
            period_start, period_end = period_for(date.today().isoformat())
            self.send_json(
                {
                    "limit_amount": LIMIT_AMOUNT,
                    "anchor_date": ANCHOR_DATE.isoformat(),
                    "period_months": PERIOD_MONTHS,
                    "current_period_start": period_start,
                    "current_period_end": period_end,
                    "employees": [
                        {"dni": row["dni"], "full_name": row["full_name"]}
                        for row in {item["dni"]: item for item in reversed(purchase_query())}.values()
                    ],
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
        if parsed.path == "/api/export.xlsx":
            query = parse_qs(parsed.query)
            period = query.get("period", ["all"])[0]
            dni = query.get("dni", [""])[0]
            if period == "all":
                body = build_backup_xlsx(dni=dni or None)
                suffix = f"-{dni}" if dni else "-todos"
                self.send_xlsx(body, f"seguimiento-compras{suffix}-{date.today().isoformat()}.xlsx")
                return
            try:
                period_start, period_end = period.split("|", 1)
                body = build_backup_xlsx(period_start, period_end, dni or None)
                person_suffix = f"-{dni}" if dni else "-todos"
                filename = f"seguimiento-compras{person_suffix}-{period_start}-al-{period_end}.xlsx"
                self.send_xlsx(body, filename)
            except (ValueError, TypeError):
                self.send_json({"error": "Período de exportación inválido."}, 400)
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
        static_name = unquote(parsed.path).lstrip("/")
        static_types = {
            ".png": "image/png",
            ".jpg": "image/jpeg",
            ".jpeg": "image/jpeg",
            ".webp": "image/webp",
            ".svg": "image/svg+xml",
            ".gif": "image/gif",
        }
        static_path = APP_DIR / static_name
        if static_path.is_file() and static_path.parent == APP_DIR:
            content_type = static_types.get(static_path.suffix.lower())
            if content_type:
                self.serve_file(static_path, content_type)
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
            row = normalize_payload(self.read_json(), allow_purchase_date=True)
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

    def send_xlsx(self, body, filename):
        self.send_response(200)
        self.send_header(
            "Content-Type",
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )
        self.send_header("Content-Disposition", f'attachment; filename="{filename}"')
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


if __name__ == "__main__":
    init_db()
    start_telegram_bot()
    port = int(os.environ.get("PORT", "7070"))
    server = ThreadingHTTPServer(("0.0.0.0", port), Handler)
    print(f"Seguimiento descuento escuchando en http://0.0.0.0:{port}")
    server.serve_forever()
