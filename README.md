# Contratos de Nubeduca (v1)

Contratos del MVP: 16 eventos y las APIs de los 6 microservicios. Es la salida de la tarea **ARQ-01** y lo que usan LIB-01 (plantilla de servicio) y cada microservicio. Ningún microservicio publica un evento ni expone una ruta que no esté aquí.

```text
nubeduca-contratos/
├── EVENT_STORMING.md          # flujos del MVP: comando → evento → reacción
├── docs/                      # diagrama de arquitectura en AWS: .png, .svg y .drawio editable
├── eventos/
│   ├── catalogo.yaml          # fuente de verdad: productor, consumidores, canal y tarea de cada evento
│   ├── esquemas/              # JSON Schema Draft-04 del campo detail de cada evento
│   │   └── _metadata.json     # bloque metadata común a todos
│   └── ejemplos/              # un evento de EventBridge completo por cada esquema
├── apis/                      # OpenAPI 3.0.3, uno por microservicio
├── scripts/validar_contratos.py
├── requirements.txt
└── .github/workflows/contratos.yml   # corre la validación en cada PR
```

## Catálogo de eventos

| Evento (`detail-type`) | Productor | Consumidores | Canal | Tarea |
|---|---|---|---|---|
| `lms.users.tenant.created.v1` | usuarios | — (solo Archive) | bus | ACA-01 |
| `lms.users.term.started.v1` | usuarios | cursos | bus | ACA-01 |
| `lms.users.user.provisioned.v1` | usuarios | cursos, notificaciones | bus | ACA-02 |
| `lms.courses.course.created.v1` | cursos | contenidos | bus | ACA-03 |
| `lms.courses.course.published.v1` | cursos | notificaciones | bus | ACA-03 |
| `lms.courses.course.archived.v1` | cursos | — (solo Archive) | bus | ACA-03 |
| `lms.courses.enrollment.created.v1` | cursos | contenidos, evaluaciones, calificaciones, notificaciones | bus | ACA-04 |
| `lms.courses.enrollment.dropped.v1` | cursos | contenidos, evaluaciones, calificaciones, notificaciones | bus | ACA-04 |
| `lms.courses.announcement.published.v1` | cursos | notificaciones | bus | COL-01 |
| `lms.content.file.scanned.v1` | contenidos | evaluaciones | bus | CON-01 |
| `lms.content.module.published.v1` | contenidos | notificaciones | bus | CON-02 |
| `lms.assessment.assignment.published.v1` | evaluaciones | calificaciones, notificaciones | bus | EVA-01 |
| `lms.assessment.submission.submitted.v1` | evaluaciones | notificaciones | bus | EVA-01 |
| `lms.assessment.submission.graded.v1` | evaluaciones | calificaciones | bus | EVA-01 |
| `lms.grading.grade.changed.v1` | calificaciones | — (la auditoría de DAT-01 lo lee dentro de ms-calificaciones) | bus | EVA-02 |
| `lms.grading.grade.posted.v1` | calificaciones | notificaciones | bus + SNS FIFO | EVA-02 |

El diagrama de arquitectura con los íconos de AWS está en [EVENT_STORMING.md](EVENT_STORMING.md) y en `docs/`.

"bus" es el bus de EventBridge `lms`. Cada evento tiene una descripción en `catalogo.yaml` y en su esquema.

## Convenciones

- **Nombre:** `detail-type` = `lms.<contexto>.<entidad>.<hecho>.v<N>`, con el hecho en pasado. Cada microservicio publica con un solo `source`:

  | `source` | Microservicio |
  |---|---|
  | `lms.users` | ms-usuarios |
  | `lms.courses` | ms-cursos |
  | `lms.content` | ms-contenidos |
  | `lms.assessment` | ms-evaluaciones |
  | `lms.grading` | ms-calificaciones |

  ms-notificaciones solo consume.
- **Forma del evento:** el esquema describe el campo `detail` del evento de EventBridge, que siempre es `{ "metadata": {...}, "data": {...} }`. `metadata` es igual en todos los eventos (`esquemas/_metadata.json`). En los eventos de un curso, `metadata.courseId` es obligatorio y no se repite en `data`.
- **Payload mínimo:** ids y lo que cambió. Nada de nombres, correos ni archivos; un archivo viaja como `fileId`. Quien necesite un nombre o un correo lo pide a ms-usuarios o a Cognito.
- **Ids:** `tenantId` es `utec`, `upc` o `pucp`. `userId` es el `sub` de Cognito (UUID). El resto es prefijo + ULID: `trm_` periodo, `crs_` curso, `sec_` sección, `enr_` matrícula, `ann_` anuncio, `fil_` archivo, `mod_` módulo, `itm_` ítem, `asg_` tarea, `sbm_` entrega, `gri_` columna de notas, `grd_` nota, `ntf_` aviso. `eventId` es un ULID sin prefijo.
- **Idioma:** ids, rutas y campos en inglés y camelCase; textos para el usuario en español.
- **Tenant y rol:** salen del ID token de Cognito (`custom:tenant_id`, `custom:role`), nunca de la ruta ni del body. En las APIs, `x-roles` dice qué roles pueden llamar cada operación y `x-emits` qué eventos publica.
- **Este repo es la única fuente de verdad de los esquemas.** EventBridge Schema Registry no está disponible en AWS Academy Learner Lab: el LabRole no tiene permiso `schemas:CreateRegistry`. Tampoco hace falta, porque el registry no valida los eventos al publicarlos. La validación la hacen la CI de este repo y el productor antes de escribir el evento.
- **Esquemas en Draft-04 y autocontenidos (sin `$ref`):** es un formato que Schema Registry acepta, así que se podrían registrar tal cual en una cuenta que lo permita.

