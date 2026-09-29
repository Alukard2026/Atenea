(() => {
  'use strict';
  const el = document.getElementById('calendar');
  const filters = document.getElementById('calendar-filters');
  const status = document.getElementById('calendar-status');
  let controller;
  const calendar = new FullCalendar.Calendar(el, {
    locale: 'es', timeZone: el.dataset.timezone, firstDay: 1,
    initialView: window.innerWidth < 600 ? 'listWeek' : 'dayGridMonth',
    headerToolbar: {left: 'prev,next today', center: 'title', right: 'dayGridMonth,timeGridWeek,timeGridDay,listWeek'},
    buttonIcons: false, buttonText: {prev: '‹', next: '›', today: 'Hoy', month: 'Mes', week: 'Semana', day: 'Día', list: 'Agenda'},
    height: 'auto', dayMaxEvents: 3, displayEventEnd: true, nowIndicator: true,
    slotMinTime: '00:00:00', slotMaxTime: '24:00:00', scrollTime: '07:00:00',
    datesSet(info) {
      calendar.setOption('height', info.view.type.startsWith('timeGrid') ? 680 : 'auto');
      if (info.view.type.startsWith('timeGrid')) requestAnimationFrame(() => calendar.scrollToTime('07:00:00'));
    },
    slotLabelFormat: {hour: '2-digit', minute: '2-digit', hour12: false},
    businessHours: {daysOfWeek: el.dataset.businessDays ? el.dataset.businessDays.split(',').map(Number) : [], startTime: '08:00', endTime: '18:00'},
    editable: false, selectable: false, navLinks: true, eventInteractive: true,
    eventTimeFormat: {hour: '2-digit', minute: '2-digit', hour12: false},
    dateClick(info) { window.location.assign('/calendar/new?' + new URLSearchParams({date: info.dateStr.slice(0, 10), time: info.allDay ? '' : info.dateStr.slice(11, 16)})); },
    eventContent(info) {
      const node = document.createElement('span'); node.className = 'calendar-event-label';
      const p = info.event.extendedProps;
      node.textContent = [info.timeText, info.event.title, p.client, p.institution].filter(Boolean).join(' · ');
      node.title = node.textContent;
      return {domNodes: [node]};
    },
    async events(info, success, failure) {
      if (controller) controller.abort();
      controller = new AbortController();
      const current = controller;
      const timeout = setTimeout(() => current.abort(), 15000);
      const data = new FormData(filters);
      const params = new URLSearchParams({start: info.startStr, end: info.endStr, timeZone: el.dataset.timezone, types: data.getAll('types').join(','), completed: data.has('completed') ? '1' : '0', client_id: data.get('client_id'), project_id: data.get('project_id')});
      status.textContent = 'Cargando eventos…';
      try {
        const response = await fetch('/calendar/events?' + params, {credentials: 'same-origin', cache: 'no-store', signal: current.signal});
        if (response.redirected) { window.location.assign('/login'); return; }
        if (!response.ok) throw new Error('calendar');
        const rows = await response.json();
        if (current !== controller) return;
        success(rows); status.textContent = rows.length ? `${rows.length} entradas en este período.` : 'Sin eventos para este período y filtros.';
      } catch (_) {
        if (current !== controller) return;
        success([]); status.textContent = 'No se pudieron cargar los eventos. Reduce el rango o vuelve a aplicar los filtros.';
      } finally { clearTimeout(timeout); }
    }
  });
  calendar.render();
  filters.addEventListener('submit', event => { event.preventDefault(); calendar.refetchEvents(); });
  const client = document.getElementById('client_id');
  const project = document.getElementById('project_id');
  const options = [...project.options].map(option => option.cloneNode(true));
  client.addEventListener('change', () => {
    project.replaceChildren(...options.filter(option => !client.value || !option.value || option.dataset.clientId === client.value).map(option => option.cloneNode(true)));
  });
})();
