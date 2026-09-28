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

Abre **http://127.0.0.1:8000/login**. Usa el nombre de la organización, el email y la contraseña definidos mediante el CLI. Se toleran mayúsculas y espacios exteriores en organización y email. Si varias organizaciones coinciden por nombre, el login se rechaza con el mismo mensaje genérico que los demás fallos de credenciales.

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

La cookie `atenea_session` está firmada por `SessionMiddleware` de Starlette, es `HttpOnly`, `SameSite=Lax`, limitada al host y a la ruta `/`, y tiene una duración de 8 horas. Contiene solamente `user_id`, `organization_id` y un token aleatorio para proteger los formularios frente a CSRF. No contiene nombre, email, rol, contraseña ni hash. La firma protege frente a modificaciones; no cifra su contenido. Referencia: [SessionMiddleware de Starlette](https://github.com/Kludex/starlette/blob/main/docs/middleware.md#sessionmiddleware).

El login renueva los datos de sesión y el token CSRF. Login y logout requieren el token del formulario. Las páginas llevan `Cache-Control: no-store`, una política CSP que permite solo scripts locales y protección contra inclusión en marcos; las plantillas escapan el contenido HTML.

`get_current_user()` en `app/web_auth.py` es la dependencia reutilizable para rutas protegidas. Consulta PostgreSQL usando **ambos IDs** y vuelve a comprobar los estados del usuario y la organización. El nombre y rol se leen de la base de datos, no de la cookie. Cada futura consulta de negocio deberá filtrar también por `user.organization_id`.

Los fallos de login muestran un mensaje genérico. La verificación de cuentas inexistentes también realiza trabajo bcrypt para reducir diferencias de tiempo evidentes; esto no sustituye a un limitador de intentos. No se registran formularios, cookies ni excepciones SQL con parámetros; los errores internos generan una respuesta y un registro genéricos. El comando recomendado desactiva el registro de URLs de Uvicorn.

## Antes de producción

1. Usa HTTPS y añade `SESSION_COOKIE_SECURE=true` a `.env`. El valor local predeterminado es `false` para permitir HTTP en `127.0.0.1`; con `true`, la cookie solo se envía por HTTPS.
2. Retira `--reload`, configura el proxy, los hosts permitidos, los encabezados reenviados de confianza y HSTS. El proxy debe evitar registrar contraseñas, cookies y cuerpos de formularios.
3. Añade límites de intentos de login y monitorización de abusos antes de exponer el acceso a Internet.
4. Protege `.env` y usa el mismo secreto aleatorio en todos los procesos de la aplicación. Rotarlo invalida todas las cookies existentes.
5. El logout borra la cookie de ese navegador. Al ser sesiones firmadas en el cliente, una copia robada no se revoca individualmente: puede funcionar hasta caducar si usuario y organización siguen activos. Cambiar la contraseña tampoco revoca por sí solo una cookie emitida. Para revocación individual, incorpora sesiones almacenadas en servidor; desactivar usuario/organización ya bloquea el siguiente acceso.

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

Desde el detalle de correo pulsa **Crear tarea desde este correo**. La ruta **`GET /mail/{message_id}/create-task`** consulta únicamente `id`, `subject`, `from` e `importance` en el buzón del usuario conectado. Propone el asunto como título editable, deja la descripción vacía y reutiliza los campos de tareas (cliente, proyecto, vencimiento, prioridad, recordatorio y recurrencia). No consulta ni copia el cuerpo completo ni el preview. Las notas de descripción son las que el usuario escribe en el formulario.

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
