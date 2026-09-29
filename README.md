# Atenea

MVP con FastAPI, PostgreSQL y autenticación web por organización. Ejecuta los comandos desde la raíz del proyecto en PowerShell.

## Preparación

```powershell
& .\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

La conexión existente usa `DATABASE_URL` en `.env`. No copies ese archivo al repositorio: ya está ignorado en `.gitignore`.

La web requiere una nueva variable: **`SESSION_SECRET`**. Para generar un secreto aleatorio de 32 bytes y guardarlo directamente en `.env`, sin imprimirlo ni reemplazar uno existente:

```powershell
& .\.venv\Scripts\python.exe -c "import secrets; from dotenv import dotenv_values, set_key; values = dotenv_values('.env', interpolate=False); set_key('.env', 'SESSION_SECRET', secrets.token_urlsafe(32)) if not values.get('SESSION_SECRET') else None"
```

No hay un secreto predeterminado. Si falta o tiene menos de 32 caracteres, la web rechaza el arranque. Las pruebas generan sus propios secretos temporales y no modifican tu `.env`.

Antes de iniciar esta versión sobre una base existente, aplica las migraciones pendientes:

```powershell
& .\.venv\Scripts\python.exe -m app.migrate
```

La migración `migrations/001_organization_workdays.sql` añade siete columnas booleanas `NOT NULL` a `organizations`: `workday_monday`, `workday_tuesday`, `workday_wednesday`, `workday_thursday`, `workday_friday`, `workday_saturday` y `workday_sunday`. Las organizaciones existentes y las nuevas comienzan con lunes a viernes laborables y sábado/domingo no laborables.

`migrations/002_optional_time_entry_project.sql` permite `NULL` en `time_entries.project_id` para registrar trabajo únicamente contra un cliente. Conserva las claves foráneas y los registros existentes. El módulo de informes no añade migraciones.

El ejecutor usa una transacción, un bloqueo para evitar migraciones simultáneas, un límite de espera de bloqueo de 5 segundos y un límite de 30 segundos por sentencia. Verifica tipos, nulabilidad y defaults antes de confirmar. Si falla, revierte la transacción. Puede ejecutarse nuevamente: no elimina datos ni restablece configuraciones guardadas. No usa `create_all()` para actualizar una tabla existente ni ejecuta migraciones automáticamente al arrancar Uvicorn.

Para crear una cuenta real, si todavía no existe:

```powershell
& .\.venv\Scripts\python.exe -m app.cli create-admin
```

## Ejecutar la aplicación

```powershell
& .\.venv\Scripts\python.exe -m uvicorn app.main:create_app --factory --reload --host 127.0.0.1 --port 8000 --no-access-log
```

Abre **http://127.0.0.1:8000/login**. Introduce únicamente email y contraseña. La cuenta registrada determina automáticamente la organización. El email es único globalmente, ignorando mayúsculas y espacios exteriores; el nombre de organización no se solicita ni se acepta para decidir el acceso. Los fallos de credenciales muestran el mismo mensaje genérico.

Se usa una fábrica (`create_app`), por eso el comando incluye `--factory`. No se crean ni alteran tablas al iniciar la web.

- `GET /login`: formulario de acceso; si ya existe una sesión válida, redirige al dashboard.
- `POST /login`: verifica credenciales y crea la sesión.
- `GET /dashboard`: requiere usuario y organización activos.
- `POST /logout`: elimina la cookie desde el botón «Cerrar sesión», con protección CSRF. Un enlace GET no cierra la sesión.
- `GET /`: redirige al dashboard y, si corresponde, al login.

## Clientes, proyectos y horas

Todas estas pantallas requieren una sesión válida. Clientes y proyectos se comparten entre los usuarios activos de la misma organización. Cada usuario, incluido un administrador, ve y modifica únicamente sus propias horas.

| URL | Métodos | Uso |
| --- | --- | --- |
| `/clients` | GET, POST | Usuarios: listar activos. Administradores: listar todos y crear clientes. |
| `/projects` | GET, POST | Listar y crear proyectos/expedientes asociados a un cliente activo. |
| `/hours` | GET, POST | Registrar una actividad con fecha, cliente, proyecto, descripción, horas y condición facturable. |
| `/hours/week` | GET | Ver la semana actual. `?week=2026-09-07` permite elegir cualquier semana. |
| `/hours/history` | GET | Histórico mensual propio; filtros de cliente, proyecto y condición facturable. |
| `/hours/{id}/edit` | GET, POST | Consultar el formulario y guardar cambios de un registro propio. |
| `/hours/{id}/delete` | GET, POST | Mostrar la confirmación y eliminar un registro propio. GET no elimina datos. |
| `/settings/workdays` | GET, POST | Administradores: consultar y guardar los siete días laborables de su organización. |

Flujo manual completo:

1. Inicia Atenea con el comando de Uvicorn anterior y abre `/login`.
2. Como administrador, en **Clientes**, crea un cliente, por ejemplo «Cliente de demostración», con código opcional. También puedes editarlo, desactivarlo y reactivarlo sin borrar su histórico.
3. En **Proyectos / Expedientes**, selecciona ese cliente y crea un proyecto con nombre, código y descripción opcionales.
4. En **Registrar horas**, elige cualquier fecha pasada o actual y un cliente activo; el proyecto es opcional y depende del cliente. Introduce una descripción, `1.50` horas y si es facturable. El servidor comprueba la correspondencia entre cliente y proyecto.
5. Al guardar, se abre el histórico del mes de la fecha registrada. Añade otra actividad de `2.25` horas el mismo día y consulta **Mi semana**: el total diario debe ser `3.75` horas.
6. Usa **Editar** para cambiar un registro y comprueba que se recalculan los totales. **Eliminar** abre una pantalla de confirmación antes de borrar.
7. Usa **Semana anterior**, **Semana siguiente** y **Semana actual**. Siempre aparecen los siete días, incluidos sábado y domingo, aunque estén vacíos. Cada día muestra si es laborable y su total; arriba se muestran los totales laborable, no laborable y general.
8. El dashboard ofrece accesos a registro, histórico, semana, informes y catálogos, con totales del mes y semana actuales. Con otra organización, no deben aparecer los clientes, proyectos ni registros de la primera; otro usuario de la misma organización comparte los catálogos, pero tiene sus propias horas.

Decisiones del MVP:

- Las consultas usan la organización del usuario autenticado. Los POST que incluyan `organization_id` o `user_id` se rechazan; esos campos no aparecen en los formularios.
- Se rechazan nombres o códigos duplicados de clientes dentro de la organización, ignorando mayúsculas y espacios redundantes. En proyectos se aplica la misma regla dentro del cliente. También se consideran los inactivos, para no duplicar identidades históricas.
- Las altas del catálogo se serializan por organización mediante bloqueos transaccionales de PostgreSQL. Estos controles cubren las escrituras de la web; futuros importadores o escritores SQL deberán utilizar la misma validación o incorporar índices únicos mediante una migración.
- Las horas usan `Decimal`, de `0.01` a `24`, con hasta dos decimales. Se aceptan punto y coma decimal en el servidor. La suma por usuario y día tampoco puede superar `24`, tanto al crear como al editar. Las escrituras se serializan por usuario para evitar que dos peticiones eludan ese límite.
- Los selectores de períodos admiten fechas entre 1900 y 2100. El registro de horas acepta fechas pasadas o actuales, incluidas las de meses anteriores, y rechaza fechas futuras. La fecha y semana actuales usan la fecha local del servidor; en producción configura su zona horaria según el equipo.
- La vista principal muestra los siete días de lunes a domingo y clasifica cada uno según la configuración de la organización. El total general incluye horas laborables y no laborables, facturables y no facturables, y conserva registros asociados a clientes/proyectos inactivos.
- Registrar exige un cliente activo y, si se elige proyecto, que esté activo y pertenezca al cliente. Una edición puede conservar su cliente/proyecto histórico desactivado, pero no seleccionar otros inactivos. Los administradores pueden desactivar o reactivar clientes sin borrarlos físicamente.
- La eliminación de horas es definitiva, requiere confirmación mediante formulario POST con CSRF y solo afecta a un registro propio. Todavía no hay facturas, bloqueos de períodos ni auditoría; antes de incorporarlos deberá revisarse esta política.
- Los formularios conservan los valores cuando hay errores de negocio y muestran un mensaje claro. Los registros ajenos devuelven el mismo 404 que un ID inexistente.

## Configurar días laborables

Inicia sesión como administrador y abre **http://127.0.0.1:8000/settings/workdays**, o utiliza el enlace **Días laborables** de la navegación. Marca los días y pulsa **Guardar días laborables**. Cualquier combinación es válida: lunes a sábado, los siete días, días alternos o ninguno.

El rol `admin` se comprueba en PostgreSQL en cada petición; el rol `user` no puede abrir ni modificar esta configuración. La organización procede de la sesión y el guardado requiere CSRF. No se admite seleccionar otra organización desde un formulario o URL.

`/hours` permite indicar **fecha trabajada, horas, cliente, proyecto/expediente opcional, descripción de qué se hizo y facturable sí/no**. Los días no laborables, incluidos sábados y domingos, siguen admitiendo registros y ediciones. Se conservan los límites de horas y las validaciones de cliente/proyecto. El campo legado `activity` se deriva de la descripción al crear y se conserva al editar.

`weekly_view()` en `app/worklog.py` es la lógica compartida para la vista y futuras exportaciones. Devuelve los siete días con todos sus registros, `is_workday`, total diario, `working_total`, `non_working_total` y `week_total` (total general). Todos los importes de horas se calculan con `Decimal`. La clasificación se obtiene de `workday_flags()` en `app/workdays.py`; no depende de una regla fija de lunes a viernes. Los defaults iniciales están en el esquema, no en las reglas de reporte.

La configuración actual se aplica también a semanas anteriores: cambiarla reclasifica sus totales, pero no modifica fechas, registros ni horas. No hay historial de calendarios por fecha de vigencia en este MVP. Si más adelante se requieren reportes históricos inmutables, habrá que versionar esa configuración o guardar una copia al emitir el reporte.

Para comprobarlo manualmente, registra horas un lunes y un domingo, consulta la semana, marca el domingo como laborable y vuelve a consultarla. El total general debe permanecer igual; las horas del domingo pasan del total no laborable al laborable. Otra organización debe mantener su configuración.

## Sesión y protección

La cookie `atenea_session` está firmada por `SessionMiddleware` de Starlette, es `HttpOnly`, `SameSite=Lax`, limitada al host y a la ruta `/`, y tiene una duración de 8 horas. La sesión de acceso contiene `user_id`, `organization_id`, `auth_version` y un token aleatorio para proteger los formularios frente a CSRF. No contiene nombre, email, rol, contraseña ni hash. La firma protege frente a modificaciones; no cifra su contenido. Referencia: [SessionMiddleware de Starlette](https://github.com/Kludex/starlette/blob/main/docs/middleware.md#sessionmiddleware).

El login renueva los datos de sesión y el token CSRF. Login y logout requieren el token del formulario. Las páginas llevan `Cache-Control: no-store`, una política CSP que permite solo scripts locales y protección contra inclusión en marcos; las plantillas escapan el contenido HTML.

`get_current_user()` en `app/web_auth.py` es la dependencia reutilizable para rutas protegidas. Consulta PostgreSQL usando **ambos IDs** y vuelve a comprobar los estados del usuario y la organización. El nombre y rol se leen de la base de datos, no de la cookie. Cada futura consulta de negocio deberá filtrar también por `user.organization_id`.

Los fallos de login muestran un mensaje genérico. La verificación de cuentas inexistentes también realiza trabajo bcrypt para reducir diferencias de tiempo evidentes; esto no sustituye a un limitador de intentos. No se registran formularios, cookies ni excepciones SQL con parámetros; los errores internos generan una respuesta y un registro genéricos. El comando recomendado desactiva el registro de URLs de Uvicorn.

## Antes de producción

1. Usa HTTPS y añade `SESSION_COOKIE_SECURE=true` a `.env`. El valor local predeterminado es `false` para permitir HTTP en `127.0.0.1`; con `true`, la cookie solo se envía por HTTPS.
2. Retira `--reload`, configura el proxy, los hosts permitidos, los encabezados reenviados de confianza y HSTS. El proxy debe evitar registrar contraseñas, cookies y cuerpos de formularios.
3. Añade límites de intentos de login y monitorización de abusos antes de exponer el acceso a Internet.
4. Protege `.env` y usa el mismo secreto aleatorio en todos los procesos de la aplicación. Rotarlo invalida todas las cookies existentes.
5. El logout borra la cookie de ese navegador. Restablecer una contraseña, cambiar el email o cambiar el estado del usuario mediante la administración incrementa `auth_version` y revoca sus cookies anteriores. Desactivar la organización bloquea los accesos mientras permanezca inactiva. No existe todavía revocación por dispositivo: el contador revoca todas las sesiones de la cuenta.

## Pruebas

```powershell
& .\.venv\Scripts\python.exe -m unittest discover -s tests -v
& .\.venv\Scripts\python.exe -m pip check
```

Las pruebas de CLI y web usan `atenea_db` con transacciones externas revertidas y verifican que no queden filas de prueba. PostgreSQL debe estar disponible; no requieren `SESSION_SECRET` en tu `.env`. Las secuencias de IDs pueden avanzar aunque se reviertan las filas. La prueba de arranque levanta Uvicorn temporalmente en un puerto libre de loopback, usa un `.env` temporal con un secreto aleatorio, comprueba HTTP y detiene el proceso.

Las dependencias de pruebas incluyen `httpx2`, compatible con el `TestClient` de Starlette instalado. `tests/test_worklog.py` cubre el flujo operativo completo, controles de organización y propiedad, CSRF, duplicados, horas válidas, totales diarios/semanales, fines de semana y navegación entre años. `tests/test_workdays.py` prueba los calendarios configurables, permisos, aislamiento, persistencia, días no laborables y todos los totales. `tests/test_migrations.py` verifica la migración sobre tablas temporales, su repetición, defaults y rollback sin alterar las tablas reales. La prueba de Uvicorn comprueba también que `/settings/workdays` y las demás URLs protegidas redirijan al login.

## Informes personales y Excel

Desde el dashboard o la navegación, abre **Informes** (`http://127.0.0.1:8000/reports/month`). La vista abre el mes actual y permite elegir un mes o navegar al anterior/siguiente. Muestra horas totales, facturables y no facturables, días distintos con actividad y todas las semanas que intersectan el mes, incluidas las vacías.

