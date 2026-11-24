"""Веб-сервер: загрузка .dem (или .dem.zst / .dem.bz2 / .zip) -> список хайлайтов.

Локально:    python3 app.py   ->  http://localhost:5000
На сервере:  gunicorn app:app   (см. Dockerfile и README)

Переменные окружения:
  PORT            порт (по умолчанию 5000)
  MAX_UPLOAD_MB   максимальный размер загрузки, МБ (по умолчанию 1024)
  MAX_PARALLEL    сколько демок разбирать одновременно (по умолчанию 1; каждая ест 1–3 ГБ RAM)
"""
import os
import tempfile
import threading
import uuid

from flask import Flask, jsonify, request, send_from_directory
from werkzeug.exceptions import HTTPException

from highlights import TICKRATE, analyze
from replay import build_replay
from unpack import UnpackError, detect_kind, unpack

MAX_UPLOAD_MB = int(os.environ.get("MAX_UPLOAD_MB", "1024"))
MAX_PARALLEL = int(os.environ.get("MAX_PARALLEL", "1"))

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
app = Flask(__name__, static_folder=None)
app.config["MAX_CONTENT_LENGTH"] = MAX_UPLOAD_MB * 1024 * 1024

# Ограничение одновременных разборов, чтобы сервер не упал по памяти.
_slots = threading.BoundedSemaphore(MAX_PARALLEL)


@app.errorhandler(413)
def too_large(_e):
    return jsonify(error=f"Файл слишком большой (лимит {MAX_UPLOAD_MB} МБ)"), 413


@app.get("/")
def index():
    return send_from_directory(BASE_DIR, "index.html")


@app.get("/health")
def health():
    return jsonify(ok=True)


@app.get("/api/sample")
def sample():
    from sample_data import make_sample

    kills, ends, rosters = make_sample()
    res = analyze(kills, ends, rosters)
    from sample_data import make_sample_replay

    res["replay"] = make_sample_replay(kills, ends)
    res["meta"] = {"map": "демо-данные (синтетический матч)", "tickrate": TICKRATE, "sample": True}
    return jsonify(res)


@app.post("/api/analyze")
def analyze_upload():
    if not _slots.acquire(blocking=False):
        return jsonify(error="Сервер сейчас разбирает другую демку. Попробуй через минуту."), 429

    uid = uuid.uuid4().hex
    tmp = tempfile.gettempdir()
    dem_path = os.path.join(tmp, f"{uid}.dem")
    upload_path = dem_path
    try:
        f = request.files.get("demo")
        kind = detect_kind(f.filename) if f is not None else None
        if kind is None:
            return jsonify(error="Нужен файл .dem, .dem.zst, .dem.bz2 или .zip"), 400

        if kind != "dem":
            upload_path = os.path.join(tmp, f"{uid}.upload")
        f.save(upload_path)
        if kind != "dem":
            unpack(upload_path, dem_path, kind)

        from parse_demo import parse_demo

        kills, ends, rosters, meta, positions, windows = parse_demo(dem_path)
        if kills.empty or ends.empty:
            return jsonify(error="В демке не найдено убийств или раундов (разминка/неполный файл?)"), 422
        res = analyze(kills, ends, rosters)
        res["replay"] = None
        if positions is not None:
            try:
                res["replay"] = build_replay(positions, windows, kills)
            except Exception:
                res["replay"] = None  # без реплея, но хайлайты остаются
        meta["tickrate"] = TICKRATE
        res["meta"] = meta
        return jsonify(res)
    except HTTPException:
        raise  # например 413: пусть сработает обработчик выше
    except UnpackError as e:
        return jsonify(error=str(e)), e.status
    except ImportError:
        return jsonify(error="На сервере не установлен demoparser2: pip install demoparser2"), 503
    except Exception as e:  # битая демка, неподдерживаемая версия и т.п.
        return jsonify(error=f"Не удалось разобрать демку: {e}"), 422
    finally:
        for p in {upload_path, dem_path}:
            try:
                os.remove(p)
            except OSError:
                pass
        _slots.release()


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", "5000")), debug=False)
