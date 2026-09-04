# -*- coding: utf-8 -*-
"""JSON を安全に書き出す。

途中で処理が止まってもファイルが壊れないよう、いったん同じフォルダの一時ファイルに
書いてから置き換える。実際に data/schools.json を空にしてしまったことがあるので、
データを書くツールは必ずこれを使うこと。
"""
from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path


def write_json(path: Path, doc) -> None:
    path = Path(path)
    text = json.dumps(doc, ensure_ascii=False, indent=2) + "\n"
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=path.name + ".", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(text)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    except BaseException:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise


def write_text(path: Path, text: str) -> None:
    path = Path(path)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=path.name + ".", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(text)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    except BaseException:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise
