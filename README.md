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

No se implementan Outlook, Microsoft Graph, IA, notificaciones push o del sistema operativo, correo automático ni sincronización de calendario.
