# Replay escrime — installation

Trois fichiers, à garder **dans le même dossier** :
`serveur_replay.py`, `lecteur.html`, `Lancer le replay.bat`.

---

## 1. Python

Installe Python depuis python.org (ou le Microsoft Store). Coche
**« Add Python to PATH »** pendant l'installation.

Aucune bibliothèque à installer : tout tient dans la bibliothèque standard.

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
- Onglet **Tampon de relecture** : activé, durée max **20 s**.

> Le format MKV ne fonctionnera pas : aucun navigateur ne sait le lire.

### Paramètres → Raccourcis clavier
Assigne **« Sauvegarder la relecture »** à une touche. Un pavé numérique USB
ou une pédale USB posés au bord de la piste font un excellent déclencheur.

Note le dossier de destination des enregistrements : c'est celui que le
serveur doit surveiller.

---

## 3. Lancer

Double-clic sur **`Lancer le replay.bat`**. Le navigateur s'ouvre tout seul
sur `http://127.0.0.1:8000`.

Si tes replays ne sont pas dans `C:\Users\<toi>\Videos`, ouvre
`serveur_replay.py` et renseigne le chemin en haut du fichier :

```python
DOSSIER_CLIPS = r"D:\Escrime\Replays"
FPS = 60          # doit correspondre à la cadence réelle de la caméra
```

Le `r` devant les guillemets est important sous Windows.

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

## 5. Plus tard : une tablette

Sans réseau dans la salle, seule la machine de capture peut afficher le
lecteur. Pour y ajouter une tablette, il suffit d'un petit routeur de voyage
(type GL.iNet, ~35 CHF, alimenté en USB) qui crée un réseau local **sans
aucun internet**. Branche le PC et la tablette dessus : l'adresse à saisir
sur la tablette s'affiche au démarrage du serveur, du genre
`http://192.168.8.100:8000`. Rien à modifier dans le code.
