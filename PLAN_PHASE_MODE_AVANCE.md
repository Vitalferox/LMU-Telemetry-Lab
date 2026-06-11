# Plan — Phase « Mode Avancé » (canaux DAMPlugin / Engineer Mode)

> Rédigé le 2026-06-11. Autoportant : exécutable par une nouvelle session sans contexte préalable.
> Pré-requis : lire `CLAUDE.md` (racine) pour l'état du projet. La V1 standard est fonctionnelle.

## Objectif

Exposer dans l'app les ~57 canaux « ingénieur setup » que seul le DAMPlugin enregistre
(absents du `.duckdb` natif du jeu) : Tyre Load, Grip Fract, carrossage/pincement **dynamiques**,
forces pneus Lat/Long, downforce/drag, body pitch/roll, ride heights 4 roues, températures
rubber I/C/O, susp forces. Mêmes types de graphes que les canaux existants, dans de nouveaux
onglets, derrière un toggle « Engineer Mode ».

## Découverte clé (vérifiée dans le code le 11/06)

**Le backend est déjà transparent aux nouveaux canaux.** `fuse_session_data`
([telemetry_service.py:730](backend/app/services/telemetry_service.py)) lit TOUTES les tables du
.duckdb génériquement (hors skip-list ligne ~710), et `GET /sessions/{id}/telemetry`
([endpoints.py:1166](backend/app/api/endpoints.py)) sérialise TOUTES les colonnes du dataframe.
Idem côté frontend : `TelemetryChart` lit `telemetryData[channel]` génériquement, et la barre
d'onglets ([App.tsx:1186](frontend/src/App.tsx)) **masque déjà automatiquement** les catégories
dont aucun canal n'est présent dans `telemetryData`.

→ Conséquence : aucun changement dans le pipeline fusion/API. Le travail réel est :
(1) faire écrire les canaux par le plugin, (2) les mapper dans le convertisseur .ld,
(3) les afficher dans de nouveaux onglets.

---

## Étape 0 — Discovery : noms exacts des canaux .ld (≈30 min, OBLIGATOIRE en premier)

Le mapping actuel de `ld_converter.py` (`_SINGLE_MAP`/`_MULTI_MAP`, lignes 193–281) ne couvre
PAS les canaux exclusifs DAMPlugin — ils sont silencieusement ignorés à la conversion.
Les noms proposés plus bas sont des hypothèses : **les vérifier avant de coder**.

1. Ajouter une option CLI `--list-channels` à `ld_converter.py` qui dump
   `nom | unité | freq | scale/dec/mul/shift` de tous les canaux d'un .ld (le décodage
   d'en-têtes existe déjà dans le fichier).
