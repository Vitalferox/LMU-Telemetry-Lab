# Le Mans Ultimate — Guide Complet : Télémétrie & Setup

> **Base de connaissance pour agents IA (Claude Code / Cowork)**
> Dernière mise à jour : Avril 2026
> Jeu : **Le Mans Ultimate (LMU)** — Développeur : Studio 397 — Moteur : rFactor 2

---

## Table des matières

1. [Vue d'ensemble du jeu](#1-vue-densemble-du-jeu)
2. [Télémétrie dans LMU](#2-télémétrie-dans-lmu)
   - 2.1 [Télémétrie native en jeu](#21-télémétrie-native-en-jeu)
   - 2.2 [Architecture Shared Memory & plugins](#22-architecture-shared-memory--plugins)
   - 2.3 [Canaux de données disponibles](#23-canaux-de-données-disponibles)
   - 2.4 [Formats de fichiers](#24-formats-de-fichiers)
3. [Logiciels de télémétrie compatibles](#3-logiciels-de-télémétrie-compatibles)
   - 3.1 [MoTeC i2 (via DAMPlugin)](#31-motec-i2-via-damplugin)
   - 3.2 [SimHub](#32-simhub)
   - 3.3 [RaceLab Garage](#33-racelab-garage)
   - 3.4 [LMU Trace](#34-lmu-trace)
   - 3.5 [MyLMU App](#35-mylmu-app)
   - 3.6 [Crew Chief](#36-crew-chief)
   - 3.7 [SIM Dashboard](#37-sim-dashboard)
4. [Setup des voitures dans LMU](#4-setup-des-voitures-dans-lmu)
   - 4.1 [Philosophie générale du setup](#41-philosophie-générale-du-setup)
   - 4.2 [Aérodynamique](#42-aérodynamique)
   - 4.3 [Ride Height (Garde au sol)](#43-ride-height-garde-au-sol)
   - 4.4 [Suspensions](#44-suspensions)
   - 4.5 [Pneus (Tyres)](#45-pneus-tyres)
   - 4.6 [Freins (Brakes)](#46-freins-brakes)
   - 4.7 [Transmission & Différentiel](#47-transmission--différentiel)
   - 4.8 [Électronique (Electronics)](#48-électronique-electronics)
5. [Particularités par catégorie](#5-particularités-par-catégorie)
   - 5.1 [Hypercar (LMH & LMDh)](#51-hypercar-lmh--lmdh)
   - 5.2 [LMP2](#52-lmp2)
   - 5.3 [LMP3](#53-lmp3)
   - 5.4 [LMGT3 (GT3)](#54-lmgt3-gt3)
6. [Stratégie de setup : Sprint vs Endurance](#6-stratégie-de-setup--sprint-vs-endurance)
7. [Installation et gestion des setups](#7-installation-et-gestion-des-setups)
8. [Ressources communautaires et setups partagés](#8-ressources-communautaires-et-setups-partagés)
9. [Tableau récapitulatif des outils](#9-tableau-récapitulatif-des-outils)
10. [Sources](#10-sources)

---

## 1. Vue d'ensemble du jeu

**Le Mans Ultimate (LMU)** est un simulateur de course développé par Studio 397, basé sur le moteur physique de rFactor 2. Il est la simulation officielle du Championnat du Monde d'Endurance (WEC) et des 24 Heures du Mans.

**Catégories de voitures disponibles :**
- **Hypercar** : LMH (Le Mans Hypercar) et LMDh (Le Mans Daytona h) — ex. Toyota GR010, Ferrari 499P, Porsche 963, Cadillac V-Series.R, Lamborghini SC63, BMW M Hybrid V8
- **LMP2** : Oreca 07 (présent aux 24H Le Mans uniquement depuis 2024)
- **LMP3** : Ligier JS P320
- **LMGT3** : Classe GT3 introduite en 2024 — ex. BMW M4 GT3, McLaren 720S GT3, Ferrari 296 GT3, Lamborghini Huracán GT3 Evo2, Ford Mustang GT3

**Circuits disponibles :** Circuit de la Sarthe (24H Le Mans), Spa-Francorchamps, Monza, Portimão, Fuji, Bahrain, Imola, São Paulo, Qatar, Lusail, COTA, etc.

---

## 2. Télémétrie dans LMU

### 2.1 Télémétrie native en jeu

Depuis la version **1.2 de LMU**, le jeu intègre un système de **télémétrie native en jeu**. Voici comment l'activer :

**Activation de l'enregistrement télémétrique :**
1. Aller dans **Settings → Gameplay** pour activer l'enregistrement automatique
2. Ou assigner un bouton via **Controls → Bindings** (aucun bouton assigné par défaut)
3. En piste, le texte **"Telemetry recording started"** apparaît au-dessus du compte-tours (côté droit)

**HUD intégré (sans outil externe) :**
- Affichage en temps réel des températures et usures pneus
- Monitoring thermique des freins et du moteur
- Prédiction de consommation carburant
- Comparaison de delta en temps réel

**Affichages télémétriques in-game :**
LMU dispose de plusieurs **pages d'écrans de setup** accessibles pendant la course via le MFD (Multi-Function Display) qui offrent un readout télémétrique simplifié : températures pneus, pressions, brake bias, consommation, etc.

---

### 2.2 Architecture Shared Memory & plugins

LMU hérite de l'architecture de rFactor 2 basée sur la **Shared Memory** (mémoire partagée Windows). Voici les sous-systèmes disponibles :

| Sous-système | Contenu |
|---|---|
| `General` | Infos générales de session |
| `DMR` | Données moteur en temps réel |
| `Telemetry / MappedBuffer` | Buffer principal télémétrie voiture |
| `Scoring` | Classements, positions, gaps |
| `Rules` | Règlement en cours (FC, SC, etc.) |
| `ForceFeedback` | Données force de retour volant |
| `Graphics` | Infos rendu graphique |
| `Weather` | Météo, température piste/air |
| `Extended` | Données étendues supplémentaires |
| `InputBuffer` | Inputs joueur (throttle, brake, steering) |

**Plugin Shared Memory requis :**

```
Fichier : LMU_SharedMemoryMapPlugin64.dll
Destination : Steam\steamapps\common\Le Mans Ultimate\Plugins\
```

> **Note importante :** En raison d'accords de confidentialité avec les constructeurs automobiles, Studio 397 est contractuellement obligé de **restreindre certaines sorties télémétriques** pour protéger les données techniques propriétaires. Cela affecte principalement les données de pneus et d'aéro pour les voitures sous licence.

---

### 2.3 Canaux de données disponibles

LMU enregistre **des centaines de paramètres** tout au long d'une session. Voici les canaux principaux classés par catégorie :

#### Moteur & Propulsion

| Canal | Description | Unité |
|---|---|---|
| `RPM` | Régime moteur | tr/min |
| `Throttle` | Position accélérateur | % (0–100) |
| `Gear` | Rapport engagé | - |
| `FuelConsumption` | Consommation carburant | L/lap |
| `FuelRemaining` | Carburant restant | L |
| `EngineTemp` | Température moteur | °C |
| `RevLimiter` | État limiteur de régime | bool |

#### Inputs conducteur

| Canal | Description | Unité |
|---|---|---|
| `Brake` | Pression pédale de frein | % (0–100) |
| `Clutch` | Position embrayage | % |
| `SteeringAngle` | Angle volant | degrés |
| `Throttle` | Position accélérateur | % |

#### Pneus (Tyres)

| Canal | Description | Unité |
|---|---|---|
| `TyreTemp_FL/FR/RL/RR` | Températures pneus (surface) | °C |
| `TyreTempCore_*` | Températures pneus (cœur) | °C |
| `TyrePressure_*` | Pression pneus | kPa / PSI |
| `TyreWear_*` | Usure pneus | % |
| `TyreSlipAngle_*` | Angle de glissement | degrés |
| `TyreSlipRatio_*` | Ratio de patinage | % |

> **Important :** Les canaux pneus ne sont **pas toujours disponibles** pour toutes les voitures en raison des accords de licence avec les fabricants de pneus.

#### Freinage

| Canal | Description | Unité |
|---|---|---|
| `BrakeTemp_*` | Température disques | °C |
| `BrakePressure_*` | Pression hydraulique | bar |
| `BrakeBias` | Balance de freinage avant/arrière | % avant |
| `BrakeMigration` | Bias actif (Hypercar) | % |

#### Aérodynamique & Ride Height

| Canal | Description | Unité |
|---|---|---|
| `RideHeight_Front` | Garde au sol avant | mm |
| `RideHeight_Rear` | Garde au sol arrière | mm |
| `Downforce` | Force d'appui total | N |
| `Drag` | Traînée aérodynamique | N |

#### Suspension & Châssis

| Canal | Description | Unité |
|---|---|---|
| `SuspTravel_*` | Débattement suspension | mm |
| `DamperVelocity_*` | Vitesse amortisseur | m/s |
| `LateralG` | Accélération latérale | G |
| `LongitudinalG` | Accélération longitudinale | G |
| `VerticalG` | Accélération verticale | G |
| `YawRate` | Vitesse de lacet | rad/s |

#### Données environnementales

| Canal | Description |
|---|---|
| `TrackTemp` | Température piste |
| `AmbientTemp` | Température ambiante |
| `WindSpeed` / `WindDirection` | Vent |

---

### 2.4 Formats de fichiers

| Format | Outil associé | Localisation |
|---|---|---|
| `.duckdb` | Télémétrie native LMU (v1.2+) | `Documents\Le Mans Ultimate\Telemetry\` |
| `.ld` | MoTeC i2 (via DAMPlugin) | Dossier configurable via DAMPlugin.INI |
| `.csv` | Export universel | `Documents\Le Mans Ultimate\Telemetry\` |
| Shared Memory | SimHub, RaceLab, etc. | RAM (temps réel uniquement) |

> **Format DuckDB :** Le fichier `.duckdb` est une base de données analytique columnar. Des outils communautaires permettent de le convertir en CSV ou d'autres formats pour l'analyse.

---

## 3. Logiciels de télémétrie compatibles

### 3.1 MoTeC i2 (via DAMPlugin)

**MoTeC i2** est le standard professionnel de l'analyse télémétrique en motorsport. Compatible avec LMU via le **DAMPlugin**.

#### Installation du DAMPlugin

```
1. Télécharger DAMPlugin (lien communauté OverTake / forum LMU)
2. Copier DAMPlugin.dll → Steam\steamapps\common\Le Mans Ultimate\Plugins\
3. Vérifier le fichier DAMPlugin.INI pour activer la collecte de données
4. Configurer CustomPluginVariables.JSON avec DAMPlugin activé
```

**Fichier CustomPluginVariables.JSON :**
```json
{
  "DAMPlugin": {
    "Enabled": 1,
    "LogPath": "C:\\Users\\[User]\\Documents\\Le Mans Ultimate\\MoTeC\\"
  }
}
```

#### Configuration DAMPlugin.INI (paramètres clés)

```ini
[General]
Enabled=1
LogPath=.\

[Channels]
Engine=1
Suspension=1
Tyres=1
Aero=1
Driver=1
```

#### Workspace MoTeC pour rFactor 2 / LMU

Un workspace MoTeC communautaire est disponible sur GitHub (`orion-miller/RFactor2-Motec-Workspace`) incluant :
- Plots préconfigurés pour la plupart des canaux disponibles
- **Math channels** calculés (ex. : balance freinage effective, température pneu normalisée)
- Vues comparaison de tours (overlay)
- Track map avec données superposées

**Canaux disponibles dans le workbook :**
- Throttle, Brake, Clutch, Steering traces
- RPM, Gear, Speed
- G-forces (X, Y, Z)
- Suspension travel et damper velocity
- Températures pneus et freins (quand disponibles)
- Fuel consumption

> **Limite :** Les canaux pneus et aéro ne sont **pas populés pour toutes les voitures** en raison des restrictions de licence.

#### Workflow d'analyse MoTeC

1. Lancer LMU avec DAMPlugin actif
2. Réaliser la session (QF ou Race)
3. Ouvrir MoTeC i2 → File → Open → sélectionner le fichier `.ld`
4. Comparer les tours : **Overlay** de deux tours pour identifier les gains
5. Analyser les zones clés : freinage (trace brake vs speed), throttle application, G-forces

---

### 3.2 SimHub

**SimHub** est l'outil le plus populaire en simracing pour les dashboards, overlays, effets haptiques et télémétrie.

#### Installation du plugin SimHub pour LMU

```
Fichier plugin 1 : LMU_SharedMemoryMapPlugin64.dll
  → Steam\steamapps\common\Le Mans Ultimate\Plugins\

Fichier plugin 2 : Redadeg.lmuDataPlugin.dll
  → Dossier d'installation SimHub\
```

> **Auteurs du plugin :** @redadeg, avec contributions de Temur et carux (communauté LMU)

#### Lancer LMU avec SimHub

```
1. Lancer SimHub EN PREMIER
2. Lancer LMU ensuite
3. SimHub détecte automatiquement LMU via shared memory
```

#### Dashboards SimHub disponibles pour LMU

Des dashboards précis pour **chaque voiture** de LMU sont disponibles, incluant :

| Dashboard | Compatibilité | Fonctionnalités |
|---|---|---|
| **AFX Dashboard** | Toutes classes (GTE, LMGT3, LMP3, LMP2, LMP2+, Hypercar) | Toutes infos essentielles |
| **LMU NeoSuperDash** | Toutes classes | Vue claire et lisible |
| **WEC Graphics Pack 2024** | Toutes classes | Style broadcast officiel WEC |
| **WEC Leaderboards 2025** | Multi-classe | Classements style TV |

**Téléchargement :** [lmu-dashboards.com](https://lmu-dashboards.com) et [OverTake.gg](https://www.overtake.gg/downloads/categories/le-mans-ultimate.260/)

#### Configuration LMU Electronic Bridge (SimHub)

Pour synchroniser les electronics (TC, ABS, BB, etc.) entre SimHub et LMU :

```
Dans SimHub → LMU Electronic Bridge :
- Assigner chaque valeur électronique au même bouton que dans LMU
- TC increase/decrease → même bouton que contrôleur
- BB adjust → même mapping que MFD LMU
- REGEN → même bouton volant
```

> Paramètres synchronisables : TC, TC CUT, TC SLIP, ABS, BB (Brake Bias), MAP (moteur), REGEN, BMIG (Brake Migration), FARB (Front ARB), RARB (Rear ARB)

---

### 3.3 RaceLab Garage

**RaceLab** est une suite d'overlays et de télémétrie moderne pour simracers.

#### Installation RaceLab pour LMU

```
1. Fermer LMU et RaceLab complètement
2. Télécharger RacelabLMUPlugin.dll
3. Copier dans : steamapps\common\Le Mans Ultimate\Plugins\
4. Ouvrir CustomPluginVariables.json et activer le plugin
5. Lancer RaceLab EN PREMIER, puis LMU
```

#### Configuration in-game

```
Display : Borderless Window
Vertical Sync : Video
```

#### Overlays disponibles

- Relative (positions proches)
- Standings (classement complet)
- Radar (détection proximité voitures)
- Fuel (gestion carburant)
- Custom overlays (création personnalisée)

**Compatibilité résolution :** Simple écran, triple écran, ultrawide

---

### 3.4 LMU Trace

**LMU Trace** ([lmutrace.com](https://www.lmutrace.com)) est un outil de télémétrie web spécialement conçu pour Le Mans Ultimate.

**Fonctionnalités :**
- Analyse télémétrique dédiée LMU
- Comparaison de tours
- Visualisation des données de conduite
- Interface web accessible sans installation lourde

**Fréquence d'échantillonnage :** 200 samples/seconde pour les données principales

---

### 3.5 MyLMU App

**MyLMU** ([mylmu.app](https://www.mylmu.app)) est un tracker de télémétrie et de statistiques dédié à LMU.

**Fonctionnalités :**
- Suivi des performances par session
- Historique des temps au tour
- Statistiques par voiture et par circuit
- Analyse des tendances de performance

---

### 3.6 Crew Chief

**Crew Chief** est un spotter/ingénieur vocal pour simulateurs de course.

#### Installation Crew Chief pour LMU

> **Important :** Il faut désinstaller la version standard de Crew Chief et installer la **version bêta** spécifique LMU.

```
1. Désinstaller Crew Chief actuel
2. Télécharger la version bêta Crew Chief (forum LMU / Steam)
3. Installer dans le même drive que LMU/Steam
4. Au premier lancement : accepter l'installation automatique des plugins LMU
```

**Vérification que ça fonctionne :**
- Crew Chief affiche : "Plugin launch successfully"
- Puis : "Initialised with Le Mans Ultimate shared memory"

#### Fonctionnalités

- Appels de gap en temps réel ("Car behind, 2 seconds")
- Alertes d'incidents et drapeaux
- Informations stratégie (tours restants, carburant)
- Coaching vocal de base (freinage, tracé)
- Compatible toutes catégories LMU

---

### 3.7 SIM Dashboard

**SIM Dashboard** (Stryder-IT) permet d'utiliser un smartphone ou une tablette comme tableau de bord secondaire.

**Configuration pour LMU :**
```
Application PC : SIM Dashboard (stryder-it.de)
Application mobile : SIM Dashboard (iOS / Android)
Connexion : WiFi réseau local
Port : configurable
```

---

## 4. Setup des voitures dans LMU

### 4.1 Philosophie générale du setup

> "Un setup compétitif ne se concentre jamais sur un seul paramètre — il représente un équilibre entre toutes les zones, avec des interactions complexes. Les changements doivent être faits de manière incrémentale et évalués soigneusement."

**Localisation des fichiers setup :**
```
Steam\steamapps\common\Le Mans Ultimate\UserData\player\Settings\<NomCircuit>\
```

**Format de fichier :** `.svm` (Setup Values Map)

**Principes fondamentaux :**
- Chaque paramètre interagit avec les autres
- Les tooltips in-game sont détaillés et fiables — les lire avant de modifier
- Partir des setups par défaut (Coach Dave defaults inclus dans le jeu)
- Modifier **un paramètre à la fois** pour isoler son effet
- Différencier setup **qualif** (agressif) vs setup **course** (endurance-friendly)

---

### 4.2 Aérodynamique

L'aéro dans LMU comprend le réglage des **wings** (ailes avant et arrière) et des **dive planes** selon les voitures.

| Paramètre | Effet | Impact |
|---|---|---|
| **Front Wing** (downforce ↑) | Meilleur turn-in, rotation précise | Possible instabilité arrière à haute vitesse |
| **Front Wing** (downforce ↓) | Moins de résistance aéro | Sous-virage plus marqué |
| **Rear Wing** (downforce ↑) | Plus de stabilité, moins de rotation | Perte de vitesse en ligne droite |
| **Rear Wing** (downforce ↓) | Meilleure vitesse de pointe | Arrière plus instable |

**Balance aéro :**
- Plus d'aéro avant → Turn-in précis mais perte de stabilité arrière
- Plus d'aéro arrière → Voiture sécurisante mais rotation difficile
- L'objectif est un **équilibre neutre** adapté au profil du circuit

**Circuits haute vitesse** (Monza, Le Mans lignes droites) : aéro minimum
**Circuits techniques** (Spa, Portimão) : aéro élevé pour l'appui mécanique

---

### 4.3 Ride Height (Garde au sol)

Le **ride height** est particulièrement critique pour les Hypercars.

| Paramètre | Trop bas | Trop haut |
|---|---|---|
| **Ride Height Avant** | Bottom-out dans les zones rapides → déstabilisation | Perte massive de downforce |
| **Ride Height Arrière** | Frottements diffuseur, rebonds | Moins d'appui, plus de drag |

**Règles pratiques :**
- **Hypercars :** Quelques millimètres font une différence énorme — très sensibles
- **GT3 :** Moins sensibles, mais une bonne garde au sol reste essentielle pour freinage et stabilité
- Modifier le ride height affecte directement les springs (précharge) et donc l'équilibre global
- Toujours vérifier que le ride height reste stable en conditions dynamiques (vitesse, carburant plein)

---

### 4.4 Suspensions

#### Springs (Ressorts)

| Réglage | Effet |
|---|---|
| **Ressort avant + rigide** | Direction plus vive, moins de roulis, moins de grip en virage |
| **Ressort avant + souple** | Réduction du sous-virage, meilleur grip avant |
| **Ressort arrière + rigide** | Plus de rotation à l'accélération |
| **Ressort arrière + souple** | Meilleur grip arrière, plus stable mais moins de rotation |

#### Anti-Roll Bars (Barres stabilisatrices)

| Réglage | Effet |
|---|---|
| **ARB avant + rigide** | Poussée vers le sous-virage |
| **ARB avant + souple** | Plus de rotation, moins stable |
| **ARB arrière + rigide** | Plus de rotation mais imprévisible sur les vibreurs |
| **ARB arrière + souple** | Arrière plus doux, meilleur pour l'endurance |

> **Conseil endurance :** Des ARB légèrement plus souples donnent un équilibre plus drivable sur 100 tours consécutifs.

#### Dampers (Amortisseurs)

| Canal | Fonction |
|---|---|
| **Bump (Compression)** | Contrôle la vitesse d'écrasement de la suspension (vibreurs, transfert de masse) |
| **Rebound (Détente)** | Contrôle la vitesse de retour de la suspension après compression |
| **Slow Bump/Rebound** | Mouvements lents (transfert de masse en virage) |
| **Fast Bump/Rebound** | Impacts rapides (vibreurs, bosses) |

#### Camber (Carrossage)

- LMU requiert des valeurs de carrossage **plus faibles** que d'autres simulations (ex. ACC)
- **Objectif :** Distribution uniforme de la température de surface — intérieur/milieu/extérieur
- Trop de carrossage négatif → dégradation thermique rapide

#### Toe (Pincement)

| Zone | Réglage | Effet |
|---|---|---|
| **Avant toe-in ↑** | Plus de stabilité haute vitesse | Plus de frottement/chauffage pneu |
| **Avant toe-out** | Turn-in plus réactif | Moins de frottement, moins stable |
| **Arrière toe-in ↑** | Meilleure stabilité à l'entrée de virage | Moins de rotation |
| **Arrière toe-in ↓** | Plus de rotation mid-corner | Moins stable à l'entrée |

---

### 4.5 Pneus (Tyres)

#### Pression

> "Always run the lowest pressures — raise them and you lose lap time."

- Partir des pressions **minimales** et ajuster si nécessaire
- Les pressions basses donnent une meilleure empreinte de contact et une adhérence plus prévisible
- Les pressions augmentent naturellement avec la chaleur en course

#### Température optimale par zone

- **Intérieur ≈ Milieu ≈ Extérieur** → camber et toe corrects
- Intérieur >> Extérieur → trop de camber négatif
- Extérieur >> Intérieur → pas assez de camber négatif

#### Compounds

- **Soft :** Grip maximal, dégradation rapide — pour qualifications courtes
- **Hard :** Meilleure longévité, moins de grip instantané — pour relais long en endurance
- **Mixed strategy :** Possible en endurance selon les règlements

---

### 4.6 Freins (Brakes)

#### Brake Bias

| Réglage | Effet |
|---|---|
| **Bias avant ↑** | Stabilité maximale au freinage, tendance sous-virage à l'entrée |
| **Bias arrière ↑** | Meilleure rotation, risque de blocage roues arrière |

- Typiquement entre **54–62% avant** selon la voiture et le circuit
- Adjustable en temps réel via le MFD

#### Brake Migration (Hypercars uniquement)

- Bias actif qui avance automatiquement quand la pédale atteint **100% d'appui**
- **Réglage optimal recommandé (2025) : 2.5F** pour tous les Hypercars

---

### 4.7 Transmission & Différentiel

| Paramètre | Réglage ↑ | Réglage ↓ |
|---|---|---|
| **Acceleration Lock** | Meilleure traction en sortie | Meilleure rotation virages lents |
| **Deceleration Lock** | Plus stable au freinage | Plus de rotation à l'entrée |
| **Preload** | Différentiel plus "engagé" | Comportement plus neutre |

---

### 4.8 Électronique (Electronics)

#### TC — Trois facettes

| Paramètre | Plage |
|---|---|
| **TC** | 1–11 |
| **TC Power Cut** | 1–11 |
| **TC Slip Angle** | 1–11 |

**Hypercar :** TC 9–11, TC Power Cut 1–2, TC Slip 9–11
**LMGT3 :** Plus élevé pneus froids, descendre progressivement

#### ABS (LMGT3 uniquement) — 9 positions

| Positions | Effet |
|---|---|
| 1–3 | ABS plus efficace à l'avant |
| 4–6 | Équilibré |
| 7–9 | Stabilité arrière, risque sous-virage |

#### Regen Level (Hypercars)

- Mettre au **maximum** pour maximiser la décélération régénérative
- **Ne jamais atteindre 100% batterie** → perte du freinage régénératif

---

## 5. Particularités par catégorie

### 5.1 Hypercar (LMH & LMDh)

**Setup priorities :** Ride height → Brake Migration 2.5F → Regen max → TC élevé / TC Power Cut faible → Aéro

**Voitures :** Toyota GR010, Ferrari 499P, Porsche 963, Cadillac V-Series.R, Lamborghini SC63, BMW M Hybrid V8, Peugeot 9X8

**Spécificités :**
- Système hybride : NRG (énergie disponible), regen en freinage, motor maps
- Très sensibles au ride height (quelques mm = secondes par tour)
- Brake Migration critique pour la stabilité au freinage
- TC Power Cut doit rester bas (1–2) pour ne pas perdre d'énergie hybride

### 5.2 LMP2

Oreca 07 — fenêtre thermique pneus précise, aéro conventionnelle, différentiel important pour virages lents.

**Spécificités :**
- Pas de système hybride
- Pneus Michelin — fenêtre de température étroite
- Plus sensible au différentiel que les Hypercars

### 5.3 LMP3

Ligier JS P320 — moins sensible à l'aéro, principes généraux s'appliquent.

**Spécificités :**
- Voiture d'entrée de gamme prototype
- Bonne option pour apprendre la physique LMU/rF2

### 5.4 LMGT3

**Voitures :** BMW M4 GT3, McLaren 720S GT3 Evo, Ferrari 296 GT3, Lamborghini Huracán GT3 Evo2, Ford Mustang GT3, Porsche 911 GT3 R (992), Corvette Z06 GT3.R, Alpine A110 GT3 Evo

**Setup priorities :** Pneus → ARB → Différentiel → ABS/TC

**Spécificités :**
- ABS à 9 positions (ajustable)
- Chaque voiture a sa propre personnalité de conduite
- Plus accessibles que les Hypercars pour débuter

---

## 6. Stratégie de setup : Sprint vs Endurance

| Paramètre | Qualif/Sprint | Endurance |
|---|---|---|
| Ride height | Plus bas | Légèrement relevé |
| ARB | Plus rigides | Plus souples |
| Springs | Plus rigides | Plus souples |
| Brake Bias | Plus arrière | Plus avant |
| TC | Plus bas | Plus élevé |
| Tyres | Soft | Hard |
| Différentiel Accel | Plus fermé | Plus ouvert |
| Aéro | Agressif (selon circuit) | Conservateur |

> "Un setup endurance, c'est ce que vous pouvez répéter 100 fois de suite sans fatigue physique ou dégradation excessive."

---

## 7. Installation et gestion des setups

```
Dossier setups :
Steam\steamapps\common\Le Mans Ultimate\UserData\player\Settings\<NomCircuit>\

Format : .svm (Setup Values Map)
Nommage recommandé : [Voiture]_[Circuit]_[Type]_[Version].svm
  ex : Ferrari499P_LeMans_Race_v3.svm
```

**Installation d'un setup téléchargé :**
1. Copier le fichier `.svm` dans le dossier du circuit correspondant
2. Lancer LMU → Garage → Setup → Load
3. Sélectionner le setup importé

**GitHub communautaire :** `seralaci/Le-Mans-Ultimate-Setups`

---

## 8. Ressources communautaires et setups partagés

### Ressources officielles
- **Guide officiel :** [guide.lemansultimate.com](https://guide.lemansultimate.com)
- **Forum officiel :** [community.lemansultimate.com](https://community.lemansultimate.com)
- **Discord officiel :** [discord.com/invite/lemansultimate](https://discord.com/invite/lemansultimate) (~56 000 membres)
- **Wiki :** [lemansultimate.wiki.gg](https://lemansultimate.wiki.gg)

### Sites de setups

| Site | Type | Prix |
|---|---|---|
| Coach Dave Academy | Setups officiels + coaching | Gratuit (defauts) / Payant (premium) |
| Coach Dave Delta | Setups pro mis à jour | Abonnement payant |
| GO Setups | Setups compétitifs | Payant |
| SimRacingSetup.com | Setups communautaires | Gratuit/Payant |
| UltimateSetupHub.com | Setups partagés | Gratuit |
| GitHub seralaci | Setups open-source | Gratuit |
| OverTake.gg | Mods + setups + guides | Gratuit |

### Coaching et analyse
- **Virtual Racing School (VRS)** — coaching données télémétrie
- **Traxion.gg** — articles, guides setup, news LMU
- **SimRacingCockpit.gg** — tutoriels setup
- **RaceControl.gg** — analyses performance

### Communautés Reddit
- [r/Le_Mans_Ultimate](https://www.reddit.com/r/Le_Mans_Ultimate/) — communauté dédiée LMU
- [r/simracing](https://www.reddit.com/r/simracing/) — simracing général

### Chaînes YouTube recommandées
- Coach Dave Academy
- Jimmy Broadbent (LMU content)
- OverTake TV
- Jardier (setup analysis)

---

## 9. Tableau récapitulatif des outils

| Outil | Type | Gratuit | Difficulté | Cas d'usage |
|---|---|---|---|---|
| Télémétrie native LMU | In-game | ✅ | Facile | Monitoring temps réel basique |
| SimHub | Dashboard/Overlay/Haptic | ✅ | Moyen | Dashboard, effets haptiques |
| MoTeC i2 | Analyse post-session | ✅ (base) | Difficile | Analyse pro, comparaison tours |
| RaceLab | Overlay + télémétrie | Freemium | Moyen | Overlays modernes, stream |
| LMU Trace | Analyse web | ✅ | Facile | Analyse télémétrie sans install |
| MyLMU App | Stats tracker | ✅ | Facile | Suivi progression |
| Crew Chief | Spotter vocal | ✅ | Facile | Co-pilote/ingénieur vocal |
| SIM Dashboard | Dashboard mobile | ✅ | Facile | 2ème écran sur smartphone |
| Coach Dave Delta | Setups pro | 💲 | N/A | Setups compétitifs prêts à l'emploi |
| GO Setups | Setups pro | 💲 | N/A | Setups haute performance |

---

## 10. Sources

1. Guide officiel Le Mans Ultimate — guide.lemansultimate.com
2. Wiki LMU — lemansultimate.wiki.gg
3. Forum officiel LMU — community.lemansultimate.com
4. Coach Dave Academy — coachdaveacademy.com
5. OverTake.gg — overtake.gg/downloads/categories/le-mans-ultimate.260/
6. Traxion.gg — articles setup LMU 2024/2025
7. GitHub orion-miller/RFactor2-Motec-Workspace
8. GitHub seralaci/Le-Mans-Ultimate-Setups
9. RaceControl.gg — LMU setup guides
10. SimRacingSetup.com — LMU car setup explained
11. lmu-dashboards.com — dashboards SimHub LMU
12. mylmu.app
13. lmutrace.com
14. Reddit r/Le_Mans_Ultimate — threads setup/télémétrie
15. Discord officiel LMU — channels #setup et #telemetry
16. Studio 397 patch notes v1.2 (télémétrie native)
17. SimHub documentation plugin LMU (@redadeg)
18. RaceLab documentation LMU plugin
19. Crew Chief beta release notes LMU
20. DAMPlugin documentation (rFactor 2 / LMU)

---

*Document généré en avril 2026 — Basé sur LMU v1.2+ — Pour usage comme base de connaissance agent IA*
*Ce document est destiné à être utilisé par Claude Code ou tout autre agent IA pour répondre à des questions sur la télémétrie et les setups dans Le Mans Ultimate.*
