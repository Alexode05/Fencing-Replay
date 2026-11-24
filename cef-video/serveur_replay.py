#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Serveur de replay pour l'arbitrage video en salle d'armes.

Surveille le dossier ou OBS depose les clips du tampon de relecture et les
sert à un lecteur web local. L'envoi vers CEF Vidéo est déclenché par le lecteur.

Bibliotheque standard uniquement, aucune installation requise.

Usage :
    python serveur_replay.py
    python serveur_replay.py "D:\\Escrime\\Replays"
"""

from __future__ import annotations

import argparse
import hmac
import http.server
import json
import mimetypes
import os
import re
import secrets
import socketserver
import sys
import tempfile
import threading
import time
import traceback
import webbrowser
from pathlib import Path
from urllib.parse import unquote

from cef_upload import envoyer_clips, verifier_jeton

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
CONFIG = Path.home() / ".cef-fencing-replay"
FORMAT_CLE_ENVOI = re.compile(r"[A-Za-z0-9_-]{32,128}")
NOM_VALIDE = re.compile(r"^[\w\-. ()\[\]'+&,]+$", re.UNICODE)
RESSOURCES = {
    "/assets/OpenSauceSans-Regular.ttf",
    "/assets/OpenSauceSans-SemiBold.ttf",
    "/assets/cef-favicon.png",
    "/assets/cef-logo.png",
    "/assets/Flyer-Black-Condensed-Regular.ttf",
}


def dossier_clips(argument=None) -> Path:
    reglage = argument or os.environ.get("CEF_REPLAY_CLIPS") or DOSSIER_CLIPS
    if not reglage:
        try:
            reglage = (CONFIG / "clips-directory").read_text(encoding="utf-8").strip()
        except OSError:
            pass
    if reglage:
        return Path(reglage).expanduser().absolute()
    choix = ("Movies",) if sys.platform == "darwin" else ("Videos", "Vidéos")
    return next((Path.home() / nom for nom in choix if (Path.home() / nom).is_dir()),
                Path.home() / choix[0])


def jeton_envoi():
    try:
        valeur = (CONFIG / "upload-token").read_text(encoding="ascii").strip()
    except (OSError, UnicodeError):
        valeur = os.environ.get("CEF_REPLAY_UPLOAD_TOKEN", "").strip()
    return valeur if FORMAT_CLE_ENVOI.fullmatch(valeur) else ""


def enregistrer_jeton(valeur):
    CONFIG.mkdir(mode=0o700, parents=True, exist_ok=True)
    temporaire = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="ascii", dir=CONFIG,
                                         prefix=".upload-token-", delete=False) as fichier:
            temporaire = Path(fichier.name)
            fichier.write(valeur + "\n")
        temporaire.replace(CONFIG / "upload-token")
    finally:
        if temporaire is not None:
            temporaire.unlink(missing_ok=True)


def lister_clips(dossier: Path) -> list[dict]:
    """Les clips les plus recents, du plus recent au plus ancien."""
    maintenant = time.time()
    trouves = []
    for chemin in dossier.iterdir():
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


def chemin_sur(dossier: Path, nom: str) -> Path | None:
    """Resout un nom de clip en refusant tout ce qui sort du dossier."""
    nom = unquote(nom)
    if not nom or not NOM_VALIDE.match(nom):
        return None
    if Path(nom).name != nom:
        return None
    chemin = (dossier / nom).resolve()
    if chemin.parent != dossier.resolve() or not chemin.is_file():
        return None
    if chemin.suffix.lower() not in EXTENSIONS:
        return None
    return chemin


class Envoi:
    def __init__(self):
        self.verrou = threading.Lock()
        self.actif = False
        self.rapport = None

    def lire(self):
        with self.verrou:
            return {"running": self.actif, "report": self.rapport}

    def publier(self, rapport):
        with self.verrou:
            self.rapport = rapport

    def demarrer(self, dossier, nettoyer):
        with self.verrou:
            if self.actif:
                return
            self.actif = True
            self.rapport = None
        threading.Thread(target=self.executer, args=(dossier, nettoyer), daemon=True).start()

    def executer(self, dossier, nettoyer):
        try:
            rapport = envoyer_clips(dossier, jeton_envoi(), nettoyer=nettoyer, progression=self.publier)
        except Exception:
            traceback.print_exc()
            rapport = {"status": "error", "errors": [{"message": "L’envoi a été interrompu."}]}
        with self.verrou:
            self.rapport = rapport
            self.actif = False


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
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")
        self.end_headers()
        try:
            if self.command != "HEAD":
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
        try:
            taille = chemin.stat().st_size
        except FileNotFoundError:
            self._envoyer_octets(b"Clip introuvable.", "text/plain", 404)
            return
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

        if self.command == "HEAD":
            return
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
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError, FileNotFoundError):
            # Le navigateur coupe en permanence les requetes video : normal.
            pass

    # --- routes ---------------------------------------------------------

    def do_HEAD(self):
        self.do_GET()

    def connexion_locale(self):
        if self.headers.get("Host") in self.server.hotes:
            return True
        self._envoyer_json({"error": "Adresse locale requise."}, 403)
        return False

    def do_GET(self):
        if not self.connexion_locale():
            return
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
            page = page.replace("__CLE_LOCALE__", self.server.cle_locale)
            self._envoyer_octets(page.encode("utf-8"), "text/html; charset=utf-8")
            return

        if chemin_url == "/api/clips":
            dossier = self.server.dossier
            try:
                clips = lister_clips(dossier)
            except OSError:
                self._envoyer_json({"dossier": str(dossier), "clips": [], "dossierDisponible": False})
            else:
                self._envoyer_json({"dossier": str(dossier), "clips": clips, "dossierDisponible": True})
            return

        if chemin_url == "/api/envoi":
            self._envoyer_json(self.server.envoi.lire())
            return

        if chemin_url == "/api/parametres":
            self._envoyer_json({"configured": bool(jeton_envoi())})
            return

        if chemin_url in RESSOURCES:
            fichier = RACINE / chemin_url.lstrip("/")
            if fichier.is_file():
                type_mime = "font/ttf" if fichier.suffix == ".ttf" else "image/png"
                self._envoyer_octets(fichier.read_bytes(), type_mime)
                return

        if chemin_url.startswith("/clip/"):
            chemin = chemin_sur(self.server.dossier, chemin_url[len("/clip/"):])
            if chemin is None:
                self._envoyer_octets(b"Clip introuvable.", "text/plain", 404)
                return
            self._envoyer_video(chemin)
            return

        self._envoyer_octets(b"Page inconnue.", "text/plain", 404)

    def do_POST(self):
        if not self.connexion_locale():
            return
        origine = self.headers.get("Origin")
        cle = self.headers.get("X-Replay-Local", "")
        if ((origine and origine not in self.server.origines)
                or not cle.isascii() or not hmac.compare_digest(cle, self.server.cle_locale)):
            self.close_connection = True
            self._envoyer_json({"error": "Action réservée au lecteur local."}, 403)
            return
        if self.path == "/api/parametres":
            self.enregistrer_parametres()
            return
        if self.path == "/api/parametres/verifier":
            self._envoyer_json({"status": verifier_jeton(jeton_envoi())})
            return
        if self.path == "/api/envoi":
            self.server.envoi.demarrer(self.server.dossier, self.server.nettoyer)
            self._envoyer_json(self.server.envoi.lire(), 202)
            return
        if self.path == "/api/redemarrer":
            self.server.dossier = dossier_clips(self.server.argument_dossier)
            self._envoyer_json({"ok": True})
            return
        if self.path == "/api/quitter":
            if self.server.envoi.lire()["running"]:
                self._envoyer_json({"error": "Un envoi est en cours."}, 409)
                return
            self._envoyer_json({"ok": True})
            threading.Thread(target=self.server.shutdown, daemon=True).start()
            return
        self._envoyer_json({"error": "Page inconnue."}, 404)

    def enregistrer_parametres(self):
        try:
            tailles = self.headers.get_all("Content-Length", ())
            taille = int(tailles[0]) if len(tailles) == 1 else 0
            if not 1 <= taille <= 512 or self.headers.get("Transfer-Encoding"):
                raise ValueError
            donnees = json.loads(self.rfile.read(taille))
        except (ValueError, UnicodeError):
            self.close_connection = True
            self._envoyer_json({"error": "Impossible de lire les paramètres."}, 400)
            return
        valeur = donnees.get("uploadToken") if isinstance(donnees, dict) else None
        if not isinstance(valeur, str) or not FORMAT_CLE_ENVOI.fullmatch(valeur.strip()):
            self._envoyer_json({"error": "La clé d’envoi doit contenir entre 32 et 128 caractères valides."}, 400)
            return
        valeur = valeur.strip()
        statut = verifier_jeton(valeur)
        if statut == "invalid":
            self._envoyer_json({"error": "Clé refusée par CEF Vidéo. Vérifie qu’elle est active dans l’espace admin."}, 422)
            return
        try:
            enregistrer_jeton(valeur)
        except OSError:
            self._envoyer_json({"error": "La clé n’a pas pu être enregistrée sur cet ordinateur."}, 500)
            return
        self._envoyer_json({"configured": True, "status": statut})


class Serveur(socketserver.ThreadingTCPServer):
    daemon_threads = True
    allow_reuse_address = True


def main():
    parser = argparse.ArgumentParser(description="Lecteur local CEF Vidéo")
    parser.add_argument("dossier", nargs="?")
    parser.add_argument("--port", type=int, default=PORT)
    parser.add_argument("--sans-navigateur", action="store_true")
    parser.add_argument("--garder-les-clips", action="store_true")
    args = parser.parse_args()
    with Serveur(("127.0.0.1", args.port), Handler) as httpd:
        port = httpd.server_address[1]
        url = f"http://127.0.0.1:{port}"
        httpd.argument_dossier = args.dossier
        httpd.dossier = dossier_clips(args.dossier)
        httpd.hotes = {f"127.0.0.1:{port}", f"localhost:{port}"}
        httpd.origines = {f"http://{hote}" for hote in httpd.hotes}
        httpd.cle_locale = secrets.token_urlsafe(32)
        httpd.envoi = Envoi()
        httpd.nettoyer = not args.garder_les_clips
        print(f"CEF_REPLAY_URL={url}", flush=True)
        print(f"Dossier surveillé : {httpd.dossier}", flush=True)
        if not args.sans_navigateur:
            threading.Thread(target=webbrowser.open, args=(url,), daemon=True).start()
        try:
            httpd.serve_forever()
        except KeyboardInterrupt:
            print("\nServeur arrete.")


if __name__ == "__main__":
    main()
