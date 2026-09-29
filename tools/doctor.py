#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""別のパソコンで作業を始める前に、環境がそろっているかを確かめる。

    python tools/doctor.py

新しいパソコンで clone したら、まずこれを実行する。
何をすればいいかまで出すので、上から順に潰していけば作業を始められる。

このプロジェクトで実際に起きた事故を検出する:
  - クラウド同期フォルダ（OneDrive など）の中に置いていて data/schools.json が
    巻き戻った。**.git ごと壊れることもある**ので、置き場所を最初に見る
  - GitHub Actions が12時間ごとに main を書き換えるので、pull し忘れると衝突する
  - bundle.js を作り直さずに push して、公開版だけ古いままになった

外部ライブラリ不要（標準ライブラリのみ）。
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"

# 同期フォルダの目印。パスにこれが入っていたら危ない。
CLOUD_DIRS = re.compile(
    r"onedrive|dropbox|google\s*drive|googledrive|icloud|box sync|pcloud|nextcloud|"
    r"megasync|yandex\.?disk", re.I)

# 名簿の件数の下限。これを割っていたら巻き戻りを疑う。
MIN_SCHOOLS = 200
MIN_LINES = 40

ok_count = warn_count = ng_count = 0


def say(mark: str, title: str, detail: str = "", fix: str = "") -> None:
    global ok_count, warn_count, ng_count
    if mark == "OK":
        ok_count += 1
    elif mark == "注意":
        warn_count += 1
    else:
        ng_count += 1
    print(f"[{mark}] {title}")
    if detail:
        print(f"       {detail}")
    if fix:
        print(f"       → {fix}")


def git(*args: str) -> tuple[int, str]:
    try:
        p = subprocess.run(["git", *args], cwd=str(ROOT), capture_output=True,
                           text=True, encoding="utf-8", errors="replace", timeout=60)
        return p.returncode, (p.stdout or "").strip() or (p.stderr or "").strip()
    except FileNotFoundError:
        return 127, "git が見つかりません"
    except subprocess.TimeoutExpired:
        return 124, "git の応答がありません"


def check_python() -> None:
    v = sys.version_info
    if v < (3, 9):
        say("NG", f"Python {v.major}.{v.minor} は古すぎます",
            "このプロジェクトのツールは Python 3.9 以上で動きます（3.13 で動作確認）。",
            "python.org から新しい Python を入れてください。")
    else:
        say("OK", f"Python {v.major}.{v.minor}.{v.micro}")


def check_location() -> None:
    path = str(ROOT)
    if CLOUD_DIRS.search(path):
        say("注意", "クラウド同期フォルダの中にあります",
            path,
            "同期と git がぶつかって data/schools.json が巻き戻ったり、\n"
            "       　　.git が壊れたりします。C:\\dev\\sinro のような同期されない場所へ\n"
            "       　　移してください。複数のパソコンで使うなら、フォルダを同期するのではなく\n"
            "       　　それぞれで git clone すること。")
    else:
        say("OK", "置き場所", path)


def check_git_identity() -> None:
    code, _ = git("--version")
    if code == 127:
        say("NG", "git が入っていません", fix="https://git-scm.com/ から入れてください。")
        return
    name = git("config", "user.name")[1]
    mail = git("config", "user.email")[1]
    if not name or not mail:
        say("NG", "git の名前とメールが未設定です",
            "コミットできません。",
            'git config --global user.name "あなたの名前"\n'
            "       　　git config --global user.email あなたのメール")
    else:
        say("OK", "git の名前とメール", f"{name} <{mail}>")


def check_remote() -> None:
    url = git("remote", "get-url", "origin")[1]
    if not url:
        say("NG", "origin が設定されていません",
            fix="git remote add origin https://github.com/nomaps1215-arch/sinro.git")
        return
    code, out = git("ls-remote", "--heads", "origin", "main")
    if code != 0:
        say("NG", "GitHub に届きません", out[:160],
            "ネットワークか認証を確認してください。push には GitHub の認証が要ります\n"
            "       　　（gh auth login か、Git Credential Manager のログイン）。")
    else:
        say("OK", "GitHub への接続", url)


