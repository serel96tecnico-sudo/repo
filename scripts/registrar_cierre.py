"""
Registra un trade cerrado en contex/trades_historico.json.

Uso:
    # Con imagen (Claude lo llama internamente tras analizar la captura)
    python scripts/registrar_cierre.py --imagen ruta/captura.png

    # Manual directo
    python scripts/registrar_cierre.py --ticker AMD --cierre 115.50 --broker broker_1
"""
import argparse
import base64
import json
import os
import sys
from datetime import date
from pathlib import Path

CONTEXT_DIR  = Path("contex")
HIST_PATH    = CONTEXT_DIR / "trades_historico.json"


def append_trade(trade: dict) -> None:
    if HIST_PATH.exists():
        data = json.loads(HIST_PATH.read_text(encoding="utf-8"))
    else:
        data = {"description": "Historico acumulado de trades cerrados",
                "created": date.today().isoformat(), "trades": []}

    data["trades"].append(trade)
    tmp = HIST_PATH.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, HIST_PATH)
    print(f"Trade registrado. Total historico: {len(data['trades'])} trades.")


def extract_from_image(image_path: str) -> dict:
    """Usa Claude Vision para extraer datos del trade desde una captura de broker."""
    import anthropic
    img_bytes = Path(image_path).read_bytes()
    suffix    = Path(image_path).suffix.lower()
    media_map = {".jpg": "image/jpeg", ".jpeg": "image/jpeg",
                 ".png": "image/png",  ".webp": "image/webp"}
    media_type = media_map.get(suffix, "image/png")

    client = anthropic.Anthropic()
    prompt = (
        "Esta es una captura de pantalla de una operacion cerrada en un broker de trading. "
        "Extrae los datos en JSON con exactamente estos campos:\n"
        '{\n'
        '  "ticker":       "XXXX",\n'
        '  "precio_cierre": 0.00,\n'
        '  "entrada_usd":   0.00,\n'
        '  "cantidad":      0,\n'
        '  "direccion":    "long",\n'
        '  "pl_bruto":      0.00,\n'
        '  "comision":      0.00,\n'
        '  "pl_neto":       0.00,\n'
        '  "broker":       "broker_1",\n'
        '  "fecha_cierre": "YYYY-MM-DD"\n'
        '}\n\n'
        "Reglas:\n"
        "- direccion: 'long' si fue compra/buy, 'short' si fue venta/sell\n"
        "- broker: 'broker_1' si la moneda es EUR o es DeGiro/ING, "
        "'broker_2' si es USD o es Interactive Brokers/Alpaca\n"
        "- Para campos no visibles usa null\n"
        "- fecha_cierre: usa hoy si no se ve\n"
        "Responde SOLO el JSON, sin texto adicional."
    )

    resp = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=512,
        messages=[{
            "role": "user",
            "content": [
                {"type": "image", "source": {
                    "type": "base64",
                    "media_type": media_type,
                    "data": base64.standard_b64encode(img_bytes).decode(),
                }},
                {"type": "text", "text": prompt},
            ],
        }],
    )

    raw = resp.content[0].text.strip()
    if raw.startswith("```"):
        raw = "\n".join(raw.split("\n")[1:])
        if raw.endswith("```"):
            raw = raw[:-3]
    return json.loads(raw.strip())


def build_trade_from_image(image_path: str, nota: str = "") -> dict:
    data = extract_from_image(image_path)

    ticker  = (data.get("ticker") or "").upper()
    cierre  = data.get("precio_cierre") or data.get("cierre_usd")
    entrada = data.get("entrada_usd")
    qty     = data.get("cantidad")
    dire    = data.get("direccion", "long")
    broker  = data.get("broker", "broker_1")
    pl_b    = data.get("pl_bruto")
    pl_n    = data.get("pl_neto") or data.get("net_pl_usd") or data.get("net_pl_eur")
    com     = data.get("comision", 0) or 0
    fecha   = data.get("fecha_cierre") or date.today().isoformat()

    # Calcular P&L si no vino de la imagen
    if pl_n is None and entrada and cierre and qty:
        gross = (float(cierre) - float(entrada)) * float(qty) if dire == "long" \
                else (float(entrada) - float(cierre)) * float(qty)
        pl_n  = round(gross - float(com), 2)
        pl_b  = round(gross, 2)

    trade = {
        "ticker":       ticker,
        "broker":       broker,
        "direccion":    dire,
        "cantidad":     qty,
        "entrada_usd":  entrada,
        "cierre_usd":   float(cierre) if cierre else None,
        "gross_pl":     pl_b,
        "net_pl":       pl_n,
        "comision":     com,
        "fecha_cierre": fecha[:10] if fecha else date.today().isoformat(),
        "nota":         nota or f"Captura broker — registrado {date.today().isoformat()}",
    }

    print("\nDatos extraidos de la captura:")
    for k, v in trade.items():
        if v is not None and k != "nota":
            print(f"  {k:15} {v}")
    return trade


def build_trade_manual(ticker, cierre, broker, entrada=None,
                       qty=None, dire="long", nota="") -> dict:
    trade = {
        "ticker":       ticker.upper(),
        "broker":       broker,
        "direccion":    dire,
        "cantidad":     qty,
        "entrada_usd":  entrada,
        "cierre_usd":   float(cierre),
        "net_pl":       None,
        "fecha_cierre": date.today().isoformat(),
        "nota":         nota or "Registro manual",
    }
    print(f"Trade manual: {ticker} cierre ${cierre}")
    return trade


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--imagen",  help="Ruta a la captura del broker")
    parser.add_argument("--ticker",  help="Ticker (registro manual)")
    parser.add_argument("--cierre",  help="Precio de cierre (registro manual)")
    parser.add_argument("--broker",  default="broker_1")
    parser.add_argument("--entrada", help="Precio de entrada (opcional)")
    parser.add_argument("--qty",     help="Cantidad (opcional)")
    parser.add_argument("--dir",     default="long", help="long o short")
    parser.add_argument("--nota",    default="")
    args = parser.parse_args()

    if args.imagen:
        trade = build_trade_from_image(args.imagen, args.nota)
    elif args.ticker and args.cierre:
        trade = build_trade_manual(
            args.ticker, args.cierre, args.broker,
            args.entrada, args.qty, args.dir, args.nota
        )
    else:
        print("Indica --imagen o --ticker + --cierre")
        sys.exit(1)

    confirm = input("\n¿Registrar este trade? [s/N] ").strip().lower()
    if confirm == "s":
        append_trade(trade)
    else:
        print("Cancelado.")
