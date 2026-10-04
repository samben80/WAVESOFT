# photocompress

Compresse des photos sous une taille maximale (**50 Ko par défaut**) **sans changer leur résolution**, puis les enregistre **renommées** dans un dossier de destination. Les originaux ne sont jamais modifiés.

Formats acceptés : `.jpg`, `.jpeg`, `.png`, `.webp`, `.bmp`, `.tif`, `.tiff`, `.gif`.

## Version Windows (.exe)

Aucune installation de Python n'est nécessaire.

Téléchargement direct (dernière version) :

- **[PhotoCompress.exe](https://github.com/samben80/WAVESOFT/releases/latest/download/PhotoCompress.exe)** — version avec fenêtre
- [photocompress-cli.exe](https://github.com/samben80/WAVESOFT/releases/latest/download/photocompress-cli.exe) — version ligne de commande
- [PhotoCompress-windows.zip](https://github.com/samben80/WAVESOFT/releases/latest/download/PhotoCompress-windows.zip) — les deux + ce README

(Toutes les versions : page [Releases](https://github.com/samben80/WAVESOFT/releases). Les builds de chaque commit sont aussi disponibles dans l'onglet **Actions**, artefact **PhotoCompress-windows**, connexion GitHub requise.)

Contenu :
   - **`PhotoCompress.exe`** : version avec fenêtre (double-clic). Choisissez les dossiers, réglez la taille et le modèle de nom, puis cliquez sur **Lancer**. Le bouton **Aperçu des noms** montre le renommage sans rien écrire. Le bouton **Générer la liste Excel** crée un fichier `.xlsx` listant les photos du dossier de destination (nom avec extension, ex. `125-1E-D100-2B-3K-W BK.jpg`, puis nom sans extension, extension, taille et dimensions).
   - **`photocompress-cli.exe`** : version en ligne de commande, mêmes options que le script ci-dessous.

Au premier lancement, Windows SmartScreen peut afficher « Windows a protégé votre ordinateur », car l'exécutable n'est pas signé : cliquez sur **Informations complémentaires** → **Exécuter quand même**.

Pour publier une nouvelle version : onglet **Actions** → **Build Windows exe** → **Run workflow**, en indiquant un tag (ex. `v1.1`). Pousser un tag `v*` fonctionne aussi. Les fichiers sont joints automatiquement à la *Release*.

### Compiler soi-même (sous Windows)

```bash
pip install -r requirements.txt pyinstaller
pyinstaller --onefile --windowed --name PhotoCompress photocompress_gui.py
pyinstaller --onefile --console --name photocompress-cli photocompress.py
```

Les exécutables sont créés dans `dist/`.

## Installation (script Python)

```bash
pip install -r requirements.txt   # Pillow, openpyxl
```

## Utilisation

```bash
python photocompress.py <dossier_source> <dossier_destination> [options]
```

Exemples :

```bash
# Garde les noms d'origine
python photocompress.py photos/ sortie/

# Renomme en produit_001.jpg, produit_002.png...
python photocompress.py photos/ sortie/ --pattern "produit_{n:03}"

# 20261004_ete-a-paris.jpg (sans accents ni espaces)
python photocompress.py photos/ sortie/ --pattern "{date}_{name}" --slug

# Vérifier les nouveaux noms sans rien écrire
python photocompress.py photos/ sortie/ --pattern "produit_{n:03}" --dry-run
```

### Renommage (`--pattern`)

Le motif ne contient pas l'extension ; elle est ajoutée automatiquement.

| Variable | Valeur                                         |
|----------|------------------------------------------------|
| `{name}` | nom d'origine sans extension (après `--cut`) |
| `{n}`    | numéro (`{n:03}` → 001, 002…), départ `--start` |
| `{date}` | date du jour `AAAAMMJJ`                        |

**Suppression d'une fin de nom (`--cut`, champ « Supprimer à partir de » dans la fenêtre)** : par défaut, tout ce qui suit `_photo` (compris) est retiré du nom d'origine, sans tenir compte des majuscules. Ainsi `125-1E-D100-2B-3K-W BK_photo_1.jpg` devient `125-1E-D100-2B-3K-W BK.jpg`. Pour garder le nom complet : `--cut ""` (ou vider le champ).

Les fichiers sont numérotés dans l'ordre alphabétique. Si un nom existe déjà, un suffixe `_1`, `_2`… est ajouté (sauf avec `--overwrite`).

### Options

| Option | Rôle |
|--------|------|
| `--max-size 50` | taille max en Ko (1 Ko = 1000 octets, donc la limite tient aussi si elle est comptée en 1024) |
| `--pattern`, `--start`, `--slug` | renommage (voir ci-dessus) |
| `--format auto\|jpg\|png\|webp` | format de sortie (`auto` garde le format d'origine ; BMP/TIFF/GIF → JPEG, ou PNG s'il y a de la transparence) |
| `--keep-format` | ne jamais changer de format, même si la limite n'est pas atteinte |
| `--min-quality 10` | qualité JPEG/WebP minimale acceptée (1 à 95) |
| `-r`, `--recursive` | traite les sous-dossiers en conservant l'arborescence |
| `--workers N` | nombre d'images traitées en parallèle (défaut : nombre de cœurs) |
| `--excel liste.xlsx` | après traitement, crée un fichier Excel listant les photos du dossier de destination (nom avec extension, nom sans extension, extension, taille, dimensions) |
| `--dry-run` | affiche le plan de renommage sans écrire |

## Fonctionnement

La résolution (largeur × hauteur) n'est jamais modifiée. Seul l'encodage change :

- **JPEG / WebP** : recherche par dichotomie de la **meilleure qualité** qui passe sous la limite (JPEG progressif optimisé).
- **PNG** : d'abord compression sans perte ; si ce n'est pas suffisant, réduction de la palette (256 → 2 couleurs), transparence conservée. Si même 2 couleurs ne suffit pas, le fichier est converti en JPEG (en WebP s'il y a de la transparence), à condition que ça permette de passer sous la limite.
- Une image déjà sous la limite est **copiée telle quelle** (seul le nom change).
- Les métadonnées (EXIF, GPS, profil couleur) sont supprimées, ce qui fait gagner de la place. La rotation EXIF est appliquée avant, pour que les photos restent dans le bon sens.

## Limite importante

Sans réduire la résolution, 50 Ko n'est pas toujours atteignable. Une photo de smartphone de 12 mégapixels (4000×3000) très détaillée dépasse souvent 50 Ko, même en qualité JPEG minimale. Dans ce cas, le fichier est enregistré au plus petit possible, marqué **TROP GROS** dans le rapport, et le script se termine avec le code `2`. Vous pouvez alors :

- baisser `--min-quality` (au prix d'artefacts visibles) ;
- utiliser `--format webp`, en général 25 à 35 % plus léger que le JPEG à qualité égale ;
- augmenter `--max-size`.

Code de sortie : `0` si tout est OK, `2` si au moins un fichier dépasse la limite ou a échoué, `1` en cas d'erreur d'utilisation.

## Tests

```bash
pip install pytest
python -m pytest
```
