# Batman OS

[![CI](https://github.com/rodrigogvieira98/batman-os/actions/workflows/ci.yml/badge.svg)](https://github.com/rodrigogvieira98/batman-os/actions/workflows/ci.yml) [![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE) ![Python](https://img.shields.io/badge/python-3.11%2B-blue)

**Gobernanza de ingeniería como sistema operativo.** 283 reglas deterministas que
auditan un repositorio entero — seguridad, infraestructura, calidad, deuda técnica,
datos — sin llamar a un modelo de lenguaje. El LLM entra al final, solo cuando la regla
determinista no decide.

🇧🇷 [Português](README.md) · 🇪🇸 **Español** · 🇺🇸 [English](README.en.md)

---

### ⚠️ Qué es este repositorio, y qué no es

Esta es una **vitrina curada**, no un espejo del repositorio de trabajo. La
diferencia importa para quien lee el código:

* **Lo que está aquí es el método**: la especificación (39 capítulos, 18 ADRs, 6
  adendas), el motor de scan con sus 283 specs de regla, el kernel, el runtime,
  las capabilities, la gobernanza de alerta y sus pruebas.
* **El monitor de infraestructura no se publica.** El sistema corre en
  producción contra una VPS real, cada 5 minutos, y el conjunto de reglas vivo
  lleva los **umbrales** — cuántas respuestas 404 caracterizan un barrido,
  cuántas rutas de sonda disparan alerta, qué reglas siguen en observación.
  Publicarlo sería publicar el mapa de cómo pasar por debajo del radar de esa
  máquina.
* **Lo que hay aquí de `observe/` es un corte del 2026-08-25** y no se mantiene:
  fue publicado antes de que la política anterior fuera escrita. Léelo como
  historia, no como lo que corre hoy.

El deploy, los runbooks, el backlog y el programa de respuesta a incidentes
también quedan fuera — un runbook saneado sigue enseñando la topología, y el
backlog narra incidentes reales con fecha.

*Actualizado el 2026-09-22.*


> "Un sistema inteligente no es el que responde todas las preguntas. Es el que reduce
> continuamente la cantidad de preguntas que hay que hacer."

| | |
|---|---|
| **176 commits**, autor único | **1.690 pruebas**, ejecutadas por el CI de este repositorio |
| **283 especificaciones** de regla | `mypy --strict` y `ruff` limpios, bloqueantes en CI |
| **39 capítulos** de especificación escritos antes del código | corre en producción contra un producto real, cada 5 minutos |

## Por qué esto no es un linter

Un linter responde *"¿esta línea está mal?"*. Batman OS responde *"¿este repositorio se
está desarrollando de una forma que va a fallar?"* — y trata su propia respuesta como
algo que puede estar mintiendo.

```mermaid
flowchart TB
    R["repositorio objetivo"] --> S["batman scan --root REPO"]
    S --> E["283 reglas deterministas<br/>seguridad · infra · calidad · deuda · datos"]
    E --> D{"¿la regla decide?"}
    D -->|sí| V["hallazgo con severidad,<br/>evidencia y ruta"]
    D -->|"no decide"| L["escalada al LLM<br/>el último recurso, nunca el primero"]
    L --> V
    V --> K{"¿marcador cero?"}
    K -->|sí| CAN["el canario REPRUEBA<br/>el silencio no es aprobación"]
    K -->|no| FO{"--fail-on high"}
    FO -->|"hay high o critical"| X["salida 1 — el CI bloquea"]
    FO -->|"ninguno"| OKN["salida 0"]

    classDef det fill:#e0e7ff,stroke:#4f46e5,color:#1e1b4b
    classDef gate fill:#fef3c7,stroke:#d97706,color:#78350f
    classDef bad fill:#fee2e2,stroke:#dc2626,color:#7f1d1d
    classDef ok fill:#dcfce7,stroke:#16a34a,color:#14532d
    classDef base fill:#f8fafc,stroke:#94a3b8,color:#0f172a
    class E,V det
    class D,K,FO gate
    class CAN,X bad
    class OKN ok
    class R,S,L base
```

<sub>El LLM es el <b>último</b> paso, nunca el primero — y el canario corre antes del código de salida, porque <code>0 hallazgos</code> y <code>todo bien</code> son indistinguibles.</sub>

Dos defectos reales que el proyecto encontró **en sí mismo**, y las defensas que
nacieron de ellos:

**1. La compuerta no veía lo que se había commiteado.** `pytest`, `mypy` y `ruff` corren
sobre el *árbol de trabajo*. Un archivo importado por el código y nunca versionado pasa
los cuatro — y revienta en el primer clon limpio. Pasó dos veces. La defensa
(`scripts/verificar_head_autocontido.sh`) materializa el HEAD en un worktree efímero y
exige que `(tipos, especificaciones)` coincida con el árbol.

**2. "Cero hallazgos" y "todo bien" son indistinguibles.** Una ejecución en CI enumeró
1.382 archivos, reportó `0 hallazgo(s)` y salió con código 0 — porque el paquete se
publicó sin las 283 especificaciones, y un `glob` sobre un directorio inexistente no
lanza error. Una compuerta que solo mira el código de salida habría dado verde. La
defensa es un canario que **reprueba el marcador cero**: el silencio dejó de ser
aprobación.

Esa es la tesis del proyecto entero — **el fallo silencioso es el enemigo**. Un proceso
que se cuelga es peor que uno que descarta, porque el descarte aparece en el informe y
el cuelgue parece lentitud.

```mermaid
flowchart LR
    subgraph local["Compuerta local — pre-push"]
        direction TB
        T1["pytest"] --> T2["mypy src/ tests/"] --> T3["ruff check + ruff format --check"] --> T4["verificar_head_autocontido.sh<br/>materializa el HEAD en un<br/>worktree efímero"]
    end
    subgraph ci["Compuerta en CI"]
        direction TB
        S1["los mismos cuatro"] --> S2["batman scan --fail-on high"] --> S3["canario: marcador cero REPRUEBA"]
    end
    local -->|"solo empuja si pasa"| ci

    classDef step fill:#e0e7ff,stroke:#4f46e5,color:#1e1b4b
    classDef guard fill:#fef3c7,stroke:#d97706,color:#78350f
    class T1,T2,T3,S1,S2 step
    class T4,S3 guard
    style local fill:#f8fafc,stroke:#cbd5e1,color:#0f172a
    style ci fill:#f8fafc,stroke:#cbd5e1,color:#0f172a
```

## Arquitectura de un vistazo

```
docs/spec/       especificación (39 capítulos, 10 volúmenes, 17 ADRs) — gana al código
                 en caso de divergencia, por regla explícita
src/batman_os/
  foundation/    tipos del glosario oficial (Mission, Capability, Operator, ...)
  kernel/        Mission Runtime, Planning/Decision/Workflow Engine, Event Bus, Scheduler
  runtime/       Capability/Execution Engine, Operational Memory (SQLite), concurrencia
  capabilities/  operadores, certificación, skills, tools — y las 283 reglas migradas
  workflow/      misiones formales, playbooks multi-paso, recuperación y fallback
  learning/      Knowledge Graph, evolución de regla/workflow, aprendizaje operacional
  governance/    Governance Engine, Human Review, escalada a LLM, observabilidad
  api/           API HTTP (FastAPI) — el tenant se deriva de la clave, nunca del cuerpo
tests/           1 archivo por capítulo, nombrado por los criterios de aceptación
```

**El aislamiento multi-tenant es estructural**, en lectura y en mutación — no un
`WHERE tenant_id` esparcido por las consultas. El tenant aplicado a una Misión viene
100% de la clave de API usada; no existe endpoint que acepte el tenant como campo del
payload.

## Ejecutar

```bash
python3.11 -m venv .venv
pip install -e ".[dev]"

pytest                                      # pruebas de aceptación
mypy src/
ruff check src/ tests/

batman scan --root <repo>                   # audita un repositorio real
batman scan --root <repo> --fail-on high    # salida 1 si hay hallazgo high/critical
```

> La especificación, la documentación y los comentarios están en portugués — es el
> idioma en que el sistema fue diseñado. Los identificadores de código (clases,
> funciones, variables) están en inglés. El [README en portugués](README.md) tiene el
> detalle completo: convenciones de nomenclatura, la API HTTP y la compuerta de CI.

---

<sub>MIT © 2026 Rodrigo Gomes Vieira</sub>
