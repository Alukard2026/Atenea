"use strict";
// Mejora progresiva: el servidor vuelve a validar siempre cliente y proyecto.
const clientSelect = document.getElementById("client_id");
const projectSelect = document.getElementById("project_id");
if (clientSelect && projectSelect) {
  const projectOptions = Array.from(projectSelect.options).map(option => option.cloneNode(true));
  const filterProjects = () => {
    const previous = projectSelect.value;
    const matching = projectOptions.filter(option => !option.value || option.dataset.clientId === clientSelect.value);
    projectSelect.replaceChildren(...matching.map(option => option.cloneNode(true)));
    projectSelect.value = matching.some(option => option.value === previous) ? previous : "";
  };
  clientSelect.addEventListener("change", filterProjects);
  filterProjects();
}
