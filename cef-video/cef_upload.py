"""Envoi manuel des clips OBS vers CEF Vidéo et nettoyage après confirmation."""

from __future__ import annotations

from contextlib import closing
from datetime import datetime, timezone
import hashlib
import http.client
import json
import os
from pathlib import Path
import ssl
import stat
import sys
import time
from urllib.parse import quote, urlsplit

CEF_UPLOAD_URL = "https://video.escrime-founex.ch/api/replay-upload"
EXTENSIONS = {".mp4", ".m4v", ".mov", ".webm"}
TAILLE_MAX = 128 * 1024 * 1024
TAILLE_REPONSE_MAX = 64 * 1024
DELAI_STABILITE = 5


class ErreurEnvoi(Exception):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


def clips_a_envoyer(dossier: Path, prefixe: str = "Replay") -> list[Path]:
    """Limite l'envoi aux clips OBS du dossier, y compris ceux hors du lecteur."""
    prefixe = prefixe.strip().casefold()
    if not prefixe:
        raise ErreurEnvoi("configuration", "Indique le préfixe des clips de relecture dans OBS.")
    clips = []
    for chemin in Path(dossier).iterdir():
        nom = chemin.stem.casefold()
        if chemin.suffix.lower() not in EXTENSIONS or not nom.startswith(prefixe):
            continue
        suite = nom[len(prefixe):]
        if suite and suite[0] not in " _-":
            continue
        if chemin.is_symlink() or not chemin.is_file():
            continue
        clips.append(chemin)
    return sorted(clips)


def _connexion(url: str, *, timeout=30):
    try:
        adresse = urlsplit(url)
        hote, port = adresse.hostname, adresse.port
    except ValueError:
        raise ErreurEnvoi("configuration", "L’adresse du serveur est invalide.") from None

    if not hote or adresse.username is not None or adresse.fragment:
        raise ErreurEnvoi("configuration", "L’adresse du serveur est invalide.")
    if adresse.scheme == "https":
        contexte = ssl.create_default_context()
        certificats_macos = Path("/etc/ssl/cert.pem")
        if sys.platform == "darwin" and contexte.cert_store_stats()["x509_ca"] == 0 and certificats_macos.is_file():
            contexte.load_verify_locations(cafile=str(certificats_macos))
        connexion = http.client.HTTPSConnection(hote, port, timeout=timeout, context=contexte)
    elif adresse.scheme == "http" and hote in {"127.0.0.1", "::1"}:
        connexion = http.client.HTTPConnection(hote, port, timeout=timeout)
    else:
        raise ErreurEnvoi("configuration", "Le transfert vers CEF Vidéo requiert HTTPS.")
    destination = adresse.path or "/"
    if adresse.query:
        destination += "?" + adresse.query
    return connexion, destination


def verifier_jeton(jeton, *, url=CEF_UPLOAD_URL):
    """Contrôle la clé sans transmettre de clip, y compris sur l'ancien serveur."""
    if not jeton:
        return "missing"
    try:
        connexion, destination = _connexion(url, timeout=8)
        with closing(connexion):
            entetes = {"Authorization": f"Bearer {jeton}", "Accept": "application/json"}
            connexion.request("GET", destination + "/check", headers=entetes)
            with connexion.getresponse() as reponse:
                code = reponse.status
                corps = reponse.read(TAILLE_REPONSE_MAX + 1)
            if code == 200:
                resultat = json.loads(corps)
                if isinstance(resultat, dict) and resultat.get("valid") is True:
                    return "valid"
            if code == 404:
                entetes.update({"X-Clip-Name": "Replay-validation.mp4",
                                "X-Clip-Sha256": hashlib.sha256(b"").hexdigest()})
                connexion.request("POST", destination, b"", entetes)
                with connexion.getresponse() as reponse:
                    code = reponse.status
                    if code == 415:
                        resultat = json.loads(reponse.read(TAILLE_REPONSE_MAX + 1))
                        if isinstance(resultat, dict) and resultat.get("error") == "Choisissez une vidéo MP4, M4V, WebM ou MOV valide.":
                            return "valid"
            if code in (401, 403):
                return "invalid"
    except (ErreurEnvoi, OSError, http.client.HTTPException, ValueError):
        pass
    return "unavailable"


def _signature(info):
    return (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns)


def _verifier_fichier(chemin: Path, reference, flux=None):
    try:
        nom_actuel = chemin.stat(follow_symlinks=False)
        conforme = stat.S_ISREG(nom_actuel.st_mode) and _signature(nom_actuel) == _signature(reference)
        if flux is not None:
            conforme = conforme and _signature(os.fstat(flux.fileno())) == _signature(reference)
    except OSError:
        conforme = False
    if not conforme:
        raise ErreurEnvoi("changed", "Le fichier a changé pendant l’envoi. Il est conservé.")