2. L'exécuter sur un .ld DAMPlugin **complet** (245 canaux). Si aucun n'existe encore
   (les 28 archives de `F:\SteamLibrary\...\Le Mans Ultimate\LOG\` n'ont que 70 canaux),
   faire l'étape 1 d'abord, rouler une session courte, puis revenir ici.
3. Diff avec les tables du .duckdb natif (189 canaux) → liste définitive des ~57 exclusifs.
   Sauver le résultat dans `docs/damplugin_channels.md` (créer le dossier).

## Étape 1 — Réactivation du plugin : `DAMPluginManager` (≈1 h)

État : DLL présente dans `<LMU>\Plugins\` mais ABSENTE de `Bin64\Plugins\` (la MAJ jeu du
11/06 l'a supprimée). `CustomPluginVariables.JSON` a toujours `"DAMPlugin.dll": {"Enabled": 1}`.
Assets complets dans `F:\Claude Code\LMU Setup\damplugin\` (DLL 330 Ko + DAMPlugin.ini + PluginData/Sounds).

1. **Copier les assets** dans le repo : `backend/assets/damplugin/` (DLL + ini + PluginData).
   Ajouter `backend/assets/damplugin/` au `.gitignore` (DLL tierce, pas à committer).
2. **Porter** `F:\Claude Code\LMU Setup\src\lmu_ri\dam_plugin.py` (classe `DAMPluginManager`,
   complète et fonctionnelle : activate copie DLL→`Bin64\Plugins\` + PluginData + ini, patch
   du JSON ; deactivate inverse tout) vers `backend/app/services/dam_plugin.py`.
   **Adapter `find_lmu_root()`** : remplacer la détection registre par la logique
   `detect-lmu-path` existante ([endpoints.py:199](backend/app/api/endpoints.py),
   libraryfolders.vdf + scan lecteurs) — la factoriser en fonction partagée.
3. **Endpoints** dans `endpoints.py` :
   - `GET /system/damplugin/status` → `{installed: bool, enabled_in_json: bool, ini_present: bool, lmu_root: str}`
   - `POST /system/damplugin/activate` / `POST /system/damplugin/deactivate`
4. **Survie aux MAJ du jeu** : le statut est vérifié à chaque ouverture du FileManager (étape 4).
   Si la DLL a disparu de `Bin64\Plugins\` alors qu'Engineer Mode est actif → bannière
   « Plugin supprimé par une MAJ du jeu — Réinstaller » avec bouton one-click.

## Étape 2 — Extension du convertisseur .ld (≈2 h)

Fichier : `backend/app/services/ld_converter.py`. Étendre les mappings avec les canaux
découverts à l'étape 0. Noms de tables cibles à créer (convention CamelCase de l'app,
cf. `FrontRideHeight`, `TyresTempCentre`) :

| Canal .ld (à confirmer étape 0) | Table .duckdb | Type |
|---|---|---|
| `Tyre Load {w}` | `TyreLoad` | multi-roues (N,4) |
| `Grip Fract {w}` (ou `Tire Grip Fract`) | `GripFract` | multi-roues |
| `Camber {w}` (dynamique) | `CamberDyn` | multi-roues |
| `Toe {w}` (dynamique, si présent) | `ToeDyn` | multi-roues |
| `Tyre Lat Force {w}` | `TyreLatForce` | multi-roues |
| `Tyre Long Force {w}` | `TyreLongForce` | multi-roues |
| `Downforce Front` / `Rear` | `DownforceFront` / `DownforceRear` | simple |
| `Drag` | `Drag` | simple — **fix** : actuellement mal mappé sur `Brakes Force` (ligne 231, « closest available ») |
| `Body Pitch` / `Body Roll` | `BodyPitch` / `BodyRoll` | simple |
| `Tyre Rubber Temp {w} I` / `O` | `TyresRubberTempInner` / `TyresRubberTempOuter` | multi-roues (le `C` existe déjà → `TyresRubberTemp`) |

- Multi-roues : réutiliser `_build_multi_channel` (format `value1..value4` FL/FR/RL/RR).
- Vérifier les unités (forces en N, angles en deg ou rad → documenter dans `docs/damplugin_channels.md`).
- `Susp Force` et `RideHeights` 4 roues sont DÉJÀ mappés (lignes 278–279).
- **Validation** : convertir un .ld 245 canaux, ouvrir le .duckdb, vérifier présence des
  nouvelles tables + plausibilité (Tyre Load ~3000–6000 N en appui, camber négatif en virage chargé…).

## Étape 3 — Workflow d'ingest : .ld prioritaire quand plugin actif (≈1 h)

Quand le plugin tourne, chaque session produit DEUX fichiers : le `.duckdb` natif (189 canaux,
`UserData\Telemetry\`) et le `.ld` DAMPlugin (245 canaux, superset, dossier `LOG\` — vérifier
la destination réelle dans `DAMPlugin.ini`). Pas de merge nécessaire : **le .ld converti
remplace avantageusement le natif.**

1. Étendre `POST /sessions/import-ld` ([endpoints.py:463](backend/app/api/endpoints.py)) :
   scanner AUSSI le dossier .ld du plugin (chemin déduit de `DAMPlugin.ini` ou paramètre).
2. Règle de dédup quand .ld et .duckdb natif couvrent la même session (même piste + horodatage
   à ±2 min) : préférer le .ld converti ; nom canonique identique `{Track}_{R|P|Q}_{ISO}.duckdb`.
3. Réponse de l'import : indiquer combien de sessions sont « advanced » (canaux DAMPlugin présents).

## Étape 4 — Frontend : onglets Engineer + toggle (≈3 h)

1. **`telemetryStore.ts`** : étendre `ChartCategory`
   ([telemetryStore.ts:289](frontend/src/store/telemetryStore.ts)) avec
   `'TyresPro' | 'Aero' | 'Chassis'` et ajouter dans `CATEGORY_CHART_CONFIGS` :
   - **TyresPro** : TyreLoad ×4 (ou merged 4-roues), GripFract (merged), TyresCarcassTemp ×4,
     Rubber I/C/O (composite par roue, même pattern que `TireHeat` qui combine
     Inside/Centre/Outside — cf. [TelemetryChart.tsx:370](frontend/src/components/TelemetryChart.tsx)),
     TyreLatForce / TyreLongForce (merged).
   - **Aero** : DownforceFront, DownforceRear, Drag, RideHeights F/R (réutilise l'existant),
     éventuellement ratio aéro calculé front/(front+rear).
   - **Chassis** : BodyPitch, BodyRoll, Susp Force ×4 (table déjà convertie), CamberDyn ×4, ToeDyn ×4.
2. **`App.tsx`** : ajouter les 3 onglets dans `availableTabs` (ligne ~1181 : `TYRES PRO`,
   `AERO`, `CHASSIS`). Le filtre existant par présence de données fait le reste — les onglets
   n'apparaissent que si la session contient les canaux. Conditionner EN PLUS au toggle Engineer Mode.
3. **`SettingsOverlay.tsx`** : toggle « Engineer Mode (DAMPlugin) », persisté dans le store
   (même mécanisme que `speedUnit`). Défaut : ON (les onglets restent invisibles sans données,
   donc sans coût pour une session standard).
4. **`FileManager.tsx`** : carte statut DAMPlugin (installé/absent + boutons Install/Remove
   via les endpoints de l'étape 1) + badge « ADV » sur les sessions qui ont les canaux avancés.
5. **`types.ts` / `client.ts`** : types et appels API correspondants.
6. Si un graphe composite inédit est nécessaire (Rubber I/C/O par roue), suivre le pattern
   des cas spéciaux existants dans `TelemetryChart.tsx` (`TireHeat`, `RideHeights`, `HandlingMerged`).

## Étape 5 — Validation E2E (≈1 h + une session de jeu)

1. `POST /system/damplugin/activate` → vérifier DLL dans `Bin64\Plugins\`, ini, JSON patché.
2. Rouler une session courte LMU (practice, 3-4 tours).
3. Importer via « Import LMU Telemetry Folder » → la session « advanced » doit gagner les
   3 nouveaux onglets ; les sessions natives anciennes ne changent pas.
4. Vérifier graphes : Tyre Load qui charge en appui, camber dynamique cohérent, downforce ∝ v².
5. Tester le toggle OFF → onglets masqués. Tester une archive .ld 70 canaux → onglets
   partiels (seuls les canaux présents).

## Risques / pièges connus

- **MAJ du jeu** : supprime la DLL de `Bin64\Plugins\` (constaté le 11/06). Couvert par le
  check de statut (étape 1.4). Ne JAMAIS stocker l'unique copie de la DLL dans l'arbre du jeu.
- **Noms de canaux .ld** : hypothèses jusqu'à l'étape 0. Ne pas coder les mappings avant.
- **Archives 70 canaux** : ne contiennent qu'un sous-ensemble — l'UI doit dégrader proprement
  (déjà le cas grâce au filtre par présence de données).
- **Susp Pos en mm→m** (`_MULTI_SCALE`) : vérifier si les nouveaux canaux ont besoin du même
  genre de conversion d'unité.
- Quirks .ld existants (GPS monde, secteurs parfois absents) : voir CLAUDE.md, inchangés.

## Hors scope (phase suivante : Coach IA)

Le coach Claude API exploitera ces canaux (ex. analyse carrossage via distribution des temps
pneus I/C/O) — prévu en dernier, ne rien anticiper ici à part garder les noms de tables stables.

## Ordre d'exécution recommandé

Étape 1 (plugin) → session de jeu courte → Étape 0 (discovery sur le .ld frais) →
Étape 2 (convertisseur) → Étape 3 (ingest) → Étape 4 (UI) → Étape 5 (E2E).
Total estimé : ~8 h de dev + 2 sessions de jeu de test.