| URL | Parámetro opcional | Resultado |
| --- | --- | --- |
| `GET /reports/month` | `month=2026-09` | Resumen mensual y enlaces a cada semana y su Excel. |
| `GET /reports/week/export` | `week=2026-09-03` | Excel de la semana lunes 31/08/2026 a domingo 06/09/2026. |
| `GET /reports/month/export` | `month=2026-09` | Excel de septiembre de 2026, con hojas `Resumen` y `Detalle`. |

Sin parámetro, las exportaciones usan la semana o mes actuales. El selector semanal es el mismo que en `/hours/week`: cualquier fecha de referencia dentro de la semana. La vista semanal conserva sus funciones y añade **Descargar Excel semanal**. No cambian `/hours` ni `/hours/history`.

Las semanas son siempre de lunes a domingo y se identifican por su lunes, aunque crucen un mes o año. La vista muestra tanto **Horas de la semana completa** como **Horas del mes**. El total mensual, el detalle mensual y sus agrupaciones por cliente y semana cuentan exclusivamente registros con `inicio_del_mes <= work_date < inicio_del_mes_siguiente`. Por ejemplo, el 31 de agosto aparece en el Excel semanal del 31/08 al 06/09, pero no suma en septiembre. No se suman semanas completas para calcular el mes. Los días con actividad se cuentan por fechas distintas con registros propios, sin depender del calendario laborable.

El Excel semanal contiene una hoja **Semanal**: organización, usuario, título, inicio/fin, fecha de generación UTC, detalle de actividades y totales. Incluye un resumen diario de los siete días, con ceros cuando no hay registros; total semanal, facturable, no facturable, laborable y no laborable. La clasificación usa la configuración actual de la organización, también para semanas históricas.

El Excel mensual contiene **Resumen** (identidad, período, totales, días con actividad, horas por cliente y por semana limitadas al mes) y **Detalle** (todos los registros del mes ordenados por fecha). Ambos Excel conservan clientes/proyectos inactivos del histórico, admiten registros sin proyecto y se generan también para períodos vacíos. Fechas y horas se escriben con tipos nativos de Excel, con formato de fecha y dos decimales; incluyen encabezados, filtros de detalle cuando hay filas, paneles inmovilizados e impresión horizontal ajustada a una página de ancho y tantas páginas de alto como sean necesarias.

Los cálculos se realizan con `Decimal` en el servidor y se exportan como valores numéricos: son una instantánea, no fórmulas que se recalculen al editar el archivo. Los textos completos se conservan hasta los límites actuales de los formularios. Excel limita la altura de una fila a 409 puntos: una descripción excepcionalmente larga se conserva en la celda, pero puede requerir consultar la barra de fórmulas para leerla entera. Se sustituyen por `�` únicamente caracteres de control incompatibles con XML.

Los archivos se generan con `openpyxl` en `BytesIO`, sin guardar informes de usuarios en disco. Nombres: `Atenea_Horas_2026-08-31_al_2026-09-06.xlsx` y `Atenea_Horas_2026-09.xlsx`. Solo contienen prefijos fijos y fechas validadas. No requieren migraciones, variables nuevas ni dependencias adicionales.

Cada ruta exige una sesión válida y comprueba nuevamente usuario y organización activos. También los administradores descargan únicamente sus propias horas. Las consultas comparten `owned_entries()`, con filtros de usuario y organización y validación de pertenencia de cliente/proyecto. Se rechazan `user_id`, `organization_id`, parámetros desconocidos y parámetros repetidos en las URLs de informes. Son operaciones GET de solo lectura; los POST existentes conservan CSRF. Las respuestas llevan `Cache-Control: no-store`.

Todo texto, incluidos nombres, códigos y descripciones que comiencen por `=`, `+`, `-` o `@`, se escribe explícitamente como texto y nunca como fórmula. No hay macros, hipervínculos automáticos ni enlaces externos. No se exportan email, IDs de sesión, hashes, contraseñas ni secretos.

Prueba manual: registra actividades facturables y no facturables, alguna sin proyecto, en un fin de mes y el comienzo del siguiente. Abre **Informes**, selecciona el mes y comprueba la diferencia entre semana completa y aporte al mes. Descarga ambos Excel y revisa los totales y los siete días en el semanal. Cambia a un mes sin registros y verifica que las descargas siguen disponibles con totales cero.

`tests/test_reports.py` añade 22 pruebas sobre acceso, aislamiento, períodos, meses de seis semanas y bisiestos, totales Decimal, configuración laborable, históricos inactivos, archivos vacíos, Unicode, texto largo, caracteres XML y formula injection. Los archivos se reabren con `openpyxl.load_workbook()` y se verifican sus hojas, fechas, valores, tipos, filtros, impresión y ausencia de fórmulas/macros/enlaces. La prueba de Uvicorn comprueba también las tres URLs nuevas.

No se han añadido Outlook, Microsoft Graph, IA, envío de correo, facturación ni aprobación de horas.

## Tareas y recordatorios

Antes de arrancar esta versión, instala las dependencias y aplica la migración:

```powershell
& .\.venv\Scripts\python.exe -m pip install -r requirements.txt
& .\.venv\Scripts\python.exe -m app.migrate
```

La migración aditiva `003_tasks.sql` crea `tasks`, sus índices y restricciones sin modificar tablas ni registros existentes. El ejecutor sigue usando una transacción, límites de espera y bloqueo de migraciones; verifica la estructura y puede repetirse sin borrar o restablecer tareas. No se migra automáticamente al arrancar la web.

| URL | Método | Uso |
| --- | --- | --- |
| `/tasks` | GET | Lista personal, primero pendientes y después completadas/canceladas. |
| `/tasks/new` | GET, POST | Crear una tarea pendiente. |
| `/tasks/{id}/edit` | GET, POST | Editar una tarea propia, incluso histórica. |
| `/tasks/{id}/status` | POST | Completar, volver a pendiente o cancelar; exige CSRF. |

En **Tareas → Nueva tarea**, introduce un título. Descripción, fecha, hora, cliente, proyecto y recordatorio son opcionales. Una hora límite requiere una fecha. Un proyecto requiere un cliente compatible. Los nuevos vínculos deben apuntar a catálogos activos de la misma organización; una edición puede conservar vínculos históricos archivados o retirarlos. No se crean registros de horas automáticamente.

Las prioridades son `low`, `normal`, `high`, `urgent`, mostradas como **Baja, Normal, Alta y Urgente**, mediante texto y color. Los estados son `pending`, `completed`, `cancelled`. Hay validación en aplicación y CHECK constraints en PostgreSQL, sin enums nativos. La base también impide asociaciones con usuarios/clientes de otra organización y proyectos de un cliente distinto.

La lista permite combinar filtros `status`, `priority`, `client_id` y `period`. Los períodos son `overdue`, `today`, `tomorrow`, `next7`, `later`, `undated`. Se identifica visualmente cada pendiente: vencida, hoy, mañana, próximos 7 días, posterior o sin fecha. El filtro `next7` comprende desde mañana hasta hoy + 7 días, ambos incluidos; excluye hoy. Los filtros temporales se aplican a la fecha límite también al consultar el histórico. El dashboard cuenta solo pendientes propios: vencidas, hoy y próximas. Las fechas de hoy con hora ya pasada se cuentan como vencidas.

**Marcar como completada** guarda `completed_at` con zona horaria. Repetir esa acción no cambia la fecha de finalización. **Volver a pendiente** limpia `completed_at`; una nueva finalización guardará un nuevo instante. **Cancelar tarea** conserva la fila, sus detalles y su recordatorio; se puede reabrir. No hay eliminación física ni bitácora de todas las transiciones en este MVP. Al salir de completada se limpia `completed_at`, que representa la finalización del estado actual.

Todos los usuarios, incluidos administradores, ven y modifican únicamente sus propias tareas. `organization_id` y `user_id` provienen de la sesión y se rechazan en formularios. Se validan nuevamente usuario y organización activos en cada acceso; una tarea ajena devuelve el mismo 404 que una inexistente. Los cambios de estado usan bloqueo de fila y CSRF. Los títulos y descripciones se escapan al mostrarlos.

### Zona horaria y recordatorios

Cada organización guarda su zona IANA en `Organization.timezone`, inicialmente **`America/El_Salvador`**. Un administrador puede cambiarla en **`/settings/timezone`**, mediante una lista de zonas soportadas por `zoneinfo`. El guardado requiere CSRF y solo modifica su propia organización. Se valida tanto en el formulario como al asignar el campo mediante ORM. `tzdata` proporciona la base de zonas también en Windows. La opción **`APP_TIMEZONE`** continúa como fallback de instalación para datos transitorios sin zona; no reemplaza una zona guardada ni oculta valores inválidos.

`due_date` es una fecha y `due_time` una hora civil opcional, interpretadas en la zona de la organización. Una tarea del día sin hora sigue siendo del día hasta el cambio de fecha local: no se le asigna medianoche como vencimiento. Una fecha con hora pasa a vencida cuando esa hora ya transcurrió. La comparación usa un reloj consciente de zona horaria. En entradas manuales se rechazan horas ambiguas o inexistentes durante cambios estacionales. La política de ocurrencias automáticas se explica más abajo. Este módulo no cambia la estrategia de fechas locales del servidor usada por los módulos anteriores de horas/informes.

El formulario `datetime-local` del recordatorio se interpreta en la zona de la organización y se convierte a UTC. PostgreSQL guarda `reminder_at`, `completed_at`, `created_at` y `updated_at` como `TIMESTAMP WITH TIME ZONE`. La edición/lista convierte el recordatorio de vuelta a la zona configurada. Se permiten recordatorios pasados e independientes de la fecha límite; los pasados aparecen inmediatamente como pendientes si la tarea sigue pendiente. Cambiar la zona conserva los instantes UTC ya guardados y los valores civiles de vencimiento: la hora mostrada del recordatorio y su distancia al vencimiento pueden cambiar. Las nuevas ocurrencias usan la zona vigente al generarse. No hay versionado temporal de zonas.

`app.tasks.due_reminders(db, user, now=None, limit=100)` obtiene tareas del usuario y organización indicados que siguen pendientes, cuyo `reminder_at <= now` y cuyas cuentas siguen activas. `now`, si se proporciona, debe tener zona horaria. No acepta IDs procedentes del navegador, no realiza envíos ni consume recordatorios. Consultarlo nuevamente devuelve las mismas tareas hasta completarlas, cancelarlas o cambiar su recordatorio. El futuro scheduler deberá recorrer ámbitos autorizados e implementar seguimiento de entregas e idempotencia antes de emitir avisos. Por ahora se muestra **Recordatorio pendiente** en la lista; no hay avisos push, de escritorio ni correo.

`source_type` y `source_id` son textos opcionales que deben existir juntos o estar ambos vacíos. Reservan una referencia externa genérica para futuras integraciones. No se exponen en formularios, no se alteran al editar tareas y no contienen lógica de Outlook, Microsoft Graph ni sincronización. No se impone unicidad aún: una integración futura definirá su política de duplicados.

Prueba manual: crea «Enviar informe mensual» sin hora, con prioridad alta y un recordatorio. Comprueba la lista y sus filtros; edita la fecha; marca la tarea como completada y localízala mediante el filtro de completadas. Reábrela o cancélala y comprueba que sigue en el histórico. Prueba con otro usuario: no debe aparecer ni poder editarla aunque conozca su URL.

`tests/test_tasks.py` añade 32 pruebas funcionales y de seguridad; `tests/test_task_migration.py` añade tres pruebas de creación, repetición, rollback y esquema incompatible en un esquema aislado que se revierte. Todas las filas de prueba se revierten y se comprueba que no quedan tareas de prueba. La comprobación de Uvicorn incluye `/tasks` y `/tasks/new`.

## Tareas recurrentes

Aplica **`python -m app.migrate`** antes de iniciar esta versión. La migración aditiva `004_task_recurrence_timezone.sql`, junto con las restricciones definidas en el ejecutor, añade la zona a las organizaciones y los campos de recurrencia a tareas. Conserva filas, identificadores, fechas y estados. Las organizaciones existentes comienzan con `America/El_Salvador` y todas las tareas anteriores con **No repetir**. Repetir la migración no reinicializa configuraciones. Las restricciones se crean dentro de la misma transacción que las columnas.

En `/tasks/new` y `/tasks/{id}/edit`, elige **No repetir**, **Diaria**, **Semanal**, **Mensual** o **Anual**, un intervalo entero de 1 a 365 y, opcionalmente, una fecha final inclusiva. Una tarea recurrente necesita fecha inicial; la hora sigue siendo opcional. Por ejemplo, mensual cada 1 mes desde 30/09/2026; semanal cada 2 semanas desde un viernes; anual cada 1 año desde el vencimiento de un contrato.

Se almacenan `recurrence_type`, `recurrence_interval`, `recurrence_end_date`, `recurrence_anchor_date` (fecha inicial estable), `recurrence_index` (posición dentro de la regla) y `parent_task_id` (ocurrencia inmediatamente anterior). No se usa RRULE ni se precalcula un calendario de tareas. Una clave foránea compuesta obliga a que la ocurrencia anterior pertenezca al mismo usuario y organización; `UNIQUE(parent_task_id)` permite como máximo una sucesora.

Al **completar o cancelar** una ocurrencia se crea solamente la siguiente como pendiente, dentro de la misma transacción del cambio de estado. Se bloquea la fila original y se comprueba si ya tiene sucesora; la restricción única refuerza la protección frente a duplicados. Repetir el POST, reabrir y volver a completar, o cancelar una ya completada no crea otra sucesora. Si la fecha siguiente supera el fin de recurrencia o el límite de fechas de Atenea (2100), no se crea ninguna. Una ocurrencia atrasada puede generar otra también atrasada: no se saltan pendientes ni se crean miles de filas para recuperar el calendario.

