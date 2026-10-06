# Fiches techniques Wavesoft et définitions de formats

Les fiches techniques de l'éditeur (PDF) ne sont **pas** stockées dans ce dépôt,
qui est public : elles sont la propriété de Wavesoft.

L'agent lit des **définitions de formats** (un fichier JSON par flux), tirées
de ces fiches. Elles sont gardées hors du dépôt, dans le dossier
`wavesoft_agent/specs/formats/` (ignoré par git) ou dans le dossier indiqué par
la variable `WAVESOFT_SPECS` / l'option `--specs`.

Le schéma d'une définition est décrit en tête de `wavesoft_agent/formats.py`.

Formats transcrits à ce jour (fiches v26.00.01) :

| Code | Flux | Fiche |
|---|---|---|
| FTC002 | Pièces de vente (entête, adresses, lignes, commandes ET/EP/EI/CLO/SUP…) | FTC002 v42 |
| FTC002-TRAITEMENT | Validation et transformation des pièces de vente (V, TR) | FTC002 v42 |
| FTC007 | Pièces d'achat | FTC007 v21 |
| FTC007-TRAITEMENT | Validation et transformation des pièces d'achat (V, TR) | FTC007 v21 |
| FTC011 | Pièces divers (stock, fabrication) | FTC011 v8 |
| FTC022-CDE | Import EDI des commandes de vente | FTC022 v26 |
| FTC004-* | Imports spéciaux : lignes de pièces, réception, inventaire, articles/client, APE, jours fériés | FTC004 v12 |

Règles éditeur appliquées par l'agent (FTC010, FTC005) :

- ne jamais modifier un objet Wavesoft (tables, vues, procédures, fonctions) ;
- objets ajoutés : tables `EXT_…`, vues `V_EXT_…`, procédures `EXT_SP_…`, triggers `EXT_…` ;
- les triggers spécifiques sur les pièces se posent sur `PIECEVENTES_VALIDATION`,
  `PIECEACHATS_VALIDATION`, `PIECEDIVERS_VALIDATION` (déclenchés en fin de traitement).
