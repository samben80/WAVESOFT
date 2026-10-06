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
            │ 5. Automate        │  automate.py : script SQL ws_sp_add_tache_automate
            │   (fait)           │  (une tâche par pièce), à relire et exécuter ;
            └─────────┬──────────┘  suivi des tâches en lecture seule
            ┌─────────▼──────────┐
            │ 6. Contrôles base  │  codes clients, articles, natures, dépôts…
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
| `integration.py` + `integration.json` | intégration directe en base depuis Excel : familles, articles, clients, fournisseurs, produits, pièces de vente et d'achat |
| `automate.py` | tâches de l'Automate de transferts : script SQL à relire, lecture de l'état dans `WSAUTOMATE` |
| `cli.py` | commandes `discover`, `summary`, `search`, `describe`, `formats`, `template`, `build`, `check-file`, `automate`, `automate-etat` |
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

## Confier le fichier à l'Automate de transferts

L'agent n'écrit jamais dans la base. `automate` contrôle le fichier puis écrit
un script SQL qui appelle la procédure éditeur `ws_sp_add_tache_automate`
(FTC005), une tâche par pièce ou par commande isolée comme le demande le guide
Automate. Le script s'exécute en une transaction (tout ou rien) et se termine
par la liste des `TRSID` créés.

```
python -m wavesoft_agent automate FTC002 import_ventes.txt        # → import_ventes.automate.sql
python -m wavesoft_agent automate-etat --ids 1817 1818            # suivi (lecture seule)
python -m wavesoft_agent automate-etat --erreurs                  # tâches en erreur et leur message
```

L'entité (`TRSENTITE`) est déduite du format : 99 ventes, 199 achats, 299
stock, 400 EDI, 24 et 28 pour les traitements de pièces ; `--entite` la force
pour les autres imports, et `--profil` donne le profil d'I/E quand le format
n'est pas fixe. Le script passe `@TRSISTCP = 'N'` car la valeur par défaut de la
procédure (`'O'`) réserve la tâche à un automate en mode serveur TCP ; `--tcp`
rétablit ce mode.

## Intégration directe dans la base

Pour écrire directement dans le dossier, sans passer par les fichiers d'import
ni l'Automate. Ordre fixe : familles d'articles, articles, clients,
fournisseurs, produits, pièces de vente, pièces d'achat.

```
python -m wavesoft_agent discover --out sortie/                           # catalogue du dossier
python -m wavesoft_agent integration-verifier sortie/catalog.json         # tables cibles présentes ?
python -m wavesoft_agent integration-modele sortie/catalog.json           # classeur à remplir
python -m wavesoft_agent integrer sortie/catalog.json donnees.xlsx        # simulation, demande les natures
python -m wavesoft_agent integrer sortie/catalog.json donnees.xlsx --nature-vente FACHISTO --nature-achat FACFOU --executer
```

- Les colonnes du classeur sont celles des tables du dossier (catalogue) ; les
  références se saisissent par code (FAMILLE, CLIENT, ARTICLE…). Une famille
  vide prend la famille `DEFAULT`.
- Chaque identifiant vient de `ws_sp_GetIdTable` (FTC005) et la ligne de la
  table paramétrable `<TABLE>_P` est créée avec lui.
- Tout le classeur est contrôlé avant d'écrire, puis écrit dans une seule
  transaction. Sans `--executer`, tout est exécuté (contraintes et triggers
  compris) puis annulé. Avec `--executer`, il faut retaper le nom de la base.
- Une fiche dont le code existe déjà est laissée telle quelle ; une pièce
  existante ne reçoit jamais de lignes.
- Les noms de tables de `integration.json` marqués `a_confirmer` viennent des
  fiches et guides : `integration-verifier` les compare au catalogue réel.
- Limite : une insertion directe ne déclenche pas les calculs de l'ERP. Pour
  les pièces, totaux, TVA, échéances, stock et comptabilisation ne sont pas
  recalculés. À valider sur une copie du dossier.