La fecha se calcula siempre desde el ancla, multiplicando intervalo por índice. La regla semanal conserva el día de semana original. Las reglas mensual y anual usan el menor entre el día original y el último día válido del mes de destino. Ejemplos: **31/01 → 28/02 → 31/03**, **30/01 → 28/02 → 30/03**, y **29/02/2024 → 28/02/2025**, recuperando **29/02/2028** en el siguiente año bisiesto. No se desplaza permanentemente el ancla al 28.

El siguiente recordatorio se calcula conservando la diferencia civil entre vencimiento y recordatorio, en la zona vigente de la organización. Una tarea de las 15:00 con aviso a las 09:00 conserva esas seis horas locales en la siguiente ocurrencia, aunque cambie el desplazamiento UTC por horario estacional. Sin hora límite se conserva la hora local del aviso y su separación en días respecto de la fecha límite. No se copia el timestamp absoluto del aviso anterior.

Para ocurrencias automáticas, una hora ambigua por cambio estacional usa la primera aparición (`fold=0`); una hora inexistente se interpreta desplazada hacia adelante por la duración del salto. La hora civil almacenada de vencimiento sigue mostrando la hora de la regla; las comparaciones usan el instante resuelto. El recordatorio se guarda como un instante UTC válido. Una ocurrencia automática permite conservar su fecha/hora al editar otros datos; las nuevas horas manuales ambiguas o inexistentes siguen rechazándose.

Las ediciones modifican esa fila, sin actualizar ocurrencias ya creadas. La siguiente, cuando se genere, copiará los datos vigentes de título, descripción, prioridad, cliente/proyecto, hora y relación del recordatorio. Corregir solo la fecha de una ocurrencia conserva el calendario original de las siguientes. Cambiar la regla reinicia el ancla desde la fecha de esa tarea y solo se permite en una pendiente sin sucesora; para cambiar la continuación, edita la siguiente pendiente. No hay edición masiva de series.

**Cancelar tarea** conserva la fila y genera la siguiente: no detiene toda la serie. Para detener la continuación, cambia a **No repetir** la pendiente que todavía no tenga sucesora. Reabrir una anterior no elimina su sucesora; ambas pueden quedar pendientes. No se borran tareas ni se conserva una bitácora de todas las transiciones.

Las nuevas ocurrencias aparecen en `/tasks` y en los grupos y contadores actuales del dashboard. El histórico conserva la tarea, el vencimiento, la fecha de finalización si sigue completada y el enlace **Siguiente ocurrencia**. La sucesora enlaza a su anterior. Los enlaces y sus consultas están limitados al mismo usuario y organización. Las referencias externas `source_type` y `source_id` se conservan en la ocurrencia original y no se duplican en las generadas.

Prueba manual: crea una mensual con fecha 31/01/2026 y un recordatorio. Complétala: debe aparecer 28/02/2026 como pendiente. Completa esta y comprueba 31/03/2026. Repite el POST desde la tarea original o reábrela y complétala: seguirá existiendo una sola sucesora directa. Cancela la siguiente y comprueba que el histórico la conserva y la serie continúa. Prueba una fecha de fin inclusiva y el ajuste de zona desde `/settings/timezone`.

`tests/test_recurrence.py` y `tests/test_recurrence_migration.py` añaden pruebas de calendario, intervalos, límites, idempotencia, cancelación, recordatorios, zonas por organización, permisos, CSRF y preservación/repetición/rollback de la migración. Los datos de prueba se revierten.

No se implementan IA, notificaciones push o del sistema operativo, correo automático ni sincronización de calendario. La conexión Microsoft y la lectura controlada de correo se describen a continuación.

## Conectar una cuenta Microsoft (primera etapa)

Cada usuario autenticado puede conectar **una cuenta Microsoft propia** desde **Microsoft**, en la navegación. El flujo de conexión obtiene únicamente el perfil de `GET https://graph.microsoft.com/v1.0/me`. La Fase 1B, descrita al final de este documento, utiliza el permiso delegado `Mail.Read` ya consentido para consultar mensajes bajo demanda en `/mail`.

### Registrar la aplicación en Microsoft Entra