## Cómo se publica un evento (para LIB-01)

1. La Lambda valida `detail` contra el esquema y escribe la entidad y un ítem de outbox en la misma `TransactWriteItems`. El ítem de outbox va en la misma tabla, con `sk = EVENTO#<eventId>` y los atributos `source`, `detailType`, `detail` (JSON) y `ttl` (7 días).
2. Un EventBridge Pipe lee el Stream de la tabla, deja pasar solo los `INSERT` cuyo `sk` empieza con `EVENTO#` y manda el evento al bus `lms`.
3. `poblar_datos.py` no escribe ítems `EVENTO#`. Así, cargar los 30.000 registros no inunda el bus ni a los consumidores. La única excepción son los 3 `tenant.created`.
4. `grade.posted` sale además por el tópico SNS FIFO `<stage>-notas.fifo`, con `MessageGroupId = <tenantId>:<courseId>` y `MessageDeduplicationId = eventId`. ms-notificaciones lo lee solo de su cola FIFO, no de una regla del bus, para no mandar dos avisos.

## Cómo se consume un evento

- Hay una regla de EventBridge por consumidor, que copia el evento a la cola SQS de ese consumidor. La cola tiene DLQ con `maxReceiveCount: 5`.
- **Idempotencia:** el consumidor guarda el `eventId` con escritura condicional (`attribute_not_exists`) en la misma `TransactWriteItems` que su cambio. Si ya existía, el evento se descarta.
- **Orden:** si `aggregateVersion` es menor o igual a la que el consumidor ya tiene, el evento es viejo y se descarta.
- **Lector tolerante:** el consumidor ignora los campos que no conoce y no valida con el esquema. Así, agregar un campo opcional no rompe a nadie.

## Versionado

- **Cambio compatible:** un campo opcional nuevo en `data`, o una descripción. Se queda en `v1`. El PR lo aprueban el productor y los consumidores del catálogo.
- **Cambio incompatible:** quitar o renombrar un campo, volverlo obligatorio, cambiar su tipo o agregar un valor a un `enum`. Va en un archivo `v2` nuevo. El productor publica `v1` y `v2` en paralelo hasta que todos los consumidores migren.

## Validar

```bash
pip install -r requirements.txt
python scripts/validar_contratos.py
```

El script revisa que cada evento del catálogo tenga esquema y ejemplo, que cada ejemplo cumpla su esquema, que las 6 APIs sean OpenAPI válido y que cada `x-emits` apunte a un evento que ese microservicio produce. La misma validación corre en GitHub Actions en cada PR (PLT-02 la vuelve obligatoria para mergear).

Para mandar un ejemplo al bus local de LocalStack (LOC-01):

```bash
jq -c '[{Source: .source, DetailType: ."detail-type", Detail: (.detail | tojson), EventBusName: "lms"}]' \
  eventos/ejemplos/lms.courses.course.created.v1.json > /tmp/evento.json
awslocal events put-events --entries file:///tmp/evento.json
```

## Revisión de ARQ-01

ARQ-01 está lista cuando los 6 aprueban el PR. Cada uno revisa lo suyo:

- [ ] **Dev 1 · cruz-eng:** que el canal y las colas de cada evento se puedan crear con la plantilla de PLT-04.
- [ ] **Dev 2 · MarianoUtec:** el bus, el tópico FIFO de notas y los claims del token (AUTH-01).
- [ ] **Dev 3 · btoroled:** ms-usuarios, ms-cursos y la forma común de los eventos.
- [ ] **Dev 4 · Edinson695:** ms-evaluaciones y ms-calificaciones.
- [ ] **Dev 5 · AIexander-lx:** ms-contenidos y el flujo de archivos con antivirus.
- [ ] **Dev 6 · Guillermo-Heredia:** ms-notificaciones y que las APIs alcancen para las pantallas de FE-02 y FE-03.
