(() => {
  'use strict';
  const allDay = document.getElementById('all_day');
  const start = document.getElementById('start_time');
  const end = document.getElementById('end_time');
  const update = () => { start.disabled = end.disabled = allDay.checked; start.required = !allDay.checked; };
  allDay.addEventListener('change', update); update();
  const taskLink = document.getElementById('calendar-task-link');
  if (taskLink) {
    const updateLink = () => { taskLink.href = '/tasks/new?' + new URLSearchParams({date: document.getElementById('date').value, time: allDay.checked ? '' : start.value}); };
    document.getElementById('calendar-event-form').addEventListener('change', updateLink);
    document.getElementById('event_type').addEventListener('change', event => {
      if (event.target.value === 'task') { updateLink(); window.location.assign(taskLink.href); }
    });
    updateLink();
  }
})();