1. Entra en [Microsoft Entra admin center](https://entra.microsoft.com/) con una cuenta que pueda registrar aplicaciones. Abre **Entra ID → App registrations / Registros de aplicaciones → New registration / Nuevo registro**. Pon un nombre, por ejemplo **Atenea**.
2. Elige los tipos de cuenta que admitirás. Para Microsoft 365 y Outlook.com personales, elige **Accounts in any organizational directory and personal Microsoft accounts** y configura `MICROSOFT_TENANT=common`. Si solo admitirás cuentas corporativas de cualquier organización, elige la opción multitenant corporativa y usa `organizations`. Si solo admitirás tu directorio, elige **Single tenant** y usa su **Directory (tenant) ID**. Este tenant de Microsoft es independiente de las organizaciones internas de Atenea.
3. En **Redirect URI**, elige plataforma **Web** y registra exactamente `http://localhost:8000/integrations/microsoft/callback` para desarrollo. Pulsa **Register**. Abre Atenea también desde `http://localhost:8000` para conservar la cookie al volver: no mezcles `localhost` y `127.0.0.1`. En producción registra tu URL HTTPS con esa misma ruta; valor, puerto y ruta deben coincidir con `MICROSOFT_REDIRECT_URI`.
4. En **Overview / Información general**, copia **Application (client) ID** a `MICROSOFT_CLIENT_ID` en tu `.env` local. Si elegiste single tenant, copia **Directory (tenant) ID** a `MICROSOFT_TENANT`.
5. En **Certificates & secrets → Client secrets → New client secret**, crea un secreto con vencimiento acorde a tu operación. Copia su **Value / Valor**, no su Secret ID, a `MICROSOFT_CLIENT_SECRET`. No lo pegues en código, tickets, capturas o logs. Guarda y renueva el secreto antes de que venza.
6. En **API permissions → Add a permission → Microsoft Graph → Delegated permissions**, configura exactamente `openid`, `profile`, `offline_access`, `User.Read` y `Mail.Read`. No añadas permisos de aplicación, `Mail.Send` ni `Mail.ReadWrite`. Si las políticas del directorio impiden el consentimiento del usuario, un administrador deberá conceder el consentimiento correspondiente.
7. En **Authentication**, confirma la plataforma **Web**. Mantén desmarcadas las opciones de emisión implícita de access tokens e ID tokens y deshabilitados los flujos de cliente público. Atenea usa Authorization Code Flow con cliente confidencial MSAL y PKCE, sin flujo implícito.

Referencias oficiales: [registro de aplicaciones](https://learn.microsoft.com/en-us/entra/identity-platform/quickstart-register-app), [MSAL Python y Authorization Code Flow](https://msal-python.readthedocs.io/en/latest/), [permisos y acceso delegado de Graph](https://learn.microsoft.com/en-us/graph/auth-v2-user), [perfil `/me` en Graph v1.0](https://learn.microsoft.com/en-us/graph/api/user-get?view=graph-rest-1.0).

### Configurar `.env` e iniciar

No reemplaces tu `.env` existente con `.env.example`: conserva `DATABASE_URL`, `SESSION_SECRET` y las demás opciones actuales. El ejemplo contiene solo placeholders `replace_me`, que debes sustituir localmente. Las variables nuevas son:

| Variable | Valor local que debes configurar |
| --- | --- |
| `MICROSOFT_CLIENT_ID` | Application (client) ID de la App Registration. |
| `MICROSOFT_CLIENT_SECRET` | Valor del secreto de cliente. |
| `MICROSOFT_TENANT` | `common`, `organizations` o ID del directorio, según la opción registrada. |
| `MICROSOFT_REDIRECT_URI` | URL Web registrada; desarrollo: `http://localhost:8000/integrations/microsoft/callback`. |
| `TOKEN_ENCRYPTION_KEY` | Clave Fernet aleatoria, diferente de `SESSION_SECRET`, compartida por todos los procesos de Atenea. |

Si partes del ejemplo completo, configura también `APP_TIMEZONE` con una zona IANA, `SESSION_COOKIE_SECURE=false` para desarrollo HTTP (`true` en producción HTTPS), tu conexión PostgreSQL y un `SESSION_SECRET` aleatorio. Los placeholders no son configuración válida.

Instala las dependencias y genera la clave de cifrado directamente en el `.env` ignorado por Git, sin imprimirla ni sustituir una clave existente:

```powershell
& .\.venv\Scripts\python.exe -m pip install -r requirements.txt
& .\.venv\Scripts\python.exe -c "from cryptography.fernet import Fernet; from dotenv import dotenv_values, set_key; v = dotenv_values('.env', interpolate=False); _ = set_key('.env', 'TOKEN_ENCRYPTION_KEY', Fernet.generate_key().decode()) if v.get('TOKEN_ENCRYPTION_KEY') in (None, '', 'replace_me') else None"
& .\.venv\Scripts\python.exe -m app.migrate
& .\.venv\Scripts\python.exe -m uvicorn app.main:create_app --factory --reload --host 127.0.0.1 --port 8000 --no-access-log
```

Abre `http://localhost:8000/login`, inicia sesión en Atenea y visita `http://localhost:8000/integrations/microsoft`. Pulsa **Conectar Microsoft**, selecciona tu cuenta y acepta el consentimiento. El retorno debe mostrar **Conectado** y tu cuenta Microsoft. **Desconectar Microsoft** elimina la conexión local y cualquier intento pendiente de ese usuario.

La configuración Microsoft se valida al conectar; si falta o es inválida, el resto de Atenea puede arrancar y la pantalla indica que la integración está pendiente de configuración. La desconexión local sigue disponible aunque falte o haya cambiado la clave. No se generan claves ni se modifica `.env` al arrancar.

### Rutas y almacenamiento

| URL | Método | Función |
| --- | --- | --- |
| `/integrations/microsoft` | GET | Estado e identidad de la conexión propia; formulario conectar/desconectar. |
| `/integrations/microsoft/connect` | POST + CSRF | Inicia OAuth y redirige a Microsoft. |
| `/integrations/microsoft/callback` | GET | Valida state/sesión, intercambia el código, obtiene `/me` y guarda la conexión cifrada. |
| `/integrations/microsoft/disconnect` | POST + CSRF | Borra la cuenta local, su cache y su flujo pendiente. |

Todas requieren usuario y organización activos. Ninguna acepta del navegador una identidad Atenea para vincular la cuenta. El callback rechaza IDs de propietario en la URL; los formularios obtienen ambos IDs exclusivamente de la sesión verificada, incluso si se añaden campos extra. Administradores y usuarios tienen el mismo aislamiento personal.

La migración aditiva **`005_microsoft_accounts.sql`** crea `microsoft_accounts` y `microsoft_oauth_flows`; no modifica filas anteriores. La clave foránea compuesta `(organization_id, user_id)` impide mezclar organizaciones y usuarios. Solo puede existir una conexión y un intento pendiente por propietario. `app.migrate` verifica columnas, claves, restricciones y defaults, usa una transacción y permite repetir la migración sin borrar conexiones.

El cache de `msal.SerializableTokenCache`, incluidos los tokens que entregue Microsoft, se cifra con Fernet antes de escribirlo en PostgreSQL. El contenido cifrado incluye el usuario, organización y propósito para rechazar el intercambio de blobs entre propietarios. La lectura del cache desde ORM requiere una carga explícita; la pantalla solo consulta identidad. No hay cache global, archivos de tokens, tokens en cookies ni tokens en respuestas HTML.

El flujo OAuth completo, con state, nonce y verificador PKCE, también se cifra en PostgreSQL. Los hashes de state y del token CSRF de la sesión vinculan el retorno al usuario y sesión originales. Los intentos caducan a los **10 minutos** y se consumen una sola vez, también ante errores del proveedor tras validar el state. Volver a conectar sustituye el intento previo. Los intentos abandonados permanecen cifrados e inutilizables tras caducar hasta la siguiente conexión o desconexión de ese propietario; no se incorpora un proceso automático de limpieza. Login en otra sesión o cambio de configuración de cliente/tenant/retorno exige reiniciar el flujo.

El callback usa `response_mode=query` (GET) para que el navegador envíe la cookie existente `SameSite=Lax` al volver desde Microsoft. MSAL emite una advertencia informativa recomendando `form_post`; un POST entre sitios requeriría otro mecanismo de continuidad de sesión. Se mantienen PKCE, nonce, state de uso único, redirección inmediata a URL limpia y protección de logs/referrer. La CSP permite `https://login.microsoftonline.com` en `form-action` únicamente para la pantalla de integración y su POST de conexión, porque Chromium también comprueba el destino del 303.

Connect, callback y disconnect serializan las escrituras por usuario en PostgreSQL. Un fallo al reconectar conserva la conexión anterior. Disconnect elimina físicamente las filas locales y su cache: no cierra la sesión de Microsoft ni revoca el consentimiento en Entra. Para revocar ese consentimiento, el usuario o administrador debe hacerlo en Microsoft. La eliminación de PostgreSQL no purga copias históricas de backups: protégelos junto con sus políticas de retención.

Mantén la clave Fernet fuera de Git y separada de los backups de PostgreSQL. Perderla impide descifrar las conexiones existentes. Esta etapa no implementa rotación automática: para cambiarla sin migrar caches, desconecta las cuentas localmente, cambia la clave en todos los procesos y vuelve a conectarlas. Los logs de MSAL y su transporte se deshabilitan para evitar respuestas de tokens en DEBUG; Uvicorn omite las peticiones del callback mediante un filtro adicional. Conserva `--no-access-log` y configura el proxy/APM para no registrar query strings del callback, cuerpos, encabezados Authorization ni cookies. El callback redirige inmediatamente a una URL limpia y las respuestas llevan `no-store` y `no-referrer`.

### Verificación sin Microsoft real

```powershell
& .\.venv\Scripts\python.exe -m unittest discover -s tests -v
& .\.venv\Scripts\python.exe -m pip check
& .\.venv\Scripts\python.exe -m unittest discover -s tests -p test_uvicorn_startup.py -v
```

`tests/test_microsoft.py` simula MSAL y Graph, bloquea el transporte real y prueba el usuario desconectado, scopes, state, PKCE, sesión original, callback correcto, aislamiento por usuario/organización, cifrado, ausencia de secretos en respuestas/logs y desconexión. Incluye un contrato offline del inicio OAuth con MSAL real y metadatos simulados. `tests/test_microsoft_migration.py` comprueba creación, repetición, preservación, rollback y rechazo de esquemas incompatibles. `tests/test_uvicorn_startup.py` comprueba HTTP de Uvicorn, incluyendo las nuevas páginas protegidas. Estas pruebas no verifican una App Registration real ni realizan llamadas reales a Graph; el consentimiento interactivo queda como paso manual después de configurar Entra.

## Fase 1B: lectura controlada del buzón

Con la conexión Microsoft existente, abre **Correo** en la navegación o `http://localhost:8000/mail`. No hay nuevas variables `.env`, permisos, dependencias ni migraciones. Los permisos siguen siendo `openid`, `profile`, `offline_access`, `User.Read` y `Mail.Read`; las adquisiciones silenciosas del buzón piden exclusivamente `Mail.Read` delegado. No se consultan buzones ajenos, aunque el usuario sea administrador de Atenea.

| Ruta | Método | Comportamiento |
| --- | --- | --- |
| `/mail` | GET | Lista de hasta 20 correos, ordenados por recepción descendente. |
| `/mail/{message_id}` | GET | Consulta de un único mensaje, con cuerpo en texto, destinatarios y CC. |

Cada visita vuelve a consultar Microsoft Graph v1.0. Se usa `/me/messages` para el buzón completo (no solo la carpeta Entrada) y `/me/messages/{id}` para el detalle. No se marca el mensaje como leído al abrirlo. La pantalla sin conexión ofrece un enlace a `/integrations/microsoft` y no llama a Microsoft.

### Filtros y paginación

Los filtros son `state=all|unread|read` e `importance=all|low|normal|high`. Se rechazan parámetros desconocidos, repetidos y valores fuera de esos enums; no se admite OData arbitrario. El filtro de fecha base aparece antes de `isRead` e `importance` para cumplir las reglas de Graph al combinar `$filter` con `$orderby`. Los asuntos, previews y remitentes no se guardan para buscar ni filtrar localmente.

**La búsqueda por texto no se implementa en esta fase.** Se prioriza la combinación estable de filtros y orden por recepción: `$search` de mensajes tiene su propio orden por envío y un máximo de 1.000 resultados; no se mezcla con la consulta ordenada del listado ni se simula una búsqueda parcial sobre 20 mensajes. Referencias: [reglas de filter/orderby](https://learn.microsoft.com/en-us/graph/api/user-list-messages?view=graph-rest-1.0), [restricciones de búsqueda de mensajes](https://learn.microsoft.com/en-us/graph/search-query-parameter).

**Siguiente página** utiliza el `@odata.nextLink` original, sin reconstruir `$skip` ni `$skiptoken`. La URL queda dentro de un cursor Fernet autenticado y cifrado, válido durante **15 minutos**, ligado al usuario, organización, sesión, conexión Microsoft y filtros. No se guarda en PostgreSQL ni se almacena historial de mensajes. Cambiar filtros vuelve a la primera página. Se ofrece **Volver al inicio**, sin salto a página N ni botón de página anterior; el botón Atrás del navegador conserva la URL anterior, que puede volver a consultarse mientras el cursor sea válido.

Antes de enviar un Bearer, se exige HTTPS y el host exacto `graph.microsoft.com`, sin credenciales, fragmentos, puertos alternativos ni redirecciones HTTP. Los nextLink se limitan a `/v1.0/me/messages` o su forma canónica con el ID de **esa misma cuenta**; además deben conservar `$select`, `$top`, `$orderby` y los filtros originales. Se rechazan expansiones y campos adicionales. El navegador solo proporciona el cursor emitido por Atenea, nunca una URL externa a solicitar. Un nextLink de formato inesperado se rechaza y permite volver al inicio. [Paginación oficial de Graph](https://learn.microsoft.com/en-us/graph/paging).

### Cliente Graph y tokens

`app/graph.py` concentra el transporte de lectura y el manejo de errores. Recupera la cuenta por ambos IDs de la sesión, descifra el cache existente y utiliza `acquire_token_silent_with_error`. Se exige un único usuario MSAL dentro del cache propio, creado por la conexión 1A; un cache vacío, ambiguo o imposible de descifrar pide reconectar. No se compara el `realm` interno de MSAL con el tenant del perfil, porque al autenticar mediante `common` pueden diferir. No hay tokens globales.

La adquisición silenciosa y la persistencia del cache usan el mismo bloqueo por propietario que connect/callback/disconnect. Una renovación cifra el cache completo antes de actualizarlo, incluyendo una eventual rotación del refresh token. Si no cambió, no se escribe. El bloqueo termina antes de consultar mensajes a Graph. Una desconexión impide consultas posteriores y no puede ser revertida por una escritura tardía del cache; una petición que ya salió hacia Graph puede terminar. Reconectar invalida los cursores anteriores.

El transporte usa timeouts explícitos de **5 segundos de conexión y 15 de lectura**, con redirecciones deshabilitadas. Hay como máximo **un reintento total** por consulta:

- `401`: fuerza una adquisición silenciosa y reintenta una vez; si persiste, solicita reconexión.
- `403`: informa del acceso/consentimiento insuficiente sin cambiar permisos.
- `404`: informa de que el mensaje no está disponible.
- `429`: interpreta `Retry-After` como segundos o fecha HTTP. Si la espera es de hasta 2 segundos, espera y reintenta una vez. Para esperas mayores no mantiene bloqueada la petición: devuelve una pantalla 429 y el encabezado `Retry-After`. Si falta o es inválido, indica 30 segundos sin reintento inmediato.
- `5xx`, fallos de transporte y timeout: muestran mensajes amigables y permiten reintentar manualmente; no hay bucles ni reintentos en background.

Nunca se muestra el JSON ni el texto de error interno del proveedor. [Guía oficial sobre throttling y Retry-After](https://learn.microsoft.com/en-us/graph/throttling).

### Cuerpo, fechas y privacidad

El listado pide únicamente `id`, `subject`, `from`, `receivedDateTime`, `isRead`, `hasAttachments`, `importance`, `bodyPreview` y `webLink`: **no solicita `body` ni adjuntos**. El detalle añade solo los campos necesarios de destinatarios, CC, fecha de envío y cuerpo. `hasAttachments` es un indicador; nunca se consulta la colección de adjuntos ni se descarga contenido.

Se solicita `Prefer: outlook.body-content-type="text"`. Si Graph devuelve HTML, se extrae solamente texto mediante `html.parser.HTMLParser`, descartando atributos y contenido de script, style, iframe, form, object, SVG y otros elementos ejecutables/embebidos. **La salida siempre pasa por el autoescape de Jinja; nunca se renderiza HTML del correo con `safe`**, incluso si Graph lo etiqueta erróneamente como texto. Es una vista textual, no un sanitizador que intente conservar HTML. Por ello no se necesita añadir una biblioteca de sanitización. No se insertan imágenes, estilos, formularios, enlaces ni recursos remotos del cuerpo; la CSP existente añade otra barrera. [Preferencia de texto de Graph](https://learn.microsoft.com/en-us/graph/api/message-get?view=graph-rest-1.0).

Se limita la representación del cuerpo a un millón de caracteres, con aviso si se recorta, y el preview a 240. La vista pierde formato HTML y no hace clicables los enlaces del cuerpo. El enlace opcional **Abrir en Outlook** solo acepta HTTPS en `outlook.office.com`, `outlook.office365.com` y `outlook.live.com`, usando `noopener noreferrer`. Las fechas con offset/UTC se convierten mediante `organization_zone`; fechas sin zona o inválidas muestran «Fecha no disponible», sin suponer la zona del servidor.

**No se crean tablas, archivos ni caches persistentes de correo.** Asuntos, remitentes, destinatarios, previews y cuerpos solo viven durante la petición/respuesta. Las únicas escrituras del módulo son cambios del cache MSAL cifrado en la cuenta existente. La sesión no contiene mensajes; los cursores solo contienen navegación y su vinculación. Todas las páginas conservan `Cache-Control: no-store` y `Referrer-Policy: no-referrer`.

El logger `atenea.graph` registra únicamente endpoint lógico (`token`, `messages`, `message`), estado técnico/HTTP y, si tiene formato UUID, `request-id`. No registra URLs, filtros, IDs de mensajes, Authorization, tokens, cache descifrado ni cuerpos. El filtro de Uvicorn omite también `/mail` y sus detalles/cursores. Mantén `--no-access-log` y configura proxies/APM para no registrar estas URLs, cuerpos o encabezados sensibles.

### Validación y uso manual

`tests/test_mail.py` cubre autenticación, cuenta desconectada, listado/select, detalle, aislamiento de usuarios/organizaciones/administradores, filtros, IDs inválidos, cursores alterados/caducados y SSRF, `401/403/404/429/5xx`, redirecciones, timeout, HTML hostil, timezone, cache corrupto, desconexión, ausencia de contenido en SQL y logs y renovación cifrada. Incluye MSAL real con transporte simulado para comprobar tanto un cache `common` vigente como la renovación de un token vencido. Se bloquea todo transporte Microsoft real durante las pruebas.

Ejecuta la suite y `pip check` con los comandos anteriores. La prueba de Uvicorn también verifica que `/mail` y `/mail/{message_id}` redirijan al login sin sesión.

Para probar manualmente con tu conexión ya operativa: reinicia Uvicorn, inicia sesión, abre **Correo**, cambia estado/importancia, abre un mensaje y comprueba destinatarios, fechas y texto. Usa **Siguiente página** si hay más resultados. No necesitas modificar Entra ni `.env`. Si Microsoft requiere interacción por caducidad/revocación de la sesión, desconecta y reconecta desde **Microsoft**. No se implementan envíos, respuestas, modificaciones del buzón, adjuntos, sincronización, webhooks, IA, clasificación ni resúmenes.

## Correo → tarea: empresa o institución por dominio

Desde el detalle de correo pulsa **Crear tarea desde este correo**. La ruta **`GET /mail/{message_id}/create-task`** consulta únicamente `id`, `subject`, `from` y `bodyPreview` en el buzón del usuario conectado. Propone el asunto como título editable, deja la descripción vacía y reutiliza los campos de tareas (cliente, proyecto, vencimiento, prioridad, recordatorio y recurrencia). Desde Fase 1E usa hasta 240 caracteres del preview para sugerir la prioridad; no consulta el cuerpo completo ni copia el preview a la tarea. Las notas de descripción son las que el usuario escribe en el formulario.

La sección **Empresa / Institución detectada** normaliza el dominio del remitente a minúsculas, sin inferir identidad a partir del nombre visible. La comparación es por dominio exacto y organización actual; no se comparte entre organizaciones ni se busca por el nombre sugerido. La prioridad es: asociación existente → mapping explícito → sugerencia básica del primer componente del dominio. Un cliente existente conserva su nombre y tipo, aunque difieran de la sugerencia.

`app/client_domains.py` centraliza `DOMAIN_NAMES`, `PUBLIC_EMAIL_DOMAINS` y `CLIENT_TYPES`. Los overrides iniciales son `corporativa.cr → Corporativa`, `dma.com.sv → DMA` y `defensoria.gob.sv → Defensoría del Consumidor`. Se pueden ampliar editando esas estructuras, sin tocar rutas ni templates. Los dominios `.gob.sv` sugieren `institucion_publica`; el resto de dominios no públicos sugieren `empresa`. `otro` está disponible para correcciones. Estas reglas son configurables y no usan IA, DNS ni servicios externos.

Gmail, Outlook, Hotmail, Yahoo, iCloud y los otros proveedores centralizados (incluidos sus subdominios) dejan el cliente sin preseleccionar y permiten selección manual. No se crean clientes llamados Gmail/Outlook ni se permite asociar un dominio de proveedor público a un único cliente desde el catálogo. La lista no pretende ser exhaustiva: añade nuevos proveedores a `PUBLIC_EMAIL_DOMAINS` conforme sea necesario.

Si el dominio no tiene asociación, se muestra **No existe todavía en Atenea**. Los administradores pueden desplegar **Crear cliente/institución a partir del remitente**, corregir el nombre y el tipo, y confirmar mediante **POST + CSRF**. El dominio es el del remitente leído de nuevo desde Graph: el navegador no puede sustituirlo. Crear el cliente conserva los campos de la tarea, lo deja seleccionado y todavía no guarda la tarea. **Crear tarea** requiere un segundo envío explícito. Abrir el correo o el formulario mediante GET no crea clientes ni tareas.

Se conserva el permiso previo del catálogo: **solo administradores crean clientes**. Otros usuarios pueden seleccionar clientes existentes y guardar sus propias tareas. Un cliente archivado mantiene reservado el dominio, se muestra como archivado y debe reactivarse antes de usarlo en una tarea nueva. Si otro proceso crea la asociación antes de confirmar, se reutiliza el cliente existente sin renombrarlo ni duplicarlo.

Los administradores también pueden configurar `email_domain` y `client_type` desde **Clientes → Editar**, para asociar dominios a clientes previos. La coincidencia de nombres por sí sola no autoriza asociar un dominio automáticamente: si el nombre sugerido ya existe sin dominio, usa Editar para registrar la asociación en ese cliente. Las ediciones antiguas que omitan estos campos conservan sus valores.

### Migración 006 y conservación de datos

Ejecuta `python -m app.migrate` antes de iniciar esta versión en otro entorno. La migración aditiva **`006_client_email_domains.sql`**, con sus restricciones aplicadas por el ejecutor en la misma transacción, añade:

- `clients.email_domain VARCHAR(253) NULL`: los clientes existentes quedan con dominio vacío.
- `clients.client_type VARCHAR(30) NOT NULL DEFAULT 'otro'`: valor neutral para registros existentes, sin reclasificarlos por su nombre.
- Unicidad de `(organization_id, email_domain)`, permitiendo varios NULL y el mismo dominio en organizaciones distintas.
- CHECKs para los tres tipos admitidos y dominios normalizados en minúsculas.

No se elimina ni modifica el nombre, código, estado, ID o relaciones de ningún cliente. La normalización se realiza al guardar datos nuevos/editados; la migración no infiere dominios ni renombra datos existentes. El ejecutor usa los mismos límites de bloqueo, transacción y verificaciones que las migraciones anteriores. Se puede repetir sin restablecer valores.

### Seguridad y alcance del flujo de tarea

Los POST van a **`/mail/{message_id}/create-task`**, con acción explícita `create_client` o `create_task`. Además del CSRF, el formulario contiene un contexto cifrado ligado a usuario, organización, sesión, conexión y mensaje, válido 30 minutos. Nunca contiene el cuerpo. Se rechazan campos de propietario o referencias de origen enviados por el navegador. El mensaje se vuelve a consultar en el buzón propio antes de guardar; una desconexión o cambio de cuenta invalida el formulario.

Las creaciones de clientes comparten el bloqueo de organización del catálogo y la restricción única protege también la base. Las tareas guardan únicamente el título y notas confirmadas por el usuario, sus campos normales y `source_type=microsoft_mail` con una referencia hash del ID de cuenta y mensaje en `source_id`. El guardado se serializa por usuario: repetir el envío devuelve la tarea original en vez de crear otra, sin sobrescribir sus ediciones. Si el mensaje cambia de ID al moverse en Outlook, esta referencia no detecta que se trata del mismo mensaje; no se modifica la estrategia de IDs de Graph en esta fase.

No se autentica una empresa por su dominio remitente: una dirección puede ser suplantada y toda sugerencia debe revisarse. No se hacen modificaciones en Microsoft, no se cambian permisos y no hay IA ni almacenamiento automático del cuerpo completo. No se añaden dependencias ni variables `.env`.

Este repositorio no contenía un flujo anterior de Fase 1C al implementar esta ampliación. El alcance añadido es el formulario manual descrito aquí; no se presupone ninguna otra funcionalidad de una especificación de Fase 1C no incluida.

`tests/test_mail_tasks.py` comprueba los mappings, proveedores públicos, precedencia de datos guardados, tipos, normalización, correcciones, CSRF, GET sin escrituras, asociación y selección del cliente, duplicados, clientes archivados, permisos, aislamiento, contexto cifrado, creación de tarea sin cuerpo y doble envío. `tests/test_client_domains_migration.py` comprueba preservación, defaults, repetición, unicidad por organización, restricciones, rollback y rechazo de esquemas incompatibles. Microsoft está simulado en todas estas pruebas.

## Fase 1D: recordatorios activos y navegación

El centro **Avisos** (`GET /notifications`) muestra únicamente tareas propias pendientes con `reminder_at`: título, cliente/proyecto, fecha/hora local, prioridad y estado. Clasifica cada recordatorio como **Vencido**, **Hoy** o **Próximo**, según la zona IANA configurada por la organización. Ordena primero vencidos, después urgentes dentro de cada grupo y luego fecha/hora e ID. Pagina a 30 filas, sin descartar recordatorios futuros. Las tareas completadas, canceladas o sin recordatorio dejan de aparecer.

La campana muestra los vencidos y los que están programados hasta dentro de **15 minutos**, incluyendo el instante actual; el badge se limita visualmente a `9+`. `GET /notifications/status` devuelve exclusivamente `pending` (cantidad que requiere atención), `overdue` y `next_at` (próximo instante en ISO 8601 UTC, o null). No devuelve IDs, títulos, cuerpos, clientes ni información de Microsoft. Tanto el centro como el contador validan la sesión, el usuario activo y la organización activa. Un administrador tampoco accede a recordatorios ajenos. Los parámetros de propietario se rechazan; el ámbito siempre sale de la sesión.

Las acciones tienen POST + CSRF y bloqueo de la tarea propietaria:

- `POST /notifications/{task_id}/snooze`, con `delay=15m`, `1h` o `tomorrow`, actualiza únicamente `reminder_at`. Los minutos/horas se cuentan desde el momento de pulsar. **Mañana** significa las **09:00 del siguiente día civil en la organización**, teniendo en cuenta el cambio estacional. No cambia el vencimiento de la tarea. Para elegir otra fecha/hora, abre el título y edita su recordatorio en el formulario existente.
- `POST /notifications/{task_id}/complete` reutiliza `tasks.set_status`: completa la ocurrencia y genera su sucesora con las reglas existentes. Repetir el completado no crea otra sucesora. Una tarea cancelada no puede completarse desde un aviso antiguo.

Posponer 15 minutos puede mantener el badge: el recordatorio queda dentro de la ventana de próximos 15 minutos, pero ya no está vencido. En tareas recurrentes se conserva el comportamiento previo: la sucesora calcula su recordatorio a partir del recordatorio actual de la ocurrencia, incluido un snooze realizado antes de completar.

`app/static/workspace.js` consulta el estado al abrir una pantalla autenticada y después cada **60 segundos**, con timeout de 10 segundos, sin peticiones solapadas y sin consultar mientras la pestaña está oculta. Una sesión caducada detiene el polling; un fallo temporal espera al siguiente ciclo. La CSP permite únicamente conexiones al mismo origen. No hay WebSockets, scheduler, push ni llamadas a Graph para estas notificaciones.

El banner es genérico, discreto y se puede cerrar. Para no repetirlo al navegar o recargar, `sessionStorage` guarda solo la marca «mostrado», separada por usuario y organización, **sin contenido de tareas ni credenciales**. Se muestra una vez por episodio con recordatorios que requieren atención; se habilita de nuevo después de que el contador llegue a cero. Abrir el centro también cuenta como haber visto el aviso. El contador sigue actualizándose aunque el banner esté cerrado. Limitación deliberada: la marca es por pestaña, no se sincroniza entre dispositivos; los recordatorios añadidos durante el mismo episodio actualizan el contador sin abrir otro banner. Si el navegador bloquea storage, solo se evita la repetición dentro de la página actual. Sin JavaScript siguen funcionando el centro, los enlaces y las acciones, pero no el contador ni el banner automático.

**No se requiere migración**: se reutilizan `tasks.reminder_at`, su índice existente y la lógica de tareas. Los GET no modifican la base. No se añaden dependencias de producción, variables `.env`, permisos de Microsoft ni cambios a datos existentes.

### Navegación y dashboard

La navegación queda agrupada en **Inicio**, **Trabajo** (tareas, registrar horas, historial), **Correo** (bandeja y conexión Microsoft), **Clientes** (clientes y proyectos), **Reportes** (vista semanal existente con exportación y reporte mensual), **Configuración** (días laborables y zona horaria, solo administradores), **Avisos** y cierre de sesión. El backend mantiene sus controles de permisos. Se resalta el grupo activo y el enlace actual con `aria-current`.

Los menús usan botones con `aria-expanded`/`aria-controls`, Tab, Enter/Espacio y Escape, foco visible y cierre al salir de la navegación. Hasta 1100 px se utiliza un botón **Menú** y submenús verticales; en escritorio son desplegables. Sin JavaScript los destinos permanecen visibles. No se incorporó framework ni SPA.

El dashboard conserva el estilo de Atenea con seis tarjetas enlazadas: tareas pendientes, vencidas, recordatorios/próxima hora, horas semanales, horas mensuales y estado local de la conexión Microsoft. Los períodos del dashboard se calculan con la fecha de la organización; comprobar la conexión no consulta Graph ni carga tokens. «Conectado» refleja la conexión local activa: no verifica en tiempo real si Microsoft exige volver a autenticarse.

### Validación y archivos de la fase

Archivos creados:

- `app/notifications.py`, `app/routers/notifications.py`.
- `app/templates/notifications.html`, `app/static/workspace.js`.
- `tests/test_notifications.py`, `tests/notification_browser_fixture.py`, `tests/check_notifications_browser.py`.

Archivos modificados: `app/main.py`, `app/worklog.py`, `app/templates/base.html`, `app/templates/workspace.html`, `app/templates/dashboard.html`, `app/static/styles.css`, `tests/test_uvicorn_startup.py` y este `README.md`.

Se añadieron **21 pruebas** de autenticación, aislamiento entre usuarios/organizaciones incluso para admins, límites del contador, clasificación/orden, zona horaria y DST, las tres opciones de snooze, CSRF, campos manipulados, completado idempotente/recurrencia, paginación, GET sin escrituras, respuestas mínimas, navegación/permisos y dashboard. La suite completa conserva las pruebas anteriores de Microsoft, correo, tareas, clientes, proyectos, horas y reportes: **280 pruebas aprobadas**. `pip check`: **No broken requirements found**. La prueba de arranque inicia un Uvicorn real y verifica también la protección de `/notifications` y `/notifications/status`.

Validación adicional en Chromium: **14 escenarios aprobados**, incluyendo usuarios normales y administradores en 1440, 1101, 1024, 768, 390 y 320 px; ausencia de overflow horizontal, menús por teclado, estados activos, badge `9+`, supresión del banner tras recargar, polling de 60 segundos y funcionamiento sin JavaScript. Se usan templates reales con datos sintéticos, endpoints de polling simulados y rollback de las filas de prueba; no se contacta Microsoft. No se ha validado en Safari/Firefox ni con lector de pantalla.

Para repetir las comprobaciones en PowerShell desde Atenea:

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -q
.\.venv\Scripts\python.exe -m pip check
```

La comprobación visual opcional necesita Playwright y Chromium en el intérprete que la ejecute (no son dependencias de Atenea). En este equipo están disponibles en el Python general:

```powershell
python tests/check_notifications_browser.py
```

Prueba manual: reinicia Uvicorn; entra en **Trabajo → Tareas**, crea una tarea con recordatorio unos minutos en el pasado y abre **Avisos**. Comprueba su clasificación, pospón una hora y verifica la nueva hora y el contador. Prueba «mañana» frente a la zona de **Configuración → Zona horaria**. Completa otra tarea recurrente y verifica que exista una sola sucesora. Recarga/navega para confirmar que el banner no se repita. En otra sesión de usuario u organización, comprueba que la tarea no aparezca. Reduce la ventana a móvil, abre Menú y prueba Tab/Enter/Escape. No se necesitan cambios en Entra ni `.env`.

## Fase 1E: clasificación de correo por reglas

`app/mail_rules.py` centraliza las categorías, palabras clave y precedencia. Calcula sugerencias al consultar `/mail`, su detalle y el formulario de tarea. No usa IA, servicios externos ni reglas aprendidas; no añade endpoints, tablas, migraciones, dependencias ni permisos. No escribe categorías en Outlook ni marca mensajes como leídos. Se conservan los GET de Graph v1.0 con `Mail.Read` delegado y el aislamiento del buzón por usuario/organización.

### Entradas, resultados y reglas configurables

El motor puro `classify(MailInput, ...)` analiza el **asunto (máximo 1000 caracteres)**, **bodyPreview (máximo 240)** y el dominio validado del email del remitente. No analiza el nombre visible como dirección ni el cuerpo completo, adjuntos, fecha/antigüedad o importancia de Outlook. Devuelve `Analysis` con categorías, prioridad, razones aptas para mostrar, señales estructuradas (`rule`, `source`, `term`) y la sugerencia de cliente/institución. No registra el texto del correo en logs.

Categorías disponibles: `urgente`, `legal`, `cobro_facturacion`, `reunion_cita`, `seguimiento`, `cliente`, `institucion_publica`, `interno`, `informativo` y `sin_clasificar`. Se permiten varias categorías simultáneas. Si no hay una categoría aplicable, se usa `sin_clasificar`.

Las reglas iniciales están en la tupla `RULES`; cada `Rule` declara identificador, categorías, prioridad y términos. Para ampliar palabras o ajustar prioridad, edita esa estructura y añade pruebas; los routers y templates no contienen reglas de clasificación. No hay interfaz administrativa para editarlas todavía.

| Señal | Categoría / prioridad sugerida |
| --- | --- |
| Urgente, inmediato, vence hoy, último día, requerimiento urgente | `urgente`; prioridad `urgent` |
| Cuanto antes, vencimiento, plazo, suspensión, incumplimiento | Prioridad `high`; la categoría depende de otras señales |
| Factura vencida / facturas vencidas | `cobro_facturacion`; `high` |
| Audiencia, citación, expediente, tribunal, juzgado, demanda, escrito, resolución, notificación, requerimiento, recurso, apelación | `legal`; `normal` |
| Factura, cobro, pago, saldo, mora, vencida, estado de cuenta | `cobro_facturacion`; `normal` |
| Reunión, Teams, Zoom, cita, convocatoria, agenda, calendar | `reunion_cita`; `normal` |
| Seguimiento, pendiente, recordar, confirmación, respuesta pendiente | `seguimiento`; `normal` |
| Boletín, newsletter, informativo, para su información | `informativo`; `low` |
| Sin señales de prioridad | `normal` |

La precedencia es **urgent > high > normal > low** entre las reglas de texto coincidentes; no se suman puntos. Repetir una palabra cien veces no aumenta prioridad. Un boletín que además menciona una audiencia queda normal; «audiencia + vence hoy» queda urgente. Los dominios aportan categorías, pero no elevan prioridad por sí solos. La importancia de Outlook se sigue mostrando y filtrando aparte, sin convertir automáticamente su valor «alta» en urgencia de Atenea.

Se normalizan mayúsculas y tildes Unicode, y se comparan palabras completas o frases contiguas. Por ejemplo, «mora» no coincide con «demora», ni «cita» con «solicita». Las frases no se forman uniendo el final del asunto con el principio del preview. Como precaución, `no` o `sin` dentro de las tres palabras anteriores suprimen esa coincidencia. Hay variantes plurales explícitas en las listas; no se aplica análisis semántico ni stemming.

### Dominios y aislamiento

Se reutilizan las reglas y mappings de `app/client_domains.py`. `suggest_clients` consulta todos los dominios de la página en **una sola consulta**, limitada a la organización de la sesión, sin crear ni modificar clientes:

- Un dominio ya registrado, incluso de cliente archivado, añade `cliente`; en detalle se identifica el archivo. La tarea sigue preseleccionando solamente clientes activos.
- `*.gob.sv` o un cliente registrado con tipo `institucion_publica` añade esa categoría, sin urgencia. `defensoria.gob.sv` conserva el nombre configurado «Defensoría del Consumidor» cuando aún no está asociado.
- Gmail, Hotmail, Outlook, Yahoo, iCloud y los proveedores centralizados no identifican clientes ni dominios internos, aunque haya una asociación heredada errónea. Sus asuntos y previews sí se analizan.
- Los nombres inferidos se distinguen de asociaciones guardadas; la creación de clientes sigue requiriendo confirmación y permisos existentes.

`INTERNAL_DOMAINS_BY_ORGANIZATION` es un diccionario vacío por defecto. Un operador puede configurarlo en Python usando el ID real de una organización y un `frozenset` de dominios cuya pertenencia haya comprobado por separado. No se debe rellenar a partir del email del usuario, nombre de organización o cuenta Microsoft: esos datos no prueban propiedad del dominio. La comparación es exacta, no incluye subdominios implícitamente y nunca acepta proveedores públicos. No hay un dominio verificado almacenado actualmente en `Organization`; por eso `interno` no se activa por defecto. Esta configuración se puede trasladar posteriormente a administración sin cambiar el motor.

### Bandeja, detalle y tareas

La bandeja muestra una fila discreta con prioridad sugerida, hasta dos categorías y cliente/institución cuando corresponde; el resto de categorías queda accesible en el detalle. Un mensaje sin señales relevantes no añade badges de clasificación. No se reordena la bandeja: conserva fecha descendente, filtros de Microsoft y paginación segura.

El detalle incorpora **Análisis por reglas**, con todas las categorías, prioridad, entidad detectada y motivos que identifican las palabras y su origen (asunto o vista previa). Distingue sugerencias de asociaciones registradas y recuerda que el dominio no autentica al remitente. El detalle solicita además `bodyPreview` para usar exactamente el mismo límite que el listado; el cuerpo sigue disponible únicamente en la vista de lectura existente y nunca se usa para clasificar.

Crear tarea utiliza la prioridad sugerida como valor inicial editable. El formulario consulta solamente `id,subject,from,bodyPreview`, sin `body`. La elección del usuario se conserva tras errores de validación o creación del cliente. Solo el POST confirmado guarda la prioridad seleccionada y los campos normales de la tarea; no guarda categorías, razones, preview ni cuerpo. No crea tareas automáticamente. Los POST siguen verificando CSRF, contexto cifrado, conexión y propietario.

### Validación y archivos

Archivos creados: `app/mail_rules.py`, `app/templates/mail_rule_analysis.html`, `tests/test_mail_rules.py`, `tests/mail_rules_browser_fixture.py`, `tests/check_mail_rules_browser.py`.

Archivos modificados: `app/client_domains.py`, `app/mail.py`, `app/mail_tasks.py`, `app/routers/mail.py`, `app/routers/mail_tasks.py`, `app/templates/mail_list.html`, `app/templates/mail_detail.html`, `app/templates/mail_task_suggestion.html`, `app/static/styles.css` y `README.md`.

Se añadieron **32 pruebas**: categorías y precedencia, tildes/mayúsculas, negación simple, límites/palabras completas, reglas configurables, exclusión de proveedores públicos, dominios internos explícitos, motivos/señales, prioridad inicial y corrección manual, GET sin escrituras, clasificación recalculada, cuerpo completo ignorado, logs/HTML seguros, consultas por lote, aislamiento de usuarios/organizaciones/admins y conservación de permisos/Graph de lectura. Toda la suite tiene **312 pruebas**, incluidos los módulos anteriores y el arranque real con Uvicorn. `pip check`: **No broken requirements found**.

Chromium aprobó cuatro escenarios de listado → detalle → formulario a 1440, 768, 390 y 320 px: badges, motivos, prioridad sugerida editable y ausencia de overflow horizontal. Se usan templates reales y datos/Graph simulados, con rollback de filas. Los POST de creación de tarea se prueban contra FastAPI/PostgreSQL en la suite. No se contactó una cuenta Microsoft real ni se modificó correo en Microsoft durante las pruebas.

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -q
.\.venv\Scripts\python.exe -m pip check
# QA opcional con el Python que ya dispone de Playwright y Chromium:
python tests/check_mail_rules_browser.py
```

Limitaciones: son coincidencias literales conservadoras, no comprensión del mensaje. Pueden confundirse referencias históricas, citas, negaciones complejas y términos ambiguos («recurso», «agenda», «escrito»). No se interpretan fechas de vencimiento ni autenticidad del remitente. El límite del preview puede omitir señales posteriores, y un texto o mapping modificado puede producir otra sugerencia al actualizar. No hay clasificación persistente, búsqueda/filtro local por categoría, editor administrativo ni cambios de etiquetas de Outlook.

Prueba manual: reinicia Atenea y abre **Correo → Bandeja**. En un mensaje existente con «audiencia» revisa Legal/Normal; con «audiencia» y «vence hoy», revisa Urgente. Abre el detalle para ver motivos, pulsa **Crear tarea desde este correo**, comprueba la prioridad inicial, cámbiala y confirma. Verifica la prioridad guardada en Tareas y repite con otro usuario para comprobar aislamiento. No necesitas modificar Entra, permisos ni `.env`.

## Fase 1F: análisis manual de correo con IA

La IA está **desactivada por defecto**. Abrir la bandeja, el detalle, el dashboard, el formulario normal de tarea o el polling de notificaciones **no llama a OpenAI**. En el detalle se muestra el proveedor y, antes del botón, el aviso: «Este correo será enviado al proveedor de IA configurado para generar el análisis». El envío requiere pulsar **Analizar con IA**, mediante POST autenticado con CSRF. No hay procesos automáticos ni análisis de lotes.

### Configuración y proveedor

Se utiliza la SDK oficial **`openai==3.20.0`**, fijada en `requirements.txt`, con **Responses API** y Structured Outputs mediante `responses.parse(text_format=EmailAnalysis)`. La instalación añade las dependencias transitivas necesarias de la SDK (en esta validación, `jiter==0.17.0` y `sniffio==1.3.1`); no se añadió otro framework. Referencia: [Structured Outputs de OpenAI](https://developers.openai.com/api/docs/guides/structured-outputs).

Instala las dependencias y configura localmente estas variables, manteniendo `.env` fuera de Git:

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

```env
AI_ENABLED=false
AI_PROVIDER=openai
OPENAI_API_KEY=replace_me
OPENAI_MODEL=replace_me
```

No hay modelo predeterminado: `OPENAI_MODEL` debe contener el identificador de un modelo disponible en tu proyecto OpenAI que admita Responses API y la salida JSON estructurada. No se comprueba su disponibilidad mediante llamadas al iniciar Atenea. Antes de activar, revisa con IT la autorización para enviar los correos, el modelo, los límites de gasto y los controles del proyecto. Después completa clave/modelo en `.env`, cambia explícitamente **`AI_ENABLED=true`** y reinicia Uvicorn. Una clave presente por sí sola no activa nada. Para desactivar, vuelve a `false` y reinicia.

Un flag ausente o diferente de `true` desactiva IA. Proveedor desconocido, clave vacía/placeholder o modelo vacío/placeholder muestran una explicación segura y dejan inhabilitado el botón; el resto de Atenea funciona normalmente. Una clave no autorizada o un modelo incompatible se detectan únicamente al solicitar un análisis y producen un error controlado. No se modificó el `.env` real durante esta fase; la comprobación local final encontró `AI_ENABLED=False`.

### Arquitectura y contrato de salida

- `app/ai_provider.py`: protocolo `AIProvider`, adaptador `OpenAIProvider`, disponibilidad/configuración, instrucciones fijas y traducción de errores. Es el único módulo con llamadas a la SDK.
- `app/ai_schema.py`: contrato Pydantic estricto independiente del proveedor; rechaza campos extra, tipos/enums incorrectos, fechas imposibles, horas inválidas y longitudes excesivas.
- `app/mail_ai.py`: minimización de datos, `analyze_email`, revalidación del resultado, selección conservadora de fechas y borrador cifrado efímero.
- `app/routers/mail_ai.py`: autorización, POST/CSRF, consulta al buzón propio y presentación/formulario. No contiene prompts ni llamadas directas a OpenAI.

`EmailAnalysis` exige: `summary`, `suggested_priority`, **`priority_reason`**, `categories`, `action_items`, `dates_found`, `entities`, `suggested_client`, `suggested_task_title`, `suggested_task_description`, `warnings` y `confidence`. Además de fecha/hora nullable, cada acción indica **`kind=explicit|inferred`**. Los nombres de cliente y textos sugeridos pueden ser null cuando no corresponden. Las categorías usan los diez valores de Fase 1E, y la prioridad sigue `low|normal|high|urgent`.

Se permite un resumen de hasta 1200 caracteres, explicación de prioridad de 500, hasta ocho acciones, ocho fechas, diez entidades y ocho advertencias. Título de tarea: 255 caracteres; descripción: 3000. Las fechas deben existir, usar `YYYY-MM-DD` y estar entre 1900 y 2100; la hora debe usar `HH:MM` y tener fecha. Un resultado incompleto, rechazo del proveedor, JSON inválido o incumplimiento de esquema muestra un error seguro, nunca JSON bruto ni información interna del proveedor.

### Datos y límites

Se construye un objeto de entrada mediante una lista explícita de campos:

| Enviado como contenido para analizar | Excluido del contenido |
| --- | --- |
| Asunto, máximo 1000 caracteres | Tokens/cache Microsoft, refresh tokens y headers OAuth |
| Email del remitente, máximo 320 | Client secret, claves de cifrado, contraseñas de Atenea, cookies y API keys |
| Fecha local de recepción y zona IANA de la organización | IDs de usuario/organización/cuenta/mensaje y nombre interno de organización |
| Texto del cuerpo extraído/sanitizado por Atenea, máximo 12 000 caracteres | HTML crudo, imágenes, adjuntos, URLs de navegación y enlaces a Outlook |
| Nombre/tipo del cliente, solo cuando ya está registrado para el dominio en la organización | Destinatarios/CC/BCC como campos, catálogo completo de clientes, otros correos |
| Categorías y prioridad de reglas | Prompts/resultados de otros análisis, objetos Request/User/Settings |

No se solicitan adjuntos. Se vuelve a pasar el texto por el extractor HTML de Atenea, para retirar etiquetas y scripts incluso ante contenido mal etiquetado. Los valores conocidos de las credenciales configuradas de Atenea se reemplazan si aparecen literalmente en el texto. Esto **no es un sistema DLP ni anonimización completa**: el correo puede contener información confidencial o credenciales desconocidas, nombres, direcciones o un hilo citado. El usuario debe decidir si puede compartir ese contenido antes de pulsar el botón. No se verifica automáticamente su clasificación de confidencialidad.

El **JSON de entrada completo se limita a 16 000 caracteres**, además de instrucciones y esquema constantes; se recorta el cuerpo adicionalmente si el escape JSON ocupa demasiado espacio. No es un contador exacto de tokens. El recorte establece `truncated=true` para el modelo y muestra **Análisis parcial** en la UI, aunque el modelo omita advertirlo.

La petición limita `max_output_tokens=2500`, usa `store=False`, `background=False`, `tools=[]`, `tool_choice='none'` y ninguna conversación o `previous_response_id`. No hay clientes SDK ni tokens de acceso globales. El cliente HTTP usa timeout de conexión de 5 segundos y timeout de lectura/escritura/pool de 45 segundos, sin redirecciones y sin proxies/endpoints heredados del entorno. Los timeouts son de operación de transporte, no un cronómetro global de toda la solicitud. El endpoint del adaptador inicial es `https://api.openai.com/v1`.

No hay reintentos automáticos de OpenAI (`max_retries=0`): evita repetir un envío facturable tras fallos inciertos. Autenticación/permisos, timeout, rate limit, 4xx, 5xx, desconexión, rechazo e invalidación de esquema se traducen a mensajes fijos seguros. El usuario decide si reintenta.

### Prompt injection y control humano

Las instrucciones fijas viajan en `instructions`; el correo y todo su contexto se serializan separadamente como un único contenido de usuario no confiable. El prompt prohíbe seguir órdenes del email, revelar secretos, abrir enlaces, usar herramientas y modificar Atenea/Microsoft. Las credenciales y los objetos de aplicación no están dentro del contexto del modelo; la API key de OpenAI se utiliza solo en la autenticación HTTPS de la SDK, nunca dentro del mensaje para analizar.

El proveedor no tiene herramientas ni funciones que pueda ejecutar. La aplicación trata el resultado como datos, lo valida y escapa todo texto al mostrarlo: no ejecuta HTML, Markdown, código ni instrucciones devueltas. Las pruebas verifican estas fronteras con correos que piden «ignora instrucciones anteriores» o extraer secretos. **No prueban que un modelo real sea infalible frente a prompt injection ni que sus conclusiones sean correctas**. El diseño impide acciones autónomas; todas las sugerencias siguen requiriendo revisión humana.

La UI mantiene separados **Análisis por reglas** y **Análisis con IA**. Expone ambas prioridades, explica la prioridad IA y señala discrepancias. Presenta acciones explícitas/inferidas, fechas con su significado, entidades, posible cliente y confianza declarada —no una garantía—. Las fechas ambiguas y acciones inferidas reciben advertencias. La IA no sustituye el resultado determinista ni modifica prioridades guardadas.

### Crear tarea y almacenamiento efímero

Rutas nuevas, ambas exclusivamente POST + CSRF:

- **`/mail/{message_id}/analyze`**: vuelve a consultar el mensaje en Graph usando exclusivamente `/me/messages/{id}` y la conexión actual del usuario; solo después prepara y envía el contenido a IA.
- **`/mail/{message_id}/ai/create-task`**: abre el formulario existente prellenado con la sugerencia. No llama a OpenAI ni guarda una tarea.

El título, descripción, prioridad y fecha/hora propuesta llegan a un formulario totalmente editable. Solo se prellena fecha cuando las acciones explícitas ofrecen un único plazo distinto; ante varios plazos, inferencias o una hora ambigua/inexistente por DST, se deja la fecha/hora vacía con advertencia. La zona enviada al modelo y usada para comprobar horas es la de la organización. Una fecha extraída no se convierte automáticamente en vencimiento.

El cliente se preselecciona mediante la asociación determinista de dominio en la organización; **el nombre sugerido por la IA no crea ni selecciona clientes por sí solo**. El POST final reutiliza la ruta de tarea desde correo, sus validaciones, CSRF, permisos e idempotencia. El usuario puede cambiar todos los campos antes de confirmar. Si ya existe la tarea vinculada a ese correo, se conserva el comportamiento anterior: se abre la existente sin sobrescribirla.

No se guardan cuerpo enviado, prompt, resumen ni resultado completo en PostgreSQL, sesión, localStorage o archivos. El resultado solo vive en la petición y la página mostrada, con `Cache-Control: no-store`; volver al detalle por GET lo descarta. Para abrir el formulario se incluye un **borrador cifrado en un campo oculto POST**, con únicamente los campos propuestos de tarea. Está ligado a usuario, organización, mensaje, conexión Microsoft y CSRF/sesión; caduca en **15 minutos**. No viaja en URLs ni cookies. Se valida y vuelve a comprobar el acceso al mensaje al abrir el formulario. **Los campos de tarea sí se almacenan como una tarea normal cuando el usuario confirma**, incluida la descripción sugerida que haya revisado.

Se impide el doble clic desde el frontend; una marca temporal en la sesión limita repeticiones habituales a una por minuto. Un bloqueo por usuario coordina análisis en curso y el cambio de conexión. La cookie no contiene resultados ni texto del correo. Este control no sustituye una cuota de gasto: otra sesión o una repetición deliberada puede generar más llamadas. Configura los controles de uso del proyecto OpenAI. No hay cuotas mensuales ni contabilidad de costes propia en esta fase.

### Información para IT / seguridad

- **Egreso:** el envío manual comunica texto y datos personales del correo a OpenAI mediante HTTPS. Autorizarlo según políticas de la organización antes de activar `AI_ENABLED`. El adaptador inicial no configura residencia regional ni un proxy empresarial.
- **Retención:** `store=False` evita solicitar almacenamiento de la respuesta como estado de aplicación; **no equivale a Zero Data Retention** ni garantiza ausencia de retención del proveedor. Deben revisarse los controles, monitoreo de abuso y condiciones del proyecto OpenAI. Según la documentación, el monitoreo de abuso puede conservar datos hasta 30 días por defecto; ZDR requiere los controles/acuerdos aplicables. Referencia: [Controles de datos de OpenAI](https://developers.openai.com/api/docs/guides/your-data).
- **Autorización:** sesión activa, usuario/organización activos, CSRF y conexión Microsoft propietaria. Los IDs de propietario recibidos del navegador se rechazan. Un administrador no adquiere acceso al buzón de otros usuarios.
- **Microsoft:** no hay cambios de App Registration, permisos Entra, scopes, mensajes ni etiquetas. Se conserva `Mail.Read` delegado, sin envío/modificación ni acceso a adjuntos.
- **Logs:** no se registran cuerpo, prompt, output completo, cookies, Authorization ni claves. Se desactiva logging sensible de SDK/transporte; los errores técnicos propios solo incluyen proveedor y código de resultado fijo. No habilitar captura externa de cuerpos HTTP ni logging indiscriminado de requests en proxies/APM.
- **Persistencia:** sin tablas nuevas ni migraciones. El cache MSAL cifrado mantiene su funcionamiento existente; los análisis no se guardan. Solo la tarea confirmada conserva los campos seleccionados por el usuario.
- **Límites de garantía:** esquema válido no significa hechos correctos; fechas, confianza e inferencias pueden ser erróneas. Los modelos compatibles y sus límites deben validarse con correos sintéticos autorizados antes de uso productivo.

### Pruebas, archivos y verificación manual

Archivos creados en Fase 1F: `app/ai_provider.py`, `app/ai_schema.py`, `app/mail_ai.py`, `app/routers/mail_ai.py`, `app/templates/mail_ai_panel.html`, `app/templates/mail_ai_error.html`, `tests/test_mail_ai.py`, `tests/mail_ai_browser_fixture.py`, `tests/check_mail_ai_browser.py`.

Archivos modificados en Fase 1F: `.env.example`, `requirements.txt`, `app/config.py`, `app/main.py`, `app/microsoft.py`, `app/routers/mail.py`, `app/routers/mail_tasks.py`, `app/templates/mail_detail.html`, `app/templates/mail_task_suggestion.html`, `app/static/workspace.js`, `app/static/styles.css` y `README.md`. Se conservaron los cambios previos de Fase 1E que ya estaban en el árbol de trabajo.

Se añadieron **40 pruebas**, incluyendo configuración/feature flag, autenticación, CSRF, aislamiento/admins, GET sin IA, truncado, sanitización/minimización, schema estricto, SDK real con transporte simulado, errores del proveedor, separación de instrucciones y prompt injection, logs seguros, borrador cifrado/caducidad, edición/confirmación de tarea, ausencia de escrituras de análisis y conservación de permisos Microsoft. **352 pruebas aprobadas** en la suite completa, incluidos los módulos anteriores y la prueba de arranque de un Uvicorn real. `pip check`: **No broken requirements found**. No se realizaron llamadas reales a OpenAI ni Microsoft.

Chromium verificó cuatro tamaños (1440, 768, 390 y 320 px): flag desactivado, proveedor mock habilitado, aviso de privacidad previo, ausencia de POST al listar/abrir, análisis explícito, resultados/prioridades diferenciadas y formulario editable, sin overflow horizontal. Los POST finales de creación se comprobaron contra FastAPI/PostgreSQL en la suite.

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -q
.\.venv\Scripts\python.exe -m pip check
# QA opcional, con el Python que dispone de Playwright/Chromium:
python tests/check_mail_ai_browser.py
```

Para probar manualmente: con `AI_ENABLED=false`, abre un detalle y comprueba el botón deshabilitado. Tras la autorización interna, configura clave y modelo compatibles, activa el flag y reinicia. Abre un correo de prueba autorizado, lee el aviso y pulsa **Analizar con IA**. Revisa resumen, motivos, acciones, fechas y discrepancias con reglas. Pulsa **Crear tarea con esta sugerencia**, modifica campos y confirma; verifica que solo se haya guardado esa tarea. Repite con otro usuario y comprueba aislamiento. No hace falta modificar Entra.

## Fase 2A — Diseño corporativo y calendario operativo

### Sistema visual

El layout compartido utiliza sidebar fija a la izquierda, compacta en tablet y desplegable
en móvil. Navegación: Inicio, Trabajo (incluye Calendario), Correo, Gestión, Reportes y
Configuración. Microsoft está disponible para cada usuario; días laborables y zona horaria
solo aparecen para administradores. La barra superior muestra contexto, campana, identidad
y acciones de tareas/calendario. La organización y la cuenta también aparecen al pie del menú.

Los colores semánticos se centralizan en `app/static/styles.css`: principal/secundario,
fondo/superficies, texto, bordes, éxito, advertencia, error, información y prioridades.
`app/static/corporate.css` aplica el layout y normaliza botones, formularios, estados disabled,
tablas, badges, errores, paginación y calendario usando esos tokens. Se usan fuentes del
sistema, sin servicios de fuentes externos. Las tablas anchas tienen desplazamiento interno
deliberado; no ensanchan la página.

Accesibilidad: enlace para saltar al contenido, foco visible, `aria-current`, grupos de
navegación con `aria-expanded`, labels y errores asociados a sus campos. El menú móvil
admite teclado, Escape, retorno del foco, fondo de cierre y contención del foco. Sin JS,
la navegación y los formularios siguen disponibles; la cuadrícula de calendario requiere JS.
Las categorías de eventos se identifican por texto además del color.

El dashboard conserva tareas pendientes/vencidas, recordatorios de tareas y eventos, horas y estado
Microsoft, y añade ocho próximos eventos de los siguientes 30 días, con enlace al calendario.
Se consulta un número acotado de filas por tipo; no se carga el historial completo.
Acciones rápidas: Nueva tarea, Nuevo evento, Registrar horas, Abrir correo e Informes.

### FullCalendar y dependencias

- **FullCalendar Standard 6.1.21, MIT**, fijado en la rama 6 compatible con la aplicación.
- **Luxon 3.7.2, MIT**, más `@fullcalendar/luxon3` 6.1.21 para zonas IANA.
- Archivos distribuidos localmente en `app/static/vendor/`, con licencias originales,
  orígenes y hashes SHA-256. No hay CDN en tiempo de ejecución, React, Vue, Scheduler ni Premium.
- No se añadieron dependencias Python ni variables de entorno. `.env` permanece excluido
  de Git y no se modificó. No hay cambios de Entra, permisos Graph ni activación de IA.

La rama 7 es más reciente, pero cambió a AGPLv3/comercial; se eligió explícitamente la
última 6.x documentada con MIT. Referencias oficiales:
[scripts Standard 6](https://legacy.fullcalendar.io/v6/initialize-globals),
[Luxon y zonas horarias](https://legacy.fullcalendar.io/v6/luxon),
[cambios en versión 7](https://fullcalendar.io/docs/upgrading-from-v6).

El calendario usa un nonce CSS aleatorio por respuesta y permite las fuentes de iconos
embebidas de la librería únicamente en esa página. No se habilitan scripts inline,
`unsafe-inline` ni dominios externos. Véase [CSP de FullCalendar](https://legacy.fullcalendar.io/v6/content-security-policy).

### Modelo y migración

Se revisó `Task`: su fecha límite, prioridad y recurrencia no representan adecuadamente
una audiencia con inicio/fin, ubicación y tribunal. Se añadió `CalendarEvent`, exclusivamente
para eventos que no sean tareas: audiencia, reunión, cita, recordatorio y otro.
El usuario responsable siempre es el usuario autenticado; no hay asignaciones a terceros.

Campos: `id`, `organization_id`, `user_id`, `event_type`, `title`, `description`,
`client_id`, `project_id`, `start_at`, `end_at`, `start_date`, `end_date`, `all_day`,
`location`, `institution`, `status`, `reminder_at`, `source_type`, `source_id`,
`created_at` y `updated_at`. Los campos de origen quedan nulos y reservados para futuras
integraciones; el navegador no puede asignarlos.

`migrations/007_calendar_events.sql` es **aditiva, transaccional y repetible**. Crea la tabla,
FKs compuestas de propietario/organización/cliente/proyecto, restricciones de tipo, estado,
fechas y coherencia, e índices por propietario/fecha/recordatorio, cliente y proyecto.
El ejecutor valida columnas, FKs, restricciones e índices y falla ante un esquema incompatible
sin reemplazarlo. No elimina ni transforma datos existentes. La migración quedó aplicada
y su repetición verificada en la base local durante esta implementación.

En otro entorno, aplicar antes de arrancar la nueva versión:

```powershell
.\.venv\Scripts\python.exe -m app.migrate
.\.venv\Scripts\python.exe -m uvicorn app.main:create_app --factory --host 127.0.0.1 --port 8000
```

### Rutas y comportamiento

| Método | Ruta | Uso |
|---|---|---|
| GET | `/calendar` | Calendario autenticado |
| GET | `/calendar/events?start=...&end=...` | JSON limitado al rango y propietario |
| GET / POST | `/calendar/new` | Formulario y creación de evento |
| GET | `/calendar/{event_id}` | Detalle privado del evento |
| GET / POST | `/calendar/{event_id}/edit` | Edición privada |
| POST | `/calendar/{event_id}/cancel` | Cancelación local, conserva el registro |
| GET | `/calendar/tasks/{task_id}` | Detalle de la Task existente |
| POST | `/notifications/events/{event_id}/snooze` | Posponer el recordatorio del evento privado |

Los POST necesitan CSRF. Editar, completar o cancelar una tarea reutiliza las rutas y lógica
de tareas. `/tasks/new` admite una fecha/hora inicial validada desde el calendario; no guarda
nada hasta confirmar el formulario. Elegir «Tarea» al crear un evento abre ese formulario.

Vistas: Mes (`dayGridMonth`), Semana (`timeGridWeek`), Día (`timeGridDay`) y Agenda
(`listWeek`), en español y comenzando en lunes. Móvil abre Agenda por legibilidad; permite
cambiar a cualquier vista. Semana/día muestran las 24 horas en una zona desplazable y abren
a las 07:00; se destacan 08:00–18:00 en los días laborables configurados por organización.
Seleccionar una fecha/hora abre el formulario. El detalle permite editar y cancelar; una
tarea pendiente permite completar o cancelar mediante el módulo existente.

Filtros: tareas, audiencias, reuniones, citas, recordatorios, otros, completados, cliente y
proyecto. Los filtros se validan en el servidor y se aplican al pulsar **Aplicar filtros**.
El rango es semiabierto `[start, end)`, positivo, máximo 93 días. Se aceptan fechas civiles
o timestamps ISO con offset; se rechazan timestamps sin zona. Máximo 2.000 entradas por
respuesta; si se supera se solicita reducir el rango/filtros, sin truncado silencioso.
No se aceptan `user_id`, `organization_id` ni parámetros desconocidos para escoger calendario.
La sesión define ambos ámbitos y un administrador no ve eventos privados ajenos.

Las tareas aparecen directamente desde `Task`, sin copias en `CalendarEvent` ni modificaciones
al consultar. Sin hora, se muestran todo el día; con hora, conservan su fecha/hora civil.
Solo aparecen las ocurrencias recurrentes ya generadas. Completar/cancelar conserva la
generación existente de la siguiente ocurrencia; consultar el calendario no genera tareas.

Los recordatorios de tareas y eventos se muestran con una etiqueta separada si difieren del
instante principal. Si coinciden, no se dibuja un duplicado. Los recordatorios propios del
tipo «Recordatorio» tampoco se duplican. Cancelar un evento retira su recordatorio y lo oculta
del calendario, manteniendo el registro. El centro **Avisos** combina recordatorios de
tareas pendientes y eventos programados, con un único contador, orden y paginación de 30
filas. Cada rama de la consulta aplica organización y usuario antes de combinar resultados;
solo se cargan los objetos de la página, sin duplicar notificaciones en otra tabla.
La campana, el banner y el dashboard reutilizan el mismo contador, sin exponer títulos
en `/notifications/status`. Se conserva la ventana de atención de 15 minutos y el orden:
vencidos, prioridad urgente de tareas y fecha; tipo/ID resuelven empates.

Los eventos tienen las mismas opciones para posponer: 15 minutos, una hora o mañana a las
09:00 de la organización, respetando DST. `POST /notifications/events/{event_id}/snooze`
exige sesión, CSRF, propiedad y estado programado con recordatorio, y bloquea la fila
durante la actualización. No cambia inicio, fin, tipo ni asociaciones. Posponer puede
llevar el aviso después del inicio del evento: es aplazar el aviso, no reprogramar el evento.
Al editar otros campos se conserva ese recordatorio, incluida su precisión de segundos;
un recordatorio nuevo introducido en el formulario debe seguir siendo anterior al inicio.
Para retirarlo, editar el evento y vaciar Recordatorio; cancelarlo también lo retira.
Los eventos no tienen la acción «Completar tarea». Las acciones y recurrencias de Task
siguen usando la lógica y URLs anteriores.
No se implementaron envíos externos, avisos push ni procesos de background.

### Zona horaria

Se usa siempre la zona IANA de la organización, independientemente de la zona del navegador.
Eventos con hora y recordatorios se almacenan como `TIMESTAMPTZ`/instantes UTC. El formulario
interpreta entradas en la zona de la organización y rechaza horas ambiguas o inexistentes
por DST. Inicio y fin se convierten individualmente, incluso al cruzar un cambio de offset.

Los eventos de día completo usan fechas civiles `start_date`/`end_date` y dejan nulos los
instantes: así no cambian de día al cambiar la zona. `end_date` es exclusiva internamente;
formulario y detalle muestran el último día inclusivo. El modelo valida que se utilice
exactamente una representación. Las tareas conservan su modelo civil y las reglas existentes
para recurrencias automáticas. Cambiar la zona de la organización cambia la presentación de
los eventos con hora, conservando su instante; no reprograma automáticamente sus instantes.

### Archivos de la fase

Nuevos:

- `app/calendar.py`, `app/routers/calendar.py`.
- `app/templates/calendar.html`, `calendar_form.html`, `calendar_detail.html`.
- `app/static/corporate.css`, `calendar.js`, `calendar-form.js`, `app/static/vendor/`.
- `migrations/007_calendar_events.sql`.
- `tests/test_calendar.py`, `test_calendar_migration.py`, `test_calendar_notifications.py`, `calendar_browser_fixture.py`, `check_calendar_browser.py`.

Modificados:

- `app/models.py`, `app/migrate.py`, `app/main.py`, `app/routers/tasks.py`.
- `app/notifications.py`, `app/routers/notifications.py`, `app/templates/notifications.html`.
- CSS: `app/static/styles.css`; JS compartido: `app/static/workspace.js`.
- Templates: `base.html`, `workspace.html`, `dashboard.html`.
- QA: `tests/check_notifications_browser.py`, `check_mail_rules_browser.py`, `check_mail_ai_browser.py`, `test_uvicorn_startup.py`.
- `README.md`.

Los demás módulos reciben el nuevo diseño mediante sus estilos y layout compartidos.

### Validación y prueba manual

Se añadieron 37 pruebas de calendario/migración y 14 de avisos de eventos: login, aislamiento entre usuarios y
organizaciones, admins, CSRF de creación/edición/cancelación, FKs, clientes/proyectos,
tipos, filtros, rangos, límites, tareas con/sin hora, recordatorios, DST, días completos,
cancelación idempotente, consultas sin duplicación, recurrencias, dashboard, CSP y navegación.
La ampliación cubre contador y paginación combinados, IDs iguales en ambas tablas,
snooze/CSRF, aislamiento incluyendo administradores, DST, cancelación, edición después
de posponer, actualización de calendario/dashboard y GET sin escrituras ni metadatos sensibles.
La suite anterior se conserva; Uvicorn también comprueba recursos y rutas nuevas.

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -q
.\.venv\Scripts\python.exe -m pip check
# QA con el intérprete que dispone de Playwright/Chromium:
python tests/check_calendar_browser.py
python tests/check_notifications_browser.py
python tests/check_mail_rules_browser.py
python tests/check_mail_ai_browser.py
```

QA usa datos ficticios en transacciones revertidas y Microsoft/IA simulados. El navegador
carga HTML/JSON renderizados por FastAPI y los archivos JS reales; la suite comprueba los
POST contra PostgreSQL. Se revisan 320, 375, 768, 1024 y 1440 px, cuatro vistas de calendario,
20 pantallas, menú/foco, formularios de creación/edición, login, usuarios, tablas, correo,
tareas, avisos de eventos, dashboard y una zona de navegador
distinta a la organización. No hay overflow horizontal del documento ni errores JS/CSP.

Resultado final de Fase 2A ampliada, conservando Parte C: **430 pruebas aprobadas**
(14 nuevas sobre las 416 anteriores; 51 específicas de calendario y sus avisos).
`pip check` sin dependencias incompatibles y arranque real de Uvicorn aprobado.
Chromium aprobó los cinco anchos de esta fase y 26 escenarios adicionales de regresión:
14 de notificaciones, 4 de clasificación por reglas, 4 de IA simulada y 4 de login/usuarios.
Microsoft y OpenAI permanecieron simulados durante las pruebas. Se volvieron a verificar
las migraciones 001–008: **007** crea CalendarEvent; **008** sigue siendo la última,
correspondiente al login/usuarios de Parte C. Esta ampliación de Avisos reutiliza
`calendar_events.reminder_at` e índices existentes y no requiere migración adicional.
`git diff --check` quedó limpio.

Prueba manual sugerida:

1. Reinicia Atenea, inicia sesión y abre **Trabajo → Calendario**.
2. Cambia entre Mes, Semana, Día y Agenda; comprueba la zona indicada.
3. Selecciona una fecha/hora. Crea una audiencia con cliente, expediente, tribunal, ubicación,
   hora de fin y recordatorio anterior. Ábrela, corrige la hora y guarda.
4. Crea una reunión, una cita y un evento de día completo; revisa sus etiquetas y fechas.
5. Desde el formulario elige Tarea, confirma en el módulo existente y comprueba que aparece
   una sola tarea en el calendario. Completar una recurrente conserva su funcionamiento.
6. Aplica filtros por tipo, cliente, proyecto y completados; revisa Próximos eventos en Inicio.
7. Abre Avisos y pospón el recordatorio de una audiencia: cambia el aviso en el calendario
   y el contador, pero la audiencia conserva su inicio. Prueba editarla después del snooze.
   Cancélala y verifica que desaparece de la agenda y Avisos; su detalle conserva el estado.
8. En otra sesión, usa otro usuario de la misma organización y un administrador: ninguno debe
   ver el evento privado. Repite con otra organización.
9. Comprueba menú, foco y formularios en móvil; revisa correo, avisos, horas y reportes.

Limitaciones deliberadas: sin drag & drop/resize; se edita mediante formulario. Sin calendarios
compartidos, asignaciones, sincronización Outlook/Google, permisos Calendar de Graph,
videollamadas ni IA en calendario. No se expanden recurrencias futuras aún no generadas y
no se envían recordatorios fuera de la aplicación.

## Parte C — Login simplificado y usuarios por organización

El login pide **email y contraseña**, con CSRF. El servidor busca la identidad normalizada,
verifica bcrypt, usuario activo y organización activa; obtiene `organization_id` de la fila
del usuario y crea la sesión. Nunca se admite que el navegador seleccione otra organización.
Cuenta inexistente, contraseña incorrecta y cuenta inactiva muestran **Email o contraseña
incorrectos.** No hay recuperación pública que revele la existencia de cuentas.

### Migración 008 e identidad

`008_global_user_email.sql` añade el índice único global `lower(btrim(email))` y el contador
`auth_version` (entero no negativo, inicialmente cero). Se conserva el índice previo por
organización y todas las cuentas, hashes y referencias históricas.

`python -m app.migrate` bloquea escrituras de usuarios durante la comprobación y construcción
del índice. Primero detecta duplicados normalizados, incluyendo cuentas inactivas. Si hay
conflictos, detiene y revierte la migración, enumera los **IDs de usuarios en conflicto** y
solicita resolverlos explícitamente; no borra usuarios, no fusiona cuentas ni cambia emails
silenciosamente. El operador local puede consultar `python -m app.cli list-users` para
identificar los registros y coordinar su corrección. No se debe iniciar esta versión hasta
que la migración finalice correctamente.

Las altas y ediciones normalizan con trim y lowercase. La migración conserva la escritura
original de emails históricos; autenticación e índice comparan su forma normalizada.
En la base local no había duplicados, y la migración quedó aplicada durante esta implementación.

### Administración → Usuarios

Solo administradores activos de la organización pueden acceder. La lista está paginada
(50 cuentas por página), incluye activas/inactivas y consulta únicamente los campos visibles,
nunca hashes. El propietario organizativo se toma de la sesión y se rechazan IDs de
organización/usuario enviados como campos de formulario.

| Ruta | Métodos | Función |
|---|---|---|
| `/settings/users` | GET | Usuarios de la organización autenticada |
| `/settings/users/new` | GET, POST | Nombre, email, rol, estado y contraseña inicial |
| `/settings/users/{id}/edit` | GET, POST | Nombre, email, rol y estado |
| `/settings/users/{id}/password` | GET, POST | Restablecimiento local de contraseña |

Los POST requieren CSRF. Acceder a una cuenta de otra organización devuelve el mismo 404
que una inexistente. Un email ocupado devuelve un mensaje genérico de indisponibilidad,
sin revelar qué cuenta u organización lo utiliza. El índice global también cubre carreras
entre altas/ediciones de diferentes organizaciones.

Se impide degradar o desactivar al último administrador activo. Las modificaciones se
serializan con un bloqueo de fila de la organización; tras adquirirlo se revalida el rol,
estado y versión de sesión del administrador. Una prueba con dos transacciones simultáneas
comprueba que al menos un administrador permanece activo.

Desactivar no elimina datos ni ejecuta borrados en cascada: tareas, horas, eventos y conexiones
permanecen asociadas a la cuenta. Al cambiar estado o email se incrementa `auth_version`;
reactivar no vuelve válidas las cookies antiguas. El rol se comprueba en cada petición, de
modo que un administrador degradado deja de tener acceso administrativo inmediatamente.

### Contraseñas y sesiones

Sin infraestructura de invitaciones, el administrador configura una contraseña inicial o
una nueva contraseña, confirmándola en el formulario. Se reutiliza bcrypt con 12 rondas,
sal aleatoria y la política existente: mínimo 12 caracteres, máximo 72 bytes UTF-8, sin
truncamiento y rechazando contraseñas de solo espacios. Solo se guarda el hash. Las contraseñas
no se repueblan en formularios tras errores, ni aparecen en respuestas, logs o URLs.

El restablecimiento local invalida todas las sesiones anteriores mediante `auth_version`.
Si el administrador restablece su propia contraseña, se elimina también su cookie y vuelve
al login. Las sesiones previas a la migración se consideran versión cero, por lo que siguen
funcionando hasta caducar o hasta una revocación. Una contraseña restablecida no borra
conexiones Microsoft ni cambia sus permisos; sí impide reutilizar la antigua sesión Atenea.

La entrega de la contraseña al titular es manual y debe realizarse por un canal privado.
No se envían emails ni se implementan todavía invitaciones, magic links, recuperación pública
o cambio obligatorio en primer acceso. `app/users.py` centraliza normalización, política,
alta y restablecimiento para poder incorporar esos flujos posteriormente.

### CLI, archivos y validación

`create-admin` conserva la selección de organización como herramienta local de alta,
pero rechaza un email ya utilizado en cualquier organización y revierte el alta completa.
`list-users` mantiene su alcance de operador local (todas las organizaciones) y muestra
también el ID de organización para diagnosticar conflictos. No muestra hashes. Ese alcance
no se traslada a la pantalla web, que siempre está limitada a la organización autenticada.

Nuevos: `app/users.py`, `app/routers/users.py`, `app/templates/users.html`,
`app/templates/user_form.html`, `migrations/008_global_user_email.sql`,
`tests/test_users.py`, `tests/test_user_identity_migration.py`,
`tests/test_user_admin_concurrency.py`, `tests/users_browser_fixture.py` y
`tests/check_users_browser.py`.

Modificados en esta parte: `app/models.py`, `app/migrate.py`, `app/routers/auth.py`,
`app/web_auth.py`, `app/cli.py`, `app/main.py`, `app/templates/login.html`,
`app/templates/workspace.html`, `app/static/corporate.css`, fixtures/pruebas de autenticación,
CLI, trabajo, correo y Uvicorn, y este README. Se conservó el trabajo de Fase 2A.
No hay dependencias adicionales, variables nuevas, cambios en `.env` ni permisos Microsoft nuevos.

```powershell
.\.venv\Scripts\python.exe -m app.migrate
.\.venv\Scripts\python.exe -m unittest discover -s tests -q
.\.venv\Scripts\python.exe -m pip check
# QA opcional con Playwright/Chromium:
python tests/check_users_browser.py
```

Prueba manual: reinicia Atenea, entra con email/contraseña y abre **Administración → Usuarios**.
Crea una cuenta; inicia sesión con ella en otro navegador y verifica su organización.
Cambia nombre/email/rol/estado desde administración; verifica que desactivar conserva los
registros y bloquea accesos. Restablece su contraseña y comprueba que la sesión anterior
deja de funcionar. Intenta retirar al último administrador y confirma que se rechaza.
Con un usuario normal o un administrador de otra organización no deben aparecer las cuentas ajenas.

Resultado de validación de Parte C: **416 pruebas aprobadas**, 27 nuevas respecto de Fase 2A.
Incluye la carrera real entre administradores, migración con duplicados, restablecimiento y
revocación, y login por email seguido de lectura de buzones Microsoft simulados para tres
propietarios en dos organizaciones. `pip check` sin incompatibilidades y arranque real de
Uvicorn aprobado, incluidas las nuevas rutas protegidas. Chromium verificó login, listado,
alta, edición y restablecimiento a 320/768/1024/1440 px, sin overflow del documento, errores
JS/CSP ni repoblación de contraseñas; también pasaron los 14 escenarios de notificaciones.
