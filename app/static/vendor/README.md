# Dependencias del calendario

Distribuciones originales, sin modificar. Se sirven desde Atenea; el navegador no contacta un CDN.

| Archivo | Paquete y versión | Origen | Licencia |
|---|---|---|---|
| fullcalendar-6.1.21.min.js | fullcalendar 6.1.21 (Standard) | https://cdn.jsdelivr.net/npm/fullcalendar@6.1.21/index.global.min.js | MIT |
| fullcalendar-es-6.1.21.min.js | @fullcalendar/core 6.1.21 | https://cdn.jsdelivr.net/npm/@fullcalendar/core@6.1.21/locales/es.global.min.js | MIT |
| fullcalendar-luxon3-6.1.21.min.js | @fullcalendar/luxon3 6.1.21 | https://cdn.jsdelivr.net/npm/@fullcalendar/luxon3@6.1.21/index.global.min.js | MIT |
| luxon-3.7.2.min.js | luxon 3.7.2 | https://cdn.jsdelivr.net/npm/luxon@3.7.2/build/global/luxon.min.js | MIT |

Licencias originales en `fullcalendar-LICENSE.txt` y `luxon-LICENSE.txt`.
Integridad de archivos en `SHA256SUMS.json`.
Standard incluye core, interaction, daygrid, timegrid, list y multimonth. No incluye Scheduler/Premium.

Se fija la última versión de la rama 6 publicada en la documentación consultada; se elige
por compatibilidad con scripts locales y licencia MIT. FullCalendar 7.1.0 es más reciente,
pero cambió a AGPLv3/comercial. No actualizar de rama sin revisar licencia e integración.

Documentación oficial:
- https://legacy.fullcalendar.io/v6/initialize-globals
- https://legacy.fullcalendar.io/v6/luxon
- https://legacy.fullcalendar.io/v6/content-security-policy
- https://fullcalendar.io/docs/upgrading-from-v6

Luxon utiliza las zonas IANA/Intl del navegador. La zona siempre procede de la organización.
El CSP del calendario admite solamente un nonce aleatorio por respuesta para el CSS de
FullCalendar y `font-src data:` para sus iconos embebidos; no habilita scripts inline,
`unsafe-inline`, conexiones externas ni carga de fuentes desde terceros.
