# Agent intégrateur Wavesoft

Prépare les fichiers d'intégration de données client dans l'ERP Wavesoft,
en s'appuyant sur la structure réelle de la base et sur les fiches techniques
de l'éditeur.

Hypothèse de départ : la base Wavesoft tourne sur **Microsoft SQL Server**
(connexion ODBC). L'agent ne fait que **lire** la base : il n'écrit jamais
dedans, il produit des fichiers que l'on importe ensuite par les outils
prévus par Wavesoft.

## Architecture

```
            ┌────────────────────┐
 Base SQL ──▶ 1. Découverte      │  discovery.py  → catalog.json / .md / .xlsx
 Wavesoft   │   (fait)           │
            └─────────┬──────────┘
                      │ catalogue (tables, vues, procédures, fonctions,
                      │ triggers, clés, FK, contraintes, dépendances)
            ┌─────────▼──────────┐
 Fiches ────▶ 2. Définitions     │  formats.py + définitions JSON privées
 techniques │   de formats       │  (FTC002 ventes, FTC007 achats, FTC011 divers,
 éditeur    │   (fait)           │   FTC022 EDI, FTC004 imports spéciaux…)
            └─────────┬──────────┘
            ┌─────────▼──────────┐
 Données ───▶ 3. Modèle Excel    │  excel_io.py : un onglet par type de ligne
 client     │   et contrôles     │  (E, AF, LA…), contrôle colonne par colonne
            │   (fait)           │  avec renvoi à la feuille et la ligne Excel
            └─────────┬──────────┘
            ┌─────────▼──────────┐
            │ 4. Fichier         │  importfile.py : fichier .txt au format éditeur
            │   d'import (fait)  │  (séparateur ; ou tab, ANSI), prêt pour la
            └─────────┬──────────┘  Gestion ou l'Automate de transfert
            ┌─────────▼──────────┐
            │ 5. Contrôles base  │  codes clients, articles, natures, dépôts…
            │   (à venir)        │  vérifiés contre la base réelle
            └────────────────────┘
```

| Module | Rôle |
|---|---|
| `connection.py` | paramètres de connexion (fichier `.env` ou variables d'environnement), connexion ODBC en lecture seule |
| `discovery.py` | requêtes sur les vues système `sys.*` pour reconnaître tous les objets de la base |
| `catalog.py` | modèle du catalogue, sérialisé en JSON pour travailler hors connexion |
| `export.py` | dictionnaire de données Markdown et classeur Excel (onglets Objets, Colonnes, Paramètres) |
| `formats.py` | définitions de formats d'import (types d'enregistrements, colonnes, règles) |
| `importfile.py` | lecture, contrôle et écriture des fichiers d'import Wavesoft |
| `excel_io.py` | modèle Excel à remplir et génération du fichier d'import depuis ce modèle |
| `cli.py` | commandes `discover`, `summary`, `search`, `describe`, `formats`, `template`, `build`, `check-file` |
| `specs/` | où placer les définitions de formats (privées, hors dépôt) |

Ce que la découverte relève pour chaque objet :

- **tables et vues** : colonnes (type exact, longueur, nullable, identité, calculée, valeur par défaut, collation, description), clé primaire, index uniques, clés étrangères, contraintes CHECK, nombre de lignes ;
- **procédures et fonctions** : paramètres (type, sortie, valeur par défaut) et code SQL ;
- **triggers** : table concernée, événements (INSERT/UPDATE/DELETE), actif ou non — important car un import peut déclencher des traitements Wavesoft ;
- **synonymes et séquences** ;
- **dépendances** : quels objets lisent ou écrivent quelles tables (ex. procédures d'import existantes).

Chaque colonne est marquée **obligatoire** quand elle doit être fournie par
un fichier d'intégration : non nullable, sans valeur par défaut, ni identité
ni calculée.

## Installation

```
pip install -r wavesoft_agent/requirements.txt
```

Il faut aussi le pilote Microsoft « ODBC Driver 18 for SQL Server » (ou 17,
via `WAVESOFT_DRIVER`).

## Compte SQL conseillé

Un compte dédié, en lecture seule :

```sql
CREATE LOGIN wavesoft_agent WITH PASSWORD = '...';
USE [BASE_WAVESOFT];
CREATE USER wavesoft_agent FOR LOGIN wavesoft_agent;
ALTER ROLE db_datareader ADD MEMBER wavesoft_agent;
GRANT VIEW DEFINITION TO wavesoft_agent;  -- pour lire le code des vues et procédures
```

Sans `VIEW DEFINITION`, la découverte fonctionne mais le code SQL des objets
n'est pas lu (un avertissement le signale).

## Utilisation

Créer un fichier `.env` (ignoré par git) :

```
WAVESOFT_SERVER=SERVEUR\INSTANCE
WAVESOFT_DATABASE=NOM_DE_LA_BASE
WAVESOFT_USER=wavesoft_agent
WAVESOFT_PASSWORD=...
# WAVESOFT_TRUST_CERT=yes   si le certificat du serveur est auto-signé
```

Laisser `WAVESOFT_USER` vide pour l'authentification Windows.

```
python -m wavesoft_agent discover --out sortie
python -m wavesoft_agent summary  sortie/catalog.json
python -m wavesoft_agent search   sortie/catalog.json "ARTICLE|ART_"   # noms d'objets et de colonnes
python -m wavesoft_agent search   sortie/catalog.json IMPORT --code    # cherche aussi dans le code SQL
python -m wavesoft_agent describe sortie/catalog.json dbo.NOM_TABLE --code
```

Le dossier `sortie/` contient des informations sur la base du client : il est
ignoré par git et ne doit pas être publié.

## Préparer un fichier d'intégration

Les définitions de formats sont privées (voir `specs/README.md`) : les placer
dans `wavesoft_agent/specs/formats/` ou indiquer leur dossier par
`WAVESOFT_SPECS`.

```
python -m wavesoft_agent formats                                  # formats disponibles
python -m wavesoft_agent template FTC002 --records E,AF,AL,LA,NO  # modèle Excel à remplir
python -m wavesoft_agent build FTC002 modele_FTC002.xlsx --out import_ventes.txt
python -m wavesoft_agent check-file FTC002 fichier_existant.txt   # contrôle d'un fichier reçu
```

Dans le modèle, `PIECE` regroupe les lignes d'une même pièce et `ORDRE` ordonne
les lignes (un `LD` ou `AN` reprend l'`ORDRE` de la ligne article qu'il
complète). `build` n'écrit aucun fichier tant qu'il reste une erreur ; chaque
anomalie indique la feuille et la ligne Excel à corriger. Le fichier produit
s'importe par la Gestion (Traitement des pièces) ou par l'Automate de transfert.
