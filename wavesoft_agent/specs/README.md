# Fiches techniques Wavesoft

Déposer ici les fiches techniques de l'éditeur (PDF, Word, Excel) décrivant
les intégrations de données : un fichier par flux (articles, tiers, tarifs,
pièces commerciales, écritures…).

Chaque fiche sera traduite en une définition structurée (un fichier par flux)
qui précise :

- le format du fichier attendu par Wavesoft (séparateur, encodage, en-tête, format des dates et des nombres) ;
- la liste ordonnée des colonnes, avec la table et la colonne Wavesoft cible ;
- les règles éditeur (valeurs autorisées, codes à créer avant, calculs) ;
- l'ordre de chargement entre flux (ex. familles avant articles).

Chaque définition est vérifiée contre le catalogue de la base réelle
(`catalog.json`) : colonne absente, longueur dépassée ou colonne obligatoire
non couverte sont signalées avant toute génération de fichier.
