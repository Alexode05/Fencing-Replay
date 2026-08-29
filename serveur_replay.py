#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Serveur de replay pour l'arbitrage video en salle d'armes.

Surveille le dossier ou OBS depose les clips du tampon de relecture et les
sert a un lecteur web local. Fonctionne entierement hors ligne : rien ne sort
de la machine tant qu'aucun autre appareil ne s'y connecte.

Bibliotheque standard uniquement, aucune installation requise.

Usage :
    python serveur_replay.py
    python serveur_replay.py "D:\\Escrime\\Replays"
"""

from __future__ import annotations

import http.server
import json
import mimetypes
import re
import socket
import socketserver
import sys
import threading
import time
import webbrowser
from pathlib import Path
from urllib.parse import unquote

# --------------------------------------------------------------- Reglages

PORT = 8000

# Dossier surveille. Laisse None pour utiliser le dossier "Videos" de
# l'utilisateur, qui est la destination par defaut d'OBS sous Windows.
DOSSIER_CLIPS = None

# Cadence de la camera, utilisee par le lecteur pour le pas image par image.
FPS = 60

# Nombre de clips recents proposes dans le lecteur.
NB_CLIPS = 8

# Un fichier ecrit il y a moins de X secondes est ignore : OBS est peut-etre
# encore en train de le remuxer.
DELAI_STABILITE = 1.5

EXTENSIONS = {".mp4", ".m4v", ".mov", ".webm"}

# ------------------------------------------------------------------------

RACINE = Path(__file__).resolve().parent
NOM_VALIDE = re.compile(r"^[\w\-. ()\[\]'+&,]+$", re.UNICODE)


def dossier_clips() -> Path:
    """Dossier surveille, par ordre de priorite : argument, reglage, defaut."""
    if len(sys.argv) > 1:
        return Path(sys.argv[1]).expanduser().resolve()
    if DOSSIER_CLIPS:
        return Path(DOSSIER_CLIPS).expanduser().resolve()
    for nom in ("Videos", "Vidéos"):
        candidat = Path.home() / nom
        if candidat.is_dir():
            return candidat.resolve()
    return Path.home().resolve()


DOSSIER = dossier_clips()


def lister_clips() -> list[dict]:
    """Les clips les plus recents, du plus recent au plus ancien."""
    maintenant = time.time()
    trouves = []
    try:
        entrees = list(DOSSIER.iterdir())
    except OSError:
        return []

    for chemin in entrees:
        if chemin.suffix.lower() not in EXTENSIONS:
            continue
        try:
            info = chemin.stat()
        except OSError:
            continue
        if not chemin.is_file() or info.st_size == 0:
            continue
        # Fichier trop frais : OBS finit peut-etre de l'ecrire.
        if maintenant - info.st_mtime < DELAI_STABILITE:
            continue
        trouves.append(
            {
                "nom": chemin.name,
                "horodatage": info.st_mtime,
                "heure": time.strftime("%H:%M:%S", time.localtime(info.st_mtime)),
                "octets": info.st_size,
            }
        )

    trouves.sort(key=lambda c: c["horodatage"], reverse=True)
    return trouves[:NB_CLIPS]


def chemin_sur(nom: str) -> Path | None:
    """Resout un nom de clip en refusant tout ce qui sort du dossier."""
    nom = unquote(nom)
    if not nom or not NOM_VALIDE.match(nom):
        return None
    if Path(nom).name != nom:
        return None
    chemin = (DOSSIER / nom).resolve()
    if chemin.parent != DOSSIER or not chemin.is_file():
        return None
    if chemin.suffix.lower() not in EXTENSIONS:
        return None
    return chemin


class Handler(http.server.BaseHTTPRequestHandler):
    server_version = "ReplayEscrime/1.0"
    protocol_version = "HTTP/1.1"

    # --- utilitaires ----------------------------------------------------

    def log_message(self, format, *args):  # noqa: A002
        pass  # console silencieuse : seuls les messages utiles s'affichent

    def _envoyer_octets(self, contenu: bytes, type_mime: str, code: int = 200):
        self.send_response(code)
        self.send_header("Content-Type", type_mime)
        self.send_header("Content-Length", str(len(contenu)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        try:
            self.wfile.write(contenu)
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
            pass

    def _envoyer_json(self, donnees, code: int = 200):
        self._envoyer_octets(
            json.dumps(donnees, ensure_ascii=False).encode("utf-8"),
            "application/json; charset=utf-8",
            code,
        )

    # --- diffusion video avec support des requetes Range ----------------

    def _envoyer_video(self, chemin: Path):
        taille = chemin.stat().st_size
        type_mime = mimetypes.guess_type(chemin.name)[0] or "video/mp4"
        entete = self.headers.get("Range", "")

        debut, fin = 0, taille - 1
        partiel = False
        correspondance = re.match(r"bytes=(\d*)-(\d*)", entete)
        if correspondance:
            g1, g2 = correspondance.groups()
            if g1:
                debut = int(g1)
                fin = int(g2) if g2 else taille - 1
            elif g2:  # forme "bytes=-500" : les X derniers octets
                debut = max(0, taille - int(g2))
            fin = min(fin, taille - 1)
            if debut > fin:
                self.send_response(416)
                self.send_header("Content-Range", f"bytes */{taille}")
                self.send_header("Content-Length", "0")
                self.end_headers()
                return
            partiel = True

        longueur = fin - debut + 1
        self.send_response(206 if partiel else 200)
        self.send_header("Content-Type", type_mime)
        self.send_header("Accept-Ranges", "bytes")
        self.send_header("Content-Length", str(longueur))
        if partiel:
            self.send_header("Content-Range", f"bytes {debut}-{fin}/{taille}")
        self.end_headers()

        try:
            with open(chemin, "rb") as f:
                f.seek(debut)
                restant = longueur
                while restant > 0:
                    bloc = f.read(min(64 * 1024, restant))
                    if not bloc:
                        break
                    self.wfile.write(bloc)
                    restant -= len(bloc)
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
            # Le navigateur coupe en permanence les requetes video : normal.
            pass

    # --- routes ---------------------------------------------------------

    def do_HEAD(self):
        self.do_GET(corps=False)

    def do_GET(self, corps: bool = True):
        chemin_url = self.path.split("?", 1)[0]

        if chemin_url in ("/", "/index.html"):
            fichier = RACINE / "lecteur.html"
            if not fichier.is_file():
                self._envoyer_octets(
                    b"lecteur.html est introuvable a cote de serveur_replay.py.",
                    "text/plain; charset=utf-8",
                    500,
                )
                return
            page = fichier.read_text(encoding="utf-8")
            page = page.replace("__FPS__", str(FPS))
            page = page.replace("__DOSSIER__", str(DOSSIER))
            self._envoyer_octets(page.encode("utf-8"), "text/html; charset=utf-8")
            return

        if chemin_url == "/api/clips":
            self._envoyer_json({"dossier": str(DOSSIER), "clips": lister_clips()})
            return

        if chemin_url.startswith("/clip/"):
            chemin = chemin_sur(chemin_url[len("/clip/"):])
            if chemin is None:
                self._envoyer_octets(b"Clip introuvable.", "text/plain", 404)
                return
            self._envoyer_video(chemin)
            return

        self._envoyer_octets(b"Page inconnue.", "text/plain", 404)


class Serveur(socketserver.ThreadingTCPServer):
    daemon_threads = True
    allow_reuse_address = True


def adresse_locale() -> str:
    """IP de la machine sur le reseau local, si elle en a une."""
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("10.255.255.255", 1))
        return s.getsockname()[0]
    except OSError:
        return ""
    finally:
        s.close()


def main():
    if not DOSSIER.is_dir():
        print(f"Le dossier surveille n'existe pas : {DOSSIER}")
        print("Passe le bon chemin en argument, ou modifie DOSSIER_CLIPS en haut du fichier.")
        sys.exit(1)

    print("=" * 62)
    print("  REPLAY ESCRIME")
    print("=" * 62)
    print(f"  Dossier surveille : {DOSSIER}")
    print(f"  Cadence declaree  : {FPS} images/s")
    print()
    print(f"  Sur cette machine : http://127.0.0.1:{PORT}")
    ip = adresse_locale()
    if ip:
        print(f"  Depuis une tablette : http://{ip}:{PORT}")
    print()
    print("  Ferme cette fenetre pour arreter le serveur.")
    print("=" * 62)

    threading.Timer(1.0, lambda: webbrowser.open(f"http://127.0.0.1:{PORT}")).start()

    # Ecoute uniquement la machine elle-meme : pas d'alerte pare-feu Windows,
    # rien de joignable depuis l'exterieur. Remplacer par "0.0.0.0" si un jour
    # un autre appareil doit se connecter.
    with Serveur(("127.0.0.1", PORT), Handler) as httpd:
        try:
            httpd.serve_forever()
        except KeyboardInterrupt:
            print("\nServeur arrete.")


if __name__ == "__main__":
    main()
