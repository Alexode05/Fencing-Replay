# CEF Vidéo — lecteur d’arbitrage local

Cette édition ajoute une fenêtre dédiée sur Windows et macOS, ainsi que l’envoi des replays vers le site du club.

---

## 1. Python

Installe Python 3.10 ou une version plus récente depuis python.org.
Sur Windows, coche **« Add Python to PATH »** pendant l’installation.

Le lecteur Python utilise uniquement la bibliothèque standard.

---

## 2. OBS — les réglages qui comptent

### Source caméra
Propriétés de la source → résolution/FPS sur **Personnalisé** → 1280×720 @ 60.
Format vidéo : **MJPEG** (en YUY2, la plupart des webcams plafonnent à 30 fps
sans le dire). Bouton « Configurer la vidéo » → exposition **manuelle**,
temps de pose 1/500 s si l'éclairage le permet.

### Paramètres → Vidéo
FPS communes : **60**. Sinon OBS jette une image sur deux.

### Paramètres → Sortie → mode Avancé
- Onglet **Enregistrement** : format **MP4**, encodeur matériel
  (NVENC / QuickSync / AMF), intervalle d'images clés **1 s**
  (le défilement arrière devient beaucoup plus réactif).
- Onglet **Tampon de relecture** : activé, durée max **20 s**, préfixe de nom `Replay`.

> Le format MKV ne fonctionnera pas : aucun navigateur ne sait le lire.

### Paramètres → Raccourcis clavier
Assigne **« Sauvegarder la relecture »** à une touche. Un pavé numérique USB
ou une pédale USB posés au bord de la piste font un excellent déclencheur.

Utilise un dossier OBS dédié aux replays. C’est celui que le lecteur doit surveiller.

---

## 3. Lancer

**Windows.** Depuis ce dossier, compile l’application avec le SDK .NET.

```powershell
dotnet build -c Release windows/CEF-Video.csproj
```

Ouvre ensuite `windows/bin/Release/net48/CEF Video.exe`. L’application
utilise .NET Framework 4.8 ou une version ultérieure et le
[runtime Microsoft Edge WebView2](https://developer.microsoft.com/microsoft-edge/webview2/).
`Lancer le replay.bat` ouvre également le lecteur dans le navigateur.

**macOS.** Ouvre `CEF Vidéo.app` dans le Finder. Pour compiler l’application
depuis le dossier source avec les outils de ligne de commande de Xcode, lance
`./macos/build.sh`. La commande affiche le chemin de l’application créée.

Le dossier par défaut est `~/Movies` sur macOS et `Videos` ou `Vidéos` dans
le dossier utilisateur Windows. Pour indiquer le dossier utilisé par OBS, écris
son chemin absolu dans `~/.cef-fencing-replay/clips-directory` ou définis la
variable d’environnement `CEF_REPLAY_CLIPS`. Sous Windows, `~` désigne le dossier
`%USERPROFILE%`. Le dossier `.cef-fencing-replay` est à créer s’il manque.

Si le lecteur affiche **Dossier vidéo introuvable**, reconnecte le disque ou
corrige le chemin, puis clique sur **Redémarrer**.

---

## 4. Commandes de l'arbitre

| Touche | Action |
|---|---|
| Espace | Lecture / pause |
| ← → | Image par image |
| Maj + ← → | Recul / avance d'une demi-seconde |
| 1 2 3 4 | Vitesse 1× · ½ · ¼ · ⅒ |
| L | Boucle |
| R | Retour au début |
| N | Dernier clip |
| F | Plein écran |

La barre grise sous la vidéo se manipule à la souris ou au doigt.

**Auto** : le lecteur charge automatiquement chaque nouveau clip. Désactive-le
si tu veux analyser une action tranquillement pendant que l'assaut continue.

---

## 5. Envoyer les clips

Dans le lecteur, ouvre **Paramètres**, colle la clé d’envoi fournie par
l’administrateur et clique sur **Enregistrer**. L’app garde la clé dans
`~/.cef-fencing-replay/upload-token`. La variable d’environnement
`CEF_REPLAY_UPLOAD_TOKEN` sert de solution de secours si ce fichier est absent.

Clique sur **Envoyer au serveur** pour transférer les clips finalisés dont le
nom commence par `Replay`. CEF Vidéo reconnaît les doublons à partir du contenu
du fichier. La copie locale est supprimée après confirmation du serveur. Les
clips dont l’envoi reste à confirmer sont conservés. Le suivi reste visible en
haut du lecteur pendant que tu utilises les commandes vidéo. Une alerte rouge
s’affiche en cas de perte de connexion pendant l’envoi.

Pour un premier essai avec conservation des fichiers locaux, démarre le lecteur
depuis le dossier source avec `python3 serveur_replay.py --garder-les-clips`
sur macOS ou `py serveur_replay.py --garder-les-clips` sur Windows.

**Quitter** propose **Envoyer et quitter** ou **Quitter sans envoyer**. Cette
confirmation s’affiche aussi à la fermeture de l’application Windows ou macOS.
Dans un navigateur, le bouton **Quitter** du lecteur ouvre ce dialogue.

Le titre utilise Open Sauce Sans par défaut. Si tu disposes d’une licence
pour Flyer Black Condensed, place `Flyer-Black-Condensed-Regular.ttf` dans
`assets` avant de compiler pour l’utiliser sur le titre.
