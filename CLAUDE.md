---
name: project-lmu-telemetry-lab
description: Projet télémétrie LMU — fork LMU-Telemetry-Lab + convertisseur .ld→DuckDB (fait) + coach IA Claude (à venir). Le jeu écrit des .duckdb natifs compatibles.
metadata: 
  node_type: memory
  type: project
  originSessionId: cd60d857-ef56-4e41-a1e4-39bb6c19c5c3
---

# LMU Telemetry Lab — état au 2026-06-11 (soir)

**Objectif** : analyser la télémétrie Le Mans Ultimate dans le fork de [LMU-Telemetry-Lab](https://github.com/rabbit20031225/LMU-Telemetry-Lab) (MIT, TrackMap 3D React/Three.js que Thierry adore), avec ensuite un coach IA Claude pour l'aider à régler la voiture.

## ⚠️ CORRECTION IMPORTANTE (2026-06-11)

**Le jeu écrit À NOUVEAU des `.duckdb` natifs**, directement au schéma de l'app. Vérifié empiriquement : session Interlagos du 11/06 → `Autódromo José Carlos Pace_P_2026-06-11T16_04_32Z.duckdb` (101 tables, metadata, GPS Time, Lap… 100% compatible, zéro conversion). L'ancienne note « il ne produit PLUS de .duckdb depuis mai » était un état transitoire du jeu : la MAJ récente a restauré le .duckdb et **supprimé les anciens .ld du dossier Telemetry**. Un `config.json` dans `Telemetry\` liste les canaux enregistrés (noms = schéma de l'app, pas de réglage de format).

**Conséquence** : les nouvelles sessions s'importent telles quelles. Le convertisseur .ld sert uniquement aux archives.

## Architecture

- **Sources** : `F:\SteamLibrary\steamapps\common\Le Mans Ultimate\UserData\Telemetry\` (.duckdb natifs, nouvelles sessions) + `...\LOG\` (28 anciens .ld DAMPlugin 70 canaux, archives — convertibles).
- **Post-session replay** d'abord (pas de live/shared memory en V1).
- **Coach IA** (PROCHAINE GROSSE ÉTAPE, à faire en dernier selon Thierry) : Claude API (Sonnet 4.6 envisagé, ~0,15-0,30 €/analyse, prompt caching sur la base de connaissance), provider derrière une interface pour pouvoir brancher OpenRouter/Ollama plus tard. Panneau chat React + endpoint FastAPI. Réutiliser agents + RAG de l'ancien projet.

## Installation ✅ (faite, fonctionnelle)

- Fork cloné : `F:\Claude Code\LMU-Telemetry-Lab`
- venv Python 3.12 dans `.venv` : fastapi, uvicorn[standard], duckdb, pandas, numpy, python-multipart, pyarrow (pas de requirements.txt dans le repo — déduit des imports)
- `frontend/index.html` recréé (manquait dans le repo, oubli de commit du dev)
- **Lancement : `start_dev.bat` à la racine** (backend uvicorn 127.0.0.1:8000 + vite 5173, CORS limité à 5173/3000)
- Sessions de l'app : `C:\Users\GAMER\AppData\Local\LMU_Telemetry_Lab_Dev\Data\guest\DuckDB_data\` (+ cache parquet dans `...\guest\cache\`)
- Testé OK 2D+3D avec la session native `Circuit de Spa-Francorchamps_R_2026-04-21T21_31_59Z.duckdb` (Aston GT3 #27, 2:21.761)

## Convertisseur .ld → .duckdb ✅ (fait et validé le 2026-06-11)

- `backend/app/services/ld_converter.py` — conversion complète, validée sur natif (245 canaux) ET DAMPlugin (70 canaux). Formule : `valeur = raw/scale × 10^-dec × mul + shift`. Susp Pos converti mm→m. CLI : `python ld_converter.py <file.ld> [out.duckdb]`.
- Endpoints : upload accepte .duckdb + .ld (conversion auto), `POST /sessions/import-ld` (batch d'un dossier, dédup), `GET /system/detect-lmu-path` (auto-détection via libraryfolders.vdf de Steam).
- Frontend : bouton « Import LMU Telemetry Folder » dans FileManager, drag-and-drop .ld, auto-détection du chemin si invalide.
- Noms canoniques en sortie : `{Track}_{R|P|Q}_{ISO-date}.duckdb` (format parsé par le frontend).
- Quirks connus : GPS = coordonnées monde du jeu (pas géographiques, rendu OK), secteurs s1/s2 parfois manquants, voir mémoire [[project-ld-converter]].

## Phase « Mode Avancé » — DAMPlugin (EN COURS — gros du travail fait le 2026-06-11 tard)

Plan : [PLAN_PHASE_MODE_AVANCE.md](PLAN_PHASE_MODE_AVANCE.md). Référence canaux : [docs/damplugin_channels.md](docs/damplugin_channels.md).

**Fait ✅** :
- **Plugin réinstallé à la main** : DLL dans `Bin64\Plugins\`, PluginData à la racine du jeu, tous les groupes Extra de `DAMPlugin.ini` à 1 (245 canaux). `Active on startup=1` → logging auto, Ctrl+M pour toggler en jeu. Logs dans `<LMU>\LOG\` (.ld + .ldx + copie .svm du setup).
- **Discovery** : .ld frais Spa P1 du 11/06 23h30 → 245 canaux, noms exacts + unités dans `docs/damplugin_channels.md`.
- **Convertisseur** (commit `9e2a564`) : 23 nouveaux canaux mappés → tables `TyreLoad`, `GripFract`, `CamberDyn`, `ToeDyn`, `TyreLatForce/LongForce`, `VertTyreDeflection`, `BrakePressure`, `TyresRubberTempInner/Outer`, `BodyPitch/Roll`, `DownforceFront/Rear`, `Drag` (bug « Brakes Force » corrigé), `EngineTorque`, `FrontWingHeight`, `BodyRot*`, `Motor*`. Validé : 23/23 tables, valeurs physiques OK.
- **Frontend** (commit `1d4a9a4`) : onglets **TYRES PRO / AERO / CHASSIS** + toggle **Engineer Mode** (Off/DAMPlugin) dans Settings, état `engineerMode` persisté. Onglets visibles seulement si mode ON **et** canaux présents dans la session. `tsc --noEmit` passe.

**Reste à faire ⏳** :
1. **Test visuel E2E** (priorité) : importer `<LMU>\LOG\2026-06-11 - 23-30-00 - Circuit de Spa-Francorchamps - P1.ld`, activer Engineer Mode, vérifier onglets + graphes.
2. Étape 3 du plan : scan du dossier `LOG\` dans `/sessions/import-ld` + dédup .ld-prioritaire (même session = .ld 245 canaux remplace le .duckdb natif 189).
3. Étape 1 du plan : endpoints `GET /system/damplugin/status` / activate / deactivate + bannière « plugin supprimé par MAJ jeu » (moins urgent, plugin actif).
4. Le coach IA exploitera ces canaux (ex. analyse carrossage via température rubber I/C/O).

## Briques réutilisables de l'ancien projet `F:\Claude Code\LMU Setup`

- `src/lmu_ri/svm_parser.py` — parseur setups .svm (pour le coach IA)
- Agents IA (coach/setup/télémétrie/stratège), RAG ChromaDB
- Docs : `LMU_Telemetrie_Setup.md` (base de connaissance setup) + `LMU_Analyse_Software_Conception.md`

Voir [[user-thierry]] et [[feedback-rythme-prototypage]].