def _envoyer_fichier(connexion, destination, jeton, chemin, flux, reference, empreinte):
    entetes = {
        "Authorization": f"Bearer {jeton}",
        "Accept": "application/json",
        "Content-Type": "application/octet-stream",
        "Content-Length": str(reference.st_size),
        "X-Clip-Name": quote(chemin.name, safe=""),
        "X-Clip-Sha256": empreinte,
        "X-Clip-Time": datetime.fromtimestamp(reference.st_mtime, timezone.utc).isoformat(timespec="seconds"),
    }

    def lire_clip():
        restant = reference.st_size
        while restant:
            try:
                bloc = flux.read(min(restant, 256 * 1024))
            except OSError:
                raise ErreurEnvoi("local_file", "La lecture du clip local a échoué.") from None
            if not bloc:
                raise ErreurEnvoi("changed", "Le fichier a changé pendant l’envoi. Il est conservé.")
            restant -= len(bloc)
            yield bloc

    try:
        connexion.request("POST", destination, lire_clip(), headers=entetes)
        with connexion.getresponse() as reponse:
            code = reponse.status
            contenu = reponse.read(TAILLE_REPONSE_MAX + 1)
    except ssl.SSLCertVerificationError:
        raise ErreurEnvoi("server", "Le certificat de CEF Vidéo n’a pas pu être vérifié.") from None
    except (OSError, http.client.HTTPException):
        raise ErreurEnvoi("network", "Impossible de joindre CEF Vidéo. Vérifie la connexion Internet.") from None
    finally:
        connexion.close()

    if code not in {200, 201}:
        if code in {401, 403}:
            raise ErreurEnvoi("configuration", "CEF Vidéo a refusé la clé d’envoi.")
        if code == 413:
            raise ErreurEnvoi("size", "Ce clip dépasse la limite d’envoi.")
        if code in {400, 415, 422}:
            raise ErreurEnvoi("server", "CEF Vidéo a refusé le clip.")
        raise ErreurEnvoi("server", f"CEF Vidéo a refusé l’envoi (HTTP {code}).")
    try:
        recu = json.loads(contenu) if len(contenu) <= TAILLE_REPONSE_MAX else None
    except (UnicodeDecodeError, json.JSONDecodeError):
        recu = None
    statut = {200: "duplicate", 201: "stored"}[code]
    if not isinstance(recu, dict) or recu.get("status") != statut or recu.get("sha256") != empreinte:
        raise ErreurEnvoi("invalid_receipt", "CEF Vidéo n’a pas confirmé l’intégrité du clip. Il est conservé.")
    return statut


def envoyer_clips(dossier, jeton, nettoyer=True, progression=None, *, url=CEF_UPLOAD_URL, prefixe="Replay"):
    """Envoie le lot en série. Supprime uniquement une copie locale confirmée par le serveur."""
    rapport = {
        "status": "ok", "total": 0, "processed": 0, "uploaded": 0,
        "duplicates": 0, "deleted": 0, "skipped": 0, "errors": [], "current": None,
    }

    def publier():
        if progression is not None:
            progression({**rapport, "errors": rapport["errors"].copy()})

    def signaler(code, message):
        rapport["errors"].append({"name": rapport["current"], "message": message, "code": code})
        rapport["status"] = code if code in {"network", "configuration", "directory"} else "partial"

    try:
        clips = clips_a_envoyer(dossier, prefixe)
    except OSError:
        signaler("directory", "Le dossier des clips est introuvable. Redémarre CEF Vidéo après l’avoir reconnecté.")
        return rapport
    except ErreurEnvoi as erreur:
        signaler(erreur.code, str(erreur))
        return rapport

    rapport["total"] = len(clips)
    if not clips:
        return rapport
    if not jeton or not all(33 <= ord(caractere) <= 126 for caractere in jeton):
        signaler("configuration", "La clé d’envoi CEF Vidéo est manquante ou invalide.")
        return rapport

    try:
        connexion, destination = _connexion(url)
    except ErreurEnvoi as erreur:
        signaler(erreur.code, str(erreur))
        return rapport

    for chemin in clips:
        rapport["current"] = chemin.name
        publier()
        try:
            reference = chemin.stat(follow_symlinks=False)
            if not stat.S_ISREG(reference.st_mode) or not reference.st_size:
                rapport["skipped"] += 1
                continue
            if time.time() - reference.st_mtime < DELAI_STABILITE:
                rapport["skipped"] += 1
                continue
            if reference.st_size > TAILLE_MAX:
                raise ErreurEnvoi("size", "Ce clip dépasse la limite d’envoi de 128 Mio.")

            options = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_BINARY", 0)
            with os.fdopen(os.open(chemin, options), "rb") as flux:
                _verifier_fichier(chemin, reference, flux)
                empreinte = hashlib.sha256()
                for bloc in iter(lambda: flux.read(256 * 1024), b""):
                    empreinte.update(bloc)
                _verifier_fichier(chemin, reference, flux)
                flux.seek(0)
                recu = _envoyer_fichier(connexion, destination, jeton, chemin, flux, reference, empreinte.hexdigest())
                compteur = "uploaded" if recu == "stored" else "duplicates"
                rapport[compteur] += 1
                _verifier_fichier(chemin, reference, flux)

            if nettoyer:
                _verifier_fichier(chemin, reference)
                try:
                    chemin.unlink()
                except OSError:
                    raise ErreurEnvoi("cleanup", "Clip reçu. La copie locale n’a pas pu être supprimée.") from None
                rapport["deleted"] += 1
        except ErreurEnvoi as erreur:
            signaler(erreur.code, str(erreur))
            if rapport["status"] in {"network", "configuration"}:
                break
        except OSError:
            signaler("local_file", "Impossible de lire le clip local. Il est conservé.")
        finally:
            rapport["processed"] += 1

    rapport["current"] = None
    publier()
    return rapport
