# DAMPlugin — Liste complète des canaux .ld (245 canaux)

> Extrait le 2026-06-11 d'un .ld frais Spa Practice 100% extras activés.
> Source : `2026-06-11 - 23-30-00 - Circuit de Spa-Francorchamps - P1.ld` (28 Mo)

## Canaux exclusifs DAMPlugin (absents du .duckdb natif)

### Setup — priorité haute

| Canal .ld                    | Unité | Freq  | Table DuckDB cible    | Type       |
|------------------------------|-------|-------|-----------------------|------------|
| `Body Pitch`                 | rad   | 100Hz | `BodyPitch`           | simple     |
| `Body Roll`                  | rad   | 100Hz | `BodyRoll`            | simple     |
| `Front Downforce`            | N     | 100Hz | `DownforceFront`      | simple     |
| `Rear Downforce`             | N     | 100Hz | `DownforceRear`       | simple     |
| `Drag`                       | N     | 100Hz | `Drag`                | simple     |
| `Delta Best`                 | s     | 100Hz | `DeltaBest`           | simple     |
| `Engine Torque`              | N.m   | 100Hz | `EngineTorque`        | simple     |
| `Front Wing Height`          | mm    | 100Hz | `FrontWingHeight`     | simple     |
| `Camber FL/FR/RL/RR`         | rad   | 100Hz | `CamberDyn`           | multi (×4) |
| `Toe FL/FR/RL/RR`            | rad   | 100Hz | `ToeDyn`              | multi (×4) |
| `Tyre Load FL/FR/RL/RR`      | N     | 100Hz | `TyreLoad`            | multi (×4) |
| `Grip Fract FL/FR/RL/RR`     | %     | 100Hz | `GripFract`           | multi (×4) |
| `Lat Force FL/FR/RL/RR`      | N     | 100Hz | `TyreLatForce`        | multi (×4) |
| `Long Force FL/FR/RL/RR`     | N     | 100Hz | `TyreLongForce`       | multi (×4) |
| `Vertical Tyre Deflection FL/FR/RL/RR` | mm | 100Hz | `VertTyreDeflection` | multi (×4) |
| `Brake Pressure FL/FR/RL/RR` | %     | 100Hz | `BrakePressure`       | multi (×4) |
| `Tyre Rubber Temp {w} I`     | °C    | 100Hz | `TyresRubberTempInner`| multi (×4) |
| `Tyre Rubber Temp {w} O`     | °C    | 100Hz | `TyresRubberTempOuter`| multi (×4) |

### Hybrid/Moteur électrique

| Canal .ld        | Unité | Table DuckDB cible | Type   |
|------------------|-------|--------------------|--------|
| `Motor RPM`      | rpm   | `MotorRPM`         | simple |
| `Motor Torque`   | N.m   | `MotorTorque`      | simple |
| `Motor State`    | —     | `MotorState`       | simple |
| `Motor Water Temp` | °C  | `MotorWaterTemp`   | simple |

### Corps de la voiture (rotation/accélérations)

| Canal .ld              | Unité    | Table DuckDB cible | Type   |
|------------------------|----------|--------------------|--------|
| `Local Rotation X`     | rad/s    | `BodyRotX`         | simple |
| `Local Rotation Y`     | rad/s    | `BodyRotY`         | simple |
| `Local Rotation Z`     | rad/s    | `BodyRotZ`         | simple |
| `Local Rot Accel X`    | rad/s²   | `BodyRotAccelX`    | simple |
| `Local Rot Accel Y`    | rad/s²   | `BodyRotAccelY`    | simple |
| `Local Rot Accel Z`    | rad/s²   | `BodyRotAccelZ`    | simple |

### Canaux à ignorer (non pertinents pour l'analyse setup)

- `Dent Severity 1–8` — dommages bodywork
- `Driver Type`, `Game Phase`, `Flag`, `Ignition State`, `Start Light`
- `Lat/Long/Ground/Patch Vel FL/FR/RL/RR` — vitesses relatives contact pneu (très spécialisé)
- `Wheel Y Location FL/FR/RL/RR` — position verticale roue (redondant avec Susp Pos)
- `Terrain Idx FL/FR/RL/RR` — index terrain
- `Laps Behind Leader/Next`, `Num Penalties/Pitstops/Red Lights`, `Place`
- `Max Straight Speed`, `Min Corner Speed`, `Realtime Loss` — stats session
- `Beacon`, `Marker` — marqueurs
- `Tyre Flat FL/FR/RL/RR` — bool crevaison (rare en practice)
- `Session Elapsed Time`, `Lap Start Elapsed Time`, `Lap Number` — déjà couverts
- `Raining` — déjà dans `Min Path Wetness`

## Correction bug existant

`Drag` (unit=N) était mappé sur `Brakes Force` dans `_SINGLE_MAP` ligne 231 — **à corriger**.

## Canaux déjà mappés dans ld_converter.py

Multi-roues : `Susp Pos`, `Brake Temp`, `Tyre Pressure`, `Tyre Temp {w} Centre/Inner/Outer`,
`Tyre Carcass Temp`, `Tyre Rubber Temp {w} C`, `Tyre Wear`, `Wheel Rot Speed`,
`Wheel Detached`, `Ride Height`, `Susp Force`, `Surface Type`.

Single : tous les canaux Driver + Weather + GPS + Fuel + Hybrid base.