def check_sync() -> None:
    """GitHub Actions が12時間ごとに main を書き換えるので、遅れていないか見る。"""
    if git("fetch", "--quiet", "origin", "main")[0] != 0:
        say("注意", "origin/main を取得できませんでした", fix="あとで git pull してください。")
        return
    behind = git("rev-list", "--count", "HEAD..origin/main")[1]
    ahead = git("rev-list", "--count", "origin/main..HEAD")[1]
    dirty = git("status", "--porcelain")[1]
    if behind.isdigit() and int(behind) > 0:
        say("注意", f"リモートより {behind} コミット遅れています",
            "巡回が自動で更新した内容がまだ手元にありません。",
            "git pull --rebase origin main")
    else:
        say("OK", "リモートと同じところにいます")
    if ahead.isdigit() and int(ahead) > 0:
        say("注意", f"push していないコミットが {ahead} 件あります", fix="git push origin main")
    if dirty:
        n = len(dirty.splitlines())
        say("注意", f"コミットしていない変更が {n} 件あります",
            "自動コミットと衝突するもとになります。",
            "git add -A; git commit -m '...'  （または git stash）")


def check_data() -> None:
    schools = DATA / "schools.json"
    lines = DATA / "lines.json"
    try:
        s = json.loads(schools.read_text(encoding="utf-8"))["schools"]
        ln = json.loads(lines.read_text(encoding="utf-8"))["lines"]
    except Exception as e:  # noqa: BLE001
        say("NG", "データを読み込めません", f"{type(e).__name__}: {e}",
            "git checkout -- data/ で戻せます。")
        return
    if len(s) < MIN_SCHOOLS or len(ln) < MIN_LINES:
        say("NG", f"データが減っています（高校 {len(s)} / 路線 {len(ln)}）",
            "同期による巻き戻りの可能性があります。",
            "git checkout -- data/ で戻してください。")
        return
    filled = {
        "偏差値": sum(1 for x in s if any(c.get("deviation") is not None for c in x["courses"])),
        "入学金": sum(1 for x in s if (x.get("admissionFee") or {}).get("amount")),
        "写真": sum(1 for x in s if x.get("photos")),
        "説明会": sum(1 for x in s if x.get("openSchool")),
    }
    say("OK", f"データ（高校 {len(s)} 校 / 路線 {len(ln)}）",
        "　".join(f"{k} {v}" for k, v in filled.items()))


def check_bundle() -> None:
    """bundle.js を作り直さずに push すると、公開版だけ古いままになる。"""
    bundle = DATA / "bundle.js"
    if not bundle.exists():
        say("NG", "data/bundle.js がありません", fix="python tools/build_bundle.py")
        return
    # 更新時刻では判定しない。git pull の直後は両方とも新しくなるので、
    # どちらが新しいかが当てにならず、毎回「古い」と言ってしまう。
    # 中身を作り直して見比べる。
    try:
        payload = {
            "lines": json.loads((DATA / "lines.json").read_text(encoding="utf-8")),
            "schools": json.loads((DATA / "schools.json").read_text(encoding="utf-8")),
        }
        expect = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
        current = bundle.read_text(encoding="utf-8").split("window.HS_DATA = ", 1)[-1]
        stale = current.rstrip().rstrip(";") != expect
    except Exception as e:  # noqa: BLE001
        say("注意", "data/bundle.js を確認できませんでした", f"{type(e).__name__}: {e}",
            "python tools/build_bundle.py")
        return
    if stale:
        say("注意", "data/bundle.js が data/*.json と一致しません",
            "このまま push すると、公開版だけ古い内容になります。",
            "python tools/build_bundle.py")
    else:
        say("OK", "data/bundle.js は最新です")


def check_gh() -> None:
    """巡回を手で走らせるときだけ要る。無くても作業はできる。"""
    if not shutil.which("gh"):
        say("注意", "GitHub CLI（gh）が入っていません",
            "無くても編集と push はできます。巡回を手で走らせたいときだけ必要です。",
            "https://cli.github.com/ から入れて gh auth login")
        return
    p = subprocess.run(["gh", "auth", "status"], capture_output=True, text=True,
                       encoding="utf-8", errors="replace")
    if p.returncode != 0:
        say("注意", "gh にログインしていません", fix="gh auth login")
    else:
        say("OK", "GitHub CLI（gh）")


def main() -> int:
    print("SINRO 開発環境の確認\n")
    check_python()
    check_location()
    check_git_identity()
    check_remote()
    check_sync()
    check_data()
    check_bundle()
    check_gh()

    print(f"\nOK {ok_count} / 注意 {warn_count} / NG {ng_count}")
    if ng_count:
        print("NG を直さないと作業を始められません。上の → を実行してください。")
        return 1
    if warn_count:
        print("注意はそのままでも動きますが、事故のもとになります。")
    else:
        print("そのまま作業を始められます。")
    print("\n作業の流れ:")
    print("  git pull --rebase origin main      # 始める前に必ず")
    print("  （編集）")
    print("  python tools/build_bundle.py       # data/*.json を触ったら")
    print("  python tools/qa_check.py           # データを触ったら")
    print("  git add -A; git commit -m '...'; git push origin main")
    return 0


if __name__ == "__main__":
    sys.exit(main())
